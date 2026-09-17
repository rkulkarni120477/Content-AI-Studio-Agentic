"""Batched LLM content tagging for ``hangar_sheet`` units.

Same durable retry flags as calendar sheets / ebook pages. The LLM adds:

* ``topics`` — 3–8 short shop/lab topic phrases
* ``summary`` — at most two sentences covering hangar activities on the sheet
* ``acs_codes`` — validated ``AM.*`` codes merged with spreadsheet ACS
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Sequence

from services.ebook_page_tagger import (
    TAGGING_FAILED,
    TAGGING_OK,
    _TAG_MAX_TOKENS,
    _normalize_tag_response,
    _parse_tag_json,
    _stamp_status,
    mark_units_pending,
    tagger_config_from_pipeline,
    validate_acs_codes,
)

log = logging.getLogger(__name__)

_DEFAULT_BATCH_SIZE = 10
_MAX_SHEET_CHARS = 6000


def _sheet_excerpt(text: str, limit: int = _MAX_SHEET_CHARS) -> str:
    t = (text or "").strip()
    if len(t) <= limit:
        return t
    return t[:limit] + "\n…[truncated]"


def _build_batch_prompt(batch: Sequence[Dict[str, Any]]) -> str:
    parts = [
        "Tag each AIM hangar-activity SHEET for aviation maintenance training retrieval.",
        "Each item is one Excel worksheet of Optional Hangar Activities",
        "(e.g. Block 5 Day-Night), not a single day.",
        "Return JSON only: a top-level ARRAY of objects (not an object wrapper),",
        "one object per sheet, with keys:",
        "  sheet_index (int), topics (3-8 short phrases), acs_codes (list of AM.* codes),",
        "  summary (at most two sentences covering hangar / shop activities).",
        "Rules:",
        "- Prefer tools, procedures, safety, and shop skills that appear in the text.",
        "- ACS codes MUST match AM.<roman>.<letter>.<K|R|S><digits> (e.g. AM.I.B.K1).",
        "- If unsure about an ACS code, omit it.",
        "- topics must be short noun phrases, not full sentences.",
        "- Do NOT wrap the array in {\"sheets\": ...} or any other object.",
        "",
        "Sheets:",
    ]
    for unit in batch:
        meta = unit.get("metadata") or {}
        sheet_index = meta.get("sheet_index")
        if sheet_index is None:
            sheet_index = unit.get("unit_number")
        title = unit.get("title") or meta.get("sheet_name") or ""
        block = meta.get("block") or ""
        schedule = meta.get("schedule") or ""
        parts.append(
            f"--- sheet_index={sheet_index} title={title!r} block={block!r} "
            f"schedule={schedule!r} ---"
        )
        parts.append(_sheet_excerpt(unit.get("text") or ""))
        parts.append("")
    return "\n".join(parts)


def _apply_sheet_tag(unit: Dict[str, Any], tag: Dict[str, Any]) -> None:
    topics = [str(t).strip() for t in (tag.get("topics") or []) if str(t).strip()]
    if topics:
        unit["topics"] = topics[:8]
    llm_acs = validate_acs_codes(tag.get("acs_codes") or [])
    summary = str(tag.get("summary") or "").strip()
    meta = dict(unit.get("metadata") or {})
    existing = validate_acs_codes(meta.get("acs_codes") or [])
    merged: List[str] = []
    seen = set()
    for code in existing + llm_acs:
        if code not in seen:
            seen.add(code)
            merged.append(code)
    if merged:
        meta["acs_codes"] = merged
    if summary:
        meta["summary"] = summary[:600]
    if topics:
        meta["topics"] = topics[:8]
    unit["metadata"] = meta
    _stamp_status(unit, TAGGING_OK)


def tag_hangar_sheet_units(
    units: List[Dict[str, Any]],
    *,
    call_llm_fn: Callable[..., Any],
    model_id: str,
    batch_size: int = _DEFAULT_BATCH_SIZE,
    enabled: bool = True,
    token_guard: Any = None,
    errors: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Enrich hangar_sheet units in place. Returns the same list."""
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
                token_guard.check_or_raise(
                    max(2000, len(prompt) // 3 + _TAG_MAX_TOKENS), "hangar_sheet_tagging",
                )
            resp, tokens_in, tokens_out = call_llm_fn(
                model_id, prompt, max_tokens=_TAG_MAX_TOKENS,
            )
            if token_guard is not None:
                token_guard.record_usage(
                    tokens_in + tokens_out, "hangar_sheet_tagging",
                    tokens_in=tokens_in, tokens_out=tokens_out, model=model_id,
                )
            parsed = _normalize_tag_response(_parse_tag_json(resp))

            by_sheet: Dict[int, Dict[str, Any]] = {}
            for item in parsed:
                try:
                    idx = item.get("sheet_index")
                    if idx is None:
                        idx = item.get("pdf_page")
                    s = int(idx)
                except (TypeError, ValueError):
                    continue
                by_sheet[s] = item

            for unit in batch:
                meta = unit.get("metadata") or {}
                try:
                    s = int(meta.get("sheet_index"))
                except (TypeError, ValueError):
                    try:
                        s = int(unit.get("unit_number")) - 1
                    except (TypeError, ValueError):
                        _stamp_status(unit, TAGGING_FAILED, "missing_sheet_index")
                        continue
                if s in by_sheet:
                    _apply_sheet_tag(unit, by_sheet[s])
                else:
                    _stamp_status(unit, TAGGING_FAILED, "missing_from_llm_response")
        except Exception as exc:
            msg = f"hangar_sheet_tagging batch@{start}: {exc}"
            log.warning(msg)
            err_list.append(msg)
            for unit in batch:
                _stamp_status(unit, TAGGING_FAILED, str(exc))
            continue

    return units


__all__ = ["tag_hangar_sheet_units", "tagger_config_from_pipeline"]
