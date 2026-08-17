"""Integration tests for platform-admin tenant hard-delete (delete-icon feature).

Uses the real (in-memory SQLite) db/client fixtures rather than tests/unit/ —
this exercises the actual endpoint (auth, 403/404) and the full ORM cascade
against a real session, not mocks.
"""

from __future__ import annotations

import json

import pytest

from app.core.security import create_access_token, hash_password
from app.services import tenant_service


def _headers(db, *, username: str, is_platform_admin: bool = False, project_id=None):
    from promptops_app.database import TenantMembership, User

    user = User(
        username=username, password_hash=hash_password("test_password"),
        role="admin" if is_platform_admin else "author",
        is_active=True, is_platform_admin=is_platform_admin, project_id=project_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    if not is_platform_admin:
        # get_current_user re-validates an active TenantMembership on every
        # request for non-platform-admin users, not just User.is_active.
        db.add(TenantMembership(user_id=user.id, project_id=project_id, role="author", active=True))
        db.commit()

    token = create_access_token(
        user.username, user.role or "author",
        project_id=project_id, is_platform_admin=is_platform_admin,
    )
    return {"Authorization": f"Bearer {token}"}, user


@pytest.fixture()
def tenant_graph(db):
    from promptops_app.database import (
        Block,
        BudgetPolicy,
        Cluster,
        Course,
        Generation,
        Project,
        Style,
        TenantMembership,
        TenantRole,
        User,
    )

    project = Project(name="Delete Me", slug="delete-me-test", is_active=True, status="active")
    db.add(project)
    db.flush()

    cluster = Cluster(name="Cat", project_id=project.id, is_active=True)
    db.add(cluster)
    db.flush()

    course = Course(name="Course", project_id=project.id, cluster_id=cluster.id, is_active=True)
    db.add(course)
    db.flush()

    gen = Generation(
        topic="Topic", prompt_name="import", prompt_version="import", block_type="lesson",
        output_text="body", project_id=project.id, course_id=course.id, created_by="tester",
    )
    db.add(gen)
    db.flush()

    block = Block(generation_id=gen.id, block_type="lesson", content="block body")
    db.add(block)

    style = Style(style_id="tenant-style", name="Tenant Style", project_id=project.id)
    db.add(style)

    user = User(username="tenant_member", password_hash="x", role="author", project_id=project.id)
    db.add(user)
    db.flush()

    db.add(TenantMembership(user_id=user.id, project_id=project.id, role="author"))
    db.add(TenantRole(
        project_id=project.id, key="custom_role", name="Custom Role",
        permissions=json.dumps(["course.edit"]),
    ))
    db.add(BudgetPolicy(
        scope="project", scope_id=str(project.id), period="monthly",
        limit_type="usd", limit_usd=100.0, warn_threshold_pct=80.0,
    ))
    db.commit()
    db.refresh(project)
    db.refresh(user)
    return {
        "project": project, "cluster": cluster, "course": course, "gen": gen,
        "block": block, "style": style, "user": user,
    }


def test_hard_delete_removes_project_and_owned_content(db, tenant_graph):
    from promptops_app.database import (
        AuditLog,
        Block,
        BudgetPolicy,
        Cluster,
        Course,
        Generation,
        Project,
        Style,
        TenantMembership,
        TenantRole,
        User,
    )

    project = tenant_graph["project"]
    project_id = project.id
    cluster_id = tenant_graph["cluster"].id
    course_id = tenant_graph["course"].id
    gen_id = tenant_graph["gen"].id
    block_id = tenant_graph["block"].id
    style_id = tenant_graph["style"].id
    user_id = tenant_graph["user"].id

    tenant_service.hard_delete_tenant(db, project, deleted_by="platform_admin_user")
    db.commit()

    assert db.query(Project).filter_by(id=project_id).first() is None
    assert db.query(Cluster).filter_by(id=cluster_id).first() is None
    assert db.query(Course).filter_by(id=course_id).first() is None
    assert db.query(Generation).filter_by(id=gen_id).first() is None
    # purge_course's own cascade (blocks) ran, not just the top-level rows.
    assert db.query(Block).filter_by(id=block_id).first() is None
    # Tenant-authored style is gone too — only shared/global styles survive.
    assert db.query(Style).filter_by(id=style_id).first() is None
    assert db.query(TenantMembership).filter_by(project_id=project_id).count() == 0
    assert db.query(TenantRole).filter_by(project_id=project_id).count() == 0

    # The user survives — only their membership grant and "last-active
    # tenant" pointer are removed, never the account itself.
    user = db.query(User).filter_by(id=user_id).first()
    assert user is not None
    assert user.project_id is None

    # Billing history is retained, orphaned but intact (deliberate design choice).
    policy = db.query(BudgetPolicy).filter_by(scope="project", scope_id=str(project_id)).first()
    assert policy is not None

    # The deletion itself is audited.
    audit_row = db.query(AuditLog).filter_by(action="project.deleted", project_id=project_id).first()
    assert audit_row is not None
    assert audit_row.user_id == "platform_admin_user"


def test_hard_delete_preserves_members_other_tenant_membership(db, tenant_graph):
    """A user who belongs to another tenant too must keep that membership and
    (if it's their active one) their project_id pointer."""
    from promptops_app.database import Project, TenantMembership, User

    project = tenant_graph["project"]
    user = tenant_graph["user"]

    other_project = Project(name="Other Tenant", slug="other-tenant-test", is_active=True, status="active")
    db.add(other_project)
    db.flush()
    db.add(TenantMembership(user_id=user.id, project_id=other_project.id, role="author"))
    db.commit()

    tenant_service.hard_delete_tenant(db, project, deleted_by="platform_admin_user")
    db.commit()

    remaining = db.query(TenantMembership).filter_by(user_id=user.id, project_id=other_project.id).first()
    assert remaining is not None

    survivor = db.query(User).filter_by(id=user.id).first()
    assert survivor is not None
    # Their "last-active tenant" pointer was the deleted one — cleared, not
    # silently repointed at the surviving membership.
    assert survivor.project_id is None


class TestDeleteTenantEndpoint:
    def test_platform_admin_can_delete(self, db, client, tenant_graph):
        project_id = tenant_graph["project"].id
        headers, _ = _headers(db, username="platform_admin", is_platform_admin=True)
        resp = client.delete(f"/api/v1/platform/tenants/{project_id}", headers=headers)
        assert resp.status_code == 204, resp.text

        from promptops_app.database import Project
        assert db.query(Project).filter_by(id=project_id).first() is None

    def test_non_platform_admin_gets_403(self, db, client, tenant_graph):
        project_id = tenant_graph["project"].id
        headers, _ = _headers(db, username="regular_admin", is_platform_admin=False, project_id=project_id)
        resp = client.delete(f"/api/v1/platform/tenants/{project_id}", headers=headers)
        assert resp.status_code == 403

        from promptops_app.database import Project
        assert db.query(Project).filter_by(id=project_id).first() is not None

    def test_unknown_tenant_gets_404(self, db, client, tenant_graph):
        headers, _ = _headers(db, username="platform_admin_2", is_platform_admin=True)
        resp = client.delete("/api/v1/platform/tenants/9999999", headers=headers)
        assert resp.status_code == 404
