"""Project Repository — Project and ProjectUserAssignment database access."""

from app.core.tenant_context import apply_tenant_filter
from promptops_app.database import Project, ProjectUserAssignment


def list_active_projects(db, tenant_id=None, is_platform_admin=False):
    q = (
        db.query(Project)
        .filter(Project.is_active == True)
        .order_by(Project.name)
    )
    return apply_tenant_filter(q, Project, tenant_id, is_platform_admin).all()


def get_project_by_id(db, project_id: int, tenant_id=None, is_platform_admin=False):
    q = db.query(Project).filter(Project.id == project_id)
    return apply_tenant_filter(q, Project, tenant_id, is_platform_admin).first()


def get_assigned_usernames(db, project_id: int) -> set:
    rows = (
        db.query(ProjectUserAssignment.username)
        .filter(ProjectUserAssignment.project_id == project_id)
        .all()
    )
    return {r[0] for r in rows}


def assign_user_to_project(db, project_id: int, username: str) -> None:
    if username in get_assigned_usernames(db, project_id):
        return
    db.add(ProjectUserAssignment(project_id=project_id, username=username))
    db.commit()


def unassign_user_from_project(db, project_id: int, username: str) -> None:
    db.query(ProjectUserAssignment).filter(
        ProjectUserAssignment.project_id == project_id,
        ProjectUserAssignment.username == username,
    ).delete()
    db.commit()
