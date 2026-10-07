"""Agent Step Execution Service — Stage 2 of Phase 2

Manages the lifecycle of individual steps within an agent run:
- Create and initialize steps
- Execute steps (model calls, retrievals, validations)
- Retry failed steps with exponential backoff
- Track token usage and cost per step
- Persist step state and outputs

Patterns:
- Uses SQLAlchemy ORM with atomic operations
- Implements exponential backoff for retries (50ms → 500ms → 5s)
- Tracks token usage and cost per step
- Stores input_data and output_data as JSON
- Follows SQLite-safe state transitions
- Enforces tenant isolation via run ownership
- Follows existing error handling patterns
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, Optional, Any, Tuple

from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.core.exceptions import NotFoundError, ValidationError, WorkflowError
from promptops_app.agents_models import AgentRunStep, AgentRun
from promptops_app.services.agents_tenant_service import AgentsTenantService

_log = logging.getLogger(__name__)

# Exponential backoff configuration
DEFAULT_MAX_RETRIES = 3
INITIAL_BACKOFF_MS = 50  # 50ms
MAX_BACKOFF_MS = 5000    # 5 seconds
BACKOFF_MULTIPLIER = 10  # 50ms → 500ms → 5s


class AgentStepService:
    """Service for managing agent step execution and state transitions."""

    @staticmethod
    def create_step(
        db: Session,
        run_id: int,
        project_id: int,
        step_index: int,
        step_type: str,
        input_data: Optional[Dict] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> AgentRunStep:
        """
        Create a new step within a run.

        A step represents a single atomic operation (model call, retrieval, validation, etc)
        within the larger agent execution. Steps are created sequentially and executed
        according to the step_index order.

        Args:
            db: SQLAlchemy session
            run_id: Parent run ID
            project_id: Tenant ID (for scoping validation)
            step_index: 0-based position in run (sequential)
            step_type: Type of step (model_call|retrieval|validation|handoff)
            input_data: Input data for step as dict (will be JSON-serialized)
            max_retries: Maximum retry attempts for this step (default 3)

        Returns:
            AgentRunStep: Newly created step record

        Raises:
            NotFoundError: Run not found or doesn't belong to tenant
            ValidationError: Step index invalid or step_type unsupported
            WorkflowError: Run is not in a state that allows new steps
        """
        # 1. Validate run ownership and state
        run = AgentsTenantService.validate_run_ownership(db, run_id, project_id)

        # 2. Check run state (must be running or awaiting_input)
        if run.state not in ["running", "awaiting_input"]:
            raise WorkflowError(
                f"Cannot create step for run {run_id}. Current state: {run.state}. "
                f"Expected: running or awaiting_input"
            )

        # 3. Validate step_type
        valid_step_types = ["model_call", "retrieval", "validation", "handoff"]
        if step_type not in valid_step_types:
            raise ValidationError(
                f"Invalid step_type: {step_type}. "
                f"Must be one of: {', '.join(valid_step_types)}"
            )

        # 4. Validate step_index (must be sequential)
        if step_index < 0:
            raise ValidationError(f"step_index must be >= 0, got {step_index}")

        # Check that step_index matches current step count
        current_step_count = db.query(AgentRunStep).filter(
            AgentRunStep.run_id == run_id
        ).count()

        if step_index != current_step_count:
            raise ValidationError(
                f"step_index must be sequential. Expected {current_step_count}, got {step_index}"
            )

        # 5. Check max_steps limit
        if run.step_count >= run.max_steps:
            raise WorkflowError(
                f"Run {run_id} has reached max_steps limit ({run.max_steps}). "
                f"Cannot create additional steps."
            )

        # 6. Create step record
        step = AgentRunStep(
            run_id=run_id,
            step_index=step_index,
            step_type=step_type,
            status="pending",
            input_data=json.dumps(input_data) if input_data else None,
            retry_count=0,
            max_retries=max_retries,
            created_at=datetime.utcnow(),
        )

        db.add(step)
        db.commit()
        db.refresh(step)

        _log.info(
            f"Created step {step.id} (index {step_index}) in run {run_id}. "
            f"Type: {step_type}, Max retries: {max_retries}"
        )
        return step

    @staticmethod
    def execute_step(
        db: Session,
        step_id: int,
        run_id: int,
        project_id: int,
        execution_fn,
    ) -> AgentRunStep:
        """
        Execute a single step with error handling and metrics tracking.

        This is a general-purpose execution method that accepts a callable
        execution function. The function is responsible for:
        - Performing the actual work (model call, retrieval, validation, etc)
        - Returning (output_data, prompt_tokens, completion_tokens, step_cost)

        The service handles:
        - State transitions (pending → running → completed/failed)
        - Metrics persistence (tokens, cost, execution time)
        - Error capture and logging
        - Atomic updates

        Args:
            db: SQLAlchemy session
            step_id: Step ID to execute
            run_id: Parent run ID (for validation)
            project_id: Tenant ID (for scoping)
            execution_fn: Callable that performs the step execution.
                         Signature: (step_data: Dict) -> Tuple[Dict, int, int, float]
                         Returns: (output_data, prompt_tokens, completion_tokens, step_cost)

        Returns:
            AgentRunStep: Executed step with output_data and metrics

        Raises:
            NotFoundError: Step or run not found
            WorkflowError: Step not in pending or retrying state
        """
        # 1. Validate ownership and state
        step = AgentsTenantService.validate_step_ownership(db, step_id, run_id, project_id)

        if step.status not in ["pending", "retrying"]:
            raise WorkflowError(
                f"Cannot execute step {step_id}. Status: {step.status}. "
                f"Expected: pending or retrying"
            )

        # 2. Transition to running
        stmt = db.query(AgentRunStep).filter(
            and_(
                AgentRunStep.id == step_id,
                AgentRunStep.status.in_(["pending", "retrying"])
            )
        )

        rows_updated = stmt.update({
            AgentRunStep.status: "running",
            AgentRunStep.started_at: datetime.utcnow(),
        })

        db.commit()

        if rows_updated != 1:
            db.refresh(step)
            raise WorkflowError(
                f"Failed to claim step {step_id}. Status: {step.status}. "
                f"Another worker may have started it."
            )

        # 3. Execute step with error handling
        db.refresh(step)
        input_data = json.loads(step.input_data) if step.input_data else {}

        try:
            output_data, prompt_tokens, completion_tokens, step_cost = execution_fn(input_data)

            # 4. Persist results (atomic update)
            completed_at = datetime.utcnow()
            execution_time_ms = int((completed_at - step.started_at).total_seconds() * 1000)

            stmt = db.query(AgentRunStep).filter(
                and_(
                    AgentRunStep.id == step_id,
                    AgentRunStep.status == "running"
                )
            )

            rows_updated = stmt.update({
                AgentRunStep.status: "completed",
                AgentRunStep.output_data: json.dumps(output_data) if output_data else None,
                AgentRunStep.prompt_tokens: prompt_tokens,
                AgentRunStep.completion_tokens: completion_tokens,
                AgentRunStep.step_cost: step_cost,
                AgentRunStep.execution_time_ms: execution_time_ms,
                AgentRunStep.completed_at: completed_at,
            })

            db.commit()

            if rows_updated != 1:
                db.refresh(step)
                raise WorkflowError(
                    f"Failed to persist step {step_id} results. "
                    f"Another worker may have modified it."
                )

            db.refresh(step)
            _log.info(
                f"Completed step {step_id} in run {run_id}. "
                f"Type: {step.step_type}, Cost: ${step_cost:.6f}, "
                f"Tokens: {prompt_tokens} prompt + {completion_tokens} completion, "
                f"Time: {execution_time_ms}ms"
            )
            return step

        except Exception as e:
            # On execution error, mark step as failed
            _log.error(f"Step {step_id} execution failed: {e}", exc_info=True)
            error_message = str(e)

            stmt = db.query(AgentRunStep).filter(
                and_(
                    AgentRunStep.id == step_id,
                    AgentRunStep.status == "running"
                )
            )

            rows_updated = stmt.update({
                AgentRunStep.status: "failed",
                AgentRunStep.error_message: error_message,
                AgentRunStep.completed_at: datetime.utcnow(),
            })

            db.commit()

            if rows_updated != 1:
                db.refresh(step)
                raise WorkflowError(
                    f"Failed to mark step {step_id} as failed. "
                    f"Another worker may have modified it."
                )

            db.refresh(step)
            raise WorkflowError(
                f"Step {step_id} execution failed: {error_message}"
            )

    @staticmethod
    def retry_step(
        db: Session,
        step_id: int,
        run_id: int,
        project_id: int,
        execution_fn,
    ) -> AgentRunStep:
        """
        Retry a failed step with exponential backoff.

        Implements exponential backoff with jitter:
        - Attempt 1: 50ms
        - Attempt 2: 500ms
        - Attempt 3: 5000ms
        - (Maxed at 5s)

        The retry_count is checked before attempting retry. If max_retries
        exceeded, raises WorkflowError instead of retrying.

        Args:
            db: SQLAlchemy session
            step_id: Step ID to retry
            run_id: Parent run ID (for validation)
            project_id: Tenant ID (for scoping)
            execution_fn: Same execution function as execute_step()

        Returns:
            AgentRunStep: Retried step

        Raises:
            NotFoundError: Step or run not found
            ValidationError: Step not in failed state
            WorkflowError: Max retries exceeded or execution failed again
        """
        # 1. Validate ownership and state
        step = AgentsTenantService.validate_step_ownership(db, step_id, run_id, project_id)

        if step.status != "failed":
            raise ValidationError(
                f"Cannot retry step {step_id}. Status: {step.status}. "
                f"Expected: failed"
            )

        # 2. Check retry limit
        if step.retry_count >= step.max_retries:
            raise WorkflowError(
                f"Step {step_id} has exceeded max retries ({step.max_retries}). "
                f"Cannot retry further."
            )

        # 3. Calculate backoff with jitter
        # Exponential: 50ms * 10^retry_count, capped at 5s
        backoff_ms = min(
            INITIAL_BACKOFF_MS * (BACKOFF_MULTIPLIER ** step.retry_count),
            MAX_BACKOFF_MS
        )
        # Add jitter: ±10% of backoff
        import random
        jitter_ms = random.uniform(backoff_ms * 0.9, backoff_ms * 1.1)

        _log.info(
            f"Retrying step {step_id} after {jitter_ms:.0f}ms. "
            f"Attempt {step.retry_count + 1}/{step.max_retries}"
        )

        # 4. Sleep before retry
        time.sleep(jitter_ms / 1000.0)

        # 5. Transition to retrying state
        stmt = db.query(AgentRunStep).filter(
            and_(
                AgentRunStep.id == step_id,
                AgentRunStep.status == "failed"
            )
        )

        rows_updated = stmt.update({
            AgentRunStep.status: "retrying",
            AgentRunStep.retry_count: AgentRunStep.retry_count + 1,
        })

        db.commit()

        if rows_updated != 1:
            db.refresh(step)
            raise WorkflowError(
                f"Failed to mark step {step_id} for retry. "
                f"Another worker may have modified it."
            )

        # 6. Execute again (using regular execute_step logic)
        db.refresh(step)
        return AgentStepService.execute_step(db, step_id, run_id, project_id, execution_fn)

    @staticmethod
    def get_step_data(
        db: Session,
        step_id: int,
        run_id: int,
        project_id: int,
    ) -> Dict[str, Any]:
        """
        Retrieve input and output data for a step.

        Returns a dict with:
        - step_id
        - step_type
        - status
        - input_data (parsed JSON)
        - output_data (parsed JSON if available)
        - tokens (prompt_tokens, completion_tokens)
        - cost (step_cost)
        - metrics (execution_time_ms)
        - error_message (if failed)
        - retry_info (retry_count, max_retries)

        Args:
            db: SQLAlchemy session
            step_id: Step ID to retrieve
            run_id: Parent run ID (for validation)
            project_id: Tenant ID (for scoping)

        Returns:
            Dict with step input/output data and metadata

        Raises:
            NotFoundError: Step or run not found
        """
        # 1. Validate ownership
        step = AgentsTenantService.validate_step_ownership(db, step_id, run_id, project_id)

        # 2. Parse JSON fields
        input_data = json.loads(step.input_data) if step.input_data else None
        output_data = json.loads(step.output_data) if step.output_data else None

        # 3. Build result dict
        return {
            "step_id": step.id,
            "step_index": step.step_index,
            "step_type": step.step_type,
            "status": step.status,
            "input_data": input_data,
            "output_data": output_data,
            "tokens": {
                "prompt_tokens": step.prompt_tokens,
                "completion_tokens": step.completion_tokens,
            },
            "cost": {
                "step_cost": step.step_cost,
            },
            "metrics": {
                "execution_time_ms": step.execution_time_ms,
                "started_at": step.started_at.isoformat() if step.started_at else None,
                "completed_at": step.completed_at.isoformat() if step.completed_at else None,
            },
            "error_message": step.error_message,
            "retry_info": {
                "retry_count": step.retry_count,
                "max_retries": step.max_retries,
            },
        }

    @staticmethod
    def mark_step_complete(
        db: Session,
        step_id: int,
        run_id: int,
        project_id: int,
        output_data: Optional[Dict] = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        step_cost: float = 0.0,
    ) -> AgentRunStep:
        """
        Mark a step as complete (manual completion).

        This method is used when a step completes successfully but needs
        to be explicitly marked complete with output data and metrics.
        The step must be in "running" state.

        Args:
            db: SQLAlchemy session
            step_id: Step ID to mark complete
            run_id: Parent run ID (for validation)
            project_id: Tenant ID (for scoping)
            output_data: Output data dict (will be JSON-serialized)
            prompt_tokens: Number of prompt tokens used
            completion_tokens: Number of completion tokens used
            step_cost: Cost of this step in USD

        Returns:
            AgentRunStep: Updated step record

        Raises:
            NotFoundError: Step or run not found
            WorkflowError: Step is not in running state
        """
        # 1. Validate ownership
        step = AgentsTenantService.validate_step_ownership(db, step_id, run_id, project_id)

        if step.status != "running":
            raise WorkflowError(
                f"Cannot mark step {step_id} complete. Status: {step.status}. "
                f"Expected: running"
            )

        # 2. Calculate execution time
        completed_at = datetime.utcnow()
        execution_time_ms = None
        if step.started_at:
            execution_time_ms = int((completed_at - step.started_at).total_seconds() * 1000)

        # 3. Atomic update
        stmt = db.query(AgentRunStep).filter(
            and_(
                AgentRunStep.id == step_id,
                AgentRunStep.status == "running"
            )
        )

        rows_updated = stmt.update({
            AgentRunStep.status: "completed",
            AgentRunStep.output_data: json.dumps(output_data) if output_data else None,
            AgentRunStep.prompt_tokens: prompt_tokens,
            AgentRunStep.completion_tokens: completion_tokens,
            AgentRunStep.step_cost: step_cost,
            AgentRunStep.execution_time_ms: execution_time_ms,
            AgentRunStep.completed_at: completed_at,
        })

        db.commit()

        if rows_updated != 1:
            db.refresh(step)
            raise WorkflowError(
                f"Failed to mark step {step_id} complete. "
                f"Another worker may have modified it."
            )

        db.refresh(step)
        _log.info(
            f"Marked step {step_id} complete. "
            f"Cost: ${step_cost:.6f}, Tokens: {prompt_tokens}+{completion_tokens}"
        )
        return step

    @staticmethod
    def mark_step_failed(
        db: Session,
        step_id: int,
        run_id: int,
        project_id: int,
        error_message: str,
        should_retry: bool = True,
    ) -> AgentRunStep:
        """
        Mark a step as failed with error details.

        If should_retry is True and max retries not exceeded, transitions to
        "failed" state (ready for retry_step call). If should_retry is False
        or max retries exceeded, immediately transitions to "failed" terminal state.

        Args:
            db: SQLAlchemy session
            step_id: Step ID to mark failed
            run_id: Parent run ID (for validation)
            project_id: Tenant ID (for scoping)
            error_message: Error message describing the failure
            should_retry: Whether this error is retriable

        Returns:
            AgentRunStep: Updated step record

        Raises:
            NotFoundError: Step or run not found
            WorkflowError: Step is not in running/retrying state
        """
        # 1. Validate ownership
        step = AgentsTenantService.validate_step_ownership(db, step_id, run_id, project_id)

        if step.status not in ["running", "retrying"]:
            raise WorkflowError(
                f"Cannot mark step {step_id} failed. Status: {step.status}. "
                f"Expected: running or retrying"
            )

        # 2. Determine if we should mark as failed vs failed-terminal
        # A step can be retried if:
        # - should_retry is True
        # - retry_count < max_retries
        # Otherwise it's terminal failure

        can_retry = should_retry and step.retry_count < step.max_retries

        # 3. Calculate execution time
        completed_at = datetime.utcnow() if not can_retry else None
        execution_time_ms = None
        if step.started_at and not can_retry:
            execution_time_ms = int((completed_at - step.started_at).total_seconds() * 1000)

        # 4. Atomic update
        if can_retry:
            # Transition to "failed" (retriable) state
            stmt = db.query(AgentRunStep).filter(
                and_(
                    AgentRunStep.id == step_id,
                    AgentRunStep.status.in_(["running", "retrying"])
                )
            )

            rows_updated = stmt.update({
                AgentRunStep.status: "failed",
                AgentRunStep.error_message: error_message,
            })

            log_msg = f"Marked step {step_id} failed (retriable). Error: {error_message}. " \
                      f"Will retry (attempt {step.retry_count + 1}/{step.max_retries})"
        else:
            # Terminal failure
            stmt = db.query(AgentRunStep).filter(
                and_(
                    AgentRunStep.id == step_id,
                    AgentRunStep.status.in_(["running", "retrying"])
                )
            )

            rows_updated = stmt.update({
                AgentRunStep.status: "failed",
                AgentRunStep.error_message: error_message,
                AgentRunStep.completed_at: completed_at,
                AgentRunStep.execution_time_ms: execution_time_ms,
            })

            log_msg = f"Marked step {step_id} failed (terminal). Error: {error_message}. " \
                      f"Max retries exceeded ({step.retry_count}/{step.max_retries})"

        db.commit()

        if rows_updated != 1:
            db.refresh(step)
            raise WorkflowError(
                f"Failed to mark step {step_id} as failed. "
                f"Another worker may have modified it."
            )

        db.refresh(step)
        _log.warning(log_msg)
        return step

    @staticmethod
    def get_step(
        db: Session,
        step_id: int,
        run_id: int,
        project_id: int,
    ) -> AgentRunStep:
        """
        Retrieve a step by ID with tenant isolation.

        Args:
            db: SQLAlchemy session
            step_id: Step ID
            run_id: Parent run ID (for validation)
            project_id: Tenant ID (for scoping)

        Returns:
            AgentRunStep: The step record

        Raises:
            NotFoundError: Step or run not found
        """
        return AgentsTenantService.validate_step_ownership(db, step_id, run_id, project_id)

    @staticmethod
    def list_steps(
        db: Session,
        run_id: int,
        project_id: int,
        limit: int = 100,
        offset: int = 0,
        status: Optional[str] = None,
    ) -> tuple[list[AgentRunStep], int]:
        """
        List steps for a run with optional filtering.

        Args:
            db: SQLAlchemy session
            run_id: Parent run ID
            project_id: Tenant ID (for scoping)
            limit: Max results to return
            offset: Results to skip
            status: Optional status filter (pending|running|completed|failed|retrying)

        Returns:
            Tuple of (steps, total_count)

        Raises:
            NotFoundError: Run not found or doesn't belong to tenant
        """
        # Validate run ownership first
        AgentsTenantService.validate_run_ownership(db, run_id, project_id)

        # Build query
        query = db.query(AgentRunStep).filter(AgentRunStep.run_id == run_id)

        if status:
            query = query.filter(AgentRunStep.status == status)

        total_count = query.count()
        steps = query.order_by(AgentRunStep.step_index).limit(limit).offset(offset).all()

        return steps, total_count
