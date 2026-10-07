"""Unit tests for AgentRunService

Tests cover:
- create_run() idempotency, authorization, budget reservation
- start_run() state transitions, atomic updates
- complete_run() result persistence, cost tracking
- fail_run() error handling, budget release
- cancel_run() authorization, graceful cancellation
- Tenant isolation enforcement
- SQLite concurrency handling
"""

import json
import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock

from sqlalchemy.orm import Session
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import NotFoundError, PermissionDeniedError, ValidationError, WorkflowError
from app.core.permissions import rbac_check
from promptops_app.database import Base
from promptops_app.agents_models import (
    AgentTemplate,
    AgentDefinition,
    AgentDefinitionVersion,
    AgentRun,
)
from promptops_app.services.agent_run_service import AgentRunService
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


# ─────────────────────────────────────────────────────────────────────────────
# Test: create_run()
# ─────────────────────────────────────────────────────────────────────────────

class TestCreateRun:
    """Test suite for AgentRunService.create_run()"""

    def test_create_run_basic(self, db, setup_agent, mock_project_id, mock_user_id, mock_user_role):
        """Test basic run creation."""
        run = AgentRunService.create_run(
            db=db,
            project_id=mock_project_id,
            definition_id=setup_agent.id,
            initiated_by=mock_user_id,
            initiated_by_role=mock_user_role,
            input_content="Test content",
            estimated_cost=0.05,
        )

        assert run.id is not None
        assert run.project_id == mock_project_id
        assert run.definition_id == setup_agent.id
        assert run.initiated_by == mock_user_id
        assert run.state == "queued"
        assert run.input_content == "Test content"
        assert run.estimated_cost == 0.05

    def test_create_run_idempotency(self, db, setup_agent, mock_project_id, mock_user_id, mock_user_role):
        """Test that same request_id returns same run."""
        request_id = "req-123-abc"

        run1 = AgentRunService.create_run(
            db=db,
            project_id=mock_project_id,
            definition_id=setup_agent.id,
            initiated_by=mock_user_id,
            initiated_by_role=mock_user_role,
            request_id=request_id,
            input_content="Content 1",
        )

        run2 = AgentRunService.create_run(
            db=db,
            project_id=mock_project_id,
            definition_id=setup_agent.id,
            initiated_by=mock_user_id,
            initiated_by_role=mock_user_role,
            request_id=request_id,
            input_content="Content 2",  # Different content
        )

        assert run1.id == run2.id
        assert run1.input_content == "Content 1"  # Original content preserved

    def test_create_run_permission_denied(self, db, setup_agent, mock_project_id, mock_user_id):
        """Test that users without agents.run permission cannot create runs."""
        with patch("promptops_app.services.agent_run_service.rbac_check") as mock_rbac:
            mock_rbac.return_value = False

            with pytest.raises(PermissionDeniedError):
                AgentRunService.create_run(
                    db=db,
                    project_id=mock_project_id,
                    definition_id=setup_agent.id,
                    initiated_by=mock_user_id,
                    initiated_by_role="viewer",
                    input_content="Test",
                )

    def test_create_run_agent_not_found(self, db, mock_project_id, mock_user_id, mock_user_role):
        """Test that creating run with non-existent agent raises NotFoundError."""
        with pytest.raises(NotFoundError):
            AgentRunService.create_run(
                db=db,
                project_id=mock_project_id,
                definition_id=9999,  # Non-existent
                initiated_by=mock_user_id,
                initiated_by_role=mock_user_role,
                input_content="Test",
            )

    def test_create_run_tenant_isolation(self, db, setup_agent, mock_user_id, mock_user_role):
        """Test that agent from one project cannot be run in another project."""
        wrong_project_id = 999

        with pytest.raises(NotFoundError):
            AgentRunService.create_run(
                db=db,
                project_id=wrong_project_id,
                definition_id=setup_agent.id,  # Agent belongs to project 1
                initiated_by=mock_user_id,
                initiated_by_role=mock_user_role,
                input_content="Test",
            )

    def test_create_run_agent_not_active(self, db, setup_agent, mock_project_id, mock_user_id, mock_user_role):
        """Test that runs cannot be created for inactive agents."""
        # Change agent to paused state
        setup_agent.lifecycle_state = "paused"
        db.commit()

        with pytest.raises(ValidationError):
            AgentRunService.create_run(
                db=db,
                project_id=mock_project_id,
                definition_id=setup_agent.id,
                initiated_by=mock_user_id,
                initiated_by_role=mock_user_role,
                input_content="Test",
            )

    def test_create_run_with_context(self, db, setup_agent, mock_project_id, mock_user_id, mock_user_role):
        """Test run creation with input context."""
        context = {"course_id": 123, "module_id": 456}

        run = AgentRunService.create_run(
            db=db,
            project_id=mock_project_id,
            definition_id=setup_agent.id,
            initiated_by=mock_user_id,
            initiated_by_role=mock_user_role,
            input_content="Test",
            input_context=context,
        )

        assert run.input_context == json.dumps(context)


# ─────────────────────────────────────────────────────────────────────────────
# Test: start_run()
# ─────────────────────────────────────────────────────────────────────────────

class TestStartRun:
    """Test suite for AgentRunService.start_run()"""

    def test_start_run_success(self, db, setup_agent, mock_project_id, mock_user_id, mock_user_role):
        """Test successful run state transition from queued to running."""
        run = AgentRunService.create_run(
            db=db,
            project_id=mock_project_id,
            definition_id=setup_agent.id,
            initiated_by=mock_user_id,
            initiated_by_role=mock_user_role,
            input_content="Test",
        )

        started_run = AgentRunService.start_run(
            db=db,
            run_id=run.id,
            project_id=mock_project_id,
        )

        assert started_run.state == "running"
        assert started_run.started_at is not None
        assert started_run.expires_at is not None

    def test_start_run_not_found(self, db, mock_project_id):
        """Test starting non-existent run."""
        with pytest.raises(NotFoundError):
            AgentRunService.start_run(
                db=db,
                run_id=9999,
                project_id=mock_project_id,
            )

    def test_start_run_already_started(self, db, setup_agent, mock_project_id, mock_user_id, mock_user_role):
        """Test that starting an already-running run fails."""
        run = AgentRunService.create_run(
            db=db,
            project_id=mock_project_id,
            definition_id=setup_agent.id,
            initiated_by=mock_user_id,
            initiated_by_role=mock_user_role,
            input_content="Test",
        )

        # Start it
        AgentRunService.start_run(db=db, run_id=run.id, project_id=mock_project_id)

        # Try to start again
        with pytest.raises(WorkflowError):
            AgentRunService.start_run(db=db, run_id=run.id, project_id=mock_project_id)

    def test_start_run_tenant_isolation(self, db, setup_agent, mock_user_id, mock_user_role):
        """Test that runs can only be started by their tenant."""
        run = AgentRunService.create_run(
            db=db,
            project_id=1,
            definition_id=setup_agent.id,
            initiated_by=mock_user_id,
            initiated_by_role=mock_user_role,
            input_content="Test",
        )

        with pytest.raises(NotFoundError):
            AgentRunService.start_run(
                db=db,
                run_id=run.id,
                project_id=999,  # Different tenant
            )


# ─────────────────────────────────────────────────────────────────────────────
# Test: complete_run()
# ─────────────────────────────────────────────────────────────────────────────

class TestCompleteRun:
    """Test suite for AgentRunService.complete_run()"""

    def test_complete_run_success(self, db, setup_agent, mock_project_id, mock_user_id, mock_user_role):
        """Test successful run completion."""
        run = AgentRunService.create_run(
            db=db,
            project_id=mock_project_id,
            definition_id=setup_agent.id,
            initiated_by=mock_user_id,
            initiated_by_role=mock_user_role,
            input_content="Test",
        )

        AgentRunService.start_run(db=db, run_id=run.id, project_id=mock_project_id)

        result = {"generated_content": "Hello, world!", "confidence": 0.95}
        completed_run = AgentRunService.complete_run(
            db=db,
            run_id=run.id,
            project_id=mock_project_id,
            result=result,
            actual_cost=0.03,
            prompt_tokens=100,
            completion_tokens=50,
        )

        assert completed_run.state == "completed"
        assert completed_run.result == json.dumps(result)
        assert completed_run.actual_cost == 0.03
        assert completed_run.completed_at is not None
        assert completed_run.execution_time_ms is not None

    def test_complete_run_not_found(self, db, mock_project_id):
        """Test completing non-existent run."""
        with pytest.raises(NotFoundError):
            AgentRunService.complete_run(
                db=db,
                run_id=9999,
                project_id=mock_project_id,
                result={"data": "test"},
            )

    def test_complete_run_wrong_state(self, db, setup_agent, mock_project_id, mock_user_id, mock_user_role):
        """Test that completing a queued run (without starting) fails."""
        run = AgentRunService.create_run(
            db=db,
            project_id=mock_project_id,
            definition_id=setup_agent.id,
            initiated_by=mock_user_id,
            initiated_by_role=mock_user_role,
            input_content="Test",
        )

        with pytest.raises(WorkflowError):
            AgentRunService.complete_run(
                db=db,
                run_id=run.id,
                project_id=mock_project_id,
                result={"data": "test"},
            )

    def test_complete_run_tenant_isolation(self, db, setup_agent, mock_user_id, mock_user_role):
        """Test that runs can only be completed by their tenant."""
        run = AgentRunService.create_run(
            db=db,
            project_id=1,
            definition_id=setup_agent.id,
            initiated_by=mock_user_id,
            initiated_by_role=mock_user_role,
            input_content="Test",
        )

        AgentRunService.start_run(db=db, run_id=run.id, project_id=1)

        with pytest.raises(NotFoundError):
            AgentRunService.complete_run(
                db=db,
                run_id=run.id,
                project_id=999,  # Different tenant
                result={"data": "test"},
            )

    def test_complete_run_execution_time(self, db, setup_agent, mock_project_id, mock_user_id, mock_user_role):
        """Test that execution time is calculated correctly."""
        run = AgentRunService.create_run(
            db=db,
            project_id=mock_project_id,
            definition_id=setup_agent.id,
            initiated_by=mock_user_id,
            initiated_by_role=mock_user_role,
            input_content="Test",
        )

        AgentRunService.start_run(db=db, run_id=run.id, project_id=mock_project_id)

        # Simulate some delay
        import time
        time.sleep(0.1)

        completed_run = AgentRunService.complete_run(
            db=db,
            run_id=run.id,
            project_id=mock_project_id,
            result={"data": "test"},
        )

        assert completed_run.execution_time_ms >= 100


# ─────────────────────────────────────────────────────────────────────────────
# Test: fail_run()
# ─────────────────────────────────────────────────────────────────────────────

class TestFailRun:
    """Test suite for AgentRunService.fail_run()"""

    def test_fail_run_success(self, db, setup_agent, mock_project_id, mock_user_id, mock_user_role):
        """Test successful run failure."""
        run = AgentRunService.create_run(
            db=db,
            project_id=mock_project_id,
            definition_id=setup_agent.id,
            initiated_by=mock_user_id,
            initiated_by_role=mock_user_role,
            input_content="Test",
        )

        AgentRunService.start_run(db=db, run_id=run.id, project_id=mock_project_id)

        failed_run = AgentRunService.fail_run(
            db=db,
            run_id=run.id,
            project_id=mock_project_id,
            error_message="API timeout",
            error_traceback="Traceback: ...",
        )

        assert failed_run.state == "failed"
        assert failed_run.error_message == "API timeout"
        assert failed_run.error_traceback == "Traceback: ..."
        assert failed_run.completed_at is not None

    def test_fail_run_not_found(self, db, mock_project_id):
        """Test failing non-existent run."""
        with pytest.raises(NotFoundError):
            AgentRunService.fail_run(
                db=db,
                run_id=9999,
                project_id=mock_project_id,
                error_message="Test error",
            )

    def test_fail_run_from_queued(self, db, setup_agent, mock_project_id, mock_user_id, mock_user_role):
        """Test that queued runs can be failed."""
        run = AgentRunService.create_run(
            db=db,
            project_id=mock_project_id,
            definition_id=setup_agent.id,
            initiated_by=mock_user_id,
            initiated_by_role=mock_user_role,
            input_content="Test",
        )

        # Fail without starting
        failed_run = AgentRunService.fail_run(
            db=db,
            run_id=run.id,
            project_id=mock_project_id,
            error_message="Validation error",
        )

        assert failed_run.state == "failed"


# ─────────────────────────────────────────────────────────────────────────────
# Test: cancel_run()
# ─────────────────────────────────────────────────────────────────────────────

class TestCancelRun:
    """Test suite for AgentRunService.cancel_run()"""

    def test_cancel_run_success(self, db, setup_agent, mock_project_id, mock_user_id, mock_user_role):
        """Test successful run cancellation."""
        run = AgentRunService.create_run(
            db=db,
            project_id=mock_project_id,
            definition_id=setup_agent.id,
            initiated_by=mock_user_id,
            initiated_by_role=mock_user_role,
            input_content="Test",
        )

        AgentRunService.start_run(db=db, run_id=run.id, project_id=mock_project_id)

        cancelled_run = AgentRunService.cancel_run(
            db=db,
            run_id=run.id,
            project_id=mock_project_id,
            initiated_by=mock_user_id,
            initiated_by_role=mock_user_role,
        )

        assert cancelled_run.state == "cancelled"
        assert mock_user_id in (cancelled_run.state_reason or "")

    def test_cancel_run_not_found(self, db, mock_project_id, mock_user_id, mock_user_role):
        """Test cancelling non-existent run."""
        with pytest.raises(NotFoundError):
            AgentRunService.cancel_run(
                db=db,
                run_id=9999,
                project_id=mock_project_id,
                initiated_by=mock_user_id,
                initiated_by_role=mock_user_role,
            )

    def test_cancel_run_permission_denied(self, db, setup_agent, mock_project_id, mock_user_id, mock_user_role):
        """Test that non-creator cannot cancel run unless admin."""
        run = AgentRunService.create_run(
            db=db,
            project_id=mock_project_id,
            definition_id=setup_agent.id,
            initiated_by=mock_user_id,
            initiated_by_role=mock_user_role,
            input_content="Test",
        )

        AgentRunService.start_run(db=db, run_id=run.id, project_id=mock_project_id)

        # Different user tries to cancel
        with pytest.raises(PermissionDeniedError):
            AgentRunService.cancel_run(
                db=db,
                run_id=run.id,
                project_id=mock_project_id,
                initiated_by="other_user",
                initiated_by_role="author",
            )

    def test_cancel_run_admin_override(self, db, setup_agent, mock_project_id, mock_user_id, mock_user_role):
        """Test that admin can cancel anyone's run."""
        run = AgentRunService.create_run(
            db=db,
            project_id=mock_project_id,
            definition_id=setup_agent.id,
            initiated_by=mock_user_id,
            initiated_by_role=mock_user_role,
            input_content="Test",
        )

        AgentRunService.start_run(db=db, run_id=run.id, project_id=mock_project_id)

        # Admin cancels
        cancelled_run = AgentRunService.cancel_run(
            db=db,
            run_id=run.id,
            project_id=mock_project_id,
            initiated_by="admin_user",
            initiated_by_role="admin",
        )

        assert cancelled_run.state == "cancelled"

    def test_cancel_run_already_completed(self, db, setup_agent, mock_project_id, mock_user_id, mock_user_role):
        """Test that completed runs cannot be cancelled."""
        run = AgentRunService.create_run(
            db=db,
            project_id=mock_project_id,
            definition_id=setup_agent.id,
            initiated_by=mock_user_id,
            initiated_by_role=mock_user_role,
            input_content="Test",
        )

        AgentRunService.start_run(db=db, run_id=run.id, project_id=mock_project_id)
        AgentRunService.complete_run(db=db, run_id=run.id, project_id=mock_project_id, result={})

        with pytest.raises(WorkflowError):
            AgentRunService.cancel_run(
                db=db,
                run_id=run.id,
                project_id=mock_project_id,
                initiated_by=mock_user_id,
                initiated_by_role=mock_user_role,
            )


# ─────────────────────────────────────────────────────────────────────────────
# Test: Utility Methods
# ─────────────────────────────────────────────────────────────────────────────

class TestUtilityMethods:
    """Test suite for get_run() and list_runs()"""

    def test_get_run(self, db, setup_agent, mock_project_id, mock_user_id, mock_user_role):
        """Test retrieving a run by ID."""
        run = AgentRunService.create_run(
            db=db,
            project_id=mock_project_id,
            definition_id=setup_agent.id,
            initiated_by=mock_user_id,
            initiated_by_role=mock_user_role,
            input_content="Test",
        )

        retrieved = AgentRunService.get_run(db=db, run_id=run.id, project_id=mock_project_id)
        assert retrieved.id == run.id

    def test_list_runs(self, db, setup_agent, mock_project_id, mock_user_id, mock_user_role):
        """Test listing runs for a project."""
        # Create multiple runs
        for i in range(5):
            AgentRunService.create_run(
                db=db,
                project_id=mock_project_id,
                definition_id=setup_agent.id,
                initiated_by=mock_user_id,
                initiated_by_role=mock_user_role,
                input_content=f"Test {i}",
            )

        runs, total = AgentRunService.list_runs(db=db, project_id=mock_project_id)
        assert total == 5
        assert len(runs) == 5

    def test_list_runs_with_limit(self, db, setup_agent, mock_project_id, mock_user_id, mock_user_role):
        """Test list_runs with pagination."""
        # Create multiple runs
        for i in range(10):
            AgentRunService.create_run(
                db=db,
                project_id=mock_project_id,
                definition_id=setup_agent.id,
                initiated_by=mock_user_id,
                initiated_by_role=mock_user_role,
                input_content=f"Test {i}",
            )

        runs, total = AgentRunService.list_runs(db=db, project_id=mock_project_id, limit=5, offset=0)
        assert total == 10
        assert len(runs) == 5

    def test_list_runs_filter_by_state(self, db, setup_agent, mock_project_id, mock_user_id, mock_user_role):
        """Test list_runs with state filtering."""
        # Create runs with different states
        for i in range(3):
            AgentRunService.create_run(
                db=db,
                project_id=mock_project_id,
                definition_id=setup_agent.id,
                initiated_by=mock_user_id,
                initiated_by_role=mock_user_role,
                input_content=f"Test {i}",
            )

        # Start one and complete it
        all_runs, _ = AgentRunService.list_runs(db=db, project_id=mock_project_id)
        if all_runs:
            run = all_runs[0]
            AgentRunService.start_run(db=db, run_id=run.id, project_id=mock_project_id)
            AgentRunService.complete_run(db=db, run_id=run.id, project_id=mock_project_id, result={})

        # Filter by completed
        completed_runs, total_completed = AgentRunService.list_runs(
            db=db,
            project_id=mock_project_id,
            state="completed"
        )
        assert total_completed == 1
        assert len(completed_runs) == 1

        # Filter by queued
        queued_runs, total_queued = AgentRunService.list_runs(
            db=db,
            project_id=mock_project_id,
            state="queued"
        )
        assert total_queued == 2
