"""Unit tests for AgentCheckpointService

Tests cover:
- create_checkpoint() atomic checkpoint creation, snapshot validation
- get_latest_checkpoint() retrieval of most recent checkpoint
- resume_from_checkpoint() resumable state validation, idempotent resumption
- cleanup_checkpoints() cleanup with keep_latest option
- list_checkpoints() pagination and audit trail
- validate_checkpoint() integrity checks and recoverability
- Tenant isolation enforcement
- Error handling and edge cases
"""

import json
import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from sqlalchemy.orm import Session
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import NotFoundError, ValidationError, WorkflowError
from promptops_app.database import Base
from promptops_app.agents_models import (
    AgentTemplate,
    AgentDefinition,
    AgentDefinitionVersion,
    AgentRun,
    AgentCheckpoint,
)
from promptops_app.services.agent_checkpoint_service import AgentCheckpointService
from promptops_app.services.agents_tenant_service import AgentsTenantService


# ─────────────────────────────────────────────────────────────────────────────
# Test Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def db():
    """In-memory SQLite database for testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def mock_project_id():
    """Mock project ID."""
    return 1


@pytest.fixture
def mock_user_id():
    """Mock user ID (username)."""
    return "test_user"


@pytest.fixture
def mock_user_role():
    """Mock user role."""
    return "author"


@pytest.fixture
def setup_template(db):
    """Create a test agent template."""
    template = AgentTemplate(
        key="content_creator",
        name="Content Creator",
        description="Creates educational content",
        specialization="content_creation",
        instructions="You are a content creator...",
        input_schema='{"type": "object"}',
        output_schema='{"type": "object"}',
        owner="admin",
        version=1,
        is_active=True,
    )
    db.add(template)
    db.commit()
    return template


@pytest.fixture
def setup_agent(db, setup_template, mock_project_id, mock_user_id):
    """Create a test agent definition."""
    agent = AgentDefinition(
        project_id=mock_project_id,
        template_id=setup_template.id,
        name="Test Agent",
        call_handle="test_agent",
        description="Test agent for unit tests",
        owner=mock_user_id,
        configuration='{"model": "gpt-4o"}',
        lifecycle_state="active",
        created_at=datetime.utcnow(),
        created_by=mock_user_id,
    )
    db.add(agent)
    db.commit()

    # Create an active version
    version = AgentDefinitionVersion(
        definition_id=agent.id,
        version_number=1,
        configuration='{"model": "gpt-4o"}',
        is_active=True,
        created_at=datetime.utcnow(),
        created_by=mock_user_id,
    )
    db.add(version)
    db.commit()

    return agent


@pytest.fixture
def setup_run(db, setup_agent, mock_project_id, mock_user_id, mock_user_role):
    """Create a test run in running state."""
    run = AgentRun(
        project_id=mock_project_id,
        definition_id=setup_agent.id,
        version_id=setup_agent.versions[0].id,
        initiated_by=mock_user_id,
        initiated_by_role=mock_user_role,
        input_content="Test content",
        state="running",
        started_at=datetime.utcnow(),
        step_count=0,
        created_at=datetime.utcnow(),
    )
    db.add(run)
    db.commit()
    return run


@pytest.fixture
def sample_snapshot():
    """Create a sample run state snapshot."""
    return {
        "steps_completed": [
            {
                "step_index": 0,
                "step_type": "model_call",
                "status": "completed",
                "output": "Generated content",
            }
        ],
        "current_outputs": {
            "generation_1": "Generated content"
        },
        "run_metadata": {
            "run_id": 1,
            "step_count": 1,
            "accumulated_cost": 0.05,
            "max_steps": 10,
        },
        "timestamp": datetime.utcnow().isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Test: create_checkpoint()
# ─────────────────────────────────────────────────────────────────────────────

class TestCreateCheckpoint:
    """Test suite for AgentCheckpointService.create_checkpoint()"""

    def test_create_checkpoint_basic(self, db, setup_run, mock_project_id, sample_snapshot):
        """Test basic checkpoint creation."""
        checkpoint = AgentCheckpointService.create_checkpoint(
            db=db,
            run_id=setup_run.id,
            project_id=mock_project_id,
            step_count=1,
            accumulated_cost=0.05,
            run_state_snapshot=sample_snapshot,
            resumable=True,
        )

        assert checkpoint.id is not None
        assert checkpoint.run_id == setup_run.id
        assert checkpoint.checkpoint_index == 0
        assert checkpoint.resumable == True
        assert checkpoint.resume_reason is None

        # Verify snapshot was persisted
        persisted_snapshot = json.loads(checkpoint.step_state)
        assert persisted_snapshot["steps_completed"][0]["step_type"] == "model_call"

    def test_create_checkpoint_adds_timestamp(self, db, setup_run, mock_project_id):
        """Test that checkpoint adds timestamp to snapshot if missing."""
        snapshot_without_ts = {
            "steps_completed": [],
            "current_outputs": {},
            "run_metadata": {"run_id": 1, "step_count": 0, "accumulated_cost": 0.0},
        }

        checkpoint = AgentCheckpointService.create_checkpoint(
            db=db,
            run_id=setup_run.id,
            project_id=mock_project_id,
            step_count=0,
            accumulated_cost=0.0,
            run_state_snapshot=snapshot_without_ts,
        )

        persisted_snapshot = json.loads(checkpoint.step_state)
        assert "timestamp" in persisted_snapshot

    def test_create_checkpoint_not_resumable(self, db, setup_run, mock_project_id, sample_snapshot):
        """Test creating a non-resumable checkpoint."""
        checkpoint = AgentCheckpointService.create_checkpoint(
            db=db,
            run_id=setup_run.id,
            project_id=mock_project_id,
            step_count=1,
            accumulated_cost=0.05,
            run_state_snapshot=sample_snapshot,
            resumable=False,
            resume_reason="awaiting_input",
        )

        assert checkpoint.resumable == False
        assert checkpoint.resume_reason == "awaiting_input"

    def test_create_checkpoint_run_not_found(self, db, mock_project_id, sample_snapshot):
        """Test that creating checkpoint for non-existent run raises NotFoundError."""
        with pytest.raises(NotFoundError):
            AgentCheckpointService.create_checkpoint(
                db=db,
                run_id=9999,
                project_id=mock_project_id,
                step_count=1,
                accumulated_cost=0.05,
                run_state_snapshot=sample_snapshot,
            )

    def test_create_checkpoint_invalid_snapshot_not_dict(self, db, setup_run, mock_project_id):
        """Test that invalid snapshot (not dict) raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            AgentCheckpointService.create_checkpoint(
                db=db,
                run_id=setup_run.id,
                project_id=mock_project_id,
                step_count=1,
                accumulated_cost=0.05,
                run_state_snapshot="not a dict",  # type: ignore
            )
        assert "must be a dict" in str(exc_info.value)

    def test_create_checkpoint_invalid_snapshot_missing_fields(self, db, setup_run, mock_project_id):
        """Test that snapshot missing required fields raises ValidationError."""
        incomplete_snapshot = {
            "steps_completed": [],
            # Missing: current_outputs, run_metadata
        }

        with pytest.raises(ValidationError) as exc_info:
            AgentCheckpointService.create_checkpoint(
                db=db,
                run_id=setup_run.id,
                project_id=mock_project_id,
                step_count=1,
                accumulated_cost=0.05,
                run_state_snapshot=incomplete_snapshot,
            )
        assert "missing required field" in str(exc_info.value)

    def test_create_checkpoint_sequential_indexing(self, db, setup_run, mock_project_id, sample_snapshot):
        """Test that checkpoint indices are sequential."""
        checkpoint1 = AgentCheckpointService.create_checkpoint(
            db=db,
            run_id=setup_run.id,
            project_id=mock_project_id,
            step_count=1,
            accumulated_cost=0.05,
            run_state_snapshot=sample_snapshot,
        )

        checkpoint2 = AgentCheckpointService.create_checkpoint(
            db=db,
            run_id=setup_run.id,
            project_id=mock_project_id,
            step_count=2,
            accumulated_cost=0.10,
            run_state_snapshot=sample_snapshot,
        )

        assert checkpoint1.checkpoint_index == 0
        assert checkpoint2.checkpoint_index == 1

    def test_create_checkpoint_invalid_step_count_negative(self, db, setup_run, mock_project_id, sample_snapshot):
        """Test that negative step_count raises ValidationError."""
        with pytest.raises(ValidationError):
            AgentCheckpointService.create_checkpoint(
                db=db,
                run_id=setup_run.id,
                project_id=mock_project_id,
                step_count=-1,
                accumulated_cost=0.05,
                run_state_snapshot=sample_snapshot,
            )

    def test_create_checkpoint_run_not_in_running_state(self, db, mock_project_id, setup_agent, mock_user_id, mock_user_role, sample_snapshot):
        """Test that checkpoint creation fails if run is not in running state."""
        # Create run in queued state
        run = AgentRun(
            project_id=mock_project_id,
            definition_id=setup_agent.id,
            version_id=setup_agent.versions[0].id,
            initiated_by=mock_user_id,
            initiated_by_role=mock_user_role,
            input_content="Test",
            state="queued",
            created_at=datetime.utcnow(),
        )
        db.add(run)
        db.commit()

        with pytest.raises(WorkflowError):
            AgentCheckpointService.create_checkpoint(
                db=db,
                run_id=run.id,
                project_id=mock_project_id,
                step_count=1,
                accumulated_cost=0.05,
                run_state_snapshot=sample_snapshot,
            )


# ─────────────────────────────────────────────────────────────────────────────
# Test: get_latest_checkpoint()
# ─────────────────────────────────────────────────────────────────────────────

class TestGetLatestCheckpoint:
    """Test suite for AgentCheckpointService.get_latest_checkpoint()"""

    def test_get_latest_checkpoint_basic(self, db, setup_run, mock_project_id, sample_snapshot):
        """Test retrieving the latest checkpoint."""
        cp1 = AgentCheckpointService.create_checkpoint(
            db=db,
            run_id=setup_run.id,
            project_id=mock_project_id,
            step_count=1,
            accumulated_cost=0.05,
            run_state_snapshot=sample_snapshot,
        )

        cp2 = AgentCheckpointService.create_checkpoint(
            db=db,
            run_id=setup_run.id,
            project_id=mock_project_id,
            step_count=2,
            accumulated_cost=0.10,
            run_state_snapshot=sample_snapshot,
        )

        latest = AgentCheckpointService.get_latest_checkpoint(
            db=db,
            run_id=setup_run.id,
            project_id=mock_project_id,
        )

        assert latest.id == cp2.id
        assert latest.checkpoint_index == 1

    def test_get_latest_checkpoint_none_exist(self, db, setup_run, mock_project_id):
        """Test retrieving latest checkpoint when none exist."""
        latest = AgentCheckpointService.get_latest_checkpoint(
            db=db,
            run_id=setup_run.id,
            project_id=mock_project_id,
        )

        assert latest is None

    def test_get_latest_checkpoint_resumable_only(self, db, setup_run, mock_project_id, sample_snapshot):
        """Test retrieving latest resumable checkpoint only."""
        cp1 = AgentCheckpointService.create_checkpoint(
            db=db,
            run_id=setup_run.id,
            project_id=mock_project_id,
            step_count=1,
            accumulated_cost=0.05,
            run_state_snapshot=sample_snapshot,
            resumable=True,
        )

        cp2 = AgentCheckpointService.create_checkpoint(
            db=db,
            run_id=setup_run.id,
            project_id=mock_project_id,
            step_count=2,
            accumulated_cost=0.10,
            run_state_snapshot=sample_snapshot,
            resumable=False,
            resume_reason="awaiting_input",
        )

        # Get latest without resumable filter
        latest_all = AgentCheckpointService.get_latest_checkpoint(
            db=db,
            run_id=setup_run.id,
            project_id=mock_project_id,
            resumable_only=False,
        )
        assert latest_all.id == cp2.id

        # Get latest with resumable filter
        latest_resumable = AgentCheckpointService.get_latest_checkpoint(
            db=db,
            run_id=setup_run.id,
            project_id=mock_project_id,
            resumable_only=True,
        )
        assert latest_resumable.id == cp1.id

    def test_get_latest_checkpoint_run_not_found(self, db, mock_project_id):
        """Test retrieving checkpoint for non-existent run raises NotFoundError."""
        with pytest.raises(NotFoundError):
            AgentCheckpointService.get_latest_checkpoint(
                db=db,
                run_id=9999,
                project_id=mock_project_id,
            )


# ─────────────────────────────────────────────────────────────────────────────
# Test: resume_from_checkpoint()
# ─────────────────────────────────────────────────────────────────────────────

class TestResumeFromCheckpoint:
    """Test suite for AgentCheckpointService.resume_from_checkpoint()"""

    def test_resume_from_checkpoint_latest(self, db, setup_run, mock_project_id, sample_snapshot):
        """Test resuming from latest checkpoint."""
        checkpoint = AgentCheckpointService.create_checkpoint(
            db=db,
            run_id=setup_run.id,
            project_id=mock_project_id,
            step_count=1,
            accumulated_cost=0.05,
            run_state_snapshot=sample_snapshot,
        )

        run, cp, snapshot = AgentCheckpointService.resume_from_checkpoint(
            db=db,
            run_id=setup_run.id,
            project_id=mock_project_id,
        )

        assert cp.id == checkpoint.id
        assert run.state == "running"
        assert snapshot["steps_completed"][0]["step_type"] == "model_call"

    def test_resume_from_checkpoint_specific(self, db, setup_run, mock_project_id, sample_snapshot):
        """Test resuming from specific checkpoint."""
        cp1 = AgentCheckpointService.create_checkpoint(
            db=db,
            run_id=setup_run.id,
            project_id=mock_project_id,
            step_count=1,
            accumulated_cost=0.05,
            run_state_snapshot=sample_snapshot,
        )

        cp2 = AgentCheckpointService.create_checkpoint(
            db=db,
            run_id=setup_run.id,
            project_id=mock_project_id,
            step_count=2,
            accumulated_cost=0.10,
            run_state_snapshot=sample_snapshot,
        )

        # Resume from first checkpoint
        run, cp, snapshot = AgentCheckpointService.resume_from_checkpoint(
            db=db,
            run_id=setup_run.id,
            project_id=mock_project_id,
            checkpoint_id=cp1.id,
        )

        assert cp.id == cp1.id

    def test_resume_from_checkpoint_idempotent(self, db, setup_run, mock_project_id, sample_snapshot):
        """Test that resume is idempotent (safe to call multiple times)."""
        checkpoint = AgentCheckpointService.create_checkpoint(
            db=db,
            run_id=setup_run.id,
            project_id=mock_project_id,
            step_count=1,
            accumulated_cost=0.05,
            run_state_snapshot=sample_snapshot,
        )

        # Call resume twice
        run1, cp1, snapshot1 = AgentCheckpointService.resume_from_checkpoint(
            db=db,
            run_id=setup_run.id,
            project_id=mock_project_id,
        )

        run2, cp2, snapshot2 = AgentCheckpointService.resume_from_checkpoint(
            db=db,
            run_id=setup_run.id,
            project_id=mock_project_id,
        )

        # Should return same checkpoint and snapshot
        assert cp1.id == cp2.id
        assert snapshot1 == snapshot2

    def test_resume_from_checkpoint_not_resumable(self, db, setup_run, mock_project_id, sample_snapshot):
        """Test that resuming from non-resumable checkpoint raises ValidationError."""
        checkpoint = AgentCheckpointService.create_checkpoint(
            db=db,
            run_id=setup_run.id,
            project_id=mock_project_id,
            step_count=1,
            accumulated_cost=0.05,
            run_state_snapshot=sample_snapshot,
            resumable=False,
        )

        with pytest.raises(ValidationError) as exc_info:
            AgentCheckpointService.resume_from_checkpoint(
                db=db,
                run_id=setup_run.id,
                project_id=mock_project_id,
            )
        assert "not resumable" in str(exc_info.value)

    def test_resume_from_checkpoint_corrupted_state(self, db, setup_run, mock_project_id):
        """Test that corrupted checkpoint state raises ValidationError."""
        # Create checkpoint with invalid JSON
        checkpoint = AgentCheckpoint(
            run_id=setup_run.id,
            checkpoint_index=0,
            step_state="{ invalid json",
            resumable=True,
            created_at=datetime.utcnow(),
        )
        db.add(checkpoint)
        db.commit()

        with pytest.raises(ValidationError) as exc_info:
            AgentCheckpointService.resume_from_checkpoint(
                db=db,
                run_id=setup_run.id,
                project_id=mock_project_id,
            )
        assert "corrupted" in str(exc_info.value)

    def test_resume_from_checkpoint_not_found(self, db, setup_run, mock_project_id):
        """Test that resuming from non-existent checkpoint raises NotFoundError."""
        with pytest.raises(NotFoundError):
            AgentCheckpointService.resume_from_checkpoint(
                db=db,
                run_id=setup_run.id,
                project_id=mock_project_id,
                checkpoint_id=9999,
            )


# ─────────────────────────────────────────────────────────────────────────────
# Test: cleanup_checkpoints()
# ─────────────────────────────────────────────────────────────────────────────

class TestCleanupCheckpoints:
    """Test suite for AgentCheckpointService.cleanup_checkpoints()"""

    def test_cleanup_checkpoints_basic(self, db, setup_run, mock_project_id, sample_snapshot):
        """Test basic checkpoint cleanup."""
        for i in range(3):
            AgentCheckpointService.create_checkpoint(
                db=db,
                run_id=setup_run.id,
                project_id=mock_project_id,
                step_count=i + 1,
                accumulated_cost=0.05 * (i + 1),
                run_state_snapshot=sample_snapshot,
            )

        # Verify 3 checkpoints exist
        checkpoints = db.query(AgentCheckpoint).filter(
            AgentCheckpoint.run_id == setup_run.id
        ).all()
        assert len(checkpoints) == 3

        # Cleanup keeping only 1
        deleted_count = AgentCheckpointService.cleanup_checkpoints(
            db=db,
            run_id=setup_run.id,
            project_id=mock_project_id,
            keep_latest=1,
        )

        assert deleted_count == 2

        # Verify 1 checkpoint remains (the latest)
        remaining = db.query(AgentCheckpoint).filter(
            AgentCheckpoint.run_id == setup_run.id
        ).all()
        assert len(remaining) == 1
        assert remaining[0].checkpoint_index == 2

    def test_cleanup_checkpoints_keep_zero(self, db, setup_run, mock_project_id, sample_snapshot):
        """Test cleanup with keep_latest=0 (delete all)."""
        for i in range(3):
            AgentCheckpointService.create_checkpoint(
                db=db,
                run_id=setup_run.id,
                project_id=mock_project_id,
                step_count=i + 1,
                accumulated_cost=0.05 * (i + 1),
                run_state_snapshot=sample_snapshot,
            )

        deleted_count = AgentCheckpointService.cleanup_checkpoints(
            db=db,
            run_id=setup_run.id,
            project_id=mock_project_id,
            keep_latest=0,
        )

        assert deleted_count == 3

        remaining = db.query(AgentCheckpoint).filter(
            AgentCheckpoint.run_id == setup_run.id
        ).all()
        assert len(remaining) == 0

    def test_cleanup_checkpoints_no_cleanup_needed(self, db, setup_run, mock_project_id, sample_snapshot):
        """Test cleanup when keep_latest is greater than checkpoint count."""
        for i in range(2):
            AgentCheckpointService.create_checkpoint(
                db=db,
                run_id=setup_run.id,
                project_id=mock_project_id,
                step_count=i + 1,
                accumulated_cost=0.05 * (i + 1),
                run_state_snapshot=sample_snapshot,
            )

        deleted_count = AgentCheckpointService.cleanup_checkpoints(
            db=db,
            run_id=setup_run.id,
            project_id=mock_project_id,
            keep_latest=5,
        )

        assert deleted_count == 0

        remaining = db.query(AgentCheckpoint).filter(
            AgentCheckpoint.run_id == setup_run.id
        ).all()
        assert len(remaining) == 2

    def test_cleanup_checkpoints_invalid_keep_latest(self, db, setup_run, mock_project_id):
        """Test that negative keep_latest raises ValidationError."""
        with pytest.raises(ValidationError):
            AgentCheckpointService.cleanup_checkpoints(
                db=db,
                run_id=setup_run.id,
                project_id=mock_project_id,
                keep_latest=-1,
            )


# ─────────────────────────────────────────────────────────────────────────────
# Test: list_checkpoints()
# ─────────────────────────────────────────────────────────────────────────────

class TestListCheckpoints:
    """Test suite for AgentCheckpointService.list_checkpoints()"""

    def test_list_checkpoints_basic(self, db, setup_run, mock_project_id, sample_snapshot):
        """Test listing checkpoints."""
        for i in range(3):
            AgentCheckpointService.create_checkpoint(
                db=db,
                run_id=setup_run.id,
                project_id=mock_project_id,
                step_count=i + 1,
                accumulated_cost=0.05 * (i + 1),
                run_state_snapshot=sample_snapshot,
            )

        checkpoints, total = AgentCheckpointService.list_checkpoints(
            db=db,
            run_id=setup_run.id,
            project_id=mock_project_id,
        )

        assert len(checkpoints) == 3
        assert total == 3
        # Verify ordering (by checkpoint_index)
        assert checkpoints[0].checkpoint_index == 0
        assert checkpoints[1].checkpoint_index == 1
        assert checkpoints[2].checkpoint_index == 2

    def test_list_checkpoints_pagination(self, db, setup_run, mock_project_id, sample_snapshot):
        """Test checkpoint pagination."""
        for i in range(10):
            AgentCheckpointService.create_checkpoint(
                db=db,
                run_id=setup_run.id,
                project_id=mock_project_id,
                step_count=i + 1,
                accumulated_cost=0.05 * (i + 1),
                run_state_snapshot=sample_snapshot,
            )

        # Get first page
        page1, total = AgentCheckpointService.list_checkpoints(
            db=db,
            run_id=setup_run.id,
            project_id=mock_project_id,
            limit=5,
            offset=0,
        )

        # Get second page
        page2, total = AgentCheckpointService.list_checkpoints(
            db=db,
            run_id=setup_run.id,
            project_id=mock_project_id,
            limit=5,
            offset=5,
        )

        assert len(page1) == 5
        assert len(page2) == 5
        assert total == 10
        assert page1[0].checkpoint_index == 0
        assert page2[0].checkpoint_index == 5

    def test_list_checkpoints_empty(self, db, setup_run, mock_project_id):
        """Test listing checkpoints when none exist."""
        checkpoints, total = AgentCheckpointService.list_checkpoints(
            db=db,
            run_id=setup_run.id,
            project_id=mock_project_id,
        )

        assert len(checkpoints) == 0
        assert total == 0

    def test_list_checkpoints_invalid_limit(self, db, setup_run, mock_project_id):
        """Test that invalid limit raises ValidationError."""
        with pytest.raises(ValidationError):
            AgentCheckpointService.list_checkpoints(
                db=db,
                run_id=setup_run.id,
                project_id=mock_project_id,
                limit=0,
            )

    def test_list_checkpoints_invalid_offset(self, db, setup_run, mock_project_id):
        """Test that invalid offset raises ValidationError."""
        with pytest.raises(ValidationError):
            AgentCheckpointService.list_checkpoints(
                db=db,
                run_id=setup_run.id,
                project_id=mock_project_id,
                offset=-1,
            )


# ─────────────────────────────────────────────────────────────────────────────
# Test: validate_checkpoint()
# ─────────────────────────────────────────────────────────────────────────────

class TestValidateCheckpoint:
    """Test suite for AgentCheckpointService.validate_checkpoint()"""

    def test_validate_checkpoint_valid(self, db, setup_run, mock_project_id, sample_snapshot):
        """Test validating a valid checkpoint."""
        checkpoint = AgentCheckpointService.create_checkpoint(
            db=db,
            run_id=setup_run.id,
            project_id=mock_project_id,
            step_count=1,
            accumulated_cost=0.05,
            run_state_snapshot=sample_snapshot,
        )

        is_valid, error = AgentCheckpointService.validate_checkpoint(
            db=db,
            checkpoint_id=checkpoint.id,
            run_id=setup_run.id,
            project_id=mock_project_id,
        )

        assert is_valid == True
        assert error is None

    def test_validate_checkpoint_not_resumable(self, db, setup_run, mock_project_id, sample_snapshot):
        """Test validating a non-resumable checkpoint."""
        checkpoint = AgentCheckpointService.create_checkpoint(
            db=db,
            run_id=setup_run.id,
            project_id=mock_project_id,
            step_count=1,
            accumulated_cost=0.05,
            run_state_snapshot=sample_snapshot,
            resumable=False,
        )

        is_valid, error = AgentCheckpointService.validate_checkpoint(
            db=db,
            checkpoint_id=checkpoint.id,
            run_id=setup_run.id,
            project_id=mock_project_id,
        )

        assert is_valid == False
        assert "not resumable" in error

    def test_validate_checkpoint_corrupted_json(self, db, setup_run, mock_project_id):
        """Test validating checkpoint with corrupted JSON."""
        checkpoint = AgentCheckpoint(
            run_id=setup_run.id,
            checkpoint_index=0,
            step_state="{ invalid json",
            resumable=True,
            created_at=datetime.utcnow(),
        )
        db.add(checkpoint)
        db.commit()

        is_valid, error = AgentCheckpointService.validate_checkpoint(
            db=db,
            checkpoint_id=checkpoint.id,
            run_id=setup_run.id,
            project_id=mock_project_id,
        )

        assert is_valid == False
        assert "corrupted" in error

    def test_validate_checkpoint_missing_snapshot_fields(self, db, setup_run, mock_project_id):
        """Test validating checkpoint with missing snapshot fields."""
        incomplete_snapshot = {
            "steps_completed": [],
            # Missing current_outputs, run_metadata
        }
        checkpoint = AgentCheckpoint(
            run_id=setup_run.id,
            checkpoint_index=0,
            step_state=json.dumps(incomplete_snapshot),
            resumable=True,
            created_at=datetime.utcnow(),
        )
        db.add(checkpoint)
        db.commit()

        is_valid, error = AgentCheckpointService.validate_checkpoint(
            db=db,
            checkpoint_id=checkpoint.id,
            run_id=setup_run.id,
            project_id=mock_project_id,
        )

        assert is_valid == False
        assert "missing fields" in error

    def test_validate_checkpoint_missing_metadata(self, db, setup_run, mock_project_id):
        """Test validating checkpoint with incomplete metadata."""
        incomplete_snapshot = {
            "steps_completed": [],
            "current_outputs": {},
            "run_metadata": {
                "run_id": 1,
                # Missing step_count, accumulated_cost
            },
        }
        checkpoint = AgentCheckpoint(
            run_id=setup_run.id,
            checkpoint_index=0,
            step_state=json.dumps(incomplete_snapshot),
            resumable=True,
            created_at=datetime.utcnow(),
        )
        db.add(checkpoint)
        db.commit()

        is_valid, error = AgentCheckpointService.validate_checkpoint(
            db=db,
            checkpoint_id=checkpoint.id,
            run_id=setup_run.id,
            project_id=mock_project_id,
        )

        assert is_valid == False
        assert "metadata missing" in error

    def test_validate_checkpoint_not_found(self, db, setup_run, mock_project_id):
        """Test validating non-existent checkpoint."""
        is_valid, error = AgentCheckpointService.validate_checkpoint(
            db=db,
            checkpoint_id=9999,
            run_id=setup_run.id,
            project_id=mock_project_id,
        )

        assert is_valid == False
        assert "not found" in error


# ─────────────────────────────────────────────────────────────────────────────
# Test: Tenant Isolation
# ─────────────────────────────────────────────────────────────────────────────

class TestTenantIsolation:
    """Test suite for tenant isolation enforcement"""

    def test_checkpoint_tenant_isolation(self, db, setup_run, mock_project_id, sample_snapshot):
        """Test that checkpoints are isolated by project_id."""
        # Create checkpoint in project 1
        checkpoint = AgentCheckpointService.create_checkpoint(
            db=db,
            run_id=setup_run.id,
            project_id=mock_project_id,
            step_count=1,
            accumulated_cost=0.05,
            run_state_snapshot=sample_snapshot,
        )

        # Try to access from project 2
        other_project_id = 2
        with pytest.raises(NotFoundError):
            AgentCheckpointService.get_latest_checkpoint(
                db=db,
                run_id=setup_run.id,
                project_id=other_project_id,
            )
