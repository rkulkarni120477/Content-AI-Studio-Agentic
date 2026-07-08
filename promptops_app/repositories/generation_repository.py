"""Generation Repository — Generation, Block, Review, FeedbackSignal, and WorkflowEvent access."""

from promptops_app.database import Block, FeedbackSignal, Generation, Review, WorkflowEvent


# ── Generation ────────────────────────────────────────────────────────────────

def list_generations_scoped(
    db,
    *,
    blueprint_id: int = None,
    cdd_id: int = None,
    limit: int = 50,
):
    q = db.query(Generation).order_by(Generation.created_at.desc())
    if blueprint_id:
        q = q.filter(Generation.blueprint_id == blueprint_id)
    elif cdd_id:
        q = q.filter(Generation.cdd_id == cdd_id)
    return q.limit(limit).all()


def list_generations_all_scoped(
    db,
    *,
    user_name: str = None,
    project_id: int = None,
    is_admin: bool = True,
    limit: int = 1000,
):
    """Return scoped generations up to `limit` — used for analytics leaderboard.

    Capped at 1 000 by default to prevent full-table scans on busy tenants.
    """
    q = db.query(Generation)
    if not is_admin and user_name:
        q = q.filter(Generation.created_by == user_name)
        if project_id:
            q = q.filter(Generation.project_id == project_id)
    return q.order_by(Generation.created_at.desc()).limit(limit).all()


def list_recent_generations(
    db,
    *,
    user_name: str = None,
    project_id: int = None,
    is_admin: bool = True,
    limit: int = 20,
):
    q = db.query(Generation)
    if not is_admin and user_name:
        q = q.filter(Generation.created_by == user_name)
        if project_id:
            q = q.filter(Generation.project_id == project_id)
    return q.order_by(Generation.created_at.desc()).limit(limit).all()


def get_generation_by_id(db, gen_id: int):
    return db.query(Generation).filter(Generation.id == gen_id).first()


def list_course_generations(
    db,
    project_id: int | None = None,
    course_id: int | None = None,
    limit: int = 200,
):
    """List generations for a course; project_id is optional when course_id is set."""
    q = db.query(Generation)
    if project_id is not None:
        q = q.filter(Generation.project_id == project_id)
    if course_id is not None:
        q = q.filter(Generation.course_id == course_id)
    return q.order_by(Generation.created_at.desc()).limit(limit).all()


def list_editor_generations(
    db,
    *,
    course_id: int | None = None,
    project_id: int | None = None,
    blueprint_id: int | None = None,
    cdd_id: int | None = None,
    limit: int = 200,
):
    """Editor page list — mirrors Streamlit list_generations_scoped + course context."""
    q = db.query(Generation).order_by(Generation.created_at.desc())
    if course_id is not None:
        q = q.filter(Generation.course_id == course_id)
    if project_id is not None:
        q = q.filter(Generation.project_id == project_id)
    if blueprint_id:
        q = q.filter(Generation.blueprint_id == blueprint_id)
    elif cdd_id:
        q = q.filter(Generation.cdd_id == cdd_id)
    return q.limit(limit).all()


def list_latest_generations_for_blueprint(db, blueprint_id: int, limit: int = 500):
    """One Generation per distinct topic (lesson) within a module — the most recent attempt.

    Modules are regenerated/attempted repeatedly (same topic, many rows); this collapses
    that history down to the current version of each lesson for module-level export.
    """
    gens = (
        db.query(Generation)
        .filter(Generation.blueprint_id == blueprint_id)
        .order_by(Generation.created_at.desc())
        .limit(limit)
        .all()
    )
    seen_topics = set()
    latest = []
    for g in gens:
        if g.topic in seen_topics:
            continue
        seen_topics.add(g.topic)
        latest.append(g)
    return latest


def list_generations_for_project(db, project_id: int, limit: int = 500):
    """Return generation IDs for a project. Capped to avoid OOM on large tenants."""
    return (
        db.query(Generation)
        .filter(Generation.project_id == project_id)
        .order_by(Generation.created_at.desc())
        .limit(limit)
        .all()
    )


def list_generations_by_ids(db, gen_ids: list):
    if not gen_ids:
        return []
    return db.query(Generation).filter(Generation.id.in_(gen_ids)).all()


# ── Block ────────────────────────────────────────────────────────────────────

def list_blocks_for_generation(db, gen_id: int):
    return (
        db.query(Block)
        .filter(Block.generation_id == gen_id)
        .order_by(Block.id.asc())
        .all()
    )


def count_blocks_for_generation(db, gen_id: int) -> int:
    return db.query(Block).filter(Block.generation_id == gen_id).count()


def list_blocks_for_gen_ids(db, gen_ids: list, limit: int = 500):
    if not gen_ids:
        return []
    return (
        db.query(Block)
        .filter(Block.generation_id.in_(gen_ids))
        .order_by(Block.id.asc())
        .limit(limit)
        .all()
    )


def get_block_by_id(db, block_id: int):
    return db.query(Block).filter(Block.id == block_id).first()


def list_workflow_blocks_scoped(
    db,
    *,
    user_name: str,
    project_id: int,
    is_admin: bool,
    is_lead: bool = False,
    course_id: int = None,
    state_filter: str = None,
    page_size: int = 200,
    block_limit: int = 500,
):
    gen_q = db.query(Generation)
    if not is_admin:
        if is_lead:
            # Lead sees all content in their assigned project
            if project_id:
                gen_q = gen_q.filter(Generation.project_id == project_id)
        else:
            # Author sees only their own content
            gen_q = gen_q.filter(Generation.created_by == user_name)
            if project_id:
                gen_q = gen_q.filter(Generation.project_id == project_id)
    if course_id:
        gen_q = gen_q.filter(Generation.course_id == course_id)
    gen_ids = [
        r[0] for r in gen_q
        .with_entities(Generation.id)
        .order_by(Generation.created_at.desc())
        .limit(page_size)
        .all()
    ]
    if not gen_ids:
        return []
    q = db.query(Block).filter(Block.generation_id.in_(gen_ids))
    if state_filter:
        q = q.filter(Block.workflow_state.ilike(state_filter))
    return q.order_by(Block.updated_at.desc()).limit(block_limit).all()


def count_blocks_scoped(
    db,
    *,
    user_name: str = None,
    project_id: int = None,
    is_admin: bool = True,
) -> int:
    q = db.query(Generation)
    if not is_admin and user_name:
        q = q.filter(Generation.created_by == user_name)
        if project_id:
            q = q.filter(Generation.project_id == project_id)
    gen_ids = [r[0] for r in q.with_entities(Generation.id).all()]
    if not gen_ids:
        return 0
    return db.query(Block).filter(Block.generation_id.in_(gen_ids)).count()


def get_block_ratings_scoped(
    db,
    *,
    user_name: str = None,
    project_id: int = None,
    is_admin: bool = True,
):
    q = db.query(Generation)
    if not is_admin and user_name:
        q = q.filter(Generation.created_by == user_name)
        if project_id:
            q = q.filter(Generation.project_id == project_id)
    gen_ids = [r[0] for r in q.with_entities(Generation.id).all()]
    if not gen_ids:
        return []
    return db.query(Block.rating).filter(Block.generation_id.in_(gen_ids)).all()


def list_rated_blocks_for_gen_ids(db, gen_ids: list):
    if not gen_ids:
        return []
    return (
        db.query(Block)
        .filter(Block.generation_id.in_(gen_ids), Block.rating > 0)
        .all()
    )


# ── Review ───────────────────────────────────────────────────────────────────

def list_reviews_for_block(db, block_id: int):
    return (
        db.query(Review)
        .filter(Review.block_id == block_id)
        .order_by(Review.created_at.desc())
        .all()
    )


def count_reviews(db) -> int:
    return db.query(Review).count()


def count_approved_reviews(db) -> int:
    return db.query(Review).filter(Review.approved == True).count()


def avg_review_score(db) -> float:
    from sqlalchemy import func
    result = db.query(func.avg(Review.score)).filter(Review.score.isnot(None)).scalar()
    return float(result) if result else 0.0


def list_recent_reviews(db, limit: int = 30, offset: int = 0):
    return (
        db.query(Review)
        .order_by(Review.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


# ── FeedbackSignal ───────────────────────────────────────────────────────────

def count_feedback_signals(db) -> int:
    return db.query(FeedbackSignal).count()


def count_feedback_signals_by_scope(db, scope: str) -> int:
    return db.query(FeedbackSignal).filter(FeedbackSignal.feedback_scope == scope).count()


def list_feedback_signals_filtered(
    db,
    scope: str = None,
    limit: int = 25,
    offset: int = 0,
):
    q = db.query(FeedbackSignal).order_by(FeedbackSignal.created_at.desc())
    if scope:
        q = q.filter(FeedbackSignal.feedback_scope == scope)
    return q.offset(offset).limit(limit).all()


def list_all_feedback_signals(db, limit: int = 200, offset: int = 0):
    """Legacy accessor — bounded at limit=200 by default."""
    return (
        db.query(FeedbackSignal)
        .order_by(FeedbackSignal.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


def list_learning_signals(db, block_type: str, limit: int = 5):
    return (
        db.query(FeedbackSignal)
        .filter(
            FeedbackSignal.feedback_scope == "learning",
            FeedbackSignal.block_type == block_type,
        )
        .order_by(FeedbackSignal.created_at.desc())
        .limit(limit)
        .all()
    )


# ── WorkflowEvent ────────────────────────────────────────────────────────────

def list_workflow_events_for_block(db, block_id: int, limit: int = 5):
    return (
        db.query(WorkflowEvent)
        .filter(WorkflowEvent.block_id == block_id)
        .order_by(WorkflowEvent.created_at.desc())
        .limit(limit)
        .all()
    )
