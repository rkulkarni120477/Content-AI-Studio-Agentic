"""A mock LLM must never be mistaken for a working one.

``call_llm`` serves canned replies when the environment looks like an
uncredentialled dev box. On 2026-08-13 prod matched that condition — ``environment``
defaults to "development" and the DIS container could not see any credentials — so
every MAP call returned the literal
``{"doc_type":"study_material","classification":"internal"}``. No model was ever
contacted, a whole 20-day block was built from that string, and the pipeline
reported success.

``preflight_extractor`` exists to stop exactly that, and it did not fire: it refuses
a build when the probe reports zero input tokens, and the mock claimed 400. These
tests pin both halves — the mock is detectable, and it reports zero.

They also pin the other direction, which cost dev a day on 2026-08-27: "no explicit
key in settings" is NOT "no credentials". With only a region in the Bedrock kwargs,
boto3 resolves the instance role, so a box that invokes Bedrock perfectly well must
never be flipped onto canned replies for holding its credentials somewhere else.
"""
from __future__ import annotations

import types

import pytest

from services.digests import build as build_mod
from services.pipeline import common


def _settings(*, environment="development", anthropic=None, bedrock_kwargs=None,
              aws_key=None):
    return types.SimpleNamespace(
        environment=environment,
        anthropic_api_key=anthropic,
        aws_access_key_id=aws_key,
        bedrock_client_kwargs=lambda: dict(bedrock_kwargs or {}),
    )


@pytest.fixture(autouse=True)
def no_ambient_credentials(monkeypatch, request):
    """No instance role / ~/.aws by default.

    Autouse because the answer would otherwise depend on the machine running the
    tests: a developer laptop with ~/.aws, or CI on an EC2 runner, resolves real
    credentials and every "is mocked" assertion below would invert. Tests that
    exercise the probe itself opt out with @pytest.mark.real_ambient_probe.
    """
    if request.node.get_closest_marker("real_ambient_probe"):
        return
    monkeypatch.setattr(common, "ambient_aws_credentials", lambda: False)


def test_no_credentials_anywhere_is_mocked():
    assert common.llm_is_mocked(_settings()) is True


def test_the_bedrock_only_credential_pair_counts_as_real_credentials():
    """bedrock_client_kwargs() prefers DIS_BEDROCK_* over AWS_*. Reading
    aws_access_key_id directly meant a deployment that set ONLY the Bedrock pair kept
    serving mock replies while holding credentials it never used."""
    s = _settings(bedrock_kwargs={"aws_access_key_id": "AKIA-bedrock-only"}, aws_key=None)
    assert common.llm_is_mocked(s) is False


def test_shared_aws_credentials_also_count():
    s = _settings(bedrock_kwargs={"aws_access_key_id": "AKIA-shared"}, aws_key="AKIA-shared")
    assert common.llm_is_mocked(s) is False


def test_an_instance_role_counts_as_real_credentials(monkeypatch):
    """The dev box's shape: no keys in dis_backend/.env, ENVIRONMENT=development, and
    Bedrock reached through the EC2 instance role. Treating that as "no model" refused
    credentials that work and served canned filler in their place."""
    monkeypatch.setattr(common, "ambient_aws_credentials", lambda: True)
    assert common.llm_is_mocked(_settings()) is False


def test_region_only_kwargs_still_consult_the_ambient_chain(monkeypatch):
    """bedrock_client_kwargs() returns region_name alone when no key is configured —
    which is 'let boto3 resolve it', not 'there is nothing to resolve'."""
    monkeypatch.setattr(common, "ambient_aws_credentials", lambda: True)
    s = _settings(bedrock_kwargs={"region_name": "us-east-1"})
    assert common.llm_is_mocked(s) is False


@pytest.mark.real_ambient_probe
def test_the_ambient_probe_is_resolved_once_per_process(monkeypatch):
    """It costs an IMDS round trip on EC2 and llm_is_mocked runs on every call_llm."""
    common.reset_ambient_credential_probe()
    calls = []

    class _Session:
        def get_credentials(self):
            calls.append(1)
            return object()

    # Patched as an attribute of the package, not in sys.modules: `import
    # botocore.session` resolves `botocore.session` through the already-imported
    # parent package, so a sys.modules entry alone is ignored.
    import botocore
    import botocore.session  # noqa: F401 — ensure the real submodule is bound first
    monkeypatch.setattr(botocore, "session",
                        types.SimpleNamespace(get_session=lambda: _Session()))
    try:
        assert common.ambient_aws_credentials() is True
        assert common.ambient_aws_credentials() is True
        assert len(calls) == 1
    finally:
        common.reset_ambient_credential_probe()


@pytest.mark.real_ambient_probe
def test_an_unresolvable_chain_counts_as_absent(monkeypatch):
    """A probe that raises must not read as 'credentials present' — the mock exists
    for a box that genuinely cannot call a model."""
    common.reset_ambient_credential_probe()
    import botocore
    import botocore.session  # noqa: F401
    monkeypatch.setattr(botocore, "session", types.SimpleNamespace(
        get_session=lambda: (_ for _ in ()).throw(RuntimeError("no IMDS"))))
    try:
        assert common.ambient_aws_credentials() is False
    finally:
        common.reset_ambient_credential_probe()


def test_production_is_never_mocked_even_without_credentials():
    """A credential-less production box must fail loudly, not fabricate answers."""
    assert common.llm_is_mocked(_settings(environment="production")) is False


def test_an_anthropic_key_alone_disables_the_mock():
    assert common.llm_is_mocked(_settings(anthropic="sk-ant-xxx")) is False


def test_a_settings_object_of_an_unexpected_shape_does_not_flip_to_mock():
    """Falling back must keep the old behaviour rather than silently mocking a
    credentialled deployment."""
    broken = types.SimpleNamespace(
        environment="development", anthropic_api_key=None,
        aws_access_key_id="AKIA-real",
        bedrock_client_kwargs=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert common.llm_is_mocked(broken) is False


def test_the_mock_reports_zero_tokens_so_preflight_can_refuse_it(monkeypatch):
    """The token count is the signal preflight keys on. 400 made the mock
    indistinguishable from a working model."""
    monkeypatch.setattr(common, "get_settings", lambda: _settings())
    for prompt in ("extract the document structure", "extract metadata from this",
                   "classify this document"):
        _reply, tokens_in, tokens_out = common.call_llm("any-model", prompt)
        assert tokens_in == 0, f"mock claimed real input tokens for {prompt!r}"
        assert tokens_out == 0


def test_preflight_refuses_a_build_when_replies_are_mocked(monkeypatch):
    monkeypatch.setattr(build_mod, "_PREFLIGHT_OK", set())
    monkeypatch.setattr(common, "get_settings", lambda: _settings())

    with pytest.raises(build_mod.ExtractorUnavailable) as excinfo:
        build_mod.preflight_extractor("global.anthropic.claude-sonnet-4-5-20250929-v1:0")

    msg = str(excinfo.value)
    assert "MOCK" in msg, "the operator must be told no model was contacted"
    assert "dis_backend/.env" in msg, "naming the env file it actually reads is the fix"
    assert "ENVIRONMENT" in msg
    # The message must not send an operator hunting for keys that are already there:
    # an instance role is a real credential source and has to be named as one.
    assert "instance role" in msg


def test_a_real_model_failure_keeps_its_own_distinct_message(monkeypatch):
    """A credentialled environment whose model is simply unavailable must NOT be told
    it is running mocks — that is a different fix entirely."""
    monkeypatch.setattr(build_mod, "_PREFLIGHT_OK", set())
    monkeypatch.setattr(common, "get_settings",
                        lambda: _settings(bedrock_kwargs={"aws_access_key_id": "AKIA-real"}))
    monkeypatch.setattr(common, "call_llm",
                        lambda model, prompt, max_tokens=8: ('{"doc_type":"other"}', 0, 0))

    with pytest.raises(build_mod.ExtractorUnavailable) as excinfo:
        build_mod.preflight_extractor("some-model")

    msg = str(excinfo.value)
    assert "MOCK" not in msg
    assert "could not be invoked from this environment" in msg


def test_a_working_model_still_passes_preflight(monkeypatch):
    monkeypatch.setattr(build_mod, "_PREFLIGHT_OK", set())
    monkeypatch.setattr(common, "get_settings",
                        lambda: _settings(bedrock_kwargs={"aws_access_key_id": "AKIA-real"}))
    monkeypatch.setattr(common, "call_llm",
                        lambda model, prompt, max_tokens=8: ('{"ok":true}', 12, 4))

    build_mod.preflight_extractor("good-model")   # must not raise
