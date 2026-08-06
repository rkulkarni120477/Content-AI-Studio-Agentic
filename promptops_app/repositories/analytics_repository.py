"""Analytics Repository — cross-domain aggregation, logs, reviews, and reporting queries."""

from promptops_app.database import (
    Block, CourseDesignDocument, Document, Generation, ModuleBlueprint,
    Project, PromptVersion, Review, SystemLog,
)


def count_generations_scoped(db, user_name: str = None, project_id: int = None, is_admin: bool = True) -> int:
    q = db.query(Generation)
    if not is_admin and user_name:
        q = q.filter(Generation.created_by == user_name)
        if project_id:
            q = q.filter(Generation.project_id == project_id)
    return q.count()


def list_active_projects_for_analytics(db):
    return (
        db.query(Project)
        .filter(Project.is_active == True)
        .order_by(Project.name)
        .all()
    )


def get_project_generation_ids(db, project_id: int, limit: int = 2000) -> list:
    """Return up to `limit` generation IDs for the project (id-only query)."""
    rows = (
        db.query(Generation.id)
        .filter(Generation.project_id == project_id)
        .limit(limit)
        .all()
    )
    return [r[0] for r in rows]


def count_project_blocks(db, gen_ids: list) -> int:
    if not gen_ids:
        return 0
    return db.query(Block).filter(Block.generation_id.in_(gen_ids)).count()


def count_project_cdds(db, project_id: int) -> int:
    return (
        db.query(CourseDesignDocument)
        .filter(CourseDesignDocument.project_id == project_id)
        .count()
    )


def count_project_blueprints(db, project_id: int) -> int:
    return (
        db.query(ModuleBlueprint)
        .filter(ModuleBlueprint.project_id == project_id)
        .count()
    )


def list_cdd_blueprint_events(db, limit: int = 40, offset: int = 0):
    return (
        db.query(SystemLog)
        .filter(
            SystemLog.event_type.in_([
                "cdd_created", "cdd_version_committed",
                "blueprint_created", "blueprint_version_committed",
            ])
        )
        .order_by(SystemLog.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


# ── Actors / filters ──────────────────────────────────────────────────────────

def list_distinct_actors(db) -> list:
    rows = db.query(SystemLog.actor).distinct().all()
    return sorted({r[0] for r in rows if r[0]})


# ── Reviews ───────────────────────────────────────────────────────────────────

def count_reviews(db) -> int:
    return db.query(Review).count()


def list_recent_reviews(db, limit: int = 30, offset: int = 0):
    return (
        db.query(Review)
        .order_by(Review.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


# ── Uploads / versions ────────────────────────────────────────────────────────

def list_recent_doc_uploads(db, limit: int = 20):
    return (
        db.query(Document)
        .order_by(Document.uploaded_at.desc())
        .limit(limit)
        .all()
    )


def list_recent_prompt_versions(db, limit: int = 20):
    return (
        db.query(PromptVersion)
        .order_by(PromptVersion.created_at.desc())
        .limit(limit)
        .all()
    )
