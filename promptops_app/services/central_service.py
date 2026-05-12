"""Business logic for the Central Repository module."""

import logging
from typing import Optional

from promptops_app.repositories import central_repository
from promptops_app.database import CentralRepository, Prompt, PromptVersion

_log = logging.getLogger(__name__)

ITEM_TYPES = ["Prompt", "Asset", "Learning"]
SOURCE_MODULES = [
    "Project", "Cluster", "Course", "Component",
    "Style", "CDD", "Blueprint", "Generate", "Other",
]


def create_repository_item(
    db,
    *,
    title: str,
    item_type: str,
    content: str,
    description: str = "",
    source_module: str = "",
    project_id: Optional[int] = None,
    cluster_id: Optional[int] = None,
    course_id: Optional[int] = None,
    client_name: str = "",
    cluster_name: str = "",
    tags: str = "",
    created_by: str,
) -> tuple[bool, str, Optional[CentralRepository]]:
    if not title.strip():
        return False, "Title is required.", None
    if not content.strip():
        return False, "Content is required.", None
    if item_type not in ITEM_TYPES:
        return False, f"Invalid type. Must be one of: {', '.join(ITEM_TYPES)}", None
    try:
        item = central_repository.create_item(
            db,
            title=title.strip(),
            item_type=item_type,
            content=content.strip(),
            description=description.strip(),
            source_module=source_module,
            project_id=project_id,
            cluster_id=cluster_id,
            course_id=course_id,
            client_name=client_name.strip(),
            cluster_name=cluster_name.strip(),
            tags=tags.strip(),
            created_by=created_by,
        )
        _log.info("central_repo.create  id=%d  title=%r  by=%r", item.id, item.title, created_by)
        return True, "Item created successfully.", item
    except Exception as exc:
        _log.error("central_repo.create FAILED  title=%r  error=%s", title, exc, exc_info=True)
        return False, f"Failed to create item: {exc}", None


def update_repository_item(
    db,
    item_id: int,
    updated_by: str,
    **fields,
) -> tuple[bool, str]:
    if "title" in fields and not fields["title"].strip():
        return False, "Title cannot be empty."
    if "content" in fields and not fields["content"].strip():
        return False, "Content cannot be empty."
    item = central_repository.update_item(db, item_id, **fields)
    if not item:
        return False, "Item not found."
    _log.info("central_repo.update  id=%d  by=%r", item_id, updated_by)
    return True, "Item updated successfully."


def archive_repository_item(db, item_id: int, by: str) -> tuple[bool, str]:
    item = central_repository.archive_item(db, item_id)
    if not item:
        return False, "Item not found."
    _log.info("central_repo.archive  id=%d  by=%r", item_id, by)
    return True, "Item archived."


def delete_repository_item(db, item_id: int, by: str) -> tuple[bool, str]:
    ok = central_repository.delete_item(db, item_id)
    if not ok:
        return False, "Item not found."
    _log.info("central_repo.delete  id=%d  by=%r", item_id, by)
    return True, "Item permanently deleted."


def reuse_item(db, item_id: int, by: str) -> tuple[bool, str, str]:
    """Mark item as reused and return its content for copy-to-clipboard."""
    item = central_repository.get_by_id(db, item_id)
    if not item:
        return False, "Item not found.", ""
    central_repository.record_reuse(db, item_id)
    _log.info("central_repo.reuse  id=%d  by=%r", item_id, by)
    return True, "Content ready for reuse.", item.content


def import_from_prompt_registry(
    db,
    prompt_id: int,
    created_by: str,
    cluster_name: str = "",
    client_name: str = "",
    tags: str = "",
) -> tuple[bool, str, Optional[CentralRepository]]:
    """Pull an active PromptVersion into the Central Repository."""
    prompt: Optional[Prompt] = db.query(Prompt).filter(Prompt.id == prompt_id).first()
    if not prompt:
        return False, "Prompt not found.", None
    pv: Optional[PromptVersion] = (
        db.query(PromptVersion)
        .filter(PromptVersion.prompt_id == prompt_id, PromptVersion.is_active == True)
        .order_by(PromptVersion.id.desc())
        .first()
    )
    if not pv:
        return False, "No active version found for this prompt.", None

    combined = f"[SYSTEM]\n{pv.system_prompt or ''}\n\n[USER TEMPLATE]\n{pv.user_prompt_template or ''}"
    return create_repository_item(
        db,
        title=prompt.name,
        item_type="Prompt",
        content=combined,
        description=prompt.description or "",
        source_module="Prompt Registry",
        client_name=client_name,
        cluster_name=cluster_name,
        tags=tags or (prompt.tags or ""),
        created_by=created_by,
    )
