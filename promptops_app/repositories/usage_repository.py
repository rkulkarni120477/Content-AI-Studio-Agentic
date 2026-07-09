"""LLM Usage analytics queries.

All aggregations use GROUP BY + SQL aggregate functions — no Python-side loops.
Every public function accepts optional scope filters (project, user, date range)
so the same query serves Admin (all data) and Lead (project-scoped) views.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import func, text

from promptops_app.database import LLMUsageLog, Project, Course


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _apply_scope(q, user_name=None, project_id=None, model_name=None,
                 date_from=None, date_to=None, tenant_id=None, is_platform_admin=False):
    if user_name:    q = q.filter(LLMUsageLog.user_id == user_name)
    if project_id:   q = q.filter(LLMUsageLog.project_id == project_id)
    if model_name:   q = q.filter(LLMUsageLog.model_name == model_name)
    if date_from:    q = q.filter(LLMUsageLog.created_at >= date_from)
    if date_to:      q = q.filter(LLMUsageLog.created_at <= date_to)
    # Tenant isolation: platform admin with no tenant_id sees all rows.
    if tenant_id:
        q = q.filter(LLMUsageLog.tenant_id == tenant_id)
    return q


# ---------------------------------------------------------------------------
# Summary KPIs — single-pass aggregate (no sub-queries)
# ---------------------------------------------------------------------------

def get_summary(
    db,
    user_name: Optional[str] = None,
    project_id: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    tenant_id=None,
    is_platform_admin=False,
) -> dict:
    """Return dashboard KPI values in one query pass."""
    q = db.query(
        func.count(LLMUsageLog.id).label("total_calls"),
        func.coalesce(func.sum(LLMUsageLog.total_tokens),  0).label("total_tokens"),
        func.coalesce(func.sum(LLMUsageLog.input_tokens),  0).label("total_input_tokens"),
        func.coalesce(func.sum(LLMUsageLog.output_tokens), 0).label("total_output_tokens"),
        func.coalesce(func.sum(LLMUsageLog.estimated_cost), 0.0).label("total_cost"),
        func.coalesce(func.avg(LLMUsageLog.duration_ms), 0.0).label("avg_duration_ms"),
    )
    q = _apply_scope(q, user_name=user_name, project_id=project_id,
                     date_from=date_from, date_to=date_to,
                     tenant_id=tenant_id, is_platform_admin=is_platform_admin)
    row = q.one()

    # Failed count in a separate small query (avoids CASE complexity across ORM versions)
    fail_q = db.query(func.count(LLMUsageLog.id)).filter(LLMUsageLog.status == "error")
    fail_q = _apply_scope(fail_q, user_name=user_name, project_id=project_id,
                          date_from=date_from, date_to=date_to,
                          tenant_id=tenant_id, is_platform_admin=is_platform_admin)
    failed = fail_q.scalar() or 0

    return {
        "total_calls":         row.total_calls or 0,
        "total_tokens":        int(row.total_tokens or 0),
        "total_input_tokens":  int(row.total_input_tokens or 0),
        "total_output_tokens": int(row.total_output_tokens or 0),
        "total_cost":          round(float(row.total_cost or 0), 4),
        "avg_duration_ms":     round(float(row.avg_duration_ms or 0), 1),
        "failed_calls":        failed,
    }


# ---------------------------------------------------------------------------
# Cost by dimension (GROUP BY queries)
# ---------------------------------------------------------------------------

def cost_by_model(
    db,
    user_name: Optional[str] = None,
    project_id: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    tenant_id=None,
    is_platform_admin=False,
) -> list[dict]:
    """Aggregate cost and token totals grouped by model_name."""
    q = db.query(
        LLMUsageLog.model_name,
        func.count(LLMUsageLog.id).label("calls"),
        func.coalesce(func.sum(LLMUsageLog.total_tokens), 0).label("tokens"),
        func.coalesce(func.sum(LLMUsageLog.estimated_cost), 0.0).label("cost"),
        func.coalesce(func.avg(LLMUsageLog.duration_ms), 0.0).label("avg_ms"),
    ).group_by(LLMUsageLog.model_name)
    q = _apply_scope(q, user_name=user_name, project_id=project_id,
                     date_from=date_from, date_to=date_to,
                     tenant_id=tenant_id, is_platform_admin=is_platform_admin)
    return [
        {
            "Model":       r.model_name or "unknown",
            "Calls":       r.calls,
            "Tokens":      int(r.tokens or 0),
            "Cost ($)":    round(float(r.cost or 0), 4),
            "Avg ms":      round(float(r.avg_ms or 0), 0),
        }
        for r in q.order_by(func.sum(LLMUsageLog.estimated_cost).desc()).all()
    ]


def cost_by_user(
    db,
    project_id: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 50,
    tenant_id=None,
    is_platform_admin=False,
) -> list[dict]:
    """Aggregate cost and token totals grouped by user_id."""
    q = db.query(
        LLMUsageLog.user_id,
        func.count(LLMUsageLog.id).label("calls"),
        func.coalesce(func.sum(LLMUsageLog.total_tokens), 0).label("tokens"),
        func.coalesce(func.sum(LLMUsageLog.estimated_cost), 0.0).label("cost"),
    ).group_by(LLMUsageLog.user_id)
    q = _apply_scope(q, project_id=project_id, date_from=date_from, date_to=date_to,
                     tenant_id=tenant_id, is_platform_admin=is_platform_admin)
    return [
        {
            "User":     r.user_id or "(anonymous)",
            "Calls":    r.calls,
            "Tokens":   int(r.tokens or 0),
            "Cost ($)": round(float(r.cost or 0), 4),
        }
        for r in q.order_by(func.sum(LLMUsageLog.estimated_cost).desc()).limit(limit).all()
    ]


def cost_by_project(
    db,
    user_name: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    tenant_id=None,
    is_platform_admin=False,
) -> list[dict]:
    """Aggregate cost grouped by project_id, enriched with project name via join."""
    q = (
        db.query(
            LLMUsageLog.project_id,
            Project.name.label("project_name"),
            func.count(LLMUsageLog.id).label("calls"),
            func.coalesce(func.sum(LLMUsageLog.total_tokens), 0).label("tokens"),
            func.coalesce(func.sum(LLMUsageLog.estimated_cost), 0.0).label("cost"),
        )
        .outerjoin(Project, Project.id == LLMUsageLog.project_id)
        .filter(LLMUsageLog.project_id.isnot(None))
        .group_by(LLMUsageLog.project_id, Project.name)
    )
    if user_name:  q = q.filter(LLMUsageLog.user_id == user_name)
    if date_from:  q = q.filter(LLMUsageLog.created_at >= date_from)
    if date_to:    q = q.filter(LLMUsageLog.created_at <= date_to)
    if tenant_id:  q = q.filter(LLMUsageLog.tenant_id == tenant_id)
    return [
        {
            "Project":  r.project_name or f"Project #{r.project_id}",
            "Calls":    r.calls,
            "Tokens":   int(r.tokens or 0),
            "Cost ($)": round(float(r.cost or 0), 4),
        }
        for r in q.order_by(func.sum(LLMUsageLog.estimated_cost).desc()).all()
    ]


def cost_by_course(
    db,
    project_id: Optional[int] = None,
    user_name: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    tenant_id=None,
    is_platform_admin=False,
) -> list[dict]:
    """Aggregate cost grouped by course_id, enriched with course name via join."""
    q = (
        db.query(
            LLMUsageLog.course_id,
            Course.name.label("course_name"),
            func.count(LLMUsageLog.id).label("calls"),
            func.coalesce(func.sum(LLMUsageLog.total_tokens), 0).label("tokens"),
            func.coalesce(func.sum(LLMUsageLog.estimated_cost), 0.0).label("cost"),
        )
        .outerjoin(Course, Course.id == LLMUsageLog.course_id)
        .filter(LLMUsageLog.course_id.isnot(None))
        .group_by(LLMUsageLog.course_id, Course.name)
    )
    if project_id: q = q.filter(LLMUsageLog.project_id == project_id)
    if user_name:  q = q.filter(LLMUsageLog.user_id == user_name)
    if date_from:  q = q.filter(LLMUsageLog.created_at >= date_from)
    if date_to:    q = q.filter(LLMUsageLog.created_at <= date_to)
    if tenant_id:  q = q.filter(LLMUsageLog.tenant_id == tenant_id)
    return [
        {
            "Course":   r.course_name or f"Course #{r.course_id}",
            "Calls":    r.calls,
            "Tokens":   int(r.tokens or 0),
            "Cost ($)": round(float(r.cost or 0), 4),
        }
        for r in q.order_by(func.sum(LLMUsageLog.estimated_cost).desc()).all()
    ]


def monthly_usage(
    db,
    user_name: Optional[str] = None,
    project_id: Optional[int] = None,
    months: int = 6,
    tenant_id=None,
    is_platform_admin=False,
) -> list[dict]:
    """Monthly token and cost totals — PostgreSQL date_trunc GROUP BY."""
    month_col = func.date_trunc("month", LLMUsageLog.created_at)
    # Filters MUST be applied before GROUP BY / ORDER BY / LIMIT.
    q = db.query(
        month_col.label("month"),
        func.count(LLMUsageLog.id).label("calls"),
        func.coalesce(func.sum(LLMUsageLog.total_tokens), 0).label("tokens"),
        func.coalesce(func.sum(LLMUsageLog.estimated_cost), 0.0).label("cost"),
    )
    if user_name:  q = q.filter(LLMUsageLog.user_id == user_name)
    if project_id: q = q.filter(LLMUsageLog.project_id == project_id)
    if tenant_id:  q = q.filter(LLMUsageLog.tenant_id == tenant_id)
    q    = q.group_by(month_col).order_by(month_col.desc()).limit(months)
    rows = q.all()
    return [
        {
            "Month":    r.month.strftime("%Y-%m") if r.month else "—",
            "Calls":    r.calls,
            "Tokens":   int(r.tokens or 0),
            "Cost ($)": round(float(r.cost or 0), 4),
        }
        for r in reversed(rows)   # chronological order for charts
    ]


# ---------------------------------------------------------------------------
# Detailed log (paginated)
# ---------------------------------------------------------------------------

def count_usage_logs(
    db,
    user_name: Optional[str] = None,
    project_id: Optional[int] = None,
    model_name: Optional[str] = None,
    status: Optional[str] = None,
    entity_type: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    tenant_id=None,
    is_platform_admin=False,
) -> int:
    q = db.query(func.count(LLMUsageLog.id))
    q = _apply_scope(q, user_name=user_name, project_id=project_id,
                     model_name=model_name, date_from=date_from, date_to=date_to,
                     tenant_id=tenant_id, is_platform_admin=is_platform_admin)
    if status:      q = q.filter(LLMUsageLog.status == status)
    if entity_type: q = q.filter(LLMUsageLog.entity_type == entity_type)
    return q.scalar() or 0


def list_usage_logs(
    db,
    user_name: Optional[str] = None,
    project_id: Optional[int] = None,
    model_name: Optional[str] = None,
    status: Optional[str] = None,
    entity_type: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 50,
    offset: int = 0,
    tenant_id=None,
    is_platform_admin=False,
) -> list:
    q = db.query(LLMUsageLog)
    q = _apply_scope(q, user_name=user_name, project_id=project_id,
                     model_name=model_name, date_from=date_from, date_to=date_to,
                     tenant_id=tenant_id, is_platform_admin=is_platform_admin)
    if status:      q = q.filter(LLMUsageLog.status == status)
    if entity_type: q = q.filter(LLMUsageLog.entity_type == entity_type)
    return (
        q.order_by(LLMUsageLog.created_at.desc())
        .offset(offset).limit(limit)
        .all()
    )


def list_distinct_models(db, tenant_id=None) -> list[str]:
    q = db.query(LLMUsageLog.model_name).distinct()
    if tenant_id:
        q = q.filter(LLMUsageLog.tenant_id == tenant_id)
    return sorted({r[0] for r in q.all() if r[0]})


def list_distinct_entity_types(db, tenant_id=None) -> list[str]:
    q = db.query(LLMUsageLog.entity_type).distinct()
    if tenant_id:
        q = q.filter(LLMUsageLog.tenant_id == tenant_id)
    return sorted({r[0] for r in q.all() if r[0]})


def list_distinct_users(db, project_id: Optional[int] = None, tenant_id=None) -> list[str]:
    q = db.query(LLMUsageLog.user_id).distinct()
    if project_id:
        q = q.filter(LLMUsageLog.project_id == project_id)
    if tenant_id:
        q = q.filter(LLMUsageLog.tenant_id == tenant_id)
    rows = q.all()
    return sorted({r[0] for r in rows if r[0]})
