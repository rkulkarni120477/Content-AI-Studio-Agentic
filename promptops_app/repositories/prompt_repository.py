"""Prompt Repository — Prompt, PromptVersion, and PromptFixing database access."""

from datetime import datetime
from app.core.tenant_context import apply_tenant_filter
from promptops_app.database import Prompt, PromptVersion, PromptFixing, UserPromptPreference


# ---------------------------------------------------------------------------
# Basic lookups
# ---------------------------------------------------------------------------

def list_all_prompts(db, tenant_id=None, is_platform_admin=False):
    q = db.query(Prompt).order_by(Prompt.name.asc())
    return apply_tenant_filter(q, Prompt, tenant_id, is_platform_admin).all()


def get_prompt_by_name(db, name: str, tenant_id=None, is_platform_admin=False):
    q = db.query(Prompt).filter(Prompt.name == name)
    return apply_tenant_filter(q, Prompt, tenant_id, is_platform_admin).first()


def count_prompts(db, tenant_id=None, is_platform_admin=False) -> int:
    q = db.query(Prompt)
    return apply_tenant_filter(q, Prompt, tenant_id, is_platform_admin).count()


# ---------------------------------------------------------------------------
# Component-scoped queries
# ---------------------------------------------------------------------------

def get_default_prompt(db, component_type: str, tenant_id=None, is_platform_admin=False) -> Prompt | None:
    """Return the single is_default=True asset for *component_type*, or None."""
    q = (
        db.query(Prompt)
        .filter(
            Prompt.component_type == component_type,
            Prompt.is_default == True,  # noqa: E712
        )
    )
    return apply_tenant_filter(q, Prompt, tenant_id, is_platform_admin).first()


def list_prompts_by_component(db, component_type: str, tenant_id=None, is_platform_admin=False) -> list[Prompt]:
    """Return all prompts whose component_type matches, default first then alpha."""
    q = (
        db.query(Prompt)
        .filter(Prompt.component_type == component_type)
        .order_by(Prompt.is_default.desc(), Prompt.name.asc())
    )
    return apply_tenant_filter(q, Prompt, tenant_id, is_platform_admin).all()


def list_prompts_tagged(db, tag: str, tenant_id=None, is_platform_admin=False) -> list[Prompt]:
    """Return prompts whose tags column contains *tag* (case-insensitive)."""
    q = (
        db.query(Prompt)
        .filter(Prompt.tags.ilike(f"%{tag}%"))
        .order_by(Prompt.name.asc())
    )
    return apply_tenant_filter(q, Prompt, tenant_id, is_platform_admin).all()


# ---------------------------------------------------------------------------
# Version management
# ---------------------------------------------------------------------------

def list_versions_for_prompt(db, prompt_id: int):
    return (
        db.query(PromptVersion)
        .filter(PromptVersion.prompt_id == prompt_id)
        .order_by(PromptVersion.created_at.asc())
        .all()
    )


def get_active_version(db, prompt_id: int) -> PromptVersion | None:
    """Return the currently-active PromptVersion for *prompt_id*."""
    versions = list_versions_for_prompt(db, prompt_id)
    active = [v for v in versions if v.is_active]
    return active[-1] if active else (versions[-1] if versions else None)


def get_prompt_version(db, prompt_id: int, version: str):
    return (
        db.query(PromptVersion)
        .filter(
            PromptVersion.prompt_id == prompt_id,
            PromptVersion.version == version,
        )
        .first()
    )


def deploy_new_version(
    db,
    prompt: Prompt,
    system_prompt: str,
    user_prompt_template: str,
    version_tag: str,
    change_reason: str,
    created_by: str,
) -> PromptVersion:
    """Deactivate all existing versions, insert a new active one, and bump updated_at."""
    db.query(PromptVersion).filter(
        PromptVersion.prompt_id == prompt.id
    ).update({PromptVersion.is_active: False})
    new_ver = PromptVersion(
        prompt_id=prompt.id,
        version=version_tag,
        system_prompt=system_prompt,
        user_prompt_template=user_prompt_template,
        change_reason=change_reason,
        is_active=True,
        created_by=created_by,
    )
    db.add(new_ver)
    prompt.active_version = version_tag
    prompt.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(new_ver)
    return new_ver


def list_recent_versions(db, limit: int = 20):
    return (
        db.query(PromptVersion)
        .order_by(PromptVersion.created_at.desc())
        .limit(limit)
        .all()
    )


# ---------------------------------------------------------------------------
# Prompt-fixing — scope-level default locking
# ---------------------------------------------------------------------------

def resolve_fixed_prompt(
    db,
    component: str,
    *,
    project_id=None,
    cluster_id=None,
    course_id=None,
) -> "PromptFixing | None":
    """Return the most-specific PromptFixing for component + context, or None.

    Resolution order (most specific wins):
        course → cluster → project → global

    Each scope is checked only when the corresponding ID is provided.
    Crucially, each scope is matched on its OWN ID only — not on parent IDs.
    A course-scope fix is found by ``course_id`` alone, regardless of which
    project or cluster the course belongs to.  This keeps lookup correct
    even when the caller only knows some of the hierarchy IDs (e.g. cluster_id=None).
    """
    # Build a prioritised list of (scope_level, id_column, id_value).
    # Skip a scope when its key ID is None — no fix could have been set that way.
    checks: list[tuple[str, object, object]] = []
    if course_id is not None:
        checks.append(("course",   PromptFixing.course_id,   course_id))
    if cluster_id is not None:
        checks.append(("cluster",  PromptFixing.cluster_id,  cluster_id))
    if project_id is not None:
        checks.append(("project",  PromptFixing.project_id,  project_id))
    checks.append(("global", None, None))   # always check global as final fallback

    for scope, id_col, id_val in checks:
        q = (
            db.query(PromptFixing)
            .filter(
                PromptFixing.component   == component,
                PromptFixing.scope_level == scope,
            )
        )
        if id_col is not None:
            q = q.filter(id_col == id_val)
        fixing = q.first()
        if fixing and fixing.prompt_id:
            return fixing
    return None


# ---------------------------------------------------------------------------
# User prompt preferences — Priority 4 persistence
# ---------------------------------------------------------------------------

def save_user_prompt_preference(
    db,
    user_name: str,
    component: str,
    course_id,
    prompt_id: int,
    project_id=None,
) -> None:
    """Persist a user's last-chosen prompt for (component, course_id).

    Uses an upsert so the table stays to one row per (user, component, course).
    Never raises — caller should swallow any exception so a save failure never
    blocks generation.
    """
    existing = (
        db.query(UserPromptPreference)
        .filter(
            UserPromptPreference.user_name == user_name,
            UserPromptPreference.component == component,
            UserPromptPreference.course_id == course_id,
        )
        .first()
    )
    if existing:
        existing.prompt_id  = prompt_id
        existing.project_id = project_id
        existing.updated_at = datetime.utcnow()
    else:
        db.add(UserPromptPreference(
            user_name=user_name, component=component,
            course_id=course_id, project_id=project_id,
            prompt_id=prompt_id,
        ))
    db.commit()


def get_user_prompt_preference(
    db,
    user_name: str,
    component: str,
    course_id,
) -> "Prompt | None":
    """Return the Prompt the user last selected for (component, course_id), or None."""
    pref = (
        db.query(UserPromptPreference)
        .filter(
            UserPromptPreference.user_name == user_name,
            UserPromptPreference.component == component,
            UserPromptPreference.course_id == course_id,
        )
        .first()
    )
    return (pref.prompt if pref and pref.prompt_id else None)


def set_fixed_prompt(
    db,
    *,
    component: str,
    scope_level: str,
    project_id=None,
    cluster_id=None,
    course_id=None,
    prompt_id: int,
    fixed_by: str,
    fixed_by_role: str,
) -> "PromptFixing":
    """Upsert a prompt fixing for the given scope. Returns the PromptFixing record."""
    existing = (
        db.query(PromptFixing)
        .filter(
            PromptFixing.component   == component,
            PromptFixing.scope_level == scope_level,
            PromptFixing.project_id  == project_id,
            PromptFixing.cluster_id  == cluster_id,
            PromptFixing.course_id   == course_id,
        )
        .first()
    )
    if existing:
        existing.prompt_id     = prompt_id
        existing.fixed_by      = fixed_by
        existing.fixed_by_role = fixed_by_role
        existing.fixed_at      = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        return existing

    fixing = PromptFixing(
        component=component, scope_level=scope_level,
        project_id=project_id, cluster_id=cluster_id, course_id=course_id,
        prompt_id=prompt_id, fixed_by=fixed_by, fixed_by_role=fixed_by_role,
    )
    db.add(fixing)
    db.commit()
    db.refresh(fixing)
    return fixing


def unset_fixed_prompt(
    db,
    *,
    component: str,
    scope_level: str,
    project_id=None,
    cluster_id=None,
    course_id=None,
) -> bool:
    """Remove a prompt fixing for the given scope. Returns True if a row was deleted."""
    rows = (
        db.query(PromptFixing)
        .filter(
            PromptFixing.component   == component,
            PromptFixing.scope_level == scope_level,
            PromptFixing.project_id  == project_id,
            PromptFixing.cluster_id  == cluster_id,
            PromptFixing.course_id   == course_id,
        )
        .delete()
    )
    db.commit()
    return rows > 0
