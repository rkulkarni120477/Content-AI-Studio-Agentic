"""
Reference-aware cleanup of uploaded editor images (S3).

Policy ("Safe"): an uploaded asset is deleted from S3 only when it is referenced
NOWHERE — not in any block's current content, draft, or Canvas HTML, and not in
any saved BlockVersion. This guarantees version restore / compare never breaks.

Only our own uploads are ever considered — URLs under the configured bucket's
``.../cas-assets/`` prefix. External or pasted image URLs and YouTube embeds are
never touched.

Runs on two occasions (best-effort; failures never break the caller):
  * manual block Save — clean up images removed from that block;
  * permanent course purge — clean up the purged course's images.
"""

from __future__ import annotations

import logging
import re

from app.core.config import settings
from app.services.asset_storage import delete_asset, _asset_region

_log = logging.getLogger(__name__)

_ASSET_MARKER = "/cas-assets/"


def _url_prefixes() -> list[str]:
    """Browser-facing URL prefixes under which our uploaded assets live."""
    bucket = settings.assets_s3_bucket
    prefixes: list[str] = []
    if settings.aws_endpoint_url:
        prefixes.append(f"{settings.aws_endpoint_url.rstrip('/')}/{bucket}/")
    prefixes.append(f"https://{bucket}.s3.{_asset_region(settings)}.amazonaws.com/")
    return prefixes


def assets_in_content(content: str | None) -> set[str]:
    """Return the S3 keys of our uploaded images referenced in a content string."""
    if not content or not settings.assets_s3_bucket:
        return set()
    keys: set[str] = set()
    for prefix in _url_prefixes():
        pattern = re.escape(prefix) + r"""([^\s"'\)<>\]]+)"""
        for match in re.finditer(pattern, content):
            key = match.group(1).rstrip(".,;)")
            if _ASSET_MARKER in key:
                keys.add(key)
    return keys


def is_asset_referenced(db, key: str) -> bool:
    """True if the key appears in any block content/draft/html or any version."""
    from promptops_app.database import Block, BlockVersion

    like = f"%{key}%"
    if db.query(Block.id).filter(Block.content.like(like)).first():
        return True
    if db.query(Block.id).filter(Block.draft_content.like(like)).first():
        return True
    if db.query(Block.id).filter(Block.content_html.like(like)).first():
        return True
    if db.query(BlockVersion.id).filter(BlockVersion.content.like(like)).first():
        return True
    return False


def delete_unreferenced(db, keys) -> None:
    """Delete each key from S3 only if it is referenced nowhere. Best-effort."""
    for key in keys or ():
        try:
            if not is_asset_referenced(db, key):
                delete_asset(key)
        except Exception:  # noqa: BLE001 — cleanup must never break the request
            _log.exception("asset_cleanup_failed  key=%s", key)


def cleanup_removed_assets(db, old_content: str | None, new_content: str | None) -> None:
    """On save: delete images that left this block and are now referenced nowhere."""
    if not settings.assets_s3_bucket:
        return
    removed = assets_in_content(old_content) - assets_in_content(new_content)
    if removed:
        delete_unreferenced(db, removed)


def collect_course_assets(db, course_id: int) -> set[str]:
    """
    All asset keys referenced by a course's blocks (content/draft/html) and their
    versions. Mirrors purge_course's block selection (by generation_id + module_id)
    so it captures every block the purge will delete. Call BEFORE purging.
    """
    if not settings.assets_s3_bucket:
        return set()
    from promptops_app.database import Block, BlockVersion, CourseModule, Generation

    gen_ids = [g.id for g in db.query(Generation.id).filter(Generation.course_id == course_id).all()]
    module_ids = [m.id for m in db.query(CourseModule.id).filter(CourseModule.course_id == course_id).all()]

    blocks = []
    if gen_ids:
        blocks += db.query(Block).filter(Block.generation_id.in_(gen_ids)).all()
    if module_ids:
        blocks += db.query(Block).filter(Block.module_id.in_(module_ids)).all()

    keys: set[str] = set()
    block_ids = set()
    for b in blocks:
        block_ids.add(b.id)
        keys |= assets_in_content(b.content)
        keys |= assets_in_content(getattr(b, "draft_content", None))
        keys |= assets_in_content(getattr(b, "content_html", None))

    if block_ids:
        rows = db.query(BlockVersion.content).filter(BlockVersion.block_id.in_(block_ids)).all()
        for (version_content,) in rows:
            keys |= assets_in_content(version_content)

    return keys
