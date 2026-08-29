"""Phase 2 characterization: pin _source_matches filter behaviour BEFORE migration.

These tests encode today's hardcoded field-list matching. They must stay green
across registry-driven matching. Do not invent behaviour that the production
code does not already exhibit.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.context_retrieval import ContextRetrievalService


def _svc():
    return ContextRetrievalService.__new__(ContextRetrievalService)


def _src(**kwargs):
    base = {
        "document_type": "syllabus",
        "source_file_type": "pdf",
        "course_name": "Aircraft Systems",
        "block": "Block 9",
        "day": "Day 3",
        "chapter": "Ch 4",
        "module_name": "Section 2",
        "lesson_name": "Intro",
        "learning_objective": "LO-1",
        "visibility": "instructor",
        "status": "processed",
        "purpose": "cdd",
        "course_id": "101",
        "title": "Sample Doc",
        "source_file_name": "sample.pdf",
    }
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# Empty / wildcard filters — match everything
# ---------------------------------------------------------------------------

def test_empty_filters_match():
    assert _svc()._source_matches(_src(), {}) is True


def test_blank_and_wildcard_taxonomy_filters_match():
    svc = _svc()
    src = _src()
    for field in ("course_name", "block", "day", "chapter", "module_name", "lesson_name"):
        assert svc._source_matches(src, {field: ""}) is True
        assert svc._source_matches(src, {field: None}) is True
        for wild in ("all", "*", "any", "ALL"):
            assert svc._source_matches(src, {field: wild}) is True


# ---------------------------------------------------------------------------
# Exact case-insensitive matching for hardcoded taxonomy fields
# ---------------------------------------------------------------------------

def test_course_name_exact_case_insensitive():
    svc = _svc()
    src = _src(course_name="Aircraft Systems")
    assert svc._source_matches(src, {"course_name": "Aircraft Systems"}) is True
    assert svc._source_matches(src, {"course_name": "aircraft systems"}) is True
    assert svc._source_matches(src, {"course_name": "Powerplant"}) is False


def test_chapter_exact_case_insensitive():
    svc = _svc()
    src = _src(chapter="Ch 4")
    assert svc._source_matches(src, {"chapter": "Ch 4"}) is True
    assert svc._source_matches(src, {"chapter": "ch 4"}) is True
    assert svc._source_matches(src, {"chapter": "Ch 5"}) is False


def test_module_name_exact_case_insensitive():
    svc = _svc()
    src = _src(module_name="Section 2")
    assert svc._source_matches(src, {"module_name": "Section 2"}) is True
    assert svc._source_matches(src, {"module_name": "section 2"}) is True
    assert svc._source_matches(src, {"module_name": "Section 3"}) is False


def test_day_exact_case_insensitive():
    svc = _svc()
    src = _src(day="Day 3")
    assert svc._source_matches(src, {"day": "Day 3"}) is True
    assert svc._source_matches(src, {"day": "day 3"}) is True
    assert svc._source_matches(src, {"day": "Day 4"}) is False


def test_block_uses_same_block_not_string_equality():
    """Block matching is variant-aware (Block 9 ≈ Block 09), not plain strings."""
    svc = _svc()
    src = _src(block="Block 09")
    assert svc._source_matches(src, {"block": "Block 9"}) is True
    assert svc._source_matches(src, {"block": "Block 09"}) is True
    assert svc._source_matches(src, {"block": "Block 8"}) is False


# ---------------------------------------------------------------------------
# Aliases as FILTER KEYS — current code does NOT remap UI keys here
# ---------------------------------------------------------------------------

def test_module_filter_key_does_not_match_module_name_field():
    """Frontend maps module→module_name before the API; backend matching does not."""
    svc = _svc()
    src = _src(module_name="Section 2")
    assert svc._source_matches(src, {"module": "Section 2"}) is True  # unknown key ignored
    assert svc._source_matches(src, {"module_name": "Section 3"}) is False


def test_day_number_filter_key_does_not_match_day_field():
    """day_number is an alias for storage promotion, not a listing filter key today."""
    svc = _svc()
    src = _src(day="Day 3", day_number=3)
    assert svc._source_matches(src, {"day_number": 3}) is True  # unknown key ignored
    assert svc._source_matches(src, {"day": "Day 4"}) is False


# ---------------------------------------------------------------------------
# Missing metadata — filter set → no match
# ---------------------------------------------------------------------------

def test_missing_course_name_fails_when_filtered():
    svc = _svc()
    src = _src()
    del src["course_name"]
    assert svc._source_matches(src, {"course_name": "Aircraft Systems"}) is False


def test_missing_module_name_fails_when_filtered():
    svc = _svc()
    src = _src()
    del src["module_name"]
    assert svc._source_matches(src, {"module_name": "Section 2"}) is False


def test_empty_string_metadata_fails_when_filtered():
    svc = _svc()
    assert _svc()._source_matches(
        _src(chapter=""), {"chapter": "Ch 4"}
    ) is False


# ---------------------------------------------------------------------------
# learning_objective — via metadata_filters only (not top-level field loop)
# ---------------------------------------------------------------------------

def test_learning_objective_via_metadata_filters_matches():
    svc = _svc()
    src = _src(learning_objective="LO-1")
    assert svc._source_matches(
        src, {"metadata_filters": {"learning_objective": "LO-1"}}
    ) is True
    assert svc._source_matches(
        src, {"metadata_filters": {"learning_objective": "lo-1"}}
    ) is True
    assert svc._source_matches(
        src, {"metadata_filters": {"learning_objective": "LO-2"}}
    ) is False


def test_learning_objective_top_level_filter_is_registry_driven():
    """Phase 2: LO is filter_options-promoted, so top-level filters match like module_name."""
    svc = _svc()
    src = _src(learning_objective="LO-1")
    assert svc._source_matches(src, {"learning_objective": "LO-1"}) is True
    assert svc._source_matches(src, {"learning_objective": "lo-1"}) is True
    assert svc._source_matches(src, {"learning_objective": "LO-2"}) is False


def test_learning_objective_missing_fails_via_metadata_filters():
    svc = _svc()
    src = _src()
    del src["learning_objective"]
    assert svc._source_matches(
        src, {"metadata_filters": {"learning_objective": "LO-1"}}
    ) is False


# ---------------------------------------------------------------------------
# Operational listing fields still in the hardcoded loop
# ---------------------------------------------------------------------------

def test_document_type_and_visibility_and_status():
    svc = _svc()
    src = _src()
    assert svc._source_matches(src, {"document_type": "syllabus"}) is True
    assert svc._source_matches(src, {"document_type": "quiz_exam"}) is False
    assert svc._source_matches(src, {"visibility": "instructor"}) is True
    assert svc._source_matches(src, {"visibility": "student"}) is False
    assert svc._source_matches(src, {"status": "processed"}) is True
    assert svc._source_matches(src, {"status": "failed"}) is False


def test_lesson_name_filter():
    svc = _svc()
    src = _src(lesson_name="Intro")
    assert svc._source_matches(src, {"lesson_name": "Intro"}) is True
    assert svc._source_matches(src, {"lesson_name": "outro"}) is False


def test_source_file_type_filter():
    svc = _svc()
    assert _svc()._source_matches(_src(), {"source_file_type": "pdf"}) is True
    assert _svc()._source_matches(_src(), {"source_file_type": "docx"}) is False
