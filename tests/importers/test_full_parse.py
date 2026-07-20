"""Full-parse tests (Session 2): structure + page markdown + quiz questions."""

from __future__ import annotations

import tempfile

from promptops_app.importers.canvas_parser import parse_course
from promptops_app.importers.imscc_importer import parse_package
from promptops_app.importers.internal_model import IAssessment, IPage
from promptops_app.importers.package_extractor import extract_package
from tests.importers.fixtures import build_content_imscc


def _parse():
    return parse_package(build_content_imscc())


def test_pages_carry_markdown_and_raw_html():
    course = _parse()
    page = course.modules[0].items[0]           # Module A → Intro
    assert isinstance(page, IPage)
    assert "Welcome to the course" in page.markdown
    assert "First point" in page.markdown
    assert page.raw_body_html                    # kept for Block.content_html
    assert "font-family" not in page.markdown    # locked CSS stripped


def test_quiz_carries_questions_and_normalised_markdown():
    course = _parse()
    quiz = course.modules[0].items[1]           # Module A → Quiz 1
    assert isinstance(quiz, IAssessment)
    assert len(quiz.questions) == 3
    assert "### Question 1" in quiz.body_markdown
    assert "**Correct Answer:** B" in quiz.body_markdown


def test_structure_unchanged_by_body_pass():
    course = _parse()
    assert [m.title for m in course.modules] == ["Module A", "Module B"]
    counts = course.structure_counts()
    assert counts["pages"] == 2
    assert counts["quizzes"] == 1


def test_missing_page_file_degrades_to_warning():
    # Extract, delete a page file, then run the body pass — must warn, not raise.
    with tempfile.TemporaryDirectory() as workdir:
        import os

        extract = extract_package(build_content_imscc(), workdir)
        os.remove(os.path.join(extract.root, "wiki_content", "page2.html"))
        course = parse_course(extract)
        assert any("page2.html" in w or "Wrap Up" in w for w in course.warnings)
        # The rest of the course still parsed.
        assert course.modules[0].items[0].markdown
