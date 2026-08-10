"""Budget policy schemas for the platform-admin API — P3.1 of
claude_plan_platform_hardening.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

BudgetScope = Literal["project", "course", "user"]
BudgetPeriod = Literal["monthly", "rolling"]
BudgetLimitType = Literal["usd", "tokens"]


class BudgetPolicyRead(BaseModel):
    id: int
    scope: BudgetScope
    scope_id: str
    period: BudgetPeriod
    limit_type: BudgetLimitType = "usd"
    limit_usd: Optional[float] = None
    limit_tokens: Optional[int] = None
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
    # Either/or: exactly one of limit_usd/limit_tokens is required, matching
    # limit_type — enforced below rather than via two separate endpoints.
    limit_type: BudgetLimitType = "usd"
    limit_usd: Optional[float] = Field(default=None, gt=0)
    limit_tokens: Optional[int] = Field(default=None, gt=0)
    warn_threshold_pct: float = Field(default=80.0, ge=0, le=100)

    @model_validator(mode="after")
    def _require_matching_limit(self) -> "BudgetPolicyUpsertRequest":
        if self.limit_type == "usd" and self.limit_usd is None:
            raise ValueError("limit_usd is required when limit_type is 'usd'")
        if self.limit_type == "tokens" and self.limit_tokens is None:
            raise ValueError("limit_tokens is required when limit_type is 'tokens'")
        return self


class BudgetRemaining(BaseModel):
    """One scope's remaining headroom, as of right after a single LLM call."""
    scope: BudgetScope
    scope_id: str
    limit_type: BudgetLimitType = "usd"
    limit_usd: Optional[float] = None
    spent_usd: Optional[float] = None
    remaining_usd: Optional[float] = None
    limit_tokens: Optional[int] = None
    spent_tokens: Optional[int] = None
    remaining_tokens: Optional[int] = None


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
