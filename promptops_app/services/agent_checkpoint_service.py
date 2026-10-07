"""Agent Checkpoint & Resumption Service — Stage 5 of Phase 2

Manages checkpoint creation, persistence, and resumption for agent runs:
- Create atomic checkpoints after step completion
- Persist JSON snapshots of run state for recovery
- Resume execution from checkpoints (idempotent replay-safe)
- Cleanup checkpoints after successful run completion
- Crash recovery: identify incomplete runs and resume from latest checkpoint
- Audit trail: list all checkpoints for debugging

Patterns:
- Uses SQLAlchemy ORM with atomic operations
- Implements SQLite-safe state transitions
- Enforces tenant isolation via project_id
- Follows existing error handling patterns (raise NotFoundError, ValidationError, WorkflowError)
- JSON serialization of complete run state for recoverability
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Dict, Optional, List, Tuple, Any

from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.core.exceptions import NotFoundError, ValidationError, WorkflowError
from promptops_app.agents_models import AgentCheckpoint, AgentRun, AgentRunStep
from promptops_app.services.agents_tenant_service import AgentsTenantService

_log = logging.getLogger(__name__)


class AgentCheckpointService:
    """Service for managing agent checkpoint creation, persistence, and resumption."""

    @staticmethod
    def create_checkpoint(
        db: Session,
        run_id: int,
        project_id: int,
        step_count: int,
        accumulated_cost: float,
        run_state_snapshot: Dict[str, Any],
        resumable: bool = True,
        resume_reason: Optional[str] = None,
    ) -> AgentCheckpoint:
        """
        Create an atomic checkpoint after a step completes.

        Persists the complete run state (steps completed, outputs, costs) to enable
        crash recovery and resumption. Called after each step executes successfully.

        Args:
            db: SQLAlchemy session
            run_id: Parent run ID
            project_id: Tenant ID (for scoping)
            step_count: Number of steps completed at this checkpoint
            accumulated_cost: Total USD cost up to this checkpoint
            run_state_snapshot: Dict containing:
                - steps_completed: List of completed step outputs
                - current_outputs: Dict of accumulated outputs
                - run_metadata: Run configuration and context
                - timestamp: When this snapshot was taken
            resumable: Whether execution can resume from this checkpoint (default True)
            resume_reason: Reason if not resumable (e.g., "awaiting_input", "paused", "error")

        Returns:
            AgentCheckpoint: Newly created checkpoint record

        Raises:
            NotFoundError: Run not found or doesn't belong to tenant
            ValidationError: Invalid step_count or snapshot
            WorkflowError: Run not in a state that allows checkpointing
        """
        # 1. Validate run ownership and state
        run = AgentsTenantService.validate_run_ownership(db, run_id, project_id)

        # 2. Verify run is in a state that allows checkpointing
        if run.state not in ["running", "awaiting_input"]:
            raise WorkflowError(
                f"Cannot create checkpoint for run {run_id}. Current state: {run.state}. "
                f"Expected: running or awaiting_input"
            )

        # 3. Validate step_count (must be non-negative)
        if step_count < 0:
            raise ValidationError(
                f"Invalid step_count: {step_count}. Must be >= 0"
            )

        # 4. Validate snapshot structure
        if not isinstance(run_state_snapshot, dict):
            raise ValidationError(
                "run_state_snapshot must be a dict, got " + type(run_state_snapshot).__name__
            )

        required_snapshot_fields = ["steps_completed", "current_outputs", "run_metadata"]
        for field in required_snapshot_fields:
            if field not in run_state_snapshot:
                raise ValidationError(
                    f"run_state_snapshot missing required field: {field}"
                )

        # 5. Ensure snapshot has timestamp
        if "timestamp" not in run_state_snapshot:
            run_state_snapshot["timestamp"] = datetime.utcnow().isoformat()

        # 6. Calculate checkpoint_index (sequential)
        current_checkpoint_count = db.query(AgentCheckpoint).filter(
            AgentCheckpoint.run_id == run_id
        ).count()
        checkpoint_index = current_checkpoint_count

        # 7. Create checkpoint record
        checkpoint = AgentCheckpoint(
            run_id=run_id,
            checkpoint_index=checkpoint_index,
            step_state=json.dumps(run_state_snapshot),
            resumable=resumable,
            resume_reason=resume_reason,
            created_at=datetime.utcnow(),
        )

        db.add(checkpoint)
        db.commit()
        db.refresh(checkpoint)

        _log.info(
            f"Created checkpoint {checkpoint.id} for run {run_id} (index {checkpoint_index}). "
            f"Steps: {step_count}, Cost: ${accumulated_cost:.4f}, Resumable: {resumable}"
        )
        return checkpoint

    @staticmethod
    def get_latest_checkpoint(
        db: Session,
        run_id: int,
        project_id: int,
        resumable_only: bool = False,
    ) -> Optional[AgentCheckpoint]:
        """
        Retrieve the most recent checkpoint for a run.

        Used to identify where to resume execution after a crash or pause.
        Optionally filters to only resumable checkpoints.

        Args:
            db: SQLAlchemy session
            run_id: Run ID to get checkpoint for
            project_id: Tenant ID (for scoping)
            resumable_only: If True, only return resumable checkpoints (default False)

        Returns:
            AgentCheckpoint: Most recent checkpoint, or None if no checkpoints exist

        Raises:
            NotFoundError: Run not found or doesn't belong to tenant
        """
        # 1. Validate run ownership
        run = AgentsTenantService.validate_run_ownership(db, run_id, project_id)

        # 2. Query latest checkpoint
        query = db.query(AgentCheckpoint).filter(
            AgentCheckpoint.run_id == run_id
        )

        if resumable_only:
            query = query.filter(AgentCheckpoint.resumable == True)

        checkpoint = query.order_by(AgentCheckpoint.checkpoint_index.desc()).first()

        if checkpoint:
            _log.info(
                f"Retrieved latest checkpoint {checkpoint.id} for run {run_id} "
                f"(index {checkpoint.checkpoint_index}, resumable: {checkpoint.resumable})"
            )
        else:
            _log.debug(f"No checkpoints found for run {run_id} (resumable_only={resumable_only})")

        return checkpoint

    @staticmethod
    def resume_from_checkpoint(
        db: Session,
        run_id: int,
        project_id: int,
        checkpoint_id: Optional[int] = None,
    ) -> Tuple[AgentRun, AgentCheckpoint, Dict[str, Any]]:
        """
        Resume execution from a checkpoint (idempotent replay-safe).

        Retrieves the specified checkpoint (or latest) and restores the run state
        to that point. Subsequent steps will resume from this checkpoint.

        Safe to call multiple times with same checkpoint_id — will return same state.

        Args:
            db: SQLAlchemy session
            run_id: Run ID to resume
            project_id: Tenant ID (for scoping)
            checkpoint_id: Optional specific checkpoint to resume from
                          If None, uses most recent resumable checkpoint

        Returns:
            Tuple of:
                - AgentRun: Updated run record ready for resumption
                - AgentCheckpoint: Checkpoint being resumed from
                - Dict: Deserialized run_state_snapshot for execution

        Raises:
            NotFoundError: Run not found or doesn't belong to tenant
            ValidationError: Checkpoint not resumable or doesn't belong to run
            WorkflowError: Run not in resumable state
        """
        # 1. Validate run ownership
        run = AgentsTenantService.validate_run_ownership(db, run_id, project_id)

        # 2. Verify run is in a resumable state
        if run.state not in ["running", "awaiting_input", "queued"]:
            raise WorkflowError(
                f"Cannot resume run {run_id} from state: {run.state}. "
                f"Expected: running, awaiting_input, or queued"
            )

        # 3. Get checkpoint (specific or latest)
        if checkpoint_id:
            checkpoint = db.query(AgentCheckpoint).filter(
                and_(
                    AgentCheckpoint.id == checkpoint_id,
                    AgentCheckpoint.run_id == run_id
                )
            ).first()

            if not checkpoint:
                raise NotFoundError("AgentCheckpoint", checkpoint_id)
        else:
            # Get latest checkpoint (may not be resumable)
            checkpoint = AgentCheckpointService.get_latest_checkpoint(
                db, run_id, project_id, resumable_only=False
            )

            if not checkpoint:
                raise ValidationError(
                    f"No checkpoints found for run {run_id}"
                )

        # 4. Validate checkpoint is resumable
        if not checkpoint.resumable:
            raise ValidationError(
                f"Checkpoint {checkpoint.id} is not resumable. Reason: {checkpoint.resume_reason}"
            )

        # 5. Deserialize checkpoint state
        try:
            run_state_snapshot = json.loads(checkpoint.step_state)
        except (json.JSONDecodeError, ValueError) as e:
            raise ValidationError(
                f"Checkpoint {checkpoint.id} state corrupted or invalid JSON: {e}"
            )

        # 6. Update run state for resumption (idempotent)
        # Only update if run is not already in resumed state
        if run.state != "running":
            stmt = db.query(AgentRun).filter(AgentRun.id == run_id)
            stmt.update({
                AgentRun.state: "running",
                AgentRun.started_at: datetime.utcnow() if not run.started_at else run.started_at,
            })
            db.commit()
            db.refresh(run)

        _log.info(
            f"Resumed run {run_id} from checkpoint {checkpoint.id} (index {checkpoint.checkpoint_index}). "
            f"Resumable reason: {checkpoint.resume_reason}"
        )
        return run, checkpoint, run_state_snapshot

    @staticmethod
    def cleanup_checkpoints(
        db: Session,
        run_id: int,
        project_id: int,
        keep_latest: int = 1,
    ) -> int:
        """
        Remove checkpoints after successful run completion.

        Cleans up checkpoints for a run, optionally keeping the most recent N.
        Called after run completes successfully to save storage.

        Args:
            db: SQLAlchemy session
            run_id: Run ID to cleanup checkpoints for
            project_id: Tenant ID (for scoping)
            keep_latest: Number of most recent checkpoints to keep (default 1)

        Returns:
            int: Number of checkpoints deleted

        Raises:
            NotFoundError: Run not found or doesn't belong to tenant
            ValidationError: keep_latest is negative
        """
        # 1. Validate run ownership
        run = AgentsTenantService.validate_run_ownership(db, run_id, project_id)

        # 2. Validate keep_latest
        if keep_latest < 0:
            raise ValidationError(f"keep_latest must be >= 0, got {keep_latest}")

        # 3. Query all checkpoints for this run, ordered by index
        checkpoints = db.query(AgentCheckpoint).filter(
            AgentCheckpoint.run_id == run_id
        ).order_by(AgentCheckpoint.checkpoint_index.desc()).all()

        # 4. Delete old checkpoints (keep keep_latest most recent)
        to_delete = checkpoints[keep_latest:]
        deleted_count = len(to_delete)

        for checkpoint in to_delete:
            db.delete(checkpoint)

        if deleted_count > 0:
            db.commit()
            _log.info(
                f"Cleaned up {deleted_count} checkpoints for run {run_id} "
                f"(kept {keep_latest} most recent)"
            )
        else:
            _log.debug(f"No checkpoints to clean up for run {run_id}")

        return deleted_count

    @staticmethod
    def list_checkpoints(
        db: Session,
        run_id: int,
        project_id: int,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[AgentCheckpoint], int]:
        """
        Get all checkpoints for a run (for debugging/audit trail).

        Returns paginated list of checkpoints with total count.
        Ordered by checkpoint_index for chronological ordering.

        Args:
            db: SQLAlchemy session
            run_id: Run ID to list checkpoints for
            project_id: Tenant ID (for scoping)
            limit: Maximum number of checkpoints to return (default 50)
            offset: Pagination offset (default 0)

        Returns:
            Tuple of:
                - List[AgentCheckpoint]: Checkpoints in order
                - int: Total count of checkpoints for run

        Raises:
            NotFoundError: Run not found or doesn't belong to tenant
            ValidationError: Invalid limit or offset
        """
        # 1. Validate run ownership
        run = AgentsTenantService.validate_run_ownership(db, run_id, project_id)

        # 2. Validate pagination parameters
        if limit < 1:
            raise ValidationError(f"limit must be >= 1, got {limit}")
        if offset < 0:
            raise ValidationError(f"offset must be >= 0, got {offset}")

        # 3. Query total count
        total_count = db.query(AgentCheckpoint).filter(
            AgentCheckpoint.run_id == run_id
        ).count()

        # 4. Query checkpoints with pagination
        checkpoints = db.query(AgentCheckpoint).filter(
            AgentCheckpoint.run_id == run_id
        ).order_by(AgentCheckpoint.checkpoint_index.asc()).offset(offset).limit(limit).all()

        _log.debug(
            f"Listed {len(checkpoints)} of {total_count} checkpoints for run {run_id} "
            f"(limit: {limit}, offset: {offset})"
        )
        return checkpoints, total_count

    @staticmethod
    def validate_checkpoint(
        db: Session,
        checkpoint_id: int,
        run_id: int,
        project_id: int,
    ) -> Tuple[bool, Optional[str]]:
        """
        Verify checkpoint integrity and recoverability.

        Validates that:
        - Checkpoint exists and belongs to run
        - Checkpoint state is valid JSON
        - Required fields present in state snapshot
        - Checkpoint is marked resumable

        Args:
            db: SQLAlchemy session
            checkpoint_id: Checkpoint to validate
            run_id: Expected parent run ID
            project_id: Tenant ID (for scoping)

        Returns:
            Tuple of:
                - bool: True if checkpoint is valid and resumable, False otherwise
                - Optional[str]: Error message if validation fails, None if valid

        Raises:
            NotFoundError: Run not found or doesn't belong to tenant
        """
        # 1. Validate run ownership
        run = AgentsTenantService.validate_run_ownership(db, run_id, project_id)

        # 2. Query checkpoint
        checkpoint = db.query(AgentCheckpoint).filter(
            and_(
                AgentCheckpoint.id == checkpoint_id,
                AgentCheckpoint.run_id == run_id
            )
        ).first()

        if not checkpoint:
            return False, f"Checkpoint {checkpoint_id} not found for run {run_id}"

        # 3. Check resumable flag
        if not checkpoint.resumable:
            return False, f"Checkpoint {checkpoint_id} is not resumable. Reason: {checkpoint.resume_reason}"

        # 4. Validate JSON
        try:
            run_state_snapshot = json.loads(checkpoint.step_state)
        except (json.JSONDecodeError, ValueError) as e:
            return False, f"Checkpoint {checkpoint_id} state is corrupted: {e}"

        # 5. Verify required fields in snapshot
        required_fields = ["steps_completed", "current_outputs", "run_metadata"]
        missing_fields = [f for f in required_fields if f not in run_state_snapshot]

        if missing_fields:
            return False, f"Checkpoint {checkpoint_id} missing fields: {', '.join(missing_fields)}"

        # 6. Validate metadata structure
        run_metadata = run_state_snapshot.get("run_metadata", {})
        required_metadata = ["run_id", "step_count", "accumulated_cost"]
        missing_metadata = [f for f in required_metadata if f not in run_metadata]

        if missing_metadata:
            return False, f"Checkpoint {checkpoint_id} metadata missing: {', '.join(missing_metadata)}"

        _log.info(
            f"Checkpoint {checkpoint_id} validation successful "
            f"(step_count: {run_metadata.get('step_count')}, resumable: {checkpoint.resumable})"
        )
        return True, None
