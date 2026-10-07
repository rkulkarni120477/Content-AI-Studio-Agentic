"""Unit Tests for Agent Budget Service — Stage 4 of Phase 2

Comprehensive test coverage for budget tracking and enforcement:
- Budget reservation (pessimistic allocation)
- Cost reconciliation (actual vs reserved)
- Budget release on failure/cancel
- Hard/soft limit enforcement
- Period-based accounting
- Run cost aggregation
- Project usage summary
- Tenant isolation
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch, MagicMock

from app.core.exceptions import NotFoundError, ValidationError, WorkflowError
from promptops_app.agents_models import AgentRun, AgentRunStep, AgentDefinition
from promptops_app.database import BudgetPolicy, BudgetPeriodSpend
from promptops_app.services.agent_budget_service import (
    AgentBudgetService,
    BudgetStatus,
    BudgetReservationRecord,
)
from promptops_app.services.budget_service import BudgetExceededError
from sqlalchemy.orm import Session


class TestGetProjectBudget:
    """Tests for get_project_budget() method."""

    def test_get_project_budget_with_policy(self, db_session):
        """Should return budget policy if configured."""
        # Create mock policy
        with patch("promptops_app.services.agent_budget_service._get_policy") as mock_get:
            mock_policy = Mock(spec=BudgetPolicy)
            mock_policy.project_id = 1
            mock_policy.limit_usd = 100.0
            mock_policy.period = "monthly"
            mock_get.return_value = mock_policy

            result = AgentBudgetService.get_project_budget(db_session, 1)

            assert result == mock_policy
            assert result.limit_usd == 100.0

    def test_get_project_budget_no_policy(self, db_session):
        """Should return None if no budget policy configured."""
        with patch("promptops_app.services.agent_budget_service._get_policy") as mock_get:
            mock_get.return_value = None

            result = AgentBudgetService.get_project_budget(db_session, 999)

            assert result is None


class TestReserveBudget:
    """Tests for reserve_budget() method."""

    def test_reserve_budget_zero_cost(self, db_session):
        """Should skip reservation for zero cost."""
        success, record = AgentBudgetService.reserve_budget(
            db_session,
            project_id=1,
            estimated_cost=0.0,
            run_id=100,
        )

        assert success is True
        assert record is None

    def test_reserve_budget_negative_cost(self, db_session):
        """Should reject negative cost."""
        with pytest.raises(ValidationError):
            AgentBudgetService.reserve_budget(
                db_session,
                project_id=1,
                estimated_cost=-10.0,
                run_id=100,
            )

    def test_reserve_budget_no_policy(self, db_session):
        """Should allow reservation if no budget policy configured."""
        with patch("promptops_app.services.agent_budget_service.get_project_budget") as mock_get:
            mock_get.return_value = None

            success, record = AgentBudgetService.reserve_budget(
                db_session,
                project_id=1,
                estimated_cost=50.0,
                run_id=100,
            )

            assert success is True
            assert record is None

    def test_reserve_budget_success(self, db_session):
        """Should create reservation record when successful."""
        mock_policy = Mock(spec=BudgetPolicy)
        mock_policy.project_id = 1
        mock_policy.limit_usd = 200.0
        mock_policy.is_warning_only = False
        mock_policy.limit_type = "usd"

        with patch("promptops_app.services.agent_budget_service.get_project_budget") as mock_get_policy:
            with patch("promptops_app.services.agent_budget_service._reserve") as mock_reserve:
                mock_get_policy.return_value = mock_policy
                mock_reserve.return_value = True

                success, record = AgentBudgetService.reserve_budget(
                    db_session,
                    project_id=1,
                    estimated_cost=50.0,
                    run_id=100,
                    estimated_tokens=1500,
                )

                assert success is True
                assert record is not None
                assert record.run_id == 100
                assert record.reserved_cost_usd == 50.0
                assert record.reserved_tokens == 1500

    def test_reserve_budget_hard_limit_exceeded(self, db_session):
        """Should raise BudgetExceededError when hard limit exceeded."""
        mock_policy = Mock(spec=BudgetPolicy)
        mock_policy.project_id = 1
        mock_policy.limit_usd = 50.0
        mock_policy.is_warning_only = False
        mock_policy.period = "monthly"
        mock_policy.limit_type = "usd"

        with patch("promptops_app.services.agent_budget_service.get_project_budget") as mock_get_policy:
            with patch("promptops_app.services.agent_budget_service._reserve") as mock_reserve:
                with patch("promptops_app.services.agent_budget_service.current_period_spend") as mock_spend:
                    mock_get_policy.return_value = mock_policy
                    mock_reserve.return_value = False  # Reservation failed
                    mock_spend.return_value = 45.0

                    with pytest.raises(BudgetExceededError):
                        AgentBudgetService.reserve_budget(
                            db_session,
                            project_id=1,
                            estimated_cost=20.0,
                            run_id=100,
                        )

    def test_reserve_budget_soft_limit_warning(self, db_session):
        """Should allow with warning on soft limit."""
        mock_policy = Mock(spec=BudgetPolicy)
        mock_policy.project_id = 1
        mock_policy.limit_usd = 50.0
        mock_policy.is_warning_only = True  # Soft limit
        mock_policy.period = "monthly"
        mock_policy.limit_type = "usd"

        with patch("promptops_app.services.agent_budget_service.get_project_budget") as mock_get_policy:
            with patch("promptops_app.services.agent_budget_service._reserve") as mock_reserve:
                with patch("promptops_app.services.agent_budget_service.current_period_spend") as mock_spend:
                    mock_get_policy.return_value = mock_policy
                    mock_reserve.return_value = False  # Would exceed
                    mock_spend.return_value = 45.0

                    success, record = AgentBudgetService.reserve_budget(
                        db_session,
                        project_id=1,
                        estimated_cost=20.0,
                        run_id=100,
                    )

                    assert success is True  # Allowed with soft limit
                    assert record is None  # No hard reservation

    def test_reserve_budget_estimates_tokens(self, db_session):
        """Should estimate tokens if not provided."""
        mock_policy = Mock(spec=BudgetPolicy)
        mock_policy.is_warning_only = False

        with patch("promptops_app.services.agent_budget_service.get_project_budget") as mock_get_policy:
            with patch("promptops_app.services.agent_budget_service._reserve") as mock_reserve:
                mock_get_policy.return_value = mock_policy
                mock_reserve.return_value = True

                AgentBudgetService.reserve_budget(
                    db_session,
                    project_id=1,
                    estimated_cost=0.003,  # ~100 tokens at $0.00003/token
                    run_id=100,
                )

                # Verify _reserve was called with estimated tokens
                assert mock_reserve.called
                call_kwargs = mock_reserve.call_args[1]
                assert call_kwargs["tokens"] == pytest.approx(100, rel=0.1)


class TestReconcileCost:
    """Tests for reconcile_cost() method."""

    def test_reconcile_cost_run_not_found(self, db_session):
        """Should raise NotFoundError if run not found."""
        with pytest.raises(NotFoundError):
            AgentBudgetService.reconcile_cost(
                db_session,
                project_id=1,
                run_id=999,
                actual_cost=10.0,
            )

    def test_reconcile_cost_negative(self, db_session):
        """Should reject negative actual cost."""
        with pytest.raises(ValidationError):
            AgentBudgetService.reconcile_cost(
                db_session,
                project_id=1,
                run_id=100,
                actual_cost=-5.0,
            )

    def test_reconcile_cost_no_policy(self, db_session, mock_agent_run):
        """Should handle gracefully if no policy configured."""
        mock_run = Mock(spec=AgentRun)
        mock_run.id = 100
        mock_run.project_id = 1
        mock_run.estimated_cost = 50.0

        with patch.object(db_session.query(AgentRun), "first", return_value=mock_run):
            with patch("promptops_app.services.agent_budget_service.get_project_budget") as mock_get:
                mock_get.return_value = None

                # Should not raise
                AgentBudgetService.reconcile_cost(
                    db_session,
                    project_id=1,
                    run_id=100,
                    actual_cost=25.0,
                    prompt_tokens=500,
                    completion_tokens=200,
                )

    def test_reconcile_cost_with_delta(self, db_session):
        """Should reconcile delta between reserved and actual."""
        mock_policy = Mock(spec=BudgetPolicy)
        mock_policy.limit_usd = 500.0

        mock_run = Mock(spec=AgentRun)
        mock_run.id = 100
        mock_run.project_id = 1
        mock_run.estimated_cost = 50.0

        with patch.object(db_session.query(AgentRun), "first", return_value=mock_run):
            with patch("promptops_app.services.agent_budget_service.get_project_budget") as mock_get:
                with patch("promptops_app.services.agent_budget_service.reconcile_budget") as mock_rec:
                    mock_get.return_value = mock_policy
                    mock_rec.return_value = None

                    AgentBudgetService.reconcile_cost(
                        db_session,
                        project_id=1,
                        run_id=100,
                        actual_cost=25.0,  # Less than estimated 50.0
                        prompt_tokens=400,
                        completion_tokens=150,
                    )

                    # Verify reconcile was called
                    assert mock_rec.called
                    call_args = mock_rec.call_args
                    assert call_args[0][1].reserved_usd == 50.0
                    assert call_args[0][2] == 25.0  # actual_cost
                    assert call_args[1]["tokens"] == 550  # 400 + 150


class TestReleaseBudget:
    """Tests for release_budget() method."""

    def test_release_budget_run_not_found(self, db_session):
        """Should raise NotFoundError if run not found."""
        with pytest.raises(NotFoundError):
            AgentBudgetService.release_budget(
                db_session,
                project_id=1,
                run_id=999,
            )

    def test_release_budget_not_reserved(self, db_session):
        """Should handle gracefully if no budget was reserved."""
        mock_run = Mock(spec=AgentRun)
        mock_run.id = 100
        mock_run.project_id = 1
        mock_run.budget_reserved = False
        mock_run.estimated_cost = None

        with patch.object(db_session.query(AgentRun), "filter") as mock_query:
            mock_query.return_value.first.return_value = mock_run

            # Should not raise
            AgentBudgetService.release_budget(
                db_session,
                project_id=1,
                run_id=100,
            )

    def test_release_budget_success(self, db_session):
        """Should release reserved budget."""
        mock_policy = Mock(spec=BudgetPolicy)
        mock_policy.limit_usd = 500.0

        mock_run = Mock(spec=AgentRun)
        mock_run.id = 100
        mock_run.project_id = 1
        mock_run.budget_reserved = True
        mock_run.estimated_cost = 50.0

        with patch.object(db_session.query(AgentRun), "filter") as mock_query:
            with patch("promptops_app.services.agent_budget_service.get_project_budget") as mock_get:
                with patch("promptops_app.services.agent_budget_service.reconcile_budget") as mock_rec:
                    mock_query.return_value.first.return_value = mock_run
                    mock_get.return_value = mock_policy
                    mock_rec.return_value = None

                    AgentBudgetService.release_budget(
                        db_session,
                        project_id=1,
                        run_id=100,
                    )

                    # Verify reconcile was called to release
                    assert mock_rec.called
                    call_args = mock_rec.call_args
                    assert call_args[0][2] == 0.0  # actual_cost = 0 (full release)


class TestGetBudgetStatus:
    """Tests for get_budget_status() method."""

    def test_get_budget_status_no_policy(self, db_session):
        """Should raise ValidationError if no policy configured."""
        with patch("promptops_app.services.agent_budget_service.get_project_budget") as mock_get:
            mock_get.return_value = None

            with pytest.raises(ValidationError):
                AgentBudgetService.get_budget_status(db_session, project_id=1)

    def test_get_budget_status_success(self, db_session):
        """Should return comprehensive budget status."""
        mock_policy = Mock(spec=BudgetPolicy)
        mock_policy.project_id = 1
        mock_policy.limit_usd = 500.0
        mock_policy.period = "monthly"
        mock_policy.limit_type = "usd"
        mock_policy.is_warning_only = False

        with patch("promptops_app.services.agent_budget_service.get_project_budget") as mock_get:
            with patch("promptops_app.services.agent_budget_service.current_period_spend") as mock_spend:
                with patch("promptops_app.services.agent_budget_service.period_key") as mock_key:
                    mock_get.return_value = mock_policy
                    mock_spend.return_value = 250.0
                    mock_key.return_value = "2026-10"

                    status = AgentBudgetService.get_budget_status(db_session, project_id=1)

                    assert status.project_id == 1
                    assert status.current_spend == 250.0
                    assert status.remaining_budget == 250.0
                    assert status.limit_usd == 500.0
                    assert status.percent_used == 50.0
                    assert status.is_enforced is True

    def test_get_budget_status_over_limit(self, db_session):
        """Should handle status when over limit."""
        mock_policy = Mock(spec=BudgetPolicy)
        mock_policy.project_id = 1
        mock_policy.limit_usd = 500.0
        mock_policy.period = "monthly"
        mock_policy.limit_type = "usd"
        mock_policy.is_warning_only = False

        with patch("promptops_app.services.agent_budget_service.get_project_budget") as mock_get:
            with patch("promptops_app.services.agent_budget_service.current_period_spend") as mock_spend:
                with patch("promptops_app.services.agent_budget_service.period_key") as mock_key:
                    mock_get.return_value = mock_policy
                    mock_spend.return_value = 600.0  # Over limit
                    mock_key.return_value = "2026-10"

                    status = AgentBudgetService.get_budget_status(db_session, project_id=1)

                    assert status.current_spend == 600.0
                    assert status.remaining_budget == 0.0  # Clamped to 0
                    assert status.percent_used == 100.0  # Clamped to 100


class TestEnforceBudget:
    """Tests for enforce_budget() method."""

    def test_enforce_budget_no_policy(self, db_session):
        """Should allow if no policy configured."""
        with patch("promptops_app.services.agent_budget_service.get_project_budget") as mock_get:
            mock_get.return_value = None

            allowed, warning = AgentBudgetService.enforce_budget(
                db_session,
                project_id=1,
                estimated_cost=100.0,
            )

            assert allowed is True
            assert warning is None

    def test_enforce_budget_hard_limit_exceeded(self, db_session):
        """Should raise BudgetExceededError on hard limit."""
        mock_policy = Mock(spec=BudgetPolicy)
        mock_policy.limit_usd = 500.0
        mock_policy.is_warning_only = False

        mock_status = BudgetStatus(
            project_id=1,
            current_spend=450.0,
            remaining_budget=50.0,
            limit_usd=500.0,
            period="monthly",
            period_key="2026-10",
            limit_type="usd",
            is_enforced=True,
            percent_used=90.0,
        )

        with patch("promptops_app.services.agent_budget_service.get_project_budget") as mock_get:
            with patch("promptops_app.services.agent_budget_service.get_budget_status") as mock_status_fn:
                mock_get.return_value = mock_policy
                mock_status_fn.return_value = mock_status

                with pytest.raises(BudgetExceededError):
                    AgentBudgetService.enforce_budget(
                        db_session,
                        project_id=1,
                        estimated_cost=100.0,  # More than remaining 50
                    )

    def test_enforce_budget_soft_limit_exceeded_allow(self, db_session):
        """Should allow with warning on soft limit."""
        mock_policy = Mock(spec=BudgetPolicy)
        mock_policy.limit_usd = 500.0
        mock_policy.is_warning_only = True  # Soft limit

        mock_status = BudgetStatus(
            project_id=1,
            current_spend=450.0,
            remaining_budget=50.0,
            limit_usd=500.0,
            period="monthly",
            period_key="2026-10",
            limit_type="usd",
            is_enforced=False,
            percent_used=90.0,
        )

        with patch("promptops_app.services.agent_budget_service.get_project_budget") as mock_get:
            with patch("promptops_app.services.agent_budget_service.get_budget_status") as mock_status_fn:
                mock_get.return_value = mock_policy
                mock_status_fn.return_value = mock_status

                allowed, warning = AgentBudgetService.enforce_budget(
                    db_session,
                    project_id=1,
                    estimated_cost=100.0,
                )

                assert allowed is True
                assert warning is not None
                assert "warning" in warning.lower()

    def test_enforce_budget_approaching_limit(self, db_session):
        """Should warn when approaching limit even if not exceeded."""
        mock_policy = Mock(spec=BudgetPolicy)
        mock_policy.limit_usd = 500.0
        mock_policy.is_warning_only = False

        mock_status = BudgetStatus(
            project_id=1,
            current_spend=380.0,  # 76% used
            remaining_budget=120.0,
            limit_usd=500.0,
            period="monthly",
            period_key="2026-10",
            limit_type="usd",
            is_enforced=True,
            percent_used=76.0,
        )

        with patch("promptops_app.services.agent_budget_service.get_project_budget") as mock_get:
            with patch("promptops_app.services.agent_budget_service.get_budget_status") as mock_status_fn:
                mock_get.return_value = mock_policy
                mock_status_fn.return_value = mock_status

                allowed, warning = AgentBudgetService.enforce_budget(
                    db_session,
                    project_id=1,
                    estimated_cost=50.0,
                )

                assert allowed is True
                assert warning is not None
                assert "76" in warning  # Should mention percent used

    def test_enforce_budget_within_limits(self, db_session):
        """Should allow without warning when plenty of budget."""
        mock_policy = Mock(spec=BudgetPolicy)
        mock_policy.limit_usd = 500.0
        mock_policy.is_warning_only = False

        mock_status = BudgetStatus(
            project_id=1,
            current_spend=100.0,
            remaining_budget=400.0,
            limit_usd=500.0,
            period="monthly",
            period_key="2026-10",
            limit_type="usd",
            is_enforced=True,
            percent_used=20.0,
        )

        with patch("promptops_app.services.agent_budget_service.get_project_budget") as mock_get:
            with patch("promptops_app.services.agent_budget_service.get_budget_status") as mock_status_fn:
                mock_get.return_value = mock_policy
                mock_status_fn.return_value = mock_status

                allowed, warning = AgentBudgetService.enforce_budget(
                    db_session,
                    project_id=1,
                    estimated_cost=50.0,
                )

                assert allowed is True
                assert warning is None


class TestGetRunCosts:
    """Tests for get_run_costs() method."""

    def test_get_run_costs_not_found(self, db_session):
        """Should raise NotFoundError if run not found."""
        with pytest.raises(NotFoundError):
            AgentBudgetService.get_run_costs(
                db_session,
                project_id=1,
                run_id=999,
            )

    def test_get_run_costs_empty_run(self, db_session):
        """Should return zero costs for run with no steps."""
        mock_run = Mock(spec=AgentRun)
        mock_run.id = 100
        mock_run.project_id = 1

        with patch.object(db_session.query(AgentRun), "filter") as mock_run_query:
            with patch.object(db_session.query(AgentRunStep), "filter") as mock_step_query:
                mock_run_query.return_value.first.return_value = mock_run
                mock_step_query.return_value.order_by.return_value.all.return_value = []

                costs = AgentBudgetService.get_run_costs(
                    db_session,
                    project_id=1,
                    run_id=100,
                )

                assert costs["run_id"] == 100
                assert costs["total_cost"] == 0.0
                assert costs["total_tokens"] == 0
                assert len(costs["steps"]) == 0

    def test_get_run_costs_with_steps(self, db_session):
        """Should aggregate costs from all steps."""
        mock_run = Mock(spec=AgentRun)
        mock_run.id = 100
        mock_run.project_id = 1

        mock_step1 = Mock(spec=AgentRunStep)
        mock_step1.id = 1
        mock_step1.step_index = 0
        mock_step1.step_type = "model_call"
        mock_step1.model_used = "gpt-4o"
        mock_step1.step_cost = 0.025
        mock_step1.prompt_tokens = 450
        mock_step1.completion_tokens = 200

        mock_step2 = Mock(spec=AgentRunStep)
        mock_step2.id = 2
        mock_step2.step_index = 1
        mock_step2.step_type = "validation"
        mock_step2.model_used = "gpt-4o"
        mock_step2.step_cost = 0.010
        mock_step2.prompt_tokens = 200
        mock_step2.completion_tokens = 50

        with patch.object(db_session.query(AgentRun), "filter") as mock_run_query:
            with patch.object(db_session.query(AgentRunStep), "filter") as mock_step_query:
                mock_run_query.return_value.first.return_value = mock_run
                mock_step_query.return_value.order_by.return_value.all.return_value = [
                    mock_step1,
                    mock_step2,
                ]

                costs = AgentBudgetService.get_run_costs(
                    db_session,
                    project_id=1,
                    run_id=100,
                )

                assert costs["total_cost"] == pytest.approx(0.035)
                assert costs["total_tokens"] == 900
                assert len(costs["steps"]) == 2
                assert costs["by_model"]["gpt-4o"]["cost"] == pytest.approx(0.035)
                assert costs["by_model"]["gpt-4o"]["tokens"] == 900
                assert costs["by_model"]["gpt-4o"]["count"] == 2


class TestGetProjectUsageSummary:
    """Tests for get_project_usage_summary() method."""

    def test_get_usage_summary_no_runs(self, db_session):
        """Should return zero summary for project with no runs."""
        with patch.object(db_session.query(AgentRun), "filter") as mock_query:
            mock_query.return_value.order_by.return_value.all.return_value = []

            summary = AgentBudgetService.get_project_usage_summary(
                db_session,
                project_id=1,
                days=30,
            )

            assert summary["project_id"] == 1
            assert summary["period_days"] == 30
            assert summary["total_cost"] == 0.0
            assert summary["total_runs"] == 0
            assert summary["average_cost_per_run"] == 0.0

    def test_get_usage_summary_with_runs(self, db_session):
        """Should aggregate costs by day."""
        today = datetime.now(timezone.utc)
        yesterday = today - timedelta(days=1)

        mock_run1 = Mock(spec=AgentRun)
        mock_run1.created_at = today
        mock_run1.actual_cost = 25.0

        mock_run2 = Mock(spec=AgentRun)
        mock_run2.created_at = yesterday
        mock_run2.actual_cost = 15.0

        with patch.object(db_session.query(AgentRun), "filter") as mock_query:
            mock_query.return_value.order_by.return_value.all.return_value = [
                mock_run1,
                mock_run2,
            ]

            summary = AgentBudgetService.get_project_usage_summary(
                db_session,
                project_id=1,
                days=30,
            )

            assert summary["project_id"] == 1
            assert summary["total_cost"] == 40.0
            assert summary["total_runs"] == 2
            assert summary["average_cost_per_run"] == 20.0
            assert len(summary["daily_costs"]) == 2

    def test_get_usage_summary_custom_period(self, db_session):
        """Should respect custom days parameter."""
        with patch.object(db_session.query(AgentRun), "filter") as mock_query:
            mock_query.return_value.order_by.return_value.all.return_value = []

            summary = AgentBudgetService.get_project_usage_summary(
                db_session,
                project_id=1,
                days=7,
            )

            assert summary["period_days"] == 7


class TestBudgetIntegration:
    """Integration tests for budget operations."""

    def test_full_budget_lifecycle(self, db_session):
        """Test complete budget flow: reserve -> execute -> reconcile."""
        mock_policy = Mock(spec=BudgetPolicy)
        mock_policy.limit_usd = 500.0
        mock_policy.is_warning_only = False
        mock_policy.period = "monthly"
        mock_policy.limit_type = "usd"

        mock_run = Mock(spec=AgentRun)
        mock_run.id = 100
        mock_run.project_id = 1
        mock_run.estimated_cost = 50.0
        mock_run.budget_reserved = True

        # 1. Reserve
        with patch("promptops_app.services.agent_budget_service.get_project_budget") as mock_get:
            with patch("promptops_app.services.agent_budget_service._reserve") as mock_reserve:
                mock_get.return_value = mock_policy
                mock_reserve.return_value = True

                success, record = AgentBudgetService.reserve_budget(
                    db_session, 1, 50.0, 100
                )
                assert success is True
                assert record.reserved_cost_usd == 50.0

        # 2. Reconcile (actual cost less than estimated)
        with patch.object(db_session.query(AgentRun), "filter") as mock_query:
            with patch("promptops_app.services.agent_budget_service.get_project_budget") as mock_get:
                with patch("promptops_app.services.agent_budget_service.reconcile_budget") as mock_rec:
                    mock_query.return_value.first.return_value = mock_run
                    mock_get.return_value = mock_policy
                    mock_rec.return_value = None

                    AgentBudgetService.reconcile_cost(
                        db_session, 1, 100, 25.0, 400, 150
                    )

                    # Should have called reconcile with delta
                    assert mock_rec.called

    def test_budget_enforcement_prevents_over_spend(self, db_session):
        """Test that hard limits prevent execution."""
        mock_policy = Mock(spec=BudgetPolicy)
        mock_policy.limit_usd = 100.0
        mock_policy.is_warning_only = False

        mock_status = BudgetStatus(
            project_id=1,
            current_spend=95.0,
            remaining_budget=5.0,
            limit_usd=100.0,
            period="monthly",
            period_key="2026-10",
            limit_type="usd",
            is_enforced=True,
            percent_used=95.0,
        )

        with patch("promptops_app.services.agent_budget_service.get_project_budget") as mock_get:
            with patch("promptops_app.services.agent_budget_service.get_budget_status") as mock_status_fn:
                mock_get.return_value = mock_policy
                mock_status_fn.return_value = mock_status

                with pytest.raises(BudgetExceededError):
                    AgentBudgetService.enforce_budget(
                        db_session, 1, 20.0  # Trying to spend more than remaining
                    )


# Test fixtures

@pytest.fixture
def db_session():
    """Mock database session."""
    return Mock(spec=Session)


@pytest.fixture
def mock_agent_run():
    """Mock agent run."""
    run = Mock(spec=AgentRun)
    run.id = 100
    run.project_id = 1
    run.estimated_cost = 50.0
    run.budget_reserved = True
    return run
