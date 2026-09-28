"""CAS AIM findings, Phase 5 (finding 9): the continuity rules already exist
in the generation prompt, but nothing re-reads the finished multi-section
result to check whether they actually landed. run_continuity_review is that
second look -- these tests pin its skip/run/fail-safe contract.
"""
from __future__ import annotations

from promptops_app.services.continuity_review_service import run_continuity_review


class _FakeResult:
    def __init__(self, text="", is_error=False, truncated=False):
        self.text = text
        self.is_error = is_error
        self.truncated = truncated


def test_skips_short_single_section_content():
    """One heading -- at most one transition, nothing worth reviewing. No
    LLM call at all (proven by never supplying llm_call_fn)."""
    content = "## Only Section\n\nJust one block of text."
    assert run_continuity_review(content) == content


def test_skips_content_with_no_headings():
    content = "Plain text with no structure at all."
    assert run_continuity_review(content) == content


def test_reviews_multi_section_content_and_returns_the_revision():
    # Headings are topic names and stay fixed -- the weak transition text
    # this pass rewrites is the body sentence under each heading, not the
    # heading itself (see the structural check: revised headings must match).
    content = (
        "## Intro\n\nBody.\n\n"
        "## Valves\n\nNext, let's look at valves. Body.\n\n"
        "## Accumulators\n\nNow we cover accumulators. Body."
    )
    revised = "## Intro\n\nBody.\n\n## Valves\n\nBecause valves control flow, we look at them next. Body.\n\n## Accumulators\n\nAccumulators store what valves route. Body."
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


def test_a_truncated_reply_falls_back_to_original_content():
    """PR review (blocker): the rewrite re-emits the whole document at the
    same max_output_tokens and is asked to add transition text, so it's more
    likely to hit the output cap than the primary call was -- a truncated
    reply is missing tail sections and must never be saved as final content."""
    content = "## A\n\nx\n\n## B\n\ny\n\n## C\n\nz"

    def truncated_llm(*a, **kw):
        return _FakeResult(text="## A\n\nx\n\n## B\n\ny", truncated=True)

    assert run_continuity_review(content, llm_call_fn=truncated_llm) == content


def test_a_revision_that_changes_headings_falls_back_to_original_content():
    """PR review (should-fix): 'don't change headings/add or remove sections'
    is only a prompt instruction -- verify the heading lines structurally
    rather than trusting any non-empty reply."""
    content = "## Intro\n\nBody.\n\n## Valves\n\nBody.\n\n## Accumulators\n\nBody."

    def dropped_heading_llm(*a, **kw):
        return _FakeResult(text="## Intro\n\nBody.\n\n## Valves\n\nBody.\n\nBody.")

    assert run_continuity_review(content, llm_call_fn=dropped_heading_llm) == content


def test_a_revision_with_reordered_headings_falls_back_to_original_content():
    content = "## Intro\n\nBody.\n\n## Valves\n\nBody.\n\n## Accumulators\n\nBody."

    def reordered_llm(*a, **kw):
        return _FakeResult(text="## Intro\n\nBody.\n\n## Accumulators\n\nBody.\n\n## Valves\n\nBody.")

    assert run_continuity_review(content, llm_call_fn=reordered_llm) == content


def test_a_revision_with_the_same_headings_in_order_is_accepted():
    content = "## Intro\n\nBody.\n\n## Valves\n\nBody.\n\n## Accumulators\n\nBody."
    revised = "## Intro\n\nBody.\n\n## Valves\n\nBecause valves control flow, they matter here. Body.\n\n## Accumulators\n\nBody."

    def rewritten_llm(*a, **kw):
        return _FakeResult(text=revised)

    assert run_continuity_review(content, llm_call_fn=rewritten_llm) == revised


def test_usage_context_is_forwarded_to_the_llm_call():
    """PR review (should-fix): without a usage_ctx, the call is logged as
    unattributed/direct_call and skips budget checks -- a full-document
    rewrite on every multi-section generation needs the same visibility the
    primary call already gets."""
    from promptops_app.services.usage_service import UsageLogContext

    content = "## A\n\nx\n\n## B\n\ny\n\n## C\n\nz"
    captured = {}

    def capturing_llm(model_choice, system_prompt, user_prompt, usage_ctx=None, **kwargs):
        captured["usage_ctx"] = usage_ctx
        return _FakeResult(text=content)

    ctx = UsageLogContext(entity_type="generation", entity_id="job-123")
    run_continuity_review(content, llm_call_fn=capturing_llm, usage_ctx=ctx)

    assert captured["usage_ctx"] is ctx


def test_the_prompt_forbids_calling_out_specific_days_or_blocks():
    """PR review (should-fix): the findings' own continuity rules include
    'do not cross-reference or call out days' (screens get reused in other
    courses) -- the no-backward-reference rule alone doesn't cover this."""
    from promptops_app.services.continuity_review_service import _CONTINUITY_SYSTEM

    assert "day" in _CONTINUITY_SYSTEM.lower()
    assert "reused" in _CONTINUITY_SYSTEM.lower()
