"""Parse assessment/quiz block content into structured question objects."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape

ASSESSMENT_BLOCK_TYPES = frozenset({
    "quiz", "assessment", "test", "exam", "module_assessment",
})

_OPTION_LINE = re.compile(r"^([A-Da-d])\)\s+(.+)$", re.MULTILINE)
_QUESTION_HEADER = re.compile(r"^#{1,3}\s*Question\s+(\d+)", re.MULTILINE | re.IGNORECASE)
_SIMPLE_Q = re.compile(
    r"^(?:Q\s*(\d+)[\.:\)]\s*|Question\s+(\d+)[\.:\)]\s*)(.+)$",
    re.MULTILINE | re.IGNORECASE,
)


@dataclass
class ParsedQuestion:
    """One question extracted from an assessment block."""

    ident: str
    title: str
    qtype: str  # multiple_choice_question | true_false_question | essay_question
    stem: str
    choices: list[tuple[str, str]] = field(default_factory=list)  # (id, text)
    correct: str = ""
    feedback: str = ""


def is_assessment_block(block_type: str, label: str) -> bool:
    """Return True when a block should export as QTI rather than HTML."""
    bt = (block_type or "").lower().replace(" ", "_")
    if bt in ASSESSMENT_BLOCK_TYPES:
        return True
    if any(k in bt for k in ("assessment", "quiz", "exam")):
        return True
    lbl = (label or "").lower()
    return any(k in lbl for k in ("assessment", "quiz", "module_assessment", "knowledge check"))


def normalize_assessment_text(content: str) -> str:
    """Convert HTML assessments to plain text/markdown for parsing."""
    text = content or ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>\s*", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</h[1-6]>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<li[^>]*>", "\n- ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_assessment_questions(content: str) -> list[ParsedQuestion]:
    """Extract questions from markdown or HTML assessment content."""
    text = normalize_assessment_text(content)
    if not text:
        return []

    questions: list[ParsedQuestion] = []

    # Structured format: ### Question N ... **Stem:** ... **Answer Options:**
    parts = re.split(r"(?=^#{1,3}\s*Question\s+\d+)", text, flags=re.MULTILINE | re.IGNORECASE)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        header = _QUESTION_HEADER.match(part)
        if not header:
            continue
        q_num = header.group(1)
        q = _parse_structured_question(q_num, part)
        if q:
            questions.append(q)

    if questions:
        return questions

    # Fallback: scan for Q1. / multiple-choice clusters
    return _parse_loose_questions(text)


def _parse_structured_question(q_num: str, section: str) -> ParsedQuestion | None:
    stem = _extract_field(section, "Stem")
    if not stem:
        return None

    options_block = _extract_field(section, "Answer Options")
    correct_raw = _extract_field(section, "Correct Answer", single_line=True)
    explanation = _extract_field(section, "Explanation")

    choices = _parse_options(options_block)
    correct = _normalize_correct(correct_raw, choices)

    if choices:
        qtype = "multiple_choice_question"
    elif correct_raw and correct_raw.strip().lower() in {"true", "false"}:
        qtype = "true_false_question"
        choices = [("true", "True"), ("false", "False")]
        correct = correct_raw.strip().lower()
    else:
        qtype = "essay_question"

    return ParsedQuestion(
        ident=f"question_{q_num}",
        title=f"Question {q_num}",
        qtype=qtype,
        stem=stem.strip(),
        choices=choices,
        correct=correct,
        feedback=explanation.strip(),
    )


def _extract_field(section: str, name: str, single_line: bool = False) -> str:
    if single_line:
        m = re.search(rf"\*\*{re.escape(name)}:\*\*\s*(.+)$", section, re.MULTILINE | re.IGNORECASE)
        return m.group(1).strip() if m else ""
    m = re.search(
        rf"\*\*{re.escape(name)}:\*\*\s*\n(.*?)(?=\n\*\*[A-Za-z]|\n---|\Z)",
        section,
        re.DOTALL | re.IGNORECASE,
    )
    return m.group(1).strip() if m else ""


def _parse_options(options_block: str) -> list[tuple[str, str]]:
    if not options_block:
        return []
    choices: list[tuple[str, str]] = []
    for m in _OPTION_LINE.finditer(options_block):
        ident = m.group(1).lower()
        choices.append((ident, m.group(2).strip()))
    return choices


def _normalize_correct(correct_raw: str, choices: list[tuple[str, str]]) -> str:
    if not correct_raw:
        return choices[0][0] if choices else ""
    letter = correct_raw.strip()[0].lower()
    valid = {c[0] for c in choices}
    if letter in valid:
        return letter
    low = correct_raw.strip().lower()
    if low in {"true", "false"}:
        return low
    return letter


def _parse_loose_questions(text: str) -> list[ParsedQuestion]:
    """Parse simpler Q1. / A) B) C) D) patterns."""
    questions: list[ParsedQuestion] = []
    chunks = re.split(r"\n(?=Q\s*\d+[\.:\)]|\nQuestion\s+\d+)", text, flags=re.IGNORECASE)
    q_num = 0
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        m = _SIMPLE_Q.match(chunk)
        if not m:
            continue
        q_num += 1
        stem = m.group(3).strip()
        rest = chunk[m.end():]
        choices = _parse_options(rest)
        correct_m = re.search(r"\*\*Correct Answer:\*\*\s*([A-Da-d]|True|False)", rest, re.I)
        correct = _normalize_correct(correct_m.group(1) if correct_m else "", choices)

        if choices:
            qtype = "multiple_choice_question"
        elif correct in {"true", "false"}:
            qtype = "true_false_question"
            choices = [("true", "True"), ("false", "False")]
        else:
            qtype = "essay_question"

        questions.append(ParsedQuestion(
            ident=f"question_{q_num}",
            title=f"Question {q_num}",
            qtype=qtype,
            stem=stem,
            choices=choices,
            correct=correct,
            feedback="",
        ))
    return questions
