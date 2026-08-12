"""Day-scoped structured retrieval (plan §7) for DLU / Learn It / Today's Mission.

Day-scoped generation is a *precision* problem, not a completeness one, and it
becomes easy once the digest tier exists. Instead of a mushy similarity query over
the whole prompt (the §5.4 anti-pattern), we:

  1. **structured-first fetch** — given ``block + day``, return *every* unit placed
     on that day (complete, deterministic), reusing ENUMERATE's attribution so
     NULL-day guides/projects/quizzes are included, plus the day's calendar row;
  2. attach the day's **digest** (the same one block-wide reduce uses), if built;
  3. add a **bounded kNN supplement** — related handbook pages *beyond* the day's
     own sources — using a narrow, facet-derived query (topic / objective / ACS),
     never the whole prompt blob.

One day fits one LLM call, so there is no map-reduce here. This is read-only: it
runs ENUMERATE (SELECT-only) and OpenSearch reads; it never writes or DDLs.

The §8.4 text gate applies: metadata (ACS/topic/day) is always returned for
coverage, but a unit's ``text`` is withheld when the audience isn't allowed to see
it (answer keys never; instructor-only text only for instructor-facing use).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from config.settings import TenantConfig
from services import indexing
from services.digests import mapper
from services.digests.enumerate import enumerate_block

log = logging.getLogger(__name__)

# Bound the kNN supplement so a day-scoped call stays cheap and precise.
DEFAULT_SUPPLEMENT_K = 6
_SUPPLEMENT_TEXT_CAP = 1200  # chars per supplemental hit (bounded context)


def _unit_projection(unit: Dict[str, Any], audience: str) -> Dict[str, Any]:
    """Light, text-gated view of a day unit. Metadata always present; ``text`` is
    None when §8.4 withholds it (the field stays so the caller sees it was gated)."""
    md = unit.get("metadata_json") or {}
    allowed = mapper.text_allowed_for_digest(unit, audience)
    return {
        "content_unit_id": unit.get("content_unit_id"),
        "unit_type": unit.get("unit_type"),
        "title": unit.get("title"),
        "acs_codes": [c for c in (md.get("acs_codes") or []) if c],
        "attribution_signal": unit.get("attribution_signal"),
        "text": (unit.get("text_content") or "") if allowed else None,
        "text_withheld": not allowed,
    }


def _supplement_allowed(hit: Dict[str, Any], audience: str) -> bool:
    """§8.4 gate for a kNN supplement hit. vector_search returns OpenSearch _source
    dicts (metadata under ``metadata``, not ``metadata_json``); normalize to the
    unit shape ``text_allowed_for_digest`` expects and reuse that single gate so the
    supplement can never leak answer-key or restricted/instructor-only text that the
    day's own units would withhold."""
    normalized = {
        "unit_type": hit.get("unit_type"),
        "metadata_json": hit.get("metadata") or {},
    }
    return mapper.text_allowed_for_digest(normalized, audience)


def _supplement_query(day_row: Optional[Dict[str, Any]],
                      digest: Optional[Dict[str, Any]],
                      acs_codes: List[str]) -> str:
    """A NARROW query built from structural facets (§5.4) — topic, derived
    objective, ACS labels — NOT the caller's whole prompt. Empty ⇒ skip kNN."""
    parts: List[str] = []
    if day_row:
        parts += [day_row.get("topic") or "", day_row.get("lesson_title") or ""]
    if digest:
        parts.append(digest.get("derived_objective") or "")
    parts += acs_codes[:8]
    return " ".join(p for p in parts if p).strip()


def day_context(
    tenant_cfg: TenantConfig,
    block: str,
    day: int,
    client_id: str = "",
    audience: str = "instructor",
    supplement_k: int = DEFAULT_SUPPLEMENT_K,
) -> Dict[str, Any]:
    """Assemble the complete day bundle for a day-scoped generator.

    Read-only. ENUMERATE errors propagate (misconfigured block); the kNN
    supplement is best-effort — a vector-store failure degrades to an empty
    supplement with a flag, never a failed fetch.
    """
    try:
        day = int(day)
    except (TypeError, ValueError):
        raise ValueError(f"day must be an integer day_number, got {day!r}")

    result = enumerate_block(tenant_cfg, block, client_id)
    cid = result.client_id

    day_units = result.units_by_day.get(day, [])
    day_row = next((d for d in result.days if d.get("day_number") == day), None)
    acs_codes = result.acs_by_day.get(day, [])

    flags: List[str] = []
    if day_row is None:
        flags.append(f"DAY_NOT_FOUND:{day} — no calendar row for this day in the canonical calendar")
    if not any(u.get("unit_type") in mapper.SUBSTANTIVE_UNIT_TYPES for u in day_units):
        flags.append(f"THIN_DAY:{day} — no substantive source units placed on this day")

    # ── Digest (same artifact block-wide reduce uses) ────────────────────────
    day_digest: Optional[Dict[str, Any]] = None
    try:
        for d in indexing.fetch_digests(tenant_cfg, block, cid):
            if d.get("day_number") == day:
                day_digest = d
                break
    except Exception as exc:  # noqa: BLE001 — digest is optional context
        log.warning("day_context: digest fetch failed for block=%s day=%s: %s", block, day, exc)
        flags.append("DIGEST_FETCH_FAILED")
    if day_digest is None and "DIGEST_FETCH_FAILED" not in flags:
        flags.append(f"DIGEST_MISSING:{day} — build digests to enrich day-scoped context")
    # Digests are built for the instructor audience and can quote instructor-only
    # source verbatim in salient_excerpts. For a student-facing deliverable, drop
    # that verbatim field (keep the synthesized objective/ACS/concept_type). §8.4.
    if day_digest is not None and audience == "student" and day_digest.get("salient_excerpts"):
        day_digest = {k: v for k, v in day_digest.items() if k != "salient_excerpts"}
        flags.append("DIGEST_EXCERPTS_WITHHELD — student audience")

    # ── Bounded kNN supplement (precision boost, beyond the day's own units) ─
    supplement: List[Dict[str, Any]] = []
    query_text = _supplement_query(day_row, day_digest, acs_codes)
    own_ids = {u.get("content_unit_id") for u in day_units}
    if supplement_k and query_text:
        try:
            emb = indexing.embed_query(tenant_cfg, query_text)
            hits = indexing.vector_search(tenant_cfg, cid, query_text, emb, size=max(supplement_k * 3, 12))
            for h in hits:
                if h.get("content_unit_id") in own_ids:
                    continue
                if h.get("unit_type") == indexing.DIGEST_UNIT_TYPE:
                    continue
                # §8.4 gate — vector_search enforces ONLY client_id isolation (see
                # its docstring: "Security is NOT enforced here"), so answer-key and
                # restricted/instructor-only bodies are matchable. Apply the SAME
                # text gate the day's own units use before returning any text.
                if not _supplement_allowed(h, audience):
                    continue
                text = (h.get("text") or "")[:_SUPPLEMENT_TEXT_CAP]
                supplement.append({
                    "content_unit_id": h.get("content_unit_id"),
                    "unit_type": h.get("unit_type"),
                    "title": h.get("title"),
                    "day_number": h.get("day_number"),
                    "score": round(float(h.get("_score", 0.0)), 4),
                    "text": text,
                })
                if len(supplement) >= supplement_k:
                    break
        except Exception as exc:  # noqa: BLE001 — supplement is best-effort
            log.warning("day_context: kNN supplement failed for block=%s day=%s: %s", block, day, exc)
            flags.append("SUPPLEMENT_FAILED")

    units = [_unit_projection(u, audience) for u in day_units]
    return {
        "block": block,
        "client_id": cid,
        "day_number": day,
        "audience": audience,
        "topic": (day_row or {}).get("topic"),
        "lesson_title": (day_row or {}).get("lesson_title"),
        "week_number": (day_row or {}).get("week_number"),
        "day_source_text": (day_row or {}).get("source_text"),
        "acs_codes": acs_codes,
        "unit_count": len(units),
        "units": units,
        "digest": day_digest,
        "supplement": supplement,
        "flags": flags,
    }
