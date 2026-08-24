"""A token cap that an unrelated global flag cannot switch off.

``clip_tokens`` is governed by ``PROMPTOPS_TOKEN_LIMIT_ENABLED``, which defaults
to False and is unset in this deployment — so it returns its input untouched.
That is the intended behaviour for a cost/quality preference ("send the full
source, accept the cost") and the wrong behaviour for a safety bound.

The distinction is not theoretical. A mis-tagged reference PDF in the AIM corpus
is 646 units and ~79,000 tokens; a cap meant to stop that reaching a prompt has
to hold regardless of how anyone has configured truncation.
"""

from promptops_app.core.config import clip_tokens, clip_tokens_strict, count_tokens, settings


def test_the_soft_clip_is_a_noop_in_this_deployment():
    """Documents the state that made the strict variant necessary. If this ever
    fails, the flag has been turned on and the budgets elsewhere became real."""
    assert settings.token_limit_enabled is False
    long_text = "word " * 5000
    assert clip_tokens(long_text, 10) == long_text


def test_the_strict_clip_holds_with_the_flag_off():
    long_text = "word " * 5000
    clipped = clip_tokens_strict(long_text, 100)
    assert count_tokens(clipped) <= 100
    assert len(clipped) < len(long_text)


def test_text_under_the_cap_is_returned_unchanged():
    assert clip_tokens_strict("short text", 1000) == "short text"


def test_a_non_positive_cap_means_no_cap():
    text = "word " * 100
    assert clip_tokens_strict(text, 0) == text
    assert clip_tokens_strict(text, -5) == text


def test_empty_input_is_safe():
    assert clip_tokens_strict("", 10) == ""
    assert clip_tokens_strict(None, 10) == ""


def test_it_clips_to_the_start_not_the_end():
    """A truncated document must still read from its beginning — that is what
    makes a partial syllabus useful rather than confusing."""
    text = "FIRST " + "filler " * 2000 + " LAST"
    clipped = clip_tokens_strict(text, 50)
    assert clipped.startswith("FIRST")
    assert "LAST" not in clipped


def test_it_degrades_to_characters_when_the_encoder_is_unavailable(monkeypatch):
    import promptops_app.core.config as cfg
    monkeypatch.setattr(cfg, "_get_token_encoder", lambda *a, **k: None)
    clipped = cfg.clip_tokens_strict("x" * 10_000, 10)
    assert 0 < len(clipped) < 10_000


def test_an_encoder_that_raises_still_bounds_the_output(monkeypatch):
    """A tokenizer problem must never turn a bound into no bound."""
    import promptops_app.core.config as cfg

    class Boom:
        def encode(self, text):
            raise RuntimeError("tokenizer exploded")

    monkeypatch.setattr(cfg, "_get_token_encoder", lambda *a, **k: Boom())
    clipped = cfg.clip_tokens_strict("y" * 10_000, 10)
    assert 0 < len(clipped) < 10_000
