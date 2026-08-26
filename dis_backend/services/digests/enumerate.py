"""ENUMERATE — deterministic block inventory that OWNS day-attribution.

Given a tenant + block, this reads the DIS structure store (read-only) and returns
the ground truth a block-wide CDD / Blueprint reduce must cover:

  * the ordered, de-duplicated calendar days,
  * every content unit placed on a day (attributing NULL-day units via
    ``attribution.attribute`` — S1 item cross-ref -> S2 filename token -> S3 term
    overlap), never silently dropping the ones that can't be placed,
  * the declared-ACS set (union of ``metadata_json.acs_codes`` across the block),
  * coverage flags (BLOCK_INCOMPLETE / DUPLICATE_CALENDAR / THIN_DAY / UNATTRIBUTED).

This is Slice A: read-only, no LLM, no writes to dis_db. ``resolved_day`` is
computed on the fly per call (plan §2.3a) — nothing is persisted here.

Connection mirrors ``services/indexing.py`` (``dsn = cfg.url`` fallback), but
SELECT-only with ``conn.read_only = True`` and it NEVER calls ``ensure_schema``.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from config.settings import TenantConfig, get_settings
from services.digests import attribution
from services.digests.profiles import get_curriculum_profile

log = logging.getLogger(__name__)

# Unit types with teachable substance (a day represented only by these is not
# "thin"). Kept in sync with attribution.SUBSTANTIVE.
SUBSTANTIVE = attribution.SUBSTANTIVE


@dataclass
class EnumerateResult:
    block: str
    client_id: str
    calendar_id: str
    total_days: int
    enumerated_days: int
    days: List[Dict[str, Any]] = field(default_factory=list)
    # resolved_day -> units (full rows; kept in-process for MAP in Slice B).
    units_by_day: Dict[int, List[Dict[str, Any]]] = field(default_factory=dict)
    unattributed: List[Dict[str, Any]] = field(default_factory=list)
    declared_acs: List[str] = field(default_factory=list)
    acs_by_day: Dict[int, List[str]] = field(default_factory=dict)
    duplicate_calendar_ids: List[str] = field(default_factory=list)
    attribution: Dict[str, Any] = field(default_factory=dict)
    flags: List[str] = field(default_factory=list)

    def to_summary(self, include_units: bool = False) -> Dict[str, Any]:
        """JSON-safe projection for the HTTP endpoint.

        Heavy ``text_content`` is never returned. With ``include_units`` the
        per-day unit list is included as light descriptors (id/type/signal).
        """
        summary: Dict[str, Any] = {
            "block": self.block,
            "client_id": self.client_id,
            "calendar_id": self.calendar_id,
            "total_days": self.total_days,
            "enumerated_days": self.enumerated_days,
            "days": [
                {
                    "day_number": d["day_number"],
                    "week_number": d.get("week_number"),
                    "topic": d.get("topic"),
                    "lesson_title": d.get("lesson_title"),
                    "unit_count": len(self.units_by_day.get(d["day_number"], [])),
                    "acs_codes": self.acs_by_day.get(d["day_number"], []),
                    **_day_worksheet_fields(d, self.units_by_day.get(d["day_number"], [])),
                }
                for d in self.days
            ],
            "declared_acs": self.declared_acs,
            "declared_acs_count": len(self.declared_acs),
            "duplicate_calendar_ids": self.duplicate_calendar_ids,
            "attribution": self.attribution,
            "flags": self.flags,
        }
        if include_units:
            summary["units_by_day"] = {
                str(dn): [_unit_descriptor(u) for u in units]
                for dn, units in sorted(self.units_by_day.items())
            }
            summary["unattributed"] = [_unit_descriptor(u) for u in self.unattributed]
        return summary


def _day_worksheet_fields(day: Dict[str, Any], units: List[Dict[str, Any]]) -> Dict[str, Any]:
    from services.digests import worksheets  # lazy: worksheets stays a leaf module
    return worksheets.build_day_fields(day, units)


def _unit_descriptor(u: Dict[str, Any]) -> Dict[str, Any]:
    md = u.get("metadata_json") or {}
    return {
        "content_unit_id": u.get("content_unit_id"),
        "unit_type": u.get("unit_type"),
        "title": u.get("title"),
        "raw_day_number": md.get("day_number"),
        "resolved_day": u.get("resolved_day"),
        "attribution_signal": u.get("attribution_signal"),
        "acs_codes": md.get("acs_codes") or [],
    }


#: How many distinct source files the UNATTRIBUTED flag names before saying
#: "and others". The flag is read by a human in a coverage report, so this is a
#: legibility bound, not a memory one.
_UNRESOLVED_NAMES_IN_FLAG = 6


def _source_names(units: List[Dict[str, Any]], *, limit: int) -> List[str]:
    """Distinct source files for these units, first-seen order, bounded.

    De-duplicated by name so a 40-section instructor guide contributes one entry —
    the actionable unit of work is the document, not the chunk.
    """
    out: List[str] = []
    for u in units:
        md = u.get("metadata_json") or {}
        # Whitespace collapsed, not merely stripped: these flags are rendered as
        # single markdown bullets (block_wide_service._coverage_section), and an
        # embedded newline in a stored filename would end the bullet early and orphan
        # the rest of the line. Same defence _json_list already applies to day cells.
        name = " ".join(str(md.get("source_file_name") or u.get("title") or "").split())
        if name and name not in out:
            out.append(name)
        if len(out) >= limit:
            break
    return out


def _named_flag(prefix: str, count: int, reason: str, units: List[Dict[str, Any]]) -> str:
    """``PREFIX:n — reason — in: a, b, c`` with a bounded, de-duplicated file list.

    The file list is omitted entirely rather than rendered empty when no unit carries
    a usable name, so the flag never trails a dangling "in:".
    """
    names = _source_names(units, limit=_UNRESOLVED_NAMES_IN_FLAG)
    head = f"{prefix}:{count} — {reason}"
    if not names:
        return head
    more = ", and others" if len(names) >= _UNRESOLVED_NAMES_IN_FLAG else ""
    return f"{head} — in: {', '.join(names)}{more}"


def _resolve_dsn(cfg) -> str:
    return cfg.url or get_settings().db_url.replace("postgresql+asyncpg://", "postgresql://")


def enumerate_block(tenant_cfg: TenantConfig, block: str, client_id: str = "") -> EnumerateResult:
    """Enumerate + attribute one block. Read-only; raises on misconfiguration."""
    import psycopg
    from psycopg.rows import dict_row

    cfg = tenant_cfg.structure_store
    if not cfg.enabled:
        raise RuntimeError("structure_store.enabled=false — cannot enumerate block")
    if getattr(cfg, "provider", "postgres") != "postgres":
        raise RuntimeError(f"enumerate_block supports postgres only, not {cfg.provider!r}")

    cid = tenant_cfg.effective_client_id(client_id)
    schema = cfg.schema_name
    dsn = _resolve_dsn(cfg)

    # D8: the tenant's CurriculumProfile owns all tenant-specific reads. The shared
    # assembler below never touches dis_calendar_days / acs_codes directly (§5.5).
    profile = get_curriculum_profile(cid, tenant_cfg)

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.read_only = True  # belt-and-suspenders; only SELECTs are issued
        with conn.cursor() as cur:
            scope = profile.load_scope(cur, schema, cid, block)

    return _assemble(block, cid, profile, scope)


def _assemble(block, client_id, profile, scope) -> EnumerateResult:
    calendar_id = scope.calendar_id
    dup_ids = scope.duplicate_calendar_ids
    total_days = scope.total_days
    days = scope.days
    units = scope.units
    enumerated_days = len(days)
    day_numbers = {d["day_number"] for d in days}

    proj_ref, quiz_ref = attribution.build_calendar_refs(days)
    dterms = attribution.day_terms_by_day(days)

    units_by_day: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    unattributed: List[Dict[str, Any]] = []
    declared: set[str] = set()
    acs_by_day: Dict[int, set[str]] = defaultdict(set)

    # attribution telemetry (Slice A gate)
    unmapped_substantive = 0
    recovered = 0
    by_signal: Dict[str, int] = defaultdict(int)
    unresolved_by_type: Dict[str, int] = defaultdict(int)

    for u in units:
        md = u.get("metadata_json") or {}
        codes = profile.coverage_codes(u)
        declared.update(codes)

        raw_day = md.get("day_number")
        placed: List[int]
        if raw_day is not None:
            placed = [int(raw_day)]
            u["resolved_day"] = int(raw_day)
            u["attribution_signal"] = "raw:day_number"
        elif u.get("unit_type") in SUBSTANTIVE:
            unmapped_substantive += 1
            placed, signal, _conf = attribution.attribute(u, days, proj_ref, quiz_ref, dterms)
            by_signal[signal.split(":")[0]] += 1
            if placed:
                recovered += 1
                u["resolved_day"] = placed[0]
                u["attribution_signal"] = signal
            else:
                u["resolved_day"] = None
                u["attribution_signal"] = signal
                unresolved_by_type[u.get("unit_type") or "unknown"] += 1
        else:
            # Non-substantive with no day (e.g. syllabus_section, loose chunk).
            placed = []
            u["resolved_day"] = None
            u["attribution_signal"] = "n/a:non-substantive"

        # A resolved day (raw metadata OR attribution) must be a REAL calendar day.
        # A B#D# token or metadata day_number pointing at a day the calendar doesn't
        # have (gaps, bad tagging) would otherwise land in a phantom bucket that no
        # downstream step iterates — silently lost from digests AND coverage. Route
        # such units to `unattributed` so they're surfaced, never dropped.
        placed = [dn for dn in placed if dn in day_numbers]
        if placed:
            for dn in placed:
                units_by_day[dn].append(u)
                for c in codes:
                    acs_by_day[dn].add(c)
        else:
            if u.get("resolved_day") is not None:
                u["resolved_day"] = None
                u["attribution_signal"] = "unplaced:day-not-in-calendar"
            unattributed.append(u)

    flags: List[str] = []
    # Profile notes first: they describe the scope every later flag is computed
    # from, so a reader sees "these tags were merged" before the day counts.
    flags.extend(n for n in getattr(scope, "notes", []) if n)
    if total_days and enumerated_days != total_days:
        flags.append(f"BLOCK_INCOMPLETE — enumerated {enumerated_days} days != total_days {total_days}")
    if dup_ids:
        flags.append(f"DUPLICATE_CALENDAR — {len(dup_ids)} other calendar(s) for this block: {dup_ids}")
    for dn in sorted(day_numbers):
        day_units = units_by_day.get(dn, [])
        if not any((u.get("unit_type") in SUBSTANTIVE) for u in day_units):
            flags.append(f"THIN_DAY:{dn} — no substantive source units")
    # Only substantive units that SHOULD map to a day but couldn't are actionable.
    # Non-substantive unplaced units (syllabus sections, answer keys, loose chunks)
    # legitimately have no single day and are surfaced, not counted as a gap.
    unresolved_substantive = sum(unresolved_by_type.values())
    # Partition the unplaced list by WHY, so each flag names only the files a reviewer
    # can act on for that reason, and so the counts stop conflating them. Attribution
    # genuinely failed for the first group; the second was pointed at a day the
    # calendar does not contain (bad B#D# token or metadata day_number, or a calendar
    # with gaps) — a different fix entirely, and previously it had no flag of its own
    # AND was subtracted into `non_substantive_unplaced`, which described a slide
    # tagged "Day 9" on a 20-day calendar as material that legitimately has no day.
    failed_attribution = [u for u in unattributed
                          if not str(u.get("attribution_signal") or "").startswith(("n/a:", "unplaced:"))]
    phantom_day = [u for u in unattributed
                   if str(u.get("attribution_signal") or "").startswith("unplaced:")]
    non_substantive_unplaced = len(unattributed) - unresolved_substantive - len(phantom_day)
    if unresolved_substantive:
        # Name the FILES, not just the count. A reviewer reading "UNATTRIBUTED:7" is
        # told that seven pieces of the block are missing from every digest and given
        # nothing to act on; the fix is always per-document (tag a day, rename to
        # carry a B#D# token), so the filename is the whole actionable content.
        flags.append(_named_flag(
            "UNATTRIBUTED", unresolved_substantive,
            "substantive units that could not be placed on a day", failed_attribution))
    if phantom_day:
        flags.append(_named_flag(
            "DAY_NOT_IN_CALENDAR", len(phantom_day),
            "unit(s) tagged with a day this block's calendar does not have", phantom_day))

    attribution_stats = {
        "unmapped_substantive": unmapped_substantive,
        "recovered": recovered,
        "recovery_pct": round(100 * recovered / unmapped_substantive, 1) if unmapped_substantive else 100.0,
        "by_signal": dict(by_signal),
        "unresolved_by_type": dict(unresolved_by_type),
        "unresolved_substantive": unresolved_substantive,
        "non_substantive_unplaced": non_substantive_unplaced,
        # Counted separately rather than folded into either neighbour: these units
        # HAVE a day, it just is not one this block's calendar contains. Reported so
        # the three unplaced populations sum to len(unattributed) and a reader can
        # see which of the three they are looking at.
        "phantom_day_unplaced": len(phantom_day),
        "total_units": len(units),
    }

    return EnumerateResult(
        block=block,
        client_id=client_id,
        calendar_id=calendar_id,
        total_days=total_days,
        enumerated_days=enumerated_days,
        days=days,
        units_by_day=dict(units_by_day),
        unattributed=unattributed,
        declared_acs=sorted(declared, key=attribution.acs_sort_key),
        acs_by_day={dn: sorted(codes, key=attribution.acs_sort_key) for dn, codes in acs_by_day.items()},
        duplicate_calendar_ids=dup_ids,
        attribution=attribution_stats,
        flags=flags,
    )
