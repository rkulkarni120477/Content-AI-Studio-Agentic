"""A calendar that did not parse must never be stored as if it had.

Prod, 2026-08-26. Seven AIM teacher calendars ingested as .xlsx stored 2 usable
days each, for blocks of 20. Nothing failed; the ingest reported success and the
symptom only appeared much later, as a Blueprint covering 2 of 11 days.

Three silent steps in a row:

1. ``_is_header`` matched column titles by exact string, needing 3 of
   {"days", "subject/day", "topics covered", "acs codes"}. The 2026-08 workbooks
   head the same columns "Block Days | Subject Days | Topics Covered | ACS Codes
   for Topics" — one exact hit. So the gate said "not an AIM calendar".
2. The generic row extractor then numbered every row day 1.
3. ``upsert_calendar`` keys day rows by day_number with ON CONFLICT DO UPDATE, so
   twenty days all claiming day 1 overwrote each other down to two rows — and
   returned a count that looked like a successful write.

Each step is defensible alone. Together they turn "this file is in a layout we
don't parse" into "this block has two days", with no error anywhere.
"""
from __future__ import annotations

import pytest

from services.aim_calendar import _is_header
from services.indexing import upsert_calendar

#: The 2026-08 workbooks (Block 9-15 "Calendar Day and Night.xlsx").
NEW_HEADER = ["Block Days", "Subject Days", "Topics Covered", "ACS Codes for Topics",
              "Corresponding Handbook", "Projects", "ACS Codes for Projects", "Quiz"]
#: The original layout the parser was written against.
OLD_HEADER = ["Days", "Subject/Day", "Topics Covered", "ACS Codes", "Handbook"]


@pytest.mark.parametrize("header", [NEW_HEADER, OLD_HEADER])
def test_both_calendar_wordings_are_recognised(header):
    assert _is_header(header)


@pytest.mark.parametrize("row", [
    ["1", "1", "Turbine operating principles", "AM.III.B.K1", "FAA-H-8083-32B"],
    ["", "", ""],
    ["Week", "Module", "Readings", "Assignments"],     # another tenant's sheet
    ["Day", "Notes"],                                  # day alone is not enough
])
def test_non_headers_are_not_mistaken_for_one(row):
    """Still self-gating. The concepts required (a day column, a topic column, and
    an ACS-code or FAA-handbook column) are AIM's, so no other tenant's spreadsheet
    is dragged into this parser — and a data row is not read as a header and
    skipped."""
    assert not _is_header(row)


class _Cur:
    """Records executed statements; stands in for a psycopg cursor."""

    def __init__(self):
        self.rows = []

    def execute(self, sql, params=None):
        if "dis_calendar_days" in sql:
            self.rows.append(params)


def _state(day_numbers):
    return {
        "job_id": "job1", "tenant_id": "aim", "client_id": "aim",
        "calendar_structure": {
            "block": "Block 13", "course_name": "AIM",
            "days": [{"day_number": n, "topic": f"t{i}"} for i, n in enumerate(day_numbers)],
        },
    }


def test_a_calendar_that_collapses_says_so():
    """The exact prod shape: every parsed day numbered 1."""
    state = _state([1] * 11)
    stored = upsert_calendar(_Cur(), "dis", "doc1", state)

    assert stored == 1, "eleven days sharing a number are one stored day, not eleven"
    assert state["errors"], "a collapse must be reported, not returned as a count"
    assert "day_number" in state["errors"][0] and "overwritten" in state["errors"][0]


def test_the_returned_count_is_days_actually_stored():
    """It used to return the number of INSERTs issued, which counted every
    overwrite — so 11 rows written onto 2 keys reported 11 days stored."""
    state = _state([1, 1, 2, 2, 3])
    assert upsert_calendar(_Cur(), "dis", "doc1", state) == 3


def test_a_clean_calendar_reports_nothing():
    state = _state(list(range(1, 21)))
    assert upsert_calendar(_Cur(), "dis", "doc1", state) == 20
    assert not state.get("errors")


def test_day_rows_carry_the_canonical_block_label():
    """Written through block_label so a calendar ingested as 'Block 09' is stored
    as 'Block 9' — the tag split that hid these blocks in the first place."""
    state = _state([1, 2])
    state["calendar_structure"]["block"] = "Block 09"
    cur = _Cur()
    upsert_calendar(cur, "dis", "doc1", state)
    params = [p for p in cur.rows[0] if isinstance(p, str)]
    assert "Block 9" in params and "Block 09" not in params
