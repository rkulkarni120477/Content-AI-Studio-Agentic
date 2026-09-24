"""Analytics Repository — cross-domain aggregation, logs, reviews, and reporting queries."""

from sqlalchemy import func

from promptops_app.database import (
    Block, CourseDesignDocument, Document, Generation, ModuleBlueprint,
    Project, PromptVersion, Review, SystemLog,
)


def count_generations_scoped(db, user_name: str = None, project_id: int = None, is_admin: bool = True,
                             *, date_from=None, date_to=None) -> int:
    q = db.query(Generation)
    if not is_admin and user_name:
        q = q.filter(Generation.created_by == user_name)
        if project_id:
            q = q.filter(Generation.project_id == project_id)
    if date_from is not None:
        q = q.filter(Generation.created_at >= date_from)
    if date_to is not None:
        q = q.filter(Generation.created_at <= date_to)
    return q.count()


def list_active_projects_for_analytics(db):
    return (
        db.query(Project)
        .filter(Project.is_active == True)
        .order_by(Project.name)
        .all()
    )


def get_project_metrics_batch(db, project_ids: list, *, date_from=None, date_to=None) -> dict:
    """Generation/block/cdd/blueprint counts for every project in `project_ids`,
    in 4 GROUP BY queries total instead of the old per-project loop.

    That loop (get_project_generation_ids + count_project_blocks +
    count_project_cdds + count_project_blueprints, one round each PER project)
    was the actual cost of GET /analytics/projects: 15 projects meant up to 60
    sequential DB round-trips, ~12s end to end once the dev DB is a real
    network hop away over the tunnel rather than localhost -- confirmed live
    in the request-timing logs, not assumed. Query count was the bottleneck,
    not rows scanned, so aggregating is what actually fixes it; date-range
    filtering was never the expensive part.

    Returns {project_id: {"generations": n, "blocks": n, "cdds": n, "blueprints": n}},
    defaulting to zero for a project_id with no matching rows at all (a plain
    GROUP BY never emits a row for one).
    """
    if not project_ids:
        return {}

    result = {pid: {"generations": 0, "blocks": 0, "cdds": 0, "blueprints": 0} for pid in project_ids}

    gen_q = db.query(Generation.project_id, func.count(Generation.id)).filter(
        Generation.project_id.in_(project_ids)
    )
    if date_from is not None:
        gen_q = gen_q.filter(Generation.created_at >= date_from)
    if date_to is not None:
        gen_q = gen_q.filter(Generation.created_at <= date_to)
    for pid, count in gen_q.group_by(Generation.project_id).all():
        result[pid]["generations"] = count

    # Blocks have no project_id of their own -- scoped via their parent
    # Generation's, joined in, same "blocks belong to a generation in range"
    # semantics generation_repository.count_blocks_scoped uses for the
    # summary counter (not Block's own created_at).
    block_q = (
        db.query(Generation.project_id, func.count(Block.id))
        .join(Block, Block.generation_id == Generation.id)
        .filter(Generation.project_id.in_(project_ids))
    )
    if date_from is not None:
        block_q = block_q.filter(Generation.created_at >= date_from)
    if date_to is not None:
        block_q = block_q.filter(Generation.created_at <= date_to)
    for pid, count in block_q.group_by(Generation.project_id).all():
        result[pid]["blocks"] = count

    cdd_q = db.query(CourseDesignDocument.project_id, func.count(CourseDesignDocument.id)).filter(
        CourseDesignDocument.project_id.in_(project_ids)
    )
    if date_from is not None:
        cdd_q = cdd_q.filter(CourseDesignDocument.created_at >= date_from)
    if date_to is not None:
        cdd_q = cdd_q.filter(CourseDesignDocument.created_at <= date_to)
    for pid, count in cdd_q.group_by(CourseDesignDocument.project_id).all():
        result[pid]["cdds"] = count

    bp_q = db.query(ModuleBlueprint.project_id, func.count(ModuleBlueprint.id)).filter(
        ModuleBlueprint.project_id.in_(project_ids)
    )
    if date_from is not None:
        bp_q = bp_q.filter(ModuleBlueprint.created_at >= date_from)
    if date_to is not None:
        bp_q = bp_q.filter(ModuleBlueprint.created_at <= date_to)
    for pid, count in bp_q.group_by(ModuleBlueprint.project_id).all():
        result[pid]["blueprints"] = count

    return result


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
