"""AKTR knowledge-test report ingestion — parser, spreadsheet extraction, and the
per-block attribution that makes the data reachable for a block other than the first.

The live failure these cover: ALLCAMPUS_AKTR_MissedCodes_Q12026.xlsx (16 sheets, one
per block, columns Rank | % Missed | ACS Code | ACS Code Description under a merged
title row) was ingested with its ACS codes and its miss rates absent from the text,
typed as an answer key, and stamped block="Block 1" — so a Block 6 Blueprint could
not see any of it.
"""
from __future__ import annotations

import io

import pytest

from services.knowledge_test_report import (
    build_knowledge_test_structure,
    looks_like_aktr_report,
    parse_missed_codes,
    _compose_text,
)
from services.pipeline.extractors import extract_xlsx, _unique_headers, _xlsx_header_row

openpyxl = pytest.importorskip("openpyxl")


def _workbook(sheets) -> bytes:
    """``sheets`` is [(name, [rows])]; rows are written verbatim, banner rows included."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets:
        ws = wb.create_sheet(title=name)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _aktr_sheet(block: int, rows):
    return (f"Block {block}", [
        (f"ALL CAMPUS — Block {block} | Top 10 Most Missed ACS Codes", None, None, None),
        ("Rank", "% Missed", "ACS Code", "ACS Code Description"),
        *rows,
    ])


_BLOCK_6 = _aktr_sheet(6, [
    (1, 0.4816753926701571, "AM.II.K.K1", "Generators, DC generation systems."),
    (2, 0.3821989528795812, "AM.II.K.K5", "Voltage regulators and over-volt protection."),
])
_BLOCK_1 = _aktr_sheet(1, [
    (1, 0.6562277580071174, "AM.I.K.K3", "Nondestructive Testing (NDT) procedures."),
])


# ── extract_xlsx: the merged title row must not become the header ─────────────

def test_extract_xlsx_keeps_every_column_under_a_merged_title_row():
    """Regression, measured on the real workbook: row 1 is a one-cell banner, so
    taking it as the header named columns 2-4 all "" and dict(zip(...)) collapsed
    them onto one key — the ACS code and the miss rate, the only reason to ingest
    the file, were silently dropped from the extracted text."""
    text = extract_xlsx(_workbook([_BLOCK_6])).text
    assert "Columns: Rank, % Missed, ACS Code, ACS Code Description" in text
    assert '"ACS Code": "AM.II.K.K1"' in text
    assert '"% Missed": "0.4816753926701571"' in text
    # The banner is not a column name, but it is the only place the sheet says which
    # block it is about, so it stays in the text.
    assert "ALL CAMPUS — Block 6 | Top 10 Most Missed ACS Codes" in text


def test_extract_xlsx_still_treats_row_one_as_the_header_when_it_is_one():
    """The common case — and every calendar — must be untouched by the scan."""
    content = _workbook([("Sheet1", [("Day", "Topic"), (1, "Corrosion")])])
    text = extract_xlsx(content).text
    assert "Columns: Day, Topic" in text
    assert '{"Day": "1", "Topic": "Corrosion"}' in text


def test_xlsx_header_row_falls_back_to_row_one_for_a_single_column_sheet():
    rows = [("Only",), ("value",)]
    assert _xlsx_header_row(rows) == (0, [])


def test_unique_headers_never_lets_one_column_overwrite_another():
    assert _unique_headers(("Rank", None, "Rank", "")) == [
        "Rank", "column_2", "Rank (2)", "column_4"]


# ── parser ───────────────────────────────────────────────────────────────────

def test_looks_like_aktr_report_gates_on_structure_not_filename():
    assert looks_like_aktr_report(_workbook([_BLOCK_6])) is True
    assert looks_like_aktr_report(_workbook([("Block 6", [("Day", "Topic"), (1, "x")])])) is False


def test_build_knowledge_test_structure_emits_one_record_per_block():
    result = build_knowledge_test_structure(_workbook([_BLOCK_1, _BLOCK_6]), "AKTR.xlsx")
    assert [b["block"] for b in result["blocks"]] == ["Block 1", "Block 6"]
    assert result["skipped_sheets"] == []
    block6 = result["blocks"][1]
    assert block6["block_number"] == 6
    assert [c["acs_code"] for c in block6["codes"]] == ["AM.II.K.K1", "AM.II.K.K5"]
    assert block6["codes"][0]["pct_missed"] == pytest.approx(0.48167539)
    assert block6["codes"][0]["rank"] == 1
    # The composed text states the figure the way a Blueprint has to print it; the
    # raw 0.4816... would read as neither a percentage nor a rank to a model.
    assert "48.2% missed" in block6["source_text"]


def test_build_knowledge_test_structure_reports_sheets_it_could_not_read():
    """A block missing from the Blueprint's performance data must be traceable to
    the sheet it should have come from — never dropped quietly."""
    result = build_knowledge_test_structure(_workbook([
        _BLOCK_6,
        ("Unlabelled", [("Rank", "% Missed", "ACS Code"), (1, 0.5, "AM.I.A.K1")]),
        ("Notes", [("Prepared by analytics",)]),
    ]))
    assert [b["block"] for b in result["blocks"]] == ["Block 6"]
    reasons = {s["sheet"]: s["reason"] for s in result["skipped_sheets"]}
    assert "no block number" in reasons["Unlabelled"]
    assert "Notes" not in reasons  # a known non-table sheet is not an anomaly


def test_build_knowledge_test_structure_reads_a_hand_edited_percentage():
    """A sheet edited by hand may hold "48.2%" or "48.2" where the export holds
    0.482 — none of the three may print as 4,816%."""
    content = _workbook([_aktr_sheet(6, [
        (1, "48.2%", "AM.II.K.K1", "a"),
        (2, "38.2", "AM.II.K.K5", "b"),
    ])])
    codes = build_knowledge_test_structure(content)["blocks"][0]["codes"]
    assert codes[0]["pct_missed"] == pytest.approx(0.482)
    assert codes[1]["pct_missed"] == pytest.approx(0.382)


def test_build_knowledge_test_structure_ignores_non_code_rows():
    content = _workbook([_aktr_sheet(6, [
        (1, 0.4, "AM.II.K.K1", "real"),
        ("", "", "Total", "not a code"),
    ])])
    codes = build_knowledge_test_structure(content)["blocks"][0]["codes"]
    assert [c["acs_code"] for c in codes] == ["AM.II.K.K1"]


def test_build_knowledge_test_structure_survives_an_unreadable_workbook():
    result = build_knowledge_test_structure(b"not a spreadsheet", "broken.xlsx")
    assert result["blocks"] == []
    assert result["skipped_sheets"][0]["reason"].startswith("unreadable workbook")


# ── round trip: what the worksheet builder reads back ────────────────────────

def test_parse_missed_codes_round_trips_composed_text():
    codes = [{"acs_code": "AM.II.K.K1", "pct_missed": 0.482, "rank": 1,
              "description": "Generators — DC generation systems."}]
    parsed = parse_missed_codes(_compose_text(6, "ALL CAMPUS — Block 6", codes))
    assert parsed["AM.II.K.K1"]["rank"] == 1
    assert parsed["AM.II.K.K1"]["pct_missed"] == pytest.approx(0.482)
    # An em dash inside the description must not truncate it at the separator.
    assert parsed["AM.II.K.K1"]["description"] == "Generators — DC generation systems."


# ── ingestion-side attribution ───────────────────────────────────────────────

def _aim_profile():
    from config.settings import get_tenant_config
    from services.client_profiles.aim import AIMClientProfile
    return AIMClientProfile(get_tenant_config("aim"))


def test_aim_profile_types_an_aktr_file_as_a_knowledge_test_report():
    """Live misclassification: with no rule of its own, the LLM classifier typed this
    file quiz_answer_key, which is a RESTRICTED family — so retrieval blocked it for
    role=user and the digest withheld its text, for a file holding no exam content."""
    profile = _aim_profile()
    assert profile._content_type("quiz_answer_key",
                                "ALLCAMPUS_AKTR_MissedCodes_Q12026.xlsx", "") == "knowledge_test_report"


def test_aim_profile_marks_an_aktr_file_neither_restricted_nor_student_facing():
    profile = _aim_profile()
    meta = profile.enrich_metadata("ALLCAMPUS_AKTR_MissedCodes_Q12026.xlsx", "",
                                   "ALL CAMPUS — Block 1 | Top 10 Most Missed ACS Codes", "", {})
    assert meta["document_type"] == "knowledge_test_report"
    assert meta["restricted"] is False
    assert meta["access_level"] == "client"
    assert meta["visibility"] == "instructor_only"
    assert meta["use_for_blueprint"] is True
    # The whole-program rollup must carry NO doc-level block: _extract_block_day reads
    # only the first 1,500 characters, i.e. the first sheet, and stamped all sixteen
    # blocks' data as Block 1 — where the block-scoped digest loader could never find
    # it for any other block.
    assert meta["block"] == ""
    assert meta["block_number"] is None
    assert meta["spans_multiple_blocks"] is True


def test_an_answer_key_is_still_typed_as_one():
    """The new branch runs before the answer-key heuristics, so it must not swallow
    them."""
    profile = _aim_profile()
    assert profile._content_type("", "B6Q1.Key.docx", "Quizzes/5-PDFs/") == "quiz_answer_key"


def test_aktr_codes_do_not_join_the_blocks_declared_coverage_set():
    """A rollup's top-10 routinely names codes from other blocks. Counting them as
    declared would inflate the block's coverage set and then report those same codes
    as taught on no day."""
    from config.settings import get_tenant_config
    from services.digests.profiles.aim import AIMCurriculumProfile
    profile = AIMCurriculumProfile(get_tenant_config("aim"))
    unit = {"unit_type": "knowledge_test_item",
            "metadata_json": {"acs_codes": ["AM.III.F.K10"]}}
    assert profile.coverage_codes(unit) == []
    lesson = {"unit_type": "page", "metadata_json": {"acs_codes": ["AM.II.K.K1"]}}
    assert profile.coverage_codes(lesson) == ["AM.II.K.K1"]


def test_a_knowledge_test_report_is_a_block_wide_reference():
    """Its rows are per ACS code, so its terms overlap every day of the block —
    term-overlap attribution would drag the whole report onto one arbitrary day."""
    from services.digests import attribution
    assert "knowledge_test_report" in attribution.BLOCK_WIDE_REFERENCE_DOC_TYPES
    assert attribution._is_block_wide_reference({"document_type": "knowledge_test_report"})


# ── Source Library record: what the retrieval path actually reads ─────────────

def test_a_rollup_is_stored_as_a_course_design_source_not_a_generic_reference():
    """The stored purpose comes from source_library's own doc-type sets, not from the
    tenant's retrieval mapping, so widening only the mapping left the rollup reading
    "general_reference" in the Source Library UI."""
    from services.source_library import normalize_purpose, purpose_flags
    assert normalize_purpose("", "knowledge_test_report") == "cdd"
    flags = purpose_flags("", "knowledge_test_report")
    assert flags["use_for_cdd"] is True
    assert flags["use_for_blueprint"] is True
    # Unrelated types must be untouched.
    assert normalize_purpose("", "course_calendar") == "blueprint"
    assert normalize_purpose("", "quiz") == "general_reference"


def test_the_content_file_keeps_one_unit_per_block_with_its_block_tag():
    """Retrieval reads the Source Library content file. Under the "full_document"
    policy the sixteen per-block units were welded into one blob carrying the
    document's own (block-less) tag, so a Block 6 request could neither filter it nor
    read only its block."""
    from services.source_library import build_clean_content_document, chunking_strategy
    assert chunking_strategy("", "knowledge_test_report") == "per_unit"
    doc = build_clean_content_document({
        "job_id": "j",
        "metadata": {"document_type": "knowledge_test_report", "title": "AKTR"},
        "source_file": {"name": "a.xlsx", "type": "xlsx"},
        "content_units": [
            {"content_unit_id": "j:knowledge_test_6", "unit_type": "knowledge_test_item",
             "unit_number": 1, "title": "Block 6", "text": "#1 — AM.II.K.K1 — 48.2% missed",
             "metadata": {"block": "Block 6", "block_number": 6,
                          "acs_codes": ["AM.II.K.K1"], "raw_s3_key": "internal"}},
            {"content_unit_id": "j:knowledge_test_9", "unit_type": "knowledge_test_item",
             "unit_number": 2, "title": "Block 9", "text": "#1 — AM.III.F.K10 — 25.1% missed",
             "metadata": {"block": "Block 9", "block_number": 9}},
        ],
    })
    units = doc["content_units"]
    assert [u["metadata"]["block"] for u in units] == ["Block 6", "Block 9"]
    # The content file is what CAS prompts read: an allow-list, not the whole dict.
    assert "raw_s3_key" not in units[0]["metadata"]


def test_a_syllabus_content_file_is_still_one_coherent_document():
    """The per-unit branch must not change any other type's content file."""
    from services.source_library import build_clean_content_document
    doc = build_clean_content_document({
        "job_id": "j",
        "metadata": {"document_type": "syllabus", "title": "Block 6 Syllabus"},
        "source_file": {"name": "s.docx", "type": "docx"},
        "content_units": [
            {"content_unit_id": "j:unit_1", "unit_type": "syllabus_section",
             "unit_number": 1, "title": "s", "text": "part one"},
            {"content_unit_id": "j:unit_2", "unit_type": "syllabus_section",
             "unit_number": 2, "title": "s", "text": "part two"},
        ],
    })
    assert len(doc["content_units"]) == 1
    assert doc["content_units"][0]["unit_type"] == "full_document"
    assert "metadata" not in doc["content_units"][0]


# ── calendar day attribution (same metadata-ordering class of bug) ────────────

class _FakeCtx:
    """Minimal PipelineContext stand-in: unit creation only reads unit_type_map and
    the chunking sizes off the config, and marks the step done."""
    def __init__(self):
        from config.settings import get_tenant_config
        self.cfg = get_tenant_config("aim")
        # Unit-creation metadata tests must not invoke Bedrock; force mock.
        self.cfg.pipeline.llm_provider = "mock"
        self.guard = None

    def step_done(self, state, name):
        return state


def _units_for(state):
    from services.agents.content_unit_creation_agent import ContentUnitCreationAgent
    agent = ContentUnitCreationAgent.__new__(ContentUnitCreationAgent)
    agent.ctx = _FakeCtx()
    return agent.run(state)["content_units"]


def test_a_calendar_sheet_unit_keeps_its_own_block_and_acs():
    """Source Library units are one per sheet (not per day). Day numbers live in
    Postgres ``dis_calendar_days``; content units must carry the sheet's block and
    the union of ACS codes so Block-N filters still see the right section.

    (Earlier regression: day units lost day_number when doc_metadata was spread
    last — that path is gone; sheet units must not lose block the same way.)
    """
    units = _units_for({
        "job_id": "j",
        "doc_type": "course_calendar",
        "doc_metadata": {"content_type": "course_calendar", "block": "Block 6",
                         "day_number": None, "day_id": ""},
        "calendar_structure": {"block": "Block 6", "days": [
            {"day_number": 1, "topic": "DC generation", "acs_codes": ["AM.II.K.K1"],
             "source_text": "Day 1 DC generation"},
            {"day_number": 2, "topic": "Voltage regulators", "acs_codes": ["AM.II.K.K5"],
             "source_text": "Day 2 Voltage regulators"},
        ]},
    })
    assert len(units) == 1
    assert units[0]["unit_type"] == "calendar_sheet"
    assert units[0]["metadata"]["block"] == "Block 6"
    assert units[0]["metadata"]["acs_codes"] == ["AM.II.K.K1", "AM.II.K.K5"]
    assert units[0]["metadata"]["total_days"] == 2
    assert units[0]["metadata"].get("tagging_status") == "pending"


def test_multi_sheet_calendar_units_are_titled_by_sheet_name():
    units = _units_for({
        "job_id": "j",
        "doc_type": "course_calendar",
        "doc_metadata": {"content_type": "course_calendar", "block": None},
        "calendar_structure": {
            "block": "",
            "days": [],
            "sheets": [
                {
                    "sheet_name": "Block 1 (Day-Night)", "sheet_index": 0,
                    "block": "Block 1", "block_number": 1, "schedule": "day_night",
                    "days": [
                        {"day_number": 1, "topic": "Math", "acs_codes": ["AM.I.H.K1"],
                         "source_text": "Day 1 Math"},
                    ],
                },
                {
                    "sheet_name": "Block 2 (Weekend)", "sheet_index": 1,
                    "block": "Block 2", "block_number": 2, "schedule": "weekend",
                    "days": [
                        {"day_number": 1, "topic": "Drawings", "acs_codes": ["AM.I.B.K1"],
                         "source_text": "Day 1 Drawings"},
                    ],
                },
            ],
        },
    })
    assert [u["title"] for u in units] == ["Block 1 (Day-Night)", "Block 2 (Weekend)"]
    assert [u["metadata"]["block"] for u in units] == ["Block 1", "Block 2"]
    assert units[1]["metadata"]["schedule"] == "weekend"
    assert units[0]["content_unit_id"] == "j:calendar_sheet_0"


def test_a_knowledge_test_unit_keeps_its_own_block():
    """The mirror case: the rollup's document metadata is deliberately block-less, so
    each unit's own block must win over it."""
    units = _units_for({
        "job_id": "j",
        "doc_type": "knowledge_test_report",
        "doc_metadata": {"content_type": "knowledge_test_report", "block": "",
                         "block_number": None, "spans_multiple_blocks": True},
        "knowledge_test_structure": {"blocks": [
            {"block": "Block 6", "block_number": 6, "sheet": "Block 6", "caption": "c6",
             "codes": [{"acs_code": "AM.II.K.K1", "pct_missed": 0.482, "rank": 1}],
             "source_text": "#1 — AM.II.K.K1 — 48.2% missed"},
            {"block": "Block 9", "block_number": 9, "sheet": "Block 9", "caption": "c9",
             "codes": [{"acs_code": "AM.III.F.K10", "pct_missed": 0.251, "rank": 1}],
             "source_text": "#1 — AM.III.F.K10 — 25.1% missed"},
        ]},
    })
    assert [u["metadata"]["block"] for u in units] == ["Block 6", "Block 9"]
    assert [u["unit_type"] for u in units] == ["knowledge_test_item"] * 2
    assert units[0]["metadata"]["acs_codes"] == ["AM.II.K.K1"]
    assert units[0]["metadata"]["missed_codes"][0]["pct_missed"] == 0.482
