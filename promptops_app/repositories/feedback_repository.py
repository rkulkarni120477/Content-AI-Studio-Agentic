"""Feedback Repository — reviewer-feedback document and item database access.

Plain module-level functions taking the SQLAlchemy session as the first arg
(mirrors document_repository / cdd_repository). Tenant scoping is applied by
the router via ``app.core.tenant_context`` — these helpers stay tenant-agnostic.
"""

from sqlalchemy.orm import joinedload

from promptops_app.database import FeedbackDocument, FeedbackItem, ModuleBlueprint


# ── Writes ──────────────────────────────────────────────────────────────────

def create_document(
    db,
    *,
    project_id: int,
    course_id: int | None,
    filename: str,
    file_type: str | None,
    content: str | None,
    model_used: str | None,
    created_by: str | None,
    blueprint_id: int | None = None,
) -> FeedbackDocument:
    """Insert a feedback document row and flush to obtain its id."""
    doc = FeedbackDocument(
        project_id=project_id,
        course_id=course_id,
        blueprint_id=blueprint_id,
        filename=filename,
        file_type=file_type,
        content=content,
        model_used=model_used,
        item_count=0,
        status="active",
        created_by=created_by,
    )
    db.add(doc)
    db.flush()  # assigns doc.id without committing the transaction
    return doc


def create_items(db, document: FeedbackDocument, items_data: list[dict]) -> list[FeedbackItem]:
    """Insert extracted feedback items for a document and update its item_count.

    Each item inherits ``blueprint_id`` from the parent document (module scope).
    """
    items: list[FeedbackItem] = []
    for data in items_data:
        item = FeedbackItem(
            document_id=document.id,
            project_id=document.project_id,
            course_id=document.course_id,
            blueprint_id=document.blueprint_id,
            feedback_text=data["feedback_text"],
            source_location=data.get("source_location") or None,
            theme=data.get("theme") or None,
            sentiment=data.get("sentiment") or None,
            priority=data.get("priority") or None,
            status="active",
            created_by=document.created_by,
        )
        db.add(item)
        items.append(item)
    document.item_count = len(items)
    return items


# ── Lookups ───────────────────────────────────────────────────────────────────

def get_document_by_id(db, document_id: int):
    return db.query(FeedbackDocument).filter(FeedbackDocument.id == document_id).first()


def get_item_by_id(db, item_id: int):
    return db.query(FeedbackItem).filter(FeedbackItem.id == item_id).first()


def active_items_by_ids(db, ids: list[int]):
    """Query for active feedback items whose id is in ``ids``.

    Returns a Query (document eager-loaded) so the caller can layer tenant
    scoping (apply_tenant_filter) before executing. Order is stable by id.
    """
    return (
        db.query(FeedbackItem)
        .options(joinedload(FeedbackItem.document))
        .filter(FeedbackItem.id.in_(ids), FeedbackItem.status == "active")
        .order_by(FeedbackItem.id.asc())
    )


def list_active_items(
    db,
    *,
    course_id: int | None = None,
    blueprint_id: int | None = None,
    course_wide_only: bool = False,
):
    """Base query for active feedback items, newest first.

    Returns a Query so the caller can layer tenant scoping (apply_tenant_filter)
    on top before executing.

    ``blueprint_id`` filters to that module. ``course_wide_only`` filters to
    items with ``blueprint_id IS NULL`` (entire-course scope).
    """
    q = (
        db.query(FeedbackItem)
        .options(joinedload(FeedbackItem.document))  # avoid N+1 when reading document.filename
        .filter(FeedbackItem.status == "active")
    )
    if course_id is not None:
        q = q.filter(FeedbackItem.course_id == course_id)
    if course_wide_only:
        q = q.filter(FeedbackItem.blueprint_id.is_(None))
    elif blueprint_id is not None:
        q = q.filter(FeedbackItem.blueprint_id == blueprint_id)
    return q.order_by(FeedbackItem.id.desc())


def get_blueprint_labels(db, blueprint_ids: set[int]) -> dict[int, str]:
    """Return ``{blueprint_id: display_label}`` for the given ids."""
    if not blueprint_ids:
        return {}
    rows = (
        db.query(ModuleBlueprint)
        .filter(ModuleBlueprint.id.in_(blueprint_ids))
        .all()
    )
    return {bp.id: format_module_label(bp) for bp in rows}


def format_module_label(bp: ModuleBlueprint | None) -> str:
    """Human-readable module label for UI / API responses."""
    if bp is None:
        return "Entire course"
    title = (bp.module_title or bp.title or "").strip() or f"Blueprint {bp.id}"
    num = bp.module_number
    if num is not None:
        return f"Module {num} — {title}"
    return title
