"""Phase 3 characterization: documents_library named filter dict contract.

Pins the pre-generalization named-parameter → filters mapping. These must stay
green after registry-authorized query passthrough is added.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.metadata_framework.filter_query import (
    DOCUMENTS_LIBRARY_CONTROL_PARAMS,
    build_documents_library_filters,
)


def test_control_params_match_documents_library_signature():
    """Reserved set is derived from the real FastAPI Query params (not a guess)."""
    assert DOCUMENTS_LIBRARY_CONTROL_PARAMS == frozenset({
        "purpose",
        "document_type",
        "visibility",
        "status",
        "search",
        "block",
        "day",
        "chapter",
        "module_name",
        "learning_objective",
        "course_name",
        "course_id",
        "limit",
        "offset",
    })


def test_named_params_populate_filters_dict():
    filters = build_documents_library_filters(
        document_type="syllabus",
        visibility="instructor",
        status="processed",
        search="guide",
        block="Block 9",
        day="Day 3",
        chapter="Ch 4",
        module_name="Section 2",
        learning_objective="LO-1",
        course_name="Aircraft Systems",
        course_id="101",
    )
    assert filters == {
        "document_type": "syllabus",
        "visibility": "instructor",
        "status": "processed",
        "search": "guide",
        "block": "Block 9",
        "day": "Day 3",
        "chapter": "Ch 4",
        "module_name": "Section 2",
        "learning_objective": "LO-1",
        "course_name": "Aircraft Systems",
        "course_id": "101",
        "metadata_filters": {},
    }


def test_empty_named_params_still_emit_keys():
    """Blank named params remain present (existing router contract)."""
    filters = build_documents_library_filters()
    for key in (
        "document_type", "visibility", "status", "search", "block", "day",
        "chapter", "module_name", "learning_objective", "course_name", "course_id",
    ):
        assert key in filters
        assert filters[key] == ""
    assert filters["metadata_filters"] == {}
