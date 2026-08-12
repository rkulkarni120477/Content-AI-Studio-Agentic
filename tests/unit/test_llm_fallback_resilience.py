"""Regression tests for two chained LLM failures caught live on a real block-wide
Blueprint generation: (1) a newer Bedrock model rejecting an explicit
`temperature` outright, and (2) the OpenAI fallback getting a `max_tokens`
sized for the primary Bedrock model and being rejected by OpenAI for exceeding
its own real limit — both together sank the whole call with no surviving
fallback (see promptops_app/core/llm_client.py, promptops_app/services/llm_service.py)."""
from unittest.mock import MagicMock, patch

import pytest


def test_bedrock_retries_without_temperature_when_model_rejects_it():
    from promptops_app.core.llm_client import _invoke_bedrock_with_retry

    client = MagicMock()
    reject = Exception("An error occurred (ValidationException) when calling the "
                       "InvokeModel operation: `temperature` is deprecated for this model.")
    ok_response = MagicMock()
    client.invoke_model.side_effect = [reject, ok_response]

    result = _invoke_bedrock_with_retry(client, "global.anthropic.claude-opus-4-8",
                                        "sys", "user", 32000)

    assert result is ok_response
    assert client.invoke_model.call_count == 2
    first_body, second_body = (c.kwargs["body"] for c in client.invoke_model.call_args_list)
    assert '"temperature"' in first_body
    assert '"temperature"' not in second_body


def test_bedrock_retry_does_not_mask_unrelated_errors():
    from promptops_app.core.llm_client import _invoke_bedrock_with_retry

    client = MagicMock()
    client.invoke_model.side_effect = Exception("Bedrock throttled: too many requests")

    with pytest.raises(Exception, match="throttled"):
        _invoke_bedrock_with_retry(client, "some-model", "sys", "user", 1000)
    assert client.invoke_model.call_count == 1


def test_fallback_caps_max_tokens_to_fallback_models_own_ceiling():
    """Regression: a real run had reduce_model=Claude Opus 4.8 (max_output_tokens
    32000). When Bedrock failed, the OpenAI fallback (gpt-4o, ceiling 16384) was
    called with max_tokens=32000 forwarded unchanged and OpenAI rejected the
    request outright (HTTP 400) before generating anything — losing the
    fallback entirely rather than returning its own fullest possible result."""
    from promptops_app.services import llm_service

    with patch("promptops_app.services.llm_service._call_openai_raw") as mock_call, \
         patch("promptops_app.services.llm_service.settings") as mock_settings:
        mock_settings.openai_api_key = "test-key"
        mock_call.return_value = MagicMock()
        llm_service._invoke_fallback("Claude Opus 5 (Bedrock)", "sys", "user", max_tokens=32000)

    assert mock_call.called
    _, kwargs = mock_call.call_args
    assert kwargs["max_tokens"] == 16384  # gpt-4o's own catalog ceiling, not Opus's 32000


def test_fallback_leaves_max_tokens_untouched_when_already_within_ceiling():
    from promptops_app.services import llm_service

    with patch("promptops_app.services.llm_service._call_openai_raw") as mock_call, \
         patch("promptops_app.services.llm_service.settings") as mock_settings:
        mock_settings.openai_api_key = "test-key"
        mock_call.return_value = MagicMock()
        llm_service._invoke_fallback("Claude Opus 5 (Bedrock)", "sys", "user", max_tokens=2000)

    _, kwargs = mock_call.call_args
    assert kwargs["max_tokens"] == 2000


# Bedrock model IDs confirmed invokable on this AWS account by direct InvokeModel
# in BOTH deploy regions (ap-south-1 and us-east-1).
#
# A valid-looking inference-profile prefix is NOT evidence of availability:
# `global.anthropic.claude-haiku-4-5-...` and `global.anthropic.claude-opus-4-8`
# both carry a correct prefix and both return AccessDeniedException, so neither is
# listed here. Availability is per-region AND per-role.
#
# To extend this set, actually invoke the ID in every deploy region first
# (max_tokens=4 is enough), then add it. Do not add one on the strength of its
# shape, or because Bedrock's ListFoundationModels includes the base model.
VERIFIED_INVOKABLE_BEDROCK_IDS = frozenset({
    "global.anthropic.claude-sonnet-4-5-20250929-v1:0",
})


def test_bedrock_ids_are_inference_profiles_not_bare_on_demand_ids():
    """Caught live: Haiku 4.5 shipped with the bare on-demand ID
    `anthropic.claude-haiku-4-5-...`, which this AWS account cannot invoke —
    Bedrock rejects it with "Invocation of model ID ... with on-demand throughput
    isn't supported. Retry with the ID or ARN of an inference profile."

    This is a NECESSARY-but-not-sufficient check; see the availability test below
    for why the prefix alone proves nothing.
    """
    from promptops_app.core.models import MODEL_CATALOG

    bedrock = [m for m in MODEL_CATALOG if m.provider == "bedrock"]
    assert bedrock, "no Bedrock models in the catalog — did the catalog move?"
    for m in bedrock:
        prefix = m.api_model_id.split(".", 1)[0]
        assert prefix in {"global", "us", "eu", "apac"}, (
            f"{m.display_name} uses the bare on-demand id {m.api_model_id!r}; "
            f"Bedrock requires an inference-profile prefix (e.g. "
            f"global.{m.api_model_id})"
        )


def test_every_default_resolved_model_is_verified_invokable():
    """The models reached WITHOUT an explicit user choice must be known-invokable.

    The earlier version of this guard only checked the ID's prefix, which let two
    unusable models through: Haiku 4.5 (every quality tier's 'draft', and the
    prompt-guidance distiller) and Opus 4.8 ('premium'), both AccessDenied on this
    account in every region. Prefix syntax is not availability.

    Scope is deliberately the DEFAULT paths only — a user explicitly selecting an
    unavailable model from the catalog degrades to the OpenAI fallback, which is
    acceptable and honestly reported. A default that cannot be invoked is not: it
    fails every generation for that tier with nobody having chosen it.
    """
    from promptops_app.core.models import resolve_model, resolve_tier
    from promptops_app.services.prompt_guidance import _FALLBACK_MODEL

    targets = [(f"tier {t!r}", resolve_tier(t).reduce_model)
               for t in ("draft", "standard", "premium")]
    targets.append(("prompt_guidance._FALLBACK_MODEL", _FALLBACK_MODEL))

    for label, display_name in targets:
        model = resolve_model(display_name)
        if model.provider != "bedrock":
            continue
        assert model.api_model_id in VERIFIED_INVOKABLE_BEDROCK_IDS, (
            f"{label} -> {model.display_name} ({model.api_model_id}) is not in the "
            f"verified-invokable set. Invoke it in every deploy region before "
            f"making it a default."
        )


def test_dis_default_text_models_are_verified_invokable():
    """DIS's ModelConfig defaults deserve the same guard, and more urgently: on
    failure call_llm returns a VALID-JSON stub, so an unavailable model there
    produces complete-looking output with every extracted field at its default
    rather than an error. The Claude 3 defaults this replaced were end-of-life
    (Sonnet 3, us-east-1) or provider-legacy and denied everywhere (Haiku 3).
    """
    import sys
    from pathlib import Path
    dis = str(Path(__file__).resolve().parents[2] / "dis_backend")
    if dis not in sys.path:
        sys.path.insert(0, dis)
    from config.settings import ModelConfig

    cfg = ModelConfig()
    text_steps = ("classification", "metadata_extraction", "structure_extraction",
                  "quality_check", "vision", "digest_extraction")
    for step in text_steps:
        assert getattr(cfg, step) in VERIFIED_INVOKABLE_BEDROCK_IDS, (
            f"DIS default {step}={getattr(cfg, step)!r} is not verified invokable; "
            f"call_llm would silently return its stub for every {step} call."
        )


# --------------------------------------------------------------------------- #
# Fallback ordering: same provider before crossing to another vendor
# --------------------------------------------------------------------------- #
def test_sibling_candidates_are_same_provider_and_exclude_the_failed_model():
    from promptops_app.services.llm_service import _sibling_candidates

    sibs = _sibling_candidates("Claude Opus 5 (Bedrock)")
    assert sibs, "an Opus failure must have somewhere to go on Bedrock"
    assert all(m.provider == "bedrock" for m in sibs), "must not cross providers here"
    assert all("opus-5" not in m.api_model_id for m in sibs), "the failed model was retried"
    # Sonnet is the natural substitute for Opus and must be reached first.
    assert "sonnet-4-5" in sibs[0].api_model_id


def test_openai_primary_gets_openai_siblings():
    from promptops_app.services.llm_service import _sibling_candidates
    assert all(m.provider == "openai" for m in _sibling_candidates("GPT-5.4"))


def test_invoke_model_caps_tokens_to_that_models_own_ceiling(monkeypatch):
    """Each candidate has its own real limit; forwarding the primary's value gets a
    400 before any generation rather than a longer answer."""
    from promptops_app.core.models import resolve_model
    from promptops_app.services import llm_service

    seen = {}
    monkeypatch.setattr(llm_service, "_call_bedrock_raw",
                        lambda *a, **kw: seen.update(kw) or MagicMock())
    haiku = resolve_model("Claude Haiku 4.5 (Bedrock)")   # ceiling 16384
    llm_service._invoke_model(haiku, "sys", "user", max_tokens=32000)
    assert seen["max_tokens"] == 16384
    seen.clear()
    llm_service._invoke_model(haiku, "sys", "user", max_tokens=1000)
    assert seen["max_tokens"] == 1000


def test_a_bedrock_failure_tries_bedrock_before_openai(monkeypatch):
    """The regression this ordering fixes: an unavailable model ID sent content to a
    different vendor when a sibling on the same connection would have worked."""
    from promptops_app.services import llm_service

    order = []

    def bedrock_raw(system, user, model_id=None, max_tokens=None):
        order.append(model_id)
        if "opus" in model_id:
            raise llm_service.LLMProviderError("AccessDeniedException for this model")
        resp = MagicMock()
        resp.text, resp.model = "ok", model_id
        resp.prompt_tokens = resp.completion_tokens = 1
        return resp

    def openai_raw(*a, **kw):
        order.append("OPENAI")
        raise AssertionError("crossed providers before exhausting Bedrock")

    monkeypatch.setattr(llm_service, "_call_bedrock_raw", bedrock_raw)
    monkeypatch.setattr(llm_service, "_call_openai_raw", openai_raw)
    monkeypatch.setattr(llm_service._cfg, "llm_fallback_enabled", True)

    result = llm_service.generate_with_metadata(
        "Claude Opus 5 (Bedrock)", "sys", "user", max_tokens=2000)

    assert "OPENAI" not in order, f"jumped providers too early: {order}"
    assert any("sonnet-4-5" in m for m in order), f"never tried Sonnet: {order}"
    assert result.status in ("fallback_success", "retry_success", "success")
