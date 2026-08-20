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

Fixed by reading max(current_period_usage() — the same live SUM() list_budgets
uses — , the ledger). Bare SUM() alone would understate spend when the
ledger is ahead of it (a worst-case reservation for an in-flight call, not
yet reconciled) — taking the max keeps the toast never worse than the true
log total (this bug) and never rosier than what the next call will actually
be blocked against (review finding #4).
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


def test_toast_matches_admin_dashboard_when_the_ledger_is_behind(db):
    """The common case (and the reported bug's shape): the ledger is stale
    relative to the log, so the toast must match what the admin dashboard
    would show — not silently use the smaller, wrong ledger value."""
    from promptops_app.database import BudgetPeriodSpend

    policy = _seed_policy(db, scope_id="admin_compare_user", limit_usd=10.00)
    _log_usage_row(db, user_id="admin_compare_user", entity_type="block_item_regen",
                   entity_id="1", cost_usd=1.50, total_tokens=1000)
    # Ledger deliberately behind the true log total (the reported bug's shape).
    db.add(BudgetPeriodSpend(scope="user", scope_id="admin_compare_user",
                            period_key=budget_service.period_key("monthly"),
                            spent_usd=0.10, spent_tokens=1))
    db.commit()
    _log_usage_row(db, user_id="admin_compare_user", entity_type="block_item_regen",
                   entity_id="2", cost_usd=0.25, total_tokens=200)

    summary = budget_service.build_usage_summary(db, _Ctx("admin_compare_user"), "block_item_regen", "2")
    user_budget = next(b for b in summary["budgets"] if b["scope"] == "user")

    admin_spend, admin_tokens = budget_service.current_period_usage(db, "user", "admin_compare_user", policy.period)

    assert user_budget["spent_usd"] == round(admin_spend, 4)
    assert user_budget["remaining_usd"] == round(policy.limit_usd - admin_spend, 4)


def test_toast_never_overstates_headroom_while_the_ledger_is_ahead(db):
    """Review finding #4: the ledger legitimately runs AHEAD of the log mid-
    call (check_budget reserves a worst-case estimate before the provider
    responds; reconcile_budget settles it after). A bare SUM() would show
    more headroom than actually exists for the very next call. The toast
    must reflect the higher (more conservative) of the two — diverging from
    the admin dashboard's bare SUM() is the correct, safe direction here."""
    from promptops_app.database import BudgetPeriodSpend

    policy = _seed_policy(db, scope_id="in_flight_user", limit_usd=10.00)
    _log_usage_row(db, user_id="in_flight_user", entity_type="block_item_regen",
                   entity_id="1", cost_usd=1.75, total_tokens=1200)
    # A worst-case reservation for an in-flight call, not yet reconciled —
    # genuinely ahead of what's been logged so far.
    db.add(BudgetPeriodSpend(scope="user", scope_id="in_flight_user",
                            period_key=budget_service.period_key("monthly"),
                            spent_usd=4.00, spent_tokens=5000))
    db.commit()

    summary = budget_service.build_usage_summary(db, _Ctx("in_flight_user"), "block_item_regen", "1")
    user_budget = next(b for b in summary["budgets"] if b["scope"] == "user")

    admin_spend, _ = budget_service.current_period_usage(db, "user", "in_flight_user", policy.period)
    assert admin_spend == pytest.approx(1.75)  # the bare SUM() an admin would see right now

    # The toast must not show more headroom than the reservation allows.
    assert user_budget["spent_usd"] == pytest.approx(4.00)
    assert user_budget["remaining_usd"] == pytest.approx(6.00)
