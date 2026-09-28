"""Checklist import service — split an uploaded CE checklist into rules.

Pipeline (called from the background job in ``jobs/review_jobs.py``):
  1. Read the already-parsed checklist text from its ``Document`` row.
  2. One LLM call splits the text into individual, atomic review rules —
     using the same reliability wrapper as every other LLM path, with a
     model-aware output cap and truncation escalation (a truncated reply is
     never accepted, mirroring the Outline import).
  3. Persist a new ``ReviewChecklist`` version + its ``ReviewChecklistItem``
     rows, and archive the project's previous active checklist. The new
     checklist is only made active on success, so a failed split never leaves
     the project with no checklist.

All LLM access goes through ``llm_service`` — never the provider client directly.
Rules import as **non-mandatory**; the user ticks the ones that block approval.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from promptops_app.core.llm_client import safe_json_loads
from promptops_app.core.models import DEFAULT_MODEL_NAME
from promptops_app.services.ce_review.llm_util import high_output_model, output_cap
from promptops_app.services.llm_service import generate_with_metadata
from promptops_app.services.usage_service import UsageLogContext

_log = logging.getLogger(__name__)

# A checklist rarely exceeds a few thousand words; this guards a pathological
# upload from blowing the context window. Advisory — the text is not cut here.
_LARGE_TEXT_CHARS = 200_000

_SPLIT_SYSTEM = (
    "You extract individual, atomic review rules from a Content Editor (CE) "
    "checklist document. Return ONLY a JSON object of this exact shape:\n"
    '{"rules": [{"rule_text": "...", "section": "...", "guidance": "..."}]}\n'
    "Guidelines:\n"
    "- Each rule is ONE checkable requirement. Split compound bullets into "
    "separate atomic rules.\n"
    "- rule_text: the requirement, imperative and self-contained.\n"
    "- section: the heading the rule falls under in the document, or null.\n"
    "- guidance: a short how-to-satisfy note if the document gives one, else null.\n"
    "- Extract only what the document actually states. Do NOT invent rules.\n"
    "- Preserve the document's order.\n"
    "Return the JSON object only, with no prose or code fences."
)

_SPLIT_USER = (
    "Checklist name: {name}\n\n"
    "--- CHECKLIST TEXT START ---\n{text}\n--- CHECKLIST TEXT END ---\n\n"
    "Return the JSON object of rules only."
)


class ChecklistImportError(Exception):
    """Raised when a checklist cannot be split into usable rules."""


class _SplitTruncated(RuntimeError):
    """The split reply hit the model's output cap — distinct so the caller can
    retry on a larger-output model before giving up (never persist a partial
    rule set)."""


# ---------------------------------------------------------------------------
# LLM split
# ---------------------------------------------------------------------------

def _llm_split(text: str, name: str, *, model_choice: str, usage_ctx) -> str:
    """One LLM call returning the raw JSON reply. Raises ``_SplitTruncated`` if the
    reply is cut off at the output cap, and a generic error otherwise."""
    user_prompt = _SPLIT_USER.format(name=name, text=text)
    result = generate_with_metadata(
        model_choice, _SPLIT_SYSTEM, user_prompt,
        usage_ctx=usage_ctx, max_tokens=output_cap(model_choice),
    )
    if getattr(result, "status", None) == "error" or getattr(result, "is_error", False):
        raise ChecklistImportError(
            f"The AI could not process the checklist ({getattr(result, 'error_type', 'llm_error')}). "
            "Please try again."
        )
    if getattr(result, "truncated", False):
        raise _SplitTruncated("checklist split truncated (hit the output cap)")
    return result.text or ""


def _split_with_retry(text: str, name: str, *, model_choice: str, usage_ctx) -> str:
    """Split, escalating to a larger-output model if the selected one truncates.
    A still-truncated reply raises — we never persist a partial rule set."""
    try:
        return _llm_split(text, name, model_choice=model_choice, usage_ctx=usage_ctx)
    except _SplitTruncated:
        big = high_output_model(exclude=model_choice)
        if not big:
            raise ChecklistImportError(
                "The checklist is too large to split in one pass and no larger model is available."
            )
        _log.info("checklist split truncated on %r — retrying on larger-output model %r",
                  model_choice, big)
        try:
            return _llm_split(text, name, model_choice=big, usage_ctx=usage_ctx)
        except _SplitTruncated as exc:
            raise ChecklistImportError(
                "The checklist is too large to split reliably. Try splitting it into smaller documents."
            ) from exc


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def _coerce_rules(raw) -> list[dict]:
    """Pull the rules list out of the (repaired) LLM JSON, tolerating shapes."""
    if isinstance(raw, list):
        candidate = raw
    elif isinstance(raw, dict):
        candidate = raw.get("rules") or raw.get("items") or raw.get("checklist") or []
    else:
        candidate = []
    return candidate if isinstance(candidate, list) else []


def _normalise_rule(entry, index: int) -> Optional[dict]:
    """Validate + clean one raw rule dict. Returns None to drop invalid entries."""
    if not isinstance(entry, dict):
        return None
    rule_text = str(entry.get("rule_text") or entry.get("text") or entry.get("rule") or "").strip()
    if not rule_text:
        return None
    section = str(entry.get("section") or "").strip()[:255] or None
    guidance = str(entry.get("guidance") or "").strip() or None
    return {
        "item_key": f"r{index + 1:03d}",   # stable within a checklist; survives edits
        "rule_text": rule_text[:4000],
        "section": section,
        "guidance": guidance,
        "is_mandatory": False,             # user ticks the ones that block approval
        "applies_to": None,
        "position": index,
    }


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def split_and_store(
    db,
    *,
    document_id: int,
    project_id: int,
    name: str,
    created_by: str,
    model_choice: Optional[str] = None,
) -> "object":
    """Split the checklist ``Document`` into rules and persist a new active
    ``ReviewChecklist`` version. Archives the project's previous active
    checklist(s) on success. Adds rows and commits. Returns the new checklist.

    Raises ``ChecklistImportError`` on any parse/LLM/empty-result failure — the
    caller (the job) turns that into a clean job failure and the previous active
    checklist is left untouched.
    """
    from promptops_app.database import Document, ReviewChecklist, ReviewChecklistItem

    doc = db.query(Document).filter(Document.id == document_id).first()
    if doc is None:
        raise ChecklistImportError("The uploaded checklist could not be found.")
    text = (doc.content or "").strip()
    if not text:
        raise ChecklistImportError(
            "No readable text was found in the checklist. The file may be empty or image-only."
        )
    if len(text) > _LARGE_TEXT_CHARS:
        _log.warning("checklist text is large (%d chars) — proceeding", len(text))

    model = model_choice or DEFAULT_MODEL_NAME
    usage_ctx = UsageLogContext(
        user_name=created_by,
        project_id=project_id,
        course_id=None,
        entity_type="review_checklist",
        entity_id=str(document_id),
    )

    raw = _split_with_retry(text, name, model_choice=model, usage_ctx=usage_ctx)
    rules = _coerce_rules(safe_json_loads(raw))
    normalised = [r for r in (_normalise_rule(e, i) for i, e in enumerate(rules)) if r]
    # Renumber keys/positions over the KEPT rules so they are contiguous
    # (r001, r002, ...) even when the LLM emitted a blank/invalid entry.
    for i, r in enumerate(normalised):
        r["item_key"] = f"r{i + 1:03d}"
        r["position"] = i
    if not normalised:
        raise ChecklistImportError(
            "The AI did not find any rules in this document. Please check the file and try again."
        )

    now = datetime.now(timezone.utc)
    # Next version number for this project (max existing + 1).
    prev_versions = [
        c.version for c in db.query(ReviewChecklist.version)
        .filter(ReviewChecklist.project_id == project_id).all()
    ]
    next_version = (max(prev_versions) + 1) if prev_versions else 1

    checklist = ReviewChecklist(
        project_id=project_id,
        name=name,
        source_document_id=document_id,
        version=next_version,
        status="active",
        created_by=created_by,
        created_at=now,
        updated_at=now,
    )
    db.add(checklist)
    db.flush()  # assign checklist.id for the items' FK

    for r in normalised:
        db.add(ReviewChecklistItem(
            checklist_id=checklist.id,
            project_id=project_id,
            item_key=r["item_key"],
            section=r["section"],
            rule_text=r["rule_text"],
            is_mandatory=r["is_mandatory"],
            applies_to=r["applies_to"],
            guidance=r["guidance"],
            position=r["position"],
            created_at=now,
        ))

    # Only now archive the previous active checklist(s): the new one is ready.
    (db.query(ReviewChecklist)
       .filter(
           ReviewChecklist.project_id == project_id,
           ReviewChecklist.status == "active",
           ReviewChecklist.id != checklist.id,
       )
       .update({"status": "archived", "updated_at": now}, synchronize_session=False))

    db.commit()
    db.refresh(checklist)
    _log.info(
        "checklist_imported project=%s checklist_id=%s version=%s rules=%d",
        project_id, checklist.id, next_version, len(normalised),
    )
    return checklist
