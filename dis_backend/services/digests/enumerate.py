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
from services.digests.profiles.base import store_target

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
    #: resolved_day -> the handbook/textbook passages that day's calendar row
    #: assigns as reading (services.digests.references). Separate from
    #: ``units_by_day`` because these units belong to no block and must never be
    #: counted as the block's own material — they are shared works the day cites.
    references_by_day: Dict[int, Any] = field(default_factory=dict)
    declared_acs: List[str] = field(default_factory=list)
    acs_by_day: Dict[int, List[str]] = field(default_factory=dict)
    duplicate_calendar_ids: List[str] = field(default_factory=list)
    attribution: Dict[str, Any] = field(default_factory=dict)
    flags: List[str] = field(default_factory=list)
    #: Source Library document (job) ids the requester pinned on the form, and how
    #: many units they contributed after de-duplication. Echoed back to CAS so a
    #: pinned document that reached the server can be distinguished from one that
    #: was dropped in transit — the caller cannot otherwise tell, because a server
    #: that ignores the field answers exactly like one that honoured it.
    pinned_document_ids: List[str] = field(default_factory=list)
    #: The subset of ``pinned_document_ids`` that actually contributed units. CAS
    #: verifies per id, so an aggregate count is not enough: "2 documents pinned,
    #: 40 units added" cannot distinguish both landing from one landing twice.
    pinned_applied_ids: List[str] = field(default_factory=list)
    pinned_unit_count: int = 0
    #: day_number -> {"projects"/"assessments"/"hangar": [...]} read out of each
    #: day's own calendar row by a model and checked back against that row
    #: (worksheets.compose_day_items). Supplements the pattern extraction, which
    #: can only find items shaped like a pattern someone thought to write.
    composed_day_items: Dict[int, Dict[str, List[str]]] = field(default_factory=dict)

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
                    # Counted separately from unit_count: these are shared works
                    # the day cites, not the block's own material, and conflating
                    # the two would make a day taught entirely from the handbook
                    # look as though the block had ingested content for it.
                    "reference_unit_count": len(
                        getattr(self.references_by_day.get(d["day_number"]), "units", []) or []),
                    "acs_codes": self.acs_by_day.get(d["day_number"], []),
                    **_day_worksheet_fields(d, self.units_by_day.get(d["day_number"], []),
                                            self.references_by_day.get(d["day_number"]),
                                            self.composed_day_items.get(d["day_number"])),
                }
                for d in self.days
            ],
            "declared_acs": self.declared_acs,
            "declared_acs_count": len(self.declared_acs),
            "duplicate_calendar_ids": self.duplicate_calendar_ids,
            "attribution": self.attribution,
            "flags": self.flags,
            # The acknowledgement CAS checks for. Present even when empty so the
            # caller can tell "this server understands pinning and none was asked
            # for" from "this server predates the field".
            "extra_documents_applied": {
                "requested": self.pinned_document_ids,
                "applied": self.pinned_applied_ids,
                "units_added": self.pinned_unit_count,
            },
        }
        if include_units:
            summary["units_by_day"] = {
                str(dn): [_unit_descriptor(u) for u in units]
                for dn, units in sorted(self.units_by_day.items())
            }
            summary["unattributed"] = [_unit_descriptor(u) for u in self.unattributed]
        return summary


def _day_worksheet_fields(day: Dict[str, Any], units: List[Dict[str, Any]],
                          day_references: Any = None,
                          composed: Optional[Dict[str, List[str]]] = None) -> Dict[str, Any]:
    from services.digests import worksheets  # lazy: worksheets stays a leaf module
    return worksheets.build_day_fields(day, units, day_references, composed)


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


def enumerate_block(tenant_cfg: TenantConfig, block: str, client_id: str = "",
                    extra_document_ids: Optional[List[str]] = None) -> EnumerateResult:
    """Enumerate + attribute one block. Read-only; raises on misconfiguration.

    ``extra_document_ids`` are Source Library document (job) ids the requester
    pinned on the generation form. They are ADDITIVE: their units join the
    block's own and are day-attributed by the same rules, so a pinned document
    can only ever add material. Nothing here filters the block down to them —
    a block-wide deliverable narrowed to a document subset would silently drop
    days the requester can see in the panel.
    """
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

    pinned_ids = [str(d).strip() for d in (extra_document_ids or []) if str(d).strip()]
    pinned: List[Dict[str, Any]] = []
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.read_only = True  # belt-and-suspenders; only SELECTs are issued
        # Logged every build so the store a deployment READ is a fact in the log,
        # not an inference from config. The env overrides that could silently
        # repoint it are gone (settings, "Where the backing stores live"), but a
        # stale image, an unmerged config edit or a hand-edited mounted YAML can
        # still diverge — and on 2026-08-27 that divergence surfaced only as "No
        # calendar found for block 'Block 9'", settled by connecting to two
        # databases by hand. One line turns that into "reading the wrong database".
        log.info("enumerate: client=%s block=%s store=%s schema=%s",
                 cid, block, store_target(conn), schema)
        with conn.cursor() as cur:
            scope = profile.load_scope(cur, schema, cid, block)
            if pinned_ids:
                pinned = _load_pinned_units(cur, schema, cid, pinned_ids, scope.units)

    if pinned:
        scope.units = list(scope.units) + pinned

    result = _assemble(block, cid, profile, scope, tenant_cfg)
    result.pinned_document_ids = pinned_ids
    result.pinned_unit_count = len(pinned)
    if pinned_ids:
        found = {str((u.get("metadata_json") or {}).get("job_id") or "") for u in pinned}
        result.pinned_applied_ids = [d for d in pinned_ids if d in found]
        missing = [d for d in pinned_ids if d not in found]
        if missing:
            # Named, not counted: a pinned document that contributed nothing is a
            # document the requester believes is in their deliverable and is not.
            result.flags.append(
                f"PINNED_DOCUMENT_EMPTY:{len(missing)} — pinned document(s) "
                f"{', '.join(missing[:_UNRESOLVED_CITATIONS_IN_FLAG])} contributed no "
                f"content units to this block (already part of it, not ingested for "
                f"this client, or ingested with no extractable text)")
    return result


def _load_pinned_units(cur, schema: str, client_id: str, document_ids: List[str],
                       already: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Units of explicitly pinned Source Library documents, minus any already loaded.

    ``document_ids`` are job ids — the same identifier the Source Library lists and
    the sync path pins with (``filters.document_ids`` in context_retrieval, where
    job_ids/document_ids are one field). Generic across tenants, so this lives in the
    shared assembler rather than a CurriculumProfile.

    De-duplicated against the block's own units by ``content_unit_id``: pinning a
    document that the block tag already covers must not double its text in the MAP
    prompt, which would both waste budget and over-weight it in the extraction.
    """
    seen = {u.get("content_unit_id") for u in already}
    cur.execute(
        f"""SELECT content_unit_id, unit_type, title, text_content, content_hash,
                   metadata_json
              FROM {schema}.dis_content_units
             WHERE client_id = %s AND metadata_json->>'job_id' = ANY(%s)""",
        (client_id, document_ids),
    )
    return [r for r in cur.fetchall() if r.get("content_unit_id") not in seen]


#: How many distinct unresolved citations a flag names before saying "and others".
#: Same legibility bound, and same reason, as _UNRESOLVED_NAMES_IN_FLAG.
_UNRESOLVED_CITATIONS_IN_FLAG = 6


def _resolve_references(days: List[Dict[str, Any]], scope):
    """Place each day's assigned reading, and report every citation that missed.

    Returns ``(references_by_day, flags)``. Never raises: a block whose reference
    works cannot be indexed must still enumerate, because the block's own units
    and its calendar are unaffected by that failure.

    Every unresolved citation reaches a flag. A day that cites a handbook this
    corpus has not ingested is a coverage gap the reviewer can act on — 48 AIM day
    rows cite 8083-32B, which is not ingested at all — and it is invisible in the
    delivered document, where such a day simply reads a little thinner than its
    neighbours.
    """
    from services.digests import references as refs_mod

    references_by_day: Dict[int, Any] = {}
    flags: List[str] = []
    reference_units = list(getattr(scope, "reference_units", None) or [])
    if not reference_units:
        return references_by_day, flags

    try:
        by_file = refs_mod.index_by_file(reference_units)
        for day in days:
            resolved = refs_mod.resolve_day(day, by_file)
            if resolved.citations:
                references_by_day[day["day_number"]] = resolved
    except Exception as exc:  # noqa: BLE001 — reading is additive; never sink enumerate
        log.warning("assigned-reading resolution failed: %s", exc, exc_info=True)
        return {}, [f"ASSIGNED_READING_FAILED — {type(exc).__name__}: {exc}; no day "
                    f"received the handbook reading its calendar assigns"]

    missing: Dict[str, List[int]] = defaultdict(list)
    approximate: Dict[str, List[int]] = defaultdict(list)
    for dn, resolved in references_by_day.items():
        for citation, reason in resolved.unresolved:
            (missing if not resolved.units else approximate)[reason].append(dn)
    for reason, dns in sorted(missing.items()):
        flags.append(f"READING_NOT_INGESTED — {reason} — cited on day(s) "
                     f"{_days_phrase(dns)}")
    for reason, dns in sorted(approximate.items()):
        flags.append(f"READING_APPROXIMATE — {reason} — day(s) {_days_phrase(dns)}")
    return references_by_day, flags


def _days_phrase(day_numbers: List[int]) -> str:
    """``1, 2, 3 and 4 others`` — bounded so one bad handbook can't flood a flag."""
    ordered = sorted(set(day_numbers))
    head = ordered[:_UNRESOLVED_CITATIONS_IN_FLAG]
    rest = len(ordered) - len(head)
    text = ", ".join(str(d) for d in head)
    return f"{text} and {rest} other(s)" if rest > 0 else text


def _assemble(block, client_id, profile, scope, tenant_cfg=None) -> EnumerateResult:
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

    # ASSIGNED READING — resolved before the flags below so THIN_DAY accounts for
    # the handbook passages a day was given, not just the block's own units.
    references_by_day, reference_flags = _resolve_references(days, scope)

    flags: List[str] = []
    # Profile notes first: they describe the scope every later flag is computed
    # from, so a reader sees "these tags were merged" before the day counts.
    flags.extend(n for n in getattr(scope, "notes", []) if n)
    if total_days and enumerated_days < total_days:
        # Name the gap. "enumerated 9 days != total_days 20" tells a reviewer a
        # number is wrong; the missing day numbers tell them which rows to go and
        # find. total_days is now the highest day number the calendar's own rows
        # carry (see profiles.aim._reconcile_total_days), so a shortfall here is
        # always a genuine hole in the day sequence rather than an extractor's
        # miscount, and the holes are enumerable.
        missing = [d for d in range(1, total_days + 1) if d not in day_numbers]
        flags.append(
            f"BLOCK_INCOMPLETE — enumerated {enumerated_days} of {total_days} days; "
            f"the calendar has no row for day(s) {missing}"
            if missing else
            f"BLOCK_INCOMPLETE — enumerated {enumerated_days} days != total_days {total_days}")
    elif total_days and enumerated_days > total_days:
        # Distinct from BLOCK_INCOMPLETE, and newly reachable: day rows are now
        # merged across a block's duplicate calendars, so a day number present
        # only in a lower-ranked duplicate widens the set, while total_days still
        # comes from the canonical calendar's own column. More days than declared
        # is a disagreement between calendars, not a missing part of the block,
        # and calling it "incomplete" sends a reviewer looking for the opposite
        # problem.
        flags.append(f"CALENDAR_DAYS_EXCEED_DECLARED — enumerated {enumerated_days} days "
                     f"but the canonical calendar declares total_days {total_days}; "
                     f"the block's calendars disagree on how many days it has")
    # Only the calendars that were actually available to merge from. The ones
    # describing a different schedule get their own CALENDAR_SCHEDULE_MISMATCH
    # note from the profile, which says something a reader can act on; listing
    # them here as well made a Block 9 report say "4 other calendars" and "3 of
    # them are a different schedule" in two flags a line apart, leaving the
    # fourth unaccounted for.
    foreign = set(getattr(profile, "_foreign_schedule_ids", None) or [])
    merged_dups = [c for c in dup_ids if c not in foreign]
    if merged_dups:
        flags.append(f"DUPLICATE_CALENDAR — {len(merged_dups)} other calendar(s) for this "
                     f"block hold the same schedule and were used to fill gaps: {merged_dups}")
    for dn in sorted(day_numbers):
        day_units = units_by_day.get(dn, [])
        refs = references_by_day.get(dn)
        if any((u.get("unit_type") in SUBSTANTIVE) for u in day_units):
            continue
        if refs and refs.units:
            # The block ingested nothing of its own for this day, but the calendar
            # assigned reading and that reading resolved — so the day is NOT thin,
            # it is taught from the handbook. Reported anyway, because a day with
            # no lesson material of its own is a real content gap even when the
            # digest is well fed.
            flags.append(f"READING_ONLY_DAY:{dn} — no ingested block material; "
                         f"taught from the assigned reading ({refs.label()})")
            continue
        flags.append(f"THIN_DAY:{dn} — no substantive source units")
    flags.extend(reference_flags)
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

    # Read every day's own row for the projects, assessments and hangar activities
    # it names. One call for the block, checked back against each row — see
    # worksheets.compose_day_items. Placed here, after the flags list exists, so a
    # row that did not fit the budget or an item that failed its check is reported
    # rather than quietly absent.
    from services.digests import worksheets as _worksheets
    composed_day_items = _worksheets.compose_day_items(days, tenant_cfg, flags)

    return EnumerateResult(
        block=block,
        client_id=client_id,
        calendar_id=calendar_id,
        total_days=total_days,
        enumerated_days=enumerated_days,
        days=days,
        units_by_day=dict(units_by_day),
        unattributed=unattributed,
        references_by_day=references_by_day,
        composed_day_items=composed_day_items,
        declared_acs=sorted(declared, key=attribution.acs_sort_key),
        acs_by_day={dn: sorted(codes, key=attribution.acs_sort_key) for dn, codes in acs_by_day.items()},
        duplicate_calendar_ids=dup_ids,
        attribution=attribution_stats,
        flags=flags,
    )
