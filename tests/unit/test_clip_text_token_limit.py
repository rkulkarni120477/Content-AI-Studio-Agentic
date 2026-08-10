"""P6.1 — token-based context truncation (F11).

Truncation is token-based and ON by default. `PROMPTOPS_TOKEN_LIMIT_ENABLED` is a
master on/off:
  * enabled (default) → `clip_tokens` trims to `max_tokens` real tokens (tiktoken).
  * false             → no truncation at all; the full text is returned.

`max_tokens <= 0` means no cap. If tiktoken is unavailable (or encoding fails),
`clip_tokens` degrades to a character trim at `max_tokens * chars_per_token`
characters — it can never break generation. `clip_text` remains a pure
character-based utility.

Logic tests use a fake 1-token-per-character encoder so they're deterministic and
independent of whether tiktoken is installed; a final test exercises the real
tiktoken path when available.
"""

from __future__ import annotations

import pytest

from promptops_app.core import config

SUFFIX = "\n...[truncated for faster generation]"


class _FakeEnc:
    """Deterministic stand-in for a tiktoken encoder: 1 token per character."""

    def encode(self, s):
        return list(s)

    def decode(self, toks):
        return "".join(toks)


# ── clip_text stays pure character-based (fallback + legacy utility) ──────────

def test_clip_text_is_pure_char():
    assert config.clip_text("z" * 1000, 100) == ("z" * 100) + SUFFIX
    assert config.clip_text("hi", 100) == "hi"
    assert config.clip_text("z" * 1000, 0) == "z" * 1000  # no cap


# ── Enabled (default): truncate by token budget ───────────────────────────────

def test_enabled_truncates_by_token_budget(monkeypatch):
    monkeypatch.setattr(config.settings, "token_limit_enabled", True)
    monkeypatch.setattr(config, "_get_token_encoder", lambda name: _FakeEnc())

    # 1000 chars → 1000 tokens (fake), budget 25 → 25 chars back + suffix.
    out = config.clip_tokens("x" * 1000, 25)
    assert out.endswith(SUFFIX)
    assert out[: -len(SUFFIX)] == "x" * 25


def test_enabled_under_budget_unchanged(monkeypatch):
    monkeypatch.setattr(config.settings, "token_limit_enabled", True)
    monkeypatch.setattr(config, "_get_token_encoder", lambda name: _FakeEnc())

    assert config.clip_tokens("y" * 10, 25) == "y" * 10


def test_enabled_no_cap_when_max_tokens_zero(monkeypatch):
    monkeypatch.setattr(config.settings, "token_limit_enabled", True)
    monkeypatch.setattr(config, "_get_token_encoder", lambda name: _FakeEnc())

    assert config.clip_tokens("x" * 1000, 0) == "x" * 1000


# ── Disabled (false): no truncation at all ────────────────────────────────────

def test_disabled_returns_full_text(monkeypatch):
    monkeypatch.setattr(config.settings, "token_limit_enabled", False)
    # Even a huge input and a tiny budget → returned untouched.
    assert config.clip_tokens("x" * 5000, 10) == "x" * 5000


def test_disabled_does_not_touch_tokenizer(monkeypatch):
    monkeypatch.setattr(config.settings, "token_limit_enabled", False)

    def _boom(name):  # must never be called when disabled
        raise AssertionError("tokenizer consulted while truncation disabled")

    monkeypatch.setattr(config, "_get_token_encoder", _boom)
    assert config.clip_tokens("x" * 5000, 10) == "x" * 5000


# ── Enabled but tokenizer unavailable/erroring: char fallback keeps it bounded ─

def test_tokenizer_unavailable_char_fallback(monkeypatch):
    monkeypatch.setattr(config.settings, "token_limit_enabled", True)
    monkeypatch.setattr(config.settings, "chars_per_token", 4.0)
    monkeypatch.setattr(config, "_get_token_encoder", lambda name: None)

    # budget 25 tokens × 4 chars/token = 100-char fallback trim.
    out = config.clip_tokens("w" * 1000, 25)
    assert out == ("w" * 100) + SUFFIX


def test_encode_error_char_fallback(monkeypatch):
    class _BoomEnc:
        def encode(self, s):
            raise RuntimeError("boom")

        def decode(self, toks):  # pragma: no cover - never reached
            return "".join(toks)

    monkeypatch.setattr(config.settings, "token_limit_enabled", True)
    monkeypatch.setattr(config.settings, "chars_per_token", 4.0)
    monkeypatch.setattr(config, "_get_token_encoder", lambda name: _BoomEnc())

    out = config.clip_tokens("q" * 1000, 25)
    assert out == ("q" * 100) + SUFFIX


# ── Real tiktoken integration (only when installed) ───────────────────────────

def test_real_tiktoken_truncates_within_budget(monkeypatch):
    tiktoken = pytest.importorskip("tiktoken")
    config._get_token_encoder.cache_clear()

    monkeypatch.setattr(config.settings, "token_limit_enabled", True)
    monkeypatch.setattr(config.settings, "token_encoding", "cl100k_base")

    text = "The quick brown fox jumps over the lazy dog. " * 200
    out = config.clip_tokens(text, 100)

    assert out.endswith(SUFFIX)
    enc = tiktoken.get_encoding("cl100k_base")
    assert len(enc.encode(out[: -len(SUFFIX)])) <= 100
