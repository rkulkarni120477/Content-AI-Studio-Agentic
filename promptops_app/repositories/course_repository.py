"""Course Repository — Course database access."""

from __future__ import annotations

import logging

from promptops_app.database import (
    Block,
    BlockComment,
    BlockVersion,
    BlueprintVersion,
    CentralRepository,
    Course,
    CourseDesignDocument,
    CourseImport,
    CourseModule,
    CourseUserAssignment,
    CDDVersion,
    FeedbackDocument,
    FeedbackItem,
    FeedbackSignal,
    Generation,
    GenerationJob,
    ImportProvenance,
    ModuleBlueprint,
    PlagiarismReport,
    PromptFixing,
    Review,
    UserPromptHistory,
    UserPromptPreference,
    WorkflowEvent,
)

_log = logging.getLogger(__name__)


def get_course_by_id(db, course_id: int):
    return db.query(Course).filter(Course.id == course_id).first()


def _is_empty_import_shell():
    """Boolean condition: an archived course that is an import's shell and
    has NO reconstructed content at all — the shell an import job never got
    to build anything into. Excluded even from ``include_archived=True``
    views: unlike a course a user chose to archive, this was never a real
    course to manage, and its own CourseImport row already keeps the
    failure's audit trail.

    Scoped to ``Course.import_id.isnot(None)`` — without it this also caught
    every OTHER archived-and-contentless course: a scratch title archived
    before anything was generated, or a normal CDD-→Blueprint-→generate
    waypoint (CDD + Blueprint done, no blocks generated yet) archived mid-flow.
    Neither of those has a CourseImport behind it; only an import shell does,
    and ``start_import`` sets ``import_id`` before the job is ever enqueued,
    so it can't be left unset by a race.

    Content-keyed, not CourseImport.status or GenerationJob status — those
    are mutable by unrelated paths and both over- and under-fire if reused
    here. Concretely: (a) a course that DID reconstruct before a later stage
    failed keeps CourseImport.status=="failed" forever, so a status-based
    rule would wrongly re-catch it the moment a user archives it through the
    normal archive action; (b) POST .../retry unconditionally flips
    CourseImport.status to "completed" even when it rebuilt nothing (no
    modules to work with), so a status-based rule would wrongly stop
    excluding an empty shell the instant someone retries it. "Zero
    CourseModule/Generation rows" can't be un-set by anything except real
    reconstruction, so neither case can happen here.

    Correlated NOT EXISTS rather than a flat NOT IN subquery: the previous
    ``~Course.id.in_(db.query(Generation.course_id)...)`` shape builds the
    full list of every course_id in the (highest-row-count) generations table
    before comparing — a NOT EXISTS lets the planner use the per-course_id
    index instead. generation.course_id is nullable (course-less generations
    exist elsewhere), but a correlated NOT EXISTS has no NULL-in-NOT-IN
    footgun the way a flat subquery does, so no extra filter is needed there.

    ``Course.is_active`` is a nullable column with only a Python-side default,
    so ``isnot(True)`` (not ``== False``) is used to also catch a NULL row —
    otherwise a NULL-is_active shell would silently fall through this filter.

    A row this excludes isn't stranded: it's just absent from every listing
    (including include_archived=True). ``DELETE .../courses/{id}`` still
    reaches it directly for support/debugging cleanup.
    """
    from sqlalchemy import exists

    return (
        Course.is_active.isnot(True)
        & Course.import_id.isnot(None)
        & ~exists().where(CourseModule.course_id == Course.id)
        & ~exists().where(Generation.course_id == Course.id)
    )


def list_courses_for_project(db, project_id: int):
    # No include_archived option here, so is_active==True alone already
    # excludes a failed import shell — no need for the extra join.
    return (
        db.query(Course)
        .filter(Course.project_id == project_id, Course.is_active == True)  # noqa: E712
        .order_by(Course.name)
        .all()
    )


def list_courses_for_cluster(db, cluster_id: int, *, include_archived: bool = False):
    q = db.query(Course).filter(Course.cluster_id == cluster_id)
    if not include_archived:
        # The exclusion below only ever matches non-True is_active rows, so on
        # this default path it can only be a no-op — skip it rather than pay
        # for two correlated subqueries per row that can never change the result.
        q = q.filter(Course.is_active == True)  # noqa: E712
    else:
        q = q.filter(~_is_empty_import_shell())
    return q.order_by(Course.created_at.asc()).all()


def purge_course(db, course_id: int) -> None:
    """Permanently delete a course and all owned content.

    Caller must ensure the course exists (typically already archived).
    Does **not** delete shared Style rows — only clears ``active_style_id``.
    """
    # No early return when the course row is already gone: a still-running
    # import job can keep writing rows for a course_id after it was purged
    # (e.g. blueprints), and a second purge must sweep those. Everything below
    # filters by course_id, so it's safe with no course row.
    course = get_course_by_id(db, course_id)

    if course is not None:
        # Clear soft pointers so design-doc / style deletes are not blocked by the course row.
        course.active_cdd_id = None
        course.active_blueprint_id = None
        course.active_style_id = None
        course.import_id = None
        db.flush()

    gen_ids = [
        g.id for g in db.query(Generation.id).filter(Generation.course_id == course_id).all()
    ]
    module_ids = [
        m.id for m in db.query(CourseModule.id).filter(CourseModule.course_id == course_id).all()
    ]

    block_id_set: set[int] = set()
    if gen_ids:
        block_id_set.update(
            b.id for b in db.query(Block.id).filter(Block.generation_id.in_(gen_ids)).all()
        )
    if module_ids:
        block_id_set.update(
            b.id for b in db.query(Block.id).filter(Block.module_id.in_(module_ids)).all()
        )
    block_ids = list(block_id_set)

    if block_ids:
        db.query(WorkflowEvent).filter(WorkflowEvent.block_id.in_(block_ids)).delete(
            synchronize_session=False
        )
        db.query(FeedbackSignal).filter(FeedbackSignal.block_id.in_(block_ids)).delete(
            synchronize_session=False
        )
        db.query(Review).filter(Review.block_id.in_(block_ids)).delete(synchronize_session=False)
        db.query(PlagiarismReport).filter(PlagiarismReport.block_id.in_(block_ids)).delete(
            synchronize_session=False
        )
        db.query(BlockComment).filter(BlockComment.block_id.in_(block_ids)).delete(
            synchronize_session=False
        )
        db.query(BlockVersion).filter(BlockVersion.block_id.in_(block_ids)).delete(
            synchronize_session=False
        )
        if module_ids:
            db.query(Block).filter(Block.module_id.in_(module_ids)).update(
                {Block.module_id: None}, synchronize_session=False
            )
        db.query(Block).filter(Block.id.in_(block_ids)).delete(synchronize_session=False)

    if gen_ids:
        db.query(Review).filter(Review.generation_id.in_(gen_ids)).delete(
            synchronize_session=False
        )
        db.query(FeedbackSignal).filter(FeedbackSignal.generation_id.in_(gen_ids)).delete(
            synchronize_session=False
        )
        # Detach from CDD/Blueprint before those rows are removed.
        db.query(Generation).filter(Generation.id.in_(gen_ids)).update(
            {Generation.cdd_id: None, Generation.blueprint_id: None},
            synchronize_session=False,
        )
        db.query(Generation).filter(Generation.id.in_(gen_ids)).delete(
            synchronize_session=False
        )

    db.query(GenerationJob).filter(GenerationJob.course_id == course_id).delete(
        synchronize_session=False
    )

    bp_ids = [
        b.id
        for b in db.query(ModuleBlueprint.id).filter(ModuleBlueprint.course_id == course_id).all()
    ]
    if bp_ids:
        db.query(BlueprintVersion).filter(BlueprintVersion.blueprint_id.in_(bp_ids)).delete(
            synchronize_session=False
        )
        db.query(ModuleBlueprint).filter(ModuleBlueprint.id.in_(bp_ids)).delete(
            synchronize_session=False
        )

    cdd_ids = [
        d.id
        for d in db.query(CourseDesignDocument.id)
        .filter(CourseDesignDocument.course_id == course_id)
        .all()
    ]
    if cdd_ids:
        # Blueprints that only linked via cdd_id (no course_id) for this course's CDDs.
        orphan_bps = [
            b.id
            for b in db.query(ModuleBlueprint.id)
            .filter(ModuleBlueprint.cdd_id.in_(cdd_ids))
            .all()
        ]
        if orphan_bps:
            db.query(BlueprintVersion).filter(
                BlueprintVersion.blueprint_id.in_(orphan_bps)
            ).delete(synchronize_session=False)
            db.query(ModuleBlueprint).filter(ModuleBlueprint.id.in_(orphan_bps)).delete(
                synchronize_session=False
            )
        db.query(CDDVersion).filter(CDDVersion.cdd_id.in_(cdd_ids)).delete(
            synchronize_session=False
        )
        db.query(CourseDesignDocument).filter(CourseDesignDocument.id.in_(cdd_ids)).delete(
            synchronize_session=False
        )

    if module_ids:
        db.query(CourseModule).filter(CourseModule.id.in_(module_ids)).delete(
            synchronize_session=False
        )

    import_ids = [
        i.id
        for i in db.query(CourseImport.id).filter(CourseImport.course_id == course_id).all()
    ]
    if import_ids:
        db.query(ImportProvenance).filter(ImportProvenance.import_id.in_(import_ids)).delete(
            synchronize_session=False
        )
        db.query(CourseImport).filter(CourseImport.id.in_(import_ids)).delete(
            synchronize_session=False
        )

    db.query(PromptFixing).filter(PromptFixing.course_id == course_id).delete(
        synchronize_session=False
    )
    db.query(UserPromptPreference).filter(UserPromptPreference.course_id == course_id).delete(
        synchronize_session=False
    )
    db.query(UserPromptHistory).filter(UserPromptHistory.course_id == course_id).delete(
        synchronize_session=False
    )

    fb_doc_ids = [
        d.id
        for d in db.query(FeedbackDocument.id)
        .filter(FeedbackDocument.course_id == course_id)
        .all()
    ]
    if fb_doc_ids:
        db.query(FeedbackItem).filter(FeedbackItem.document_id.in_(fb_doc_ids)).delete(
            synchronize_session=False
        )
    db.query(FeedbackItem).filter(FeedbackItem.course_id == course_id).delete(
        synchronize_session=False
    )
    db.query(FeedbackDocument).filter(FeedbackDocument.course_id == course_id).delete(
        synchronize_session=False
    )

    db.query(CourseUserAssignment).filter(CourseUserAssignment.course_id == course_id).delete(
        synchronize_session=False
    )
    db.query(CentralRepository).filter(CentralRepository.course_id == course_id).update(
        {CentralRepository.course_id: None}, synchronize_session=False
    )
    db.query(PlagiarismReport).filter(PlagiarismReport.course_id == course_id).delete(
        synchronize_session=False
    )

    if course is not None:
        db.delete(course)
    db.commit()
    _log.info("course_purged  course_id=%d", course_id)


# ---------------------------------------------------------------------------
# Requirement 1 — Target & Model configuration (course-level)
# ---------------------------------------------------------------------------

def get_course_config(db, course_id: int) -> dict:
    """Return the persisted Target & Model config and pinned component IDs.

    Keys in the returned dict mirror session-state variable names so callers
    can apply the result directly.  None values mean the course has no saved
    config for that field yet.
    """
    course = get_course_by_id(db, course_id)
    if not course:
        return {}
    return {
        "model_choice":        course.config_model_choice,
        "expert_domain":       course.config_expert_domain,
        "target_audience":     course.config_target_audience,
        "sidebar_aud_cat":     course.config_audience_category,
        "active_cdd_id":       course.active_cdd_id,
        "active_blueprint_id": course.active_blueprint_id,
    }


def save_course_target_config(
    db,
    course_id: int,
    *,
    model_choice: "str | None" = None,
    expert_domain: "str | None" = None,
    target_audience: "str | None" = None,
    audience_category: "str | None" = None,
) -> None:
    """Persist Target & Model form values for *course_id*.

    Only updates fields that are explicitly provided (non-None).
    """
    course = get_course_by_id(db, course_id)
    if not course:
        return
    if model_choice      is not None: course.config_model_choice      = model_choice
    if expert_domain     is not None: course.config_expert_domain     = expert_domain
    if target_audience   is not None: course.config_target_audience   = target_audience
    if audience_category is not None: course.config_audience_category = audience_category
    db.commit()


# ---------------------------------------------------------------------------
# Requirement 3 — Pinned component persistence (course-level)
# ---------------------------------------------------------------------------

def set_active_cdd(db, course_id: int, cdd_id: "int | None") -> None:
    """Persist the pinned CDD for *course_id* to the database."""
    course = get_course_by_id(db, course_id)
    if course:
        course.active_cdd_id = cdd_id
        db.commit()


def set_active_blueprint(db, course_id: int, blueprint_id: "int | None") -> None:
    """Persist the pinned Blueprint for *course_id* to the database."""
    course = get_course_by_id(db, course_id)
    if course:
        course.active_blueprint_id = blueprint_id
        db.commit()
