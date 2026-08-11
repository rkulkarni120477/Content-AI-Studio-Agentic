"""Alignment of the generated block-wide workbook with the AIM reference sample.

Guards the shape a reviewer actually diffs against
``data/Block_02_Block_Blueprint_AK.xlsx``. Column identity and ORDER both matter —
the reference is pasted into an existing review workflow, so a reordered column is
as disruptive as a missing one.

Deliberate deviations from the reference, asserted here so they stay deliberate:
  * "Notes" is ours (the REDUCE narrative) with no reference counterpart → last.
  * "AIM SME Comments" is reviewer-fill and blank by design (0/20 in the reference).
  * "Targets for Quick Check" / "Summative Exam Item Cluster" are derived in code and
    state the data gap where the reference's AKTR / exam-blueprint sources are not
    ingested — they must never contain invented percentages or item ranges.
"""
from __future__ import annotations

from promptops_app.services.block_wide_generator import BlockWideGenerator
from promptops_app.services.block_wide_service import _DAY_TABLE_HEADER, _day_table_from_rows


# The reference workbook's "4_Day-by-Day Map" header, verbatim, in order.
REFERENCE_COLUMNS = [
    "Day #", "Topic Label (exact from calendar)", "Handbook Reference (calendar, exact)",
    "Handbook Edition as Cited", "ACS Codes Today", "Concept Type",
    "Concept Type Explanation", "Concept Scope", "Learn-While-Doing", "How it is Applied",
    "Hangar Activity", "Projects  Today", "Assessment Today", "Targets for Quick Check",
    "Summative Exam Item Cluster", "Source Files for This Day", "Proposed Learning Objective",
    "Known Misconceptions / Student Difficulties", "Interactive Candidate",
    "Potential Storyline Interactive Type", "Interactive Content",
    "Storyline Source Asset Status", "Interactive Scope", "Interactive Rationale",
    "Job Aid Candidate", "Job Aid Content Type", "Job Aid Content Description",
    "Job Aid Source Reference", "Academian Questions", "AIM SME Comments",
]

#: reference label → our (shorter) label, where they differ by wording only.
_ALIAS = {
    "Day #": "Day",
    "Topic Label (exact from calendar)": "Topic",
    "Handbook Reference (calendar, exact)": "Handbook Reference",
    "Handbook Edition as Cited": "Handbook Edition",
    "ACS Codes Today": "ACS",
    "How it is Applied": "How It Is Applied",
    "Projects  Today": "Projects Today",
    "Source Files for This Day": "Source Files",
    "Proposed Learning Objective": "Learning Objective",
    "Known Misconceptions / Student Difficulties": "Misconceptions",
    "Potential Storyline Interactive Type": "Interactive Type",
    "Job Aid Content Type": "Job Aid Type",
    "Job Aid Content Description": "Job Aid Description",
}


def _expected():
    return [_ALIAS.get(c, c) for c in REFERENCE_COLUMNS]


def test_every_reference_column_is_present_in_reference_order():
    """The whole point: a reviewer diffing our sheet against the reference should
    find the same columns in the same order."""
    expected = _expected()
    assert _DAY_TABLE_HEADER[:len(expected)] == expected, (
        "day-table columns diverged from the AIM reference:\n"
        f"  expected: {expected}\n  actual:   {_DAY_TABLE_HEADER[:len(expected)]}"
    )


def test_only_notes_is_added_beyond_the_reference():
    """Extra columns push everything after them out of alignment, so any addition
    must sit past the reference's last column."""
    assert _DAY_TABLE_HEADER[len(_expected()):] == ["Notes"]


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def _reduce(digests, acs_registry=None):
    enumerate_summary = {
        "total_days": 2, "client_id": "aim", "block": "Block 2",
        "declared_acs": ["AM.I.B.K1", "AM.I.B.K2"],
        "days": [
            {"day_number": 1, "topic": "Aircraft drawings", "acs_codes": ["AM.I.B.K1"],
             "projects_today": [], "assessment_today": [], "handbook_reference": "FAA-H-8083-30B",
             "handbook_edition": "FAA-H-8083-30B", "source_files_today": ["B2D1.pdf"],
             "hangar_activity_today": []},
            {"day_number": 2, "topic": "Methods", "acs_codes": ["AM.I.B.K2"],
             "projects_today": ["Project 2-1"], "assessment_today": ["Quiz 1"],
             "handbook_reference": "", "handbook_edition": "", "source_files_today": [],
             "hangar_activity_today": []},
        ],
    }
    return BlockWideGenerator(llm=lambda m, s, u: "{}").reduce(
        enumerate_summary, digests, deliverable="cdd", tier="draft",
        acs_registry=acs_registry,
    )


def _day_cells(result):
    rows = next(s for s in result.sections if s.get("key") == "day_table")["rows"]
    lines = _day_table_from_rows(rows)
    header = [c.strip() for c in lines[0].strip("|").split("|")]
    out = {}
    for line in lines[2:]:                      # [1] is the |---| separator
        cells = [c.strip() for c in line.strip("|").split("|")]
        assert len(cells) == len(header), "row/header cell count mismatch"
        out[cells[0]] = dict(zip(header, cells))
    return out


_OK_DIGEST = {
    "day_number": 1, "concept_type": "Conceptual",
    "concept_type_explanation": "First-encounter knowledge acquisition.",
    "concept_scope": "Title-block contents, universal numbering system.",
    "derived_objective": "Interpret title blocks.", "misconceptions": ["Views are photos"],
    "digest_status": "ok", "storyline_source_asset_status": "AVAILABLE",
}
_REGISTRY = [
    {"acs_code": "AM.I.B.K1", "high_miss": "79.6% (rank #1)", "priority": "APPLY/ANALYZE"},
    {"acs_code": "AM.I.B.K2", "high_miss": "NO AKTR DATA", "priority": ""},
]


def test_every_row_has_one_cell_per_column():
    """A cell-count drift silently shifts every value one column left or right,
    which reads as plausible data in the wrong field."""
    cells = _day_cells(_reduce([_OK_DIGEST, {"day_number": 2, "digest_status": "failed"}], _REGISTRY))
    assert set(cells) == {"1", "2"}


def test_concept_scope_carries_the_digest_field():
    cells = _day_cells(_reduce([_OK_DIGEST], _REGISTRY))
    assert cells["1"]["Concept Scope"] == "Title-block contents, universal numbering system."


def test_concept_scope_flags_a_failed_day_instead_of_going_blank():
    cells = _day_cells(_reduce([{"day_number": 1, "digest_status": "failed"}], _REGISTRY))
    assert "REVIEW NEEDED" in cells["1"]["Concept Scope"]


def test_quick_check_targets_report_registry_facts_and_never_invent_them():
    cells = _day_cells(_reduce([_OK_DIGEST, {"day_number": 2, "digest_status": "ok"}], _REGISTRY))
    day1 = cells["1"]["Targets for Quick Check"]
    assert "79.6% (rank #1)" in day1 and "APPLY/ANALYZE" in day1
    # Day 2's code has no AKTR figure — must say so, not imply it was assessed low.
    assert "NO AKTR DATA" in cells["2"]["Targets for Quick Check"]


def test_quick_check_targets_handle_a_day_with_no_acs_codes():
    result = _reduce([_OK_DIGEST], _REGISTRY)
    rows = next(s for s in result.sections if s.get("key") == "day_table")["rows"]
    rows[0]["acs_codes"] = []
    BlockWideGenerator._apply_assessment_columns(rows, _REGISTRY)
    assert "No ACS codes" in rows[0]["quick_check_targets"]


def test_quick_check_targets_survive_an_absent_registry():
    """The registry is a separate DIS worksheet; a partial bundle must not crash the
    render or fabricate priorities."""
    cells = _day_cells(_reduce([_OK_DIGEST], acs_registry=None))
    assert "NO AKTR DATA" in cells["1"]["Targets for Quick Check"]


def test_summative_cluster_reports_the_gap_rather_than_estimating():
    """The reference's item ranges come from an exam blueprint that is not ingested;
    inventing "Items ~1-5" would be indistinguishable from a real range."""
    cells = _day_cells(_reduce([_OK_DIGEST], _REGISTRY))
    value = cells["1"]["Summative Exam Item Cluster"]
    assert "REVIEW NEEDED" in value
    assert "~" not in value, "must not fabricate an item range"


def test_aim_sme_comments_is_blank_by_design():
    cells = _day_cells(_reduce([_OK_DIGEST], _REGISTRY))
    assert cells["1"]["AIM SME Comments"] == ""


# --------------------------------------------------------------------------- #
# XLSX export — key/value worksheets
# --------------------------------------------------------------------------- #
def test_kv_bullets_export_as_label_value_cells():
    """Worksheets 1 and 5 are key-value grids in the reference. Written verbatim the
    markdown bullet landed as one string in column A ("- **Block:** Block 2"), which
    is unusable as a spreadsheet."""
    import openpyxl
    from promptops_app.exporters.xlsx_exporter import build_xlsx_worksheets

    md = ("## WORKSHEET 1: BLOCK OVERVIEW\n\n"
          "- **Block:** Block 2\n- **Total Days:** 20\n- **Grading Policy:** \n"
          "- plain bullet with no label\n")
    ws = openpyxl.load_workbook(
        build_xlsx_worksheets("Block 2", [("1 Block Overview", md)]), data_only=True
    )["1 Block Overview"]
    pairs = {r[0]: r[1] for r in ws.iter_rows(min_row=1, max_col=2, values_only=True) if r[0]}

    assert pairs["Block"] == "Block 2"
    assert pairs["Total Days"] == "20"
    assert pairs["Grading Policy"] in (None, "")      # empty value keeps its label row
    # A bullet with no bold label still renders as content, without its "- " marker.
    assert "plain bullet with no label" in pairs
    # And no cell contains raw markdown.
    assert not any("**" in str(k) for k in pairs)


def test_markdown_tables_still_export_as_grids():
    """The day table must keep exporting as real cells — the new bullet branch runs
    only for non-table lines."""
    import openpyxl
    from promptops_app.exporters.xlsx_exporter import build_xlsx_worksheets

    md = "## WORKSHEET 4\n\n| Day | Topic |\n|---|---|\n| 1 | Drawings |\n"
    ws = openpyxl.load_workbook(
        build_xlsx_worksheets("t", [("4 Day-by-day Map", md)]), data_only=True
    )["4 Day-by-day Map"]
    grid = [r for r in ws.iter_rows(min_row=1, max_col=2, values_only=True) if r[0]]
    assert ("Day", "Topic") in grid and ("1", "Drawings") in grid
