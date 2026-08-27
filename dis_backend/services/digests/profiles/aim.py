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
#: ``Days?`` — the plural is what these sheets actually use ("Days |
#: Subject/Day | Topics Covered | ACS"), and requiring the singular let the
#: header row through as a value on four of Block 2's twenty days.
_HEADER_ROW_RE = re.compile(r"^\s*Days?\s*\|", re.IGNORECASE)


#: The assignment/assessment items a pasted-in row can still be mined for. AIM
#: writes projects as "Project P30" / "Project 9-1" / "Project 14-2", quizzes as
#: "Quiz 3" or "Quiz #3", and the block exam by one of the three names
#: attribution._FINAL_EXAM already reconciles.
#: A project label runs "Project 9-1", "Project P30", "Project A27" or the paired
#: form "Project 4 A52", where the trailing code is the task the project covers.
#: Capturing only "Project 4" there dropped the half that identifies WHICH project
#: — and "Project 4" alone collides across days, so a delivered Block 9 workbook
#: showed "Project 2" on three separate days that cite three different tasks.
_PROJECT_RE = re.compile(
    r"\bProject\s+[A-Z]?\d+(?:-\d+)?(?:\s+[A-Z]\d+)?\b", re.IGNORECASE)
_QUIZ_RES = (
    re.compile(r"\bQuiz\s*#?\s*\d+\b", re.IGNORECASE),
    re.compile(r"\b(?:Final|Cumulative)(?:\s+Cumulative)?\s+Exam\b", re.IGNORECASE),
    re.compile(r"\bReview\s+Quiz\b", re.IGNORECASE),
)
#: Which patterns a field may be mined for. Mining every pattern regardless of the
#: column being filled is what put "Quiz #2" in Day 4's Projects Today and "Quiz
#: #8" in Days 16 and 17's — the pasted row names both kinds of item, and an
#: assignments cell filled from it took the assessments too.
_ITEM_RES_BY_FIELD = {
    "assignments_json": (_PROJECT_RE,),
    "assessments_json": _QUIZ_RES,
}


def _mine_row_items(text: str, fieldname: str = "") -> List[str]:
    """The assignment/assessment items named inside a pasted-in table row.

    Blanking a row-restatement cell outright was wrong, and only a whole-corpus
    comparison showed it: Blocks 14 and 15 store every day's assignments in that
    pasted form on BOTH of their calendars, so there was no clean sibling to
    borrow from and the Projects Today column went from 20 days populated to 0.
    The rows are not empty of meaning — "Day 2 | INDUCTION and EXHAUST SYSTEMS
    ... | Reading: FAA-H-8083-32B Pg. 3-4 to 3-9 Project P30" names a real
    project. Mining recovers the item and leaves the lesson prose behind, which
    is what the cell was supposed to hold in the first place.

    De-duplicated, first-seen order, whitespace-collapsed. Returns [] when the
    row names nothing, and the caller then treats the cell as absent.
    """
    body = _JSON_ESCAPE_IN_CELL.sub(" ", str(text or ""))
    out: List[str] = []
    patterns = _ITEM_RES_BY_FIELD.get(fieldname)
    if patterns is None:
        patterns = (_PROJECT_RE,) + _QUIZ_RES
    for pattern in patterns:
        for m in pattern.findall(body):
            item = " ".join(str(m).split())
            if item and item.lower() not in {o.lower() for o in out}:
                out.append(item)
    return out


#: ``::text`` renders a JSONB newline as the two characters ``\`` and ``n``; the
#: item patterns above must not be blocked by one sitting against a word.
_JSON_ESCAPE_IN_CELL = re.compile(r"\\[nrt]|[\r\n\t]")


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


#: A day-topic word, for judging whether two calendars describe the SAME day.
#: Four letters or more, so "the"/"and"/"of" cannot manufacture agreement.
_TOPIC_WORD_RE = re.compile(r"[a-z]{4,}")

#: Jaccard overlap at which two calendars' descriptions of one day are taken to
#: be the same lesson. Real matches on this corpus score near 1.0 (the Instructor
#: and Student copies of a calendar are the same text); real mismatches score
#: below 0.2 ("Aircraft tires and tubes" against "Aircraft Brakes" shares only
#: "aircraft"). Nothing lands near the middle, so the threshold is not delicate.
_DAY_TOPIC_AGREEMENT = 0.5

#: Fraction of judgeable shared days that must agree before two calendars are
#: treated as extractions of one schedule.
_SCHEDULE_AGREEMENT = 0.7

#: Below this many judgeable shared days there is not enough evidence either way,
#: and the answer is no — see _same_schedule.
_MIN_SCHEDULE_OVERLAP = 3


def _topic_words(row: Dict[str, Any]) -> set:
    """The content words describing one calendar day."""
    text = " ".join(str(row.get(k) or "") for k in ("lesson_title", "topic"))
    return set(_TOPIC_WORD_RE.findall(text.lower()))


def _same_schedule(canonical: Dict[int, Dict[str, Any]],
                   other: Dict[int, Dict[str, Any]]) -> bool:
    """Whether *other* is an extraction of the same schedule as *canonical*.

    Field-level merging across a block's calendars is only sound when they are
    different EXTRACTIONS of one schedule — then a cell one of them missed can
    honestly be taken from another. Block 9 has five calendars and they are two
    different schedules: three come from ``Block 09-Instructor/Student Copy- ACS
    Course Calendar.docx``, two from ``Block 9 Calendar Day and Night.xlsx``, and
    the documents do not agree about what is taught when — day 7 is "Aircraft
    tires and tubes" in one and "Aircraft Brakes" in the other, day 9 is
    "Projects / AC 43.13-1B" against "Aircraft Tires & Tubes", day 16 is "Ice
    control systems" against "Review & Final Exam".

    Merging across that produced rows no source ever asserted: the delivered
    workbook's Day 9 carried the .docx's lesson and reading beside the .xlsx's
    "Quiz 8", while the .docx's own row for that day says Quiz #6. A whole column
    of the Day-by-Day Map was two incompatible numbering systems interleaved.

    Judged on the days both carry, by topic-word overlap. Absent evidence the
    answer is NO: refusing to merge costs at most a cell that stays empty, and
    an empty cell is recoverable by a reviewer in a way an invented one is not.
    """
    shared = sorted(set(canonical) & set(other))
    judged = agreed = 0
    for dn in shared:
        a, b = _topic_words(canonical[dn]), _topic_words(other[dn])
        if not a or not b:
            continue  # one side says nothing about this day — no evidence, not agreement
        judged += 1
        if len(a & b) / len(a | b) >= _DAY_TOPIC_AGREEMENT:
            agreed += 1
    if judged < _MIN_SCHEDULE_OVERLAP:
        return False
    return agreed / judged >= _SCHEDULE_AGREEMENT


def _reconcile_total_days(declared: int, days: List[Dict[str, Any]]) -> tuple[int, str]:
    """How many days the block has, and a note when the stored count disagrees.

    ``dis_course_calendars.total_days`` is NOT a figure any document declares —
    no extractor reads a "total days" statement. It is ``len(days)`` as that
    extractor's own parse counted them, so a parse that picked up one extra row
    stores a length the calendar does not have. The generic ``aviation_academic``
    extractor does exactly this on every 20-day AIM calendar it handled: blocks
    5, 7, 8, 9, 10, 14 and 15 all store ``total_days=21`` beside 20 day rows
    numbered 1..20, and Block 9's delivered workbook reported "Total Days 21",
    "Days represented 20/21" and a BLOCK_INCOMPLETE flag sending a reviewer to
    look for a 21st day that has never existed.

    The day rows are the evidence. The block runs to the HIGHEST day number they
    carry — not to how many rows there are — so a calendar holding days 1-9 and
    20 is 20 days long with a genuine gap, which is the case BLOCK_INCOMPLETE
    exists to report and the one this must not paper over. The stored count is
    kept only as something to report when it disagrees.
    """
    numbers = sorted({int(d["day_number"]) for d in days
                      if d.get("day_number") is not None})
    observed = numbers[-1] if numbers else 0
    if not observed:
        return int(declared or 0), ""
    if declared and declared != observed:
        return observed, (
            f"CALENDAR_TOTAL_DAYS_CORRECTED — the calendar record stores "
            f"total_days={declared}, but its day rows are numbered "
            f"{numbers[0]}-{observed}; the rows are authoritative and the block "
            f"is treated as {observed} days long")
    return observed, ""


class AIMCurriculumProfile(CurriculumProfile):
    coverage_label = "ACS"

    def load_scope(self, cur, schema: str, client_id: str, block: str) -> ScopeData:
        self._extra_duplicate_days: List[int] = []
        self._foreign_schedule_ids: List[str] = []
        calendar_id, dup_ids, declared_days, cal_spellings = self._pick_calendar(
            cur, schema, client_id, block)
        days = self._load_days(cur, schema, calendar_id, dup_ids)
        total_days, total_days_note = _reconcile_total_days(declared_days, days)
        units, unit_spellings = self._load_units(cur, schema, client_id, block)
        # Surfaced, not just survived: matching through the key is what makes the
        # lookup work at all on data written by two disagreeing writers, but the
        # data IS inconsistent and the worksheet's reader is the person who can
        # get it fixed. See services/blocks.spelling_note.
        note = spelling_note(block, list(cal_spellings) + list(unit_spellings))
        notes = [note] if note else []
        if total_days_note:
            notes.append(total_days_note)
        if getattr(self, "_foreign_schedule_ids", None):
            notes.append(
                f"CALENDAR_SCHEDULE_MISMATCH — {len(self._foreign_schedule_ids)} other "
                f"calendar(s) for this block describe a DIFFERENT schedule (their day "
                f"topics do not match the canonical calendar's): "
                f"{self._foreign_schedule_ids}. They are reported, not merged — a cell "
                f"borrowed across two different schedules would state something no "
                f"source says. Confirm which calendar this block is actually taught to")
        if getattr(self, "_extra_duplicate_days", None):
            notes.append(
                f"DUPLICATE_CALENDAR_EXTRA_DAYS — a lower-ranked calendar for this "
                f"block carries day(s) {self._extra_duplicate_days} that the "
                f"canonical one does not; they are NOT enumerated, because the "
                f"canonical calendar defines the block's length")
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

        # Only calendars describing the SAME schedule may fill each other's gaps.
        # See _same_schedule: this block's five calendars are two different
        # documents with different lesson orders, and borrowing between them
        # assembled rows that neither source asserts.
        rows_by_cal: Dict[str, Dict[int, Dict[str, Any]]] = {}
        for r in rows:
            # A row carrying no calendar_id belongs to the canonical calendar:
            # it came back from a query filtered to this block's calendars, and
            # attributing it anywhere else would drop it. (The column is
            # nullable and some older rows have it unset.)
            rows_by_cal.setdefault(r.get("calendar_id") or calendar_id,
                                   {})[int(r["day_number"])] = r
        canonical_rows = rows_by_cal.get(calendar_id, {})
        mergeable = [calendar_id]
        self._foreign_schedule_ids = []
        for cid in ids[1:]:
            if cid not in rows_by_cal:
                continue
            # With no canonical rows to compare against there is no evidence
            # either way, and the pre-existing merge behaviour stands rather
            # than a filter that would empty the block.
            if not canonical_rows or _same_schedule(canonical_rows, rows_by_cal[cid]):
                mergeable.append(cid)
            else:
                self._foreign_schedule_ids.append(cid)
        allowed = set(mergeable)
        rows = [r for r in rows if (r.get("calendar_id") or calendar_id) in allowed]

        ids = mergeable
        rank = {cid: i for i, cid in enumerate(ids)}
        by_day: Dict[int, List[Dict[str, Any]]] = {}
        for r in rows:
            by_day.setdefault(int(r["day_number"]), []).append(r)

        # The CANONICAL calendar decides which days exist; duplicates only fill in
        # fields. Taking the union instead let a day present in a lower-ranked
        # duplicate widen the block — Block 4 went from 16 days to 17, and since
        # total_days still comes from the canonical calendar's own column that also
        # manufactured a spurious day-count disagreement. Extra days in a duplicate
        # are reported rather than absorbed, because "the calendars disagree about
        # how long this block is" is a fact for a human to resolve, not one for this
        # function to decide by silently picking the larger answer.
        canonical_days = {int(r["day_number"]) for r in rows
                          if r.get("calendar_id") == calendar_id}
        if canonical_days:
            extra = sorted(set(by_day) - canonical_days)
            if extra:
                self._extra_duplicate_days = extra
            by_day = {dn: v for dn, v in by_day.items() if dn in canonical_days}

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
                if replacement is None and fieldname in ("assignments_json",
                                                         "assessments_json"):
                    # No calendar has a clean cell for this field. Before giving
                    # up, mine the row itself — it names the project or quiz even
                    # when the extractor pasted the whole line around it.
                    mined = _mine_row_items(row.get(fieldname) or "", fieldname)
                    if mined:
                        replacement = json.dumps(mined)
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
