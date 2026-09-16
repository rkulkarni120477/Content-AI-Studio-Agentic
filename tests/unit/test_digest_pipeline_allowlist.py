"""S2 (AIM_PIPELINE_SHORTCOMINGS.txt): the digest-pipeline allowlist failed
OPEN, not closed.

settings.digest_pipeline_on_for(client_id) gates the day-scoped/block-wide
digest pipeline for every route that calls it (blueprints.py, cdd.py,
generations.py). It read:

    return not allow or (client_id or "").strip().lower() in allow

An EMPTY DIGEST_PIPELINE_CLIENTS meant "every client", including a blank or
unknown one -- the opposite of what clearing an allowlist reads as to an
operator, and dormant only because the shipped default ("aim") is non-empty.
Clearing that variable to disable the feature would instead turn it on for
every tenant.

Fixed to fail closed: an empty or unset allowlist now means no clients.
Naming clients explicitly is the only way to enable this for anyone.
"""

from __future__ import annotations

import pytest

from app.core.config import settings


@pytest.fixture(autouse=True)
def _restore_settings():
    """Every test in this file mutates the shared settings singleton."""
    orig_enabled = settings.digest_pipeline_enabled
    orig_clients = settings.digest_pipeline_clients
    yield
    settings.digest_pipeline_enabled = orig_enabled
    settings.digest_pipeline_clients = orig_clients


class TestAnEmptyAllowlistFailsClosed:
    def test_empty_string_allows_nobody(self):
        settings.digest_pipeline_enabled = True
        settings.digest_pipeline_clients = ""

        assert settings.digest_pipeline_on_for("aim") is False
        assert settings.digest_pipeline_on_for("cengage") is False
        assert settings.digest_pipeline_on_for("") is False
        assert settings.digest_pipeline_on_for(None) is False

    def test_whitespace_and_commas_only_also_allows_nobody(self):
        """The allowlist is parsed by splitting on ',' and stripping each
        piece -- a config value of stray commas/whitespace must not produce a
        surviving empty-string entry that then matches a blank client_id."""
        settings.digest_pipeline_enabled = True
        settings.digest_pipeline_clients = " , ,  "

        assert settings.digest_pipeline_on_for("aim") is False
        assert settings.digest_pipeline_on_for("") is False


class TestExplicitMembershipStillWorks:
    def test_a_named_client_is_allowed(self):
        settings.digest_pipeline_enabled = True
        settings.digest_pipeline_clients = "aim"

        assert settings.digest_pipeline_on_for("aim") is True

    def test_an_unnamed_client_is_still_refused(self):
        settings.digest_pipeline_enabled = True
        settings.digest_pipeline_clients = "aim"

        assert settings.digest_pipeline_on_for("cengage") is False

    def test_multiple_clients_and_case_insensitivity(self):
        settings.digest_pipeline_enabled = True
        settings.digest_pipeline_clients = "aim, Cengage"

        assert settings.digest_pipeline_on_for("AIM") is True
        assert settings.digest_pipeline_on_for("cengage") is True
        assert settings.digest_pipeline_on_for("acd") is False


class TestTheMasterSwitchStillGatesFirst:
    def test_disabled_beats_any_allowlist_value(self):
        """Regression guard: the fix must not accidentally make the master
        switch redundant or reachable-around."""
        settings.digest_pipeline_enabled = False
        settings.digest_pipeline_clients = "aim"
        assert settings.digest_pipeline_on_for("aim") is False

        settings.digest_pipeline_clients = ""
        assert settings.digest_pipeline_on_for("aim") is False
