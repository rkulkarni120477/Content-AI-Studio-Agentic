"""Re-index pushes stored units into OpenSearch without re-uploading.

"Not indexed" is a finished ingest: text is in source_content, the search index
has zero units. Re-index restores search. An empty extract is refused — that
file needs a re-upload, not another trip through embedding.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from services.source_library import SourceReindexError, reindex_source_document


def _tenant():
    return SimpleNamespace(tenant_id="aim")


def _index(record):
    return {"sources": [record]}


def test_missing_source_is_404():
    with patch("services.source_library.read_source_index", return_value={"sources": []}):
        with pytest.raises(SourceReindexError) as exc:
            reindex_source_document(_tenant(), "aim", "missing")
    assert exc.value.status_code == 404


def test_still_processing_is_refused():
    rec = {"job_id": "j1", "status": "processing", "content_key": "k"}
    with patch("services.source_library.read_source_index", return_value=_index(rec)):
        with pytest.raises(SourceReindexError) as exc:
            reindex_source_document(_tenant(), "aim", "j1")
    assert exc.value.status_code == 409
    assert "still running" in exc.value.message.lower()


def test_empty_extract_cannot_be_indexed():
    rec = {
        "job_id": "j1", "status": "processed", "content_key": "k",
        "source_file_name": "exam.docx",
    }
    writer = SimpleNamespace(read_json=lambda key: {
        "content_units": [{"content_unit_id": "j1:0", "text": ""}],
    })
    with patch("services.source_library.read_source_index", return_value=_index(rec)), \
         patch("services.source_library.ArtifactWriter", return_value=writer):
        with pytest.raises(SourceReindexError) as exc:
            reindex_source_document(_tenant(), "aim", "j1")
    assert exc.value.status_code == 409
    assert "re-upload" in exc.value.message.lower()


def test_extracted_units_are_embedded_and_upserted():
    rec = {
        "job_id": "j1", "status": "processed", "content_key": "k",
        "source_file_name": "Block 5 Final Cumulative Exam Alternate.docx",
        "document_type": "quiz_exam", "block": "Block 5",
        "tenant_id": "aim", "client_id": "aim",
    }
    writer = SimpleNamespace(read_json=lambda key: {
        "content_units": [{
            "content_unit_id": "j1:1",
            "unit_number": 1,
            "title": "Exam",
            "text": "Question 1: Identify the metallic structure.",
        }],
    })
    with patch("services.source_library.read_source_index", return_value=_index(rec)), \
         patch("services.source_library.ArtifactWriter", return_value=writer), \
         patch("services.indexing.generate_embeddings",
               return_value={"status": "completed", "embeddings_created": 1}) as emb, \
         patch("services.indexing.opensearch_upsert",
               return_value={"status": "completed", "documents_indexed": 1}) as up:
        result = reindex_source_document(_tenant(), "aim", "j1")
    assert result["units_indexed"] == 1
    assert result["units_available"] == 1
    assert emb.called and up.called
    state = emb.call_args[0][1]
    assert "Question 1" in state["content_units"][0]["text"]
