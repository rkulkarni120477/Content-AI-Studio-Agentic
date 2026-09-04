"""Shared fixtures for the DIS test suite."""
from __future__ import annotations

import sys

import pytest


@pytest.fixture(autouse=True)
def _reset_preflight_memo():
    """Clear the extractor preflight memo between tests.

    ``build._PREFLIGHT_OK`` is process-global by design: memoising a SUCCESS is what
    keeps a fully cached rebuild free of an extra probe call. Process-global state
    leaks across tests, though — one test proving model ``"m"`` invokable made a later
    test's failing-model assertion pass vacuously, because the probe was skipped.

    Deliberately reads sys.modules instead of importing the module: importing DIS
    packages from an autouse fixture runs their config at collection time, which
    populated the cached settings before test_dis.py could set its own storage
    environment and broke six of its tests. A fixture that only cleans up must not be
    the thing that triggers an import.
    """
    def _clear():
        mod = sys.modules.get("services.digests.build")
        memo = getattr(mod, "_PREFLIGHT_OK", None)
        if memo is not None:
            memo.clear()

    _clear()
    yield
    _clear()


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_ambient_probe: exercise services.pipeline.common.ambient_aws_credentials "
        "itself, opting out of the autouse patch that pins it to False so assertions "
        "do not depend on whether the test machine has an instance role or ~/.aws",
    )
