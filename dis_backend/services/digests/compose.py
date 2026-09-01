"""LLM composition of worksheet cells, grounded in supplied context.

Every worksheet outside the Day-by-Day Map was assembled by regular expressions
over text that had already been flattened twice — once by whatever extracted the
spreadsheet, once by the JSONB ``::text`` cast — and the delivered workbooks show
what that costs: a Block Overview reporting "no handbook citations found" beside
a Day-by-Day Map that cites a handbook on 18 of 20 days, a Source File Inventory
naming nine administrative files and none of the material the block is taught
from, an ACS registry whose task descriptions are all "NOT AVAILABLE". A pattern
that does not match returns nothing and says nothing, and nothing is what
shipped.

So the cells are composed by a model reading the actual sources instead. What
this module refuses to give up is the discipline that made the regex approach
worth defending in the first place: **a composed cell is not accepted because a
model produced it.** Each field declares how it must be grounded, and a value
that fails its grounding check is discarded in favour of the mechanical value or
an explicit placeholder — never rendered. The model can therefore add coverage
and readability; it cannot add facts.

Three grounding modes:

``VERBATIM``
    The value must appear in the context, whitespace-insensitively. For quoted
    material — a syllabus's grading policy, a course description.

``GROUNDED``
    Every *hard token* in the value — number, ACS code, handbook designator,
    filename, project/quiz label, percentage — must appear in the context.
    Ordinary prose is the model's own. This is the working mode for most cells:
    it permits real composition and rejects invention, because invention in this
    domain is always the invention of an identifier or a figure.

``DERIVED``
    Interpretive prose with no checkable tokens (a rationale, a recommendation).
    Checked only for non-emptiness and for placeholder junk. Used sparingly, and
    a field that CAN carry a token should never be declared this way.

A reflection pass then re-reads the composed sheet against the same context and
reports what is missing, unsupported, or internally inconsistent. Its findings
are returned as notes for the caller to surface — it never silently rewrites,
because a second unverified opinion is not evidence.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)

VERBATIM = "verbatim"
GROUNDED = "grounded"
DERIVED = "derived"

#: Values a model reaches for when it has nothing to say and has been asked for a
#: string anyway. Accepting one renders a cell that looks answered and is not, so
#: they are treated as an empty answer and the field falls back.
_PLACEHOLDERS = {
    "", "n/a", "na", "none", "no", "null", "nil", "tbd", "unknown", "not applicable",
    "not available", "not specified", "not stated", "-", "—", "...", "[]", "{}",
}

#: A token that must be traceable to the context. Deliberately narrow — it exists
#: to catch a fabricated IDENTIFIER or FIGURE, which is the only kind of
#: hallucination this domain actually produces:
#:   * ACS codes            AM.II.E.K12
#:   * handbook designators  FAA-H-8083-31B, AC 43.13-1B, 8083-31B
#:   * page/paragraph refs   13-42, 9-21
#:   * project/quiz labels   Project 9-1, Project 4 A52, Quiz #8
#:   * filenames             Block 9 strategy.docx
#:   * bare numbers and percentages
_HARD_TOKEN_RES = (
    re.compile(r"\b[A-Z]{2}\.[IVX]+\.[A-Z]\.[KRS]\d+\b"),
    re.compile(r"\b(?:FAA-H-)?\d{4}-\d+[A-Z]?\b", re.IGNORECASE),
    re.compile(r"\bAC\s?43\.13-\d+[A-Z]?\b", re.IGNORECASE),
    re.compile(r"\b(?:Project|Quiz)\s*#?\s*[A-Z]?\d+(?:-\d+)?(?:\s+[A-Z]\d+)?\b",
               re.IGNORECASE),
    re.compile(r"\b[\w][\w \-'&()]{0,60}\.(?:pdf|docx?|pptx?|xlsx?|csv|txt)\b",
               re.IGNORECASE),
    re.compile(r"\b\d{1,3}(?:\.\d+)?%"),
    re.compile(r"\b\d[\d,]*(?:\.\d+)?\b"),
)

#: Numbers a model may legitimately write without the context stating them: the
#: ordinals of a list it was given, and the small counts implied by that list.
#: Excluding these entirely would reject "Days 1-8 advance through Chapter 13" —
#: a true sentence about supplied facts — for the crime of containing "1".
#: Handled instead by checking numbers against the context's own number set,
#: which contains every day number and count the model was shown.
_WORD_RE = re.compile(r"[A-Za-z0-9]+")


@dataclass(frozen=True)
class FieldSpec:
    """One composed cell: what it must contain and how it must be grounded."""

    name: str
    instruction: str
    grounding: str = GROUNDED
    #: Rendered when composition produces nothing acceptable AND the caller has
    #: no mechanical value. Says what happened, in the reader's terms.
    fallback: str = ""
    #: A list-valued cell is returned as a list and each item grounded separately,
    #: so one bad item costs one item rather than the whole cell.
    is_list: bool = False


@dataclass
class ComposeResult:
    """Composed values, plus everything that was rejected on the way."""

    values: Dict[str, Any] = field(default_factory=dict)
    #: field name -> why the composed value was not used. Never silent: a cell
    #: that fell back to its mechanical value looks identical to one that was
    #: never composed, and the difference is the whole diagnostic.
    rejected: Dict[str, str] = field(default_factory=dict)
    #: The reflection pass's findings, verbatim, for the caller to surface.
    review_notes: List[str] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    calls: int = 0

    def note_lines(self, worksheet: str) -> List[str]:
        """Flag-shaped lines for the block's review-flag list."""
        out = [f"COMPOSE_REJECTED — {worksheet}.{name}: {why}"
               for name, why in sorted(self.rejected.items())]
        out += [f"COMPOSE_REVIEW — {worksheet}: {n}" for n in self.review_notes]
        return out


def normalize(text: str) -> str:
    """Whitespace- and case-insensitive form, for substring containment."""
    return " ".join(str(text or "").split()).lower()


def hard_tokens(text: str) -> List[str]:
    """The identifiers and figures in *text* that must be traceable to a source."""
    out: List[str] = []
    seen: set[str] = set()
    for pattern in _HARD_TOKEN_RES:
        for m in pattern.findall(str(text or "")):
            token = " ".join(str(m).split())
            key = token.lower()
            if token and key not in seen:
                seen.add(key)
                out.append(token)
    return out


def _token_present(token: str, haystack: str) -> bool:
    """Whether *token* is traceable to *haystack*.

    Compared on the normalised form, and for the code-shaped tokens also on the
    form with separators removed — the calendars write ``AC43.13-1B`` and
    ``AC 43.13-1B`` for the same document, and a citation is not fabricated
    because a space moved.
    """
    needle = normalize(token)
    if needle and needle in haystack:
        return True
    squashed = re.sub(r"[\s\-.#]", "", needle)
    return bool(squashed) and squashed in re.sub(r"[\s\-.#]", "", haystack)


def verify(value: str, context: str, grounding: str) -> Tuple[str, str]:
    """``(accepted_value, rejection_reason)`` — exactly one of the two is set."""
    text = " ".join(str(value or "").split())
    if normalize(text) in _PLACEHOLDERS:
        return "", "empty or placeholder answer"

    if grounding == DERIVED:
        return text, ""

    haystack = normalize(context)
    if grounding == VERBATIM:
        if normalize(text) in haystack:
            return text, ""
        return "", "not verbatim in the supplied sources"

    unsupported = [t for t in hard_tokens(text) if not _token_present(t, haystack)]
    if unsupported:
        return "", ("names identifiers or figures absent from the supplied "
                    f"sources: {', '.join(unsupported[:6])}")
    return text, ""


def _schema_block(fields: Sequence[FieldSpec]) -> str:
    parts = []
    for f in fields:
        shape = "[str, ...]" if f.is_list else "str"
        parts.append(f'  "{f.name}": {shape},   // {f.instruction}')
    return "{\n" + "\n".join(parts) + "\n}"


def _rules_block(fields: Sequence[FieldSpec]) -> str:
    modes: Dict[str, List[str]] = {}
    for f in fields:
        modes.setdefault(f.grounding, []).append(f.name)
    lines = [
        "GROUNDING RULES — these are checked after you answer, and a value that "
        "fails its rule is DISCARDED, not corrected:",
    ]
    if modes.get(VERBATIM):
        lines.append(
            f"- {', '.join(modes[VERBATIM])}: copy the wording EXACTLY from the "
            "sources. Do not paraphrase, reorder, summarise or fix it.")
    if modes.get(GROUNDED):
        lines.append(
            f"- {', '.join(modes[GROUNDED])}: write your own prose, but every "
            "number, percentage, ACS code, handbook designator, page reference, "
            "project or quiz label and filename you use MUST appear in the "
            "sources below. Never estimate a figure or complete a partial code.")
    if modes.get(DERIVED):
        lines.append(
            f"- {', '.join(modes[DERIVED])}: your own judgement, stated plainly "
            "and tied to what the sources show.")
    lines.append(
        "- A field the sources do not support is an empty string \"\" (or []). "
        "An empty answer is correct and expected; a plausible guess is not. "
        "Never write \"N/A\", \"none\", \"TBD\" or similar as if it were content.")
    return "\n".join(lines)


def build_prompt(purpose: str, fields: Sequence[FieldSpec],
                 context_blocks: Sequence[Tuple[str, str]],
                 mechanical: Optional[Dict[str, Any]] = None) -> str:
    """The composition prompt. ``context_blocks`` are ``(label, text)`` pairs."""
    parts = [
        purpose.strip(),
        "",
        _rules_block(fields),
        "",
        "Respond with ONLY this JSON object — no preamble, no markdown fence:",
        _schema_block(fields),
        "",
    ]
    if mechanical:
        parts += [
            "ALREADY ESTABLISHED (computed from the records, not by you — treat as "
            "true, use it, and never contradict it):",
            "\n".join(f"  {k}: {v}" for k, v in mechanical.items() if v not in (None, "", [])),
            "",
        ]
    for label, text in context_blocks:
        body = str(text or "").strip()
        if not body:
            continue
        parts += [f"--- {label} ---", body, ""]
    return "\n".join(parts)


def compose(purpose: str, fields: Sequence[FieldSpec],
            context_blocks: Sequence[Tuple[str, str]],
            call_llm: Callable[..., Any], safe_json: Callable[[str], Any],
            model: str, *, mechanical: Optional[Dict[str, Any]] = None,
            max_tokens: int = 4000, reflect: bool = True,
            worksheet: str = "") -> ComposeResult:
    """Compose *fields* from *context_blocks*, verifying every value.

    Never raises. A provider failure, an unparseable reply or a wholly rejected
    answer all produce an empty ``values`` and a recorded reason, so the caller
    falls back to whatever it computed mechanically — composition is additive by
    construction and cannot make a worksheet worse than not calling it.
    """
    result = ComposeResult()
    context = "\n\n".join(f"{label}\n{text}" for label, text in context_blocks if text)
    if not context.strip():
        result.rejected["*"] = "no source context was available to compose from"
        return result

    prompt = build_prompt(purpose, fields, context_blocks, mechanical)
    try:
        reply, ti, to = call_llm(model, prompt, max_tokens)
        result.calls += 1
        result.tokens_in += int(ti or 0)
        result.tokens_out += int(to or 0)
        data = safe_json(reply) or {}
    except Exception as exc:  # noqa: BLE001 — additive; never sink the worksheet
        log.warning("compose(%s) failed: %s", worksheet or purpose[:40], exc)
        result.rejected["*"] = f"composition call failed: {exc}"
        return result
    if not isinstance(data, dict):
        result.rejected["*"] = "composition reply was not a JSON object"
        return result

    for spec in fields:
        raw = data.get(spec.name)
        if spec.is_list:
            items = raw if isinstance(raw, list) else ([raw] if raw else [])
            kept, reasons = [], []
            for item in items:
                value, why = verify(str(item), context, spec.grounding)
                if value:
                    kept.append(value)
                elif why != "empty or placeholder answer":
                    reasons.append(f"{str(item)[:60]!r}: {why}")
            if kept:
                result.values[spec.name] = kept
            if reasons:
                result.rejected[spec.name] = "; ".join(reasons[:3])
            continue
        value, why = verify(str(raw or ""), context, spec.grounding)
        if value:
            result.values[spec.name] = value
        elif str(raw or "").strip():
            result.rejected[spec.name] = why

    if reflect:
        _reflect(result, purpose, fields, context, call_llm, safe_json, model,
                 worksheet)
    return result


_REFLECT_FIELDS = (
    FieldSpec("missing", "Anything the sources clearly support that the draft "
                         "leaves out or renders empty — one short sentence each.",
              DERIVED, is_list=True),
    FieldSpec("unsupported", "Anything in the draft the sources do NOT support — "
                             "quote the phrase and say why.", DERIVED, is_list=True),
    FieldSpec("inconsistent", "Anything in the draft that contradicts another part "
                              "of the draft or the established facts.",
              DERIVED, is_list=True),
)


def _reflect(result: ComposeResult, purpose: str, fields: Sequence[FieldSpec],
             context: str, call_llm, safe_json, model: str, worksheet: str) -> None:
    """Second read of the composed sheet against the same context.

    Reports; never rewrites. A model that just wrote a cell is not a witness to
    whether the cell is true, and letting its second opinion overwrite its first
    would launder an unverified value into the workbook. The findings go to the
    reviewer, who can act on them.
    """
    if not result.values:
        return
    draft = "\n".join(f"{k}: {v}" for k, v in sorted(result.values.items()))
    prompt = build_prompt(
        "You are reviewing a draft worksheet section against the sources it was "
        "written from. Report only what you can point at in the sources. If the "
        "draft is faithful and complete, return empty lists — that is the "
        f"expected answer for good work.\n\nSECTION PURPOSE: {purpose.strip()}",
        _REFLECT_FIELDS,
        [("DRAFT", draft), ("SOURCES", context)],
    )
    try:
        reply, ti, to = call_llm(model, prompt, 1500)
        result.calls += 1
        result.tokens_in += int(ti or 0)
        result.tokens_out += int(to or 0)
        data = safe_json(reply) or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("reflect(%s) failed: %s", worksheet, exc)
        return
    if not isinstance(data, dict):
        return
    for key in ("missing", "unsupported", "inconsistent"):
        for item in (data.get(key) or []):
            text = " ".join(str(item).split())
            if text and normalize(text) not in _PLACEHOLDERS:
                result.review_notes.append(f"{key}: {text}")
