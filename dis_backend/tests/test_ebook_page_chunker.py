"""Unit tests for ebook_reference page chunking + location tagging."""
from __future__ import annotations

from services.ebook_page_chunker import (
    build_ebook_page_units,
    parse_page_texts_from_raw,
    tag_pages,
)


def test_parse_page_markers_from_raw_text():
    raw = "[Page 1]\nFront matter\n\n[Page 2]\nChapter 1 intro\n"
    pages = parse_page_texts_from_raw(raw)
    assert [p["pdf_page"] for p in pages] == [1, 2]
    assert "Front matter" in pages[0]["text"]
    assert "Chapter 1" in pages[1]["text"]


def test_header_chapter_and_printed_page():
    pages = [
        {"pdf_page": 1, "text": "Table of Contents\n1-1 Landing Gear\n2-1 Hydraulics\n"},
        {"pdf_page": 2, "text": "Chapter 13\nAircraft Landing Gear Systems\n13-1\n"},
        {"pdf_page": 3, "text": "Oleo strut servicing.\n13-2\n"},
        {"pdf_page": 4, "text": ""},  # blank — skipped later by build
    ]
    tagged = tag_pages(pages)
    assert tagged[1]["chapter"] == 13
    assert tagged[1]["printed_page"] == 1
    assert tagged[1]["page_number"] == "13-1"
    assert tagged[2]["chapter"] == 13  # sticky
    assert tagged[2]["page_number"] == "13-2"


def test_body_cross_refs_do_not_override_sticky_chapter():
    """TOC/body tokens like 'see 12-4' in the middle of the page must not win."""
    pages = [
        {"pdf_page": 10, "text": "Chapter 13\nLanding gear overview\n13-1\n"},
        {
            "pdf_page": 11,
            "text": (
                "See chapter 12 for brakes (12-4).\n"
                "Also figure 11-2 in another section.\n"
                "Main content about oleo struts continues here.\n"
                "13-2\n"
            ),
        },
    ]
    tagged = tag_pages(pages)
    assert tagged[0]["chapter"] == 13
    assert tagged[1]["chapter"] == 13
    assert tagged[1]["page_number"] == "13-2"


def test_outline_map_wins_over_header():
    pages = [
        {"pdf_page": 5, "text": "Some intro without a chapter heading\n"},
        {"pdf_page": 6, "text": "Body of chapter four\n4-1\n"},
    ]
    tagged = tag_pages(pages, outline_map={5: 4})
    assert tagged[0]["chapter"] == 4
    assert tagged[1]["chapter"] == 4
    assert tagged[1]["page_number"] == "4-1"


def test_build_ebook_page_units_skips_blank_and_stamps_ids():
    pages = [
        {"pdf_page": 1, "text": "Chapter 1\nHello\n1-1\n"},
        {"pdf_page": 2, "text": "   \n"},
        {"pdf_page": 3, "text": "More\n1-2\n"},
    ]
    units = build_ebook_page_units(
        job_id="job1",
        pages=pages,
        doc_metadata={"document_type": "ebook_reference", "title": "HB"},
        title="HB",
    )
    assert len(units) == 2
    assert units[0]["content_unit_id"] == "job1:page_1"
    assert units[1]["content_unit_id"] == "job1:page_3"
    assert units[0]["metadata"]["chunking_strategy"] == "page"
    assert units[0]["metadata"]["page_number"] == "1-1"
    assert units[0]["metadata"]["pdf_page"] == 1
    assert units[1]["metadata"]["chapter"] == 1
