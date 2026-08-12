"""Unit tests for platform-admin tenant hard-delete (delete-icon feature)."""

from __future__ import annotations

import json

import pytest

from app.services import tenant_service


@pytest.fixture()
def tenant_graph(db):
    from promptops_app.database import (
        BudgetPolicy,
        Cluster,
        Course,
        Generation,
        Project,
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
    return {"project": project, "cluster": cluster, "course": course, "gen": gen, "user": user}


def test_hard_delete_removes_project_and_owned_content(db, tenant_graph):
    from promptops_app.database import (
        BudgetPolicy,
        Cluster,
        Course,
        Generation,
        Project,
        TenantMembership,
        TenantRole,
        User,
    )

    project = tenant_graph["project"]
    project_id = project.id
    cluster_id = tenant_graph["cluster"].id
    course_id = tenant_graph["course"].id
    gen_id = tenant_graph["gen"].id
    user_id = tenant_graph["user"].id

    tenant_service.hard_delete_tenant(db, project)
    db.commit()

    assert db.query(Project).filter_by(id=project_id).first() is None
    assert db.query(Cluster).filter_by(id=cluster_id).first() is None
    assert db.query(Course).filter_by(id=course_id).first() is None
    assert db.query(Generation).filter_by(id=gen_id).first() is None
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
