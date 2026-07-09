"""Document Repository — Document database access with pagination support."""

from app.core.tenant_context import apply_tenant_filter
from promptops_app.database import Document


# ── Counts ────────────────────────────────────────────────────────────────────

def count_documents(db, tenant_id=None, is_platform_admin=False) -> int:
    q = db.query(Document)
    return apply_tenant_filter(q, Document, tenant_id, is_platform_admin).count()


def count_active_documents(db, tenant_id=None, is_platform_admin=False) -> int:
    q = db.query(Document).filter(Document.status == "active")
    return apply_tenant_filter(q, Document, tenant_id, is_platform_admin).count()


def count_archived_documents(db, tenant_id=None, is_platform_admin=False) -> int:
    q = db.query(Document).filter(Document.status == "archived")
    return apply_tenant_filter(q, Document, tenant_id, is_platform_admin).count()


def count_documents_filtered(
    db,
    status: str = None,
    tag: str = None,
    search: str = None,
    tenant_id=None,
    is_platform_admin=False,
) -> int:
    q = db.query(Document)
    if status:
        q = q.filter(Document.status == status)
    if tag:
        q = q.filter(Document.doc_tag == tag)
    if search:
        q = q.filter(Document.filename.ilike(f"%{search}%"))
    return apply_tenant_filter(q, Document, tenant_id, is_platform_admin).count()


def list_distinct_document_tags(db, tenant_id=None, is_platform_admin=False) -> list:
    q = db.query(Document.doc_tag).distinct()
    q = apply_tenant_filter(q, Document, tenant_id, is_platform_admin)
    rows = q.all()
    return sorted({(r[0] or "general") for r in rows})


# ── Paginated lists ───────────────────────────────────────────────────────────

def list_active_documents(
    db,
    limit: int = 200,
    offset: int = 0,
    tenant_id=None,
    is_platform_admin=False,
):
    """Return active documents scoped to tenant.  Default limit prevents full-table loads."""
    q = (
        db.query(Document)
        .filter(Document.status == "active")
        .order_by(Document.uploaded_at.desc())
    )
    q = apply_tenant_filter(q, Document, tenant_id, is_platform_admin)
    if offset:
        q = q.offset(offset)
    return q.limit(limit).all()


def list_all_documents(
    db,
    limit: int = 200,
    offset: int = 0,
    tenant_id=None,
    is_platform_admin=False,
):
    q = db.query(Document).order_by(Document.uploaded_at.desc())
    q = apply_tenant_filter(q, Document, tenant_id, is_platform_admin)
    if offset:
        q = q.offset(offset)
    return q.limit(limit).all()


def list_documents_filtered(
    db,
    status: str = None,
    tag: str = None,
    search: str = None,
    limit: int = 20,
    offset: int = 0,
    tenant_id=None,
    is_platform_admin=False,
):
    """Return documents matching the given filters, ordered by upload date."""
    q = db.query(Document).order_by(Document.uploaded_at.desc())
    if status:
        q = q.filter(Document.status == status)
    if tag:
        q = q.filter(Document.doc_tag == tag)
    if search:
        q = q.filter(Document.filename.ilike(f"%{search}%"))
    q = apply_tenant_filter(q, Document, tenant_id, is_platform_admin)
    if offset:
        q = q.offset(offset)
    return q.limit(limit).all()


# ── Lookups ───────────────────────────────────────────────────────────────────

def get_document_by_id(db, document_id: int, tenant_id=None, is_platform_admin=False):
    q = db.query(Document).filter(Document.id == document_id)
    return apply_tenant_filter(q, Document, tenant_id, is_platform_admin).first()


def get_document_by_filename(db, filename: str, tenant_id=None, is_platform_admin=False):
    q = db.query(Document).filter(Document.filename == filename)
    return apply_tenant_filter(q, Document, tenant_id, is_platform_admin).first()


def get_documents_by_filenames(db, filenames: list, tenant_id=None, is_platform_admin=False):
    if not filenames:
        return []
    q = db.query(Document).filter(Document.filename.in_(filenames))
    return apply_tenant_filter(q, Document, tenant_id, is_platform_admin).all()


def list_recent_uploads(db, limit: int = 20, tenant_id=None, is_platform_admin=False):
    q = db.query(Document).order_by(Document.uploaded_at.desc())
    q = apply_tenant_filter(q, Document, tenant_id, is_platform_admin)
    return q.limit(limit).all()
