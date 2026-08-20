"""
Integration tests — DELETE /api/v1/prompt-library/prompts/{id} (archive).

The console Library lists only CAS pipeline prompts (Phase 12b), so delete is
the one prompt-library write that must reach pipeline rows. That makes three
things load-bearing and all three are covered here:

  * the tier split — pipeline rows are admin-only, and invisible rows 404
    rather than 403 so the response never confirms an id the caller may not read
  * the in-use guard — archiving the component default or a scope-locked prompt
    silently changes what other people's courses generate, so it takes an
    explicit force
  * the audit trail — what was archived AND what bindings were released
"""

from __future__ import annotations

import pytest

from app.core.security import hash_password

PL = "/api/v1/prompt-library"
REG = "/api/v1/prompts"


# Local auth fixtures rather than conftest's `auth_headers`: tenant login now
# requires an organization code plus an active TenantMembership, which the
# shared fixtures do not set up (every integration module that uses them is red
# on this branch, independently of this feature). Building the tenant here keeps
# these tests runnable without changing a fixture the rest of the suite shares.
ORG = "deltest"


@pytest.fixture()
def tenant(db):
    from promptops_app.database import Project

    project = Project(name="Delete Test Org", slug=ORG, status="active", is_active=True)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def _member_headers(client, db, tenant, username: str, role: str) -> dict:
    from app.core.security import hash_password
    from promptops_app.database import TenantMembership, User

    user = User(username=username, password_hash=hash_password("test_password"),
                role=role, is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    db.add(TenantMembership(user_id=user.id, project_id=tenant.id, role=role, active=True))
    db.commit()

    resp = client.post("/api/v1/auth/login", json={
        "username": username, "password": "test_password", "organization_code": ORG,
    })
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture()
def auth_headers(client, db, tenant) -> dict:
    """Admin — the tier that manages pipeline prompts."""
    return _member_headers(client, db, tenant, "del_admin", "admin")


@pytest.fixture()
def reviewer_headers(client, db, tenant) -> dict:
    """Lead — manages library prompts, but cannot see pipeline rows."""
    return _member_headers(client, db, tenant, "del_reviewer", "reviewer")


@pytest.fixture()
def author_headers(client, db, tenant) -> dict:
    """ID — no prompt-management permission at all."""
    return _member_headers(client, db, tenant, "del_author", "author")


def _pipeline_prompt(client, headers, name, component="cdd"):
    resp = client.post(
        REG,
        json={
            "name": name,
            "description": f"delete-test asset {name}",
            "component_type": component,
            "system_prompt": "SYS",
            "user_prompt_template": "USR",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _library_prompt(client, headers, title="A library prompt"):
    resp = client.post(f"{PL}/prompts",
                       json={"title": title, "content": "hello", "category": "General"},
                       headers=headers)
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


def _list_ids(client, headers, **params):
    qs = {"kind": "pipeline", "roots_only": "1", **params}
    resp = client.get(f"{PL}/prompts", params=qs, headers=headers)
    assert resp.status_code == 200, resp.text
    return [p["id"] for p in resp.json()]


class TestArchiveUnusedPrompt:
    def test_admin_archives_pipeline_prompt_and_it_leaves_the_library(
        self, client, auth_headers, db,
    ):
        from promptops_app.database import Prompt

        p = _pipeline_prompt(client, auth_headers, "del_plain")

        resp = client.delete(f"{PL}/prompts/{p['id']}", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["deleted"] == p["id"] and body["archived"] is True
        # Nothing was bound, so nothing was released.
        assert body["released"] is None

        assert p["id"] not in _list_ids(client, auth_headers)
        # Soft, not erased: the row and its history survive behind the filter.
        assert p["id"] in _list_ids(client, auth_headers, include_archived="1")
        row = db.query(Prompt).filter(Prompt.id == p["id"]).one()
        assert row.deleted_at is not None
        assert row.versions, "version history must survive an archive"

    def test_archived_prompt_can_be_restored(self, client, auth_headers):
        p = _pipeline_prompt(client, auth_headers, "del_restorable")
        assert client.delete(f"{PL}/prompts/{p['id']}", headers=auth_headers).status_code == 200

        resp = client.post(f"{PL}/prompts/{p['id']}/restore", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        assert p["id"] in _list_ids(client, auth_headers)

    def test_deleting_twice_is_a_404_not_a_second_archive(self, client, auth_headers):
        p = _pipeline_prompt(client, auth_headers, "del_twice")
        assert client.delete(f"{PL}/prompts/{p['id']}", headers=auth_headers).status_code == 200
        assert client.delete(f"{PL}/prompts/{p['id']}", headers=auth_headers).status_code == 404

    def test_library_prompt_still_archives(self, client, auth_headers):
        """The pre-existing library path must keep working (AC4)."""
        p = _library_prompt(client, auth_headers)
        resp = client.delete(f"{PL}/prompts/{p['id']}", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["released"] is None

    def test_writes_an_audit_event(self, client, auth_headers, db):
        from promptops_app.database import AuditLog

        p = _pipeline_prompt(client, auth_headers, "del_audited")
        client.delete(f"{PL}/prompts/{p['id']}", headers=auth_headers)

        row = (db.query(AuditLog)
               .filter(AuditLog.action == "prompt.delete",
                       AuditLog.entity_id == str(p["id"]))
               .one())
        assert row.changes["archived"] == {"old": False, "new": True}


class TestTierSplit:
    def test_reviewer_cannot_reach_a_pipeline_prompt(self, client, auth_headers, reviewer_headers):
        """404, not 403 — a reviewer cannot see pipeline rows at all, and the
        error must not tell them the id exists."""
        p = _pipeline_prompt(client, auth_headers, "del_tier")
        resp = client.delete(f"{PL}/prompts/{p['id']}", headers=reviewer_headers)
        assert resp.status_code == 404

    def test_author_is_refused_outright(self, client, auth_headers, author_headers):
        p = _pipeline_prompt(client, auth_headers, "del_tier_author")
        resp = client.delete(f"{PL}/prompts/{p['id']}", headers=author_headers)
        assert resp.status_code == 403


class TestInUseGuard:
    def test_component_default_is_refused_then_force_releases_it(
        self, client, auth_headers, db,
    ):
        from promptops_app.database import Prompt

        p = _pipeline_prompt(client, auth_headers, "del_default")
        assert client.put(f"{REG}/{p['id']}/default", json={"is_default": True},
                          headers=auth_headers).status_code == 200

        resp = client.delete(f"{PL}/prompts/{p['id']}", headers=auth_headers)
        assert resp.status_code == 409, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "PROMPT_IN_USE"
        assert detail["usage"]["is_default"] is True
        assert "default prompt for cdd" in detail["message"]
        assert db.query(Prompt).filter(Prompt.id == p["id"]).one().deleted_at is None

        forced = client.delete(f"{PL}/prompts/{p['id']}?force=true", headers=auth_headers)
        assert forced.status_code == 200, forced.text
        assert forced.json()["released"]["was_default"] is True
        row = db.query(Prompt).filter(Prompt.id == p["id"]).one()
        assert row.deleted_at is not None and row.is_default is False

    def test_scope_locked_prompt_is_refused_then_force_unbinds_it(
        self, client, auth_headers, db,
    ):
        from promptops_app.database import PromptFixing

        p = _pipeline_prompt(client, auth_headers, "del_locked")
        assert client.put(
            f"{REG}/fixings",
            json={"component": "cdd", "scope_level": "global", "prompt_id": p["id"]},
            headers=auth_headers,
        ).status_code == 200

        resp = client.delete(f"{PL}/prompts/{p['id']}", headers=auth_headers)
        assert resp.status_code == 409, resp.text
        usage = resp.json()["detail"]["usage"]
        assert [f["scope_level"] for f in usage["fixings"]] == ["global"]

        forced = client.delete(f"{PL}/prompts/{p['id']}?force=true", headers=auth_headers)
        assert forced.status_code == 200, forced.text
        assert len(forced.json()["released"]["fixings"]) == 1
        # The lock row is removed, not left dangling with a NULL prompt_id.
        assert db.query(PromptFixing).filter(PromptFixing.prompt_id == p["id"]).count() == 0

    def test_force_release_is_recorded_in_the_audit_event(self, client, auth_headers, db):
        from promptops_app.database import AuditLog

        p = _pipeline_prompt(client, auth_headers, "del_audit_release")
        client.put(f"{REG}/{p['id']}/default", json={"is_default": True}, headers=auth_headers)
        client.put(f"{REG}/fixings",
                   json={"component": "cdd", "scope_level": "global", "prompt_id": p["id"]},
                   headers=auth_headers)
        client.delete(f"{PL}/prompts/{p['id']}?force=true", headers=auth_headers)

        row = (db.query(AuditLog)
               .filter(AuditLog.action == "prompt.delete",
                       AuditLog.entity_id == str(p["id"]))
               .one())
        assert row.changes["is_default"] == {"old": True, "new": False}
        assert len(row.changes["released_scope_locks"]["old"]) == 1
        assert "released its generation bindings" in row.summary

    def test_force_on_an_unbound_prompt_changes_nothing(self, client, auth_headers):
        """force is permission to release bindings, not a different delete."""
        p = _pipeline_prompt(client, auth_headers, "del_force_noop")
        resp = client.delete(f"{PL}/prompts/{p['id']}?force=true", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["released"] is None


class TestUsageEndpoint:
    def test_reports_the_same_bindings_the_guard_enforces(self, client, auth_headers):
        p = _pipeline_prompt(client, auth_headers, "usage_probe")

        clean = client.get(f"{PL}/prompts/{p['id']}/usage", headers=auth_headers)
        assert clean.status_code == 200, clean.text
        assert clean.json()["blocking"] is False

        client.put(f"{REG}/{p['id']}/default", json={"is_default": True}, headers=auth_headers)
        bound = client.get(f"{PL}/prompts/{p['id']}/usage", headers=auth_headers).json()
        assert bound["blocking"] is True and bound["is_default"] is True
        assert bound["component_type"] == "cdd"

    def test_hidden_from_callers_who_cannot_read_the_row(
        self, client, auth_headers, reviewer_headers,
    ):
        p = _pipeline_prompt(client, auth_headers, "usage_hidden")
        assert client.get(f"{PL}/prompts/{p['id']}/usage",
                          headers=reviewer_headers).status_code == 404

    def test_library_prompt_is_never_blocking(self, client, auth_headers):
        p = _library_prompt(client, auth_headers, "Usage library row")
        body = client.get(f"{PL}/prompts/{p['id']}/usage", headers=auth_headers).json()
        assert body["blocking"] is False and body["fixings"] == []


# ---------------------------------------------------------------------------
# Cross-tenant isolation for the surfaces this feature ADDED.
#
# test_prompt_library_tenant_isolation.py already covers deleting a library row
# across tenants. What it cannot cover is what did not exist when it was
# written: archiving a PIPELINE row through the prompt-library endpoint, and the
# usage probe that precedes it. Both are new reachability into a table the
# tenant-scoping ticket had just finished fencing off, so they get the same
# zero-tolerance treatment here.
# ---------------------------------------------------------------------------

def _tenant_headers(db, *, username, project_id=None, role="admin", is_platform_admin=False):
    from app.core.security import create_access_token
    from promptops_app.database import TenantMembership, User

    user = User(username=username, password_hash=hash_password("test_password"),
                role=role, is_active=True, is_platform_admin=is_platform_admin,
                project_id=project_id)
    db.add(user)
    db.commit()
    db.refresh(user)
    if not is_platform_admin and project_id is not None:
        db.add(TenantMembership(user_id=user.id, project_id=project_id, role=role, active=True))
        db.commit()
    token = create_access_token(user.username, role, project_id=project_id,
                                is_platform_admin=is_platform_admin)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def tenants(db):
    from promptops_app.database import Project

    a = Project(name="Del Tenant A", slug="del-tenant-a", is_active=True, status="active")
    b = Project(name="Del Tenant B", slug="del-tenant-b", is_active=True, status="active")
    db.add_all([a, b])
    db.commit()
    db.refresh(a)
    db.refresh(b)
    return {
        "a": a, "b": b,
        "a_headers": _tenant_headers(db, username="del_a_admin", project_id=a.id),
        "b_headers": _tenant_headers(db, username="del_b_admin", project_id=b.id),
        "platform_headers": _tenant_headers(db, username="del_platform_admin",
                                            is_platform_admin=True),
    }


def _pipeline_row(db, name, project_id):
    """A pipeline prompt straight into the table — the registry POST stamps the
    caller's own tenant, which is the wrong shape for the shared/global case."""
    from promptops_app.database import Prompt

    p = Prompt(prompt_kind="pipeline", name=name, component_type="cdd", project_id=project_id)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


class TestCrossTenantIsolation:
    def test_cannot_archive_another_tenants_pipeline_prompt(self, client, db, tenants):
        p = _pipeline_row(db, "del_iso_owned", tenants["a"].id)

        resp = client.delete(f"{PL}/prompts/{p.id}", headers=tenants["b_headers"])
        assert resp.status_code == 404
        db.refresh(p)
        assert p.deleted_at is None

    def test_force_does_not_punch_through_the_tenant_boundary(self, client, db, tenants):
        """force is permission to release bindings, never permission to reach
        another tenant's row."""
        p = _pipeline_row(db, "del_iso_forced", tenants["a"].id)

        resp = client.delete(f"{PL}/prompts/{p.id}?force=true", headers=tenants["b_headers"])
        assert resp.status_code == 404
        db.refresh(p)
        assert p.deleted_at is None

    def test_tenant_cannot_archive_a_shared_pipeline_prompt(self, client, db, tenants):
        """A shared row (project_id NULL) is readable by every tenant but
        writable by none of them — the read/write split writable_by_tenant
        exists for. One tenant archiving it would silently change generation
        for all the others."""
        p = _pipeline_row(db, "del_iso_shared", None)

        resp = client.delete(f"{PL}/prompts/{p.id}", headers=tenants["a_headers"])
        assert resp.status_code == 404
        db.refresh(p)
        assert p.deleted_at is None

    def test_platform_admin_can_archive_a_shared_pipeline_prompt(self, client, db, tenants):
        p = _pipeline_row(db, "del_iso_shared_ok", None)

        resp = client.delete(f"{PL}/prompts/{p.id}", headers=tenants["platform_headers"])
        assert resp.status_code == 200, resp.text
        db.refresh(p)
        assert p.deleted_at is not None

    def test_usage_probe_does_not_leak_another_tenants_prompt(self, client, db, tenants):
        """The probe runs before the delete, so leaking here would leak just as
        much as the delete itself — it carries the read gate get_prompt uses."""
        p = _pipeline_row(db, "del_iso_usage", tenants["a"].id)

        assert client.get(f"{PL}/prompts/{p.id}/usage",
                          headers=tenants["b_headers"]).status_code == 404

    def test_usage_probe_reads_a_shared_prompt(self, client, db, tenants):
        """Deliberately the permissive read gate, not the write gate: a tenant
        may see what a shared prompt is bound to even though it may not archive
        it — that is what makes the 404 on delete explicable rather than
        mysterious."""
        p = _pipeline_row(db, "del_iso_usage_shared", None)

        resp = client.get(f"{PL}/prompts/{p.id}/usage", headers=tenants["a_headers"])
        assert resp.status_code == 200, resp.text
        assert resp.json()["blocking"] is False
