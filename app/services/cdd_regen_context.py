"""Grounding and size discipline for CDD regeneration.

Regeneration used to send a section name, a course title and the user's
instruction — 457 characters standing in for a 77,099-character worksheet — and
then replace that worksheet with whatever came back. It consulted no source:
not OpenSearch, not the day digests, not even the document it was rewriting.

This module supplies the two things that were missing, both of them from the
CDD's own stored version:

**Context.** Worksheets 1-4 already hold the block overview, the source-file
inventory, the ACS registry and the day rows — in the same row of the same
table the endpoint already has open. No network call, no DIS round-trip, no
provenance backfill: the cheapest real grounding available was sitting in the
database the whole time. ``build_context`` selects the slice the instruction
actually refers to rather than pasting all 91k characters back in.

**Size discipline.** An answer to "can the model even emit this?". Worksheet 4
measures ~18,110 tokens against a 16,384-token output ceiling, so per-item
regeneration could not physically return it. The response came back truncated,
``patch_item_in_section`` spliced the truncation in, and the browser committed
it as a new version — trailing days disappeared with no error and a success
toast. ``assert_can_emit`` refuses that call instead, because a truncated
answer is indistinguishable from a complete one once it has been saved.

Scope is deliberately CDD-only (what the AIM UI labels "Blueprint").
``ModuleBlueprint`` is a separate entity and is untouched here.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.core.exceptions import RegenerationTooLargeError
from promptops_app.core.config import count_tokens

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------
# These were per-part caps plus a whole-context cap, applied through `clip_tokens`
# so that a single oversized worksheet could not crowd out the others. They are
# retired as ENFORCED bounds, for two reasons.
#
# First, they never enforced anything: `clip_tokens` is governed by
# PROMPTOPS_TOKEN_LIMIT_ENABLED, which defaults to False and is set nowhere in this
# deployment, so every one of these was a no-op. The context was already going out
# whole; the numbers described an intention, not the behaviour.
#
# Second, that intention is wrong for this context. What they would have trimmed is
# the document's OWN worksheet content — the authoritative material the prompt
# explicitly tells the model not to contradict. Cutting a scoped day-row table
# mid-row hands the model a malformed table it cannot tell is malformed, and cutting
# the ACS registry drops codes the regeneration is meant to reconcile against.
# Crowding-out is a SELECTION problem, and this module already solves it by
# selection: `parse_scope` narrows to the days, codes and columns the instruction
# actually names, so only relevant parts are assembled in the first place.
#
# Kept as names because the sizing knowledge in them is real and still used for
# routing decisions (see SMALL_WORKSHEET_TOKENS and the scoped-regen threshold);
# deleting them would only mean re-measuring the same worksheets later.
BLOCK_OVERVIEW_TOKENS = 800
DAY_ROWS_TOKENS = 6000
DAY_INDEX_TOKENS = 1200
ACS_ROWS_TOKENS = 2500
SOURCE_INVENTORY_TOKENS = 1200
PATTERNS_TOKENS = 1200
TOTAL_CONTEXT_TOKENS = 12000

#: Below this, a whole worksheet is small enough that scoping it adds nothing —
#: the prose worksheets (1, 2, 5) all sit here, so they are passed intact.
SMALL_WORKSHEET_TOKENS = 1500


# ---------------------------------------------------------------------------
# Instruction parsing
# ---------------------------------------------------------------------------
# Deliberately deterministic — regex, not a model. Routing that decides which
# rows get rewritten has to be explainable when it gets it wrong, reproducible
# in a test, and free. An LLM router would be none of the three.

_DAY_RE = re.compile(r"\bdays?\s*#?\s*(\d{1,3})\b", re.IGNORECASE)
_DAY_RANGE_RE = re.compile(r"\bdays?\s*#?\s*(\d{1,3})\s*(?:-|–|—|to|through)\s*(\d{1,3})\b",
                           re.IGNORECASE)
#: FAA ACS codes as they appear in the registry, e.g. AM.I.B.K1 / PA.I.A.K12.
_ACS_RE = re.compile(r"\b[A-Z]{2}\.[IVXLC]+\.[A-Z]\.[A-Z]\d{1,3}\b")

#: Never treated as a column reference: "Day" is handled by day parsing above,
#: and "ACS" by code parsing — matching them as columns too would tag almost
#: every instruction with a column scope it did not ask for.
_COLUMN_STOPWORDS = {"day", "acs"}

#: A day-range wider than this is treated as "no day scope" rather than an
#: enumeration. "Days 1-20" means the whole block, and expanding it to twenty
#: individual targets would defeat the scoping this exists to provide.
MAX_RANGE_SPAN = 12


@dataclass(frozen=True)
class RegenScope:
    """What the instruction says to touch, as parsed from its text."""

    day_numbers: tuple[int, ...] = ()
    acs_codes: tuple[str, ...] = ()
    columns: tuple[str, ...] = ()

    @property
    def is_targeted(self) -> bool:
        """True when the instruction named something specific to change."""
        return bool(self.day_numbers or self.acs_codes or self.columns)

    def describe(self) -> str:
        """One line for logs and audit metadata."""
        parts = []
        if self.day_numbers:
            parts.append("days=" + ",".join(str(d) for d in self.day_numbers))
        if self.acs_codes:
            parts.append("acs=" + ",".join(self.acs_codes))
        if self.columns:
            parts.append("cols=" + "|".join(self.columns))
        return " ".join(parts) or "untargeted"


def parse_scope(instruction: str, *, known_columns: Sequence[str] = ()) -> RegenScope:
    """Extract day numbers, ACS codes and column names from *instruction*.

    Ranges ("Days 4-6") expand; wide ranges do not (see MAX_RANGE_SPAN).
    Column matching is word-boundary anchored against the columns actually
    present in the target worksheet, so an instruction can only name a column
    that exists — there is no vocabulary to keep in sync.
    """
    text = instruction or ""
    days: set[int] = set()

    for lo, hi in _DAY_RANGE_RE.findall(text):
        start, end = int(lo), int(hi)
        if start > end:
            start, end = end, start
        if end - start < MAX_RANGE_SPAN:
            days.update(range(start, end + 1))

    # Ranges are consumed above, wide ones deliberately yielding nothing, so
    # they must not be re-read as single days: "Review Days 1-20" would
    # otherwise scope to Day 1 alone and hand the model one day's rows for an
    # instruction that asked about the whole block — worse than no scope, since
    # the caller cannot see that the other 19 days were dropped.
    remaining = _DAY_RANGE_RE.sub(" ", text)
    days.update(int(d) for d in _DAY_RE.findall(remaining))

    codes = {c.upper() for c in _ACS_RE.findall(text.upper())}

    lowered = text.lower()
    columns: list[str] = []
    for col in known_columns:
        name = (col or "").strip()
        if not name or name.lower() in _COLUMN_STOPWORDS:
            continue
        if re.search(rf"\b{re.escape(name.lower())}\b", lowered):
            columns.append(name)

    return RegenScope(
        day_numbers=tuple(sorted(days)),
        acs_codes=tuple(sorted(codes)),
        columns=tuple(columns),
    )


# ---------------------------------------------------------------------------
# Markdown table helpers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MarkdownTable:
    """A parsed pipe table: the header/separator lines kept verbatim so any
    subset can be re-rendered as a valid table without reformatting cells.

    ``start_line``/``end_line`` locate the table within the text it came from,
    inclusive, so a caller can splice a rewritten table back without disturbing
    prose above or below it (see cdd_scoped_regen.merge_rows). -1 when absent.
    """

    header: str = ""
    separator: str = ""
    rows: tuple[str, ...] = ()
    columns: tuple[str, ...] = ()
    start_line: int = -1
    end_line: int = -1

    @property
    def is_table(self) -> bool:
        return bool(self.header and self.rows)

    def render(self, rows: Iterable[str]) -> str:
        rows = list(rows)
        if not rows:
            return ""
        return "\n".join([self.header, self.separator, *rows])

    def column_index(self, name: str) -> int:
        """Position of *name*, matched case-insensitively; -1 when absent."""
        wanted = (name or "").strip().lower()
        for i, col in enumerate(self.columns):
            if col.strip().lower() == wanted:
                return i
        return -1


def cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_markdown_table(text: str) -> MarkdownTable:
    """Parse the first pipe table in *text*. Returns an empty table if absent.

    Only a CONTIGUOUS run of pipe lines counts. Collecting every pipe line in
    the document would merge two separate tables into one and produce a splice
    span covering the prose between them.
    """
    all_lines = (text or "").split("\n")
    start = next((i for i, ln in enumerate(all_lines) if ln.strip().startswith("|")), -1)
    if start < 0:
        return MarkdownTable()

    end = start
    while end + 1 < len(all_lines) and all_lines[end + 1].strip().startswith("|"):
        end += 1

    block = all_lines[start:end + 1]
    if len(block) < 2:
        return MarkdownTable()
    header, separator, *rows = block
    # A separator row is all dashes/colons; without one this is not a table.
    if not re.match(r"^\|[\s:\-|]+\|?$", separator.strip()):
        return MarkdownTable()
    return MarkdownTable(
        header=header, separator=separator, rows=tuple(rows),
        columns=tuple(cells(header)), start_line=start, end_line=end,
    )


_ROW_DAY_RE = re.compile(r"day\s*#?\s*(\d{1,3})", re.IGNORECASE)


def row_day_number(row: str) -> int | None:
    """The day a table row belongs to, read from its first cell."""
    parts = cells(row)
    if not parts:
        return None
    match = _ROW_DAY_RE.search(parts[0]) or re.match(r"^\s*(\d{1,3})\s*$", parts[0])
    return int(match.group(1)) if match else None


def _rows_for_days(table: MarkdownTable, days: Sequence[int]) -> list[str]:
    wanted = set(days)
    return [r for r in table.rows if row_day_number(r) in wanted]


def _rows_for_codes(table: MarkdownTable, codes: Sequence[str]) -> list[str]:
    """Registry rows whose first cell is one of *codes*."""
    wanted = {c.upper() for c in codes}
    out = []
    for row in table.rows:
        parts = cells(row)
        if parts and parts[0].upper() in wanted:
            out.append(row)
    return out


def _codes_in_rows(table: MarkdownTable, rows: Sequence[str]) -> list[str]:
    """Every ACS code appearing anywhere in *rows* — used to pull the registry
    entries for the days in scope without the caller naming codes itself."""
    found: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for code in _ACS_RE.findall(row.upper()):
            if code not in seen:
                seen.add(code)
                found.append(code)
    return found


def _day_index(table: MarkdownTable) -> str:
    """A compact "Day N — topic" list for the whole block.

    Cross-day awareness without the 77k table: enough for the model to place a
    day in its sequence, cheap enough to always include.
    """
    lines = []
    for row in table.rows:
        parts = cells(row)
        if len(parts) < 2:
            continue
        lines.append(f"{parts[0]} — {parts[1]}")
    return "\n".join(lines) if lines else ""


# ---------------------------------------------------------------------------
# Worksheet lookup
# ---------------------------------------------------------------------------

#: Matched case-insensitively as substrings, so "WORKSHEET 4: DAY-BY-DAY MAP"
#: is found by "DAY-BY-DAY". Keys are stable across CDDs; the numeric prefix is
#: not always present, which is why the needle is the name and not the number.
_WORKSHEETS = {
    "overview": "BLOCK OVERVIEW",
    "sources": "SOURCE FILE INVENTORY",
    "acs": "ACS CODE REGISTRY",
    "days": "DAY-BY-DAY",
    "patterns": "PATTERNS",
    "coverage": "COVERAGE",
}


def _find_worksheet(sections: dict, needle: str) -> tuple[str, str]:
    """Return (section_key, content) for the first section whose key contains
    *needle*, else ("", "")."""
    needle = needle.upper()
    for key, value in (sections or {}).items():
        if needle in str(key).upper():
            return str(key), str(value or "")
    return "", ""


def find_section(sections: dict, section_key: str) -> str:
    """Content of *section_key*, matched exactly first then case-insensitively.

    The UI sends the section key it rendered, which for DLU worksheets is a
    title rather than the stored key — so an exact hit is the common case and
    the loose match is the fallback, never the other way round.
    """
    if not sections or not section_key:
        return ""
    if section_key in sections:
        return str(sections[section_key] or "")
    wanted = section_key.strip().upper()
    for key, value in sections.items():
        if str(key).strip().upper() == wanted:
            return str(value or "")
    return ""


_HEADING_RE = re.compile(r"^\s{0,3}#{1,4}\s+(.+?)\s*$")


def sections_from_full_content(full_content: str) -> dict:
    """Rebuild a sections dict by splitting *full_content* on its headings.

    A recovery path, not the primary one. The stored ``sections`` index can lose
    keys that ``full_content`` still has: the frontend rebuilds the index from a
    fixed list of legacy block names, so committing an edit to a block-wide CDD
    drops every worksheet key that is not on that list. Observed live — a commit
    took CDD 169's index from seven keys to one while its full_content kept all
    91,702 characters.

    That bug is fixed at source, but this stays, because the document body is the
    real artifact and an index derived from it should never be the reason a
    regeneration loses its grounding.
    """
    sections: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []
    for line in (full_content or "").split("\n"):
        match = _HEADING_RE.match(line)
        if match:
            if current is not None:
                sections[current] = "\n".join(buffer).strip()
            current, buffer = match.group(1).strip(), []
            continue
        if current is not None:
            buffer.append(line)
    if current is not None:
        sections[current] = "\n".join(buffer).strip()
    return {k: v for k, v in sections.items() if v}


def load_sections(db, cdd) -> dict:
    """The active version's sections, or {} when unavailable.

    The stored index is preferred, then merged with whatever the document body
    itself yields: the two disagree in practice, and when they do the body is
    right. A commit whose index lost six of seven worksheet keys must not leave
    a regeneration blind to sections the document plainly still contains.

    Never raises. A document whose sections are missing or malformed degrades to
    less context, never to a failed regeneration.
    """
    try:
        from promptops_app.core.llm_client import safe_json_loads
        from promptops_app.database import get_active_cdd_version

        version = get_active_cdd_version(db, cdd.id)
        if version is None:
            return {}

        stored = safe_json_loads(version.sections) if version.sections else {}
        stored = stored if isinstance(stored, dict) else {}
        recovered = sections_from_full_content(version.full_content or "")
        if not recovered:
            return stored

        # The BODY wins on conflict, which is the opposite of what it looks like
        # it should be. A block-wide CDD commits a worksheet edit by splicing it
        # into the 'Course Structure' blob, so full_content carries the new text
        # while the index still holds the per-worksheet copy from before the edit.
        # Verified on CDD 169: after a successful correction, full_content had the
        # completed course description and the index did not. Preferring the index
        # would feed the next regeneration the very text the last one just fixed.
        #
        # Keys the body does not yield fall back to the index — that covers a
        # direct per-section commit, where rebuildCddFullContent drops the key
        # from full_content but `sections` holds it correctly.
        merged = dict(stored)
        merged.update({k: v for k, v in recovered.items() if v})
        recovered_only = set(recovered) - set(stored)
        if recovered_only:
            _log.info("cdd_sections_recovered_from_body cdd_id=%s recovered=%s",
                      getattr(cdd, "id", "?"), sorted(recovered_only))
        return merged
    except Exception:  # noqa: BLE001 — context is best-effort by design
        _log.warning("cdd_regen_context_sections_unreadable cdd_id=%s",
                     getattr(cdd, "id", "?"), exc_info=True)
        return {}


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RegenContext:
    """Grounding for one regeneration call."""

    text: str = ""
    scope: RegenScope = field(default_factory=RegenScope)
    sources: tuple[str, ...] = ()

    @property
    def is_grounded(self) -> bool:
        return bool(self.text.strip())

    def provenance(self) -> dict[str, Any]:
        """Recorded on the audit row so a reviewer can see what the model was
        shown, not just what it produced."""
        return {
            "grounded": self.is_grounded,
            "context_tokens": count_tokens(self.text),
            "context_sources": list(self.sources),
            "scope": self.scope.describe(),
        }


def build_context(
    db,
    cdd,
    *,
    section_key: str,
    instruction: str = "",
    section_content: str = "",
) -> RegenContext:
    """Assemble grounding for regenerating *section_key* of *cdd*.

    Selection is driven by the instruction (see :func:`parse_scope`):

    * names a day  → that day's rows, plus the registry entries for its codes
    * names a code → the rows and registry entries for that code
    * otherwise    → the block overview and a compact day index

    The block overview is always included; it is small and it is what places
    any single day in the block. Everything is token-capped per part and then
    once more as a whole.
    """
    sections = load_sections(db, cdd)
    if not sections:
        return RegenContext(scope=parse_scope(instruction))

    _, overview = _find_worksheet(sections, _WORKSHEETS["overview"])
    _, day_md = _find_worksheet(sections, _WORKSHEETS["days"])
    _, acs_md = _find_worksheet(sections, _WORKSHEETS["acs"])
    _, sources_md = _find_worksheet(sections, _WORKSHEETS["sources"])

    day_table = parse_markdown_table(day_md)
    acs_table = parse_markdown_table(acs_md)

    # The target's own columns drive column matching, so the instruction can
    # only name a column the worksheet actually has.
    target_table = parse_markdown_table(section_content) if section_content else MarkdownTable()
    known_columns = target_table.columns or day_table.columns
    scope = parse_scope(instruction, known_columns=known_columns)

    parts: list[str] = []
    used: list[str] = []

    if overview.strip():
        parts.append("## BLOCK OVERVIEW\n" + overview.strip())
        used.append("block_overview")

    scoped_rows: list[str] = []
    if day_table.is_table:
        if scope.day_numbers:
            scoped_rows = _rows_for_days(day_table, scope.day_numbers)
        elif scope.acs_codes:
            scoped_rows = [r for r in day_table.rows
                           if any(c in r.upper() for c in scope.acs_codes)]

        if scoped_rows:
            rendered = day_table.render(scoped_rows)
            parts.append("## DAY ROWS IN SCOPE\n" + rendered)
            used.append("day_rows")
        else:
            index = _day_index(day_table)
            if index:
                parts.append("## DAY INDEX (whole block)\n" + index)
                used.append("day_index")

    if acs_table.is_table:
        codes = list(scope.acs_codes) or _codes_in_rows(acs_table, scoped_rows)
        acs_rows = _rows_for_codes(acs_table, codes) if codes else []
        if acs_rows:
            parts.append("## ACS CODES IN SCOPE\n" + acs_table.render(acs_rows))
            used.append("acs_registry")

    if sources_md.strip() and _mentions_sources(instruction):
        parts.append("## SOURCE FILE INVENTORY\n" + sources_md.strip())
        used.append("source_inventory")

    if not parts:
        return RegenContext(scope=scope)

    body = "\n\n".join(parts)
    text = (
        "=== CONTEXT FROM THIS DOCUMENT (authoritative — do not contradict) ===\n"
        f"{body}\n"
        "=== END CONTEXT ==="
    )
    return RegenContext(text=text, scope=scope, sources=tuple(used))


_SOURCE_WORDS = re.compile(
    r"\b(source|sources|file|files|handbook|document|documents|reference|references|pdf)\b",
    re.IGNORECASE,
)


def _mentions_sources(instruction: str) -> bool:
    """Whether to spend budget on the source inventory.

    Not included by default: it is the least often relevant of the worksheets,
    and the tokens are better spent on day rows.
    """
    return bool(_SOURCE_WORDS.search(instruction or ""))


# ---------------------------------------------------------------------------
# Output size discipline
# ---------------------------------------------------------------------------

def output_budget(model_choice: str) -> int:
    """The chosen model's output ceiling.

    Regeneration previously passed no ``max_tokens`` and inherited
    DEFAULT_MAX_OUTPUT_TOKENS (16384) even on models that support 64000 —
    capping a capable model at a quarter of its range for no reason.
    """
    from promptops_app.core.models import resolve_model
    return resolve_model(model_choice).max_output_tokens


def assert_can_emit(text: str, *, model_choice: str, label: str) -> int:
    """Refuse a regeneration whose target is larger than the model can return.

    Returns the measured token count when the call is safe.

    This is the guard against the silent-truncation path: the model emits as
    much as it can, the caller splices it back, and the commit succeeds with
    content missing. There is no partial-credit reading of that outcome, so the
    only correct response is to decline before spending anything.
    """
    needed = count_tokens(text)
    cap = output_budget(model_choice)
    if needed > cap:
        raise RegenerationTooLargeError(
            f"{label} is too large to regenerate in one call: it measures "
            f"{needed:,} tokens and {model_choice} can return at most {cap:,}. "
            f"Regenerating it would truncate the content. Target a smaller part "
            f"of it, or choose a model with a larger output limit.",
            tokens=needed,
            max_output_tokens=cap,
            model_choice=model_choice,
            detail={"label": label},
        )
    return needed
