"""Regression test for the Style "Understand" prompt-selection tenant gap.

The "Prompt Template" dropdown on the Style Prompts panel only ever lists
prompts visible to the caller's own tenant (already correctly scoped). But
POST /api/v1/styles/{id}/understand resolved whatever prompt_id it was given
with no tenant check at all — a request forging (or simply reusing a leaked)
another tenant's prompt_id would have that tenant's system/user prompt text
applied to this generation, even though it could never appear in the
dropdown. Fixed by threading the same visible_to_tenant gate prompt_loader.py
already uses for the same class of lookup.
"""

from __future__ import annotations

import pytest

from app.core.security import create_access_token, hash_password


def _headers(db, *, username: str, project_id):
    from promptops_app.database import TenantMembership, User

    user = User(
        username=username, password_hash=hash_password("test_password"),
        role="admin", is_active=True, is_platform_admin=False, project_id=project_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    db.add(TenantMembership(user_id=user.id, project_id=project_id, role="admin", active=True))
    db.commit()
    token = create_access_token(user.username, user.role, project_id=project_id, is_platform_admin=False)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def two_tenant_styles(db):
    from promptops_app.database import Project, Prompt, PromptVersion, Style

    tenant_a = Project(name="Style Tenant A", is_active=True, status="active")
    tenant_b = Project(name="Style Tenant B", is_active=True, status="active")
    db.add_all([tenant_a, tenant_b])
    db.commit()
    db.refresh(tenant_a)
    db.refresh(tenant_b)

    style_a = Style(style_id="tenant-a-style", name="Tenant A Style",
                    project_id=tenant_a.id, understanding_status="fresh")
    db.add(style_a)
    db.commit()
    db.refresh(style_a)

    secret_prompt = Prompt(prompt_kind="pipeline", name="tenant-b-secret-style-prompt",
                           component_type="style", owner="tenant_b_admin", project_id=tenant_b.id,
                           active_version="v1")
    db.add(secret_prompt)
    db.commit()
    db.refresh(secret_prompt)
    db.add(PromptVersion(
        prompt_id=secret_prompt.id, version="v1", version_number=1,
        system_prompt="TENANT B SECRET SYSTEM PROMPT", user_prompt_template="TENANT B SECRET USER PROMPT",
        is_active=True, created_by="tenant_b_admin",
    ))
    db.commit()

    return {
        "style_a": style_a, "secret_prompt": secret_prompt,
        "headers_a": _headers(db, username="style_tenant_a_admin", project_id=tenant_a.id),
    }


def test_another_tenants_prompt_id_is_not_applied_to_understand(client, db, monkeypatch, two_tenant_styles):
    captured = {}

    def _fake_generate(db_, style, model_choice, extra, *, system_prompt=None,
                       user_prompt_template=None, audit_capture=None):
        captured["system_prompt"] = system_prompt
        captured["user_prompt_template"] = user_prompt_template
        return "Generated understanding text."

    monkeypatch.setattr(
        "promptops_app.services.style_service.generate_style_understanding", _fake_generate,
    )

    resp = client.post(
        f"/api/v1/styles/{two_tenant_styles['style_a'].id}/understand",
        json={"prompt_id": two_tenant_styles["secret_prompt"].id},
        headers=two_tenant_styles["headers_a"],
    )
    assert resp.status_code == 200, resp.text
    assert captured["system_prompt"] != "TENANT B SECRET SYSTEM PROMPT"
    assert captured.get("user_prompt_template") != "TENANT B SECRET USER PROMPT"


def test_own_tenants_prompt_id_is_applied(client, db, monkeypatch, two_tenant_styles):
    from promptops_app.database import Prompt, PromptVersion

    own_prompt = Prompt(prompt_kind="pipeline", name="tenant-a-own-style-prompt",
                       component_type="style", owner="style_tenant_a_admin",
                       project_id=two_tenant_styles["style_a"].project_id, active_version="v1")
    db.add(own_prompt)
    db.commit()
    db.refresh(own_prompt)
    db.add(PromptVersion(
        prompt_id=own_prompt.id, version="v1", version_number=1,
        system_prompt="TENANT A OWN SYSTEM PROMPT", user_prompt_template="TENANT A OWN USER PROMPT",
        is_active=True, created_by="style_tenant_a_admin",
    ))
    db.commit()

    captured = {}

    def _fake_generate(db_, style, model_choice, extra, *, system_prompt=None,
                       user_prompt_template=None, audit_capture=None):
        captured["system_prompt"] = system_prompt
        return "Generated understanding text."

    monkeypatch.setattr(
        "promptops_app.services.style_service.generate_style_understanding", _fake_generate,
    )

    resp = client.post(
        f"/api/v1/styles/{two_tenant_styles['style_a'].id}/understand",
        json={"prompt_id": own_prompt.id},
        headers=two_tenant_styles["headers_a"],
    )
    assert resp.status_code == 200, resp.text
    assert captured["system_prompt"] == "TENANT A OWN SYSTEM PROMPT"
