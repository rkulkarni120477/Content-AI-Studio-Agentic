"""Tests for calendar sheet LLM tagging (ebook-style topics/summary/ACS)."""
from __future__ import annotations

import json

from services.calendar_sheet_tagger import tag_calendar_sheet_units
from services.ebook_page_tagger import TAGGING_FAILED, TAGGING_OK, TAGGING_PENDING
from services.source_library import _PER_UNIT_METADATA_KEYS, build_clean_content_document


def _sheet_unit(idx: int, *, acs=None, text="Aircraft Drawings day text AM.I.B.K1"):
    return {
        "content_unit_id": f"j:calendar_sheet_{idx}",
        "unit_type": "calendar_sheet",
        "unit_number": idx + 1,
        "title": f"Block {idx + 1} (Day-Night)",
        "text": text,
        "topics": ["heuristic"],
        "metadata": {
            "sheet_index": idx,
            "sheet_name": f"Block {idx + 1} (Day-Night)",
            "block": f"Block {idx + 1}",
            "schedule": "day_night",
            "acs_codes": acs or ["AM.I.B.K1"],
            "document_type": "course_calendar",
        },
    }


def test_calendar_sheet_tagger_merges_acs_and_sets_topics_summary():
    units = [_sheet_unit(0, acs=["AM.I.B.K1"])]

    def fake_llm(model, prompt, max_tokens=0):
        payload = [{
            "sheet_index": 0,
            "topics": ["Aircraft Drawings", "Title Blocks"],
            "acs_codes": ["AM.I.B.K1", "AM.I.B.K2", "NOT.A.CODE"],
            "summary": "Block 1 Day-Night covers drawings fundamentals.",
        }]
        return json.dumps(payload), 10, 20

    tag_calendar_sheet_units(
        units, call_llm_fn=fake_llm, model_id="test-model", batch_size=5, enabled=True,
    )
    meta = units[0]["metadata"]
    assert meta["tagging_status"] == TAGGING_OK
    assert "tagging_error" not in meta
    assert units[0]["topics"] == ["Aircraft Drawings", "Title Blocks"]
    assert meta["topics"] == ["Aircraft Drawings", "Title Blocks"]
    assert meta["summary"].startswith("Block 1")
    # Spreadsheet ACS kept; LLM K2 merged; invalid code dropped.
    assert meta["acs_codes"] == ["AM.I.B.K1", "AM.I.B.K2"]


def test_calendar_sheet_tagger_marks_pending_when_disabled():
    units = [_sheet_unit(0)]
    tag_calendar_sheet_units(
        units, call_llm_fn=lambda *a, **k: ("", 0, 0),
        model_id="", enabled=False,
    )
    assert units[0]["metadata"]["tagging_status"] == TAGGING_PENDING


def test_calendar_sheet_tagger_marks_failed_on_llm_error():
    units = [_sheet_unit(0), _sheet_unit(1)]

    def boom(model, prompt, max_tokens=0):
        raise RuntimeError("bedrock down")

    errors = []
    tag_calendar_sheet_units(
        units, call_llm_fn=boom, model_id="m", enabled=True, errors=errors,
    )
    assert all(u["metadata"]["tagging_status"] == TAGGING_FAILED for u in units)
    assert errors and "bedrock down" in errors[0]


def test_retag_batch_dispatches_to_calendar_tagger():
    from services.ebook_page_retag import _tag_batch

    units = [_sheet_unit(0)]

    def fake_llm(model, prompt, max_tokens=0):
        assert "course-calendar SHEET" in prompt or "calendar" in prompt.lower()
        return json.dumps([{
            "sheet_index": 0,
            "topics": ["Math"],
            "acs_codes": ["AM.I.H.K1"],
            "summary": "Math sheet.",
        }]), 1, 1

    errors = []
    _tag_batch(units, call_llm_fn=fake_llm, model_id="m", batch_size=1, errors=errors)
    assert units[0]["metadata"]["tagging_status"] == TAGGING_OK
    assert "Math" in units[0]["topics"]


def test_per_unit_allow_list_keeps_tagging_fields():
    for key in ("topics", "summary", "tagging_status", "tagging_error", "tagging_attempted_at"):
        assert key in _PER_UNIT_METADATA_KEYS

    payload = {
        "job_id": "j",
        "metadata": {"document_type": "course_calendar", "purpose": "blueprint", "title": "All"},
        "source_file": {"name": "cal.xlsx", "type": "xlsx"},
        "content_units": [{
            "content_unit_id": "j:calendar_sheet_0",
            "unit_type": "calendar_sheet",
            "unit_number": 1,
            "title": "Block 2 (Day-Night)",
            "text": "day text",
            "metadata": {
                "block": "Block 2",
                "sheet_name": "Block 2 (Day-Night)",
                "sheet_index": 0,
                "schedule": "day_night",
                "total_days": 20,
                "acs_codes": ["AM.I.B.K1"],
                "topics": ["Aircraft Drawings"],
                "summary": "Block 2 drawings schedule.",
                "tagging_status": "ok",
                "document_type": "course_calendar",
            },
        }],
    }
    doc = build_clean_content_document(payload)
    meta = doc["content_units"][0]["metadata"]
    assert meta["tagging_status"] == "ok"
    assert meta["topics"] == ["Aircraft Drawings"]
    assert meta["summary"].startswith("Block 2")
    assert meta["sheet_name"] == "Block 2 (Day-Night)"
