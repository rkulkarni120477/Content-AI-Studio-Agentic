"""Unit tests for ebook tagging failure flags and content.json allow-list."""
from __future__ import annotations

import json

from services.ebook_page_tagger import (
    TAGGING_FAILED,
    TAGGING_OK,
    TAGGING_PENDING,
    mark_units_pending,
    tag_ebook_page_units,
    tagging_counts,
)
from services.source_library import build_clean_content_document


def _units(n: int = 3):
    out = []
    for i in range(1, n + 1):
        out.append({
            "content_unit_id": f"j:page_{i}",
            "unit_number": i,
            "text": f"Page {i} covers landing gear AM.I.D.K1 details.",
            "topics": ["landing"],
            "keywords": ["landing"],
            "metadata": {
                "pdf_page": i,
                "page_number": f"13-{i}",
                "chapter": 13,
                "chunking_strategy": "page",
                "tagging_status": TAGGING_PENDING,
            },
        })
    return out


def test_failed_batch_stamps_failed_on_every_unit():
    units = _units(2)

    def boom(model, prompt, max_tokens=1200):
        raise RuntimeError("bedrock down")

    errors = []
    tag_ebook_page_units(
        units, call_llm_fn=boom, model_id="m", batch_size=10, enabled=True, errors=errors,
    )
    assert all(u["metadata"]["tagging_status"] == TAGGING_FAILED for u in units)
    assert "bedrock down" in units[0]["metadata"]["tagging_error"]
    assert errors


def test_partial_response_marks_missing_pages_failed():
    units = _units(2)

    def fake_llm(model, prompt, max_tokens=1200):
        # Only page 1 returned.
        return json.dumps([
            {"pdf_page": 1, "topics": ["oleo"], "acs_codes": ["AM.I.D.K1"], "summary": "Ok."},
        ]), 1, 1

    tag_ebook_page_units(
        units, call_llm_fn=fake_llm, model_id="m", batch_size=10, enabled=True,
    )
    assert units[0]["metadata"]["tagging_status"] == TAGGING_OK
    assert "tagging_error" not in units[0]["metadata"]
    assert units[1]["metadata"]["tagging_status"] == TAGGING_FAILED
    assert units[1]["metadata"]["tagging_error"] == "missing_from_llm_response"


def test_disabled_tagger_leaves_pending():
    units = _units(1)
    tag_ebook_page_units(
        units, call_llm_fn=lambda *a, **k: ("[]", 0, 0),
        model_id="", enabled=False,
    )
    assert units[0]["metadata"]["tagging_status"] == TAGGING_PENDING


def test_tagging_counts():
    units = _units(3)
    units[0]["metadata"]["tagging_status"] = TAGGING_OK
    units[1]["metadata"]["tagging_status"] = TAGGING_FAILED
    units[2]["metadata"]["tagging_status"] = TAGGING_PENDING
    assert tagging_counts(units) == {
        "tagging_failed_count": 1,
        "tagging_pending_count": 1,
    }


def test_content_json_keeps_tagging_flags():
    doc = build_clean_content_document({
        "job_id": "j",
        "metadata": {"document_type": "ebook_reference", "title": "HB"},
        "source_file": {"name": "hb.pdf", "type": "pdf"},
        "content_units": [{
            "content_unit_id": "j:page_1",
            "unit_type": "page",
            "unit_number": 1,
            "title": "HB — p. 13-1",
            "text": "Landing gear text",
            "metadata": {
                "chapter": 13, "page_number": "13-1", "pdf_page": 100,
                "chunking_strategy": "page",
                "tagging_status": "failed",
                "tagging_error": "bedrock down",
                "tagging_attempted_at": "2026-09-12T00:00:00+00:00",
            },
        }],
    })
    assert doc["chunking_strategy"] == "page"
    assert doc["tagging_failed_count"] == 1
    md = doc["content_units"][0]["metadata"]
    assert md["tagging_status"] == "failed"
    assert md["tagging_error"] == "bedrock down"
    assert md["tagging_attempted_at"]


def test_mark_units_pending_skips_ok():
    units = _units(2)
    units[0]["metadata"]["tagging_status"] = TAGGING_OK
    mark_units_pending(units)
    assert units[0]["metadata"]["tagging_status"] == TAGGING_OK
    assert units[1]["metadata"]["tagging_status"] == TAGGING_PENDING
