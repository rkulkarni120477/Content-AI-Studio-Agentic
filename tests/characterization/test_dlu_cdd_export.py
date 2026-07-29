"""DLU (worksheet-based) CDD detection, splitting, and multi-sheet XLSX export.

Covers:
- is_dlu_cdd() / split_cdd_worksheets() in promptops_app/parsers/cdd_parser.py
- build_xlsx_worksheets() + _safe_sheet_name() in promptops_app/exporters/xlsx_exporter.py

The DLU XLSX path emits one sheet per worksheet (Overview + Worksheet 1..N),
with markdown tables rendered as real cells. Standard CDDs are not DLU and use
the existing single-sheet export unchanged.
"""

from __future__ import annotations

from openpyxl import load_workbook

from promptops_app.parsers.cdd_parser import is_dlu_cdd, split_cdd_worksheets
from promptops_app.exporters.xlsx_exporter import build_xlsx_worksheets, _safe_sheet_name

DLU_CS = """# BLOCK 2 BLUEPRINT
## Sample Block

**Project:** Test Project

## WORKSHEET 1: BLOCK OVERVIEW
Overview body line one.

## WORKSHEET 2: INSTRUCTIONAL SEQUENCE MAP
| Day | Day Title | ACS Code |
|-----|-----------|----------|
| 1 | Intro | AM.I.E.K1 |
| 2 | Next | AM.I.E.K2 |

## WORKSHEET 3: ACS CODE REGISTRY
Some prose describing coverage."""

STANDARD_CS = """## Course Details
A standard CDD.

## Course Structure
Module 1: Intro
Module 2: Advanced"""


def test_is_dlu_cdd_detection():
    assert is_dlu_cdd(DLU_CS) is True
    assert is_dlu_cdd(STANDARD_CS) is False
    assert is_dlu_cdd("") is False


def test_split_cdd_worksheets_overview_first_then_worksheets():
    sections = split_cdd_worksheets(DLU_CS)
    labels = [lbl for lbl, _ in sections]
    assert labels[0] == "Overview"
    assert len(sections) == 4  # Overview + 3 worksheets
    # Short, acronym-preserving labels.
    assert labels[1].startswith("1 ")
    assert any("ACS" in lbl for lbl in labels)  # not "Acs"
    # Each worksheet section keeps its own heading (boundary marker).
    for lbl, content in sections[1:]:
        assert content.lstrip().upper().startswith("## WORKSHEET") or "WORKSHEET" in content.upper()


def test_split_cdd_worksheets_empty_for_standard():
    assert split_cdd_worksheets(STANDARD_CS) == []


def test_build_xlsx_worksheets_one_sheet_per_section_with_table_cells():
    sections = split_cdd_worksheets(DLU_CS)
    buf = build_xlsx_worksheets("Sample Block", sections)
    wb = load_workbook(buf)

    # One sheet per section, Overview first.
    assert len(wb.sheetnames) == 4
    assert wb.sheetnames[0] == "Overview"

    # Sheet names are Excel-safe.
    for name in wb.sheetnames:
        assert len(name) <= 31
        assert not any(ch in name for ch in '\\/?*[]:')
        assert not (name.startswith("'") or name.endswith("'"))

    # Worksheet 2's markdown table became real cells (header row + 2 day rows).
    seq_sheet = next(wb[n] for n in wb.sheetnames if n.startswith("2 "))
    rows = [
        [c for c in row if c not in (None, "")]
        for row in seq_sheet.iter_rows(values_only=True)
    ]
    header = next(r for r in rows if r and r[0] == "Day")
    assert "Day Title" in header and "ACS Code" in header
    day_values = {r[0] for r in rows if len(r) >= 3 and str(r[0]) in ("1", "2")}
    assert day_values == {"1", "2"}


def test_build_xlsx_worksheets_empty_falls_back():
    # No sections -> single-sheet fallback, still a valid workbook.
    buf = build_xlsx_worksheets("Empty", [])
    wb = load_workbook(buf)
    assert len(wb.sheetnames) >= 1


def test_safe_sheet_name_rules():
    used = set()
    # Illegal chars stripped, length capped, word-boundary truncation.
    long_name = _safe_sheet_name("3 ACS Code Registry With Orphan Check", used)
    used.add(long_name)
    assert len(long_name) <= 31
    assert "ACS" in long_name

    # Leading/trailing apostrophes removed (Excel rejects them).
    apos = _safe_sheet_name("'Weird' Name'", used)
    assert not apos.startswith("'")
    assert not apos.endswith("'")

    # Illegal characters replaced.
    illegal = _safe_sheet_name("A/B:C*D", set())
    assert not any(ch in illegal for ch in '\\/?*[]:')

    # Duplicate names are disambiguated.
    u = set()
    a = _safe_sheet_name("Overview", u); u.add(a)
    b = _safe_sheet_name("Overview", u)
    assert a != b
