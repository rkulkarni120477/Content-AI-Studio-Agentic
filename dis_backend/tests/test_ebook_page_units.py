"""Agent + content.json tests for ebook_reference page chunking."""
from __future__ import annotations

from services.agents.content_unit_creation_agent import ContentUnitCreationAgent
from services.source_library import build_clean_content_document, chunking_strategy


class _FakeCtx:
    def __init__(self):
        from config.settings import get_tenant_config
        self.cfg = get_tenant_config("aim")
        # Force mock provider so the agent does not call Bedrock in unit tests.
        self.cfg.pipeline.llm_provider = "mock"
        self.cfg.pipeline.bedrock_enabled = False
        self.guard = None

    def step_done(self, state, name):
        return state


def _agent():
    agent = ContentUnitCreationAgent.__new__(ContentUnitCreationAgent)
    agent.ctx = _FakeCtx()
    return agent


def test_ebook_reference_produces_page_units_from_page_texts():
    agent = _agent()
    state = {
        "job_id": "j1",
        "doc_type": "ebook_reference",
        "filename": "8083-31B.pdf",
        "file_type": "pdf",
        "doc_metadata": {"document_type": "ebook_reference", "content_type": "ebook_reference",
                         "title": "Airframe"},
        "page_texts": [
            {"pdf_page": 1, "text": "Chapter 13\nLanding gear\n13-1\n"},
            {"pdf_page": 2, "text": "Oleo strut\n13-2\n"},
        ],
        "page_count": 2,
        "raw_text": "",
        "raw_bytes": b"",  # empty → use page_texts path
    }
    units = agent.run(state)["content_units"]
    assert len(units) == 2
    assert units[0]["content_unit_id"] == "j1:page_1"
    assert units[0]["metadata"]["page_number"] == "13-1"
    assert units[0]["metadata"]["chunking_strategy"] == "page"
    assert units[1]["metadata"]["chapter"] == 13


def test_lesson_pdf_still_word_chunks():
    agent = _agent()
    words = " ".join(f"w{i}" for i in range(600))
    state = {
        "job_id": "j2",
        "doc_type": "lesson_pdf",
        "filename": "lesson.pdf",
        "file_type": "pdf",
        "doc_metadata": {"document_type": "lesson_pdf", "content_type": "lesson_pdf"},
        "raw_text": words,
        "slide_texts": [],
    }
    units = agent.run(state)["content_units"]
    assert units
    assert units[0]["content_unit_id"].startswith("j2:unit_")
    assert "page_number" not in (units[0].get("metadata") or {})


def test_chunking_strategy_page_for_ebook():
    assert chunking_strategy("", "ebook_reference") == "page"
    assert chunking_strategy("course_generation", "ebook_reference") == "page"


def test_content_json_keeps_page_metadata():
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
                "chapter": 13, "page_number": "13-1", "printed_page": 1,
                "pdf_page": 100, "acs_codes": ["AM.I.D.K1"],
                "topics": ["landing gear"], "summary": "Intro.",
                "chunking_strategy": "page",
                "raw_s3_key": "secret",  # must be stripped
            },
        }],
    })
    assert doc["chunking_strategy"] == "page"
    assert len(doc["content_units"]) == 1
    md = doc["content_units"][0]["metadata"]
    assert md["page_number"] == "13-1"
    assert md["chapter"] == 13
    assert md["acs_codes"] == ["AM.I.D.K1"]
    assert md["summary"] == "Intro."
    assert "raw_s3_key" not in md


def test_syllabus_still_full_document():
    doc = build_clean_content_document({
        "job_id": "j",
        "metadata": {"document_type": "syllabus", "title": "Syllabus"},
        "source_file": {"name": "s.docx", "type": "docx"},
        "content_units": [
            {"content_unit_id": "j:1", "unit_type": "x", "unit_number": 1,
             "title": "a", "text": "part one"},
            {"content_unit_id": "j:2", "unit_type": "x", "unit_number": 2,
             "title": "b", "text": "part two"},
        ],
    })
    assert len(doc["content_units"]) == 1
    assert doc["content_units"][0]["unit_type"] == "full_document"
