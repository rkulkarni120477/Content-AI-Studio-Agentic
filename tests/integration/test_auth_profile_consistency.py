"""
The user profile must be identical whichever endpoint returns it.

Three endpoints hand the frontend a ``UserProfileResponse``: ``POST /auth/login``,
``POST /auth/refresh`` and ``GET /auth/me``. The React store writes whichever one
arrives last into the same ``auth.user`` slot, so a field present in one and absent
from another makes UI gated on that field appear and disappear depending on how the
session started — login vs. page reload.

That is not hypothetical. ``digest_pipeline_enabled`` (which gates the block-wide
generation panel on the CDD and Blueprint pages) was set only by ``/auth/me``, while
its schema default of ``False`` made login and refresh look like a definitive "off"
rather than "not answered". Result: an AIM user who logged in normally saw no
block-wide option, and the option appeared after a hard refresh — because a reload
boots through ``/auth/me``.

These tests compare the endpoints against each other rather than against a literal,
so they keep holding as fields are added — which is the actual failure mode.
"""

from __future__ import annotations

import pytest

from app.core.config import settings


@pytest.fixture()
def platform_admin(db):
    """A platform admin, which is how the reported case signs in.

    Deliberately not the shared ``admin_user`` fixture: the tenant password path
    requires an organization code that fixture does not supply (which is why the
    rest of tests/integration/test_auth.py currently errors), and the platform-admin
    path is both unaffected by that and the one the bug was reported against.

    Named neutrally rather than "platformadmin" so the test does not depend on
    config/dis_access.json listing that username — the assertions below turn on the
    allowlist logic, which holds for whatever client the user resolves to.
    """
    from app.core.security import hash_password
    from promptops_app.database import User

    user = User(
        username="test_platform_admin",
        password_hash=hash_password("test_password"),
        role="admin",
        is_active=True,
        is_platform_admin=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture()
def digest_pipeline_on():
    """Enable the digest pipeline for every client for the duration of a test."""
    orig_enabled = settings.digest_pipeline_enabled
    orig_clients = settings.digest_pipeline_clients
    settings.digest_pipeline_enabled = True
    settings.digest_pipeline_clients = ""  # empty allowlist ⇒ all clients
    try:
        yield
    finally:
        settings.digest_pipeline_enabled = orig_enabled
        settings.digest_pipeline_clients = orig_clients


def _login(client) -> dict:
    response = client.post(
        "/api/v1/auth/login",
        json={
            "username": "test_platform_admin",
            "password": "test_password",
            "platform_admin": True,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


class TestProfileConsistency:
    def test_login_and_me_return_the_same_profile(self, client, platform_admin, digest_pipeline_on):
        """Every field, not just the ones someone remembered to wire up twice."""
        login = _login(client)
        headers = {"Authorization": f"Bearer {login['access_token']}"}

        me = client.get("/api/v1/auth/me", headers=headers)
        assert me.status_code == 200, me.text

        # Compared as whole dicts: a per-field assertion list would need editing
        # every time a field is added, which is exactly how the drift got in.
        assert login["user"] == me.json()

    def test_refresh_returns_the_same_profile_as_me(self, client, platform_admin, digest_pipeline_on):
        """A mid-session token refresh must not silently strip capabilities.

        Worse than the login case if it drifts: the panel would vanish while the
        user was working, with no action of theirs to explain it.
        """
        login = _login(client)
        headers = {"Authorization": f"Bearer {login['access_token']}"}

        refreshed = client.post("/api/v1/auth/refresh", headers=headers)
        assert refreshed.status_code == 200, refreshed.text

        me = client.get("/api/v1/auth/me", headers=headers)
        assert refreshed.json()["user"] == me.json()

    def test_login_reports_the_digest_pipeline_capability(self, client, platform_admin, digest_pipeline_on):
        """The specific field whose absence hid the block-wide panel.

        Named explicitly as well as covered by the dict comparison above: this is
        the one a reader needs to find when the panel goes missing again, and a
        capability flag that defaults to False cannot be allowed to mean
        "nobody filled this in".
        """
        login = _login(client)
        assert login["user"]["digest_pipeline_enabled"] is True

    def test_the_capability_is_off_when_the_client_is_not_allowlisted(self, client, platform_admin):
        """The flag must still be a real answer, not a rubber stamp.

        Without this, wiring the field to a literal ``True`` would pass every test
        above while handing the panel to clients that must not have it.
        """
        orig_enabled = settings.digest_pipeline_enabled
        orig_clients = settings.digest_pipeline_clients
        settings.digest_pipeline_enabled = True
        settings.digest_pipeline_clients = "some-other-client"
        try:
            login = _login(client)
            headers = {"Authorization": f"Bearer {login['access_token']}"}
            me = client.get("/api/v1/auth/me", headers=headers)

            assert login["user"]["digest_pipeline_enabled"] is False
            assert me.json()["digest_pipeline_enabled"] is False
        finally:
            settings.digest_pipeline_enabled = orig_enabled
            settings.digest_pipeline_clients = orig_clients

    def test_the_master_switch_overrides_the_allowlist(self, client, platform_admin):
        """Pipeline off ⇒ off for everyone, on both endpoints."""
        orig_enabled = settings.digest_pipeline_enabled
        orig_clients = settings.digest_pipeline_clients
        settings.digest_pipeline_enabled = False
        settings.digest_pipeline_clients = ""
        try:
            login = _login(client)
            headers = {"Authorization": f"Bearer {login['access_token']}"}
            me = client.get("/api/v1/auth/me", headers=headers)

            assert login["user"]["digest_pipeline_enabled"] is False
            assert me.json()["digest_pipeline_enabled"] is False
        finally:
            settings.digest_pipeline_enabled = orig_enabled
            settings.digest_pipeline_clients = orig_clients


class TestSingleProfileBuilder:
    def test_login_and_me_build_the_profile_through_one_helper(self):
        """Structural guard against the same drift returning.

        The endpoints agreeing today does not stop the next added field from being
        wired into one construction site and not the other. There must be exactly
        one place that builds a ``UserProfileResponse``.
        """
        import inspect
        from pathlib import Path

        from app.api.v1.routers import auth as auth_router

        # Located through the imported module rather than a path relative to the
        # working directory, so the test does not quietly pass by reading nothing
        # when pytest is invoked from somewhere other than the repo root.
        src = Path(inspect.getsourcefile(auth_router)).read_text()
        assert src.count("UserProfileResponse(") == 1, (
            "More than one construction site for UserProfileResponse in auth.py — "
            "the next field added to one will go missing from the other, which is "
            "the bug this module exists to prevent."
        )
