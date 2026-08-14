"""Prompt-guidance distillation for the block-wide digest pipeline
(promptops_app.services.prompt_guidance).

Offline + deterministic — no DB/LLM calls (build_prompt/generate_text are
monkeypatched). Covers: override-text takes priority over registry resolution,
any resolution failure degrades to "" (never raises), the digestion call's
"NONE"/error/empty outputs all collapse to "", and the top-level function is
exception-safe for every failure EXCEPT a quota breach, which must reach the 402
handler rather than silently produce guidance-free output (see the two tests at
the end of this file).
"""
from __future__ import annotations

import types

import pytest

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
    # The cap is configuration now (PROMPT_GUIDANCE_MAX_CHARS), not a module
    # constant — the old hardcoded 2500 silently discarded most of a real prompt's
    # distilled guidance. An explicit override proves the setting is honoured
    # rather than just re-reading whatever the default happens to be.
    from app.core.config import settings
    assert len(result) <= settings.prompt_guidance_max_chars

    monkeypatch.setattr(settings, "prompt_guidance_max_chars", 100)
    assert len(pg._digest_prompt("some prompt text", "model")) <= 100


def test_digest_prompt_returns_checklist_verbatim_on_success(monkeypatch):
    monkeypatch.setattr("promptops_app.services.llm_service.generate_text",
                        lambda *a, **k: "1. Emphasize safety.\n2. Cite the specific AC number.")
    result = pg._digest_prompt("some prompt text", "model")
    assert "Emphasize safety." in result and "Cite the specific AC number." in result


def test_resolve_prompt_guidance_end_to_end_with_override(monkeypatch):
    monkeypatch.setattr("promptops_app.services.llm_service.generate_text",
                        lambda model_choice, system, user, usage_ctx=None:
                        "1. Name the AC number explicitly.")
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


# --------------------------------------------------------------------------- #
# Fidelity — a long DB-maintained prompt must not be silently truncated.
#
# The caps were previously hardcoded at 6000 input / 2500 output / 12 items. AIM's
# own Block 2 CDD template is ~15.7k chars, so ~60% of the admin's instructions
# never reached the distiller and nothing said so. These tests pin the two
# properties that fix: windows instead of truncating, and log when capped.
# --------------------------------------------------------------------------- #
def test_long_prompt_is_windowed_not_truncated(monkeypatch):
    """Every part of an over-cap prompt must reach some distillation call."""
    from app.core.config import settings
    pg.reset_cache()
    monkeypatch.setattr(settings, "prompt_guidance_max_prompt_chars", 1000)
    monkeypatch.setattr(settings, "prompt_guidance_max_windows", 8)

    head, tail = "HEAD_MARKER", "TAIL_MARKER"
    long_prompt = head + ("x" * 3000) + tail
    seen = []

    def capture(model, system, user, usage_ctx=None):
        seen.append(user)
        return "1. Something substantive."

    monkeypatch.setattr("promptops_app.services.llm_service.generate_text", capture)
    pg._digest_prompt(long_prompt, "model")

    assert len(seen) > 1, "an over-cap prompt must be windowed, not sent as one call"
    joined = "".join(seen)
    assert head in joined and tail in joined, "windowing dropped part of the prompt"


def test_windows_overlap_so_a_boundary_spanning_instruction_survives(monkeypatch):
    """Windowing must not introduce its own fidelity loss at the seams."""
    from app.core.config import settings
    pg.reset_cache()
    monkeypatch.setattr(settings, "prompt_guidance_max_prompt_chars", 1000)
    windows, covered = pg._windows("y" * 2500, 1000, 8)
    assert len(windows) >= 3
    assert covered == 2500        # full coverage when max_windows allows it
    # Consecutive windows must share text, or an instruction straddling the cut
    # would be seen only in fragments.
    assert windows[0][-pg._WINDOW_OVERLAP_CHARS:] == windows[1][:pg._WINDOW_OVERLAP_CHARS]


def test_over_cap_input_is_logged_not_silent(monkeypatch, caplog):
    """If the windows genuinely cannot cover the prompt, that must be visible."""
    from app.core.config import settings
    pg.reset_cache()
    monkeypatch.setattr(settings, "prompt_guidance_max_prompt_chars", 100)
    monkeypatch.setattr(settings, "prompt_guidance_max_windows", 1)
    monkeypatch.setattr("promptops_app.services.llm_service.generate_text",
                        lambda *a, **k: "1. Item.")
    with caplog.at_level("WARNING"):
        pg._digest_prompt("z" * 5000, "model")
    assert any("prompt_guidance_input_capped" in r.message for r in caplog.records)


def test_windows_merge_into_one_consistently_numbered_list(monkeypatch):
    """Each window numbers from 1; concatenating them raw yields duplicate
    indices that read as a malformed list to the downstream model."""
    merged = pg._renumber(["1. Alpha.", "2. Beta.", "1. Alpha.", "2. Gamma."])
    assert merged == "1. Alpha.\n2. Beta.\n3. Gamma."   # deduped and renumbered


def test_short_prompt_still_makes_exactly_one_call(monkeypatch):
    """The common case must be byte-identical to the pre-windowing behaviour."""
    pg.reset_cache()
    calls = []
    monkeypatch.setattr("promptops_app.services.llm_service.generate_text",
                        lambda m, s, u, usage_ctx=None: calls.append(u) or "1. Item.")
    pg._digest_prompt("a short prompt", "model")
    assert len(calls) == 1
    assert calls[0] == "GENERATION PROMPT TEMPLATE:\na short prompt"


def test_item_cap_is_configurable_and_reaches_the_system_prompt(monkeypatch):
    from app.core.config import settings
    pg.reset_cache()
    monkeypatch.setattr(settings, "prompt_guidance_max_items", 30)
    systems = []
    monkeypatch.setattr("promptops_app.services.llm_service.generate_text",
                        lambda m, s, u, usage_ctx=None: systems.append(s) or "1. Item.")
    pg._digest_prompt("prompt", "model")
    assert "at most 30 items" in systems[0]


# --------------------------------------------------------------------------- #
# Cost — the distillation is memoized per prompt content.
# --------------------------------------------------------------------------- #
def test_identical_prompt_is_distilled_once(monkeypatch):
    pg.reset_cache()
    calls = []
    monkeypatch.setattr("promptops_app.prompts.prompt_builder.build_prompt",
                        lambda *a, **k: ("sys", "real instructions", "n", "v"))
    monkeypatch.setattr("promptops_app.services.llm_service.generate_text",
                        lambda *a, **k: calls.append(1) or "1. Item.")
    req = types.SimpleNamespace(course_id=1, project_id=1, prompt_id=None, model_choice="m")
    first = pg.resolve_prompt_guidance(db=None, request_body=req, deliverable="cdd", current_user=None)
    second = pg.resolve_prompt_guidance(db=None, request_body=req, deliverable="cdd", current_user=None)
    assert first == second == "1. Item."
    assert len(calls) == 1, "the memo must spare a repeat generation the distillation call"


def test_edited_prompt_misses_the_memo(monkeypatch):
    """Content-keyed, so an admin's edit takes effect with no invalidation step."""
    pg.reset_cache()
    text = {"v": "first version of the instructions"}
    calls = []
    monkeypatch.setattr("promptops_app.prompts.prompt_builder.build_prompt",
                        lambda *a, **k: ("sys", text["v"], "n", "v"))
    monkeypatch.setattr("promptops_app.services.llm_service.generate_text",
                        lambda m, s, u, usage_ctx=None: calls.append(u) or f"1. From {len(calls)}.")
    req = types.SimpleNamespace(course_id=1, project_id=1, prompt_id=None, model_choice="m")
    pg.resolve_prompt_guidance(db=None, request_body=req, deliverable="cdd", current_user=None)
    text["v"] = "a materially edited set of instructions"
    pg.resolve_prompt_guidance(db=None, request_body=req, deliverable="cdd", current_user=None)
    assert len(calls) == 2


def test_memo_is_bounded(monkeypatch):
    """An unbounded memo in a long-lived worker is a leak."""
    from app.core.config import settings
    pg.reset_cache()
    monkeypatch.setattr(settings, "prompt_guidance_cache_size", 3)
    monkeypatch.setattr("promptops_app.services.llm_service.generate_text",
                        lambda *a, **k: "1. Item.")
    for i in range(10):
        pg._cache_put(pg._cache_key(f"prompt {i}", "m", 12, 2500), "x", 3)
    assert len(pg._CACHE) == 3


# --------------------------------------------------------------------------- #
# Distiller calibration.
#
# Caught live against AIM's real ~15.5k-char Block 2 CDD template: the original
# system prompt told the distiller to ignore "output-format mechanics" and
# "generic boilerplate", so it classified that template's governance rules,
# review-flag vocabulary, and per-worksheet criteria as format mechanics and
# returned exactly "NONE" — zero guidance for a prompt dense with real judgment
# criteria, with no error. The recalibrated prompt yields 24 grounded items.
#
# These assert the *properties* that fix depends on, so a future "simplification"
# of the wording can't silently reintroduce the failure. They need no LLM call.
# --------------------------------------------------------------------------- #
def _digest_system() -> str:
    return pg._DIGEST_SYSTEM_TEMPLATE.format(max_items=24)


def test_distiller_does_not_broadly_exclude_boilerplate_or_format():
    """The regression itself: a broad 'ignore boilerplate / output-format
    mechanics' exclusion is what made the distiller discard real criteria."""
    system = _digest_system().lower()
    assert "generic boilerplate" not in system, (
        "broad boilerplate exclusion reintroduced — this is what returned NONE "
        "on a real 15.5k-char course prompt"
    )
    # A narrow, itemised exclusion is fine; a bare 'ignore output-format' is not.
    assert "output-format mechanics" not in system


def test_distiller_forbids_dismissing_rules_as_boilerplate():
    """The load-bearing clause: a rule is substantive even when phrased as a rule
    or concerned with document structure."""
    system = _digest_system().lower()
    assert "do not dismiss" in system
    assert "boilerplate merely because" in system


def test_distiller_enumerates_the_categories_to_extract():
    """Naming the categories is what makes a governance-style prompt extractable;
    a single vague 'judgment instructions' ask is what under-triggered."""
    system = _digest_system().lower()
    for expected in ("controlled vocabulary", "distinguishes", "evidence",
                     "level of detail", "tone"):
        assert expected in system, f"distiller no longer asks for {expected!r}"


def test_distiller_treats_none_as_a_narrow_escape_hatch():
    """NONE must be reserved for a schema-only template, not offered as the
    default answer whenever nothing looks 'substantive'."""
    system = _digest_system().lower()
    assert "reserve the single word none" in system
    assert "only schema and placeholders" in system


def test_distiller_honours_the_configured_item_cap():
    assert "at most 24 items" in _digest_system()
    assert "at most 7 items" in pg._DIGEST_SYSTEM_TEMPLATE.format(max_items=7)


# --------------------------------------------------------------------------- #
# Budget enforcement reached this call the moment it was attributed (2026-08-14)
# --------------------------------------------------------------------------- #

def test_a_quota_breach_is_not_swallowed_into_silently_worse_output(monkeypatch):
    """Attributing the distillation call also brought it under budget enforcement:
    check_budget keys off the project/course/user ids the usage context supplies,
    and before there was no context, so _levels_for returned no levels and the call
    was never checked.

    That made the module's catch-all newly dangerous. A quota breach would collapse
    to "" and the block would generate WITHOUT its guidance — quietly worse output,
    reported to the user as success. generate_with_metadata deliberately re-raises
    this one exception ("a quota breach is not 'try again later'") so it lands on
    app/main.py's 402 handler; swallowing it here would undo that.
    """
    from promptops_app.services.budget_service import BudgetExceededError

    def over_budget(*a, **k):
        raise BudgetExceededError("project", "23", limit_usd=10.0, current_spend=12.5)

    monkeypatch.setattr("promptops_app.prompts.prompt_builder.build_prompt",
                        lambda *a, **k: ("sys", "real instructions", "n", "v"))
    monkeypatch.setattr("promptops_app.services.llm_service.generate_text", over_budget)
    pg.reset_cache()

    req = types.SimpleNamespace(course_id=48, project_id=23, prompt_id=None,
                                model_choice="m", block="Block 2")
    with pytest.raises(BudgetExceededError):
        pg.resolve_prompt_guidance(db=None, request_body=req, deliverable="cdd",
                                   current_user=types.SimpleNamespace(username="platformadmin"))


def test_every_other_failure_still_degrades_to_empty(monkeypatch):
    """The re-raise must be surgical. This feature is additive, and any ordinary
    failure must still leave the pipeline running exactly as it did before it
    existed — otherwise the fix above turns a nicety into a new outage path."""
    monkeypatch.setattr("promptops_app.prompts.prompt_builder.build_prompt",
                        lambda *a, **k: ("sys", "real instructions", "n", "v"))
    monkeypatch.setattr("promptops_app.services.llm_service.generate_text",
                        lambda *a, **k: (_ for _ in ()).throw(ConnectionError("Bedrock unreachable")))
    pg.reset_cache()

    req = types.SimpleNamespace(course_id=48, project_id=23, prompt_id=None,
                                model_choice="m", block="Block 2")
    assert pg.resolve_prompt_guidance(db=None, request_body=req, deliverable="cdd",
                                      current_user=None) == ""
