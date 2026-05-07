"""Prompt Repository — Prompt and PromptVersion database access."""

from datetime import datetime
from promptops_app.database import Prompt, PromptVersion


# ---------------------------------------------------------------------------
# Basic lookups
# ---------------------------------------------------------------------------

def list_all_prompts(db):
    return db.query(Prompt).order_by(Prompt.name.asc()).all()


def get_prompt_by_name(db, name: str):
    return db.query(Prompt).filter(Prompt.name == name).first()


def count_prompts(db) -> int:
    return db.query(Prompt).count()


# ---------------------------------------------------------------------------
# Component-scoped queries
# ---------------------------------------------------------------------------

def get_default_prompt(db, component_type: str) -> Prompt | None:
    """Return the single is_default=True asset for *component_type*, or None."""
    return (
        db.query(Prompt)
        .filter(
            Prompt.component_type == component_type,
            Prompt.is_default == True,  # noqa: E712
        )
        .first()
    )


def list_prompts_by_component(db, component_type: str) -> list[Prompt]:
    """Return all prompts whose component_type matches, default first then alpha."""
    return (
        db.query(Prompt)
        .filter(Prompt.component_type == component_type)
        .order_by(Prompt.is_default.desc(), Prompt.name.asc())
        .all()
    )


def list_prompts_tagged(db, tag: str) -> list[Prompt]:
    """Return prompts whose tags column contains *tag* (case-insensitive)."""
    return (
        db.query(Prompt)
        .filter(Prompt.tags.ilike(f"%{tag}%"))
        .order_by(Prompt.name.asc())
        .all()
    )


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
