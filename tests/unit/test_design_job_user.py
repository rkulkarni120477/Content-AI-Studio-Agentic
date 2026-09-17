"""Worker identity for design jobs (Style Understand / CDD / Blueprint).

Style Understand failed in production with the generic activity-bell message
because the HTTP handler authorized a platform admin, then the worker rebuilt
the caller without ``_is_platform_admin`` / ``_project_id``. ``_get_style_or_404``
treated every tenant-owned style as missing and raised NotFoundError — whose
dict ``detail`` was also dropped by ``_fail``, so the UI never said why.
"""
from __future__ import annotations

import types
from pathlib import Path

import pytest

from app.core.exceptions import LLMGenerationError, NotFoundError, ValidationError
from app.core.tenant_context import visible_to_tenant
from promptops_app.jobs import design_jobs as jobs

_ROOT = Path(__file__).resolve().parents[2]


def test_stamp_job_user_persists_tenant_flags():
    user = types.SimpleNamespace(
        username="platformadmin", id=18, role="admin",
        _is_platform_admin=True, _project_id=None,
    )
    params = jobs.stamp_job_user({}, user)
    assert params["user_name"] == "platformadmin"
    assert params["user_id"] == 18
    assert params["is_platform_admin"] is True
    assert params["user_project_id"] is None


def test_reconstructed_platform_admin_can_see_a_tenant_style():
    """The production shape after the fix: platform admin, no home project."""
    user = jobs._reconstruct_user({
        "user_name": "platformadmin", "user_id": 18, "role": "admin",
        "is_platform_admin": True, "user_project_id": None,
    })
    assert user._is_platform_admin is True
    assert visible_to_tenant(89, user._project_id, user._is_platform_admin)


def test_reconstructed_user_without_tenant_flags_cannot_see_a_tenant_style():
    """The production bug: username/role only, so every tenant-owned style 404s."""
    user = jobs._reconstruct_user({
        "user_name": "platformadmin", "user_id": 18, "role": "admin",
    })
    assert user._is_platform_admin is False
    assert not visible_to_tenant(89, user._project_id, user._is_platform_admin)


def test_legacy_job_falls_back_to_request_project_id():
    """Jobs already in the queue have no user_project_id; the request body
    still carries workspace project_id, which is enough for the tenant check."""
    user = jobs._reconstruct_user({
        "user_name": "lead", "user_id": 7, "role": "admin",
        "project_id": 89,
    })
    assert user._project_id == 89
    assert visible_to_tenant(89, user._project_id, user._is_platform_admin)


@pytest.mark.parametrize("router", ["styles.py", "cdd.py", "blueprints.py"])
def test_design_job_routers_stamp_tenant_identity(router):
    src = (_ROOT / "app/api/v1/routers" / router).read_text(encoding="utf-8")
    assert "stamp_job_user" in src, (
        f"{router} must persist _is_platform_admin/_project_id for the worker"
    )


class _Job:
    id = "job-1"
    error_message = None
    status = None
    current_step = None
    updated_at = None
    completed_at = None


def test_fail_surfaces_app_error_message_instead_of_generic(monkeypatch):
    recorded = {}
    monkeypatch.setattr(jobs, "set_failed", lambda db, job, message: recorded.update(message=message))
    jobs._fail(object(), _Job(), ValidationError("Add at least one document."), "generic")
    assert recorded["message"] == "Add at least one document."


def test_fail_surfaces_llm_error_and_not_found(monkeypatch):
    recorded = {}
    monkeypatch.setattr(jobs, "set_failed", lambda db, job, message: recorded.update(message=message))

    jobs._fail(object(), _Job(), LLMGenerationError("The model timed out."), "generic")
    assert recorded["message"] == "The model timed out."

    jobs._fail(object(), _Job(), NotFoundError("Style", 53), "generic")
    assert "Style" in recorded["message"] and "53" in recorded["message"]
