"""Batched per-page LLM content tagging for ``ebook_reference`` units.

After page-aligned units exist, enrich each with:

* ``topics`` — 3–8 short topic phrases (LLM; heuristic keywords() on failure)
* ``acs_codes`` — pattern-validated ``AM.[IVX]+.[A-Z].[KRS]\\d+`` only
* ``summary`` — at most two sentences

Also stamps durable retry flags on each unit's metadata:

* ``tagging_status`` — ``ok`` / ``failed`` / ``pending``
* ``tagging_error`` — last failure reason (cleared on success)
* ``tagging_attempted_at`` — ISO timestamp of last attempt

Batching (~10 pages per call) is required: one Sonnet call per page on a
1,200-page handbook is not viable. Failures are soft — ingest continues with
heuristic topics and empty ACS/summary, but failed pages are flagged for retry.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

from services.pipeline.common import safe_json

log = logging.getLogger(__name__)

#: AIM ACS code shape. Anything that does not match is dropped — this codebase
#: has already paid for silent ACS hallucination in digest MAP.
_ACS_RE = re.compile(r"^AM\.[IVX]+\.[A-Z]\.[KRS]\d+[A-Za-z]?$", re.IGNORECASE)

_DEFAULT_BATCH_SIZE = 10
_MAX_PAGE_CHARS = 3500  # keep batch prompts bounded

TAGGING_OK = "ok"
TAGGING_FAILED = "failed"
TAGGING_PENDING = "pending"


def validate_acs_codes(codes: Sequence[Any]) -> List[str]:
    out: List[str] = []
    seen = set()
    for c in codes or []:
        s = str(c or "").strip().upper()
        if not s or s in seen:
            continue
        if _ACS_RE.match(s):
            seen.add(s)
            out.append(s)
    return out


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _stamp_status(unit: Dict[str, Any], status: str, error: str = "") -> None:
    meta = dict(unit.get("metadata") or {})
    meta["tagging_status"] = status
    meta["tagging_attempted_at"] = _now_iso()
    if status == TAGGING_OK:
        meta.pop("tagging_error", None)
    elif error:
        meta["tagging_error"] = str(error)[:500]
    unit["metadata"] = meta


def mark_units_pending(units: Sequence[Dict[str, Any]]) -> None:
    """Stamp ``tagging_status=pending`` before / when LLM tagging is skipped."""
    for unit in units:
        meta = dict(unit.get("metadata") or {})
        if meta.get("tagging_status") == TAGGING_OK:
            continue
        meta["tagging_status"] = TAGGING_PENDING
        meta.pop("tagging_error", None)
        unit["metadata"] = meta


def tagging_counts(units: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    """Aggregate failed/pending counts for source-index / overview badges."""
    failed = pending = 0
    for unit in units:
        status = str((unit.get("metadata") or {}).get("tagging_status") or "").lower()
        if status == TAGGING_FAILED:
            failed += 1
        elif status == TAGGING_PENDING:
            pending += 1
    return {"tagging_failed_count": failed, "tagging_pending_count": pending}


def _page_excerpt(text: str, limit: int = _MAX_PAGE_CHARS) -> str:
    t = (text or "").strip()
    if len(t) <= limit:
        return t
    return t[:limit] + "\n…[truncated]"


def _build_batch_prompt(batch: Sequence[Dict[str, Any]]) -> str:
    parts = [
        "Tag each handbook page for aviation maintenance training retrieval.",
        "Return JSON only: an array of objects, one per page, with keys:",
        '  pdf_page (int), topics (3-8 short phrases), acs_codes (list of AM.* codes),',
        "  summary (at most two sentences).",
        "Rules:",
        "- Only include ACS codes that appear in the page text or are clearly implied.",
        "- ACS codes MUST match AM.<roman>.<letter>.<K|R|S><digits> (e.g. AM.I.D.K1).",
        "- If unsure about an ACS code, omit it.",
        "- topics must be short noun phrases, not full sentences.",
        "",
        "Pages:",
    ]
    for unit in batch:
        meta = unit.get("metadata") or {}
        pdf_page = meta.get("pdf_page") or unit.get("unit_number")
        loc = meta.get("page_number") or ""
        parts.append(f"--- pdf_page={pdf_page} page_number={loc} ---")
        parts.append(_page_excerpt(unit.get("text") or ""))
        parts.append("")
    return "\n".join(parts)


def _apply_tag(unit: Dict[str, Any], tag: Dict[str, Any]) -> None:
    topics = [str(t).strip() for t in (tag.get("topics") or []) if str(t).strip()]
    if topics:
        unit["topics"] = topics[:8]
    acs = validate_acs_codes(tag.get("acs_codes") or [])
    summary = str(tag.get("summary") or "").strip()
    meta = dict(unit.get("metadata") or {})
    if acs:
        meta["acs_codes"] = acs
    if summary:
        meta["summary"] = summary[:600]
    if topics:
        meta["topics"] = topics[:8]
    unit["metadata"] = meta
    _stamp_status(unit, TAGGING_OK)


def tag_ebook_page_units(
    units: List[Dict[str, Any]],
    *,
    call_llm_fn: Callable[..., Any],
    model_id: str,
    batch_size: int = _DEFAULT_BATCH_SIZE,
    enabled: bool = True,
    token_guard: Any = None,
    errors: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Enrich page units in place. Returns the same list.

    ``call_llm_fn`` is ``services.pipeline.common.call_llm`` (or a test double)
    with signature ``(model, prompt, max_tokens=...) -> (text, tokens_in, tokens_out)``.
    """
    if not units:
        return units
    if not enabled or not model_id:
        mark_units_pending(units)
        return units

    size = max(1, int(batch_size or _DEFAULT_BATCH_SIZE))
    err_list = errors if errors is not None else []

    for start in range(0, len(units), size):
        batch = units[start:start + size]
        prompt = _build_batch_prompt(batch)
        try:
            if token_guard is not None:
                # Rough budget: ~4 chars/token on the prompt + room for JSON out.
                token_guard.check_or_raise(max(1500, len(prompt) // 3 + 800), "ebook_page_tagging")
            resp, tokens_in, tokens_out = call_llm_fn(model_id, prompt, max_tokens=1200)
            if token_guard is not None:
                token_guard.record_usage(
                    tokens_in + tokens_out, "ebook_page_tagging",
                    tokens_in=tokens_in, tokens_out=tokens_out, model=model_id,
                )
            parsed = safe_json(resp)
            if isinstance(parsed, dict) and "pages" in parsed:
                parsed = parsed["pages"]
            if not isinstance(parsed, list):
                raise ValueError(f"expected JSON array, got {type(parsed).__name__}")

            by_page: Dict[int, Dict[str, Any]] = {}
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                try:
                    p = int(item.get("pdf_page"))
                except (TypeError, ValueError):
                    continue
                by_page[p] = item

            for unit in batch:
                meta = unit.get("metadata") or {}
                try:
                    p = int(meta.get("pdf_page"))
                except (TypeError, ValueError):
                    _stamp_status(unit, TAGGING_FAILED, "missing_pdf_page")
                    continue
                if p in by_page:
                    _apply_tag(unit, by_page[p])
                else:
                    _stamp_status(unit, TAGGING_FAILED, "missing_from_llm_response")
        except Exception as exc:
            msg = f"ebook_page_tagging batch@{start}: {exc}"
            log.warning(msg)
            err_list.append(msg)
            # Fail-soft: keep heuristic keywords/topics; flag every unit in the batch.
            for unit in batch:
                _stamp_status(unit, TAGGING_FAILED, str(exc))
            continue

    return units


def tagger_config_from_pipeline(pipeline_cfg: Any) -> Dict[str, Any]:
    """Pull enable/batch/model from PipelineConfig with safe defaults."""
    models = getattr(pipeline_cfg, "models", None)
    model_id = ""
    if models is not None:
        model_id = getattr(models, "ebook_page_tagging", None) or getattr(models, "metadata_extraction", "") or ""
    return {
        "enabled": bool(getattr(pipeline_cfg, "ebook_page_tagging_enabled", True)),
        "batch_size": int(getattr(pipeline_cfg, "ebook_page_tagging_batch_size", _DEFAULT_BATCH_SIZE) or _DEFAULT_BATCH_SIZE),
        "model_id": str(model_id or ""),
    }
