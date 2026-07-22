"""Block repository — version-aware CRUD for content blocks.

All DB writes go through these functions so the UI stays free of raw ORM logic.
"""

from datetime import datetime, timezone
from sqlalchemy import func
from promptops_app.database import Block, BlockVersion, log_event


def save_block_version(
    db,
    block: Block,
    change_source: str,
    change_note: str = "",
    created_by: str = "",
    *,
    commit: bool = True,
) -> BlockVersion:
    """Snapshot the block's current content.

    Increments version_num on both the BlockVersion row and the parent Block.
    Safe to call before or after a content update — always captures the
    state of block.content at call time.

    When ``commit=False``, the version is flushed only so callers can batch
    with surrounding writes (e.g. import reconstruct).
    """
    max_ver = (
        db.query(func.max(BlockVersion.version_num))
        .filter(BlockVersion.block_id == block.id)
        .scalar()
        or 0
    )
    new_ver = max_ver + 1
    ver = BlockVersion(
        block_id=block.id,
        version_num=new_ver,
        content=block.content or "",
        change_source=change_source,
        change_note=change_note or "",
        workflow_state_at_save=block.workflow_state,
        word_count=len((block.content or "").split()),
        created_by=created_by or "",
    )
    db.add(ver)
    block.version_num = new_ver
    if commit:
        db.commit()
        db.refresh(ver)
    else:
        db.flush()
    return ver


def get_block_versions(db, block_id: int) -> list:
    """Return all versions for a block, newest first."""
    return (
        db.query(BlockVersion)
        .filter(BlockVersion.block_id == block_id)
        .order_by(BlockVersion.version_num.desc())
        .all()
    )


def get_block_version(db, block_id: int, version_id: int):
    """Return a single version's full content, or None if it doesn't belong to this block."""
    version = db.query(BlockVersion).filter(BlockVersion.id == version_id).first()
    if not version or version.block_id != block_id:
        return None
    return version


def restore_block_version(
    db,
    block: Block,
    version_id: int,
    created_by: str,
) -> tuple:
    """Restore block content to a previous snapshot.

    1. Saves the current state as a pre-restore snapshot.
    2. Overwrites block.content with the target version.
    3. Saves the restored state as a new canonical version.

    Returns (new_BlockVersion, error_string). On failure returns (None, reason).
    """
    target = db.query(BlockVersion).filter(BlockVersion.id == version_id).first()
    if not target or target.block_id != block.id:
        return None, "Version not found for this block."

    # Preserve current state before overwriting
    save_block_version(
        db, block,
        "pre_restore_snapshot",
        f"Auto-snapshot before restoring to v{target.version_num}",
        created_by,
    )

    # Overwrite content
    block.content = target.content
    block.updated_at = datetime.now(timezone.utc)
    db.commit()

    # Record the restore as a new canonical version
    restored_ver = save_block_version(
        db, block,
        "restore",
        f"Restored from v{target.version_num}",
        created_by,
    )
    log_event(
        db, "block_version_restored", created_by,
        f"Block #{block.id} restored to v{target.version_num}",
        {"block_id": block.id, "source_version_id": version_id},
    )
    return restored_ver, None
