"""Where a tenant's stores come from, and what the environment may not change.

The client YAML is the only source of a store location: for AIM,
config/clients/aim.yaml holds structure_store.url and vector_store.endpoint /
index_name, and local, dev and prod all read that same file — DIS is one shared
corpus, not one per environment.

These tests pin that rule from both sides: the resolved config must equal the
YAML literal, and the DIS_STRUCTURE_STORE_URL / DIS_VECTOR_STORE_* overrides that
used to outrank it must now be inert. They were removed on 2026-08-27, the day
dev exported the first one at an empty sibling database and every Block-9 digest
build failed with "No calendar found for block 'Block 9'" — a deployment cannot
be diagnosed from the repo when an unversioned env var silently outranks it.

Model ids (DIS_MODEL_*) and credentials stay environment-driven, and are pinned
further down: model availability is a per-region, per-principal fact no committed
file can settle, and credentials must not be committed at all.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Every prefix the resolver reads must be cleared, not just the ones a given test
# sets: otherwise a developer (or CI box) that legitimately exports
# DIS_MODEL_TEXT_ALL — which is precisely what we tell environments to do — sees
# the default-value assertions fail for reasons that have nothing to do with the code.
_ENV_PREFIXES = ("DIS_STRUCTURE_STORE_URL", "DIS_VECTOR_STORE_", "DIS_MODEL_")


@pytest.fixture
def load_config(monkeypatch):
    """Reload the settings module with a controlled environment."""
    def _load(env: dict | None = None):
        for key in list(os.environ):
            if key.startswith(_ENV_PREFIXES):
                monkeypatch.delenv(key, raising=False)
        for key, value in (env or {}).items():
            monkeypatch.setenv(key, value)
        import config.settings as settings
        importlib.reload(settings)
        return settings
    yield _load
    # Leave the module in a clean, env-free state for other tests.
    for key in list(os.environ):
        if key.startswith(_ENV_PREFIXES):
            monkeypatch.delenv(key, raising=False)
    import config.settings as settings
    importlib.reload(settings)


def test_without_env_the_yaml_literal_is_untouched(load_config):
    """The whole mechanism must be a no-op until someone opts in."""
    import yaml
    settings = load_config()
    resolved = settings.get_tenant_config("aim").structure_store.url
    literal = (yaml.safe_load(Path("config/clients/aim.yaml").read_text())
               .get("structure_store", {}).get("url", ""))
    assert literal, "aim.yaml has no structure_store.url — did the config move?"
    # Compared as a boolean so a mismatch cannot print the DSN (it carries a
    # password) into CI output.
    assert resolved == literal, "env resolution altered the committed YAML value"


def test_env_cannot_repoint_the_structure_store(load_config):
    """The exact dev misconfiguration of 2026-08-27, now inert.

    An exported DIS_STRUCTURE_STORE_URL naming an empty sibling database used to
    win over the YAML, and the only symptom was "No calendar found for block
    'Block 9'" two layers up.
    """
    settings = load_config({
        "DIS_STRUCTURE_STORE_URL": "postgresql://u:p@wrong-host:5432/empty_db",
        "DIS_STRUCTURE_STORE_URL_AIM": "postgresql://u:p@wrong-host-aim:5432/empty_db",
    })
    for client in ("aim", "cengage"):
        url = settings.get_tenant_config(client).structure_store.url
        assert "wrong-host" not in url
        assert "dis-dev-postgres" in url


def test_env_cannot_repoint_the_vector_store(load_config):
    """Same rule for OpenSearch: a repointed index reads a different corpus, which
    surfaces as thin retrieval rather than as a configuration error."""
    settings = load_config({
        "DIS_VECTOR_STORE_ENDPOINT": "https://opensearch.local:9200",
        "DIS_VECTOR_STORE_INDEX": "dis-content-local",
        "DIS_VECTOR_STORE_INDEX_AIM": "dis-content-local-aim",
    })
    vs = settings.get_tenant_config("aim").vector_store
    assert "opensearch.local" not in vs.endpoint
    assert vs.index_name == "dis-content-dev-aim"


def test_dev_and_prod_read_the_same_stores(load_config):
    """DIS is one shared corpus. The YAML carries no environment switch, so there is
    no committed value for an ENVIRONMENT to select between."""
    dev = load_config({"ENVIRONMENT": "development"}).get_tenant_config("aim")
    prod = load_config({"ENVIRONMENT": "production"}).get_tenant_config("aim")
    assert dev.structure_store.url == prod.structure_store.url
    assert dev.vector_store.index_name == prod.vector_store.index_name


def test_placeholders_in_yaml_expand_from_the_environment(load_config):
    """``${VAR}`` / ``${VAR:-fallback}`` let the YAML stop naming any host at all,
    which is what finally gets the credentials out of git."""
    settings = load_config({"SOME_HOST": "db.internal"})
    assert settings._resolve_env_placeholders("postgresql://u:p@${SOME_HOST}:5432/d") == \
        "postgresql://u:p@db.internal:5432/d"
    assert settings._resolve_env_placeholders("${MISSING:-fallback.local}") == "fallback.local"
    # Unset with no fallback collapses to "" — the same "unconfigured" signal an
    # absent key gives, so a store fails loudly instead of connecting elsewhere.
    assert settings._resolve_env_placeholders("${DEFINITELY_UNSET_XYZ}") == ""


def test_expansion_walks_nested_structures(load_config):
    settings = load_config({"H": "h1"})
    tree = {"a": "${H}", "b": ["${H}", {"c": "${H}"}], "d": 7, "e": None}
    assert settings._expand_env_in_tree(tree) == \
        {"a": "h1", "b": ["h1", {"c": "h1"}], "d": 7, "e": None}


def test_an_exported_but_empty_var_leaves_the_store_alone(load_config):
    """Kept from when the override existed: neither a set nor an empty
    DIS_STRUCTURE_STORE_URL may blank or move the store the YAML names."""
    settings = load_config({"DIS_STRUCTURE_STORE_URL": ""})
    assert "dis-dev-postgres" in settings.get_tenant_config("aim").structure_store.url


# --------------------------------------------------------------------------- #
# Pipeline model selection
#
# Model availability is an environment fact, not a code fact: an ID can be
# end-of-life in one region, provider-legacy in another, and require a model-access
# grant the calling role may not hold. Baking one into a committed YAML forces every
# environment onto it — and because call_llm returns a valid-JSON stub on failure,
# an unavailable model degrades into complete-looking output with every extracted
# field empty (2026-08-12: 8/20 days "Unknown", all AM.I.B codes orphaned, job
# reported success).
# --------------------------------------------------------------------------- #
DEFAULT_TEXT_MODEL = "global.anthropic.claude-sonnet-4-5-20250929-v1:0"
TEXT_STEPS = ("classification", "metadata_extraction", "structure_extraction",
              "quality_check", "vision", "digest_extraction")


def test_default_extractor_is_the_reliably_invokable_model(load_config):
    """Sonnet 4.5 — NOT the newest, but the only Anthropic model this account invokes
    reliably (3/3 per region; Sonnet 5 / Opus 5 / Sonnet 4.6 each gave one spurious
    success then 0/3). A default that cannot be invoked fails every build, which is
    what build.preflight_extractor now surfaces loudly instead of silently."""
    settings = load_config()
    models = settings.get_tenant_config("aim").pipeline.models
    for step in TEXT_STEPS:
        assert getattr(models, step) == DEFAULT_TEXT_MODEL


def test_text_all_sets_every_step_at_once(load_config):
    """The common case is "this environment can invoke exactly one text model";
    repeating it six times invites the six from drifting apart."""
    # Deliberately NOT the default value — otherwise this passes whether the
    # override works or not.
    target = "global.anthropic.some-other-model-v9:0"
    assert target != DEFAULT_TEXT_MODEL
    settings = load_config({"DIS_MODEL_TEXT_ALL": target})
    models = settings.get_tenant_config("aim").pipeline.models
    for step in TEXT_STEPS:
        assert getattr(models, step) == target


def test_a_single_step_can_be_overridden_on_its_own(load_config):
    settings = load_config({"DIS_MODEL_DIGEST_EXTRACTION": "model-for-map-only"})
    models = settings.get_tenant_config("aim").pipeline.models
    assert models.digest_extraction == "model-for-map-only"
    assert models.classification == DEFAULT_TEXT_MODEL


def test_per_step_beats_text_all(load_config):
    settings = load_config({"DIS_MODEL_TEXT_ALL": "broad",
                            "DIS_MODEL_DIGEST_EXTRACTION": "specific"})
    models = settings.get_tenant_config("aim").pipeline.models
    assert models.digest_extraction == "specific"
    assert models.classification == "broad"


def test_per_client_model_override(load_config):
    settings = load_config({"DIS_MODEL_TEXT_ALL": "shared",
                            "DIS_MODEL_TEXT_ALL_AIM": "aim-only"})
    assert settings.get_tenant_config("aim").pipeline.models.vision == "aim-only"
    assert settings.get_tenant_config("cengage").pipeline.models.vision == "shared"


def test_embedding_model_is_not_swept_by_text_all(load_config):
    """TEXT_ALL means text steps. Pointing the embedding model at a text model would
    break indexing in a way that looks like a search-quality problem."""
    settings = load_config({"DIS_MODEL_TEXT_ALL": "some-text-model"})
    assert settings.get_tenant_config("aim").pipeline.models.embedding == \
        "amazon.titan-embed-text-v2:0"


def test_blank_model_var_does_not_blank_the_model(load_config):
    settings = load_config({"DIS_MODEL_DIGEST_EXTRACTION": "   "})
    assert settings.get_tenant_config("aim").pipeline.models.digest_extraction == \
        DEFAULT_TEXT_MODEL


# --------------------------------------------------------------------------- #
# Bedrock-only credentials
#
# Model access is granted per IAM principal, and the principal that can invoke the
# models is not necessarily the one that owns the storage. Measured 2026-08-12:
# promptops-contentAI-Dev (acct 498628474556) invokes Sonnet 5 / Opus 5 / Haiku 4.5
# 3/3 in both regions; nandkishor-ai-project-access (acct 410453487786), which owns
# DIS's S3 bucket and OpenSearch domain, invokes only Sonnet 4.5. DIS used ONE
# credential set for everything, so swapping AWS_* wholesale would buy model access
# at the cost of the digest store.
# --------------------------------------------------------------------------- #
def _settings(load_config, env=None):
    settings = load_config(env)
    return settings.GlobalSettings()


def test_without_bedrock_creds_the_shared_aws_pair_is_used(monkeypatch, load_config):
    """Must be a no-op until someone opts in — this cannot change any deployment."""
    for k, v in (("AWS_ACCESS_KEY_ID", "AKIASHARED"), ("AWS_SECRET_ACCESS_KEY", "sharedsecret"),
                 ("AWS_REGION", "ap-south-1")):
        monkeypatch.setenv(k, v)
    # Set to "" rather than deleted: GlobalSettings also reads dis_backend/.env, so
    # deleting the process env leaves a real deployment's values in play and the test
    # asserts against whatever that file happens to contain. An explicit empty value
    # takes precedence over the file and exercises the blank-is-unset rule too.
    for k in ("DIS_BEDROCK_ACCESS_KEY_ID", "DIS_BEDROCK_SECRET_ACCESS_KEY",
              "DIS_BEDROCK_SESSION_TOKEN", "DIS_BEDROCK_REGION"):
        monkeypatch.setenv(k, "")
    kw = _settings(load_config).bedrock_client_kwargs()
    assert kw["aws_access_key_id"] == "AKIASHARED"
    assert kw["aws_secret_access_key"] == "sharedsecret"
    assert kw["region_name"] == "ap-south-1"


def test_bedrock_creds_override_only_the_model_calls(monkeypatch, load_config):
    for k, v in (("AWS_ACCESS_KEY_ID", "AKIASTORAGE"), ("AWS_SECRET_ACCESS_KEY", "storagesecret"),
                 ("AWS_REGION", "ap-south-1"),
                 ("DIS_BEDROCK_ACCESS_KEY_ID", "AKIAMODELS"),
                 ("DIS_BEDROCK_SECRET_ACCESS_KEY", "modelsecret")):
        monkeypatch.setenv(k, v)
    s = _settings(load_config)
    kw = s.bedrock_client_kwargs()
    assert kw["aws_access_key_id"] == "AKIAMODELS", "model calls did not use the Bedrock pair"
    # Storage credentials are untouched — that is the whole point of the split.
    assert s.aws_access_key_id == "AKIASTORAGE"


def test_a_shared_session_token_is_not_paired_with_a_different_principals_key(monkeypatch, load_config):
    """A token belonging to a different principal than the key is rejected outright,
    so it must not be forwarded alongside the Bedrock key."""
    for k, v in (("AWS_ACCESS_KEY_ID", "AKIASTORAGE"), ("AWS_SECRET_ACCESS_KEY", "s"),
                 ("AWS_SESSION_TOKEN", "storage-token"),
                 ("DIS_BEDROCK_ACCESS_KEY_ID", "AKIAMODELS"),
                 ("DIS_BEDROCK_SECRET_ACCESS_KEY", "m")):
        monkeypatch.setenv(k, v)
    kw = _settings(load_config).bedrock_client_kwargs()
    assert "aws_session_token" not in kw


def test_bedrock_session_token_is_forwarded_when_it_belongs_to_the_bedrock_key(monkeypatch, load_config):
    for k, v in (("DIS_BEDROCK_ACCESS_KEY_ID", "ASIAMODELS"),
                 ("DIS_BEDROCK_SECRET_ACCESS_KEY", "m"),
                 ("DIS_BEDROCK_SESSION_TOKEN", "model-token")):
        monkeypatch.setenv(k, v)
    kw = _settings(load_config).bedrock_client_kwargs()
    assert kw["aws_session_token"] == "model-token"


def test_bedrock_region_can_differ_from_the_storage_region(monkeypatch, load_config):
    monkeypatch.setenv("AWS_REGION", "ap-south-1")
    monkeypatch.setenv("DIS_BEDROCK_REGION", "us-east-1")
    assert _settings(load_config).bedrock_client_kwargs()["region_name"] == "us-east-1"


def test_blank_bedrock_vars_fall_back_rather_than_blanking_credentials(monkeypatch, load_config):
    """An exported-but-empty var in a shell profile must not disable model calls."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIASHARED")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "sharedsecret")
    monkeypatch.setenv("DIS_BEDROCK_ACCESS_KEY_ID", "   ")
    monkeypatch.setenv("DIS_BEDROCK_REGION", "  ")
    monkeypatch.setenv("AWS_REGION", "ap-south-1")
    kw = _settings(load_config).bedrock_client_kwargs()
    assert kw["aws_access_key_id"] == "AKIASHARED"
    assert kw["region_name"] == "ap-south-1"
