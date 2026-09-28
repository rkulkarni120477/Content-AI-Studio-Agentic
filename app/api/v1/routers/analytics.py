"""
Analytics router — metrics, observability, and audit trail.

Streamlit equivalent: ``pages/analytics.py`` render_page()

Provides read-only aggregated data for dashboards, leaderboards, and
audit trails. All endpoints are RBAC-scoped:
  - Non-admin users see their own project data only.
  - Admin users see cross-project data.

Write operations (user management, db clear) live in admin.py.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import date as _date, datetime as _dt, timedelta as _timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db, require_permission
from app.schemas.analytics import (
    AnalyticsSummaryResponse,
    AuditEventRead,
    AuditTrailFilters,
    AuditTrailFiltersResponse,
    AuditTrailQuery,
    CddBlueprintEventRow,
    DocumentUploadHistoryRow,
    FeedbackItemRead,
    FeedbackSummaryResponse,
    GenerationHistoryRow,
    LlmCostDashboardResponse,
    LlmCostSummary,
    ProjectAnalyticsRow,
    PromptVersionHistoryRow,
    ReviewAnalyticsResponse,
    ReviewItemRead,
    UsageByModelItem,
    UsageSummaryResponse,
)
from app.schemas.common import PaginatedResponse

_log = logging.getLogger(__name__)
router = APIRouter()


@router.get(
    "/summary",
    response_model=AnalyticsSummaryResponse,
    summary="Get dashboard metric counters",
    description="Returns generation, block, prompt, document, CDD, and blueprint counts scoped to the user's role.",
)
def get_summary(
    project_id: int | None = Query(default=None),
    date_from: _date | None = Query(default=None, description="Inclusive start date."),
    date_to: _date | None = Query(default=None, description="Inclusive end date."),
    tz_offset_minutes: int = Query(default=0, description="Browser's Date.getTimezoneOffset() value, so date ranges line up with the user's local calendar day."),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("system.analytics")),
) -> AnalyticsSummaryResponse:
    """
    Return the six metric counters shown at the top of the Analytics page.

    Replicates the analytics_repository.count_* calls in pages/analytics.py.
    """
    from promptops_app.repositories import (
        analytics_repository, blueprint_repository, cdd_repository,
        document_repository, generation_repository, prompt_repository,
    )

    parsed_from, parsed_to = _resolve_date_range(date_from, date_to, tz_offset_minutes)

    scope = dict(
        user_name=current_user.username,
        project_id=project_id,
        is_admin=(current_user.role == "admin"),
        date_from=parsed_from,
        date_to=parsed_to,
    )

    return AnalyticsSummaryResponse(
        generations=analytics_repository.count_generations_scoped(db, **scope),
        blocks=generation_repository.count_blocks_scoped(db, **scope),
        prompt_assets=prompt_repository.count_prompts(db, date_from=parsed_from, date_to=parsed_to),
        documents=document_repository.count_documents(db, date_from=parsed_from, date_to=parsed_to),
        cdds=cdd_repository.count_cdds_scoped(db, **scope),
        blueprints=blueprint_repository.count_blueprints_scoped(db, **scope),
    )


@router.get(
    "/projects",
    response_model=list[ProjectAnalyticsRow],
    summary="Cross-project comparison table",
    description="Admin only. Returns generation and block counts per project.",
)
def get_project_analytics(
    date_from: _date | None = Query(default=None, description="Inclusive start date."),
    date_to: _date | None = Query(default=None, description="Inclusive end date."),
    tz_offset_minutes: int = Query(default=0, description="Browser's Date.getTimezoneOffset() value, so date ranges line up with the user's local calendar day."),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("analytics.view_all")),
) -> list[ProjectAnalyticsRow]:
    """Return project-level metrics for the admin comparison table."""
    from promptops_app.repositories import analytics_repository

    parsed_from, parsed_to = _resolve_date_range(date_from, date_to, tz_offset_minutes)

    projects = analytics_repository.list_active_projects_for_analytics(db)
    metrics = analytics_repository.get_project_metrics_batch(
        db, [p.id for p in projects], date_from=parsed_from, date_to=parsed_to,
    )
    return [
        ProjectAnalyticsRow(
            project_id=p.id,
            project_name=p.name,
            client=p.client_name or "—",
            **metrics[p.id],
        )
        for p in projects
    ]


@router.get(
    "/usage",
    response_model=UsageSummaryResponse,
    summary="LLM usage and cost summary",
)
def get_usage_summary(
    project_id: int | None = Query(default=None),
    date_from: str | None = Query(default=None, description="ISO date, e.g. 2026-05-01"),
    date_to: str | None = Query(default=None, description="ISO date, e.g. 2026-05-18"),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("llm_usage.view_own")),
) -> UsageSummaryResponse:
    """Return token counts and cost estimates from the LLMUsageLog table."""
    from promptops_app.repositories import usage_repository

    scoped_user = current_user.username if current_user.role != "admin" else None

    summary = usage_repository.get_summary(
        db,
        user_name=scoped_user,
        project_id=project_id,
        date_from=date_from,
        date_to=date_to,
    )

    by_model_rows = usage_repository.cost_by_model(
        db,
        user_name=scoped_user,
        project_id=project_id,
        date_from=date_from,
        date_to=date_to,
    )

    by_model = [
        UsageByModelItem(
            model=row["Model"],
            prompt_tokens=row.get("Tokens", 0),
            completion_tokens=0,
            cost_usd=row.get("Cost ($)", 0.0),
        )
        for row in by_model_rows
    ]

    return UsageSummaryResponse(
        total_prompt_tokens=summary.get("total_input_tokens", 0),
        total_completion_tokens=summary.get("total_output_tokens", 0),
        estimated_cost_usd=summary.get("total_cost", 0.0),
        by_model=by_model,
    )


def _resolve_date_range(
    date_from: _date | None, date_to: _date | None, tz_offset_minutes: int = 0,
) -> tuple[_dt | None, _dt | None]:
    """Turn calendar-date query params into a UTC timestamp range.

    `tz_offset_minutes` is the browser's own Date.getTimezoneOffset() value
    (minutes to ADD to local time to reach UTC), so a range like "This Month"
    lines up with the user's own local midnight-to-midnight, not UTC's --
    without it, a user ahead of UTC (e.g. IST) can see "This Month" as empty
    for the first few hours of the month.

    The upper bound is exclusive (start of the day after date_to), so it
    never misses rows saved in the last second of date_to the way a literal
    "23:59:59" cutoff would.
    """
    offset = _timedelta(minutes=tz_offset_minutes)
    start = _dt.combine(date_from, _dt.min.time()) + offset if date_from else None
    end = _dt.combine(date_to, _dt.min.time()) + _timedelta(days=1) + offset if date_to else None
    return start, end


def _parse_audit_date(value: str | None, *, end_of_day: bool = False):
    from datetime import datetime as dt

    if not value:
        return None
    try:
        parsed = dt.strptime(value[:10], "%Y-%m-%d")
        if end_of_day:
            return parsed.replace(hour=23, minute=59, second=59)
        return parsed
    except ValueError:
        return None


def _audit_event_row(record) -> AuditEventRead:
    from app.schemas.analytics import _parse_audit_metadata
    from promptops_app.services.audit_service import get_event_meta

    meta = get_event_meta(record.action)
    return AuditEventRead(
        id=record.id,
        actor=record.user_id,
        action=record.action,
        label=meta.get("label", record.action),
        icon=meta.get("icon", "📌"),
        entity_type=record.entity_type,
        entity_id=record.entity_id,
        project_id=record.project_id,
        course_id=record.course_id,
        ip_address=record.ip_address,
        metadata=_parse_audit_metadata(record.metadata_json),
        created_at=record.created_at,
    )


@router.get(
    "/audit-trail/filters",
    response_model=AuditTrailFiltersResponse,
    summary="Audit trail filter options",
)
def get_audit_trail_filters(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("system.analytics")),
) -> AuditTrailFiltersResponse:
    """Dropdown values for the Audit Trail tab (Streamlit parity)."""
    from app.core.permissions import effective_rbac_check
    from promptops_app.repositories import analytics_repository
    from promptops_app.services.audit_service import (
        get_actors, get_all_actions, get_entity_types,
    )

    actors = get_actors(db)
    if not effective_rbac_check(current_user, "analytics.view_all"):
        actors = [current_user.username]

    projects = []
    if effective_rbac_check(current_user, "analytics.view_all"):
        projects = [
            {"id": p.id, "name": p.name}
            for p in analytics_repository.list_active_projects_for_analytics(db)
        ]

    return AuditTrailFiltersResponse(
        actors=actors,
        actions=get_all_actions(db),
        entity_types=get_entity_types(db),
        projects=projects,
    )


def _resolve_audit_trail_query(query: AuditTrailFilters, current_user) -> tuple[dict, AuditTrailFilters]:
    """Apply role scoping and parse dates for audit trail queries.

    Takes the filters base, not the paginated AuditTrailQuery: only touches
    actor/date/etc, and export calls this with an AuditTrailFilters that has
    no page/page_size at all.
    """
    from app.core.permissions import effective_rbac_check

    actor = query.actor
    if not effective_rbac_check(current_user, "analytics.view_all"):
        actor = current_user.username

    parsed_from = _parse_audit_date(query.date_from)
    parsed_to = _parse_audit_date(query.date_to, end_of_day=True)

    trail_kw = dict(
        user_id=actor,
        action=query.action,
        entity_type=query.entity_type,
        project_id=query.project_id,
        date_from=parsed_from,
        date_to=parsed_to,
    )
    return trail_kw, query.model_copy(update={"actor": actor})


@router.get(
    "/audit-trail",
    response_model=PaginatedResponse[AuditEventRead],
    summary="Paginated audit trail",
    description=(
        "Admin and Lead only. Searchable log of all user actions. "
        "Pass query parameters: entity_type, actor, action, project_id, "
        "date_from, date_to, page, page_size."
    ),
)
def list_audit_trail(
    query: Annotated[AuditTrailQuery, Query()],
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("system.analytics")),
) -> PaginatedResponse[AuditEventRead]:
    """Return paginated audit log events with optional filters."""
    from promptops_app.services.audit_service import count_trail, get_audit_trail as fetch_audit_trail

    trail_kw, q = _resolve_audit_trail_query(query, current_user)
    offset = (q.page - 1) * q.page_size
    total = count_trail(db, **trail_kw)
    events = fetch_audit_trail(
        db,
        actor=q.actor,
        event_type=q.action,
        entity=q.entity_type,
        project_id=q.project_id,
        date_from=trail_kw["date_from"],
        date_to=trail_kw["date_to"],
        limit=q.page_size,
        offset=offset,
    )

    return PaginatedResponse.create(
        items=[_audit_event_row(e) for e in events],
        total=total, page=q.page, page_size=q.page_size,
    )


@router.get(
    "/audit-trail/export",
    summary="Export audit trail as CSV",
    description="Downloads audit events as CSV using the same filters as GET /audit-trail.",
)
def export_audit_trail(
    query: Annotated[AuditTrailFilters, Query()],
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("export.audit_log")),
) -> Response:
    """
    Export audit events as a CSV download (respects current filters).

    Takes AuditTrailFilters, not AuditTrailQuery: page/page_size don't apply
    here, so this endpoint doesn't declare them at all — every matching row
    is exported (capped at 100k below), and an extra page_size on the
    request is simply ignored rather than validated.

    Replicates the "Export Audit Log" button in the Streamlit Analytics tab.
    """
    from promptops_app.repositories.audit_repository import export_to_csv_rows

    trail_kw, q = _resolve_audit_trail_query(query, current_user)
    rows = export_to_csv_rows(
        db,
        user_id=trail_kw["user_id"],
        action=trail_kw["action"],
        entity_type=trail_kw["entity_type"],
        project_id=trail_kw["project_id"],
        date_from=trail_kw["date_from"],
        date_to=trail_kw["date_to"],
        limit=100_000,
    )

    output = io.StringIO()
    writer = csv.writer(output)
    if rows:
        writer.writerow(list(rows[0].keys()))
        for row in rows:
            writer.writerow(list(row.values()))
    else:
        writer.writerow(["id", "user_id", "action", "entity_type", "entity_id", "project_id", "course_id", "created_at"])

    _log.info("audit_trail_exported  user=%s  rows=%d", current_user.username, len(rows))
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_trail.csv"},
    )


@router.get(
    "/generations",
    response_model=list[GenerationHistoryRow],
    summary="Recent generation history",
)
def get_generation_history(
    limit: int = Query(default=20, ge=1, le=100),
    project_id: int | None = Query(default=None),
    date_from: _date | None = Query(default=None, description="Inclusive start date."),
    date_to: _date | None = Query(default=None, description="Inclusive end date."),
    tz_offset_minutes: int = Query(default=0, description="Browser's Date.getTimezoneOffset() value, so date ranges line up with the user's local calendar day."),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("system.analytics")),
) -> list[GenerationHistoryRow]:
    """Return recent generations with CDD and Blueprint labels for the history tab."""
    from promptops_app.repositories import (
        blueprint_repository, cdd_repository, generation_repository,
    )

    parsed_from, parsed_to = _resolve_date_range(date_from, date_to, tz_offset_minutes)
    scope = dict(
        user_name=current_user.username, project_id=project_id, is_admin=(current_user.role == "admin"),
        date_from=parsed_from, date_to=parsed_to,
    )
    gens = generation_repository.list_recent_generations(db, limit=limit, **scope)

    rows = []
    for g in gens:
        cdd_lbl, bp_lbl = "—", "—"
        if g.cdd_id:
            cdd = cdd_repository.get_cdd_by_id(db, g.cdd_id)
            cdd_lbl = f"{cdd.title[:20]}… ({g.cdd_version})" if cdd else f"CDD#{g.cdd_id}"
        if g.blueprint_id:
            bp = blueprint_repository.get_blueprint_by_id(db, g.blueprint_id)
            bp_lbl = f"{bp.title[:20]}… ({g.blueprint_version})" if bp else f"BP#{g.blueprint_id}"
        rows.append(GenerationHistoryRow(
            id=g.id, topic=g.topic,
            prompt_name=g.prompt_name, prompt_version=g.prompt_version,
            cdd_label=cdd_lbl, blueprint_label=bp_lbl, created_at=g.created_at,
        ))
    return rows


@router.get(
    "/history/prompt-versions",
    response_model=list[PromptVersionHistoryRow],
    summary="Recent prompt registry commits",
)
def get_prompt_version_history(
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("system.analytics")),
) -> list[PromptVersionHistoryRow]:
    from promptops_app.repositories import analytics_repository

    versions = analytics_repository.list_recent_prompt_versions(db, limit=limit)
    return [
        PromptVersionHistoryRow(
            prompt_id=v.prompt_id,
            version=v.version or "",
            notes=v.change_reason or "",
            created_at=v.created_at,
        )
        for v in versions
    ]


@router.get(
    "/history/document-uploads",
    response_model=list[DocumentUploadHistoryRow],
    summary="Recent document uploads",
)
def get_document_upload_history(
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("system.analytics")),
) -> list[DocumentUploadHistoryRow]:
    from promptops_app.repositories import analytics_repository

    docs = analytics_repository.list_recent_doc_uploads(db, limit=limit)
    return [
        DocumentUploadHistoryRow(
            filename=d.filename,
            tag=d.doc_tag or "general",
            file_type=d.file_type,
            user=d.uploaded_by,
            created_at=d.uploaded_at,
        )
        for d in docs
    ]


@router.get(
    "/history/cdd-blueprint-events",
    response_model=list[CddBlueprintEventRow],
    summary="CDD and Blueprint audit events",
)
def get_cdd_blueprint_history(
    limit: int = Query(default=40, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("system.analytics")),
) -> list[CddBlueprintEventRow]:
    from promptops_app.repositories import analytics_repository

    logs = analytics_repository.list_cdd_blueprint_events(db, limit=limit)
    return [
        CddBlueprintEventRow(
            event=log.event_type.replace("_", " ").title(),
            actor=log.actor,
            details=log.details,
            created_at=log.created_at,
        )
        for log in logs
    ]


@router.get(
    "/feedback/summary",
    response_model=FeedbackSummaryResponse,
    summary="Feedback signal counts",
)
def get_feedback_summary(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("analytics.view_own")),
) -> FeedbackSummaryResponse:
    from promptops_app.repositories import generation_repository

    return FeedbackSummaryResponse(
        total=generation_repository.count_feedback_signals(db),
        learning=generation_repository.count_feedback_signals_by_scope(db, "learning"),
        one_time=generation_repository.count_feedback_signals_by_scope(db, "one_time"),
    )


@router.get(
    "/feedback",
    response_model=PaginatedResponse[FeedbackItemRead],
    summary="Paginated feedback signals",
)
def list_feedback_signals(
    scope: str | None = Query(default=None, description="learning | one_time"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("analytics.view_own")),
) -> PaginatedResponse[FeedbackItemRead]:
    from promptops_app.repositories import generation_repository

    if scope == "learning":
        total = generation_repository.count_feedback_signals_by_scope(db, "learning")
    elif scope == "one_time":
        total = generation_repository.count_feedback_signals_by_scope(db, "one_time")
    else:
        total = generation_repository.count_feedback_signals(db)

    offset = (page - 1) * page_size
    rows = generation_repository.list_feedback_signals_filtered(
        db, scope=scope, limit=page_size, offset=offset,
    )
    items = [
        FeedbackItemRead(
            id=f.id,
            source=(f.signal_source or "unknown").title(),
            scope="Learning" if f.feedback_scope == "learning" else "One-time",
            block_type=f.block_type,
            instruction=(f.user_instruction or f.edit_reason or "—")[:500],
            author=f.author,
            created_at=f.created_at,
        )
        for f in rows
    ]
    return PaginatedResponse.create(items=items, total=total, page=page, page_size=page_size)


@router.get(
    "/reviews",
    response_model=ReviewAnalyticsResponse,
    summary="Review analytics and recent reviews",
)
def get_review_analytics(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("analytics.view_own")),
) -> ReviewAnalyticsResponse:
    from promptops_app.repositories import analytics_repository, generation_repository

    total = generation_repository.count_reviews(db)
    if total == 0:
        return ReviewAnalyticsResponse()

    offset = (page - 1) * page_size
    reviews = analytics_repository.list_recent_reviews(db, limit=page_size, offset=offset)
    return ReviewAnalyticsResponse(
        total=total,
        approved=generation_repository.count_approved_reviews(db),
        avg_score=round(generation_repository.avg_review_score(db), 1),
        items=[
            ReviewItemRead(
                block_id=r.block_id,
                reviewer=r.reviewer,
                reviewer_role=r.reviewer_role,
                score=r.score,
                approved=bool(r.approved),
                comments=(r.comments or "")[:500],
                created_at=r.created_at,
            )
            for r in reviews
        ],
    )


@router.get(
    "/llm-cost",
    response_model=LlmCostDashboardResponse,
    summary="LLM cost dashboard (Streamlit parity)",
)
def get_llm_cost_dashboard(
    project_id: int | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("llm_usage.view_own")),
) -> LlmCostDashboardResponse:
    from datetime import datetime as dt

    from promptops_app.repositories import usage_repository

    is_admin = current_user.role == "admin"
    is_lead = current_user.role == "reviewer"
    is_platform_admin_user = bool(getattr(current_user, "_is_platform_admin", False))

    # Tenant isolation: only a true platform admin may see cross-tenant data
    # or choose an arbitrary project via the query param. Every tenant-scoped
    # caller — including a tenant-role "admin" — is force-scoped to their own
    # tenant, mirroring prompts.py's prompts_by_course. Without this, any
    # "admin" role (tenant-scoped, not just platform) saw every tenant's
    # by_project/by_course/by_user rows below, and any non-admin could read
    # another tenant's per-course spend just by passing its project_id.
    scoped_project = project_id if is_platform_admin_user else getattr(current_user, "_project_id", None)
    scoped_user = current_user.username if not is_admin else None

    parsed_from = dt.fromisoformat(date_from) if date_from else None
    parsed_to = dt.fromisoformat(date_to) if date_to else None

    kpi = usage_repository.get_summary(
        db,
        user_name=scoped_user if is_admin else (current_user.username if not is_lead else None),
        project_id=scoped_project,
        date_from=parsed_from,
        date_to=parsed_to,
    )

    by_model = usage_repository.cost_by_model(
        db,
        user_name=scoped_user,
        project_id=scoped_project,
        date_from=parsed_from,
        date_to=parsed_to,
    )
    monthly = usage_repository.monthly_usage(
        db,
        user_name=scoped_user,
        project_id=scoped_project,
    )

    # cost_by_project is inherently cross-tenant (groups BY project, no filter
    # param at all) — platform-admin only, never a tenant-scoped admin.
    by_project = usage_repository.cost_by_project(db, date_from=parsed_from, date_to=parsed_to) if is_platform_admin_user else []
    by_course = usage_repository.cost_by_course(
        db, project_id=scoped_project, date_from=parsed_from, date_to=parsed_to,
    )
    by_user = usage_repository.cost_by_user(
        db, project_id=scoped_project, date_from=parsed_from, date_to=parsed_to,
    ) if is_admin else []

    # Platform-wide breakdowns (project_id=None, every tenant) — restricted to
    # true platform admins, not merely tenant-role "admin", so one tenant's
    # admin can't see another tenant's users'/courses' spend. cost_by_project
    # is inherently unscoped already (it groups BY project); cost_by_course
    # and cost_by_user just need project_id=None instead of scoped_project.
    if is_platform_admin_user:
        platform_user_usage = usage_repository.cost_by_user(db, project_id=None, date_from=parsed_from, date_to=parsed_to)
        platform_tenant_usage = usage_repository.cost_by_project(db, date_from=parsed_from, date_to=parsed_to)
        platform_course_usage = usage_repository.cost_by_course(db, project_id=None, date_from=parsed_from, date_to=parsed_to)
    else:
        platform_user_usage = platform_tenant_usage = platform_course_usage = []

    return LlmCostDashboardResponse(
        summary=LlmCostSummary(**kpi),
        by_model=by_model,
        monthly=monthly,
        by_project=by_project,
        by_course=by_course,
        by_user=by_user,
        platform_user_usage=platform_user_usage,
        platform_tenant_usage=platform_tenant_usage,
        platform_course_usage=platform_course_usage,
    )
