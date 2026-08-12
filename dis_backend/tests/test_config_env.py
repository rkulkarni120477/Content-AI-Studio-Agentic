"""Environment-driven store location for the tenant configs.

The client YAMLs are committed, so a connection string written into them is baked
into the image: every environment is forced onto the same database, and
repointing one means editing a tracked file. That is how local, dev and prod all
came to share a single dev RDS whose security group admits one hard-coded /32 —
which breaks the moment an IP changes, with a failure that surfaces two layers
away as "no enumerated days or DIS error".

These tests pin the escape hatch and, just as importantly, pin that it is inert
when unused: with no environment variables set, the resolved config must be
byte-identical to the YAML literal, so adding the mechanism cannot itself change
any deployment.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_ENV_PREFIXES = ("DIS_STRUCTURE_STORE_URL", "DIS_VECTOR_STORE_")


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


def test_global_env_repoints_every_client(load_config):
    settings = load_config({"DIS_STRUCTURE_STORE_URL": "postgresql://u:p@localhost:5432/dis_db"})
    for client in ("aim", "cengage"):
        assert settings.get_tenant_config(client).structure_store.url == \
            "postgresql://u:p@localhost:5432/dis_db"


def test_per_client_env_wins_over_global(load_config):
    """Tenants may legitimately live in separate databases while sharing an image."""
    settings = load_config({
        "DIS_STRUCTURE_STORE_URL": "postgresql://u:p@shared:5432/d",
        "DIS_STRUCTURE_STORE_URL_AIM": "postgresql://u:p@aim-only:5432/d",
    })
    assert "aim-only" in settings.get_tenant_config("aim").structure_store.url
    assert "shared" in settings.get_tenant_config("cengage").structure_store.url


def test_vector_store_endpoint_and_index_are_overridable(load_config):
    settings = load_config({
        "DIS_VECTOR_STORE_ENDPOINT": "https://opensearch.local:9200",
        "DIS_VECTOR_STORE_INDEX": "dis-content-local",
    })
    vs = settings.get_tenant_config("aim").vector_store
    assert vs.endpoint == "https://opensearch.local:9200"
    assert vs.index_name == "dis-content-local"


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


def test_blank_env_var_does_not_blank_the_config(load_config):
    """An empty variable is 'unset', not 'set to nothing' — otherwise an exported
    but empty var in a shell profile would silently disable a store."""
    settings = load_config({"DIS_STRUCTURE_STORE_URL": ""})
    assert "dis-dev-postgres" in settings.get_tenant_config("aim").structure_store.url
