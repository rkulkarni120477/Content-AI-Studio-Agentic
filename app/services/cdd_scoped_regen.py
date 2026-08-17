"""Row-scoped regeneration for CDD day tables.

Grounding the prompt fixed *what the model is told*. This fixes *what its
answer is allowed to touch* — which is where the damage actually happened.

Regenerating Worksheet 4 meant replacing all 77,099 characters of a 20-day, 31
column table with one model response. Even a well-grounded response is the
wrong shape for that: the model must reproduce nineteen rows it was never asked
to change, and every one of them is a chance to drop a row, reword a source
filename, or quietly renumber a day. The output ceiling made it worse — the
table measures ~18,110 tokens against GPT-5.4's 16,384, so the reproduction
could not even finish.

The fix is to stop asking. When the instruction names days, only those rows are
sent and only those rows come back; every other row is carried across byte for
byte, never re-emitted. Two consequences follow:

* **Cost and fit collapse.** One row instead of twenty. Worksheet 4 becomes
  editable on a 16k-output model, because the model is no longer being asked to
  restate the parts nobody touched.
* **The blast radius is bounded by construction**, not by the model's
  cooperation. The row set comes from the original table; a day the model omits
  keeps its old row, and a day it invents is discarded.

Within a targeted row, ``STRUCTURAL_COLUMNS`` are restored after the model
returns. Day numbers, ACS codes, source filenames and handbook citations are
joins against the calendar and the registry — regeneration may describe them,
never author them.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from app.core.exceptions import LLMGenerationError, ProtectedFieldError
from app.services.cdd_regen_context import (
    MarkdownTable,
    RegenScope,
    cells,
    parse_markdown_table,
    row_day_number,
)

_log = logging.getLogger(__name__)


#: Columns regeneration may never write, matched case-insensitively.
#:
#: Every one is a join result rather than a judgement: the day number comes from
#: the calendar, the ACS codes from the registry join, the filenames and
#: handbook references from what was actually ingested. A model asked to "fix"
#: any of them can only guess, and a guessed ACS code is indistinguishable from
#: a real one to every reader downstream.
STRUCTURAL_COLUMNS = frozenset({
    "day",
    "acs",
    "handbook reference",
    "handbook edition",
    "source files",
    "projects today",
    "assessment today",
    "hangar activity",
})


@dataclass(frozen=True)
class RowPlan:
    """A regeneration narrowed to specific rows of a specific table."""

    table: MarkdownTable
    section_content: str
    day_numbers: tuple[int, ...]
    target_rows: tuple[str, ...]
    writable: tuple[str, ...]
    protected: tuple[str, ...]

    @property
    def target_markdown(self) -> str:
        """Just the rows in scope, as a standalone table the model can answer
        in kind — header included so the columns are named."""
        return self.table.render(self.target_rows)

    def describe(self) -> str:
        return (f"rows={len(self.target_rows)}/{len(self.table.rows)} "
                f"days={','.join(str(d) for d in self.day_numbers)} "
                f"writable={len(self.writable)}")


@dataclass(frozen=True)
class MergeResult:
    """Outcome of splicing regenerated rows back into their section."""

    section_content: str
    changed_days: tuple[int, ...] = ()
    unchanged_days: tuple[int, ...] = ()
    changed_cells: int = 0
    protected_hits: tuple[str, ...] = ()

    def describe(self) -> str:
        return (f"changed_days={','.join(str(d) for d in self.changed_days) or 'none'} "
                f"cells={self.changed_cells} "
                f"kept={','.join(str(d) for d in self.unchanged_days) or 'none'}")


def _writable_columns(table: MarkdownTable, scope: RegenScope) -> tuple[tuple[str, ...],
                                                                       tuple[str, ...]]:
    """Split the table's columns into (writable, protected).

    A named column narrows the writable set to itself; naming none leaves every
    non-structural column open, which is what an instruction like "tighten the
    wording for Day 4" needs.
    """
    protected = tuple(c for c in table.columns if c.strip().lower() in STRUCTURAL_COLUMNS)

    if scope.columns:
        blocked = [c for c in scope.columns if c.strip().lower() in STRUCTURAL_COLUMNS]
        if blocked:
            raise ProtectedFieldError(
                f"Regeneration cannot rewrite {', '.join(blocked)}. "
                "Those values come from the course calendar and the ACS registry, "
                "not from the model — a regenerated ACS code or source filename "
                "would look correct and be unverifiable. Fix them at the source "
                "and rebuild, or edit the cell directly with Save Edit.",
                fields=blocked,
            )
        writable = tuple(c for c in scope.columns if c in table.columns)
    else:
        writable = tuple(c for c in table.columns if c.strip().lower() not in STRUCTURAL_COLUMNS)

    return writable, protected


def plan_rows(section_content: str, scope: RegenScope) -> RowPlan | None:
    """Plan a row-scoped regeneration, or None when one does not apply.

    Returns None — meaning "fall back to whole-section handling" — when the
    section is not a table, has no day column, or the instruction named no day
    that the table actually contains. A day the table does not have is not an
    error: it simply leaves nothing to scope to.
    """
    table = parse_markdown_table(section_content)
    if not table.is_table or not scope.day_numbers:
        return None
    if table.column_index("Day") < 0:
        return None

    wanted = set(scope.day_numbers)
    target_rows = tuple(r for r in table.rows if row_day_number(r) in wanted)
    if not target_rows:
        return None

    writable, protected = _writable_columns(table, scope)
    if not writable:
        raise ProtectedFieldError(
            "Every column named is protected, so there is nothing this "
            "regeneration is allowed to change.",
            fields=list(protected),
        )

    present = tuple(sorted({d for r in target_rows if (d := row_day_number(r)) is not None}))
    return RowPlan(
        table=table, section_content=section_content, day_numbers=present,
        target_rows=target_rows, writable=writable, protected=protected,
    )


def unfilled_cells(plan: RowPlan) -> tuple[tuple[int, str], ...]:
    """(day, column) pairs among the plan's writable cells that hold no content.

    This is what separates a request to REVISE from a request to FILL. Revising
    needs only what the worksheet already says; filling needs the source behind
    it, because a cell the pipeline never populated has nothing in the worksheet
    to read. Detecting the difference from the document — rather than from how
    the instruction was phrased — means an instruction like "improve Day 4" that
    happens to land on an empty cell still reaches for source.
    """
    from app.services.cdd_deep_context import is_placeholder

    gaps: list[tuple[int, str]] = []
    writable_lower = {c.strip().lower() for c in plan.writable}
    for row in plan.target_rows:
        day = row_day_number(row)
        if day is None:
            continue
        values = cells(row)
        for i, column in enumerate(plan.table.columns):
            if column.strip().lower() not in writable_lower or i >= len(values):
                continue
            if is_placeholder(values[i]):
                gaps.append((day, column))
    return tuple(gaps)


def _merge_cells(original: Sequence[str], replacement: Sequence[str],
                 columns: Sequence[str], writable: Sequence[str]) -> tuple[list[str], int]:
    """Overlay *replacement* onto *original* for writable columns only.

    Cell count always follows the original: a model that returns fewer or more
    columns cannot reshape the row, it can only fill the ones that already
    exist. An empty replacement cell is treated as "no opinion" and keeps the
    original — otherwise a model that skipped a field would blank it.
    """
    writable_lower = {c.strip().lower() for c in writable}
    merged = list(original)
    changed = 0
    for i, column in enumerate(columns):
        if i >= len(merged) or column.strip().lower() not in writable_lower:
            continue
        if i >= len(replacement):
            continue
        new_value = (replacement[i] or "").strip()
        if new_value and new_value != merged[i]:
            merged[i] = new_value
            changed += 1
    return merged, changed


def merge_rows(plan: RowPlan, regenerated_markdown: str) -> MergeResult:
    """Splice the model's rows back into the section.

    The original table drives the result: rows are emitted in their original
    order, a targeted day the model omitted keeps its previous row, and a day
    the model invented is discarded. That is what makes this safe to run
    unattended — the merge cannot change the shape of the table regardless of
    what came back.
    """
    returned = parse_markdown_table(regenerated_markdown or "")
    if not returned.is_table:
        # Nothing mergeable came back. Refusing beats writing the prose into the
        # section, which would destroy the table it was meant to edit.
        raise LLMGenerationError(
            "Regeneration did not return a table, so the rows could not be "
            "merged. The section is unchanged. Please try again."
        )

    by_day: dict[int, list[str]] = {}
    for row in returned.rows:
        day = row_day_number(row)
        if day is not None and day in set(plan.day_numbers):
            by_day[day] = cells(row)

    # Day matching is the primary key. When the model renumbers a day — which
    # it is told not to do — matching fails and the edit would be dropped
    # wholesale, losing legitimate work over a formatting slip. If it returned
    # exactly the rows it was given, pair by position instead: the day cell is
    # protected and restored from the original either way, so a wrong number
    # cannot survive the merge, and the writable cells still land.
    positional: list[list[str]] = (
        [cells(r) for r in returned.rows]
        if len(returned.rows) == len(plan.target_rows) else []
    )

    targeted = set(plan.day_numbers)
    new_rows: list[str] = []
    changed_days: list[int] = []
    unchanged_days: list[int] = []
    changed_cells = 0
    target_index = 0

    for row in plan.table.rows:
        day = row_day_number(row)
        if day is None or day not in targeted:
            new_rows.append(row)                     # untouched, byte for byte
            continue
        replacement = by_day.get(day)
        if replacement is None and target_index < len(positional):
            replacement = positional[target_index]
        target_index += 1
        if replacement is None:
            unchanged_days.append(day)               # model skipped it — keep ours
            new_rows.append(row)
            continue
        merged, n = _merge_cells(cells(row), replacement, plan.table.columns, plan.writable)
        if n:
            changed_days.append(day)
            changed_cells += n
        else:
            unchanged_days.append(day)
        new_rows.append("| " + " | ".join(merged) + " |")

    rendered = plan.table.render(new_rows)
    lines = plan.section_content.split("\n")
    spliced = lines[:plan.table.start_line] + rendered.split("\n") + lines[plan.table.end_line + 1:]

    return MergeResult(
        section_content="\n".join(spliced),
        changed_days=tuple(changed_days),
        unchanged_days=tuple(sorted(unchanged_days)),
        changed_cells=changed_cells,
        protected_hits=plan.protected,
    )
