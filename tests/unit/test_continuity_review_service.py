"""CAS AIM findings, Phase 5 (finding 9): the continuity rules already exist
in the generation prompt, but nothing re-reads the finished multi-section
result to check whether they actually landed. run_continuity_review is that
second look -- these tests pin its skip/run/fail-safe contract.
"""
from __future__ import annotations

from promptops_app.services.continuity_review_service import run_continuity_review


class _FakeResult:
    def __init__(self, text="", is_error=False):
        self.text = text
        self.is_error = is_error


def test_skips_short_single_section_content():
    """One heading -- at most one transition, nothing worth reviewing. No
    LLM call at all (proven by never supplying llm_call_fn)."""
    content = "## Only Section\n\nJust one block of text."
    assert run_continuity_review(content) == content


def test_skips_content_with_no_headings():
    content = "Plain text with no structure at all."
    assert run_continuity_review(content) == content


def test_reviews_multi_section_content_and_returns_the_revision():
    content = (
        "## Intro\n\nBody.\n\n"
        "## Next, let's look at valves\n\nBody.\n\n"
        "## Now we cover accumulators\n\nBody."
    )
    revised = "## Intro\n\nBody.\n\n## Because valves control flow...\n\nBody.\n\n## Accumulators store what valves route...\n\nBody."
    calls = []

    def fake_llm(model_choice, system_prompt, user_prompt, **kwargs):
        calls.append({"model": model_choice, "system": system_prompt, "user": user_prompt})
        return _FakeResult(text=revised)

    out = run_continuity_review(content, model_choice="GPT-5.4", llm_call_fn=fake_llm)

    assert out == revised
    assert len(calls) == 1
    assert calls[0]["user"] == content
    assert "transition" in calls[0]["system"].lower()


def test_llm_error_falls_back_to_original_content():
    content = "## A\n\nx\n\n## B\n\ny\n\n## C\n\nz"

    def failing_llm(*a, **kw):
        return _FakeResult(is_error=True, text="boom")

    assert run_continuity_review(content, llm_call_fn=failing_llm) == content


def test_llm_exception_falls_back_to_original_content():
    content = "## A\n\nx\n\n## B\n\ny\n\n## C\n\nz"

    def raising_llm(*a, **kw):
        raise RuntimeError("connection reset")

    assert run_continuity_review(content, llm_call_fn=raising_llm) == content


def test_empty_llm_response_falls_back_to_original_content():
    content = "## A\n\nx\n\n## B\n\ny\n\n## C\n\nz"

    def empty_llm(*a, **kw):
        return _FakeResult(text="   ")

    assert run_continuity_review(content, llm_call_fn=empty_llm) == content


def test_empty_content_is_returned_as_is():
    assert run_continuity_review("") == ""
    assert run_continuity_review(None) is None
