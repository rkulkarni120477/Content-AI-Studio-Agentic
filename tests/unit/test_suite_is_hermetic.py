"""The suite must not reach a real DIS backend.

Guarding a wall-clock regression, not a behaviour. DIS_API_BASE_URL in a
developer's .env is the Compose service hostname (http://dis_backend:8000/v1).
Tests run outside that network, so before tests/conftest.py disabled DIS every
retrieval spent ~12s failing to resolve the name, times three connect retries,
times each call the test made. tests/unit/test_blueprint_document_title.py alone
cost 442s of the run, and `pytest tests` could not finish inside 15 minutes —
which hid 15 genuinely failing tests behind a timeout for as long as it lasted.

Nothing here asserts that disabling DIS is *correct* behaviour; it asserts the
test environment stays sealed, so the next person to widen it finds out from a
failing test rather than from a CI job that mysteriously takes a quarter hour.
"""

from __future__ import annotations

import os


def test_dis_is_disabled_for_the_test_run():
    from app.core.dis_client import dis_client

    assert dis_client.enabled is False, (
        "DIS is enabled during the test run, so retrievals will try to reach "
        f"{dis_client.base_url}. Set DIS_ENABLED=false (tests/conftest.py does "
        "this by default) or stub dis_client in the test that needs it."
    )


def test_the_disabled_client_answers_without_touching_the_network():
    """The short-circuit must return the same empty shape callers already handle.

    This is what makes the seam behaviour-preserving: routers read
    ``combined_context`` and ``source_units``, and a disabled DIS supplies both
    as empty rather than raising — the identical outcome to the unreachable-DIS
    path those routers already degrade through.
    """
    from app.core.dis_client import dis_client

    result = dis_client.request_sync("POST", "/context/retrieve/blueprint", json={})

    assert result["enabled"] is False
    assert result["combined_context"] == ""
    assert result["source_units"] == []


def test_the_database_url_never_points_at_a_real_server():
    """Companion guard to the DIS one, for the rule conftest.py states first."""
    assert os.environ["DATABASE_URL"].startswith("sqlite"), (
        "The test run is pointed at a non-SQLite database."
    )
