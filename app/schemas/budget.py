"""Budget policy schemas for the platform-admin API — P3.1 of
claude_plan_platform_hardening.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

BudgetScope = Literal["project", "course", "user"]
BudgetPeriod = Literal["monthly", "rolling"]


class BudgetPolicyRead(BaseModel):
    id: int
    scope: BudgetScope
    scope_id: str
    period: BudgetPeriod
    limit_usd: float
    warn_threshold_pct: float
    # Current spend/tokens this period — filled in by the router from
    # budget_service.current_period_usage(), not DB columns on this row.
    current_spend_usd: float = 0.0
    current_tokens: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class BudgetPolicyUpsertRequest(BaseModel):
    scope: BudgetScope
    scope_id: str = Field(..., min_length=1, max_length=100)
    period: BudgetPeriod = "monthly"
    limit_usd: float = Field(..., gt=0)
    warn_threshold_pct: float = Field(default=80.0, ge=0, le=100)


class BudgetRemaining(BaseModel):
    """One scope's remaining headroom, as of right after a single LLM call."""
    scope: BudgetScope
    scope_id: str
    limit_usd: float
    spent_usd: float
    remaining_usd: float


class UsageSummary(BaseModel):
    """Attached to a generate/regenerate response so the caller can show the
    user exactly what that one call cost, without a separate round trip.
    Only present when the call actually logged usage (see
    budget_service.build_usage_summary) — never present on request bodies.
    """
    cost_usd: float
    input_tokens: int
    output_tokens: int
    total_tokens: int
    # Only scopes that have a BudgetPolicy configured — an unrestricted scope
    # (no policy) is omitted rather than shown with a meaningless limit.
    budgets: list[BudgetRemaining] = []
