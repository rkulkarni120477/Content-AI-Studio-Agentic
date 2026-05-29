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

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db, require_permission
from app.schemas.analytics import (
    AnalyticsSummaryResponse,
    AuditEventRead,
    GenerationHistoryRow,
    ProjectAnalyticsRow,
    PromptPerformanceItem,
    QualityTrendsResponse,
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

    scope = dict(
        user_name=current_user.username,
        project_id=project_id,
        is_admin=(current_user.role == "admin"),
    )

    return AnalyticsSummaryResponse(
        generations=analytics_repository.count_generations_scoped(db, **scope),
        blocks=generation_repository.count_blocks_scoped(db, **scope),
        prompt_assets=prompt_repository.count_prompts(db),
        documents=document_repository.count_documents(db),
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
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("analytics.view_all")),
) -> list[ProjectAnalyticsRow]:
    """Return project-level metrics for the admin comparison table."""
    from promptops_app.repositories import analytics_repository

    projects = analytics_repository.list_active_projects_for_analytics(db)
    rows = []
    for p in projects:
        gen_ids = analytics_repository.get_project_generation_ids(db, p.id)
        rows.append(ProjectAnalyticsRow(
            project_id=p.id,
            project_name=p.name,
            client=p.client_name or "—",
            generations=len(gen_ids),
            blocks=analytics_repository.count_project_blocks(db, gen_ids),
            cdds=analytics_repository.count_project_cdds(db, p.id),
            blueprints=analytics_repository.count_project_blueprints(db, p.id),
        ))
    return rows


@router.get(
    "/prompt-performance",
    response_model=list[PromptPerformanceItem],
    summary="Prompt performance leaderboard",
    description="Average quality rating per prompt template, computed from reviewer ratings.",
)
def get_prompt_performance(
    project_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("analytics.view_own")),
) -> list[PromptPerformanceItem]:
    """Compute the prompt leaderboard from block ratings."""
    from promptops_app.repositories import generation_repository

    scope = dict(
        user_name=current_user.username,
        project_id=project_id,
        is_admin=(current_user.role == "admin"),
    )

    all_gens = generation_repository.list_generations_all_scoped(db, **scope)
    if not all_gens:
        return []

    gen_ids = [g.id for g in all_gens]
    rated_blocks = generation_repository.list_rated_blocks_for_gen_ids(db, gen_ids)

    # Group blocks by generation to get prompt name.
    blocks_by_gen = {}
    for b in rated_blocks:
        blocks_by_gen.setdefault(b.generation_id, []).append(b)

    perf: dict[str, dict] = {}
    for gen in all_gens:
        gen_blocks = blocks_by_gen.get(gen.id, [])
        if gen_blocks:
            avg = sum(b.rating for b in gen_blocks) / len(gen_blocks)
            key = f"{gen.prompt_name} ({gen.prompt_version})"
            if key not in perf:
                perf[key] = {"total": 0.0, "count": 0}
            perf[key]["total"] += avg
            perf[key]["count"] += 1

    result = [
        PromptPerformanceItem(
            prompt=k,
            avg_rating=round(v["total"] / v["count"], 2),
            samples=v["count"],
        )
        for k, v in perf.items()
    ]
    result.sort(key=lambda x: x.avg_rating, reverse=True)
    return result


@router.get(
    "/quality-trends",
    response_model=QualityTrendsResponse,
    summary="Block quality rating trend",
    description="Returns raw rating values in chronological order for the area chart.",
)
def get_quality_trends(
    project_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("analytics.view_own")),
) -> QualityTrendsResponse:
    """Return the sequence of block quality ratings for trend visualisation."""
    from promptops_app.repositories import generation_repository

    ratings = generation_repository.get_block_ratings_scoped(
        db,
        user_name=current_user.username,
        project_id=project_id,
        is_admin=(current_user.role == "admin"),
    )
    return QualityTrendsResponse(
        ratings=[r[0] for r in ratings if r[0] is not None and r[0] > 0]
    )


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


@router.get(
    "/audit-trail",
    response_model=PaginatedResponse[AuditEventRead],
    summary="Paginated audit trail",
    description="Admin and Lead only. Searchable log of all user actions.",
)
def get_audit_trail(
    entity_type: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("system.view_logs")),
) -> PaginatedResponse[AuditEventRead]:
    """Return paginated audit log events with optional filters."""
    from promptops_app.services.audit_service import get_audit_trail

    events, total = get_audit_trail(
        db,
        entity_type=entity_type,
        actor=actor,
        date_from=date_from,
        page=page,
        page_size=page_size,
    )

    return PaginatedResponse.create(
        items=[AuditEventRead.model_validate(e) for e in events],
        total=total, page=page, page_size=page_size,
    )


@router.get(
    "/audit-trail/export",
    summary="Export audit trail as CSV",
    description="Downloads the full audit trail as a CSV file.",
)
def export_audit_trail(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("export.audit_log")),
) -> Response:
    """
    Export all audit events as a CSV download.

    Replicates the "Export Audit Log" button in the Streamlit Analytics tab.
    """
    from promptops_app.services.audit_service import get_audit_trail

    events, _ = get_audit_trail(db, page=1, page_size=100_000)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "actor", "action", "entity_type", "entity_id", "project_id", "course_id", "created_at"])

    for e in events:
        writer.writerow([e.id, e.actor, e.action, e.entity_type, e.entity_id,
                         e.project_id, e.course_id,
                         e.created_at.isoformat() if e.created_at else ""])

    _log.info("audit_trail_exported  user=%s  rows=%d", current_user.username, len(events))
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
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("analytics.view_own")),
) -> list[GenerationHistoryRow]:
    """Return recent generations with CDD and Blueprint labels for the history tab."""
    from promptops_app.repositories import (
        blueprint_repository, cdd_repository, generation_repository,
    )

    scope = dict(user_name=current_user.username, project_id=project_id, is_admin=(current_user.role == "admin"))
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
