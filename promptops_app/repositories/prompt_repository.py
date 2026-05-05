"""Prompt Repository — Prompt and PromptVersion database access."""

from promptops_app.database import Prompt, PromptVersion


def list_all_prompts(db):
    return db.query(Prompt).all()


def get_prompt_by_name(db, name: str):
    return db.query(Prompt).filter(Prompt.name == name).first()


def count_prompts(db) -> int:
    return db.query(Prompt).count()


def list_versions_for_prompt(db, prompt_id: int):
    return (
        db.query(PromptVersion)
        .filter(PromptVersion.prompt_id == prompt_id)
        .order_by(PromptVersion.created_at.asc())
        .all()
    )


def get_prompt_version(db, prompt_id: int, version: str):
    return (
        db.query(PromptVersion)
        .filter(PromptVersion.prompt_id == prompt_id, PromptVersion.version == version)
        .first()
    )


def list_recent_versions(db, limit: int = 20):
    return (
        db.query(PromptVersion)
        .order_by(PromptVersion.created_at.desc())
        .limit(limit)
        .all()
    )
