"""Phase 9 — metadata editor service tests."""
from __future__ import annotations

import copy
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.metadata_editor import (
    DocumentNotFoundError,
    InvalidRelationshipTargetError,
    MetadataEditorError,
    get_document_metadata,
    patch_document_metadata,
    revert_document_metadata_to_ai,
)


def _tenant():
    cfg = MagicMock()
    cfg.tenant_id = "cengage"
    cfg.get_namespace.return_value = "cengage_ns"
    return cfg


def _record(job_id="job-1"):
    return {
        "job_id": job_id,
        "document_id": job_id,
        "client_id": "cengage",
        "title": "Marketing Handbook 2024",
        "source_file_name": "handbook.pdf",
        "document_type": "textbook_pdf",
        "purpose": "style",
        "size_bytes": 2048,
        "page_count": 182,
        "payload_key": "processed/cengage_ns/development/job-1/studio_payload/payload.json",
        "content_key": "processed/cengage_ns/development/job-1/source_content/content.json",
    }


def _payload(job_id="job-1"):
    return {
        "job_id": job_id,
        "client_id": "cengage",
        "tenant_id": "cengage",
        "metadata": {
            "title": "Marketing Handbook 2024",
            "authors": ["Cengage Marketing Team"],
            "discipline": "Digital Marketing",
            "language": "en-US",
            "description": "Comprehensive internal reference",
            "keywords": ["marketing", "brand"],
            "taxonomy_domain": "Marketing",
            "relationships": {
                "series_collection": "Marketing Reference Library 2024",
                "related_documents": [],
                "prerequisites": [],
                "cross_references": [],
            },
        },
        "source_file": {"name": "handbook.pdf", "type": "pdf", "size_bytes": 2048},
        "content_units": [{"text": "Sample", "metadata": {}}],
    }


def _ai_baseline():
    return {
        "title": "AI Title",
        "authors": ["AI Author"],
        "discipline": "AI Subject",
        "language": "en-GB",
        "description": "AI description",
        "keywords": ["ai", "tag"],
        "taxonomy_domain": "AI Domain",
    }


@pytest.fixture()
def writer_storage():
    store = {
        "processed/cengage_ns/development/job-1/studio_payload/payload.json": _payload(),
        "processed/cengage_ns/development/job-1/extracted/metadata.json": _ai_baseline(),
    }
    index = {"sources": [_record()]}

    def read_json(key):
        if key not in store:
            raise FileNotFoundError(key)
        return copy.deepcopy(store[key])

    def write_json(key, data):
        store[key] = copy.deepcopy(data)
        return f"s3://bucket/{key}"

    writer = MagicMock()
    writer.read_json = read_json
    writer.write_json = write_json
    writer.processed_bucket = "content-ai-studio"
    writer.base_prefix = "DIS"
    return store, index, writer


@patch("services.metadata_editor.write_source_content_and_index")
@patch("services.metadata_editor.read_source_index")
@patch("services.metadata_editor.ArtifactWriter")
def test_get_document_metadata(mock_writer_cls, mock_read_index, mock_write_index, writer_storage):
    store, index, writer = writer_storage
    mock_writer_cls.return_value = writer
    mock_read_index.return_value = index

    result = get_document_metadata(_tenant(), "cengage", "job-1")

    assert result["ai_metadata"]["title"] == "Marketing Handbook 2024"
    assert result["taxonomy_standards"]["domain"] == "Marketing"
    assert result["relationships"]["series_collection"] == "Marketing Reference Library 2024"
    assert result["ai_baseline_available"] is True
    mock_write_index.assert_not_called()


@patch("services.metadata_editor.write_source_content_and_index")
@patch("services.metadata_editor.read_source_index")
@patch("services.metadata_editor.ArtifactWriter")
def test_patch_metadata_persists_payload_and_reprojects(
    mock_writer_cls, mock_read_index, mock_write_index, writer_storage,
):
    store, index, writer = writer_storage
    mock_writer_cls.return_value = writer
    mock_read_index.return_value = index

    result = patch_document_metadata(
        _tenant(),
        "cengage",
        "job-1",
        ai_metadata={"title": "Updated Title", "keywords": ["one", "two"]},
    )

    assert result["ai_metadata"]["title"] == "Updated Title"
    saved = store["processed/cengage_ns/development/job-1/studio_payload/payload.json"]
    assert saved["metadata"]["title"] == "Updated Title"
    assert saved["metadata"]["keywords"] == ["one", "two"]
    ai_baseline = store["processed/cengage_ns/development/job-1/extracted/metadata.json"]
    assert ai_baseline["title"] == "AI Title"
    mock_write_index.assert_called_once()


@patch("services.metadata_editor.write_source_content_and_index")
@patch("services.metadata_editor.read_source_index")
@patch("services.metadata_editor.ArtifactWriter")
def test_revert_restores_ai_baseline(
    mock_writer_cls, mock_read_index, mock_write_index, writer_storage,
):
    store, index, writer = writer_storage
    mock_writer_cls.return_value = writer
    mock_read_index.return_value = index

    result = revert_document_metadata_to_ai(_tenant(), "cengage", "job-1")

    assert result["ai_metadata"]["title"] == "AI Title"
    assert result["ai_metadata"]["author"] == "AI Author"
    saved = store["processed/cengage_ns/development/job-1/studio_payload/payload.json"]
    assert saved["metadata"]["title"] == "AI Title"
    assert store["processed/cengage_ns/development/job-1/extracted/metadata.json"]["title"] == "AI Title"


@patch("services.metadata_editor.read_source_index")
def test_document_not_found(mock_read_index):
    mock_read_index.return_value = {"sources": []}
    with pytest.raises(DocumentNotFoundError):
        get_document_metadata(_tenant(), "cengage", "missing")


@patch("services.metadata_editor.write_source_content_and_index")
@patch("services.metadata_editor.read_source_index")
@patch("services.metadata_editor.ArtifactWriter")
def test_invalid_relationship_target(
    mock_writer_cls, mock_read_index, mock_write_index, writer_storage,
):
    _, index, writer = writer_storage
    mock_writer_cls.return_value = writer
    mock_read_index.return_value = index

    with pytest.raises(InvalidRelationshipTargetError):
        patch_document_metadata(
            _tenant(),
            "cengage",
            "job-1",
            relationships={"related_documents": [{"job_id": "unknown", "label": "Other"}]},
        )


@patch("services.metadata_editor.write_source_content_and_index")
@patch("services.metadata_editor.read_source_index")
@patch("services.metadata_editor.ArtifactWriter")
def test_keywords_max_validation(
    mock_writer_cls, mock_read_index, mock_write_index, writer_storage,
):
    _, index, writer = writer_storage
    mock_writer_cls.return_value = writer
    mock_read_index.return_value = index

    with pytest.raises(MetadataEditorError):
        patch_document_metadata(
            _tenant(),
            "cengage",
            "job-1",
            ai_metadata={"keywords": [str(i) for i in range(13)]},
        )
