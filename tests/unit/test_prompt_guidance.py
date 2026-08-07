"""Prompt-guidance distillation for the block-wide digest pipeline
(promptops_app.services.prompt_guidance).

Offline + deterministic — no DB/LLM calls (build_prompt/generate_text are
monkeypatched). Covers: override-text takes priority over registry resolution,
any resolution failure degrades to "" (never raises), the digestion call's
"NONE"/error/empty outputs all collapse to "", and the top-level function is
unconditionally exception-safe.
"""
from __future__ import annotations

import types

from promptops_app.services import prompt_guidance as pg


def test_resolve_prompt_text_uses_inline_override_verbatim():
    req = types.SimpleNamespace(system_prompt_override="SYS TEXT", user_prompt_override="USER TEXT")
    text = pg._resolve_prompt_text(db=None, request_body=req, deliverable="cdd")
    assert "SYS TEXT" in text and "USER TEXT" in text


def test_resolve_prompt_text_falls_back_to_registry_when_no_override(monkeypatch):
    def fake_build_prompt(name, variables, **kwargs):
        assert name == "cdd_generation"
        return "registry system", "registry user", "cdd_generation", "v3"
    monkeypatch.setattr("promptops_app.prompts.prompt_builder.build_prompt", fake_build_prompt)

    req = types.SimpleNamespace(course_id=1, project_id=1, prompt_id=None)
    text = pg._resolve_prompt_text(db=None, request_body=req, deliverable="cdd")
    assert "registry system" in text and "registry user" in text


def test_resolve_prompt_text_forwards_a_concrete_prompt_id(monkeypatch):
    """Regression coverage gap flagged by independent adversarial review: every
    prior test used prompt_id=None, so a bug that dropped or renamed this field
    somewhere along BlockWideGenerateRequest -> job params -> _reconstruct_request
    -> resolve_prompt_guidance -> build_prompt's kwargs would not have been
    caught. Pins the concrete value all the way to build_prompt's call."""
    captured = {}

    def fake_build_prompt(name, variables, **kwargs):
        captured["prompt_id"] = kwargs.get("prompt_id")
        return "s", "u", name, "v1"
    monkeypatch.setattr("promptops_app.prompts.prompt_builder.build_prompt", fake_build_prompt)

    req = types.SimpleNamespace(course_id=1, project_id=1, prompt_id=77)
    pg._resolve_prompt_text(db=None, request_body=req, deliverable="cdd")
    assert captured["prompt_id"] == 77


def test_resolve_prompt_text_degrades_to_empty_on_any_resolution_failure(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("template not found")
    monkeypatch.setattr("promptops_app.prompts.prompt_builder.build_prompt", boom)

    req = types.SimpleNamespace(course_id=1, project_id=1, prompt_id=None)
    assert pg._resolve_prompt_text(db=None, request_body=req, deliverable="cdd") == ""


def test_resolve_prompt_text_picks_blueprint_template_for_blueprint_deliverable(monkeypatch):
    captured = {}

    def fake_build_prompt(name, variables, **kwargs):
        captured["name"] = name
        return "s", "u", name, "v1"
    monkeypatch.setattr("promptops_app.prompts.prompt_builder.build_prompt", fake_build_prompt)

    req = types.SimpleNamespace(course_id=1, project_id=1, prompt_id=None)
    pg._resolve_prompt_text(db=None, request_body=req, deliverable="blueprint")
    assert captured["name"] == "blueprint_generation"


def test_digest_prompt_returns_empty_for_none_response(monkeypatch):
    monkeypatch.setattr("promptops_app.services.llm_service.generate_text", lambda *a, **k: "NONE")
    assert pg._digest_prompt("some prompt text", "model") == ""


def test_digest_prompt_returns_empty_for_error_response(monkeypatch):
    monkeypatch.setattr("promptops_app.services.llm_service.generate_text",
                        lambda *a, **k: "ERROR: LLM unavailable")
    assert pg._digest_prompt("some prompt text", "model") == ""


def test_digest_prompt_returns_empty_for_blank_prompt_text():
    assert pg._digest_prompt("   ", "model") == ""


def test_digest_prompt_caps_output_length(monkeypatch):
    long_text = "1. Item. " * 2000
    monkeypatch.setattr("promptops_app.services.llm_service.generate_text",
                        lambda *a, **k: long_text)
    result = pg._digest_prompt("some prompt text", "model")
    assert len(result) <= pg._MAX_GUIDANCE_CHARS


def test_digest_prompt_returns_checklist_verbatim_on_success(monkeypatch):
    monkeypatch.setattr("promptops_app.services.llm_service.generate_text",
                        lambda *a, **k: "1. Emphasize safety.\n2. Cite the specific AC number.")
    result = pg._digest_prompt("some prompt text", "model")
    assert "Emphasize safety." in result and "Cite the specific AC number." in result


def test_resolve_prompt_guidance_end_to_end_with_override(monkeypatch):
    monkeypatch.setattr("promptops_app.services.llm_service.generate_text",
                        lambda model_choice, system, user: "1. Name the AC number explicitly.")
    req = types.SimpleNamespace(
        system_prompt_override="You write CDDs.",
        user_prompt_override="Always cite the AC number.",
        model_choice="Claude Sonnet 4.5 (Bedrock)",
    )
    result = pg.resolve_prompt_guidance(db=None, request_body=req, deliverable="cdd", current_user=None)
    assert "Name the AC number explicitly." in result


def test_resolve_prompt_guidance_returns_empty_when_nothing_configured(monkeypatch):
    """Proves the empty-template case short-circuits BEFORE the LLM digestion
    call, not merely that the end result happens to be "" — a spy that raises
    if generate_text is called is the only way to prove that, since a real
    (unmocked) LLM call failing gracefully could otherwise also collapse to ""
    and mask a broken short-circuit."""
    def _fail_if_called(*a, **k):
        raise AssertionError("generate_text must not be called — nothing was configured")

    monkeypatch.setattr("promptops_app.prompts.prompt_builder.build_prompt",
                        lambda *a, **k: ("", "", "n", "v"))
    monkeypatch.setattr("promptops_app.services.llm_service.generate_text", _fail_if_called)
    req = types.SimpleNamespace(course_id=1, project_id=1, prompt_id=None, model_choice="m")
    assert pg.resolve_prompt_guidance(db=None, request_body=req, deliverable="cdd", current_user=None) == ""


def test_resolve_prompt_guidance_never_raises_even_on_unexpected_exception(monkeypatch):
    """The digestion LLM call itself failing outright (not just returning an
    'ERROR: ...' string) must not propagate — this feature is additive-only
    and must never be the reason a block-wide generation request fails."""
    def raises(*a, **k):
        raise ConnectionError("Bedrock unreachable")
    monkeypatch.setattr("promptops_app.prompts.prompt_builder.build_prompt",
                        lambda *a, **k: ("sys", "some real instructions here", "n", "v"))
    monkeypatch.setattr("promptops_app.services.llm_service.generate_text", raises)

    req = types.SimpleNamespace(course_id=1, project_id=1, prompt_id=None, model_choice="m")
    result = pg.resolve_prompt_guidance(db=None, request_body=req, deliverable="cdd", current_user=None)
    assert result == ""
