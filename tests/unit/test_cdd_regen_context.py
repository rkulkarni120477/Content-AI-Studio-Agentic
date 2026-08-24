"""Grounding and size discipline for CDD regeneration.

Two failures are pinned here.

The first is silent truncation. ``parse_items_from_section`` treats a markdown
table as one item, so a 20-day CDD day table reached the per-item regenerate
endpoint as a single ~18,110-token item against a 16,384-token output ceiling.
The model returned what it could, ``patch_item_in_section`` spliced the
truncation back in, and the browser committed it — trailing days vanished with
a success toast. ``assert_can_emit`` must refuse that call.

The second is ungrounded regeneration: the section endpoint sent a section name
and a course title and nothing else, then replaced the section with the answer.
``build_context`` must put the document's own worksheets back in front of the
model, scoped to what the instruction actually names.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import RegenerationTooLargeError
from app.services import cdd_regen_context as R
from promptops_app.database import Base, CDDVersion, CourseDesignDocument

# A miniature of the real artifact: bullet overview, two pipe tables, day rows
# whose first cell is "Day N". Shapes copied from a live block-wide CDD.
OVERVIEW = "- **Block:** Block 2\n- **Total Days:** 3\n- **Acs Subjects Covered:** B; E"

DAY_TABLE = (
    "| Day | Topic | ACS | Learning Objective | Misconceptions |\n"
    "|---|---|---|---|---|\n"
    "| Day 1 | Aircraft Drawings | AM.I.B.K1, AM.I.B.K4 | Read a title block | NONE DOCUMENTED |\n"
    "| Day 2 | Lines and Symbols | AM.I.B.K3 | Identify line types | Confuses hidden lines |\n"
    "| Day 3 | Graphs and Charts | — | Interpret a chart | NONE DOCUMENTED |\n"
)

ACS_TABLE = (
    "| ACS Code | Type | Task Description | Days Active |\n"
    "|---|---|---|---|\n"
    "| AM.I.B.K1 | K — Knowledge | Drawings and blueprints | 1 |\n"
    "| AM.I.B.K3 | K — Knowledge | Inspection using schematics | 2 |\n"
    "| AM.I.B.K4 | K — Knowledge | Terms used with drawings | 1 |\n"
)

SOURCES_TABLE = (
    "| Document Type | File Count | Days Applicable |\n"
    "|---|---|---|\n"
    "| course_calendar | 6 | All |\n"
)

SECTIONS = {
    "WORKSHEET 1: BLOCK OVERVIEW": OVERVIEW,
    "WORKSHEET 2: SOURCE FILE INVENTORY": SOURCES_TABLE,
    "WORKSHEET 3: ACS CODE REGISTRY": ACS_TABLE,
    "WORKSHEET 4: DAY-BY-DAY MAP": DAY_TABLE,
}

DAY_COLUMNS = ["Day", "Topic", "ACS", "Learning Objective", "Misconceptions"]


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture()
def cdd(db):
    import json

    doc = CourseDesignDocument(title="Block 2", course_title="Block 2 — General Science II",
                               active_version="v1", course_id=48, project_id=1)
    db.add(doc)
    db.commit()
    db.add(CDDVersion(cdd_id=doc.id, version="v1", full_content="",
                      sections=json.dumps(SECTIONS), is_active=True))
    db.commit()
    return doc


# ---------------------------------------------------------------------------
# Instruction parsing
# ---------------------------------------------------------------------------

def test_parses_a_single_day():
    assert R.parse_scope("Rewrite Day 4", known_columns=DAY_COLUMNS).day_numbers == (4,)


def test_parses_several_days():
    assert R.parse_scope("Fix Day 4 and Day 9").day_numbers == (4, 9)


def test_expands_a_narrow_range():
    assert R.parse_scope("objectives on Days 5-7").day_numbers == (5, 6, 7)


def test_a_whole_block_range_is_not_a_day_scope():
    """"Review Days 1-20" means the block, not Day 1.

    The regression this guards: the range is rejected for being too wide, then
    the single-day pattern reads "Days 1" out of the same text and scopes to
    one day — handing the model 1/20th of the rows for an instruction about all
    of them, with nothing in the response to show the rest were dropped.
    """
    scope = R.parse_scope("Review Days 1-20 for consistency")
    assert scope.day_numbers == ()
    assert not scope.is_targeted


def test_parses_acs_codes():
    assert R.parse_scope("tighten AM.I.B.K3").acs_codes == ("AM.I.B.K3",)


def test_matches_only_columns_the_worksheet_has():
    scope = R.parse_scope("fix the Misconceptions", known_columns=DAY_COLUMNS)
    assert scope.columns == ("Misconceptions",)
    # A column name that isn't in this worksheet cannot be matched into scope.
    assert R.parse_scope("fix the Job Aid Type", known_columns=DAY_COLUMNS).columns == ()


def test_day_and_acs_are_never_treated_as_column_names():
    """Both appear in almost every instruction; matching them as columns would
    tag nearly everything with a column scope it never asked for."""
    scope = R.parse_scope("update Day 2 ACS mapping", known_columns=DAY_COLUMNS)
    assert scope.columns == ()
    assert scope.day_numbers == (2,)


def test_an_ordinary_instruction_is_untargeted():
    assert not R.parse_scope("make it more concise", known_columns=DAY_COLUMNS).is_targeted


# ---------------------------------------------------------------------------
# Table parsing
# ---------------------------------------------------------------------------

def test_parses_a_pipe_table():
    table = R.parse_markdown_table(DAY_TABLE)
    assert table.is_table
    assert table.columns == tuple(DAY_COLUMNS)
    assert len(table.rows) == 3


def test_prose_is_not_a_table():
    assert not R.parse_markdown_table(OVERVIEW).is_table


def test_pipes_without_a_separator_row_are_not_a_table():
    assert not R.parse_markdown_table("| a | b |\n| c | d |").is_table


def test_rendering_a_row_subset_stays_a_valid_table():
    table = R.parse_markdown_table(DAY_TABLE)
    rendered = table.render([table.rows[1]])
    reparsed = R.parse_markdown_table(rendered)
    assert reparsed.columns == table.columns
    assert len(reparsed.rows) == 1


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------

def test_a_named_day_brings_only_that_day_and_its_acs_codes(db, cdd):
    ctx = R.build_context(db, cdd, section_key="WORKSHEET 4: DAY-BY-DAY MAP",
                          instruction="Rewrite the Learning Objective for Day 2",
                          section_content=DAY_TABLE)
    assert ctx.is_grounded
    assert "day_rows" in ctx.sources
    assert "Lines and Symbols" in ctx.text          # Day 2 is present
    assert "Aircraft Drawings" not in ctx.text      # Day 1 is not
    # The registry entry for Day 2's own code comes along; the others do not.
    assert "AM.I.B.K3" in ctx.text
    assert "Terms used with drawings" not in ctx.text


def test_an_untargeted_instruction_gets_the_day_index_not_the_whole_table(db, cdd):
    """Cross-day awareness without paying for every cell — the day table is the
    part that does not fit, so an untargeted regeneration gets its index."""
    ctx = R.build_context(db, cdd, section_key="WORKSHEET 4: DAY-BY-DAY MAP",
                          instruction="make it more consistent", section_content=DAY_TABLE)
    assert "day_index" in ctx.sources
    assert "day_rows" not in ctx.sources
    assert "Day 1 — Aircraft Drawings" in ctx.text
    assert "NONE DOCUMENTED" not in ctx.text        # no full rows


def test_the_block_overview_is_always_included(db, cdd):
    ctx = R.build_context(db, cdd, section_key="WORKSHEET 4: DAY-BY-DAY MAP",
                          instruction="Day 1", section_content=DAY_TABLE)
    assert "block_overview" in ctx.sources
    assert "Total Days" in ctx.text


def test_the_source_inventory_is_only_included_when_asked_about(db, cdd):
    plain = R.build_context(db, cdd, section_key="WORKSHEET 4: DAY-BY-DAY MAP",
                            instruction="Rewrite Day 1", section_content=DAY_TABLE)
    asked = R.build_context(db, cdd, section_key="WORKSHEET 4: DAY-BY-DAY MAP",
                            instruction="Check the handbook files for Day 1",
                            section_content=DAY_TABLE)
    assert "source_inventory" not in plain.sources
    assert "source_inventory" in asked.sources


def test_an_acs_code_finds_the_days_that_carry_it(db, cdd):
    ctx = R.build_context(db, cdd, section_key="WORKSHEET 4: DAY-BY-DAY MAP",
                          instruction="which day covers AM.I.B.K3?",
                          section_content=DAY_TABLE)
    assert "Lines and Symbols" in ctx.text
    assert "Graphs and Charts" not in ctx.text


def test_a_document_with_no_stored_sections_is_reported_ungrounded(db):
    """Degrade, never raise: the caller decides what to do about it."""
    doc = CourseDesignDocument(title="Empty", course_title="Empty", active_version="v1")
    db.add(doc)
    db.commit()
    ctx = R.build_context(db, doc, section_key="anything", instruction="Day 1")
    assert not ctx.is_grounded
    assert ctx.sources == ()


def test_provenance_records_what_the_model_was_shown(db, cdd):
    ctx = R.build_context(db, cdd, section_key="WORKSHEET 4: DAY-BY-DAY MAP",
                          instruction="Rewrite Day 2", section_content=DAY_TABLE)
    prov = ctx.provenance()
    assert prov["grounded"] is True
    assert prov["context_tokens"] > 0
    assert "days=2" in prov["scope"]


# ---------------------------------------------------------------------------
# Recovering sections the stored index lost
# ---------------------------------------------------------------------------

BODY = (
    "## Course Structure\n\n"
    "# Course Design Document — Block 2\n\n"
    "## WORKSHEET 1: BLOCK OVERVIEW\n"
    "- **Block:** Block 2\n- **Total Days:** 3\n\n"
    "## WORKSHEET 4: DAY-BY-DAY MAP\n"
    f"{DAY_TABLE}\n"
)


def test_the_body_is_split_into_sections_by_heading():
    parsed = R.sections_from_full_content(BODY)
    assert "WORKSHEET 1: BLOCK OVERVIEW" in parsed
    assert "WORKSHEET 4: DAY-BY-DAY MAP" in parsed
    assert "Total Days" in parsed["WORKSHEET 1: BLOCK OVERVIEW"]


def test_prose_with_no_headings_yields_nothing():
    assert R.sections_from_full_content("just text, no headings") == {}


def _doc_with(db, *, sections, full_content):
    import json

    doc = CourseDesignDocument(title="Block 2", course_title="Block 2", active_version="v1",
                               course_id=48, project_id=1)
    db.add(doc)
    db.commit()
    db.add(CDDVersion(cdd_id=doc.id, version="v1", full_content=full_content,
                      sections=json.dumps(sections), is_active=True))
    db.commit()
    return doc


def test_keys_missing_from_the_index_are_recovered_from_the_body(db):
    """The live failure. A commit rebuilt CDD 169's index from a fixed list of
    legacy block names and took it from seven keys to one; the next regeneration
    found no Worksheet 1 and rewrote it from the title alone."""
    doc = _doc_with(db, sections={"Course Structure": "blob"}, full_content=BODY)
    loaded = R.load_sections(db, doc)
    assert "WORKSHEET 1: BLOCK OVERVIEW" in loaded
    assert "WORKSHEET 4: DAY-BY-DAY MAP" in loaded


def test_the_body_wins_when_the_index_is_stale(db):
    """Counter-intuitive but load-bearing. A worksheet edit is committed by
    splicing it into the Course Structure blob, so full_content carries the new
    text while the index still holds the pre-edit copy. Preferring the index
    would feed the next regeneration the text the last one just corrected."""
    stale = "- **Course Description:** In this course students will demonstrate..."
    fresh_body = BODY.replace("- **Total Days:** 3",
                              "- **Course Description:** Complete and corrected.")
    doc = _doc_with(db, sections={"WORKSHEET 1: BLOCK OVERVIEW": stale},
                    full_content=fresh_body)
    loaded = R.load_sections(db, doc)
    assert "Complete and corrected." in loaded["WORKSHEET 1: BLOCK OVERVIEW"]
    assert "demonstrate..." not in loaded["WORKSHEET 1: BLOCK OVERVIEW"]


def test_the_index_still_supplies_keys_the_body_does_not_have(db):
    """A direct per-section commit drops the key from full_content but keeps it
    in `sections`; that copy is then the only one there is."""
    doc = _doc_with(db, sections={"WORKSHEET 9: EXTRA": "only in the index"},
                    full_content=BODY)
    loaded = R.load_sections(db, doc)
    assert loaded["WORKSHEET 9: EXTRA"] == "only in the index"
    assert "WORKSHEET 1: BLOCK OVERVIEW" in loaded


def test_an_empty_body_leaves_the_index_untouched(db):
    doc = _doc_with(db, sections={"Course Structure": "blob"}, full_content="")
    assert R.load_sections(db, doc) == {"Course Structure": "blob"}


# ---------------------------------------------------------------------------
# Section lookup
# ---------------------------------------------------------------------------

def test_finds_a_section_by_exact_key():
    assert R.find_section(SECTIONS, "WORKSHEET 4: DAY-BY-DAY MAP") == DAY_TABLE


def test_finds_a_section_ignoring_case_and_padding():
    assert R.find_section(SECTIONS, "  worksheet 4: day-by-day map  ") == DAY_TABLE


def test_a_missing_section_is_empty_not_an_error():
    assert R.find_section(SECTIONS, "WORKSHEET 9") == ""


# ---------------------------------------------------------------------------
# Output size discipline
# ---------------------------------------------------------------------------

def test_refuses_content_larger_than_the_model_can_return():
    """The silent-truncation guard. A 16,384-token ceiling cannot emit a table
    measured at ~18k, and a truncated table is indistinguishable from a whole
    one once it has been committed."""
    huge = "| Day | Topic |\n" * 20000
    with pytest.raises(RegenerationTooLargeError) as exc:
        R.assert_can_emit(huge, model_choice="GPT-5.4", label="Worksheet 4")
    detail = exc.value.detail
    assert detail["tokens"] > detail["max_output_tokens"]
    assert detail["model_choice"] == "GPT-5.4"
    # The message has to carry the numbers: "too large" alone gives the user no
    # way to judge how much smaller a target needs to be.
    assert "Worksheet 4" in str(exc.value)


def test_allows_content_the_model_can_return():
    assert R.assert_can_emit(DAY_TABLE, model_choice="GPT-5.4", label="small") > 0


def test_the_ceiling_is_the_model_s_own_not_a_flat_default():
    """Regeneration used to inherit DEFAULT_MAX_OUTPUT_TOKENS (16384) on every
    model, capping a 64k-output model at a quarter of its range."""
    assert R.output_budget("Claude Sonnet 5 (Bedrock)") > 16384


def test_refusal_is_model_dependent():
    """Same content, different verdict — so the fix is a real ceiling check and
    not a blanket size ban that would block work a capable model can do."""
    text = "word " * 17000
    with pytest.raises(RegenerationTooLargeError):
        R.assert_can_emit(text, model_choice="GPT-5.4", label="x")
    R.assert_can_emit(text, model_choice="Claude Sonnet 5 (Bedrock)", label="x")


# ---------------------------------------------------------------------------
# Prompt contract
# ---------------------------------------------------------------------------

def test_the_grounded_prompt_accepts_exactly_what_the_router_supplies():
    """A missing placeholder here is a KeyError at request time, on a path that
    only runs when someone presses Regenerate."""
    from promptops_app.prompt_templates import CDD_SECTION_REGENERATE_GROUNDED_PROMPT

    rendered = CDD_SECTION_REGENERATE_GROUNDED_PROMPT.format(
        section_title="WORKSHEET 4: DAY-BY-DAY MAP",
        course_title="Block 2",
        custom_instruction="Fill the cluster column",
        context_block="=== CONTEXT ===",
        current_content=DAY_TABLE,
    )
    assert DAY_TABLE in rendered
    assert "=== CONTEXT ===" in rendered


def test_an_item_that_is_the_whole_section_is_not_sent_twice(monkeypatch):
    """When the "item" IS the section — which is what a markdown table parses
    to — the old prompt appended it again as "Section context", doubling a
    ~19k-token payload for no added information."""
    from promptops_app.parsers import blueprint_parser

    captured = {}

    def fake_llm(model, system, user, usage_ctx=None):
        captured["user"] = user
        return "rewritten"

    monkeypatch.setattr(blueprint_parser, "call_llm", fake_llm)
    blueprint_parser.regen_single_item(
        section_title="WORKSHEET 4", section_content=DAY_TABLE, item_index=0,
        item_text=DAY_TABLE, custom_instruction="fix it",
    )
    assert captured["user"].count("Lines and Symbols") == 1
    assert "Section context:" not in captured["user"]


def test_a_genuine_item_still_gets_its_surrounding_section(monkeypatch):
    """The dedup above must not strip context from the normal case."""
    from promptops_app.parsers import blueprint_parser

    captured = {}
    monkeypatch.setattr(blueprint_parser, "call_llm",
                        lambda m, s, u, c=None: captured.setdefault("user", u) and "" or "x")
    blueprint_parser.regen_single_item(
        section_title="WORKSHEET 1", section_content="- **Block:** Block 2\n- **Days:** 3",
        item_index=0, item_text="**Block:** Block 2", custom_instruction="fix",
    )
    assert "Section context:" in captured["user"]


def test_grounding_reaches_the_item_prompt(monkeypatch):
    from promptops_app.parsers import blueprint_parser

    captured = {}
    monkeypatch.setattr(blueprint_parser, "call_llm",
                        lambda m, s, u, c=None: captured.setdefault("user", u) and "" or "x")
    blueprint_parser.regen_single_item(
        section_title="W4", section_content="a", item_index=0, item_text="b",
        custom_instruction="fix", context="=== CONTEXT FROM THIS DOCUMENT ===",
    )
    assert "=== CONTEXT FROM THIS DOCUMENT ===" in captured["user"]


def test_the_original_ungrounded_prompt_is_left_intact():
    """promptops_app/core/shared.py still formats it with its own three
    variables; changing its placeholders would break that caller silently."""
    from promptops_app.prompt_templates import CDD_SECTION_REGENERATE_PROMPT

    CDD_SECTION_REGENERATE_PROMPT.format(
        section_title="x", course_title="y", custom_instruction="z",
    )
