"""Document Repository — Document database access with pagination support."""

from promptops_app.database import Document


# ── Counts ────────────────────────────────────────────────────────────────────

def count_documents(db, *, date_from=None, date_to=None) -> int:
    q = db.query(Document)
    if date_from is not None:
        q = q.filter(Document.uploaded_at >= date_from)
    if date_to is not None:
        q = q.filter(Document.uploaded_at < date_to)
    return q.count()


def count_active_documents(db) -> int:
    return db.query(Document).filter(Document.status == "active").count()


def count_archived_documents(db) -> int:
    return db.query(Document).filter(Document.status == "archived").count()


def count_documents_filtered(
    db,
    status: str = None,
    tag: str = None,
    search: str = None,
) -> int:
    q = db.query(Document)
    if status:
        q = q.filter(Document.status == status)
    if tag:
        q = q.filter(Document.doc_tag == tag)
    if search:
        q = q.filter(Document.filename.ilike(f"%{search}%"))
    return q.count()


def list_distinct_document_tags(db) -> list:
    rows = db.query(Document.doc_tag).distinct().all()
    return sorted({(r[0] or "general") for r in rows})


# ── Paginated lists ───────────────────────────────────────────────────────────

def list_active_documents(db, limit: int = 200, offset: int = 0):
    """Return active documents.  Use limit/offset for pagination; default
    limit of 200 prevents full-table loads in style dropdowns."""
    q = (
        db.query(Document)
        .filter(Document.status == "active")
        .order_by(Document.uploaded_at.desc())
    )
    if offset:
        q = q.offset(offset)
    return q.limit(limit).all()


def list_all_documents(db, limit: int = 200, offset: int = 0):
    q = db.query(Document).order_by(Document.uploaded_at.desc())
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
):
    """Return documents matching the given filters, ordered by upload date."""
    q = db.query(Document).order_by(Document.uploaded_at.desc())
    if status:
        q = q.filter(Document.status == status)
    if tag:
        q = q.filter(Document.doc_tag == tag)
    if search:
        q = q.filter(Document.filename.ilike(f"%{search}%"))
    if offset:
        q = q.offset(offset)
    return q.limit(limit).all()


# ── Lookups ───────────────────────────────────────────────────────────────────

def get_document_by_id(db, document_id: int):
    return db.query(Document).filter(Document.id == document_id).first()


def get_document_by_filename(db, filename: str):
    return db.query(Document).filter(Document.filename == filename).first()


def get_documents_by_filenames(db, filenames: list):
    """Resolve library filenames for prompt context injection.

    Filtered to active documents only: this is generation-context resolution,
    not a lookup-by-id-for-editing -- an archived (soft-deleted) document must
    stop contributing content once removed from the Source Library, even
    though its filename is still sitting in an old context_document_names list.
    """
    if not filenames:
        return []
    return (
        db.query(Document)
        .filter(Document.filename.in_(filenames), Document.status == "active")
        .all()
    )


def list_recent_uploads(db, limit: int = 20):
    return (
        db.query(Document)
        .order_by(Document.uploaded_at.desc())
        .limit(limit)
        .all()
    )
