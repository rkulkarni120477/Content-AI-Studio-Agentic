"""Cross-tenant isolation for the Prompt Library (and the /api/v1/prompts
template registry) — the fix for the "view only prompt titles that belong to
my tenant" ticket.

Zero-tolerance, same spirit as test_generation_trace_isolation.py: a tenant
admin/reviewer must never see, search, filter, or write another tenant's
prompts through any of these endpoints, and a genuine platform admin must
still see everything.
"""

from __future__ import annotations

import pytest

from app.core.security import create_access_token, hash_password


def _headers(db, *, username: str, project_id=None, role: str = "admin", is_platform_admin: bool = False):
    from promptops_app.database import TenantMembership, User

    user = User(
        username=username, password_hash=hash_password("test_password"),
        role=role, is_active=True, is_platform_admin=is_platform_admin, project_id=project_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    if not is_platform_admin and project_id is not None:
        db.add(TenantMembership(user_id=user.id, project_id=project_id, role=role, active=True))
        db.commit()
    token = create_access_token(user.username, role, project_id=project_id, is_platform_admin=is_platform_admin)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def two_tenants(db):
    from promptops_app.database import Project

    a = Project(name="Tenant A", slug="pl-tenant-a", is_active=True, status="active")
    b = Project(name="Tenant B", slug="pl-tenant-b", is_active=True, status="active")
    db.add_all([a, b])
    db.commit()
    db.refresh(a)
    db.refresh(b)
    return {
        "a": a, "b": b,
        "headers_a": _headers(db, username="pl_admin_a", project_id=a.id),
        "headers_b": _headers(db, username="pl_admin_b", project_id=b.id),
        "headers_platform": _headers(db, username="pl_platform_admin", is_platform_admin=True),
    }


def _create(client, headers, title="Tenant Prompt", category="General"):
    payload = {"title": title, "content": "Some content", "category": category, "visibility": "draft"}
    resp = client.post("/api/v1/prompt-library/prompts", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestPromptLibraryTenantIsolation:
    def test_created_prompt_is_stamped_with_creators_tenant(self, client, db, two_tenants):
        from promptops_app.database import Prompt

        created = _create(client, two_tenants["headers_a"])
        row = db.query(Prompt).filter_by(id=created["id"]).first()
        assert row.project_id == two_tenants["a"].id

    def test_platform_admin_created_prompt_is_global(self, client, db, two_tenants):
        from promptops_app.database import Prompt

        created = _create(client, two_tenants["headers_platform"], title="Global Prompt")
        row = db.query(Prompt).filter_by(id=created["id"]).first()
        assert row.project_id is None

    def test_tenant_b_cannot_see_tenant_a_prompt_in_list(self, client, two_tenants):
        created = _create(client, two_tenants["headers_a"], title="A-only prompt")
        resp = client.get("/api/v1/prompt-library/prompts", headers=two_tenants["headers_b"])
        assert resp.status_code == 200
        ids = [p["id"] for p in resp.json()]
        assert created["id"] not in ids

    def test_tenant_a_sees_its_own_prompt_in_list(self, client, two_tenants):
        created = _create(client, two_tenants["headers_a"], title="A-own-visible prompt")
        resp = client.get("/api/v1/prompt-library/prompts", headers=two_tenants["headers_a"])
        assert resp.status_code == 200
        ids = [p["id"] for p in resp.json()]
        assert created["id"] in ids

    def test_search_stays_tenant_scoped(self, client, two_tenants):
        created = _create(client, two_tenants["headers_a"], title="UniqueSearchTerm123")
        resp = client.get(
            "/api/v1/prompt-library/prompts/search?q=UniqueSearchTerm123",
            headers=two_tenants["headers_b"],
        )
        assert resp.status_code == 200
        ids = [p["id"] for p in resp.json()["items"]]
        assert created["id"] not in ids

    def test_get_by_id_404s_for_another_tenant_same_as_nonexistent(self, client, two_tenants):
        created = _create(client, two_tenants["headers_a"])
        resp_wrong_tenant = client.get(f"/api/v1/prompt-library/prompts/{created['id']}", headers=two_tenants["headers_b"])
        resp_nonexistent = client.get("/api/v1/prompt-library/prompts/999999999", headers=two_tenants["headers_b"])
        assert resp_wrong_tenant.status_code == 404
        assert resp_nonexistent.status_code == 404
        assert resp_wrong_tenant.json().keys() == resp_nonexistent.json().keys()

    def test_cannot_update_another_tenants_prompt(self, client, two_tenants):
        created = _create(client, two_tenants["headers_a"])
        resp = client.put(
            f"/api/v1/prompt-library/prompts/{created['id']}",
            json={"title": "Hijacked"},
            headers=two_tenants["headers_b"],
        )
        assert resp.status_code == 404

    def test_cannot_delete_another_tenants_prompt(self, client, db, two_tenants):
        from promptops_app.database import Prompt

        created = _create(client, two_tenants["headers_a"])
        resp = client.delete(f"/api/v1/prompt-library/prompts/{created['id']}", headers=two_tenants["headers_b"])
        assert resp.status_code == 404
        row = db.query(Prompt).filter_by(id=created["id"]).first()
        assert row.deleted_at is None

    def test_global_prompt_visible_to_every_tenant(self, client, two_tenants):
        created = _create(client, two_tenants["headers_platform"], title="Truly Global Prompt")
        for hdrs in (two_tenants["headers_a"], two_tenants["headers_b"]):
            resp = client.get("/api/v1/prompt-library/prompts", headers=hdrs)
            ids = [p["id"] for p in resp.json()]
            assert created["id"] in ids

    def test_platform_admin_sees_every_tenants_prompts(self, client, two_tenants):
        created_a = _create(client, two_tenants["headers_a"], title="A for platform admin")
        created_b = _create(client, two_tenants["headers_b"], title="B for platform admin")
        resp = client.get("/api/v1/prompt-library/prompts", headers=two_tenants["headers_platform"])
        ids = [p["id"] for p in resp.json()]
        assert created_a["id"] in ids
        assert created_b["id"] in ids

    def test_category_filter_stays_tenant_scoped(self, client, two_tenants):
        created_a = _create(client, two_tenants["headers_a"], title="A cat prompt", category="SharedCategoryName")
        created_b = _create(client, two_tenants["headers_b"], title="B cat prompt", category="SharedCategoryName")
        resp = client.get(
            "/api/v1/prompt-library/prompts?category=SharedCategoryName",
            headers=two_tenants["headers_a"],
        )
        ids = [p["id"] for p in resp.json()]
        assert created_a["id"] in ids
        assert created_b["id"] not in ids

    def test_prompts_registry_endpoint_stays_tenant_scoped(self, client, db, two_tenants):
        """GET /api/v1/prompts (the template-registry dropdown), not the
        Prompt Library console — same underlying leak, same fix."""
        from promptops_app.database import Prompt

        p = Prompt(prompt_kind="pipeline", name="tenant-a-pipeline-prompt",
                   component_type="generate", owner="pl_admin_a", project_id=two_tenants["a"].id)
        db.add(p)
        db.commit()

        resp_b = client.get("/api/v1/prompts", headers=two_tenants["headers_b"])
        assert resp_b.status_code == 200
        names_b = [item["name"] for item in resp_b.json()["items"]]
        assert "tenant-a-pipeline-prompt" not in names_b

        resp_a = client.get("/api/v1/prompts", headers=two_tenants["headers_a"])
        names_a = [item["name"] for item in resp_a.json()["items"]]
        assert "tenant-a-pipeline-prompt" in names_a

    def test_empty_state_when_tenant_has_no_prompts(self, client, two_tenants):
        resp = client.get("/api/v1/prompt-library/prompts", headers=two_tenants["headers_b"])
        assert resp.status_code == 200
        assert resp.json() == []
