"""Phase 3 CAS characterization + dynamic filter forwarding."""
from __future__ import annotations

from app.core.source_library_filter_forward import (
    CAS_DOCUMENTS_CONTROL_PARAMS,
    build_cas_documents_library_params,
)


def test_cas_control_params_are_local_only():
    assert "project_id" in CAS_DOCUMENTS_CONTROL_PARAMS
    assert "all_courses" in CAS_DOCUMENTS_CONTROL_PARAMS
    assert "module_name" not in CAS_DOCUMENTS_CONTROL_PARAMS


def test_named_params_forwarded_unchanged():
    params = build_cas_documents_library_params(
        purpose="cdd",
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
        limit=50,
        offset=10,
    )
    assert params == {
        "purpose": "cdd",
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
        "limit": 50,
        "offset": 10,
    }


def test_dynamic_synth_field_forwarded_to_dis_params():
    params = build_cas_documents_library_params(
        module_name="Section 2",
        query_params={"test_metadata_field": "ABC", "unknown_random_filter": "x"},
    )
    assert params["module_name"] == "Section 2"
    assert params["test_metadata_field"] == "ABC"
    # CAS forwards; DIS ignores unauthorized keys.
    assert params["unknown_random_filter"] == "x"


def test_cas_control_params_not_forwarded_as_extras():
    params = build_cas_documents_library_params(
        course_id="101",
        query_params={"project_id": "9", "all_courses": "true", "client_id": "aim"},
    )
    assert "project_id" not in params
    assert "all_courses" not in params
    assert "client_id" not in params
    assert params["course_id"] == "101"


def test_named_param_not_overridden_by_query_duplicate():
    params = build_cas_documents_library_params(
        block="Block 9",
        query_params={"block": "Block 1"},
    )
    assert params["block"] == "Block 9"
