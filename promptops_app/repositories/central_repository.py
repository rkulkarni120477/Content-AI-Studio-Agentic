"""Data-access layer for the Central Repository module."""

from datetime import datetime, timezone
from typing import Optional

from app.core.tenant_context import apply_tenant_filter
from promptops_app.database import CentralRepository


# ── Read ──────────────────────────────────────────────────────────────────────

def list_items(
    db,
    *,
    status: Optional[str] = "active",
    item_type: Optional[str] = None,
    project_id: Optional[int] = None,
    cluster_id: Optional[int] = None,
    client_name: Optional[str] = None,
    search: Optional[str] = None,
    tag: Optional[str] = None,
    limit: int = 500,
    offset: int = 0,
    tenant_id=None,
    is_platform_admin=False,
) -> list:
    q = db.query(CentralRepository)
    if status:
        q = q.filter(CentralRepository.status == status)
    if item_type:
        q = q.filter(CentralRepository.item_type == item_type)
    if project_id:
        q = q.filter(CentralRepository.project_id == project_id)
    if cluster_id:
        q = q.filter(CentralRepository.cluster_id == cluster_id)
    if client_name:
        q = q.filter(CentralRepository.client_name.ilike(f"%{client_name}%"))
    if tag:
        q = q.filter(CentralRepository.tags.ilike(f"%{tag}%"))
    if search:
        term = f"%{search}%"
        q = q.filter(
            CentralRepository.title.ilike(term)
            | CentralRepository.content.ilike(term)
            | CentralRepository.tags.ilike(term)
            | CentralRepository.description.ilike(term)
        )
    q = apply_tenant_filter(q, CentralRepository, tenant_id, is_platform_admin)
    return (
        q.order_by(CentralRepository.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


def count_items(db, *, status: Optional[str] = None, tenant_id=None, is_platform_admin=False) -> int:
    q = db.query(CentralRepository)
    if status:
        q = q.filter(CentralRepository.status == status)
    return apply_tenant_filter(q, CentralRepository, tenant_id, is_platform_admin).count()


def get_by_id(db, item_id: int, tenant_id=None, is_platform_admin=False) -> Optional[CentralRepository]:
    q = db.query(CentralRepository).filter(CentralRepository.id == item_id)
    return apply_tenant_filter(q, CentralRepository, tenant_id, is_platform_admin).first()


def distinct_clients(db) -> list[str]:
    rows = (
        db.query(CentralRepository.client_name)
        .filter(CentralRepository.client_name.isnot(None))
        .distinct()
        .all()
    )
    return sorted({r[0] for r in rows if r[0]})


def distinct_clusters(db) -> list[str]:
    rows = (
        db.query(CentralRepository.cluster_name)
        .filter(CentralRepository.cluster_name.isnot(None))
        .distinct()
        .all()
    )
    return sorted({r[0] for r in rows if r[0]})


# ── Write ─────────────────────────────────────────────────────────────────────

def create_item(
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
) -> CentralRepository:
    now = datetime.now(timezone.utc)
    item = CentralRepository(
        title=title,
        item_type=item_type,
        content=content,
        description=description,
        source_module=source_module,
        project_id=project_id,
        cluster_id=cluster_id,
        course_id=course_id,
        client_name=client_name,
        cluster_name=cluster_name,
        tags=tags,
        status="active",
        usage_count=0,
        created_by=created_by,
        created_at=now,
        updated_at=now,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def update_item(db, item_id: int, **fields) -> Optional[CentralRepository]:
    item = get_by_id(db, item_id)
    if not item:
        return None
    allowed = {
        "title", "item_type", "content", "description",
        "source_module", "client_name", "cluster_name",
        "tags", "status",
    }
    for key, val in fields.items():
        if key in allowed:
            setattr(item, key, val)
    item.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(item)
    return item


def archive_item(db, item_id: int) -> Optional[CentralRepository]:
    return update_item(db, item_id, status="archived")


def delete_item(db, item_id: int) -> bool:
    item = get_by_id(db, item_id)
    if not item:
        return False
    db.delete(item)
    db.commit()
    return True


def record_reuse(db, item_id: int) -> None:
    item = get_by_id(db, item_id)
    if item:
        item.usage_count = (item.usage_count or 0) + 1
        item.last_used_at = datetime.now(timezone.utc)
        db.commit()
