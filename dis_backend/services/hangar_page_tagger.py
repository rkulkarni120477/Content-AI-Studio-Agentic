"""Batched LLM content tagging for ``hangar_page`` PDF units.

Same contract as ebook page tagging (topics / summary / ACS / tagging_status),
with a shop/lab-oriented prompt instead of handbook-chapter language.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Sequence

from services.ebook_page_tagger import (
    TAGGING_FAILED,
    TAGGING_OK,
    _MAX_PAGE_CHARS,
    _TAG_MAX_TOKENS,
    _DEFAULT_BATCH_SIZE,
    _normalize_tag_response,
    _page_excerpt,
    _parse_tag_json,
    _stamp_status,
    mark_units_pending,
    tagger_config_from_pipeline,
    validate_acs_codes,
)

log = logging.getLogger(__name__)


def _build_batch_prompt(batch: Sequence[Dict[str, Any]]) -> str:
    parts = [
        "Tag each AIM hangar / shop activity PDF page for aviation maintenance retrieval.",
        "Focus on tools, procedures, safety, materials, and practical skills — not handbook chapters.",
        "Return JSON only: a top-level ARRAY of objects (not an object wrapper),",
        "one object per page, with keys:",
        "  pdf_page (int), topics (3-8 short phrases), acs_codes (list of AM.* codes),",
        "  summary (at most two sentences).",
        "Rules:",
        "- Only include ACS codes that appear in the page text or are clearly implied.",
        "- ACS codes MUST match AM.<roman>.<letter>.<K|R|S><digits> (e.g. AM.I.D.K1).",
        "- If unsure about an ACS code, omit it.",
        "- topics must be short noun phrases, not full sentences.",
        "- Do NOT wrap the array in {\"pages\": ...} or any other object.",
        "",
        "Pages:",
    ]
    for unit in batch:
        meta = unit.get("metadata") or {}
        pdf_page = meta.get("pdf_page") or unit.get("unit_number")
        loc = meta.get("page_number") or ""
        block = meta.get("block") or ""
        day = meta.get("day_number") or ""
        parts.append(
            f"--- pdf_page={pdf_page} page_number={loc} block={block!r} day={day} ---"
        )
        parts.append(_page_excerpt(unit.get("text") or "", _MAX_PAGE_CHARS))
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


def tag_hangar_page_units(
    units: List[Dict[str, Any]],
    *,
    call_llm_fn: Callable[..., Any],
    model_id: str,
    batch_size: int = _DEFAULT_BATCH_SIZE,
    enabled: bool = True,
    token_guard: Any = None,
    errors: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Enrich hangar_page units in place. Returns the same list."""
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
                    max(2000, len(prompt) // 3 + _TAG_MAX_TOKENS), "hangar_page_tagging",
                )
            resp, tokens_in, tokens_out = call_llm_fn(
                model_id, prompt, max_tokens=_TAG_MAX_TOKENS,
            )
            if token_guard is not None:
                token_guard.record_usage(
                    tokens_in + tokens_out, "hangar_page_tagging",
                    tokens_in=tokens_in, tokens_out=tokens_out, model=model_id,
                )
            parsed = _normalize_tag_response(_parse_tag_json(resp))

            by_page: Dict[int, Dict[str, Any]] = {}
            for item in parsed:
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
            msg = f"hangar_page_tagging batch@{start}: {exc}"
            log.warning(msg)
            err_list.append(msg)
            for unit in batch:
                _stamp_status(unit, TAGGING_FAILED, str(exc))
            continue

    return units


__all__ = ["tag_hangar_page_units", "tagger_config_from_pipeline"]
