"""Offline tests for the plumbing the assigned-reading feature touches outside
services.digests.references itself:

  * attribution._unescaped / _FINAL_EXAM — the calendar cell that carries the
    reading citation also carries the final-exam name, and both arrive with
    ``::text`` escape sequences glued to the words;
  * AIMCurriculumProfile._pick_calendar — which of a block's several calendars is
    authoritative, now that "most day TEXT" breaks a tie on row count;
  * mapper.cache_key — reference units are part of what a digest was built from,
    but an EMPTY reference list must key identically to no argument at all;
  * enumerate._resolve_references — every citation that missed reaches a flag.

No DB and no LLM: the profile is driven through a fake cursor that applies the
SQL's own ORDER BY to hand-built rows.
"""
import re
import types

import pytest

from services.digests import attribution, enumerate as enumerate_mod, mapper
from services.digests.profiles.aim import AIMCurriculumProfile


# --------------------------------------------------------------------------- #
# 5. attribution._unescaped / _FINAL_EXAM
# --------------------------------------------------------------------------- #
def test_unescaped_turns_text_cast_escape_sequences_into_spaces():
    """``_load_days`` reads assessments_json with ``::text``, so a line break in a
    calendar cell arrives as the two characters ``\\`` and ``n`` — glued to the
    words either side. Replaced with a space, not removed, so the words stay
    separate tokens."""
    assert attribution._unescaped(r"Reading: None\n\n\n\nCumulative Exam") == \
        "Reading: None    Cumulative Exam"
    assert attribution._unescaped(r"Project\n9-1") == "Project 9-1"
    assert attribution._unescaped(None) == ""
    assert attribution._unescaped("no escapes here") == "no escapes here"


def test_final_exam_day_resolves_through_a_literal_backslash_n():
    """REGRESSION — Block 9's day-20 cell reads
    ``"... | Reading: None\\n\\n\\n\\nCumulative Exam"``. The character immediately
    before "Cumulative" is the letter ``n``, so ``\\b`` cannot match, and the
    block's 18 final-exam items were left unattributed on a block whose calendar
    schedules the exam plainly. The Summative Exam Item Cluster column then read
    "REVIEW NEEDED — no summative exam blueprint in source" on all 20 days."""
    days = [
        {"day_number": 19, "assessments_json": r'["Quiz 10"]', "assignments_json": ""},
        {"day_number": 20,
         "assessments_json": r'["Block 9 Review | Reading: None\n\n\n\nCumulative Exam"]',
         "assignments_json": ""},
    ]
    assert attribution.final_exam_day(days) == 20, (
        "the final-exam day was lost to a literal backslash-n immediately before "
        "'Cumulative Exam'")


@pytest.mark.parametrize("cell", [
    "Block 9: Final Exam",          # Block 9's older calendar
    "Cumulative Exam",              # Block 9's richer calendar
    "Block 9 Final Cumulative Exam",  # how the exam documents are filed
    "FINAL EXAM (100 questions)",
])
def test_final_exam_matches_every_spelling_the_calendars_use(cell):
    """AIM names the block's culminating exam three ways, inconsistently even
    between two calendars for the SAME block."""
    assert attribution._FINAL_EXAM.search(cell), f"{cell!r} was not recognised as the final exam"


@pytest.mark.parametrize("cell", [
    "Block 2 Review Quiz",
    "Quiz 10 from Cleaning and Corrosion Control - Days 5 & 6",
    "Final project presentation",
    "Exam review session",
])
def test_per_day_assessments_are_not_mistaken_for_the_final_exam(cell):
    """Review quizzes are per-day and are placed by the numbered-quiz rule. If
    ``_FINAL_EXAM`` claimed them, every final-exam unit in the block would be
    attributed to whichever day held the first review quiz."""
    assert not attribution._FINAL_EXAM.search(cell), (
        f"{cell!r} was mistaken for the block's culminating exam")


def test_final_exam_day_is_the_first_day_that_schedules_it():
    days = [{"day_number": n, "assessments_json": "", "assignments_json": ""} for n in (1, 2, 3)]
    assert attribution.final_exam_day(days) is None
    days[2]["assessments_json"] = '["Block 9: Final Exam"]'
    assert attribution.final_exam_day(days) == 3


# --------------------------------------------------------------------------- #
# 6. _pick_calendar ordering
# --------------------------------------------------------------------------- #
class _FakeCursor:
    """A cursor that applies the query's OWN ``ORDER BY`` to the rows it holds.

    There is no live DB here, so the ordering under test is the SQL text itself.
    Rather than assert on that text alone, this parses the ORDER BY clause out of
    the executed statement and sorts the fixture rows by it — so the test fails if
    the clause changes, in the same way the database's answer would change.
    """

    def __init__(self, rows):
        self._rows = rows
        self.sql = ""
        self.params = None
        self._result = []

    def execute(self, sql, params=None):
        self.sql = sql
        self.params = params
        m = re.search(r"ORDER BY\s+(.+?)(?:\n|$)", sql)
        if not m:
            self._result = list(self._rows)
            return
        terms = []
        for term in m.group(1).split(","):
            parts = term.strip().split()
            col = parts[0].split(".")[-1]
            terms.append((col, len(parts) > 1 and parts[1].upper() == "DESC"))
        rows = list(self._rows)
        for col, desc in reversed(terms):        # stable sort, least significant first
            rows.sort(key=lambda r: r[col], reverse=desc)
        self._result = rows

    def fetchall(self):
        return self._result


def _cal(cid, day_rows, day_chars, created_at, block="Block 09", total_days=20):
    return {"calendar_id": cid, "total_days": total_days, "created_at": created_at,
            "block": block, "day_rows": day_rows, "day_chars": day_chars}


def test_pick_calendar_sql_ranks_day_text_above_upload_time():
    """The ORDER BY contract, asserted on the SQL string because there is no DB in
    this suite: ``day_chars DESC`` must come BEFORE ``created_at DESC``, or a tie
    on row count is decided by upload time again."""
    cur = _FakeCursor([_cal("a", 20, 100, 1)])
    AIMCurriculumProfile({})._pick_calendar(cur, "dis", "aim", "Block 9")
    order_by = re.search(r"ORDER BY\s+(.+)", cur.sql).group(1)
    assert "day_rows DESC" in order_by
    assert order_by.index("day_chars DESC") < order_by.index("c.created_at DESC"), (
        f"day text no longer outranks upload time: ORDER BY {order_by}")
    assert order_by.index("day_rows DESC") < order_by.index("day_chars DESC")


def test_pick_calendar_prefers_more_day_text_over_a_newer_upload():
    """REGRESSION — Block 9 has four calendars and the top three all hold exactly
    20 day rows. Under ``created_at DESC`` alone the block went to whichever was
    ingested last: on 2026-08-27 a 4,279-character copy uploaded during testing,
    beating a 6,132-character sibling. The delivered workbook showed exactly that
    shortfall — Handbook Reference filled on 13 of 20 days, Projects Today on 9.
    """
    rows = [
        _cal("newer-but-thinner", 20, 4_279, created_at=99),
        _cal("older-and-richer", 20, 6_132, created_at=1),
        _cal("short", 12, 9_999, created_at=100),
    ]
    cur = _FakeCursor(rows)
    calendar_id, dup_ids, total_days, spellings = \
        AIMCurriculumProfile({})._pick_calendar(cur, "dis", "aim", "Block 9")
    assert calendar_id == "older-and-richer", (
        "a tie on day rows was decided by upload time instead of day text")
    assert dup_ids == ["newer-but-thinner", "short"], (
        f"the losing calendars must be reported as duplicates, not dropped: {dup_ids}")
    assert total_days == 20
    assert spellings == ["Block 09"] * 3


def test_pick_calendar_row_count_still_dominates_day_text():
    """Row count says how much of the block a calendar covers; text length only
    breaks ties within that."""
    cur = _FakeCursor([_cal("full", 20, 100, 1), _cal("partial", 12, 500_000, 99)])
    calendar_id, dup_ids, _total, _sp = \
        AIMCurriculumProfile({})._pick_calendar(cur, "dis", "aim", "Block 9")
    assert calendar_id == "full" and dup_ids == ["partial"]


def test_pick_calendar_matches_on_the_normalized_block_key():
    """prod stores this block's calendars as 'Block 09' while the course asks for
    'Block 9'; an ``=`` comparison reported "no calendar" for a block with three."""
    cur = _FakeCursor([_cal("a", 20, 100, 1)])
    AIMCurriculumProfile({})._pick_calendar(cur, "dis", "aim", "Block 9")
    assert cur.params == ("aim", "block 9"), cur.params
    assert "c.block = %s" not in cur.sql, "the raw block string is being compared again"


def test_pick_calendar_raises_when_the_block_has_no_calendar():
    with pytest.raises(LookupError) as excinfo:
        AIMCurriculumProfile({})._pick_calendar(_FakeCursor([]), "dis", "aim", "Block 9")
    assert "Block 9" in str(excinfo.value) and "aim" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# 7. cache_key and reference units
# --------------------------------------------------------------------------- #
def _unit(uid, content_hash):
    return {"content_unit_id": uid, "text_content": f"body of {uid}",
            "content_hash": content_hash, "metadata_json": {}}


_KEY_ARGS = dict(model="m", schema_version="s1", prompt_version="p1",
                 day_meta="Landing gear|Intro", map_guidance="")


def test_cache_key_changes_when_reference_units_are_added():
    """The assigned reading is part of what the day's digest was built from, so a
    digest built without it must not be served for a day that now has it."""
    units = [_unit("u1", "h1")]
    without = mapper.cache_key(3, units, **_KEY_ARGS)
    with_refs = mapper.cache_key(3, units, reference_units=[_unit("r1", "rh1")], **_KEY_ARGS)
    assert without != with_refs, (
        "attaching a handbook chapter did not invalidate the cached digest")


def test_cache_key_changes_when_the_reference_content_changes():
    units = [_unit("u1", "h1")]
    a = mapper.cache_key(3, units, reference_units=[_unit("r1", "rh1")], **_KEY_ARGS)
    b = mapper.cache_key(3, units, reference_units=[_unit("r1", "rh2")], **_KEY_ARGS)
    c = mapper.cache_key(3, units, reference_units=[_unit("r1", "rh1"), _unit("r2", "rh2")],
                         **_KEY_ARGS)
    assert len({a, b, c}) == 3, "a changed or extended reading did not change the key"


def test_cache_key_ignores_reference_ordering():
    """Reference units arrive in reading order, but a re-ingest can renumber them;
    only their content should decide the key."""
    units = [_unit("u1", "h1")]
    refs = [_unit("r1", "rh1"), _unit("r2", "rh2")]
    assert mapper.cache_key(3, units, reference_units=refs, **_KEY_ARGS) == \
        mapper.cache_key(3, units, reference_units=list(reversed(refs)), **_KEY_ARGS)


def test_empty_reference_list_keys_identically_to_no_argument():
    """THE deploy-safety property: most days resolve no reading, and a digest
    stored before assigned reading existed must still be a cache HIT for them.
    If an empty list keyed differently, every cached digest in production would
    rebuild — a full re-MAP of every day of every block, at LLM cost — the moment
    this shipped."""
    units = [_unit("u1", "h1"), _unit("u2", "h2")]
    baseline = mapper.cache_key(3, units, **_KEY_ARGS)
    assert mapper.cache_key(3, units, reference_units=[], **_KEY_ARGS) == baseline
    assert mapper.cache_key(3, units, reference_units=None, **_KEY_ARGS) == baseline


# --------------------------------------------------------------------------- #
# enumerate._resolve_references — every miss reaches a flag
# --------------------------------------------------------------------------- #
def _ref_chunk(index, text, filename="8083-31B.pdf"):
    return {"content_unit_id": f"{filename}#{index}", "unit_type": "chunk",
            "text_content": text,
            "metadata_json": {"chunk_index": index, "source_file_name": filename,
                              "document_type": "ebook_reference"}}


def _handbook(chapter=13, n=8):
    return [_ref_chunk(i, f"gear [Figure {chapter}-{i + 1}] strut [Figure {chapter}-{i + 2}]")
            for i in range(n)]


def _scope(reference_units):
    return types.SimpleNamespace(reference_units=reference_units, notes=[])


def test_resolve_references_places_a_cited_chapter_on_its_day():
    days = [{"day_number": 1, "topic": "Landing gear",
             "source_text": "Reading: FAA-H-8083-31B\nCh. 13 pgs. 13-1 to 13-8"},
            {"day_number": 2, "topic": "Brakes", "source_text": "Reading: None"}]
    by_day, flags = enumerate_mod._resolve_references(days, _scope(_handbook()))
    assert set(by_day) == {1}, f"reading was placed on the wrong day(s): {sorted(by_day)}"
    assert len(by_day[1].units) == 8
    assert flags == [], f"an exact resolution raised flags: {flags}"


def test_uningested_handbook_reaches_a_reading_not_ingested_flag():
    """48 AIM day rows cite 8083-32B, which is not ingested at all. Invisible in
    the delivered document — such a day simply reads a little thinner than its
    neighbours — so it has to be a flag."""
    days = [{"day_number": n, "topic": "t",
             "source_text": "Reading: FAA-H-8083-32B\nCh. 2 pgs. 2-1 to 2-9"}
            for n in (3, 4)]
    _by_day, flags = enumerate_mod._resolve_references(days, _scope(_handbook()))
    assert len(flags) == 1, flags
    assert flags[0].startswith("READING_NOT_INGESTED"), flags[0]
    assert "8083-32B" in flags[0] and "3, 4" in flags[0], flags[0]


def test_imprecise_attachment_reaches_a_reading_approximate_flag():
    """A whole-file or topical attachment is not the cited pages and must never
    look like them."""
    days = [{"day_number": 5, "topic": "Landing gear",
             "source_text": "Reading: FAA-H-8083-31B\nCh. 99 pgs. 99-1 to 99-4"}]
    by_day, flags = enumerate_mod._resolve_references(days, _scope(_handbook()))
    assert by_day[5].units, "the day lost its assigned reading entirely"
    assert len(flags) == 1 and flags[0].startswith("READING_APPROXIMATE"), flags
    assert "chapter 99 could not be located" in flags[0], flags[0]


def test_resolve_references_is_a_no_op_without_reference_works():
    by_day, flags = enumerate_mod._resolve_references(
        [{"day_number": 1, "source_text": "Reading: FAA-H-8083-31B\nCh. 13"}], _scope([]))
    assert (by_day, flags) == ({}, [])


def test_reference_resolution_failure_never_sinks_enumeration():
    """The block's own units and calendar are unaffected by a reference-indexing
    failure, so it must degrade to a flag rather than an exception."""
    # A malformed unit row: index_by_file calls .get on it and raises. The
    # failure has to happen INSIDE the resolution, which is what the try/except
    # guards — an unreadable scope attribute is a different, earlier problem.
    scope = types.SimpleNamespace(reference_units=["not a unit row"], notes=[])
    by_day, flags = enumerate_mod._resolve_references([{"day_number": 1}], scope)
    assert by_day == {}
    assert len(flags) == 1 and flags[0].startswith("ASSIGNED_READING_FAILED"), flags
    assert "AttributeError" in flags[0], flags[0]
