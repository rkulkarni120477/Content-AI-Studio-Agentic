"""Regression tests for REAL Canvas exports (not the CAS self-export shape).

Real Canvas differs in ways that broke the first parser:
  * resource hrefs live on a ``<file href>`` child (quizzes/discussions have no
    ``href`` attribute);
  * assignments use the ``learning-application-resource`` type (same as the meta
    marker), so we can't skip on type — only on a ``course_settings/`` href;
  * the course title is ``lomimscc:<string>`` / ``course_settings.xml``;
  * a quiz's CC ``assessment_qti.xml`` is often an empty shell with the real
    questions in ``non_cc_assessments/<id>.xml.qti``.
"""

from __future__ import annotations

from promptops_app.importers.imscc_importer import parse_package
from promptops_app.importers.internal_model import ASSIGNMENT_KIND, PAGE_KIND, QUIZ_KIND
from tests.importers.fixtures import build_imscc_with_orphans, build_real_canvas_imscc


def test_title_and_counts():
    course = parse_package(build_real_canvas_imscc())
    assert course.title == "Physics 101"                 # lomimscc / course_settings title
    counts = course.structure_counts()
    assert counts["modules"] == 1
    assert counts["pages"] == 1
    assert counts["assignments"] == 1                    # learning-application-resource NOT skipped
    assert counts["quizzes"] == 1
    assert course.warnings == []                         # nothing dropped


def test_items_have_content():
    course = parse_package(build_real_canvas_imscc())
    items = {i.kind: i for i in course.modules[0].items}

    assert "Welcome to physics" in items[PAGE_KIND].markdown
    assert "Assignment about electric current" in items[ASSIGNMENT_KIND].body_markdown

    quiz = items[QUIZ_KIND]
    assert len(quiz.questions) == 1                      # from the non-CC bank fallback
    assert "Unit of resistance" in quiz.body_markdown
    assert "**Correct Answer:** A" in quiz.body_markdown


def test_orphan_pages_recovered_and_deduped():
    course = parse_package(build_imscc_with_orphans())

    extra = [m for m in course.modules if m.title.startswith("Additional Pages")]
    assert len(extra) == 1                               # a trailing module was added
    # The unique orphan is recovered; the exact-duplicate orphan is skipped.
    assert len(extra[0].items) == 1
    assert "Unique orphan content" in extra[0].items[0].markdown
    # module page (1) + unique orphan (1); duplicate not counted
    assert course.structure_counts()["pages"] == 2


def test_no_orphan_module_when_all_pages_are_in_modules():
    # CAS-style / clean exports must not grow a synthetic module.
    course = parse_package(build_real_canvas_imscc())
    assert not any(m.title.startswith("Additional Pages") for m in course.modules)
