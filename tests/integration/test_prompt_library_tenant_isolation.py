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


def _create(client, headers, title="Tenant Prompt", category="General", parent_id=None):
    payload = {"title": title, "content": "Some content", "category": category, "visibility": "draft"}
    if parent_id is not None:
        payload["parent_id"] = parent_id
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

    def test_children_of_a_shared_parent_stay_tenant_scoped(self, client, two_tenants):
        """A shared/global parent (project_id NULL) is visible to every
        tenant, and any tenant may parent a prompt under it — but its
        children list must never mix tenants together (finding #3)."""
        shared_parent = _create(client, two_tenants["headers_platform"], title="Shared Parent")
        child_a = _create(client, two_tenants["headers_a"], title="A's child", parent_id=shared_parent["id"])
        child_b = _create(client, two_tenants["headers_b"], title="B's child", parent_id=shared_parent["id"])

        resp_a = client.get(f"/api/v1/prompt-library/prompts/{shared_parent['id']}", headers=two_tenants["headers_a"])
        assert resp_a.status_code == 200
        child_ids_a = [c["id"] for c in resp_a.json()["children"]]
        assert child_a["id"] in child_ids_a
        assert child_b["id"] not in child_ids_a
        assert resp_a.json()["_child_count"] == 1

        resp_b = client.get(f"/api/v1/prompt-library/prompts/{shared_parent['id']}", headers=two_tenants["headers_b"])
        child_ids_b = [c["id"] for c in resp_b.json()["children"]]
        assert child_b["id"] in child_ids_b
        assert child_a["id"] not in child_ids_b

        resp_platform = client.get(
            f"/api/v1/prompt-library/prompts/{shared_parent['id']}", headers=two_tenants["headers_platform"])
        child_ids_platform = [c["id"] for c in resp_platform.json()["children"]]
        assert child_a["id"] in child_ids_platform
        assert child_b["id"] in child_ids_platform

    def test_child_count_in_list_view_stays_tenant_scoped(self, client, two_tenants):
        shared_parent = _create(client, two_tenants["headers_platform"], title="Shared Parent For List")
        _create(client, two_tenants["headers_a"], title="A's child for list", parent_id=shared_parent["id"])
        _create(client, two_tenants["headers_b"], title="B's child for list", parent_id=shared_parent["id"])

        resp = client.get("/api/v1/prompt-library/prompts", headers=two_tenants["headers_a"])
        assert resp.status_code == 200
        item = next(p for p in resp.json() if p["id"] == shared_parent["id"])
        assert item["_child_count"] == 1

    def test_deleting_a_shared_parent_does_not_cascade_into_another_tenants_children(self, client, db, two_tenants):
        """Finding #4 (defense in depth): the delete cascade only ever
        archives children visible to the caller's own tenant. Exercised
        against a tenant-owned parent with a cross-tenant child attached
        directly at the DB layer — resolve_parent_id's own visibility gate
        (finding #3) already refuses this through the API, but the cascade
        itself must not assume that and re-open the leak for a row that
        reaches it some other way (a migration backfill, a future write
        path). Uses a tenant-owned (not shared) parent because finding #5
        makes shared rows read-only to tenant callers — a plain tenant can
        no longer delete a shared parent at all."""
        from promptops_app.database import Prompt

        parent_a = _create(client, two_tenants["headers_a"], title="Tenant A owned parent")
        child_a = _create(client, two_tenants["headers_a"], title="A's own child", parent_id=parent_a["id"])
        stray = Prompt(prompt_kind="library", parent_id=parent_a["id"], title="B's stray child",
                      category="General", visibility="draft", owner="pl_admin_b",
                      project_id=two_tenants["b"].id)
        db.add(stray)
        db.commit()
        db.refresh(stray)

        resp = client.delete(f"/api/v1/prompt-library/prompts/{parent_a['id']}", headers=two_tenants["headers_a"])
        assert resp.status_code == 200, resp.text

        assert db.query(Prompt).filter_by(id=parent_a["id"]).first().deleted_at is not None
        assert db.query(Prompt).filter_by(id=child_a["id"]).first().deleted_at is not None
        assert db.query(Prompt).filter_by(id=stray.id).first().deleted_at is None


class TestPromptsRegistryIdEndpointsTenantIsolation:
    """The /api/v1/prompts/{id}-shaped endpoints (get/update/delete/versions/
    default/variables/fixings/workflow-state) all route through
    _get_prompt_or_404 — a tenant admin must get the same 404 a nonexistent
    id would give, never another tenant's row, and never be able to write it."""

    def _tenant_a_prompt(self, db, two_tenants, **overrides):
        from promptops_app.database import Prompt, PromptVersion

        kwargs = dict(
            prompt_kind="pipeline", name="tenant-a-id-scoped-prompt",
            component_type="generate", owner="pl_admin_a", project_id=two_tenants["a"].id,
            active_version="v1",
        )
        kwargs.update(overrides)
        p = Prompt(**kwargs)
        db.add(p)
        db.commit()
        db.refresh(p)
        db.add(PromptVersion(
            prompt_id=p.id, version="v1", version_number=1,
            system_prompt="SYS {{x}}", user_prompt_template="USR {{x}}",
            is_active=True, workflow_state="draft",
        ))
        db.commit()
        return p

    def test_get_404s_for_another_tenant(self, client, db, two_tenants):
        p = self._tenant_a_prompt(db, two_tenants)
        assert client.get(f"/api/v1/prompts/{p.id}", headers=two_tenants["headers_b"]).status_code == 404
        assert client.get(f"/api/v1/prompts/{p.id}", headers=two_tenants["headers_a"]).status_code == 200

    def test_update_is_blocked_for_another_tenant(self, client, db, two_tenants):
        p = self._tenant_a_prompt(db, two_tenants)
        resp = client.put(
            f"/api/v1/prompts/{p.id}", json={"description": "hijacked"},
            headers=two_tenants["headers_b"],
        )
        assert resp.status_code == 404

    def test_delete_is_blocked_for_another_tenant(self, client, db, two_tenants):
        from promptops_app.database import Prompt

        p = self._tenant_a_prompt(db, two_tenants)
        resp = client.delete(f"/api/v1/prompts/{p.id}", headers=two_tenants["headers_b"])
        assert resp.status_code == 404
        assert db.query(Prompt).filter_by(id=p.id).first() is not None

    def test_list_versions_is_blocked_for_another_tenant(self, client, db, two_tenants):
        p = self._tenant_a_prompt(db, two_tenants)
        resp = client.get(f"/api/v1/prompts/{p.id}/versions", headers=two_tenants["headers_b"])
        assert resp.status_code == 404

    def test_create_version_is_blocked_for_another_tenant(self, client, db, two_tenants):
        p = self._tenant_a_prompt(db, two_tenants)
        resp = client.post(
            f"/api/v1/prompts/{p.id}/versions",
            json={"version": "v2", "system_prompt": "SYS2 {{x}}", "user_prompt_template": "USR2 {{x}}"},
            headers=two_tenants["headers_b"],
        )
        assert resp.status_code == 404

    def test_deploy_version_is_blocked_for_another_tenant(self, client, db, two_tenants):
        p = self._tenant_a_prompt(db, two_tenants)
        resp = client.post(
            f"/api/v1/prompts/{p.id}/versions/v1/deploy", headers=two_tenants["headers_b"],
        )
        assert resp.status_code == 404

    def test_set_default_flag_is_blocked_for_another_tenant(self, client, db, two_tenants):
        p = self._tenant_a_prompt(db, two_tenants)
        resp = client.put(
            f"/api/v1/prompts/{p.id}/default", json={"is_default": True},
            headers=two_tenants["headers_b"],
        )
        assert resp.status_code == 404

    def test_set_declared_variables_is_blocked_for_another_tenant(self, client, db, two_tenants):
        p = self._tenant_a_prompt(db, two_tenants)
        resp = client.put(
            f"/api/v1/prompts/{p.id}/variables", json={"variables": []},
            headers=two_tenants["headers_b"],
        )
        assert resp.status_code == 404

    def test_workflow_state_transition_is_blocked_for_another_tenant(self, client, db, two_tenants):
        p = self._tenant_a_prompt(db, two_tenants)
        resp = client.post(
            f"/api/v1/prompts/{p.id}/versions/v1/state", json={"state": "in_review"},
            headers=two_tenants["headers_b"],
        )
        assert resp.status_code == 404

    def test_set_fixing_is_blocked_for_another_tenant(self, client, db, two_tenants):
        p = self._tenant_a_prompt(db, two_tenants)
        resp = client.put(
            "/api/v1/prompts/fixings",
            json={"prompt_id": p.id, "component": "generate", "scope_level": "global"},
            headers=two_tenants["headers_b"],
        )
        assert resp.status_code == 404

    def test_platform_admin_still_reaches_every_tenants_prompt(self, client, db, two_tenants):
        p = self._tenant_a_prompt(db, two_tenants)
        resp = client.get(f"/api/v1/prompts/{p.id}", headers=two_tenants["headers_platform"])
        assert resp.status_code == 200

    def test_created_prompt_is_stamped_with_creators_tenant(self, client, db, two_tenants):
        """POST /api/v1/prompts (the registry create path, distinct from the
        Prompt Library's own create endpoint) must not land the new row as
        shared/global for a tenant-scoped creator."""
        from promptops_app.database import Prompt

        resp = client.post(
            "/api/v1/prompts",
            json={"name": "tenant-a-registry-created", "component_type": "generate"},
            headers=two_tenants["headers_a"],
        )
        assert resp.status_code == 201, resp.text
        row = db.query(Prompt).filter_by(id=resp.json()["id"]).first()
        assert row.project_id == two_tenants["a"].id

    def test_platform_admin_created_prompt_via_registry_is_global(self, client, db, two_tenants):
        from promptops_app.database import Prompt

        resp = client.post(
            "/api/v1/prompts",
            json={"name": "platform-registry-created", "component_type": "generate"},
            headers=two_tenants["headers_platform"],
        )
        assert resp.status_code == 201, resp.text
        row = db.query(Prompt).filter_by(id=resp.json()["id"]).first()
        assert row.project_id is None


class TestSharedPromptsAreReadOnlyToTenants:
    """Finding #5: NULL (shared/global) means "everyone may read", not
    "everyone may write". A tenant caller may still see a shared prompt but
    must not be able to edit, delete, or otherwise mutate it — only a
    platform admin may. Covers both the Prompt Library console and the
    /api/v1/prompts pipeline registry, which share the same underlying rule."""

    def test_tenant_cannot_update_a_shared_library_prompt(self, client, two_tenants):
        shared = _create(client, two_tenants["headers_platform"], title="Shared Library Prompt")
        resp = client.put(
            f"/api/v1/prompt-library/prompts/{shared['id']}",
            json={"title": "hijacked"}, headers=two_tenants["headers_a"],
        )
        assert resp.status_code == 404

    def test_tenant_can_still_read_a_shared_library_prompt(self, client, two_tenants):
        shared = _create(client, two_tenants["headers_platform"], title="Shared Library Prompt Read")
        resp = client.get(f"/api/v1/prompt-library/prompts/{shared['id']}", headers=two_tenants["headers_a"])
        assert resp.status_code == 200

    def test_tenant_cannot_delete_a_shared_library_prompt(self, client, two_tenants):
        shared = _create(client, two_tenants["headers_platform"], title="Shared Library Prompt To Delete")
        resp = client.delete(f"/api/v1/prompt-library/prompts/{shared['id']}", headers=two_tenants["headers_a"])
        assert resp.status_code == 404

    def test_tenant_cannot_add_attachment_to_a_shared_library_prompt(self, client, two_tenants):
        shared = _create(client, two_tenants["headers_platform"], title="Shared Library Prompt For Attachment")
        resp = client.post(
            f"/api/v1/prompt-library/prompts/{shared['id']}/attachments",
            files={"file": ("note.txt", b"hello", "text/plain")},
            headers=two_tenants["headers_a"],
        )
        assert resp.status_code == 404

    def test_platform_admin_can_update_a_shared_library_prompt(self, client, two_tenants):
        shared = _create(client, two_tenants["headers_platform"], title="Shared Library Prompt Admin Edit")
        resp = client.put(
            f"/api/v1/prompt-library/prompts/{shared['id']}",
            json={"title": "Admin renamed it"}, headers=two_tenants["headers_platform"],
        )
        assert resp.status_code == 200, resp.text

    def test_tenant_cannot_update_a_shared_pipeline_prompt(self, client, db, two_tenants):
        """Same rule on the /api/v1/prompts registry — includes every
        is_default=True component seed, which is created with no tenant."""
        from promptops_app.database import Prompt

        p = Prompt(prompt_kind="pipeline", name="shared-pipeline-prompt",
                   component_type="generate", owner="seed", project_id=None, is_default=True)
        db.add(p)
        db.commit()
        db.refresh(p)

        resp = client.put(
            f"/api/v1/prompts/{p.id}", json={"description": "hijacked"},
            headers=two_tenants["headers_a"],
        )
        assert resp.status_code == 404

    def test_tenant_can_still_read_a_shared_pipeline_prompt(self, client, db, two_tenants):
        from promptops_app.database import Prompt

        p = Prompt(prompt_kind="pipeline", name="shared-pipeline-prompt-read",
                   component_type="generate", owner="seed", project_id=None)
        db.add(p)
        db.commit()
        db.refresh(p)

        resp = client.get(f"/api/v1/prompts/{p.id}", headers=two_tenants["headers_a"])
        assert resp.status_code == 200

    def test_tenant_cannot_delete_a_shared_pipeline_prompt(self, client, db, two_tenants):
        from promptops_app.database import Prompt

        p = Prompt(prompt_kind="pipeline", name="shared-pipeline-prompt-delete",
                   component_type="generate", owner="seed", project_id=None)
        db.add(p)
        db.commit()
        db.refresh(p)

        resp = client.delete(f"/api/v1/prompts/{p.id}", headers=two_tenants["headers_a"])
        assert resp.status_code == 404

    def test_platform_admin_can_delete_a_shared_pipeline_prompt(self, client, db, two_tenants):
        from promptops_app.database import Prompt

        p = Prompt(prompt_kind="pipeline", name="shared-pipeline-prompt-admin-delete",
                   component_type="generate", owner="seed", project_id=None)
        db.add(p)
        db.commit()
        db.refresh(p)

        resp = client.delete(f"/api/v1/prompts/{p.id}", headers=two_tenants["headers_platform"])
        assert resp.status_code == 204
