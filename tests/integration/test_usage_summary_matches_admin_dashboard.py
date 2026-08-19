"""build_usage_summary's remaining-budget figure must match the Platform
Admin dashboard's, since both are shown side by side for the same budget.

The bug: _build_usage_summary used to read budget_period_spend (the
enforcement ledger check_budget()/reconcile_budget() maintain via one atomic
UPDATE per call) instead of a live SUM() over llm_usage_logs (what
list_budgets — the Platform Admin dashboard — actually reads). That ledger
is a write-through cache that can silently fall behind true logged spend
(kill switch, check_budget's fail-open catch, a call made before a policy
existed, an unreserved MAP settlement), so a user's own post-regeneration
toast showed a wildly different "remaining" figure than what an admin saw
for the exact same budget: reported case was budget $3.00, true spend
$2.8788, but the toast showed "$2.19 left" (as if only ~$0.81 had been
spent) instead of the correct ~$0.12.

Fixed by reading current_period_usage() (the same live SUM() list_budgets
uses) instead of the ledger. This test pins that: a real gap between the
ledger and the log table must not leak into the toast's numbers.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from promptops_app.services import budget_service


class _Ctx:
    def __init__(self, user_name):
        self.user_name = user_name
        self.project_id = None
        self.course_id = None


def _seed_policy(db, *, scope_id, limit_usd=3.00):
    from promptops_app.database import BudgetPolicy

    policy = BudgetPolicy(scope="user", scope_id=scope_id, period="monthly",
                         limit_type="usd", limit_usd=limit_usd, warn_threshold_pct=80.0)
    db.add(policy)
    db.commit()
    return policy


def _log_usage_row(db, *, user_id, entity_type, entity_id, cost_usd, total_tokens, is_latest=False):
    from promptops_app.database import LLMUsageLog

    row = LLMUsageLog(
        user_id=user_id, entity_type=entity_type, entity_id=str(entity_id),
        model_name="gpt-4o", status="success",
        input_tokens=total_tokens // 2, output_tokens=total_tokens - total_tokens // 2,
        total_tokens=total_tokens, estimated_cost=cost_usd,
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()
    return row


def test_toast_reflects_true_spend_even_when_the_ledger_has_fallen_behind(db):
    """The exact reported bug, reproduced: a real $2.87 of prior spend logged
    to llm_usage_logs, but the enforcement ledger (budget_period_spend) only
    ever recorded $0.81 of it — as would happen if the kill switch was on, or
    calls were made before the BudgetPolicy existed. The toast for the latest
    $0.0088 regeneration must show ~$0.12 remaining (matching admin), not the
    ~$2.19 the stale ledger would have produced."""
    from promptops_app.database import BudgetPeriodSpend

    _seed_policy(db, scope_id="rgr", limit_usd=3.00)

    # Prior spend genuinely logged (what the admin dashboard sums).
    _log_usage_row(db, user_id="rgr", entity_type="block_item_regen", entity_id="900",
                   cost_usd=2.87, total_tokens=798_746 - 1_692)

    # The enforcement ledger under-recorded that same spend (the drift this
    # bug is about) — only $0.81 of the true $2.87 ever landed here.
    pkey = budget_service.period_key("monthly")
    db.add(BudgetPeriodSpend(scope="user", scope_id="rgr", period_key=pkey,
                            spent_usd=0.81, spent_tokens=200_000))
    db.commit()

    # The latest regeneration — the one the toast is actually for.
    latest = _log_usage_row(db, user_id="rgr", entity_type="block_item_regen", entity_id="901",
                            cost_usd=0.0088, total_tokens=1_692)

    summary = budget_service.build_usage_summary(db, _Ctx("rgr"), "block_item_regen", "901")

    assert summary["cost_usd"] == 0.0088
    assert summary["total_tokens"] == 1692

    user_budget = next(b for b in summary["budgets"] if b["scope"] == "user")
    assert user_budget["spent_usd"] == pytest.approx(2.8788, abs=0.0001)
    assert user_budget["remaining_usd"] == pytest.approx(0.1212, abs=0.0001)
    # The bug's exact wrong figure must not reappear.
    assert user_budget["remaining_usd"] != pytest.approx(2.19, abs=0.01)


def test_toast_and_admin_dashboard_report_the_identical_figure(db):
    """Direct equivalence check: whatever the toast shows for spent/remaining
    must be exactly what list_budgets (the admin dashboard's own query) would
    compute for the same user, at the same instant."""
    from promptops_app.database import BudgetPeriodSpend

    policy = _seed_policy(db, scope_id="admin_compare_user", limit_usd=10.00)
    _log_usage_row(db, user_id="admin_compare_user", entity_type="block_item_regen",
                   entity_id="1", cost_usd=1.50, total_tokens=1000)
    # Ledger deliberately wrong/stale — must not affect either figure.
    db.add(BudgetPeriodSpend(scope="user", scope_id="admin_compare_user",
                            period_key=budget_service.period_key("monthly"),
                            spent_usd=999.0, spent_tokens=1))
    db.commit()
    _log_usage_row(db, user_id="admin_compare_user", entity_type="block_item_regen",
                   entity_id="2", cost_usd=0.25, total_tokens=200)

    summary = budget_service.build_usage_summary(db, _Ctx("admin_compare_user"), "block_item_regen", "2")
    user_budget = next(b for b in summary["budgets"] if b["scope"] == "user")

    admin_spend, admin_tokens = budget_service.current_period_usage(db, "user", "admin_compare_user", policy.period)

    assert user_budget["spent_usd"] == round(admin_spend, 4)
    assert user_budget["remaining_usd"] == round(policy.limit_usd - admin_spend, 4)
