"""CE (Content Editor) validation service.

Validates generated content against a CE_Checklist document or, if unavailable,
falls back to the Writing Rules from the active Style configuration.

Validation flow
---------------
1. Fetch CE_Checklist (style documents first, then document registry).
2. If missing → fall back to active style Writing Rules.
3. Validate content via LLM → JSON {passed, issues}.
4. If issues found → fix content via LLM.
5. Re-check fixed content.
6. Return final (possibly corrected) content.

Failures at any step are non-fatal: the original content is returned unchanged.
"""
from __future__ import annotations

import json
import logging
from typing import Callable, Optional

_log = logging.getLogger(__name__)

CE_CHECKLIST_FILENAME = "CE_Checklist"


# ---------------------------------------------------------------------------
# Checklist / rules resolution
# ---------------------------------------------------------------------------

def _fetch_ce_checklist(db, active_style=None) -> Optional[str]:
    """Return CE_Checklist content, or None if not found."""
    # 1. Documents linked to the active style
    if active_style:
        for sd in getattr(active_style, "style_documents", []) or []:
            doc = getattr(sd, "document", None)
            if doc and getattr(doc, "status", "active") == "active" and doc.filename \
                    and CE_CHECKLIST_FILENAME.lower() in doc.filename.lower():
                if doc.content and doc.content.strip():
                    _log.debug("CE_Checklist found in style documents: %s", doc.filename)
                    return doc.content.strip()

    # 2. General document registry
    try:
        from promptops_app.repositories import document_repository
        docs = document_repository.list_documents_filtered(
            db, search=CE_CHECKLIST_FILENAME, status="active", limit=1
        )
        if docs and docs[0].content and docs[0].content.strip():
            _log.debug("CE_Checklist found in document registry: %s", docs[0].filename)
            return docs[0].content.strip()
    except Exception as _e:
        _log.warning("CE_Checklist registry lookup failed: %s", _e)

    return None


def _fetch_writing_rules(active_style) -> Optional[str]:
    """Return Writing Rules from active style, or None if unavailable."""
    if not active_style:
        return None
    parts = []
    if getattr(active_style, "custom_instructions", None) and active_style.custom_instructions.strip():
        parts.append(f"WRITING INSTRUCTIONS:\n{active_style.custom_instructions.strip()}")
    if getattr(active_style, "generated_summary", None) and active_style.generated_summary.strip():
        parts.append(f"STYLE GUIDE:\n{active_style.generated_summary.strip()}")
    return "\n\n".join(parts) if parts else None


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

def _parse_validation_json(raw: str) -> tuple[bool, list[str]]:
    """Parse LLM validation response. Returns (passed, issues)."""
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    data = json.loads(text.strip())
    passed = bool(data.get("passed", True))
    issues = [str(i) for i in data.get("issues", [])]
    return passed, issues


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_ce_validation(
    content: str,
    db,
    active_style=None,
    model_choice: str = "GPT-5.6 Terra",
    llm_call_fn: Optional[Callable] = None,
) -> str:
    """Validate and auto-fix *content* against CE_Checklist or Writing Rules.

    Returns the validated (and possibly corrected) content.
    Returns the original *content* unchanged if validation cannot run.
    """
    if not content or not content.strip():
        return content

    if llm_call_fn is None:
        from promptops_app.services.llm_service import generate_with_metadata as _llm_meta
        llm_call_fn = _llm_meta

    # ── Resolve validation source ─────────────────────────────────────────
    checklist = _fetch_ce_checklist(db, active_style)
    using_fallback = False
    source_label = "CE_Checklist"

    if not checklist:
        _log.info("CE_Checklist not found — falling back to Writing Rules from active style")
        checklist = _fetch_writing_rules(active_style)
        using_fallback = True
        source_label = "Writing Rules"

    if not checklist:
        _log.warning(
            "CE validation skipped — no CE_Checklist and no Writing Rules available"
        )
        return content

    _log.info(
        "CE validation starting  source=%s  fallback=%s  content_len=%d",
        source_label, using_fallback, len(content),
    )

    # ── Step 1: Validate ──────────────────────────────────────────────────
    _validate_system = (
        "You are a content quality validator for educational materials. "
        "Evaluate the provided content strictly against the rules/checklist below. "
        "Return ONLY a JSON object in this exact format — no other text:\n"
        '{"passed": true, "issues": []}\n'
        "or\n"
        '{"passed": false, "issues": ["specific issue 1", "specific issue 2"]}'
    )
    _validate_user = (
        f"VALIDATION RULES ({source_label}):\n{checklist}\n\n"
        f"CONTENT TO VALIDATE:\n{content[:8000]}"
    )

    try:
        _val_result = llm_call_fn(model_choice, _validate_system, _validate_user)
        if _val_result.is_error:
            _log.warning("CE validation LLM call failed: %s", _val_result.text)
            return content
        passed, issues = _parse_validation_json(_val_result.text)
    except Exception as _e:
        _log.warning("CE validation step failed: %s", _e)
        return content

    if passed or not issues:
        _log.info("CE validation passed on first check  source=%s", source_label)
        return content

    _log.info(
        "CE validation found %d issue(s)  source=%s%s",
        len(issues), source_label, " [fallback]" if using_fallback else "",
    )

    # ── Step 2: Fix issues ────────────────────────────────────────────────
    _fix_system = (
        "You are a content quality editor for educational materials. "
        "Fix the content below to comply with ALL the rules/checklist provided. "
        "Preserve the original structure, headings, section order, and meaning. "
        "Return ONLY the corrected content — no preamble, no explanations."
    )
    _fix_user = (
        f"RULES ({source_label}):\n{checklist}\n\n"
        "ISSUES TO FIX:\n" + "\n".join(f"- {i}" for i in issues) + "\n\n"
        f"CONTENT TO FIX:\n{content}"
    )

    try:
        from promptops_app.core.models import resolve_model
        _fix_result = llm_call_fn(
            model_choice, _fix_system, _fix_user,
            max_tokens=resolve_model(model_choice).max_output_tokens,
        )
        if _fix_result.is_error:
            _log.warning("CE fix LLM call failed: %s", _fix_result.text)
            return content
        fixed_content = _fix_result.text.strip()
        if not fixed_content:
            return content
    except Exception as _e:
        _log.warning("CE fix step failed: %s", _e)
        return content

    # ── Step 3: Re-check ──────────────────────────────────────────────────
    try:
        _recheck_user = (
            f"VALIDATION RULES ({source_label}):\n{checklist}\n\n"
            f"CONTENT TO VALIDATE:\n{fixed_content[:8000]}"
        )
        _rc_result = llm_call_fn(model_choice, _validate_system, _recheck_user)
        if not _rc_result.is_error:
            _rc_passed, _rc_issues = _parse_validation_json(_rc_result.text)
            if _rc_passed:
                _log.info("CE re-check passed  source=%s", source_label)
            else:
                _log.info(
                    "CE re-check still has %d issue(s) — using best-effort fixed content",
                    len(_rc_issues),
                )
    except Exception:
        pass  # re-check failure is non-fatal

    return fixed_content
