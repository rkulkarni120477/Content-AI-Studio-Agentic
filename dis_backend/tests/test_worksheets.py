"""Offline unit tests for services.digests.worksheets — the Block Overview /
Source File Inventory / ACS Code Registry aggregates behind Worksheets 1-3 of the
multi-worksheet CDD/Blueprint shape. Pure functions, no DB/LLM — fixtures mirror
the real EnumerateResult shape (see enumerate.py) closely enough to exercise the
actual field names these functions read.
"""
import re
import types

from services.digests import worksheets


def _en(days, units_by_day, unattributed=None, declared_acs=None, acs_by_day=None, block="Block T", client_id="t"):
    return types.SimpleNamespace(
        block=block, client_id=client_id, total_days=len(days), days=days,
        units_by_day=units_by_day, unattributed=unattributed or [],
        declared_acs=declared_acs or [], acs_by_day=acs_by_day or {},
    )


def test_json_list_parses_text_cast_jsonb():
    # _load_days selects assignments_json/assessments_json as `::text` (the
    # JSONB column's string form) — this must parse it back, not iterate chars.
    assert worksheets._json_list('["Project 2-1"]') == ["Project 2-1"]
    assert worksheets._json_list("[]") == []
    assert worksheets._json_list(None) == []
    assert worksheets._json_list("not json") == []
    assert worksheets._json_list(["already", "a", "list"]) == ["already", "a", "list"]


def test_json_list_collapses_embedded_newlines():
    """Regression: a real Block 2 Day 17 assessments_json entry contained a raw
    embedded newline ("Quiz 10 ... Days 5 & 6\\n\\nBlock 2 Review Quiz\\n(50
    Questions)"). Every downstream consumer renders one item per markdown table
    row/cell on a single line — an unstripped newline broke that day's row and,
    in a strict markdown table parser, silently truncated every day after it
    (observed live as a block that should show 20 days rendering only ~17).
    Collapsed here, at the source, so no caller can reintroduce this."""
    raw = '["Quiz 10 from Cleaning and Corrosion Control - Days 5 & 6\\n\\nBlock 2 Review Quiz\\n(50 Questions)"]'
    result = worksheets._json_list(raw)
    assert result == ["Quiz 10 from Cleaning and Corrosion Control - Days 5 & 6 Block 2 Review Quiz (50 Questions)"]
    assert "\n" not in result[0]


def test_build_day_fields_extracts_handbook_citation_and_files():
    day = {
        "assignments_json": '["Project 2-3"]',
        "assessments_json": '["Quiz 3 from Days 1--3"]',
        "source_text": "Block 2, Day 4. Reference reading: FAA-H-8083-30B Ch. 4 pgs 4-1 to 4-9. More text.",
    }
    units = [
        {"unit_type": "page", "metadata_json": {"source_file_name": "B2D4.pdf"}},
        {"unit_type": "activity", "title": "Control Cable Check"},  # no source_file_name -> falls back to title
        {"unit_type": "calendar_day", "metadata_json": {"source_file_name": "should be excluded"}},
    ]
    fields = worksheets.build_day_fields(day, units)
    assert fields["projects_today"] == ["Project 2-3"]
    assert fields["assessment_today"] == ["Quiz 3 from Days 1--3"]
    assert fields["handbook_reference"] == "FAA-H-8083-30B Ch. 4 pgs 4-1 to 4-9"
    assert fields["source_files_today"] == ["B2D4.pdf", "Control Cable Check"]
    assert fields["handbook_edition"] == "FAA-H-8083-30B"
    assert fields["hangar_activity_today"] == []


def test_build_day_fields_splits_out_hangar_activity_by_document_type():
    """Hangar activities get their own column, matched by "hangar" appearing in
    whatever document_type the tenant's own client profile assigned — not a
    hardcoded filename check, so this works for any client using that label."""
    day = {"assignments_json": "[]", "assessments_json": "[]", "source_text": ""}
    units = [
        {"unit_type": "activity", "metadata_json": {"source_file_name": "Tying Wires.pdf",
                                                     "document_type": "hangar_activity"}},
        {"unit_type": "slide", "metadata_json": {"source_file_name": "B2D11.pptx",
                                                  "document_type": "slide_deck"}},
    ]
    fields = worksheets.build_day_fields(day, units)
    assert fields["hangar_activity_today"] == ["Tying Wires.pdf"]
    assert fields["source_files_today"] == ["B2D11.pptx", "Tying Wires.pdf"]


def test_build_acs_registry_defaults_and_days_active():
    en = _en(
        days=[{"day_number": 1}, {"day_number": 2}],
        units_by_day={},
        declared_acs=["AM.I.B.K1", "AM.I.B.R1", "AM.I.B.S1"],
        acs_by_day={1: {"AM.I.B.K1", "AM.I.B.R1"}, 2: {"AM.I.B.K1", "AM.I.B.S1"}},
    )
    registry = {r["acs_code"]: r for r in worksheets.build_acs_registry(en)}
    assert registry["AM.I.B.K1"]["days_active"] == [1, 2]
    assert registry["AM.I.B.K1"]["acs_type"] == "K — Knowledge"
    assert registry["AM.I.B.K1"]["quick_check_priority"].startswith("RECALL")
    assert registry["AM.I.B.R1"]["quick_check_priority"].startswith("ANALYZE")
    assert registry["AM.I.B.S1"]["quick_check_priority"].startswith("APPLY")
    # Never fabricated — AKTR/ACS1.pdf are not ingested anywhere in this system.
    assert registry["AM.I.B.K1"]["task_description"] == "NOT AVAILABLE — ACS1.pdf not ingested"
    assert registry["AM.I.B.K1"]["high_miss"] == "NO AKTR DATA — not supplied"


def test_build_acs_registry_sorts_naturally_not_lexicographically():
    """Regression: a real Block 2 run showed 'AM.I.G.K1, K10, K11, ... K19, K2,
    K20, K22, ...' for the G subject (10+ codes) — a plain string sort orders
    'K10' before 'K2' since '1' < '2' character-wise. Every subject with >=10
    codes of one letter, in any block, hits this."""
    codes = [f"AM.I.G.K{n}" for n in [1, 10, 11, 2, 20, 3, 9]]
    en = _en(days=[{"day_number": 1}], units_by_day={}, declared_acs=codes)
    ordered = [r["acs_code"] for r in worksheets.build_acs_registry(en)]
    assert ordered == ["AM.I.G.K1", "AM.I.G.K2", "AM.I.G.K3", "AM.I.G.K9",
                        "AM.I.G.K10", "AM.I.G.K11", "AM.I.G.K20"]


def test_build_source_file_inventory_groups_by_document_type_not_literal_file():
    """Regression: a real block's inventory used to list one row per literal
    filename (87+ rows) — grouping by document_type instead reproduces AIM's own
    curated category-rollup shape (~14 rows) as an emergent property, since types
    with many files (e.g. slide decks) naturally collapse into one row."""
    units_by_day = {
        1: [
            {"unit_type": "page", "metadata_json": {"source_file_name": "B2D1.pdf", "document_type": "lesson_pdf"}},
            {"unit_type": "calendar_day", "metadata_json": {"source_file_name": "B2D1.pdf"}},
        ],
        2: [{"unit_type": "page", "metadata_json": {"source_file_name": "B2D2.pdf", "document_type": "lesson_pdf"}}],
    }
    unattributed = [
        {"unit_type": "answer_key_item", "metadata_json": {"source_file_name": "Key.pdf",
                                                            "document_type": "quiz_answer_key"}},
    ]
    en = _en(days=[{"day_number": 1}, {"day_number": 2}], units_by_day=units_by_day, unattributed=unattributed)
    inventory = {r["document_type"]: r for r in worksheets.build_source_file_inventory(en)}
    assert set(inventory) == {"lesson_pdf", "quiz_answer_key"}  # calendar_day pseudo-unit excluded

    lesson = inventory["lesson_pdf"]
    assert lesson["file_count"] == 2  # B2D1.pdf + B2D2.pdf rolled into one row
    assert lesson["days_applicable"] == [1, 2]
    assert lesson["status"] == "EXISTS"
    assert lesson["production_action"] == "Include in input bundle"
    assert "Days 1-2" in lesson["status_notes"]

    answer_key = inventory["quiz_answer_key"]
    assert answer_key["days_applicable"] == ["Unattributed"]
    # Restricted per the default (and AIM's real) restricted_document_types list.
    assert answer_key["production_action"] == "Instructor-only — exclude from student-facing use"


def test_build_source_file_inventory_reads_tenant_restricted_types():
    """The restricted-type rule must come from the tenant's OWN config, not a
    hardcoded default, so a client with a different restricted list is honored."""
    units_by_day = {1: [{"unit_type": "page",
                        "metadata_json": {"source_file_name": "X.pdf", "document_type": "custom_type"}}]}
    en = _en(days=[{"day_number": 1}], units_by_day=units_by_day)
    tenant_cfg = types.SimpleNamespace(
        document_processing=types.SimpleNamespace(restricted_document_types=["custom_type"])
    )
    inventory = {r["document_type"]: r for r in worksheets.build_source_file_inventory(en, tenant_cfg)}
    assert inventory["custom_type"]["production_action"] == "Instructor-only — exclude from student-facing use"


class _FakeDocsCursor:
    """Mock for the dis_documents lookup in _block_wide_reference_files — records
    the query so tests can assert the block-name regex and type filter, and
    returns whatever rows the test configures."""
    def __init__(self, rows):
        self._rows = rows
        self.sql = None
        self.params = None

    def execute(self, sql, params):
        self.sql = sql
        self.params = params

    def fetchall(self):
        return self._rows


def test_build_source_file_inventory_adds_block_wide_reference_docs_not_in_units():
    """Regression: a real Block 2 run's Worksheet 2 never listed the syllabus or
    course calendar at all — those documents never became per-day content units
    (metadata_json->>'block' normalization drops them, see _syllabus_text), so
    the units-only pass silently missed core reference documents. A supplemental
    dis_documents query should surface them as their own "All"-days rows without
    double-counting a type the units pass already found."""
    en = _en(days=[{"day_number": 1}], units_by_day={}, unattributed=[], block="Block 2", client_id="aim")
    cur = _FakeDocsCursor(rows=[
        {"document_type": "syllabus", "source_file_name": "Block 02 Syllabus (Rev. 01.14.26).docx"},
        {"document_type": "course_calendar", "source_file_name": "Block 02 Calendar.pdf"},
        {"document_type": "course_calendar", "source_file_name": "Block 02 Calendar (Weekend).pdf"},
    ])
    inventory = {r["document_type"]: r for r in
                worksheets.build_source_file_inventory(en, cur=cur, schema="dis")}

    assert cur.params[0] == "aim"
    assert set(cur.params[1]) == {"syllabus", "course_calendar", "ebook_reference"}
    assert re.search(cur.params[2], "Block 02 Syllabus.docx")
    assert re.search(cur.params[2], "Some Block 2 File.pdf")

    assert inventory["syllabus"]["file_count"] == 1
    assert inventory["syllabus"]["days_applicable"] == ["All"]
    assert "block-wide reference" in inventory["syllabus"]["status_notes"]
    assert inventory["course_calendar"]["file_count"] == 2


def test_build_source_file_inventory_prefers_units_pass_over_block_wide_supplement():
    """If a tenant's classifier DOES tag a block-wide type as a per-day unit, the
    units-pass result must win — the supplement only fills genuine gaps, never
    overwrites/duplicates a type already found."""
    units_by_day = {1: [{"unit_type": "page",
                        "metadata_json": {"source_file_name": "Syllabus.pdf", "document_type": "syllabus"}}]}
    en = _en(days=[{"day_number": 1}], units_by_day=units_by_day, block="Block 2", client_id="aim")
    cur = _FakeDocsCursor(rows=[
        {"document_type": "syllabus", "source_file_name": "Some Other Syllabus Copy.docx"},
    ])
    inventory = {r["document_type"]: r for r in
                worksheets.build_source_file_inventory(en, cur=cur, schema="dis")}
    assert inventory["syllabus"]["file_count"] == 1
    assert inventory["syllabus"]["days_applicable"] == [1]  # units-pass result, not the supplement's "All"


def test_build_source_file_inventory_with_no_cursor_is_unchanged():
    """cur=None (the default) must behave exactly as before this change — no
    live DB access attempted, no block-wide rows added."""
    en = _en(days=[{"day_number": 1}], units_by_day={}, block="Block 2", client_id="aim")
    inventory = worksheets.build_source_file_inventory(en)
    assert inventory == []


class _RaisingCursor:
    """Simulates a DB failure (connection drop, query error, etc.) mid-lookup."""
    def execute(self, sql, params):
        raise RuntimeError("simulated DB failure")

    def fetchall(self):
        raise AssertionError("must not be called — execute() should have raised first")


def test_build_source_file_inventory_degrades_to_units_only_on_query_failure():
    """A failure in the block-wide-reference lookup must be caught INSIDE that
    lookup, not left to propagate — otherwise one failing query would take
    down the whole worksheet build (including the units-pass rows that had
    nothing to do with the failure), and — at the real call site in build.py —
    would also discard an already-successful block_overview/acs_registry
    result computed earlier in the same bundled try/except."""
    units_by_day = {1: [{"unit_type": "page",
                        "metadata_json": {"source_file_name": "X.pdf", "document_type": "lesson_pdf"}}]}
    en = _en(days=[{"day_number": 1}], units_by_day=units_by_day, block="Block 2", client_id="aim")
    inventory = {r["document_type"]: r for r in
                worksheets.build_source_file_inventory(en, cur=_RaisingCursor(), schema="dis")}
    assert inventory["lesson_pdf"]["file_count"] == 1  # units-pass result survives intact
    assert "syllabus" not in inventory  # the failed block-wide lookup contributes nothing, not a crash


def test_build_acs_registry_degrades_to_not_available_on_query_failure():
    en = _en(days=[{"day_number": 1}], units_by_day={}, declared_acs=["AM.I.B.K1"], client_id="aim")
    registry = {r["acs_code"]: r for r in
               worksheets.build_acs_registry(en, cur=_RaisingCursor(), schema="dis")}
    assert registry["AM.I.B.K1"]["task_description"] == "NOT AVAILABLE — ACS1.pdf not ingested"


_ACS1_SAMPLE_TEXT = (
    "I. General Subject B. Aircraft Drawings References AC 43.13-1; FAA-H-8083-30 "
    "Objective The following knowledge, risk management, and skill elements are "
    "required to demonstrate understanding of aircraft drawings. "
    "Knowledge The applicant demonstrates understanding of: "
    "AM.I.B.K1 Drawings, blueprints, sketches, charts, graphs, and system schematics, "
    "including commonly used lines, symbols, and terminology. "
    "AM.I.B.K2 Repair or alteration of an aircraft system or component(s) using "
    "drawings, blueprints, or system schematics to determine whether it conforms to "
    "its type design. 27 © 2026 Aviation Supplies & Academics, Inc. Provided for use "
    "by the Aviation Institute of Maintenance's enrolled students, active instructors, "
    "and program administrators. [Page 35] "
    "AM.I.B.K3 Inspection of an aircraft system or component(s) using drawings, "
    "blueprints, or system schematics. "
    "Risk Management The applicant demonstrates the ability to identify, assess, and "
    "mitigate risks associated with: "
    "AM.I.B.R1 Interpretation of plus or minus tolerances as depicted on aircraft "
    "drawings. "
    "Skills The applicant demonstrates the ability to: "
    "AM.I.B.S4 Identify changes on an aircraft drawing."
)


def test_parse_acs1_task_descriptions_extracts_clean_per_code_text():
    """Regression: fixture is a shortened but structurally faithful excerpt of the
    real ACS-1.pdf text (confirmed live against the ingested document) — a page
    footer lands mid-list between K2 and K3, and Knowledge/Risk Management/Skills
    section-preamble phrases separate the three code groups. Each code's captured
    description must be clean prose with none of that boilerplate/preamble text
    bleeding in."""
    result = worksheets._parse_acs1_task_descriptions(_ACS1_SAMPLE_TEXT)
    assert result["AM.I.B.K1"] == (
        "Drawings, blueprints, sketches, charts, graphs, and system schematics, "
        "including commonly used lines, symbols, and terminology"
    )
    # K2's description must stop before the page-footer text, not run into it.
    assert result["AM.I.B.K2"] == (
        "Repair or alteration of an aircraft system or component(s) using drawings, "
        "blueprints, or system schematics to determine whether it conforms to its "
        "type design"
    )
    assert "©" not in result["AM.I.B.K2"] and "administrators" not in result["AM.I.B.K2"]
    assert result["AM.I.B.K3"] == (
        "Inspection of an aircraft system or component(s) using drawings, blueprints, "
        "or system schematics"
    )
    # K3's description must stop before "Risk Management The applicant...", not
    # bleed into the next section's preamble sentence.
    assert "Risk Management" not in result["AM.I.B.K3"]
    assert result["AM.I.B.R1"] == "Interpretation of plus or minus tolerances as depicted on aircraft drawings"
    assert result["AM.I.B.S4"] == "Identify changes on an aircraft drawing"


def test_parse_acs1_task_descriptions_returns_empty_for_unparseable_text():
    assert worksheets._parse_acs1_task_descriptions("no codes in here at all") == {}


class _FakeAcs1Cursor:
    """Mocks the two-query sequence _acs1_task_descriptions issues: a cheap
    SUM(length(...)) sizing query first (no text transferred), then a full
    text_content fetch scoped to whichever document_ids survive the size
    filter — mirrors what real psycopg calls/fetches would see."""
    def __init__(self, doc_rows):
        self._doc_rows = doc_rows  # list of {"document_id": ..., "text_content": ...}
        self.calls = []  # (sql, params) per execute(), for assertions

    def execute(self, sql, params):
        self.calls.append((sql, params))

    def fetchall(self):
        if len(self.calls) == 1:
            sizes = {}
            for row in self._doc_rows:
                sizes[row["document_id"]] = sizes.get(row["document_id"], 0) + len(row["text_content"] or "")
            return [{"document_id": doc_id, "total_chars": total} for doc_id, total in sizes.items()]
        candidate_ids = set(self.calls[1][1][0])
        return [row for row in self._doc_rows if row["document_id"] in candidate_ids]


def test_build_acs_registry_wires_up_task_description_from_ingested_acs1():
    """End-to-end: build_acs_registry, given a cursor pointing at an ingested
    ACS-1-shaped ebook_reference document, must fill task_description for a
    declared code found in it — closing the previously-hardcoded NOT AVAILABLE
    gap for any code the document actually covers."""
    en = _en(
        days=[{"day_number": 1}], units_by_day={},
        declared_acs=["AM.I.B.K1", "AM.I.B.K99"],
        acs_by_day={1: {"AM.I.B.K1"}},
        client_id="aim",
    )
    # Pad with enough distinct codes to clear _ACS1_MIN_DISTINCT_CODES so this
    # reads as "the ACS reference doc", not a passing citation.
    padding = " ".join(f"AM.I.C.K{i} Filler description {i}." for i in range(3, 25))
    cur = _FakeAcs1Cursor([{"document_id": "doc1", "text_content": _ACS1_SAMPLE_TEXT + " " + padding}])
    registry = {r["acs_code"]: r for r in worksheets.build_acs_registry(en, cur=cur, schema="dis")}
    assert registry["AM.I.B.K1"]["task_description"].startswith("Drawings, blueprints")
    # A declared code NOT found in the reference doc still degrades honestly.
    assert registry["AM.I.B.K99"]["task_description"] == "NOT AVAILABLE — ACS1.pdf not ingested"


def test_build_acs_registry_ignores_a_document_with_too_few_distinct_codes():
    """A syllabus/calendar that merely cites a handful of codes in passing must
    NOT be mistaken for the ACS reference document — only a document with at
    least _ACS1_MIN_DISTINCT_CODES distinct codes qualifies."""
    en = _en(days=[{"day_number": 1}], units_by_day={}, declared_acs=["AM.I.B.K1"], client_id="aim")
    cur = _FakeAcs1Cursor([{"document_id": "doc1", "text_content": _ACS1_SAMPLE_TEXT}])  # only 5 distinct codes
    registry = {r["acs_code"]: r for r in worksheets.build_acs_registry(en, cur=cur, schema="dis")}
    assert registry["AM.I.B.K1"]["task_description"] == "NOT AVAILABLE — ACS1.pdf not ingested"


def test_acs1_task_descriptions_never_transfers_text_for_an_oversized_document():
    """Regression: a real client had two OTHER ebook_reference PDFs (multi-MB FAA
    handbooks) alongside the real ~200K-char ACS-1 document — those must be
    excluded by the cheap size-only query before any text_content is ever
    fetched for them, both so they can never win by accident and so a large
    tenant's reference library can't make every registry build scan megabytes
    of text that structurally can never be the ACS document."""
    padding = " ".join(f"AM.I.C.K{i} Filler description {i}." for i in range(3, 25))
    real_acs_doc = {"document_id": "acs1", "text_content": _ACS1_SAMPLE_TEXT + " " + padding}
    oversized_doc = {"document_id": "huge_handbook", "text_content": "x" * (worksheets._ACS1_MAX_CANDIDATE_CHARS + 1)}
    en = _en(days=[{"day_number": 1}], units_by_day={}, declared_acs=["AM.I.B.K1"], client_id="aim")
    cur = _FakeAcs1Cursor([real_acs_doc, oversized_doc])
    registry = {r["acs_code"]: r for r in worksheets.build_acs_registry(en, cur=cur, schema="dis")}
    assert registry["AM.I.B.K1"]["task_description"].startswith("Drawings, blueprints")
    # The second query's own candidate-id filter must exclude the oversized doc.
    second_call_params = cur.calls[1][1]
    assert second_call_params[0] == ["acs1"]


def test_build_block_overview_totals_and_subjects_no_syllabus():
    days = [
        {"day_number": 1, "assignments_json": "[]", "assessments_json": "[]", "source_text": ""},
        {"day_number": 2, "assignments_json": '["Project 2-1"]', "assessments_json": '["Quiz 1"]', "source_text": ""},
    ]
    en = _en(days=days, units_by_day={}, declared_acs=["AM.I.B.K1", "AM.I.E.K1"])
    flags: list = []
    # No DB cursor -> honest gap, not a crash. "No cursor" means the lookup never
    # RAN, so the honest gap is "not read", not "not ingested" — the latter is a
    # claim about the source library that this call has no basis for, and it sent
    # readers off to re-upload a syllabus that was already there.
    overview = worksheets.build_block_overview(en, cur=None, flags=flags)
    assert overview["total_projects"] == 1
    assert overview["total_quizzes"] == 1
    assert overview["acs_subjects_covered"] == ["B", "E"]
    assert overview["course_description"] == f"NOT AVAILABLE — {worksheets.SYLLABUS_NOT_READ}"
    assert overview["supplemental_references"] == f"NOT AVAILABLE — {worksheets.SYLLABUS_NOT_READ}"
    assert "not ingested" not in overview["course_description"]
    assert overview["primary_handbooks"] == ["NOT AVAILABLE — no handbook citations found"]
    assert overview["web_resources"] == ["NOT AVAILABLE — none found in ingested text"]
    # And it is reported, not just rendered: a cell is not a channel anyone watches.
    assert any(f.startswith("SYLLABUS_NOT_READ") for f in flags)


def test_build_block_overview_empty_syllabus_still_blames_ingestion():
    """The one case where "not ingested" IS the honest answer: the query ran and
    returned nothing."""
    class _Cur:
        def execute(self, *_a, **_k): pass
        def fetchone(self): return None

    en = _en(days=[], units_by_day={}, declared_acs=[], block="Block 2")
    flags: list = []
    overview = worksheets.build_block_overview(en, cur=_Cur(), flags=flags)
    assert overview["course_description"] == f"NOT AVAILABLE — {worksheets.SYLLABUS_NOT_INGESTED}"
    assert flags == [], "a successful lookup that found nothing is not a degradation"


def test_build_block_overview_unnumbered_block_says_unsearchable_not_uningested():
    """The filename match is anchored on a block NUMBER. A label without one is
    never queried, so claiming the syllabus is not ingested is unfounded — and it
    is the claim a reader would act on by re-uploading a document already there."""
    class _Cur:
        def __init__(self): self.executed = False
        def execute(self, *_a, **_k): self.executed = True
        def fetchone(self): return None

    cur = _Cur()
    en = _en(days=[], units_by_day={}, declared_acs=[], block="Foundations")
    flags: list = []
    overview = worksheets.build_block_overview(en, cur=cur, flags=flags)
    assert cur.executed is False, "nothing should be queried for an unnumbered block"
    assert overview["course_description"] == f"NOT AVAILABLE — {worksheets.SYLLABUS_NOT_SEARCHABLE}"
    assert any(f.startswith("SYLLABUS_NOT_SEARCHABLE") for f in flags)


def test_build_block_overview_query_failure_is_logged_and_flagged_not_blamed_on_ingestion(caplog):
    """A raising lookup used to leave the pre-set "not ingested" placeholders and log
    nothing at all — a broken query rendered as a confident claim about the library."""
    class _Cur:
        def execute(self, *_a, **_k): raise RuntimeError("relation does not exist")
        def fetchone(self): return None

    en = _en(days=[], units_by_day={}, declared_acs=[], block="Block 2")
    flags: list = []
    with caplog.at_level("WARNING"):
        overview = worksheets.build_block_overview(en, cur=_Cur(), flags=flags)

    assert overview["course_description"] == f"NOT AVAILABLE — {worksheets.SYLLABUS_LOOKUP_FAILED}"
    # Every field, including the derived one that is not a marker key.
    assert overview["supplemental_references"].endswith(worksheets.SYLLABUS_LOOKUP_FAILED)
    assert any(f.startswith("SYLLABUS_LOOKUP_FAILED") for f in flags)
    assert any("syllabus lookup failed" in r.getMessage().lower() for r in caplog.records)
    # Still best-effort: the rest of the overview is intact.
    assert overview["block"] == en.block


def test_syllabus_text_orders_by_created_at_desc_to_break_duplicate_ties():
    """Regression: Block 2 has two ingested syllabus documents matching the same
    filename regex — a stale "...Syllabus Revised.docx" and the canonical
    "...Syllabus (Rev. 01.14.26).docx" — with different grading percentages. A bare
    LIMIT 1 with no ORDER BY let Postgres return either one nondeterministically;
    this asserts the query is now deterministic (most recent ingestion wins)."""
    class _FakeCursor:
        def __init__(self):
            self.sql = None
            self.params = None

        def execute(self, sql, params):
            self.sql = sql
            self.params = params

        def fetchone(self):
            return {"text_content": "canonical text"}

    cur = _FakeCursor()
    en = _en(days=[], units_by_day={}, block="Block 2", client_id="aim")
    result = worksheets._syllabus_text(en, cur, schema="dis")
    assert result == "canonical text"
    assert "ORDER BY doc.created_at DESC" in cur.sql
    assert cur.sql.index("ORDER BY doc.created_at DESC") < cur.sql.index("LIMIT 1")


def test_extract_syllabus_fields_verbatim_marker_slicing():
    text = ("Course Description: This block covers drawings. Course Objectives: "
            "Students will demonstrate ACS knowledge. Grading and Evaluation: "
            "Quizzes 20%, Projects 50%.")
    fields = worksheets._extract_syllabus_fields(text)
    assert fields["course_description"] == "This block covers drawings."
    assert fields["course_objectives"] == "Students will demonstrate ACS knowledge."
    assert fields["grading_policy"] == "Quizzes 20%, Projects 50%."


def test_extract_syllabus_fields_grading_stops_before_next_real_heading_not_600_chars():
    """Regression: a real Block 2 syllabus (fetched live) has NO blank line
    between "Grading and Evaluation:" and the unrelated policy sections that
    immediately follow it in the same flattened line — the old fixed `start+600`
    boundary cut off mid-word ("...regardless of the reaso"), and naively running
    to the end of the text would be just as wrong the other way (it would swallow
    Attendance Policy/Late Work/Class Format too). The fix must stop exactly at
    the next real "Title Case Heading:" the source itself has."""
    text = (
        "Grading and Evaluation: Student grades will be based on quizzes & daily "
        "activity, shop projects, a cumulative examination and attendance. All "
        "shop projects and the final cumulative examination must be passed with "
        "a 70% or higher for successful block completion. The course grade will "
        "be computed based on the following: Attendance Policy: Students are "
        "expected to attend and participate in all scheduled class time. Regular "
        "attendance is critical to the successful completion of your program, "
        "regardless of the reason." * 2  # padded well past the old 600-char cutoff
    )
    fields = worksheets._extract_syllabus_fields(text)
    assert fields["grading_policy"].startswith("Student grades will be based on")
    assert fields["grading_policy"].endswith("the following:")
    assert "Attendance Policy" not in fields["grading_policy"]
    assert "regardless of the reaso" not in fields["grading_policy"]  # no mid-word cutoff either


def test_extract_syllabus_fields_appends_detected_percentage_table():
    """Regression: a real syllabus's grading percentages land in a separate
    flattened table physically apart from the "Grading and Evaluation:" prose
    (a PDF-table-flattening artifact) — this must still be found and appended."""
    text = ("Grading and Evaluation: Student grades will be based on the following. "
            "Attendance Policy: some unrelated policy text. "
            "Document Date | 09/29/2025 Quizzes & Daily Activity | 20% "
            "Required Shop Projects | 50% Cumulative Exam | 20% Attendance | 10% Total | 100%")
    fields = worksheets._extract_syllabus_fields(text)
    assert "Breakdown:" in fields["grading_policy"]
    assert "Quizzes & Daily Activity 20%" in fields["grading_policy"]
    assert "Total 100%" not in fields["grading_policy"]  # sum row excluded, not a component


def test_extract_syllabus_fields_extracts_supplemental_references():
    text = ("Required Text(s): FAA-H-8083-30B, Handbook AC43.13-1B/2B, Aircraft "
            "Inspection, Repair, & Alterations Other Required Materials: None "
            "Recommended Supplemental Material: AC43-4A, Corrosion Control for "
            "Aircraft Course Description: This course meets requirements.")
    fields = worksheets._extract_syllabus_fields(text)
    refs = fields["supplemental_references"]
    assert "AC43.13-1B/2B" in refs
    assert "AC43-4A" in refs
    assert "FAA-H-8083-30B" not in refs  # deduped against primary_handbooks
    assert "None" not in refs  # "Other Required Materials: None" correctly omitted


def test_heading_scan_not_fooled_by_capitalized_acronym_in_parens():
    """Regression: a real syllabus's "Required Text(s):" section ends with
    "...FAA Airman Certification Standards (ACS) Other Required Materials:" —
    "ACS)" itself (capital A, directly after "(") satisfied the per-word heading
    pattern and matched as if IT were the heading start, truncating the previous
    field one word early ("...Standards (" instead of "...Standards (ACS)")."""
    text = ("Required Text(s): FAA Airman Certification Standards (ACS) "
            "Other Required Materials: None Course Description: Text.")
    fields = worksheets._extract_syllabus_fields(text)
    refs = fields["supplemental_references"]
    assert "Standards (ACS)" in refs
    assert not refs.rstrip().endswith("(")


def test_primary_handbooks_groups_by_handbook_not_exact_citation():
    """A handbook cited with a DIFFERENT page range on different days must still
    collapse into one prose line per handbook (AIM's own sample shape), not one
    row per distinct citation string."""
    days = [
        {"day_number": 1, "source_text": "Reference reading: FAA-H-8083-30B Ch. 4 pgs 4-1 to 4-9."},
        {"day_number": 2, "source_text": "Reference reading: FAA-H-8083-30B Ch. 4 pgs 4-9 to 4-22."},
        {"day_number": 14, "source_text": "Reference reading: FAA-H-8083-31B Ch. 8 pgs 8-5 to 8-15."},
    ]
    result = worksheets._primary_handbooks(days)
    assert result == [
        "FAA-H-8083-30B — cited on Days 1-2",
        "FAA-H-8083-31B — cited on Days 14",
    ]


def test_build_web_resources_finds_unique_urls_in_ingested_text():
    days = [{"source_text": "See https://example.com/tools for details."}]
    units_by_day = {1: [{"text_content": "Also see https://example.com/tools and https://other.com/a."}]}
    urls = worksheets.build_web_resources(days, units_by_day)
    assert urls == ["https://example.com/tools", "https://other.com/a"]  # trailing "." stripped, dedup preserved


def test_build_web_resources_strips_leaked_prose_with_no_separator():
    """Regression: a real Block 2 page unit had a lost line-break — a PDF-glyph-
    fallback bullet character (Unicode Private Use Area, e.g. U+F0B7) landing
    directly between a URL and the sentence that followed it, with no whitespace
    at all: ".../Z3ZO6oRbptM" + "Questions" + <PUA bullet> + "The video
    mentions...". Plain \\S+ doesn't stop at a PUA char, so it swallowed
    "Questions" AND ran through into "The" too. The correct YouTube ID is
    exactly 11 characters (Z3ZO6oRbptM)."""
    bullet = chr(0xF0B7)
    text = "Video Link: https://youtu.be/Z3ZO6oRbptM" + "Questions" + bullet + "The video mentions views."
    units_by_day = {1: [{"text_content": text}]}
    urls = worksheets.build_web_resources([], units_by_day)
    assert urls == ["https://youtu.be/Z3ZO6oRbptM"]
