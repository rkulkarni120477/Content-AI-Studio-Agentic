"""Agent Run Lifecycle Service — Stage 1 of Phase 2

Manages the lifecycle of agent execution runs:
- Create new runs with idempotency (request_id)
- Transition run states (queued → running → completed/failed)
- Handle timeouts and cancellation
- Persist run metadata and progress

Patterns:
- Uses SQLAlchemy ORM with atomic operations
- Integrates with budget_service for cost reservation
- Implements SQLite-safe state transitions (no SELECT FOR UPDATE)
- Enforces tenant isolation via project_id
- Follows existing error handling (raise PermissionDeniedError, NotFoundError)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Dict, Optional

from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.core.exceptions import NotFoundError, PermissionDeniedError, ValidationError, WorkflowError
from app.core.permissions import rbac_check
from promptops_app.agents_models import AgentRun, AgentDefinition, AgentDefinitionVersion
from promptops_app.services.agents_tenant_service import AgentsTenantService
from promptops_app.services.budget_service import check_budget, reconcile_budget, BudgetExceededError, period_key
from promptops_app.services.usage_service import estimate_cost

_log = logging.getLogger(__name__)


class AgentRunService:
    """Service for managing agent run lifecycle and state transitions."""

    @staticmethod
    def create_run(
        db: Session,
        project_id: int,
        definition_id: int,
        initiated_by: str,
        initiated_by_role: str,
        artifact_id: Optional[int] = None,
        artifact_type: Optional[str] = None,
        input_content: str = "",
        input_context: Optional[Dict] = None,
        request_id: Optional[str] = None,
        estimated_cost: Optional[float] = None,
    ) -> AgentRun:
        """
        Create a new run with idempotency support.

        Idempotency: If a run with this request_id already exists, return it.
        This allows safe replay of create requests without creating duplicates.

        Args:
            db: SQLAlchemy session
            project_id: Tenant ID (scoping)
            definition_id: Agent definition to run
            initiated_by: Username of requester
            initiated_by_role: Role of requester (admin|reviewer|author)
            artifact_id: Optional target artifact ID
            artifact_type: Optional target artifact type (block|course|module)
            input_content: Content being processed
            input_context: Additional context as dict
            request_id: Optional idempotency key
            estimated_cost: Estimated USD cost (for budget reservation)

        Returns:
            AgentRun: Newly created or existing run record

        Raises:
            NotFoundError: Agent not found or doesn't belong to tenant
            PermissionDeniedError: User lacks agents.run permission
            ValidationError: Agent not in active state
            BudgetExceededError: Insufficient budget to reserve estimated cost
        """
        # 1. Check idempotency
        if request_id:
            existing = db.query(AgentRun).filter(
                AgentRun.request_id == request_id
            ).first()
            if existing:
                _log.info(f"Idempotent replay: returning existing run {existing.id} for request {request_id}")
                return existing

        # 2. Validate authorization
        if not rbac_check(initiated_by_role, "agents.run"):
            raise PermissionDeniedError("agents.run", user_role=initiated_by_role)

        # 3. Validate agent ownership and state
        agent = AgentsTenantService.validate_agent_ownership(db, definition_id, project_id)

        if agent.lifecycle_state != "active":
            raise ValidationError(
                f"Agent {definition_id} is not active. Current state: {agent.lifecycle_state}"
            )

        # 4. Get active version
        active_version = db.query(AgentDefinitionVersion).filter(
            and_(
                AgentDefinitionVersion.definition_id == definition_id,
                AgentDefinitionVersion.is_active == True
            )
        ).first()

        if not active_version:
            raise ValidationError(
                f"Agent {definition_id} has no active version. Cannot execute."
            )

        # 5. Reserve budget if estimated_cost provided
        budget_reserved = False
        if estimated_cost and estimated_cost > 0:
            try:
                # Attempt to reserve budget
                pkey = period_key()
                from promptops_app.services.budget_service import _reserve, _get_policy

                policy = _get_policy(db, "project", project_id)
                if policy:
                    # Convert estimated_cost to tokens for worst-case reservation
                    # Using rough 1 token ≈ $0.00003 conversion (based on gpt-4o pricing)
                    estimated_tokens = int(estimated_cost / 0.00003) if estimated_cost > 0 else 0
                    budget_reserved = _reserve(
                        db,
                        scope="project",
                        scope_id=str(project_id),
                        pkey=pkey,
                        cost_usd=estimated_cost,
                        tokens=estimated_tokens,
                        policy=policy
                    )

                    if not budget_reserved and policy.limit_type == "usd":
                        from promptops_app.services.budget_service import current_period_spend
                        current_spend = current_period_spend(db, "project", project_id, "monthly")
                        raise BudgetExceededError(
                            "project", str(project_id),
                            policy.limit_usd, current_spend
                        )
                else:
                    # No budget policy exists, allow run to proceed
                    budget_reserved = False
            except BudgetExceededError:
                raise
            except Exception as e:
                _log.warning(f"Budget reservation failed (proceeding anyway): {e}")
                budget_reserved = False

        # 6. Create run record
        import json
        run = AgentRun(
            project_id=project_id,
            definition_id=definition_id,
            version_id=active_version.id,
            initiated_by=initiated_by,
            initiated_by_role=initiated_by_role,
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            input_content=input_content,
            input_context=json.dumps(input_context) if input_context else None,
            state="queued",
            estimated_cost=estimated_cost,
            budget_reserved=budget_reserved,
            request_id=request_id,
            created_at=datetime.utcnow(),
        )

        db.add(run)
        db.commit()
        db.refresh(run)

        _log.info(
            f"Created run {run.id} for agent {definition_id} in project {project_id}. "
            f"Estimated cost: ${estimated_cost or 0.0:.4f}, Budget reserved: {budget_reserved}"
        )
        return run

    @staticmethod
    def start_run(
        db: Session,
        run_id: int,
        project_id: int,
        timeout_seconds: int = 300,
    ) -> AgentRun:
        """
        Transition run from queued → running.

        Uses SQLite-safe atomic update: checks that state is queued and updates
        in one operation. If rows_affected != 1, the claim failed (already started).

        Args:
            db: SQLAlchemy session
            run_id: Run ID to start
            project_id: Tenant ID (for scoping)
            timeout_seconds: How long before run expires if awaiting input

        Returns:
            AgentRun: Updated run record in running state

        Raises:
            NotFoundError: Run not found or doesn't belong to tenant
            WorkflowError: Run is not in queued state
        """
        # 1. Validate ownership
        run = AgentsTenantService.validate_run_ownership(db, run_id, project_id)

        # 2. Check state (must be queued)
        if run.state != "queued":
            raise WorkflowError(
                f"Cannot start run {run_id}. Current state: {run.state}. "
                f"Expected: queued"
            )

        # 3. Atomic state transition (SQLite-safe)
        # Use update with WHERE clause to ensure atomicity
        stmt = db.query(AgentRun).filter(
            and_(
                AgentRun.id == run_id,
                AgentRun.state == "queued"
            )
        )

        # Perform atomic update
        rows_updated = stmt.update({
            AgentRun.state: "running",
            AgentRun.started_at: datetime.utcnow(),
            AgentRun.expires_at: datetime.utcnow() + timedelta(seconds=timeout_seconds),
        })

        db.commit()

        if rows_updated != 1:
            # Another worker claimed this run, or it's no longer queued
            db.refresh(run)
            raise WorkflowError(
                f"Failed to claim run {run_id}. State: {run.state}. "
                f"Another worker may have started it."
            )

        # 4. Refresh and return updated record
        db.refresh(run)
        _log.info(f"Started run {run_id}. Will expire at {run.expires_at}")
        return run

    @staticmethod
    def complete_run(
        db: Session,
        run_id: int,
        project_id: int,
        result: Optional[Dict] = None,
        actual_cost: Optional[float] = None,
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
    ) -> AgentRun:
        """
        Transition run → completed with result persistence.

        Args:
            db: SQLAlchemy session
            run_id: Run ID to complete
            project_id: Tenant ID (for scoping)
            result: Final result as dict (will be JSON-serialized)
            actual_cost: Actual cost in USD (for budget reconciliation)
            prompt_tokens: Actual prompt tokens used
            completion_tokens: Actual completion tokens generated

        Returns:
            AgentRun: Completed run record

        Raises:
            NotFoundError: Run not found or doesn't belong to tenant
            WorkflowError: Run is not in running/awaiting_input state
        """
        # 1. Validate ownership
        run = AgentsTenantService.validate_run_ownership(db, run_id, project_id)

        # 2. Check state (must be running or awaiting_input)
        if run.state not in ["running", "awaiting_input"]:
            raise WorkflowError(
                f"Cannot complete run {run_id}. Current state: {run.state}. "
                f"Expected: running or awaiting_input"
            )

        # 3. Persist result and update metrics
        import json
        from datetime import datetime

        completed_at = datetime.utcnow()
        execution_time_ms = None
        if run.started_at:
            execution_time_ms = int((completed_at - run.started_at).total_seconds() * 1000)

        # 4. Perform atomic update
        stmt = db.query(AgentRun).filter(
            and_(
                AgentRun.id == run_id,
                AgentRun.state.in_(["running", "awaiting_input"])
            )
        )

        rows_updated = stmt.update({
            AgentRun.state: "completed",
            AgentRun.result: json.dumps(result) if result else None,
            AgentRun.actual_cost: actual_cost,
            AgentRun.completed_at: completed_at,
            AgentRun.execution_time_ms: execution_time_ms,
        })

        db.commit()

        if rows_updated != 1:
            db.refresh(run)
            raise WorkflowError(
                f"Failed to complete run {run_id}. State: {run.state}. "
                f"Another worker may have modified it."
            )

        # 5. Reconcile budget if we reserved it
        if run.budget_reserved and actual_cost is not None:
            try:
                from promptops_app.services.budget_service import reconcile_budget, BudgetReservation, period_key

                reservation = BudgetReservation(
                    scope="project",
                    scope_id=str(project_id),
                    period_key=period_key(),
                    reserved_usd=run.estimated_cost or 0.0,
                    reserved_tokens=int((run.estimated_cost or 0.0) / 0.00003) if run.estimated_cost else 0
                )

                reconcile_budget(db, reservation, actual_cost, completion_tokens or 0)
                _log.info(
                    f"Reconciled budget for run {run_id}. "
                    f"Reserved: ${run.estimated_cost:.4f}, Actual: ${actual_cost:.4f}"
                )
            except Exception as e:
                _log.error(f"Budget reconciliation failed for run {run_id}: {e}")

        # 6. Refresh and return
        db.refresh(run)
        _log.info(
            f"Completed run {run_id}. Cost: ${actual_cost or 0.0:.4f}, "
            f"Execution time: {execution_time_ms}ms"
        )
        return run

    @staticmethod
    def fail_run(
        db: Session,
        run_id: int,
        project_id: int,
        error_message: str,
        error_traceback: Optional[str] = None,
    ) -> AgentRun:
        """
        Transition run → failed with error details.

        Releases budget reservation if the run hasn't completed yet.

        Args:
            db: SQLAlchemy session
            run_id: Run ID to fail
            project_id: Tenant ID (for scoping)
            error_message: User-facing error message
            error_traceback: Optional stack trace for debugging

        Returns:
            AgentRun: Failed run record

        Raises:
            NotFoundError: Run not found or doesn't belong to tenant
        """
        # 1. Validate ownership
        run = AgentsTenantService.validate_run_ownership(db, run_id, project_id)

        # 2. Atomic update to failed state
        from datetime import datetime

        completed_at = datetime.utcnow()
        execution_time_ms = None
        if run.started_at:
            execution_time_ms = int((completed_at - run.started_at).total_seconds() * 1000)

        stmt = db.query(AgentRun).filter(
            and_(
                AgentRun.id == run_id,
                AgentRun.state.in_(["queued", "running", "awaiting_input"])
            )
        )

        rows_updated = stmt.update({
            AgentRun.state: "failed",
            AgentRun.error_message: error_message,
            AgentRun.error_traceback: error_traceback,
            AgentRun.completed_at: completed_at,
            AgentRun.execution_time_ms: execution_time_ms,
        })

        db.commit()

        # 3. Release budget reservation if reserved
        if run.budget_reserved and run.estimated_cost:
            try:
                from promptops_app.services.budget_service import _reserve_unconditional, period_key

                # Record the reversal as a negative adjustment
                _reserve_unconditional(
                    db,
                    scope="project",
                    scope_id=str(project_id),
                    pkey=period_key(),
                    cost_usd=-(run.estimated_cost),  # Negative to release
                    tokens=0
                )
                _log.info(
                    f"Released budget reservation for failed run {run_id}. "
                    f"Amount: ${run.estimated_cost:.4f}"
                )
            except Exception as e:
                _log.error(f"Failed to release budget for run {run_id}: {e}")

        # 4. Refresh and return
        db.refresh(run)
        _log.error(
            f"Failed run {run_id}. Error: {error_message}. "
            f"Execution time: {execution_time_ms}ms"
        )
        return run

    @staticmethod
    def cancel_run(
        db: Session,
        run_id: int,
        project_id: int,
        initiated_by: str,
        initiated_by_role: str,
    ) -> AgentRun:
        """
        Cancel an in-progress run.

        Only the run's initiator or an admin can cancel it.
        Releases budget reservation.

        Args:
            db: SQLAlchemy session
            run_id: Run ID to cancel
            project_id: Tenant ID (for scoping)
            initiated_by: Username of requester
            initiated_by_role: Role of requester

        Returns:
            AgentRun: Cancelled run record

        Raises:
            NotFoundError: Run not found or doesn't belong to tenant
            PermissionDeniedError: User is not run creator and is not admin
            WorkflowError: Run is not in a cancellable state
        """
        # 1. Validate ownership
        run = AgentsTenantService.validate_run_ownership(db, run_id, project_id)

        # 2. Check authorization (run creator or admin can cancel)
        is_creator = run.initiated_by == initiated_by
        is_admin = initiated_by_role == "admin"

        if not (is_creator or is_admin):
            raise PermissionDeniedError(
                "agents.run",
                user_role=initiated_by_role
            )

        # 3. Check state (can only cancel queued/running/awaiting_input)
        if run.state not in ["queued", "running", "awaiting_input"]:
            raise WorkflowError(
                f"Cannot cancel run {run_id}. Current state: {run.state}. "
                f"Can only cancel: queued, running, awaiting_input"
            )

        # 4. Atomic state transition
        from datetime import datetime

        completed_at = datetime.utcnow()
        execution_time_ms = None
        if run.started_at:
            execution_time_ms = int((completed_at - run.started_at).total_seconds() * 1000)

        stmt = db.query(AgentRun).filter(
            and_(
                AgentRun.id == run_id,
                AgentRun.state.in_(["queued", "running", "awaiting_input"])
            )
        )

        rows_updated = stmt.update({
            AgentRun.state: "cancelled",
            AgentRun.state_reason: f"Cancelled by {initiated_by}",
            AgentRun.completed_at: completed_at,
            AgentRun.execution_time_ms: execution_time_ms,
        })

        db.commit()

        if rows_updated != 1:
            db.refresh(run)
            raise WorkflowError(
                f"Failed to cancel run {run_id}. State: {run.state}. "
                f"Another worker may have modified it."
            )

        # 5. Release budget reservation
        if run.budget_reserved and run.estimated_cost:
            try:
                from promptops_app.services.budget_service import _reserve_unconditional, period_key

                # Record the reversal
                _reserve_unconditional(
                    db,
                    scope="project",
                    scope_id=str(project_id),
                    pkey=period_key(),
                    cost_usd=-(run.estimated_cost),  # Negative to release
                    tokens=0
                )
                _log.info(
                    f"Released budget reservation for cancelled run {run_id}. "
                    f"Amount: ${run.estimated_cost:.4f}"
                )
            except Exception as e:
                _log.error(f"Failed to release budget for cancelled run {run_id}: {e}")

        # 6. Refresh and return
        db.refresh(run)
        _log.info(
            f"Cancelled run {run_id} by {initiated_by}. "
            f"Execution time: {execution_time_ms}ms"
        )
        return run

    @staticmethod
    def get_run(
        db: Session,
        run_id: int,
        project_id: int,
    ) -> AgentRun:
        """
        Retrieve a run by ID with tenant isolation.

        Args:
            db: SQLAlchemy session
            run_id: Run ID
            project_id: Tenant ID (for scoping)

        Returns:
            AgentRun: The run record

        Raises:
            NotFoundError: Run not found or doesn't belong to tenant
        """
        return AgentsTenantService.validate_run_ownership(db, run_id, project_id)

    @staticmethod
    def list_runs(
        db: Session,
        project_id: int,
        limit: int = 100,
        offset: int = 0,
        state: Optional[str] = None,
    ) -> tuple[list[AgentRun], int]:
        """
        List runs for a tenant with optional filtering.

        Args:
            db: SQLAlchemy session
            project_id: Tenant ID
            limit: Max results to return
            offset: Results to skip
            state: Optional state filter (queued|running|completed|failed|cancelled)

        Returns:
            Tuple of (runs, total_count)
        """
        query = db.query(AgentRun).filter(AgentRun.project_id == project_id)

        if state:
            query = query.filter(AgentRun.state == state)

        total_count = query.count()
        runs = query.order_by(AgentRun.created_at.desc()).limit(limit).offset(offset).all()

        return runs, total_count
