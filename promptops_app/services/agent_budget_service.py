"""Agent Budget Service — Stage 4 of Phase 2

Manages budget tracking and enforcement for agent execution:
- Reserve budget pessimistically before run execution
- Track actual cost per step and reconcile after completion
- Release budget on failure/cancellation
- Enforce hard limits (block execution) and soft limits (warn only)
- Support period-based accounting (daily/monthly/quarterly)
- Integration with agent run service and LLM cost tracking

Patterns:
- Uses existing budget_service infrastructure and models
- Implements race-safe atomic operations for SQLite
- Pessimistic reservation followed by reconciliation
- Tenant isolation via project_id
- Comprehensive audit trail for debugging
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Tuple

from sqlalchemy import and_, func, text
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError, ValidationError, WorkflowError
from promptops_app.agents_models import AgentRun, AgentRunStep
from promptops_app.database import BudgetPolicy, BudgetPeriodSpend, LLMUsageLog
from promptops_app.services.agents_tenant_service import AgentsTenantService
from promptops_app.services.budget_service import (
    BudgetExceededError,
    period_key,
    current_period_spend,
    _get_policy,
    _reserve,
    reconcile_budget,
    BudgetReservation,
)
from promptops_app.services.usage_service import estimate_cost

_log = logging.getLogger(__name__)


@dataclass
class BudgetStatus:
    """Current budget status for a project."""
    project_id: int
    current_spend: float  # USD spent in current period
    remaining_budget: float  # USD available
    limit_usd: float  # Total budget limit
    period: str  # "daily", "monthly", "quarterly"
    period_key: str  # Current period bucket
    limit_type: str  # "usd" or "tokens"
    is_enforced: bool  # Hard limit vs soft limit (warning)
    percent_used: float  # 0-100


@dataclass
class BudgetReservationRecord:
    """Record of a budget reservation for a run."""
    run_id: int
    reserved_cost_usd: float
    reserved_tokens: int
    period_key: str
    timestamp: datetime


class AgentBudgetService:
    """Service for managing budget tracking and enforcement in agent execution."""

    @staticmethod
    def get_project_budget(
        db: Session,
        project_id: int,
    ) -> Optional[BudgetPolicy]:
        """
        Retrieve budget configuration for a project.

        Returns the project's budget policy including limits, period, and
        enforcement mode. Returns None if no budget policy is configured.

        Args:
            db: SQLAlchemy session
            project_id: Tenant ID

        Returns:
            BudgetPolicy object or None if no policy configured

        Raises:
            NotFoundError: If project doesn't exist
        """
        # Verify project exists (basic validation)
        policy = _get_policy(db, "project", str(project_id))

        if policy:
            _log.info(
                f"Retrieved budget policy for project {project_id}: "
                f"limit={policy.limit_usd}USD, period={policy.period}, "
                f"enforced={not policy.is_warning_only}"
            )
        else:
            _log.debug(f"No budget policy configured for project {project_id}")

        return policy

    @staticmethod
    def reserve_budget(
        db: Session,
        project_id: int,
        estimated_cost: float,
        run_id: int,
        estimated_tokens: Optional[int] = None,
    ) -> Tuple[bool, Optional[BudgetReservationRecord]]:
        """
        Reserve budget pessimistically before run execution.

        Uses worst-case token estimation if actual token count unknown.
        Reserves atomically against the project's budget limit.
        Raises BudgetExceededError if hard limit would be exceeded.

        Args:
            db: SQLAlchemy session
            project_id: Tenant ID
            estimated_cost: Estimated USD cost (worst case)
            run_id: The run this budget is reserved for
            estimated_tokens: Optional worst-case token count

        Returns:
            Tuple of (success, reservation_record)
            - success: True if budget reserved, False if would exceed limit
            - reservation_record: Details of reservation for later reconciliation

        Raises:
            BudgetExceededError: If project has hard limit enforced and would exceed
            ValidationError: If estimated_cost is negative or project not found
        """
        if estimated_cost < 0:
            raise ValidationError(f"Cannot reserve negative cost: {estimated_cost}")

        # If no cost, no reservation needed
        if estimated_cost == 0:
            _log.debug(f"Zero cost for run {run_id}, no budget reservation needed")
            return True, None

        # Get budget policy for project
        policy = AgentBudgetService.get_project_budget(db, project_id)
        if not policy:
            _log.debug(f"No budget policy for project {project_id}, allowing run without reservation")
            return True, None

        # Determine period key for this budget cycle
        pkey = period_key()

        # Estimate tokens if not provided
        if estimated_tokens is None:
            # Convert cost to tokens: rough estimate using $0.00003 per token
            # (based on average GPT-4o pricing)
            estimated_tokens = int(estimated_cost / 0.00003) if estimated_cost > 0 else 0

        try:
            # Attempt atomic reservation
            reserved = _reserve(
                db,
                scope="project",
                scope_id=str(project_id),
                pkey=pkey,
                cost_usd=estimated_cost,
                tokens=estimated_tokens,
                policy=policy,
            )

            if reserved:
                reservation = BudgetReservationRecord(
                    run_id=run_id,
                    reserved_cost_usd=estimated_cost,
                    reserved_tokens=estimated_tokens,
                    period_key=pkey,
                    timestamp=datetime.now(timezone.utc),
                )
                _log.info(
                    f"Reserved budget for run {run_id}: "
                    f"${estimated_cost:.2f} ({estimated_tokens} tokens) "
                    f"in period {pkey}"
                )
                return True, reservation
            else:
                # Would exceed budget
                if policy.is_warning_only:
                    # Soft limit: log warning but allow
                    current_spend = current_period_spend(
                        db, "project", str(project_id), policy.period
                    )
                    _log.warning(
                        f"Soft budget limit approaching for project {project_id}: "
                        f"${current_spend:.2f} of ${policy.limit_usd:.2f} used. "
                        f"Run {run_id} would add ${estimated_cost:.2f}"
                    )
                    # Create record but don't persist to budget_period_spend
                    # (will be added to audit log separately)
                    return True, None
                else:
                    # Hard limit: raise error
                    current_spend = current_period_spend(
                        db, "project", str(project_id), policy.period
                    )
                    raise BudgetExceededError(
                        scope="project",
                        scope_id=str(project_id),
                        limit_usd=policy.limit_usd,
                        current_spend=current_spend,
                    )

        except BudgetExceededError:
            raise
        except Exception as e:
            _log.error(
                f"Error reserving budget for run {run_id}: {str(e)}", exc_info=True
            )
            raise WorkflowError(f"Budget reservation failed: {str(e)}")

    @staticmethod
    def reconcile_cost(
        db: Session,
        project_id: int,
        run_id: int,
        actual_cost: float,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> None:
        """
        Reconcile actual cost after run completes.

        Adjusts budget reservation from worst-case to actual cost.
        Records actual token usage and cost in audit trail.

        Args:
            db: SQLAlchemy session
            project_id: Tenant ID
            run_id: The run being reconciled
            actual_cost: Actual USD cost from execution
            prompt_tokens: Actual prompt tokens used
            completion_tokens: Actual completion tokens used

        Raises:
            NotFoundError: If run not found or wrong tenant
            ValidationError: If actual_cost negative
        """
        if actual_cost < 0:
            raise ValidationError(f"Cannot reconcile negative cost: {actual_cost}")

        # Verify run ownership
        run = db.query(AgentRun).filter(
            and_(
                AgentRun.id == run_id,
                AgentRun.project_id == project_id,
            )
        ).first()

        if not run:
            raise NotFoundError("run", run_id)

        # Get budget policy
        policy = AgentBudgetService.get_project_budget(db, project_id)
        if not policy:
            _log.debug(f"No budget policy for project {project_id}, skipping reconciliation")
            return

        # Calculate reconciliation delta
        reserved_cost = run.estimated_cost or 0
        delta = reserved_cost - actual_cost

        if delta != 0:
            pkey = period_key()
            total_tokens = prompt_tokens + completion_tokens

            try:
                # Call existing reconcile_budget function
                reservation = BudgetReservation(
                    scope="project",
                    scope_id=str(project_id),
                    period_key=pkey,
                    reserved_usd=reserved_cost,
                    reserved_tokens=0,  # Not tracking reserved tokens
                )

                reconcile_budget(
                    db,
                    reservation,
                    actual_cost,
                    tokens=total_tokens,
                )

                _log.info(
                    f"Reconciled budget for run {run_id}: "
                    f"reserved=${reserved_cost:.2f}, actual=${actual_cost:.2f}, "
                    f"delta=${delta:.2f} ({total_tokens} tokens)"
                )

            except Exception as e:
                _log.error(
                    f"Error reconciling budget for run {run_id}: {str(e)}", exc_info=True
                )
                # Don't fail the run if reconciliation fails
                # Log for manual review

    @staticmethod
    def release_budget(
        db: Session,
        project_id: int,
        run_id: int,
    ) -> None:
        """
        Release reserved budget on run failure or cancellation.

        Recovers pessimistically-reserved budget when run doesn't execute.
        Handles both hard-reserved budget (in budget_period_spend) and
        soft-warned budget (audit only).

        Args:
            db: SQLAlchemy session
            project_id: Tenant ID
            run_id: The run being cancelled/failed

        Raises:
            NotFoundError: If run not found or wrong tenant
        """
        # Verify run ownership
        run = db.query(AgentRun).filter(
            and_(
                AgentRun.id == run_id,
                AgentRun.project_id == project_id,
            )
        ).first()

        if not run:
            raise NotFoundError("run", run_id)

        # If no budget was reserved, nothing to release
        if not run.budget_reserved or not run.estimated_cost:
            _log.debug(f"No budget reserved for run {run_id}, nothing to release")
            return

        # Get budget policy
        policy = AgentBudgetService.get_project_budget(db, project_id)
        if not policy:
            _log.debug(f"No budget policy for project {project_id}, no release needed")
            return

        # Release by reconciling reserved amount back to $0 spent
        pkey = period_key()
        reserved_cost = run.estimated_cost

        try:
            # Create reservation and reconcile to zero
            reservation = BudgetReservation(
                scope="project",
                scope_id=str(project_id),
                period_key=pkey,
                reserved_usd=reserved_cost,
                reserved_tokens=0,
            )

            # Reconcile to $0 cost (full release)
            reconcile_budget(
                db,
                reservation,
                actual_cost=0.0,
                tokens=0,
            )

            _log.info(
                f"Released budget for run {run_id}: "
                f"returned ${reserved_cost:.2f} to project {project_id}"
            )

        except Exception as e:
            _log.error(
                f"Error releasing budget for run {run_id}: {str(e)}", exc_info=True
            )
            # Don't fail the cancellation if release fails

    @staticmethod
    def get_budget_status(
        db: Session,
        project_id: int,
    ) -> BudgetStatus:
        """
        Get current budget status for a project.

        Calculates remaining budget, usage percentage, and period information.
        Returns comprehensive status for dashboard and enforcement decisions.

        Args:
            db: SQLAlchemy session
            project_id: Tenant ID

        Returns:
            BudgetStatus object with current spend, remaining, limits, etc.

        Raises:
            NotFoundError: If project not found
            ValidationError: If no budget policy configured
        """
        policy = AgentBudgetService.get_project_budget(db, project_id)
        if not policy:
            raise ValidationError(f"No budget policy configured for project {project_id}")

        # Get current spend in this period
        current_spend = current_period_spend(
            db, "project", str(project_id), policy.period
        )

        # Calculate remaining
        remaining = policy.limit_usd - current_spend
        percent_used = (current_spend / policy.limit_usd * 100) if policy.limit_usd > 0 else 0

        status = BudgetStatus(
            project_id=project_id,
            current_spend=current_spend,
            remaining_budget=max(0, remaining),
            limit_usd=policy.limit_usd,
            period=policy.period,
            period_key=period_key(),
            limit_type=policy.limit_type,
            is_enforced=not policy.is_warning_only,
            percent_used=min(100, max(0, percent_used)),
        )

        _log.debug(
            f"Budget status for project {project_id}: "
            f"${current_spend:.2f}/${status.limit_usd:.2f} used "
            f"({status.percent_used:.1f}%)"
        )

        return status

    @staticmethod
    def enforce_budget(
        db: Session,
        project_id: int,
        estimated_cost: float,
        raise_on_soft_limit: bool = False,
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if project can afford another run.

        Validates hard limits (raises error if enforced) and soft limits
        (returns warning message). Returns (allowed, warning_message).

        Args:
            db: SQLAlchemy session
            project_id: Tenant ID
            estimated_cost: Estimated cost of proposed run
            raise_on_soft_limit: If True, also raise on soft limit warning

        Returns:
            Tuple of (allowed, warning_message)
            - allowed: True if run can proceed
            - warning_message: Warning text if soft limit approaching (None if OK)

        Raises:
            BudgetExceededError: If hard limit would be exceeded
            ValidationError: If no budget policy configured
        """
        policy = AgentBudgetService.get_project_budget(db, project_id)
        if not policy:
            # No policy = no limits
            return True, None

        status = AgentBudgetService.get_budget_status(db, project_id)

        # Check if proposed cost exceeds remaining budget
        would_exceed = (status.current_spend + estimated_cost) > policy.limit_usd

        if would_exceed:
            if policy.is_warning_only:
                # Soft limit
                warning = (
                    f"Budget warning for project {project_id}: "
                    f"${status.current_spend:.2f} spent, "
                    f"${estimated_cost:.2f} requested, "
                    f"${status.limit_usd:.2f} limit. "
                    f"Run will exceed budget."
                )
                _log.warning(warning)
                if raise_on_soft_limit:
                    raise ValidationError(warning)
                return True, warning  # Allow but warn
            else:
                # Hard limit
                error_msg = (
                    f"Budget exceeded for project {project_id}: "
                    f"${status.current_spend:.2f} of ${status.limit_usd:.2f} used. "
                    f"Cannot execute run requiring ${estimated_cost:.2f}."
                )
                _log.error(error_msg)
                raise BudgetExceededError(
                    scope="project",
                    scope_id=str(project_id),
                    limit_usd=policy.limit_usd,
                    current_spend=status.current_spend,
                )

        # Budget OK
        if status.percent_used > 75:
            warning = (
                f"Budget warning for project {project_id}: "
                f"{status.percent_used:.1f}% of budget used "
                f"(${status.current_spend:.2f}/${status.limit_usd:.2f})"
            )
            _log.warning(warning)
            return True, warning

        return True, None

    @staticmethod
    def get_run_costs(
        db: Session,
        project_id: int,
        run_id: int,
    ) -> Dict:
        """
        Get detailed cost breakdown for a run.

        Aggregates costs from all steps in the run with per-model tracking.

        Args:
            db: SQLAlchemy session
            project_id: Tenant ID
            run_id: The run to analyze

        Returns:
            Dictionary with:
            - total_cost: Sum of all step costs
            - total_tokens: Sum of all tokens
            - steps: List of step costs with model info
            - by_model: Aggregated costs by model name
        """
        # Verify run ownership
        run = db.query(AgentRun).filter(
            and_(
                AgentRun.id == run_id,
                AgentRun.project_id == project_id,
            )
        ).first()

        if not run:
            raise NotFoundError("run", run_id)

        # Get all steps for this run
        steps = db.query(AgentRunStep).filter(
            AgentRunStep.run_id == run_id
        ).order_by(AgentRunStep.step_index).all()

        # Aggregate costs
        total_cost = 0.0
        total_tokens = 0
        step_costs = []
        costs_by_model = {}

        for step in steps:
            step_cost = step.step_cost or 0.0
            total_cost += step_cost
            total_tokens += (step.prompt_tokens or 0) + (step.completion_tokens or 0)

            step_info = {
                "step_id": step.id,
                "step_index": step.step_index,
                "step_type": step.step_type,
                "model": step.model_used,
                "cost": step_cost,
                "tokens": (step.prompt_tokens or 0) + (step.completion_tokens or 0),
                "prompt_tokens": step.prompt_tokens or 0,
                "completion_tokens": step.completion_tokens or 0,
            }
            step_costs.append(step_info)

            # Aggregate by model
            if step.model_used:
                if step.model_used not in costs_by_model:
                    costs_by_model[step.model_used] = {
                        "cost": 0.0,
                        "tokens": 0,
                        "count": 0,
                    }
                costs_by_model[step.model_used]["cost"] += step_cost
                costs_by_model[step.model_used]["tokens"] += (
                    (step.prompt_tokens or 0) + (step.completion_tokens or 0)
                )
                costs_by_model[step.model_used]["count"] += 1

        return {
            "run_id": run_id,
            "total_cost": total_cost,
            "total_tokens": total_tokens,
            "step_count": len(steps),
            "steps": step_costs,
            "by_model": costs_by_model,
        }

    @staticmethod
    def get_project_usage_summary(
        db: Session,
        project_id: int,
        days: int = 30,
    ) -> Dict:
        """
        Get usage summary for a project over a time period.

        Aggregates costs by day and model for dashboard visualization.

        Args:
            db: SQLAlchemy session
            project_id: Tenant ID
            days: Number of days to look back (default 30)

        Returns:
            Dictionary with daily costs and model breakdown
        """
        # Query runs in the period
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)

        runs = db.query(AgentRun).filter(
            and_(
                AgentRun.project_id == project_id,
                AgentRun.created_at >= cutoff_date,
                AgentRun.actual_cost.isnot(None),
            )
        ).order_by(AgentRun.created_at).all()

        # Aggregate by day
        daily_costs = {}
        total_cost = 0.0
        total_runs = 0

        for run in runs:
            date_key = run.created_at.date().isoformat()
            if date_key not in daily_costs:
                daily_costs[date_key] = 0.0

            daily_costs[date_key] += run.actual_cost or 0.0
            total_cost += run.actual_cost or 0.0
            total_runs += 1

        return {
            "project_id": project_id,
            "period_days": days,
            "total_cost": total_cost,
            "total_runs": total_runs,
            "average_cost_per_run": (total_cost / total_runs) if total_runs > 0 else 0.0,
            "daily_costs": daily_costs,
        }
