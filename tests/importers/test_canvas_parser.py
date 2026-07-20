"""Tests for the Canvas IMSCC structural parser (Session 1)."""

from __future__ import annotations

from promptops_app.importers.imscc_importer import validate_package
from promptops_app.importers.internal_model import PAGE_KIND, QUIZ_KIND
from tests.importers.fixtures import build_sample_imscc


def test_structure_counts_from_module_meta():
    course = validate_package(build_sample_imscc(with_module_meta=True))
    counts = course.structure_counts()
    assert counts["modules"] == 2
    assert counts["pages"] == 2
    assert counts["quizzes"] == 1
    assert counts["assignments"] == 0
    assert counts["discussions"] == 0
    assert counts["resources"] == 1        # web_resources/logo.png


def test_course_title_from_manifest():
    course = validate_package(build_sample_imscc())
    assert course.title == "Sample Course"


def test_module_order_follows_position():
    course = validate_package(build_sample_imscc(with_module_meta=True))
    assert [m.title for m in course.modules] == ["Module A", "Module B"]


def test_item_order_and_kinds_within_module():
    course = validate_package(build_sample_imscc(with_module_meta=True))
    module_a = course.modules[0]
    # LTI + missing-resource items are dropped; only Intro (page) and Quiz 1 remain.
    assert [i.title for i in module_a.items] == ["Intro", "Quiz 1"]
    assert module_a.items[0].kind == PAGE_KIND
    assert module_a.items[1].kind == QUIZ_KIND


def test_warnings_for_unsupported_and_missing_items():
    course = validate_package(build_sample_imscc(with_module_meta=True))
    joined = " ".join(course.warnings)
    assert "External Tool" in joined          # unsupported LTI flagged
    assert "Ghost" in joined                   # missing-resource item flagged


def test_provenance_ids_are_canvas_identifiers():
    course = validate_package(build_sample_imscc(with_module_meta=True))
    intro = course.modules[0].items[0]
    assert intro.provenance_id == "i_page1"
    assert intro.href == "wiki_content/page1.html"


def test_fallback_to_organizations_when_no_module_meta():
    course = validate_package(build_sample_imscc(with_module_meta=False))
    counts = course.structure_counts()
    assert counts["modules"] == 2
    assert counts["pages"] == 2
    assert counts["quizzes"] == 1
    assert [m.title for m in course.modules] == ["Module A", "Module B"]
    assert any("module_meta.xml not found" in w for w in course.warnings)
