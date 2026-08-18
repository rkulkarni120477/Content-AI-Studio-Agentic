"""Reconcile the user-SELECTED CDD/Blueprint prompt against what this platform can
actually produce, and act on the difference.

Two consumers, two strengths of response:

* the **block-wide digest pipeline** gets a report — it emits its own document
  regardless of the prompt, so the divergence is recorded, never blocked
  (``assess`` / ``assess_selected_prompt`` / ``append_section``);
* the **single-call path** gets a refusal for the subset of findings that make
  generation pointless there, because that path stores the reply AS the document
  (``legacy_blockers`` / ``reject_if_unsatisfiable`` / ``context_was_dropped``).

Why this exists
---------------
The digest pipeline's structure is code-owned: MAP returns a fixed JSON schema,
REDUCE returns fixed keys, and ``block_wide_service``'s renderers emit a fixed
column set. The selected prompt reaches that pipeline only as distilled
*judgment* guidance (``prompt_guidance``), wrapped in a block that states it
"must never add a field not listed above". That contract is right — an
admin-edited prompt must not be able to rename or drop a field the renderer
reads — but it has a blind spot: a prompt can ask for a different worksheet
schema, a different output format, even a downloadable ``.xlsx`` built by a code
interpreter, and the pipeline will run to completion and emit its own document
instead. Nothing tells the requester their prompt was inert.

``reduce_prompts.resolve_reduce_prompt`` already closes exactly this gap for the
REDUCE templates: validate the edit, degrade loudly rather than silently. This
module applies the same discipline to the prompt a user picks in the "Prompt
Template" dropdown — the one they believe is the spec.

Deliberately deterministic — no LLM call
----------------------------------------
The distillation call already reads this text, so folding these findings into it
would cost nothing extra. They are kept separate and rule-based anyway, because
the failure mode that matters here is a FALSE finding: a warning that cries wolf
gets trained away, and then the true one is ignored with it. Every finding below
is reproducible from the prompt text alone, so a reviewer can verify it by
reading, and two runs of one prompt can never disagree.

What it does NOT do
-------------------
* It does not adjudicate. A requested column absent from the emitted set may be
  genuinely unsupported or merely named differently; the report says
  "not emitted under this name" and leaves the judgment to a human.
* It reconciles the DAY table only (plus a count of other requested tables).
  Worksheet 4 is where the whole downstream contract lives, and confining the
  per-name findings there is what keeps them quiet enough to be trusted.
* The reporting half never raises and never changes generation. When it has
  nothing to report, callers append nothing and the rendered document is
  byte-identical to what it was before this module existed.
* Only ``reject_if_unsatisfiable`` refuses, only on the single-call path, and only
  for output-format demands — never for a column set, and never for a missing
  ``{{extra_instructions_block}}`` (see ``legacy_blockers`` for why each is
  excluded). Of this project's 17 live CDD/Blueprint prompts, exactly one trips it.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

log = logging.getLogger(__name__)

#: Cap on any single list written into the provenance row. Paired with a ``*_total``
#: count so a capped list is visibly capped rather than quietly short — the same
#: "report the omission" rule prompt_guidance's windowing follows.
_PROVENANCE_LIST_CAP = 40


# --------------------------------------------------------------------------- #
# Label normalisation
# --------------------------------------------------------------------------- #
def _norm(label: str) -> str:
    """Normalise a column label for comparison.

    Drops backticks/emphasis, parentheticals ("Type (Knowledge/Risk/Skill)"),
    trailing punctuation and case, then collapses whitespace. Parentheticals go
    because they are commentary on a column, not part of its identity — AIM's own
    prompts write the same column both ways.
    """
    text = (label or "").strip()
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"[`*_\"']", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


#: Conservative synonym map, requested-name -> emitted-name, for pairs actually
#: observed across this project's own prompt generations (the docx, prompt 77 and
#: prompt 79 all name the same columns differently). Kept small on purpose: an
#: alias that guesses wrong HIDES a real divergence, which is worse than listing a
#: rename as unmatched and letting a reviewer recognise it. Anything without an
#: alias simply reports as unmatched.
_ALIASES: Dict[str, str] = {
    "acs codes": "acs",
    "acs code": "acs",
    "acs codes today": "acs",
    "topic label": "topic",
    "day title": "topic",
    "derived day objective": "learning objective",
    "proposed learning objective": "learning objective",
    "known misconceptions student difficulties": "misconceptions",
    "known misconceptions": "misconceptions",
    "quick check targeting": "targets for quick check",
    "projects active today": "projects today",
    "projects activities": "projects today",
    "application connection": "how it is applied",
    "storyline candidate": "interactive candidate",
    "storyline template type": "interactive type",
    "potential storyline interactive type": "interactive type",
    "summative exam item cluster": "summative exam item cluster",
    "source files for this day": "source files",
}


def _canonical(label: str) -> str:
    norm = _norm(label)
    return _ALIASES.get(norm, norm)


# --------------------------------------------------------------------------- #
# Output-format demands the pipeline structurally cannot satisfy
# --------------------------------------------------------------------------- #
#: ``(key, pattern, human message, blocking)``.
#:
#: Messages are phrased path-neutrally: they surface both in the digest pipeline's
#: reconciliation section and in the single-call path's refusal, so naming either
#: stage would read as wrong in the other.
#:
#: Patterns are chosen for precision over recall — these phrasings essentially never
#: appear in a prompt that expects to emit markdown, so a hit is close to certain and
#: a miss costs only a quieter report. Verified against this project's real prompts:
#: 77 (markdown worksheets) matches none, 79 (xlsx workbook via code interpreter)
#: matches four.
#:
#: ``blocking`` is the difference between reporting a finding and REFUSING the
#: generation, so it is set only where a single hit is conclusive on its own.
#: ``binary_deliverable`` is not: naming a file extension near a verb is ordinary in a
#: prompt that merely READS one ("generate a summary of the attached .pdf"), and
#: refusing that would be exactly the false positive this module exists to avoid. It
#: is reported, and a prompt that genuinely builds a file always also trips one of the
#: conclusive checks — 79 trips three.
_ARTIFACT_CHECKS: Tuple[Tuple[str, "re.Pattern[str]", str, bool], ...] = (
    (
        "sandbox_path",
        re.compile(r"sandbox:\s*/", re.I),
        "a `sandbox:/` file path — no sandbox filesystem exists here",
        True,
    ),
    (
        "code_execution",
        re.compile(r"\bcode (?:execution|interpreter)\b", re.I),
        "code execution — generation calls are text-only, with no interpreter",
        True,
    ),
    (
        "binary_deliverable",
        # A build verb AND a deliverable noun adjacent to the extension. The noun is
        # what separates "build the .xlsx workbook" from "summarise the attached .pdf
        # handbook" — the verb alone matched both.
        re.compile(
            r"(?:build|create|produce|generate|assemble|save|export)[^.\n]{0,120}"
            r"(?:"
            r"\.(?:xlsx|xls|csv|docx|pptx|pdf)\b[^.\n]{0,25}"
            r"(?:workbook|spreadsheet|deliverable|output)"
            r"|"
            r"(?:workbook|spreadsheet|deliverable|output)[^.\n]{0,25}"
            r"\.(?:xlsx|xls|csv|docx|pptx|pdf)\b"
            r")",
            re.I,
        ),
        "a binary file as the deliverable — generation returns markdown; .xlsx is built "
        "afterwards by the exporter, not by the model",
        False,
    ),
    (
        "withhold_inline",
        re.compile(r"do not (?:paste|include|output|reproduce)[^.\n]{0,60}(?:in|into) the chat", re.I),
        "that the content NOT be returned inline — the returned text IS the document, so "
        "complying would store an empty deliverable",
        True,
    ),
    (
        "reply_is_a_link",
        re.compile(r"respond only with[^.\n]{0,80}(?:download|sandbox|\.xlsx)", re.I),
        "that the reply be only a download link",
        True,
    ),
)


# --------------------------------------------------------------------------- #
# Template variables this platform can supply
# --------------------------------------------------------------------------- #
#: Names the CDD and Blueprint ROUTERS put in their variables dicts
#: (app/api/v1/routers/cdd.py, blueprints.py) but that the prompt registry does not
#: declare. Unioned with the registry's own declarations by ``known_variables()``.
_ROUTER_SUPPLIED_VARIABLES = frozenset({
    "course_title", "extra_instructions_block",
})

#: Templates the block-wide path can resolve a selected prompt through — the two
#: whose declared variables therefore define "written for this platform".
_ASSESSED_TEMPLATES = ("cdd_generation", "blueprint_generation")


def known_variables() -> frozenset:
    """``{{name}}`` placeholders this platform can actually supply.

    Read from the prompt registry's own ``required_vars``/``optional_vars`` for the
    CDD and Blueprint templates rather than restated here, so adding a variable to a
    template does not silently turn it into a finding — a hand-kept copy of this list
    would drift into exactly the false warnings this module is built to avoid. The
    router-supplied names are unioned in because they are passed positionally in the
    routers' variables dicts without a registry declaration.

    A name OUTSIDE this set is the signal worth reporting: the prompt expects an
    injection harness that does not exist here, and ``prompt_builder.render`` leaves
    it in the text verbatim ("always leave unresolved placeholders in place"), so it
    reaches the model as literal ``{{NAME}}`` or, on the digest path, is dropped from
    distillation entirely.
    """
    names = set(_ROUTER_SUPPLIED_VARIABLES)
    try:
        from promptops_app.prompts.prompt_loader import _REGISTRY
        for template in _ASSESSED_TEMPLATES:
            entry = _REGISTRY.get(template) or {}
            names.update(entry.get("required_vars") or ())
            names.update(entry.get("optional_vars") or ())
    except Exception as exc:  # noqa: BLE001 — degrade to the router names only
        log.debug("prompt_capability: registry variables unavailable: %s", exc)
    return frozenset(names)


# --------------------------------------------------------------------------- #
# Markdown table extraction
# --------------------------------------------------------------------------- #
_SEPARATOR_RE = re.compile(r"^\|[\s:\-|]+\|$")

#: Header cells that mark a table which DESCRIBES a schema rather than being one —
#: prompt 79 writes its column contract as "| Col. | Exact header | Population rule |"
#: with the real names backticked in the second cell. Without this, the requested
#: columns would be read as literally {"col", "exact header", "population rule"}.
_SCHEMA_DESCRIPTOR_CELLS = frozenset({
    "col", "col no", "column", "columns", "exact header", "header", "field",
    "field name", "row", "column a label", "column b population rule",
    "population rule", "label",
})


def _split_row(line: str) -> List[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _looks_like_a_header_row(cells: List[str]) -> bool:
    """Whether a bare pipe row is a DECLARED header row rather than prose.

    Needed because the commonest way a prompt states a required schema is a header
    row with no separator under it — "Use this exact header row:" followed by the
    pipes alone. Prompt 77 states all three of its worksheet contracts that way, and
    a markdown-strict reader sees none of them.

    The test is shape, not vocabulary: several short, non-empty, non-sentence cells.
    A prose line that happens to contain pipes fails on cell length or on the
    trailing full stop; a real header row passes.
    """
    if len(cells) < 3 or not all(cells):
        return False
    if any(len(c) > 60 for c in cells):
        return False
    if any(c.endswith((".", "!", "?", ",", ";", ":")) for c in cells):
        return False
    # Labels are terse. Allowing one long-ish cell tolerates "ACS Code(s) (verbatim,
    # comma-separated)" without admitting a sentence split across pipes.
    return sum(1 for c in cells if len(c.split()) > 6) <= 1


def _tables(text: str) -> List[Dict[str, Any]]:
    """Every column-set declaration in *text*, as ``{"header": [...], "rows": [[...]]}``.

    Two shapes are recognised:

    * a real markdown table — a pipe row followed by a separator row, exactly as a
      renderer recognises it, so a pipe inside prose cannot invent one;
    * a bare pipe row that passes ``_looks_like_a_header_row`` — the "use this exact
      header row:" form, which carries no separator and no data rows.

    Lines consumed by a real table are never re-read as bare rows, so a table's data
    rows cannot masquerade as additional header declarations.
    """
    lines = (text or "").splitlines()
    out: List[Dict[str, Any]] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not (line.startswith("|") and line.endswith("|")) or _SEPARATOR_RE.match(line):
            i += 1
            continue
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if _SEPARATOR_RE.match(nxt):
            header = _split_row(line)
            rows: List[List[str]] = []
            j = i + 2
            while j < len(lines):
                row = lines[j].strip()
                if not (row.startswith("|") and row.endswith("|")) or _SEPARATOR_RE.match(row):
                    break
                rows.append(_split_row(row))
                j += 1
            out.append({"header": header, "rows": rows})
            i = j
            continue
        cells = _split_row(line)
        if _looks_like_a_header_row(cells):
            out.append({"header": cells, "rows": []})
        i += 1
    return out


def _names_from_table(table: Dict[str, Any]) -> List[str]:
    """The column names a table REQUESTS.

    For a literal table that is the header row itself. For a schema-descriptor
    table (see ``_SCHEMA_DESCRIPTOR_CELLS``) it is the backticked name in each data
    row, read from the column whose header names it ("Exact header", "Column A
    label", "Field", …) and falling back to the first cell that carries exactly one
    backticked token.
    """
    header = table["header"]
    norm_header = [_norm(h) for h in header]
    if not any(h in _SCHEMA_DESCRIPTOR_CELLS for h in norm_header):
        return [h for h in header if _norm(h)]

    name_col = None
    for idx, h in enumerate(norm_header):
        if h in {"exact header", "header", "field", "field name", "label", "column a label"}:
            name_col = idx
            break

    names: List[str] = []
    for row in table["rows"]:
        cell = None
        if name_col is not None and name_col < len(row):
            cell = row[name_col]
        if not (cell and "`" in cell):
            # Fall back to the first cell holding a single backticked token, which is
            # how these tables mark the literal name regardless of column order.
            cell = next((c for c in row if c.count("`") >= 2), None)
        if not cell:
            continue
        found = re.findall(r"`([^`]+)`", cell)
        if len(found) == 1 and _norm(found[0]):
            names.append(found[0].strip())
    return names


def _pick_day_table(tables: List[Dict[str, Any]], emitted_day: List[str]) -> Tuple[List[str], int]:
    """Return ``(requested day-table column names, count of other tables)``.

    Chooses the candidate that declares a day column, because that is the only
    table whose identity can be established from its own content. When none does,
    nothing is returned and every table counts as "other" — reporting no findings
    beats reporting findings against a table that may not be Worksheet 4.
    """
    emitted = {_canonical(c) for c in emitted_day}
    declared = [names for names in (_names_from_table(t) for t in tables) if names]
    best: List[str] = []
    for names in declared:
        canon = {_canonical(n) for n in names}
        if "day" not in canon:
            continue
        # Among day-bearing tables prefer the widest, and require a second column in
        # common with what is emitted: a table sharing nothing else with the renderer
        # is far more likely to be a different kind of table than a Worksheet 4 with
        # total divergence. "A day column plus one overlap", not "any overlap" — the
        # day column is the entry condition above, so counting it here would make the
        # gate vacuous and let any day-bearing table be scored as Worksheet 4.
        if len(names) > len(best) and len((canon & emitted) - {"day"}) >= 1:
            best = names
    others = sum(1 for names in declared if names is not best)
    return best, others


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class CapabilityReport:
    """What the selected prompt asks for, versus what this pipeline emits."""

    assessed: bool = False
    prompt_chars: int = 0
    requested_day_columns: List[str] = field(default_factory=list)
    matched_columns: List[str] = field(default_factory=list)
    unmatched_columns: List[str] = field(default_factory=list)
    other_requested_tables: int = 0
    unknown_variables: List[str] = field(default_factory=list)
    artifact_demands: List[str] = field(default_factory=list)

    @property
    def has_findings(self) -> bool:
        """True when there is something a reviewer needs to see. Matched-only is not
        a finding: it means the prompt and the pipeline agree."""
        return bool(self.unmatched_columns or self.unknown_variables or self.artifact_demands)

    def _capped(self, items: List[str]) -> Dict[str, Any]:
        return {"items": items[:_PROVENANCE_LIST_CAP], "total": len(items)}

    def to_provenance(self) -> Dict[str, Any]:
        """Compact, JSON-safe record for the version row's prompt_provenance.

        Counts plus (capped) names rather than the prompt text: the text is already
        recoverable from prompt_id/version, and this row is read by reviewers asking
        "was my prompt honoured", not re-parsed.
        """
        if not self.assessed:
            return {"assessed": False}
        return {
            "assessed": True,
            "prompt_chars": self.prompt_chars,
            "has_findings": self.has_findings,
            "requested_day_columns": len(self.requested_day_columns),
            "matched_columns": self._capped(self.matched_columns),
            "unmatched_columns": self._capped(self.unmatched_columns),
            "other_requested_tables": self.other_requested_tables,
            "unknown_variables": self._capped(self.unknown_variables),
            "artifact_demands": list(self.artifact_demands),
        }

    def review_lines(self) -> List[str]:
        """Markdown bullets for the generated document. Empty when there is nothing
        to say, so the caller appends no section at all."""
        if not self.assessed or not self.has_findings:
            return []
        out: List[str] = [
            "_How the prompt selected for this generation compares with what the "
            "block-wide pipeline emits. The pipeline's own schema governs the "
            "worksheets above; these lines record the difference so it is reviewed "
            "rather than assumed._",
            "",
        ]
        if self.artifact_demands:
            out.append(
                "- **⚠ The selected prompt asks for an output this pipeline cannot produce:** "
                + "; ".join(self.artifact_demands)
                + "."
            )
        if self.unmatched_columns:
            out.append(
                f"- **Requests {len(self.unmatched_columns)} day-table column(s) not emitted "
                f"under that name:** {', '.join(self.unmatched_columns)}. "
                "A column may exist above under a different label — this is a naming and "
                "scope difference to confirm, not an error."
            )
        if self.matched_columns:
            shown = self.matched_columns[:12]
            out.append(
                f"- **Matched {len(self.matched_columns)} of "
                f"{len(self.requested_day_columns)} requested column(s):** "
                + ", ".join(shown)
                + (" …" if len(self.matched_columns) > len(shown) else "")
                + "."
            )
        if self.unknown_variables:
            out.append(
                f"- **⚠ {len(self.unknown_variables)} template variable(s) this platform "
                f"never supplies:** {', '.join(self.unknown_variables[:12])}"
                + (" …" if len(self.unknown_variables) > 12 else "")
                + ". These stay in the prompt text verbatim and reach no model."
            )
        if self.other_requested_tables:
            out.append(
                f"- {self.other_requested_tables} further table(s) in the prompt were not "
                "reconciled: only the day-by-day table can be identified from its own "
                "content, and Worksheets 1 and 5 have no static column list to compare against."
            )
        return out


_EMPTY = CapabilityReport()


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def assess(prompt_text: str) -> CapabilityReport:
    """Reconcile *prompt_text* against the pipeline's emitted surface. Pure; the
    unit of behaviour worth testing. Never raises."""
    try:
        text = prompt_text or ""
        if not text.strip():
            return _EMPTY

        from promptops_app.prompts.prompt_builder import extract_variables
        from promptops_app.services.block_wide_service import emitted_columns

        emitted_day = emitted_columns()["day_table"]
        emitted_canon = {_canonical(c) for c in emitted_day}

        requested, others = _pick_day_table(_tables(text), emitted_day)
        matched, unmatched = [], []
        for name in requested:
            (matched if _canonical(name) in emitted_canon else unmatched).append(name)

        allowed = known_variables()
        unknown = [v for v in extract_variables(text) if v not in allowed]
        demands = [msg for _k, pattern, msg, _b in _ARTIFACT_CHECKS if pattern.search(text)]

        return CapabilityReport(
            assessed=True,
            prompt_chars=len(text),
            requested_day_columns=requested,
            matched_columns=matched,
            unmatched_columns=unmatched,
            other_requested_tables=others,
            unknown_variables=unknown,
            artifact_demands=demands,
        )
    except Exception as exc:  # noqa: BLE001 — a reporting layer must never break a build
        log.warning("prompt_capability_assess_failed error=%s", exc)
        return _EMPTY


def assess_selected_prompt(db: Any, request_body: Any, deliverable: str) -> CapabilityReport:
    """Resolve the prompt this request selected and assess it.

    Reuses ``prompt_guidance._resolve_prompt_text`` rather than resolving
    independently, so the text assessed here is byte-for-byte the text distilled
    into MAP/REDUCE guidance — a report about a different resolution than the one
    that ran would be worse than no report. Never raises; degrades to an
    unassessed report, which renders and records nothing.
    """
    try:
        from promptops_app.services.prompt_guidance import _resolve_prompt_text
        text = _resolve_prompt_text(db, request_body, deliverable)
        report = assess(text)
        if report.assessed and report.has_findings:
            log.warning(
                "prompt_capability_findings deliverable=%s prompt_chars=%d "
                "unmatched_columns=%d unknown_variables=%d artifact_demands=%d",
                deliverable, report.prompt_chars, len(report.unmatched_columns),
                len(report.unknown_variables), len(report.artifact_demands),
            )
        return report
    except Exception as exc:  # noqa: BLE001
        log.warning("prompt_capability_resolution_failed deliverable=%s error=%s", deliverable, exc)
        return _EMPTY


def legacy_blockers(system_prompt: str, user_prompt: str) -> List[str]:
    """Reasons the SINGLE-CALL path cannot honour this prompt at all.

    Only the output-format demands qualify, and deliberately so. They are the ones
    where compliance itself is the failure: a prompt told to answer with nothing but
    a ``sandbox:/`` download link, on a path that persists the reply AS the document,
    produces a one-line deliverable with a dead link — which then reads as finished
    work. That is the same judgment ``_reject_if_truncated`` already makes about a cut
    -off reply: a failure the user can see beats content silently lost.

    Column divergence is NOT a blocker: the single-call path emits whatever the prompt
    asks for, so a different column set is simply a different document, not a broken
    one. Nor is a missing ``{{extra_instructions_block}}`` — 7 of this project's 17
    live CDD/Blueprint prompts have no such slot, so refusing them would break working
    flows to prevent a degradation. That case is reported instead, by
    ``context_was_dropped`` below.

    Runs on the RENDERED prompt pair, so it covers an inline override pasting the same
    text as much as a registry template.
    """
    text = f"{system_prompt or ''}\n{user_prompt or ''}"
    return [msg for _k, pattern, msg, blocking in _ARTIFACT_CHECKS
            if blocking and pattern.search(text)]


def reject_if_unsatisfiable(system_prompt: str, user_prompt: str, *, what: str) -> None:
    """Raise before the LLM call when the prompt cannot be honoured on this path.

    Raises ``PromptConfigurationError`` — the existing "the resolved prompt template
    is misconfigured, surface it to the admin who owns it rather than silently
    changing what runs" error, which is exactly this situation. The message names the
    prompt's own demand so the fix is obvious from the response alone.
    """
    blockers = legacy_blockers(system_prompt, user_prompt)
    if not blockers:
        return
    from app.core.exceptions import PromptConfigurationError
    log.error("prompt_unsatisfiable what=%s blockers=%s", what, blockers)
    raise PromptConfigurationError(
        f"The selected prompt cannot be used for {what}: it asks for "
        + "; ".join(blockers)
        + ". This path returns generated text, which is stored as the document — a "
          "prompt that withholds that text, or that needs a file built by a code "
          "interpreter, would save an empty deliverable. Pick a prompt that emits the "
          "document inline, or remove those instructions.",
        detail={"blockers": blockers},
    )


def context_was_dropped(context_block: str, system_prompt: str, user_prompt: str) -> bool:
    """Whether retrieved source context was assembled but never reached the prompt.

    Checked by containment against the FINAL rendered pair rather than by looking for
    an ``{{extra_instructions_block}}`` placeholder, because that is the actual
    question: a prompt with no slot drops the context, and so does a prompt whose slot
    sits in a part that was not rendered. Containment answers both exactly, with no
    heuristic to be wrong about.

    A prompt lacking the slot still runs — see ``legacy_blockers`` on why this reports
    instead of refusing — but it runs source-blind, and a blueprint generated
    source-blind flags nearly every cell MISSING_SOURCE while looking like a real
    attempt. Recording it makes that outcome diagnosable from the row.
    """
    block = (context_block or "").strip()
    if not block:
        return False
    return block not in f"{system_prompt or ''}\n{user_prompt or ''}"


def append_section(markdown: str, report: CapabilityReport | None) -> str:
    """Append the reconciliation section to a rendered deliverable.

    Returns *markdown* unchanged when there is no report or nothing to report, so
    a generation with an aligned prompt produces byte-identical output to before
    this feature existed. The heading is a plain ``##`` section, not a
    ``## WORKSHEET N:`` one, so it joins ``## COVERAGE & REVIEW`` as document
    metadata and is skipped by the worksheet splitter the XLSX export uses.
    """
    if report is None:
        return markdown
    lines = report.review_lines()
    if not lines:
        return markdown
    return markdown + "\n\n## PROMPT RECONCILIATION\n\n" + "\n".join(lines)
