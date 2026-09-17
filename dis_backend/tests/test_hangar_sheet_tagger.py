"""Tests for hangar sheet / page LLM tagging and retag dispatch."""
from __future__ import annotations

import json

from services.ebook_page_retag import _tag_batch
from services.ebook_page_tagger import TAGGING_FAILED, TAGGING_OK, TAGGING_PENDING
from services.hangar_page_tagger import tag_hangar_page_units
from services.hangar_sheet_tagger import tag_hangar_sheet_units
from services.source_library import build_clean_content_document, chunking_strategy


def _sheet_unit(idx: int, *, acs=None, text="Hangar demo AM.I.B.K1"):
    return {
        "content_unit_id": f"j:hangar_sheet_{idx}",
        "unit_type": "hangar_sheet",
        "unit_number": idx + 1,
        "title": f"Block {idx + 5} (Day-Night)",
        "text": text,
        "topics": ["heuristic"],
        "metadata": {
            "sheet_index": idx,
            "sheet_name": f"Block {idx + 5} (Day-Night)",
            "block": f"Block {idx + 5}",
            "schedule": "day_night",
            "acs_codes": acs or ["AM.I.B.K1"],
            "document_type": "hangar_activity",
        },
    }


def _page_unit(pdf_page: int, *, block="Block 5", day=3):
    return {
        "content_unit_id": f"j:page_{pdf_page}",
        "unit_type": "hangar_page",
        "unit_number": pdf_page,
        "title": f"Hangar Lab — PDF p. {pdf_page}",
        "text": f"Page {pdf_page} shop procedure AM.I.B.K1",
        "topics": ["heuristic"],
        "metadata": {
            "pdf_page": pdf_page,
            "page_number": str(pdf_page),
            "block": block,
            "day_number": day,
            "document_type": "hangar_activity",
            "content_type": "hangar_activity",
            "tagging_status": "pending",
        },
    }


def test_hangar_sheet_tagger_merges_acs():
    units = [_sheet_unit(0, acs=["AM.I.B.K1"])]

    def fake_llm(model, prompt, max_tokens=0):
        assert "hangar" in prompt.lower()
        payload = [{
            "sheet_index": 0,
            "topics": ["Drilling", "Alloys"],
            "acs_codes": ["AM.I.B.K1", "AM.I.B.K2", "BAD"],
            "summary": "Block 5 hangar covers metallic shop demos.",
        }]
        return json.dumps(payload), 10, 20

    tag_hangar_sheet_units(
        units, call_llm_fn=fake_llm, model_id="test-model", batch_size=5, enabled=True,
    )
    meta = units[0]["metadata"]
    assert meta["tagging_status"] == TAGGING_OK
    assert units[0]["topics"] == ["Drilling", "Alloys"]
    assert meta["acs_codes"] == ["AM.I.B.K1", "AM.I.B.K2"]
    assert "metallic" in meta["summary"].lower()


def test_hangar_sheet_tagger_pending_when_disabled():
    units = [_sheet_unit(0)]
    tag_hangar_sheet_units(
        units, call_llm_fn=lambda *a, **k: ("", 0, 0),
        model_id="", enabled=False,
    )
    assert units[0]["metadata"]["tagging_status"] == TAGGING_PENDING


def test_hangar_page_tagger_sets_topics():
    units = [_page_unit(1), _page_unit(2)]

    def fake_llm(model, prompt, max_tokens=0):
        assert "shop" in prompt.lower() or "hangar" in prompt.lower()
        payload = [
            {"pdf_page": 1, "topics": ["Torque"], "acs_codes": ["AM.I.B.K1"],
             "summary": "Torque demo."},
            {"pdf_page": 2, "topics": ["Safety"], "acs_codes": [],
             "summary": "Safety briefing."},
        ]
        return json.dumps(payload), 5, 10

    tag_hangar_page_units(
        units, call_llm_fn=fake_llm, model_id="m", batch_size=10, enabled=True,
    )
    assert units[0]["metadata"]["tagging_status"] == TAGGING_OK
    assert units[0]["topics"] == ["Torque"]
    assert units[1]["topics"] == ["Safety"]


def test_hangar_page_tagger_failed_on_error():
    units = [_page_unit(1)]

    def boom(model, prompt, max_tokens=0):
        raise RuntimeError("bedrock down")

    errors = []
    tag_hangar_page_units(
        units, call_llm_fn=boom, model_id="m", enabled=True, errors=errors,
    )
    assert units[0]["metadata"]["tagging_status"] == TAGGING_FAILED
    assert errors


def test_retag_batch_dispatches_hangar_sheet():
    units = [_sheet_unit(0)]
    calls = []

    def fake_llm(model, prompt, max_tokens=0):
        calls.append("sheet")
        return json.dumps([{
            "sheet_index": 0, "topics": ["Tools"], "acs_codes": [], "summary": "ok",
        }]), 1, 1

    _tag_batch(units, call_llm_fn=fake_llm, model_id="m", batch_size=5, errors=[])
    assert calls == ["sheet"]
    assert units[0]["metadata"]["tagging_status"] == TAGGING_OK


def test_retag_batch_dispatches_hangar_page():
    units = [_page_unit(1)]
    calls = []

    def fake_llm(model, prompt, max_tokens=0):
        calls.append("page")
        return json.dumps([{
            "pdf_page": 1, "topics": ["Rivets"], "acs_codes": [], "summary": "ok",
        }]), 1, 1

    _tag_batch(units, call_llm_fn=fake_llm, model_id="m", batch_size=5, errors=[])
    assert calls == ["page"]
    assert units[0]["metadata"]["tagging_status"] == TAGGING_OK


def test_source_library_page_strategy_for_hangar_pages():
    units = [_page_unit(1), _page_unit(2)]
    assert chunking_strategy("course_generation", "hangar_activity", units=units) == "page"
    doc = build_clean_content_document({
        "job_id": "j",
        "metadata": {
            "document_type": "hangar_activity",
            "title": "B5D3 Hangar Lab",
            "block": "Block 5",
            "day_number": 3,
        },
        "source_file": {"name": "B5D3 Hangar Lab.pdf", "type": "pdf"},
        "content_units": units,
    })
    assert doc["chunking_strategy"] == "page"
    assert len(doc["content_units"]) == 2
    assert doc["content_units"][0]["unit_type"] == "hangar_page"
    assert doc["content_units"][0]["metadata"]["block"] == "Block 5"
    assert doc["content_units"][0]["metadata"]["day_number"] == 3
    assert doc["content_units"][0]["metadata"]["page_number"] == "1"
