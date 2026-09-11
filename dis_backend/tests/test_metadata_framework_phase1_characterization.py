"""Phase 1 characterization: pin current metadata projection contracts.

These tests must stay green across Field Registry migration. They encode the
hardcoded behaviour that empty ``metadata_framework`` must continue to produce.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.context_retrieval import ContextRetrievalService
from services.source_library import compact_source_record, source_filter_options


# ---------------------------------------------------------------------------
# Golden key sets — order matches the pre-registry hardcoded dicts.
# ---------------------------------------------------------------------------

GOLDEN_COMPACT_METADATA_KEYS = [
    "course_name",
    "block",
    "day",
    "chapter",
    "module_name",
    "learning_objective",
    "content_type",
    "block_id",
    "block_number",
    "day_number",
    "day_id",
    "mapped_day",
    "filename_day_id",
    "quiz_number",
    "project_number",
    "lesson_name",
    "subject_unit",
    "course_id",
    "program_id",
    "calendar_mapping_required",
    "is_generation_candidate",
    "is_archive_or_working_version",
]

GOLDEN_COMPACT_SCAFFOLD_KEYS = [
    "document_id",
    "job_id",
    "tenant_id",
    "client_id",
    "title",
    "source_file_name",
    "source_file_type",
    "document_type",
    "purpose",
    "visibility",
    "restricted",
    "access_level",
    "status",
    "size_bytes",
    "page_count",
    "total_units",
    "payload_key",
    "content_key",
    "raw_storage_url",
    "raw_key",
    "source_relative_path",
    "created_at",
    "updated_at",
]

GOLDEN_RETRIEVAL_METADATA_KEYS = [
    "title",
    "document_type",
    "doc_type",
    "purpose",
    "visibility",
    "restricted",
    "access_level",
    "status",
    "course_name",
    "block",
    "day",
    "chapter",
    "module_name",
    "learning_objective",
    "content_type",
    "block_id",
    "block_number",
    "day_number",
    "day_id",
    "mapped_day",
    "filename_day_id",
    "quiz_number",
    "project_number",
    "lesson_name",
    "subject_unit",
    "course_id",
    "program_id",
    "calendar_mapping_required",
    "is_generation_candidate",
    "is_archive_or_working_version",
]

GOLDEN_FILTER_OPTIONS_MAP = {
    "document_types": "document_type",
    "source_file_types": "source_file_type",
    "purposes": "purpose",
    "status": "status",
    "course_name": "course_name",
    "blocks": "block",
    "days": "day",
    "chapter": "chapter",
    "module": "module_name",
    "learning_objective": "learning_objective",
}

GOLDEN_CAS_LIST_TAXONOMY_KEYS = [
    "course_name",
    "block",
    "day",
    "chapter",
    "module_name",
    "learning_objective",
]


def _rich_payload() -> dict:
    return {
        "job_id": "job-1",
        "tenant_id": "aim",
        "client_id": "aim",
        "created_at": "2026-01-01T00:00:00",
        "source_file": {
            "name": "Block9_Day1.pdf",
            "type": "pdf",
            "size_bytes": 1200,
            "raw_url": "s3://bucket/raw",
            "raw_key": "raw/key",
            "relative_path": "Block9/Day1.pdf",
        },
        "metadata": {
            "title": "Lesson Deck",
            "document_type": "lesson_slide_deck",
            "purpose": "course_generation",
            "visibility": "instructor",
            "restricted": False,
            "status": "processed",
            "course_name": "General Science I",
            "block": "Block 9",
            "day": "Day 1",
            "chapter": "Ch 3",
            "module_name": "Section A",
            "learning_objective": "LO-1",
            "content_type": "lesson_slide_deck",
            "block_id": "b9",
            "block_number": 9,
            "day_number": 1,
            "day_id": "B9D1",
            "mapped_day": "B9D1",
            "filename_day_id": "B9D1",
            "quiz_number": 2,
            "project_number": "P1",
            "lesson_name": "Intro",
            "subject_unit": "SU1",
            "course_id": "101",
            "program_id": "aim",
            "calendar_mapping_required": True,
            "is_generation_candidate": True,
            "is_archive_or_working_version": False,
        },
        "content_units": [
            {
                "content_unit_id": "job-1:0",
                "text": "hello",
                "metadata": {"day_number": 1, "quiz_number": 2},
            }
        ],
        "page_count": 10,
    }


def test_compact_source_record_key_contract():
    with patch("services.source_library.datetime") as dt:
        dt.utcnow.return_value.isoformat.return_value = "2026-08-29T12:00:00"
        rec = compact_source_record(_rich_payload(), "payload/key", "content/key")
    assert list(rec.keys()) == GOLDEN_COMPACT_SCAFFOLD_KEYS + GOLDEN_COMPACT_METADATA_KEYS


def test_compact_source_record_value_contract():
    with patch("services.source_library.datetime") as dt:
        dt.utcnow.return_value.isoformat.return_value = "2026-08-29T12:00:00"
        rec = compact_source_record(_rich_payload(), "payload/key", "content/key")
    assert rec["course_name"] == "General Science I"
    assert rec["block"] == "Block 9"
    assert rec["day"] == "Day 1"
    assert rec["chapter"] == "Ch 3"
    assert rec["module_name"] == "Section A"
    assert rec["learning_objective"] == "LO-1"
    assert rec["block_id"] == "b9"
    assert rec["day_number"] == 1
    assert rec["quiz_number"] == 2
    assert rec["calendar_mapping_required"] is True
    assert rec["is_generation_candidate"] is True
    assert rec["is_archive_or_working_version"] is False
    assert rec["purpose"] == "course_generation"
    assert rec["visibility"] == "instructor"


def test_compact_source_record_promote_falls_back_to_unit_metadata():
    payload = _rich_payload()
    payload["metadata"] = {
        "document_type": "quiz_exam",
        "title": "Quiz",
    }
    payload["content_units"] = [
        {"metadata": {"day_number": 4, "day_id": "B2D4", "quiz_number": 7, "subject_unit": "U"}},
    ]
    with patch("services.source_library.datetime") as dt:
        dt.utcnow.return_value.isoformat.return_value = "2026-08-29T12:00:00"
        rec = compact_source_record(payload, "p", "c")
    assert rec["day_number"] == 4
    assert rec["day_id"] == "B2D4"
    assert rec["quiz_number"] == 7
    assert rec["subject_unit"] == "U"
    assert rec["block_id"] == ""  # no block on meta → empty, not unit fallback for block_id path


def test_compact_source_record_block_id_falls_back_to_block():
    payload = _rich_payload()
    payload["metadata"]["block_id"] = ""
    payload["metadata"]["block"] = "Block 3"
    with patch("services.source_library.datetime") as dt:
        dt.utcnow.return_value.isoformat.return_value = "2026-08-29T12:00:00"
        rec = compact_source_record(payload, "p", "c")
    assert rec["block_id"] == "Block 3"


def test_metadata_from_record_key_and_value_contract():
    svc = ContextRetrievalService.__new__(ContextRetrievalService)
    rec = {
        "title": "T",
        "document_type": "syllabus",
        "purpose": "cdd",
        "visibility": "instructor",
        "restricted": False,
        "access_level": "",
        "status": "processed",
        "course_name": "C",
        "block": "Block 1",
        "day": "Day 2",
        "chapter": "1",
        "module_name": "M",
        "learning_objective": "LO",
        "content_type": "syllabus",
        "block_id": "b1",
        "block_number": 1,
        "day_number": 2,
        "day_id": "B1D2",
        "mapped_day": "B1D2",
        "filename_day_id": "",
        "quiz_number": None,
        "project_number": "",
        "lesson_name": "",
        "subject_unit": "",
        "course_id": "101",
        "program_id": "aim",
        "calendar_mapping_required": False,
        "is_generation_candidate": None,
        "is_archive_or_working_version": False,
    }
    meta = svc._metadata_from_record(rec)
    assert list(meta.keys()) == GOLDEN_RETRIEVAL_METADATA_KEYS
    assert meta["doc_type"] == "syllabus"
    assert meta["document_type"] == "syllabus"
    assert meta["module_name"] == "M"
    assert meta["is_generation_candidate"] is None


def test_source_filter_options_map_and_values():
    records = [
        {
            "document_type": "syllabus",
            "source_file_type": "pdf",
            "purpose": "cdd",
            "status": "processed",
            "course_name": "Algebra",
            "block": "Block 1",
            "day": "Day 1",
            "chapter": "3",
            "module_name": "Section 3",
            "learning_objective": "LO-9",
        },
        {
            "document_type": "syllabus",
            "source_file_type": "docx",
            "purpose": "style",
            "status": "processed",
            "course_name": "Algebra",
            "block": "",
            "day": "",
            "chapter": "1",
            "module_name": "Section 1",
            "learning_objective": "",
        },
    ]
    opts = source_filter_options(records)
    assert set(opts.keys()) == set(GOLDEN_FILTER_OPTIONS_MAP.keys())
    assert opts["module"] == ["Section 1", "Section 3"]
    assert opts["blocks"] == ["Block 1"]
    assert opts["days"] == ["Day 1"]
    assert opts["chapter"] == ["1", "3"]
    assert opts["learning_objective"] == ["LO-9"]
    assert opts["course_name"] == ["Algebra"]
    assert "visibility" not in opts


def test_cas_list_taxonomy_keys_present_on_list_projection():
    """Taxonomy tail for CAS listing comes from registry cas_list promote."""
    from services.metadata_framework.adapters import project_cas_list_taxonomy
    from services.metadata_framework.registry import DEFAULT_REGISTRY

    src = {
        "course_name": "C",
        "block": "B",
        "day": "D",
        "chapter": "Ch",
        "module_name": "M",
        "learning_objective": "LO",
        "internal_secret": "leak",
    }
    projected = project_cas_list_taxonomy(src, DEFAULT_REGISTRY)
    assert list(projected.keys()) == GOLDEN_CAS_LIST_TAXONOMY_KEYS
    for key in GOLDEN_CAS_LIST_TAXONOMY_KEYS:
        assert projected[key] == src[key]
    assert "internal_secret" not in projected


def test_golden_filter_options_map_matches_module_alias_contract():
    """Cengage UI key ``module`` reads record field ``module_name``."""
    assert GOLDEN_FILTER_OPTIONS_MAP["module"] == "module_name"
    assert GOLDEN_FILTER_OPTIONS_MAP["blocks"] == "block"
    assert GOLDEN_FILTER_OPTIONS_MAP["days"] == "day"
