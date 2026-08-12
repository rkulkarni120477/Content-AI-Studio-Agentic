"""Day-scoped DIS grounding helpers, shared across generation routers.

Structured-first day-scoped context (§7): every unit on ``block+day`` + the
day's digest + a bounded kNN supplement, rendered into a grounding text block
for injection into an LLM prompt's free-text context/instructions field.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from app.core.dis_client import dis_client

_log = logging.getLogger(__name__)

# Mirrors dis_backend's own default StructurePatternConfig.block_regex
# (dis_backend/config/settings.py) — duplicated here because app/ and
# dis_backend/ are separate services with no shared import path. Generic
# ("Block N" / "BLK N"), not tied to any specific client or block number.
_BLOCK_LABEL_RE = re.compile(r"\b(?:Block|BLK)\s*0*(\d+)\b", re.IGNORECASE)


def infer_block_label(*titles: Optional[str]) -> Optional[str]:
    """Best-effort "Block N" label from the first title that has one, or None.

    No stored linkage exists between a course/CDD record and its DIS block
    label (it's free text a user types into the block-wide generation panel),
    so day-scoped enrichment opportunistically parses it from whatever
    course/CDD title text is available. Returns None (never a guess) when no
    title matches — callers must treat that as "enrichment unavailable", not
    fall back to some other label."""
    for title in titles:
        if not title:
            continue
        m = _BLOCK_LABEL_RE.search(title)
        if m:
            return f"Block {int(m.group(1))}"
    return None


def _render_day_context(bundle: dict, label: str) -> str:
    """Render a structured day bundle (§7) into a grounding block. Complete day
    units first (deterministic), then the day digest, then the bounded supplement.
    Text-withheld units (§8.4) are listed by title only — never their body."""
    day = bundle.get("day_number")
    lines = [
        f"\n\n---\n{label} FROM DIS SOURCE LIBRARY (structured: Block {bundle.get('block')} / Day {day})",
        "Use this as grounding. Follow active Style, approved CDD, active Blueprint, and the selected content type first. "
        "Do not expose internal DIS metadata.",
    ]
    topic = bundle.get("topic") or bundle.get("lesson_title")
    if topic:
        lines.append(f"\nDay {day} — {topic}")
    if bundle.get("acs_codes"):
        lines.append("ACS covered: " + ", ".join(bundle["acs_codes"]))
    digest = bundle.get("digest") or {}
    if digest.get("derived_objective"):
        lines.append(f"Objective: {digest['derived_objective']}")

    lines.append("\nDAY SOURCE UNITS (complete):")
    # `or []`, not a `.get(..., [])` default — DIS can return an explicit
    # "units": null (not just an absent key) alongside a non-empty digest;
    # a bare default only covers the missing-key case and would otherwise
    # raise iterating None, caught by _dis_day_context_block's caller and
    # discarding the whole day context (including a perfectly good digest)
    # over what should have been a harmless empty list. Matches the same
    # idiom already used two lines below in _dis_day_context_block.
    for u in bundle.get("units") or []:
        head = f"• [{u.get('unit_type')}] {u.get('title') or u.get('content_unit_id')}"
        if u.get("text_withheld"):
            lines.append(head + "  (restricted — title only)")
        elif u.get("text"):
            lines.append(head + "\n" + u["text"])
        else:
            lines.append(head)

    supplement = bundle.get("supplement") or []
    if supplement:
        lines.append("\nRELATED HANDBOOK PAGES (supplemental, beyond this day):")
        for s in supplement:
            lines.append(f"• [{s.get('unit_type')}] {s.get('title') or s.get('content_unit_id')}\n{s.get('text') or ''}")
    lines.append("---\n")
    return "\n".join(lines)


def _dis_day_context_block(block: str, day: int, current_user, label: str,
                           audience: str = "instructor", client_id: str = "") -> tuple[str, list]:
    """Structured-first day-scoped grounding (§7). Falls back to ('', []) on any
    DIS failure so the caller can degrade to the legacy blob-query path."""
    try:
        bundle = dis_client.get_day_context_sync(
            block, day, audience=audience, current_user=current_user, client_id=client_id,
        )
        units = list(bundle.get("units") or []) + list(bundle.get("supplement") or [])
        if bundle.get("units") or bundle.get("digest") or bundle.get("supplement"):
            return _render_day_context(bundle, label), units
    except Exception as exc:
        _log.warning("dis_day_context_unavailable block=%s day=%s error=%s", block, day, exc)
    return "", []


def resolve_day_context_block(day_number: Optional[int], dis_client_id: str, current_user,
                              label: str, *titles: Optional[str]) -> str:
    """Day-scoped grounding for a single-item generation request, or "" when
    not applicable — one call wrapping the three independent gates a caller
    would otherwise need to check separately: no day_number, digest pipeline
    disabled for this client, or no "Block N" parseable from ``titles``. Any
    of these (or a DIS failure inside ``_dis_day_context_block``) leaves this
    a no-op, never an error."""
    from app.core.config import settings

    if not day_number or not settings.digest_pipeline_on_for(dis_client_id):
        return ""
    block_label = infer_block_label(*titles)
    if not block_label:
        return ""
    day_context_block, _ = _dis_day_context_block(
        block_label, day_number, current_user, label, client_id=dis_client_id,
    )
    return day_context_block
