"""Style Understand on the async worker must keep the caller's tenant identity.

The HTTP handler authorizes with the real request user (platform admin or
tenant member). The worker then calls the same ``execute_style_understand``,
which goes through ``_get_style_or_404``. Rebuilding the caller without
``_is_platform_admin`` / ``_project_id`` made every tenant-owned style 404,
and the job failed with the generic activity-bell message.
"""
from __future__ import annotations

from app.api.v1.routers.styles import execute_style_understand
from app.core.exceptions import NotFoundError
from app.schemas.style import StyleUnderstandRequest
from promptops_app.jobs import design_jobs as jobs


def _style(db, *, project_id, name="B5 Style"):
    from promptops_app.database import Style

    style = Style(
        style_id=f"style-{name.lower().replace(' ', '-')}-{project_id}",
        name=name,
        project_id=project_id,
    )
    db.add(style)
    db.commit()
    db.refresh(style)
    return style


def _stub_generation(monkeypatch):
    monkeypatch.setattr(
        "promptops_app.services.style_service.generate_style_understanding",
        lambda *a, **k: "Stubbed style understanding.",
    )
    monkeypatch.setattr(
        "app.api.v1.routers.styles.dis_client.generated_upsert_sync",
        lambda *a, **k: None,
    )


def test_worker_user_without_tenant_flags_cannot_understand_a_tenant_style(
    db, two_tenants, monkeypatch,
):
    _stub_generation(monkeypatch)
    style = _style(db, project_id=two_tenants["a"].id)
    user = jobs._reconstruct_user({
        "user_name": "platformadmin", "user_id": 18, "role": "admin",
    })
    try:
        execute_style_understand(db, style.id, StyleUnderstandRequest(), user)
        assert False, "expected NotFoundError when the worker user has no tenant flags"
    except NotFoundError:
        pass


def test_reconstructed_platform_admin_can_understand_a_tenant_style(
    db, two_tenants, monkeypatch,
):
    _stub_generation(monkeypatch)
    style = _style(db, project_id=two_tenants["a"].id)
    user = jobs._reconstruct_user(jobs.stamp_job_user({}, type(
        "U", (), {
            "username": "platformadmin",
            "id": 18,
            "role": "admin",
            "_is_platform_admin": True,
            "_project_id": None,
        },
    )()))
    result = execute_style_understand(db, style.id, StyleUnderstandRequest(), user)
    assert result.understanding == "Stubbed style understanding."
    db.refresh(style)
    assert style.understanding_status == "fresh"
    assert style.generated_summary == "Stubbed style understanding."


def test_reconstructed_tenant_admin_can_understand_own_style(
    db, two_tenants, monkeypatch,
):
    _stub_generation(monkeypatch)
    style = _style(db, project_id=two_tenants["a"].id)
    user = jobs._reconstruct_user(jobs.stamp_job_user({}, type(
        "U", (), {
            "username": "tenant_a_admin",
            "id": 2,
            "role": "admin",
            "_is_platform_admin": False,
            "_project_id": two_tenants["a"].id,
        },
    )()))
    result = execute_style_understand(db, style.id, StyleUnderstandRequest(), user)
    assert result.understanding == "Stubbed style understanding."
