"""Row-scoped regeneration must not be able to damage the table it edits.

Grounding the prompt changed what the model is told. This changes what its
answer is allowed to touch, and the properties below are the point of it: the
row set comes from the original table, so a model that drops a row, renumbers a
day, invents an ACS code or returns an extra day cannot express any of that in
the result. The merge is bounded by construction rather than by the model's
cooperation, which is what makes it safe to run on a 20-day worksheet.
"""

import pytest

from app.core.exceptions import LLMGenerationError, ProtectedFieldError
from app.services import cdd_scoped_regen as S
from app.services.cdd_regen_context import (
    cells,
    parse_markdown_table,
    parse_scope,
    row_day_number,
)

# Columns chosen to include one of each kind: identity (Day), a registry join
# (ACS), source provenance (Source Files) and two writable narrative cells.
TABLE = (
    "| Day | Topic | ACS | Source Files | Learning Objective | Notes |\n"
    "|---|---|---|---|---|---|\n"
    "| Day 1 | Drawings | AM.I.B.K1 | b1.pdf | Read a title block | — |\n"
    "| Day 2 | Symbols | AM.I.B.K3 | b2.pdf | Identify line types | — |\n"
    "| Day 3 | Charts | — | b3.pdf | Interpret a chart | — |\n"
)

SECTION_WITH_PROSE = f"Some preamble.\n\n{TABLE}\nA closing note.\n"

COLUMNS = ["Day", "Topic", "ACS", "Source Files", "Learning Objective", "Notes"]


def _scope(instruction):
    return parse_scope(instruction, known_columns=COLUMNS)


def _plan(instruction, content=TABLE):
    return S.plan_rows(content, _scope(instruction))


def _reply(plan, mutate=None, extra=()):
    """A model response for the plan's rows, optionally corrupted."""
    rows = []
    for row in plan.target_rows:
        c = list(cells(row))
        if mutate:
            mutate(c, plan.table)
        rows.append("| " + " | ".join(c) + " |")
    return plan.table.render(rows + list(extra))


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

def test_prose_sections_have_no_row_plan():
    """Falls back to whole-section handling rather than inventing a table."""
    assert S.plan_rows("- **Block:** Block 2", _scope("Rewrite Day 1")) is None


def test_an_untargeted_instruction_has_no_row_plan():
    assert _plan("make it more concise") is None


def test_a_day_the_table_does_not_have_is_not_a_plan():
    """Not an error — there is simply nothing to scope to."""
    assert _plan("Rewrite Day 44") is None


def test_a_plan_targets_only_the_named_rows():
    plan = _plan("Rewrite Day 2")
    assert plan.day_numbers == (2,)
    assert len(plan.target_rows) == 1
    assert "Symbols" in plan.target_markdown
    assert "Drawings" not in plan.target_markdown


def test_naming_a_column_narrows_what_may_change():
    plan = _plan("Fix the Learning Objective for Day 2")
    assert plan.writable == ("Learning Objective",)


def test_naming_no_column_leaves_every_non_structural_column_writable():
    plan = _plan("Tighten the wording for Day 2")
    assert set(plan.writable) == {"Topic", "Learning Objective", "Notes"}


def test_structural_columns_are_never_writable():
    plan = _plan("Tighten the wording for Day 2")
    for protected in ("Day", "ACS", "Source Files"):
        assert protected not in plan.writable
        assert protected in plan.protected


def test_asking_to_rewrite_a_protected_column_is_refused_not_ignored():
    """Silently protecting the field would report success for an edit that
    never happened — the same silent failure as a truncated table."""
    with pytest.raises(ProtectedFieldError) as exc:
        _plan("Fix the Source Files for Day 2")
    assert "Source Files" in exc.value.detail["fields"]


# ---------------------------------------------------------------------------
# Merge — the safety properties
# ---------------------------------------------------------------------------

def test_untargeted_rows_come_through_byte_identical():
    plan = _plan("Rewrite Day 2")
    merged = S.merge_rows(plan, _reply(plan, lambda c, t: c.__setitem__(4, "New objective")))
    before, after = parse_markdown_table(TABLE), parse_markdown_table(merged.section_content)
    for b, a in zip(before.rows, after.rows, strict=True):
        if row_day_number(b) != 2:
            assert a == b


def test_the_targeted_cell_is_actually_updated():
    plan = _plan("Fix the Learning Objective for Day 2")
    merged = S.merge_rows(plan, _reply(plan, lambda c, t: c.__setitem__(4, "New objective")))
    row = [r for r in parse_markdown_table(merged.section_content).rows
           if row_day_number(r) == 2][0]
    assert cells(row)[4] == "New objective"
    assert merged.changed_cells == 1
    assert merged.changed_days == (2,)


def test_a_model_invented_row_is_discarded():
    plan = _plan("Rewrite Day 2")
    merged = S.merge_rows(plan, _reply(plan, extra=["| Day 9 | HIJACK | x | x | x | x |"]))
    after = parse_markdown_table(merged.section_content)
    assert len(after.rows) == 3
    assert "HIJACK" not in merged.section_content


def test_a_renumbered_day_cannot_survive_the_merge():
    """The model is told not to renumber. If it does, the day cell is restored
    from the original — a wrong number must never reach the document."""
    plan = _plan("Fix the Learning Objective for Day 2")
    merged = S.merge_rows(plan, _reply(plan, lambda c, t: (c.__setitem__(0, "Day 99"),
                                                           c.__setitem__(4, "New objective"))))
    days = [row_day_number(r) for r in parse_markdown_table(merged.section_content).rows]
    assert days == [1, 2, 3]
    assert 99 not in days


def test_a_renumbered_row_still_delivers_its_legitimate_edit():
    """Fail-closed on identity, not on the whole edit: the day cell is
    protected either way, so a formatting slip should not silently discard
    work the user asked for."""
    plan = _plan("Fix the Learning Objective for Day 2")
    merged = S.merge_rows(plan, _reply(plan, lambda c, t: (c.__setitem__(0, "Day 99"),
                                                           c.__setitem__(4, "New objective"))))
    row = [r for r in parse_markdown_table(merged.section_content).rows
           if row_day_number(r) == 2][0]
    assert cells(row)[4] == "New objective"


def test_an_invented_acs_code_is_restored_to_the_original():
    """The worst failure this pipeline can produce: plausible, unverifiable by
    eye, and it propagates into everything built from the CDD."""
    plan = _plan("Tighten the wording for Day 2")
    merged = S.merge_rows(plan, _reply(plan, lambda c, t: c.__setitem__(2, "AM.I.Z.K9")))
    row = [r for r in parse_markdown_table(merged.section_content).rows
           if row_day_number(r) == 2][0]
    assert cells(row)[2] == "AM.I.B.K3"
    assert "AM.I.Z.K9" not in merged.section_content


def test_a_day_the_model_omitted_keeps_its_original_row():
    plan = _plan("Rewrite Day 1 and Day 2")
    only_one = plan.table.render([plan.target_rows[0]])
    merged = S.merge_rows(plan, only_one)
    assert len(parse_markdown_table(merged.section_content).rows) == 3
    assert 2 in merged.unchanged_days


def test_an_empty_cell_from_the_model_keeps_the_original():
    """A model that skipped a field must not blank it."""
    plan = _plan("Tighten the wording for Day 2")
    merged = S.merge_rows(plan, _reply(plan, lambda c, t: c.__setitem__(4, "")))
    row = [r for r in parse_markdown_table(merged.section_content).rows
           if row_day_number(r) == 2][0]
    assert cells(row)[4] == "Identify line types"


def test_a_short_row_cannot_reshape_the_table():
    plan = _plan("Rewrite Day 2")
    merged = S.merge_rows(plan, plan.table.render(["| Day 2 | Symbols |"]))
    after = parse_markdown_table(merged.section_content)
    for row in after.rows:
        assert len(cells(row)) == len(COLUMNS)


def test_a_non_table_response_is_refused_rather_than_written_in():
    """Writing prose into the section would destroy the table it was meant to
    edit — exactly the outcome this module exists to prevent."""
    plan = _plan("Rewrite Day 2")
    with pytest.raises(LLMGenerationError):
        S.merge_rows(plan, "Sorry, I cannot help with that.")


def test_prose_around_the_table_is_preserved():
    plan = _plan("Rewrite Day 2", content=SECTION_WITH_PROSE)
    merged = S.merge_rows(plan, _reply(plan, lambda c, t: c.__setitem__(4, "New objective")))
    assert merged.section_content.startswith("Some preamble.")
    assert "A closing note." in merged.section_content


def test_the_whole_table_is_never_sent_for_a_one_day_edit():
    """The reason this path exists: the model is not asked to restate rows
    nobody touched, which is what put the old call over its output ceiling."""
    plan = _plan("Rewrite Day 2")
    assert len(plan.target_markdown) < len(TABLE)
    # One data row (the header names the columns and does not count).
    assert len(parse_markdown_table(plan.target_markdown).rows) == 1


# ---------------------------------------------------------------------------
# Prompt contract
# ---------------------------------------------------------------------------

def test_the_row_prompt_accepts_exactly_what_the_router_supplies():
    from promptops_app.prompt_templates import CDD_ROW_REGENERATE_PROMPT

    plan = _plan("Rewrite Day 2")
    rendered = CDD_ROW_REGENERATE_PROMPT.format(
        section_title="WORKSHEET 4: DAY-BY-DAY MAP",
        course_title="Block 2",
        custom_instruction="Rewrite Day 2",
        context_block="=== CONTEXT ===",
        writable_columns=", ".join(plan.writable),
        protected_columns=", ".join(plan.protected),
        current_rows=plan.target_markdown,
    )
    assert "Day 2" in rendered
    assert "ACS" in rendered
