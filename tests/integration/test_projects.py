"""
Integration tests for the projects router.

Regression coverage for the non-admin listing path: GET /api/v1/projects
used to call ``_get_user_projects(db, username)`` without the required
``role`` argument, so every non-admin request 500'd (the dashboard then
showed a misleading "No projects yet").
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def projects(db):
    """Two active projects and one inactive."""
    from promptops_app.database import Project

    rows = [
        Project(name="Alpha", is_active=True),
        Project(name="Beta", is_active=True),
        Project(name="Retired", is_active=False),
    ]
    db.add_all(rows)
    db.commit()
    for r in rows:
        db.refresh(r)
    return rows


def test_admin_sees_all_active_projects(client, auth_headers, projects):
    resp = client.get("/api/v1/projects", headers=auth_headers)
    assert resp.status_code == 200
    names = {p["name"] for p in resp.json()["items"]}
    assert names == {"Alpha", "Beta"}


def test_author_sees_only_assigned_projects(client, author_headers, projects, db):
    from promptops_app.database import ProjectUserAssignment

    alpha = next(p for p in projects if p.name == "Alpha")
    db.add(ProjectUserAssignment(project_id=alpha.id, username="test_author"))
    db.commit()

    resp = client.get("/api/v1/projects", headers=author_headers)
    assert resp.status_code == 200
    names = {p["name"] for p in resp.json()["items"]}
    assert names == {"Alpha"}


def test_author_without_assignments_gets_empty_list(client, author_headers, projects):
    resp = client.get("/api/v1/projects", headers=author_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0
