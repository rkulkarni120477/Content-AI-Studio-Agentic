"""Tests for the page-body HTML → markdown converter (Session 2)."""

from __future__ import annotations

from promptops_app.importers.html_to_markdown import page_html_to_markdown
from tests.importers.fixtures import CAS_WIKI_PAGE


def test_extracts_title():
    title, _markdown, _raw = page_html_to_markdown(CAS_WIKI_PAGE)
    assert title == "Getting Started"


def test_strips_locked_style_block():
    _title, markdown, raw = page_html_to_markdown(CAS_WIKI_PAGE)
    # The locked CSS must not survive into either markdown or the raw body.
    assert "font-family" not in markdown
    assert "<style" not in raw.lower()


def test_unwraps_cas_lesson_and_keeps_content():
    _title, markdown, raw = page_html_to_markdown(CAS_WIKI_PAGE)
    assert "cas-lesson" not in raw            # wrapper unwrapped
    assert "Welcome to the course" in markdown
    assert "First point" in markdown
    assert "Second point" in markdown


def test_raw_body_html_is_a_fidelity_fragment():
    _title, _markdown, raw = page_html_to_markdown(CAS_WIKI_PAGE)
    # Fragment, not a full document — no <html>/<body> shell, tags preserved.
    assert "<html" not in raw.lower()
    assert "<body" not in raw.lower()
    assert "<p>" in raw.lower()


def test_empty_input_is_safe():
    title, markdown, raw = page_html_to_markdown("")
    assert (title, markdown, raw) == ("", "", "")
