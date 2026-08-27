"""AIM CurriculumProfile — profile #1 (plan §5.5 / D8).

AIM's curriculum is Block → **Day** (``dis_calendar_days``) with an **ACS-code**
coverage set (``dis_content_units.metadata_json.acs_codes``). This profile owns
every AIM-specific read; the shared ``enumerate_block`` assembler calls through it
and touches no AIM table directly.

The SQL here is lifted verbatim from the original Slice-A ``enumerate.py`` so AIM
enumeration output is byte-for-byte unchanged by the D8 refactor.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from services.blocks import BLOCK_KEY_SQL, block_key, spelling_note
from services.digests.profiles.base import CurriculumProfile, ScopeData, store_target


#: An extracted cell that is really the whole table row pasted back in. The
#: 'Block 09' calendars store their ENTIRE flattened day row in assignments_json
#: and assessments_json — "Day 4 | LANDING GEAR SYSTEMS Aircraft wheels Wheel
#: construction Wheel inspection | Reading: ..." — so the Projects Today column
#: reported a paragraph of lesson topics as though it were the day's project.
_ROW_RESTATEMENT_RE = re.compile(r"^\s*Day\s*\d+\s*\|(?P<rest>.*)$",
                                 re.IGNORECASE | re.DOTALL)

#: How much text may follow a "Day N |" label before the cell is judged to be the
#: whole row rather than a label. The prefix ALONE is not evidence: a calendar may
#: legitimately write "Day 3 | Project 9-2", and rejecting that would discard a
#: real assignment and then either borrow a sibling calendar's value or blank the
#: cell. The leak is identifiable by its SIZE and by carrying further column
#: separators — the real rows run 80+ characters across three or more columns.
_ROW_RESTATEMENT_MAX_LABEL = 60


def _is_row_restatement(text: str) -> bool:
    """Whether a cell is its own table row pasted back in, not an extracted value."""
    m = _ROW_RESTATEMENT_RE.match(str(text or ""))
    if not m:
        return False
    rest = m.group("rest").strip()
    return len(rest) > _ROW_RESTATEMENT_MAX_LABEL or "|" in rest

#: A header row captured as data: the spreadsheet's own column titles. Seen live
#: on Block 9 day 1, whose assignments cell held "Day | Learning Objectives |
#: Corresponding Assignment".
_HEADER_ROW_RE = re.compile(r"^\s*Day\s*\|", re.IGNORECASE)


def _usable(value: Any, fieldname: str = "") -> bool:
    """Whether a calendar cell carries real extracted content.

    Empty is not usable, and neither is an EXTRACTED cell that merely restates
    the row it came from — that is an extraction that failed open, and treating
    it as a value is worse than treating it as missing, because a missing cell
    can be filled from a sibling calendar while a junk one cannot be told from a
    real one downstream.

    The restatement test applies ONLY to the extracted ``*_json`` columns.
    ``source_text`` IS the raw row and legitimately begins "Day 1 | LANDING GEAR
    SYSTEMS ..." — rejecting it there discarded the canonical calendar's own day
    text and fell back to a sparser sibling, taking Block 9's reading citations
    from 20 of 20 days back down to 13.
    """
    if value is None:
        return False
    text = str(value).strip()
    if not text or text in ("[]", "{}", "null"):
        return False
    extracted = fieldname.endswith("_json")
    if not extracted:
        return True
    if _HEADER_ROW_RE.search(text) or _is_row_restatement(text):
        return False
    # A JSON list whose every item restates the row is junk even when the list
    # itself is well-formed — the leak is inside the items, not the container.
    if text.startswith("["):
        try:
            items = json.loads(text)
        except (TypeError, ValueError):
            return True
        if isinstance(items, list):
            real = [i for i in items
                    if str(i).strip()
                    and not _HEADER_ROW_RE.search(str(i))
                    and not _is_row_restatement(str(i))]
            return bool(real)
    return True


class AIMCurriculumProfile(CurriculumProfile):
    coverage_label = "ACS"

    def load_scope(self, cur, schema: str, client_id: str, block: str) -> ScopeData:
        calendar_id, dup_ids, total_days, cal_spellings = self._pick_calendar(
            cur, schema, client_id, block)
        days = self._load_days(cur, schema, calendar_id, dup_ids)
        units, unit_spellings = self._load_units(cur, schema, client_id, block)
        # Surfaced, not just survived: matching through the key is what makes the
        # lookup work at all on data written by two disagreeing writers, but the
        # data IS inconsistent and the worksheet's reader is the person who can
        # get it fixed. See services/blocks.spelling_note.
        note = spelling_note(block, list(cal_spellings) + list(unit_spellings))
        notes = [note] if note else []
        references = self._load_reference_works(cur, schema, client_id)
        if not references:
            # Said out loud, because its effect is invisible in the output: with no
            # reference works loaded, every day whose calendar assigns a handbook
            # reading silently falls back to the calendar row alone, which is the
            # shape that produced 18 THIN_DAY flags on Block 9.
            notes.append("NO_REFERENCE_WORKS — no ebook_reference documents are "
                         "ingested for this client, so no day can be given the "
                         "handbook reading its calendar assigns")
        return ScopeData(
            calendar_id=calendar_id,
            total_days=total_days,
            days=days,
            units=units,
            reference_units=references,
            duplicate_calendar_ids=dup_ids,
            notes=notes,
        )

    def coverage_codes(self, unit: Dict[str, Any]) -> List[str]:
        # A knowledge-test report's codes are EVIDENCE ABOUT codes (which ones
        # cohorts miss), not a declaration that this block teaches them — its top-10
        # list routinely names codes from other blocks. Counting them as declared
        # would inflate the block's coverage set and then report those same codes as
        # taught on no day.
        if unit.get("unit_type") == "knowledge_test_item":
            return []
        codes = (unit.get("metadata_json") or {}).get("acs_codes") or []
        return [c for c in codes if c]

    # -- AIM reads (SELECT-only; caller's connection is read_only) ------------
    def _pick_calendar(self, cur, schema: str, client_id: str, block: str):
        """Canonical calendar = most day rows, then most day TEXT, then newest.

        Phase 0 found 3 Block-2 calendars; the richest one is authoritative. Others
        are reported as DUPLICATE_CALENDAR, never silently dropped.

        The middle term is what stops a tie being decided by upload time. Block 9
        has four calendars and the top three all hold exactly 20 day rows, so
        ``created_at DESC`` alone handed the block to whichever was ingested last —
        on 2026-08-27 that was a copy uploaded during testing that carries 4,279
        characters of day text, 13 reading citations and 9 project assignments,
        beating out a 6,132-character sibling with 20 and 20. The delivered
        workbook showed exactly that shortfall: Handbook Reference filled on 13 of
        20 days, Projects Today on 9 of 20. Row count says how much of the block a
        calendar covers; text length says how much it actually says about it, and
        only the pair identifies the authoritative one.
        """
        # Matched on the normalized block key, never the raw string: prod stores
        # this block's calendars as 'Block 09' while the course asks for 'Block 9',
        # and an `=` comparison reported "no calendar" for a block that has three.
        cur.execute(
            f"""SELECT c.calendar_id, c.total_days, c.created_at, c.block,
                       count(d.calendar_day_id) AS day_rows,
                       coalesce(sum(length(coalesce(d.source_text, ''))), 0) AS day_chars
                  FROM {schema}.dis_course_calendars c
                  LEFT JOIN {schema}.dis_calendar_days d ON d.calendar_id = c.calendar_id
                 WHERE c.client_id = %s AND {BLOCK_KEY_SQL.format(col="c.block")} = %s
                 GROUP BY c.calendar_id, c.total_days, c.created_at, c.block
                 ORDER BY day_rows DESC, day_chars DESC, c.created_at DESC""",
            (client_id, block_key(block)),
        )
        rows = cur.fetchall()
        if not rows:
            # Names the database, not just the table: the block key is normalized
            # here, so a miss means the rows are not in the store this deployment
            # reads — which on 2026-08-27 was an env override pointing dev at an
            # empty sibling database while the client YAML named the populated one.
            raise LookupError(
                f"No calendar found for client={client_id!r} block={block!r} in "
                f"{schema}.dis_course_calendars of {store_target(cur)}"
            )
        canonical = rows[0]
        dup_ids = [r["calendar_id"] for r in rows[1:]]
        return (canonical["calendar_id"], dup_ids,
                int(canonical.get("total_days") or 0),
                [r.get("block") for r in rows])

    #: Fields merged across a block's duplicate calendars, in the order a merged
    #: day row is assembled. ``day_number`` is the join key and is never merged.
    _MERGED_DAY_FIELDS = ("week_number", "topic", "lesson_title", "source_text",
                          "activities_json", "assignments_json", "assessments_json")

    def _load_days(self, cur, schema: str, calendar_id: str,
                   fallback_calendar_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """The canonical calendar's days, with empty cells filled from its duplicates.

        A block's duplicate calendars are not ranked copies of one another — they
        are different EXTRACTIONS of the same schedule, and on Block 9 they are
        complementary rather than better and worse. The 'Block 09' pair carries a
        reading citation on 20 of 20 days where the 'Block 9' copy has 13, while
        the 'Block 9' copy carries clean assignment cells (``["Project 9-1"]``)
        where the 'Block 09' pair has the entire flattened table row pasted into
        ``assignments_json``. Picking either one alone loses real content, which is
        what ``_pick_calendar``'s tie-break did in both directions before this.

        So the canonical calendar decides which days exist, and every field it
        cannot supply is taken from the first duplicate that can. Merging is
        per-FIELD and per-DAY: no cell is ever combined with another, so a merged
        row is always a set of values each of which some extraction actually
        produced.
        """
        ids = [calendar_id] + [c for c in (fallback_calendar_ids or []) if c != calendar_id]
        cur.execute(
            f"""SELECT calendar_id, calendar_day_id, day_number, week_number, topic,
                       lesson_title, source_text,
                       activities_json::text  AS activities_json,
                       assignments_json::text AS assignments_json,
                       assessments_json::text AS assessments_json
                  FROM {schema}.dis_calendar_days
                 WHERE calendar_id = ANY(%s) AND day_number IS NOT NULL
                 ORDER BY day_number""",
            (ids,),
        )
        rows = cur.fetchall()
        rank = {cid: i for i, cid in enumerate(ids)}
        by_day: Dict[int, List[Dict[str, Any]]] = {}
        for r in rows:
            by_day.setdefault(int(r["day_number"]), []).append(r)

        merged: List[Dict[str, Any]] = []
        for dn in sorted(by_day):
            # `.get`, not `[...]`: a row whose calendar_id is absent or NULL sorts
            # last rather than failing the whole block's enumeration. A day the
            # canonical calendar lacks but a duplicate has is still emitted — the
            # duplicate's row simply becomes the base — because dropping it would
            # silently shorten the block.
            candidates = sorted(by_day[dn], key=lambda r: rank.get(r.get("calendar_id"), 99))
            row = dict(candidates[0])
            for fieldname in self._MERGED_DAY_FIELDS:
                if _usable(row.get(fieldname), fieldname):
                    continue
                replacement = None
                for other in candidates[1:]:
                    if _usable(other.get(fieldname), fieldname):
                        replacement = other[fieldname]
                        break
                # Cleared, not left as-is, when no calendar can supply the field.
                # An unusable cell is a failed extraction, and carrying it forward
                # puts a paragraph of lesson topics in the Projects Today column
                # where an empty cell would correctly read "—". Only the JSON list
                # columns get "[]"; the free-text ones get None, matching what a
                # genuinely absent column yields.
                if replacement is None:
                    replacement = "[]" if fieldname.endswith("_json") else None
                row[fieldname] = replacement
            merged.append(row)
        return merged

    def _load_units(self, cur, schema: str, client_id: str, block: str):
        """The block's units, matched by normalized block key.

        The `=` this replaces was the quieter half of the same bug as
        _pick_calendar's: prod has this block's units split across 'Block 09' (42)
        and 'Block 9' (19), so an exact match returned a THIRD of the block and
        the worksheet built from it looked complete. Returns (units, spellings) so
        the caller can report which tags were merged.
        """
        cur.execute(
            f"""SELECT content_unit_id, unit_type, title, text_content, content_hash,
                       metadata_json
                  FROM {schema}.dis_content_units
                 WHERE client_id = %s
                   AND {BLOCK_KEY_SQL.format(col="metadata_json->>'block'")} = %s""",
            (client_id, block_key(block)),
        )
        rows = cur.fetchall()
        spellings = {(r.get("metadata_json") or {}).get("block") for r in rows}
        return rows, spellings

    #: Document types that are shared reference works rather than one block's
    #: material. Deliberately narrower than
    #: ``attribution.BLOCK_WIDE_REFERENCE_DOC_TYPES``, which also covers syllabi
    #: and calendars: those ARE per-block and are already loaded by block tag, so
    #: including them here would pull every other block's syllabus into this one.
    _REFERENCE_DOC_TYPES = ("ebook_reference",)

    def _load_reference_works(self, cur, schema: str, client_id: str):
        """The client's shared handbooks and textbooks, ignoring the block tag.

        The block tag is exactly what must NOT be applied here. Measured on the
        shared store 2026-08-27: 8083-31B (1,233 units) and OEG-AMT5 (201) carry
        no block tag at all, while AC43.13-2025 (646) and the ASA textbooks are
        tagged with curriculum AREAS ('Airframe', 'Powerplant') that
        ``block_key`` correctly refuses to equate with 'Block 9'. Under the
        block-tag filter every one of them was invisible to every block, so a
        Block 9 build saw 84 units from 9 files — three calendars, a syllabus,
        two exams and two planning documents — and summarised 18 of its 20 days
        from the calendar row alone.

        Reading order is imposed by ``references.index_by_file``, not here: a SQL
        cast on ``chunk_index`` would raise on any row whose value is not a plain
        integer, and a malformed index on one unit must not be able to fail the
        whole block's enumeration.
        """
        cur.execute(
            f"""SELECT content_unit_id, unit_type, title, text_content, content_hash,
                       metadata_json
                  FROM {schema}.dis_content_units
                 WHERE client_id = %s
                   AND coalesce(metadata_json->>'document_type',
                                metadata_json->>'doc_type') = ANY(%s)""",
            (client_id, list(self._REFERENCE_DOC_TYPES)),
        )
        return cur.fetchall()
