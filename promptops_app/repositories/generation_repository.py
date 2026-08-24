"""Generation Repository — Generation, Block, Review, FeedbackSignal, and WorkflowEvent access."""

import re
from datetime import datetime, timezone

from sqlalchemy import func

from promptops_app.database import (
    Block,
    CourseModule,
    FeedbackSignal,
    Generation,
    Review,
    WorkflowEvent,
)


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


def list_course_generation_ids(db, course_id: int, limit: int = 200) -> list:
    """Generation IDs for a course, cheaply.

    Mirrors list_course_generations(course_id=...) row selection but selects only
    the id column, so the large output_text is never loaded when the caller just
    needs the IDs (e.g. to then fetch block summaries).
    """
    rows = (
        db.query(Generation.id)
        .filter(Generation.course_id == course_id)
        .order_by(Generation.created_at.desc())
        .limit(limit)
        .all()
    )
    return [r.id for r in rows]


def list_editor_generations(
    db,
    *,
    course_id: int | None = None,
    project_id: int | None = None,
    blueprint_id: int | None = None,
    cdd_id: int | None = None,
    limit: int = 200,
):
    """Editor page list — mirrors Streamlit list_generations_scoped + course context.

    Selects only the columns the dropdown needs; notably it skips the large
    ``output_text`` column, which would otherwise transfer the full generated
    lesson text for every generation just to render a picker.
    """
    q = db.query(
        Generation.id,
        Generation.topic,
        Generation.prompt_name,
        Generation.prompt_version,
        Generation.blueprint_id,
        Generation.cdd_id,
        Generation.created_at,
    ).order_by(Generation.created_at.desc())
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

def _block_sort_order():
    """Primary sort by display position; fall back to id for legacy rows."""
    return (Block.position.asc(), Block.id.asc())


def list_blocks_for_generation(db, gen_id: int):
    return (
        db.query(Block)
        .filter(Block.generation_id == gen_id)
        .order_by(*_block_sort_order())
        .all()
    )


def count_blocks_for_generation(db, gen_id: int) -> int:
    return db.query(Block).filter(Block.generation_id == gen_id).count()


def count_blocks_for_generations(db, gen_ids: list) -> dict:
    """Block counts for many generations in a SINGLE grouped query.

    Replaces calling count_blocks_for_generation() in a loop, which issued one
    round-trip per generation — crippling on a remote DB when a course has
    hundreds of generations (e.g. imported courses).
    """
    if not gen_ids:
        return {}
    rows = (
        db.query(Block.generation_id, func.count(Block.id))
        .filter(Block.generation_id.in_(gen_ids))
        .group_by(Block.generation_id)
        .all()
    )
    return {gen_id: count for gen_id, count in rows}


def list_course_block_summaries(db, gen_ids: list, limit: int = 500):
    """Lightweight block rows for list/summary views.

    Selects ONLY the columns list views need and computes the content preview
    and has_html flag in SQL, so the large text columns (content, content_html,
    eval_report, …) are never transferred from the DB. Materialising full Block
    rows for a whole course pulls megabytes over a remote connection; this keeps
    the payload tiny. Returns SQLAlchemy Row objects with named attributes.
    """
    if not gen_ids:
        return []
    return (
        db.query(
            Block.id,
            Block.block_label,
            Block.workflow_state,
            Block.position,
            Block.rating,
            Block.generation_id,
            func.substr(func.coalesce(Block.content, ""), 1, 300).label("content_preview"),
            (func.coalesce(func.length(func.trim(Block.content_html)), 0) > 0).label("has_html"),
        )
        .filter(Block.generation_id.in_(gen_ids))
        .order_by(*_block_sort_order())
        .limit(limit)
        .all()
    )


def list_blocks_for_gen_ids(db, gen_ids: list, limit: int = 500):
    if not gen_ids:
        return []
    return (
        db.query(Block)
        .filter(Block.generation_id.in_(gen_ids))
        .order_by(*_block_sort_order())
        .limit(limit)
        .all()
    )


def reorder_course_blocks(db, course_id: int, block_ids: list[int]) -> list[Block]:
    """Persist a new display order for blocks belonging to a course.

    Only blocks that belong to the course are updated.  Positions are 1-based.
    """
    if not block_ids:
        return []

    gens = list_course_generations(db, course_id=course_id)
    gen_ids = {g.id for g in gens}
    if not gen_ids:
        return []

    blocks = (
        db.query(Block)
        .filter(Block.id.in_(block_ids), Block.generation_id.in_(gen_ids))
        .all()
    )
    block_map = {b.id: b for b in blocks}
    ordered: list[Block] = []
    for idx, block_id in enumerate(block_ids, start=1):
        block = block_map.get(block_id)
        if block is None:
            continue
        block.position = idx
        ordered.append(block)

    db.commit()
    for block in ordered:
        db.refresh(block)
    return ordered


def get_block_by_id(db, block_id: int):
    return db.query(Block).filter(Block.id == block_id).first()


def build_blueprint_export_layout(
    db,
    course_id: int,
    *,
    workflow_state: str | None = None,
    exportable_only: bool = True,
) -> tuple[list[Block], list[tuple[str, list[int]]], list[dict]]:
    """Order published/approved blocks by CDD → Blueprint → component sequence.

    Returns
    -------
    ordered_blocks :
        Flat list of blocks in export order.
    modules_struct :
        ``[(module_title, [0-based indices into ordered_blocks]), ...]`` for IMSCC.
    module_views :
        UI-friendly list of
        ``{id, title, position, blocks: [Block, ...]}`` (one entry per blueprint
        that has at least one included block), plus any leftover blocks are
        returned separately by the caller via set difference if needed.
    """
    from promptops_app.core.constants import WorkflowState
    from promptops_app.database import get_active_blueprint_version
    from promptops_app.parsers.blueprint_parser import parse_blueprint_components
    from promptops_app.repositories import blueprint_repository

    gens = list_course_generations(db, course_id=course_id)
    gen_ids = [g.id for g in gens]
    all_blocks = list_blocks_for_gen_ids(db, gen_ids)

    if exportable_only:
        candidates = [
            b for b in all_blocks
            if (b.workflow_state or "").lower() in WorkflowState.EXPORTABLE
        ]
    else:
        candidates = list(all_blocks)

    if workflow_state:
        candidates = [
            b for b in candidates
            if (b.workflow_state or "").lower() == workflow_state.lower()
        ]

    if not candidates:
        return [], [], []

    blocks_by_gen: dict[int, list[Block]] = {}
    for b in candidates:
        blocks_by_gen.setdefault(b.generation_id, []).append(b)
    for blist in blocks_by_gen.values():
        blist.sort(key=lambda b: (b.position or 0, b.id))

    blueprints = blueprint_repository.list_blueprints_for_course(db, course_id=course_id)
    blueprints = sorted(
        blueprints,
        key=lambda bp: (bp.module_number if bp.module_number is not None else 9999, bp.id),
    )

    ordered: list[Block] = []
    modules_struct: list[tuple[str, list[int]]] = []
    module_views: list[dict] = []
    used_block_ids: set[int] = set()

    def _norm(s: str) -> str:
        return re.sub(r"\s+", " ", (s or "").strip().lower())

    for bp in blueprints:
        latest = list_latest_generations_for_blueprint(db, bp.id)
        by_type = {(g.block_type or "").strip().lower(): g for g in latest if g.block_type}
        by_topic = {_norm(g.topic): g for g in latest if g.topic}

        ver = get_active_blueprint_version(db, bp.id)
        components = parse_blueprint_components(ver) if ver else []

        module_blocks: list[Block] = []
        if components:
            for comp in components:
                value = (comp.get("value") or "").strip().lower()
                label = _norm(comp.get("label") or "")
                gen = by_type.get(value) or by_topic.get(label)
                if gen is None and label:
                    # Soft match: generation topic starts with / contains lesson label
                    for topic_key, g in by_topic.items():
                        if label in topic_key or topic_key in label:
                            gen = g
                            break
                if gen is None:
                    continue
                for b in blocks_by_gen.get(gen.id, []):
                    if b.id in used_block_ids:
                        continue
                    module_blocks.append(b)
                    used_block_ids.add(b.id)
        else:
            # No parseable components — include all latest gens for this blueprint
            for g in sorted(latest, key=lambda x: x.created_at or x.id):
                for b in blocks_by_gen.get(g.id, []):
                    if b.id in used_block_ids:
                        continue
                    module_blocks.append(b)
                    used_block_ids.add(b.id)

        if not module_blocks:
            continue

        title = (bp.module_title or bp.title or f"Module {bp.module_number}").strip()
        start = len(ordered)
        ordered.extend(module_blocks)
        modules_struct.append((title, list(range(start, start + len(module_blocks)))))
        module_views.append({
            "id": bp.id,
            "title": title,
            "position": bp.module_number or 0,
            "blocks": module_blocks,
        })

    leftover = [b for b in candidates if b.id not in used_block_ids]
    leftover.sort(key=lambda b: (b.position or 0, b.id))
    if leftover:
        start = len(ordered)
        ordered.extend(leftover)
        # Only add a Course Content module when there are also blueprint modules;
        # otherwise a single course-named module is fine (handled by IMSCC fallback).
        if modules_struct:
            modules_struct.append(("Course Content", list(range(start, start + len(leftover)))))

    return ordered, modules_struct, module_views


# ── Course modules (legacy manual grouping — kept for DB compat) ──────────────

def _now():
    return datetime.now(timezone.utc)


def list_course_modules(db, course_id: int) -> list[CourseModule]:
    """Return modules for a course ordered by display position."""
    return (
        db.query(CourseModule)
        .filter(CourseModule.course_id == course_id)
        .order_by(CourseModule.position.asc(), CourseModule.id.asc())
        .all()
    )


def get_module_by_id(db, module_id: int) -> CourseModule | None:
    return db.query(CourseModule).filter(CourseModule.id == module_id).first()


def create_course_module(db, course_id: int, title: str) -> CourseModule:
    """Append a new module to the end of the course's module list."""
    existing = list_course_modules(db, course_id)
    next_pos = (max((m.position or 0) for m in existing) + 1) if existing else 1
    module = CourseModule(
        course_id=course_id,
        title=(title or "Untitled Module").strip()[:255],
        position=next_pos,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(module)
    db.commit()
    db.refresh(module)
    return module


def rename_course_module(db, module_id: int, title: str) -> CourseModule | None:
    module = get_module_by_id(db, module_id)
    if module is None:
        return None
    module.title = (title or "Untitled Module").strip()[:255]
    module.updated_at = _now()
    db.commit()
    db.refresh(module)
    return module


def delete_course_module(db, module_id: int) -> bool:
    """Delete a module; its blocks become unassigned (module_id -> NULL)."""
    module = get_module_by_id(db, module_id)
    if module is None:
        return False
    db.query(Block).filter(Block.module_id == module_id).update(
        {Block.module_id: None}, synchronize_session=False,
    )
    db.delete(module)
    db.commit()
    return True


def reorder_course_modules(db, course_id: int, module_ids: list[int]) -> list[CourseModule]:
    """Persist a new display order for a course's modules (1-based positions)."""
    if not module_ids:
        return []
    modules = (
        db.query(CourseModule)
        .filter(CourseModule.id.in_(module_ids), CourseModule.course_id == course_id)
        .all()
    )
    module_map = {m.id: m for m in modules}
    ordered: list[CourseModule] = []
    for idx, module_id in enumerate(module_ids, start=1):
        module = module_map.get(module_id)
        if module is None:
            continue
        module.position = idx
        module.updated_at = _now()
        ordered.append(module)
    db.commit()
    for module in ordered:
        db.refresh(module)
    return ordered


def save_course_module_layout(
    db,
    course_id: int,
    layout: list[dict],
) -> int:
    """Persist module order + block placement in one operation.

    ``layout`` is an ordered list of ``{"module_id": int, "block_ids": [int]}``.
    Modules are repositioned by list order; each block is assigned to its module
    and given a global 1-based ``position`` following the flattened order so the
    within-module ordering is preserved. Blocks omitted from every module are
    unassigned (``module_id`` -> NULL) but keep their existing position.

    Returns the number of blocks updated.
    """
    gens = list_course_generations(db, course_id=course_id)
    gen_ids = {g.id for g in gens}
    if not gen_ids:
        return 0

    valid_module_ids = {
        m.id for m in db.query(CourseModule).filter(
            CourseModule.course_id == course_id,
        ).all()
    }

    # Reposition modules by their order in the layout.
    module_order = [row["module_id"] for row in layout if row.get("module_id") in valid_module_ids]
    reorder_course_modules(db, course_id, module_order)

    # Clear existing assignments so blocks dropped into "unassigned" are cleared.
    db.query(Block).filter(Block.generation_id.in_(gen_ids)).update(
        {Block.module_id: None}, synchronize_session=False,
    )

    # Assign blocks to modules with a global running position.
    updated = 0
    running = 0
    assigned_ids: set[int] = set()
    for row in layout:
        module_id = row.get("module_id")
        if module_id not in valid_module_ids:
            continue
        block_ids = row.get("block_ids") or []
        if not block_ids:
            continue
        blocks = (
            db.query(Block)
            .filter(Block.id.in_(block_ids), Block.generation_id.in_(gen_ids))
            .all()
        )
        block_map = {b.id: b for b in blocks}
        for block_id in block_ids:
            block = block_map.get(block_id)
            if block is None:
                continue
            running += 1
            block.module_id = module_id
            block.position = running
            assigned_ids.add(block.id)
            updated += 1

    db.commit()
    return updated


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
