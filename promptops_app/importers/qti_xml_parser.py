"""QTI 1.2 XML → normalised questions for the IMSCC importer.

This is the inverse of ``promptops_app/exporters/qti_exporter.py`` and the
NET-NEW reader called out in Ground-truth §Exporter gap #2. The existing
``qti_parser.parse_assessment_questions`` parses *source markdown/HTML*, NOT the
exported QTI XML — so we cannot reuse it here.

The reader is namespace-agnostic (Canvas emits the QTI namespace; other tools
vary), matching on bare local element names. It reads:
  * ``item`` ``ident`` / ``title`` attributes;
  * ``itemmetadata/qtimetadata`` → ``question_type`` (mc / true-false / essay);
  * ``presentation/material/mattext`` → stem;
  * ``response_lid/render_choice/response_label`` → choices;
  * ``resprocessing/…/varequal`` → correct choice ident;
  * ``itemfeedback/…/mattext`` → explanation.

``questions_to_markdown`` re-emits the **same normalised markdown shape** the
forward ``qti_parser`` produces (``### Question N`` / ``**Stem:**`` /
``**Answer Options:**`` / ``**Correct Answer:**`` / ``**Explanation:**``), so a
re-export round-trips and the Editor renders imported quizzes identically to
scratch ones.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from promptops_app.importers.html_to_markdown import _fragment_to_markdown

_CHOICE_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# Canvas question_type values we map explicitly; anything else falls back to the
# structural heuristics (has choices → MC, else essay).
_TRUE_FALSE = "true_false_question"
_MULTIPLE_CHOICE = "multiple_choice_question"
_ESSAY = "essay_question"


@dataclass
class QTIQuestion:
    """One question read from QTI XML (mirrors qti_parser.ParsedQuestion)."""
    ident: str
    title: str
    qtype: str
    stem: str
    choices: list = field(default_factory=list)   # list[tuple[ident, text]]
    correct: str = ""                              # winning choice ident
    feedback: str = ""


# ── namespace-agnostic XML helpers (mirror canvas_parser) ─────────────────────

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find_local(elem: ET.Element, name: str) -> ET.Element | None:
    for child in elem:
        if _local(child.tag) == name:
            return child
    return None


def _iter_local(elem: ET.Element, name: str):
    for child in elem:
        if _local(child.tag) == name:
            yield child


def _findall_deep(elem: ET.Element, name: str):
    for descendant in elem.iter():
        if descendant is elem:
            continue
        if _local(descendant.tag) == name:
            yield descendant


def _mattext_markdown(material: ET.Element | None) -> str:
    """Collapse a ``material`` element's ``mattext`` HTML into clean markdown."""
    if material is None:
        return ""
    parts: list[str] = []
    for mattext in _findall_deep(material, "mattext"):
        parts.append("".join(mattext.itertext()))
    return _fragment_to_markdown(" ".join(p for p in parts if p).strip())


# ── parsing ───────────────────────────────────────────────────────────────────

def parse_qti_xml(xml_str: str) -> list[QTIQuestion]:
    """Parse QTI 1.2 assessment XML into a list of :class:`QTIQuestion`.

    Returns ``[]`` for empty/questionless XML. Raises ``ET.ParseError`` only on
    malformed XML — callers wrap this per-item so one bad quiz becomes a warning.
    """
    if not (xml_str or "").strip():
        return []
    root = ET.fromstring(xml_str)

    questions: list[QTIQuestion] = []
    for item in _findall_deep(root, "item"):
        # Only real question items carry a <presentation>; skip section wrappers.
        if _find_local(item, "presentation") is None:
            continue
        question = _parse_item(item)
        if question is not None:
            questions.append(question)
    return questions


def _parse_item(item: ET.Element) -> QTIQuestion | None:
    ident = item.get("ident", "") or f"question_{id(item)}"
    title = item.get("title", "") or ident

    presentation = _find_local(item, "presentation")
    if presentation is None:
        return None

    stem = _mattext_markdown(_find_local(presentation, "material"))
    choices = _parse_choices(presentation)
    correct = _parse_correct(item)
    feedback = _parse_feedback(item)
    qtype = _resolve_qtype(item, choices)

    if qtype == _TRUE_FALSE and not correct:
        correct = "true"

    return QTIQuestion(
        ident=ident,
        title=title,
        qtype=qtype,
        stem=stem,
        choices=choices,
        correct=correct,
        feedback=feedback,
    )


def _parse_choices(presentation: ET.Element) -> list[tuple[str, str]]:
    choices: list[tuple[str, str]] = []
    for label in _findall_deep(presentation, "response_label"):
        cid = label.get("ident", "")
        text = _mattext_markdown(_find_local(label, "material"))
        if cid or text:
            choices.append((cid, text))
    return choices


def _parse_correct(item: ET.Element) -> str:
    resprocessing = _find_local(item, "resprocessing")
    if resprocessing is None:
        return ""
    for varequal in _findall_deep(resprocessing, "varequal"):
        value = (varequal.text or "").strip()
        if value:
            return value
    return ""


def _parse_feedback(item: ET.Element) -> str:
    for feedback in _iter_local(item, "itemfeedback"):
        text = _mattext_markdown(_find_local(feedback, "flow_mat") or _find_local(feedback, "material") or feedback)
        if text:
            return text
    return ""


def _resolve_qtype(item: ET.Element, choices: list[tuple[str, str]]) -> str:
    declared = _declared_question_type(item)
    if declared in (_TRUE_FALSE, _MULTIPLE_CHOICE, _ESSAY):
        return declared
    if choices:
        idents = {cid.lower() for cid, _ in choices}
        if idents == {"true", "false"}:
            return _TRUE_FALSE
        return _MULTIPLE_CHOICE
    return _ESSAY


def _declared_question_type(item: ET.Element) -> str:
    metadata = _find_local(item, "itemmetadata")
    if metadata is None:
        return ""
    for field_elem in _findall_deep(metadata, "qtimetadatafield"):
        label = _find_local(field_elem, "fieldlabel")
        if label is not None and (label.text or "").strip() == "question_type":
            entry = _find_local(field_elem, "fieldentry")
            return (entry.text or "").strip() if entry is not None else ""
    return ""


# ── normalised markdown emission (round-trips through qti_parser) ──────────────

def questions_to_markdown(title: str, questions: list[QTIQuestion]) -> str:
    """Render questions as the normalised assessment markdown used by scratch.

    Output re-parses cleanly through ``qti_parser.parse_assessment_questions``,
    so imported quizzes render and re-export exactly like scratch-generated ones.
    """
    blocks = [_question_to_markdown(i, q) for i, q in enumerate(questions, start=1)]
    return "\n\n---\n\n".join(b for b in blocks if b).strip()


def _question_to_markdown(index: int, q: QTIQuestion) -> str:
    lines = [f"### Question {index}", "", "**Stem:**", q.stem or "", ""]

    if q.qtype == _MULTIPLE_CHOICE and q.choices:
        lines.append("**Answer Options:**")
        correct_letter = ""
        for i, (cid, text) in enumerate(q.choices):
            letter = _CHOICE_LETTERS[i] if i < len(_CHOICE_LETTERS) else str(i + 1)
            lines.append(f"{letter}) {text}")
            if cid and cid == q.correct:
                correct_letter = letter
        lines.append("")
        if correct_letter:
            lines.append(f"**Correct Answer:** {correct_letter}")
            lines.append("")
    elif q.qtype == _TRUE_FALSE:
        correct = (q.correct or "true").strip().lower()
        if correct not in ("true", "false"):
            correct = "true"
        lines.append(f"**Correct Answer:** {correct}")
        lines.append("")

    if q.feedback:
        lines.append("**Explanation:**")
        lines.append(q.feedback)
        lines.append("")

    return "\n".join(lines).rstrip()
