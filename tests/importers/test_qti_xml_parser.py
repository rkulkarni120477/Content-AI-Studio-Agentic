"""Tests for the QTI-1.2-XML reader + normalised-markdown emitter (Session 2).

The strongest check is a **round-trip**: exporter QTI → parse_qti_xml →
questions_to_markdown → the forward qti_parser must recover the same questions.
That proves the importer emits the exact normalised shape scratch produces.
"""

from __future__ import annotations

from promptops_app.exporters.qti_parser import parse_assessment_questions
from promptops_app.importers.qti_xml_parser import (
    parse_qti_xml,
    questions_to_markdown,
)
from tests.importers.fixtures import sample_qti_xml


def test_parses_all_question_types():
    questions = parse_qti_xml(sample_qti_xml())
    assert [q.qtype for q in questions] == [
        "multiple_choice_question",
        "true_false_question",
        "essay_question",
    ]


def test_multiple_choice_stem_choices_and_correct():
    q = parse_qti_xml(sample_qti_xml())[0]
    assert q.stem == "What is 2 + 2?"
    assert [text for _cid, text in q.choices] == ["3", "4", "5", "6"]
    assert q.correct == "b"
    assert q.feedback == "Basic arithmetic."


def test_empty_xml_returns_no_questions():
    assert parse_qti_xml("") == []
    assert parse_qti_xml("<questestinterop></questestinterop>") == []


def test_markdown_roundtrips_through_forward_parser():
    questions = parse_qti_xml(sample_qti_xml())
    markdown = questions_to_markdown("Quiz 1", questions)

    reparsed = parse_assessment_questions(markdown)
    assert [q.qtype for q in reparsed] == [
        "multiple_choice_question",
        "true_false_question",
        "essay_question",
    ]
    mc = reparsed[0]
    assert mc.stem == "What is 2 + 2?"
    assert mc.correct == "b"                       # letter B mapped back to ident b
    assert [text for _cid, text in mc.choices] == ["3", "4", "5", "6"]
    assert reparsed[1].correct == "true"


def test_markdown_uses_normalised_headers():
    markdown = questions_to_markdown("Quiz 1", parse_qti_xml(sample_qti_xml()))
    assert "### Question 1" in markdown
    assert "**Stem:**" in markdown
    assert "**Answer Options:**" in markdown
    assert "**Correct Answer:** B" in markdown
    assert "**Explanation:**" in markdown
