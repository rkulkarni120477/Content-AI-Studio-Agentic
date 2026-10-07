"""Unit tests for AgentStepService

Tests for:
- Step creation with validation
- Step execution and metrics tracking
- Retry logic with exponential backoff
- Error handling and state transitions
- Tenant isolation
"""

import json
import pytest
from datetime import datetime
from unittest.mock import Mock, patch

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError, ValidationError, WorkflowError
from promptops_app.agents_models import AgentRun, AgentRunStep, AgentDefinition
from promptops_app.services.agent_step_service import AgentStepService, DEFAULT_MAX_RETRIES
from promptops_app.services.agent_run_service import AgentRunService


class TestCreateStep:
    """Tests for AgentStepService.create_step"""

    def test_create_step_success(self, db: Session, sample_run: AgentRun):
        """Test successful step creation"""
        input_data = {"content": "test input", "model": "gpt-4o"}

        step = AgentStepService.create_step(
            db=db,
            run_id=sample_run.id,
            project_id=sample_run.project_id,
            step_index=0,
            step_type="model_call",
            input_data=input_data,
            max_retries=3,
        )

        assert step.id is not None
        assert step.run_id == sample_run.id
        assert step.step_index == 0
        assert step.step_type == "model_call"
        assert step.status == "pending"
        assert json.loads(step.input_data) == input_data
        assert step.retry_count == 0
        assert step.max_retries == 3

    def test_create_step_sequential_validation(self, db: Session, sample_run: AgentRun):
        """Test that step_index must be sequential"""
        # Create first step at index 0
        AgentStepService.create_step(
            db=db,
            run_id=sample_run.id,
            project_id=sample_run.project_id,
            step_index=0,
            step_type="model_call",
        )

        # Try to create step at index 2 (should fail, need index 1)
        with pytest.raises(ValidationError, match="step_index must be sequential"):
            AgentStepService.create_step(
                db=db,
                run_id=sample_run.id,
                project_id=sample_run.project_id,
                step_index=2,
                step_type="model_call",
            )

    def test_create_step_invalid_type(self, db: Session, sample_run: AgentRun):
        """Test that invalid step_type raises ValidationError"""
        with pytest.raises(ValidationError, match="Invalid step_type"):
            AgentStepService.create_step(
                db=db,
                run_id=sample_run.id,
                project_id=sample_run.project_id,
                step_index=0,
                step_type="invalid_type",
            )

    def test_create_step_run_not_found(self, db: Session):
        """Test that creating step for non-existent run raises NotFoundError"""
        with pytest.raises(NotFoundError):
            AgentStepService.create_step(
                db=db,
                run_id=99999,
                project_id=1,
                step_index=0,
                step_type="model_call",
            )

    def test_create_step_wrong_tenant(self, db: Session, sample_run: AgentRun):
        """Test that creating step for run in different tenant fails"""
        with pytest.raises(NotFoundError):
            AgentStepService.create_step(
                db=db,
                run_id=sample_run.id,
                project_id=99999,  # Different project
                step_index=0,
                step_type="model_call",
            )

    def test_create_step_wrong_run_state(self, db: Session, sample_run: AgentRun):
        """Test that creating step for non-running run fails"""
        # Mark run as completed
        sample_run.state = "completed"
        db.commit()

        with pytest.raises(WorkflowError, match="Current state: completed"):
            AgentStepService.create_step(
                db=db,
                run_id=sample_run.id,
                project_id=sample_run.project_id,
                step_index=0,
                step_type="model_call",
            )

    def test_create_step_max_steps_exceeded(self, db: Session, sample_run: AgentRun):
        """Test that creating step when max_steps reached fails"""
        sample_run.max_steps = 1
        sample_run.step_count = 1
        db.commit()

        with pytest.raises(WorkflowError, match="reached max_steps limit"):
            AgentStepService.create_step(
                db=db,
                run_id=sample_run.id,
                project_id=sample_run.project_id,
                step_index=0,
                step_type="model_call",
            )


class TestExecuteStep:
    """Tests for AgentStepService.execute_step"""

    def test_execute_step_success(self, db: Session, sample_run: AgentRun):
        """Test successful step execution"""
        step = AgentStepService.create_step(
            db=db,
            run_id=sample_run.id,
            project_id=sample_run.project_id,
            step_index=0,
            step_type="model_call",
        )

        # Mock execution function
        def mock_execution(input_data):
            return (
                {"result": "test output"},  # output_data
                100,  # prompt_tokens
                50,   # completion_tokens
                0.01  # step_cost
            )

        executed_step = AgentStepService.execute_step(
            db=db,
            step_id=step.id,
            run_id=sample_run.id,
            project_id=sample_run.project_id,
            execution_fn=mock_execution,
        )

        assert executed_step.status == "completed"
        assert json.loads(executed_step.output_data) == {"result": "test output"}
        assert executed_step.prompt_tokens == 100
        assert executed_step.completion_tokens == 50
        assert executed_step.step_cost == 0.01
        assert executed_step.execution_time_ms is not None

    def test_execute_step_failure_transitions_to_failed(self, db: Session, sample_run: AgentRun):
        """Test that step execution failure marks step as failed"""
        step = AgentStepService.create_step(
            db=db,
            run_id=sample_run.id,
            project_id=sample_run.project_id,
            step_index=0,
            step_type="model_call",
        )

        # Mock execution function that raises error
        def mock_execution(input_data):
            raise RuntimeError("Model call failed")

        with pytest.raises(WorkflowError, match="Step .* execution failed"):
            AgentStepService.execute_step(
                db=db,
                step_id=step.id,
                run_id=sample_run.id,
                project_id=sample_run.project_id,
                execution_fn=mock_execution,
            )

        # Verify step is marked as failed
        db.refresh(step)
        assert step.status == "failed"
        assert "Model call failed" in step.error_message

    def test_execute_step_not_pending(self, db: Session, sample_run: AgentRun):
        """Test that executing non-pending step fails"""
        step = AgentStepService.create_step(
            db=db,
            run_id=sample_run.id,
            project_id=sample_run.project_id,
            step_index=0,
            step_type="model_call",
        )

        # Mark step as completed
        step.status = "completed"
        db.commit()

        def mock_execution(input_data):
            return ({}, 0, 0, 0.0)

        with pytest.raises(WorkflowError, match="Status: completed"):
            AgentStepService.execute_step(
                db=db,
                step_id=step.id,
                run_id=sample_run.id,
                project_id=sample_run.project_id,
                execution_fn=mock_execution,
            )


class TestRetryStep:
    """Tests for AgentStepService.retry_step"""

    def test_retry_step_success(self, db: Session, sample_run: AgentRun):
        """Test successful step retry"""
        step = AgentStepService.create_step(
            db=db,
            run_id=sample_run.id,
            project_id=sample_run.project_id,
            step_index=0,
            step_type="model_call",
            max_retries=3,
        )

        # Manually mark as failed
        step.status = "failed"
        step.error_message = "Transient error"
        db.commit()

        # Mock execution function
        def mock_execution(input_data):
            return (
                {"result": "recovered"},
                100,
                50,
                0.01
            )

        with patch('time.sleep'):  # Don't actually sleep in test
            retried_step = AgentStepService.retry_step(
                db=db,
                step_id=step.id,
                run_id=sample_run.id,
                project_id=sample_run.project_id,
                execution_fn=mock_execution,
            )

        assert retried_step.status == "completed"
        assert retried_step.retry_count == 1
        assert json.loads(retried_step.output_data) == {"result": "recovered"}

    def test_retry_step_not_failed(self, db: Session, sample_run: AgentRun):
        """Test that retrying non-failed step raises error"""
        step = AgentStepService.create_step(
            db=db,
            run_id=sample_run.id,
            project_id=sample_run.project_id,
            step_index=0,
            step_type="model_call",
        )

        def mock_execution(input_data):
            return ({}, 0, 0, 0.0)

        with pytest.raises(ValidationError, match="Expected: failed"):
            AgentStepService.retry_step(
                db=db,
                step_id=step.id,
                run_id=sample_run.id,
                project_id=sample_run.project_id,
                execution_fn=mock_execution,
            )

    def test_retry_step_max_retries_exceeded(self, db: Session, sample_run: AgentRun):
        """Test that retrying when max retries exceeded fails"""
        step = AgentStepService.create_step(
            db=db,
            run_id=sample_run.id,
            project_id=sample_run.project_id,
            step_index=0,
            step_type="model_call",
            max_retries=2,
        )

        # Simulate max retries exceeded
        step.status = "failed"
        step.retry_count = 2
        db.commit()

        def mock_execution(input_data):
            return ({}, 0, 0, 0.0)

        with pytest.raises(WorkflowError, match="exceeded max retries"):
            AgentStepService.retry_step(
                db=db,
                step_id=step.id,
                run_id=sample_run.id,
                project_id=sample_run.project_id,
                execution_fn=mock_execution,
            )

    def test_retry_step_exponential_backoff(self, db: Session, sample_run: AgentRun):
        """Test exponential backoff timing for retries"""
        from promptops_app.services.agent_step_service import (
            INITIAL_BACKOFF_MS, BACKOFF_MULTIPLIER, MAX_BACKOFF_MS
        )

        step = AgentStepService.create_step(
            db=db,
            run_id=sample_run.id,
            project_id=sample_run.project_id,
            step_index=0,
            step_type="model_call",
            max_retries=3,
        )

        step.status = "failed"
        db.commit()

        def mock_execution(input_data):
            return ({}, 0, 0, 0.0)

        # Test that backoff increases correctly
        # Retry 0: 50ms
        expected_backoff = INITIAL_BACKOFF_MS

        with patch('time.sleep') as mock_sleep:
            AgentStepService.retry_step(
                db=db,
                step_id=step.id,
                run_id=sample_run.id,
                project_id=sample_run.project_id,
                execution_fn=mock_execution,
            )

            # Verify sleep was called with backoff time
            mock_sleep.assert_called_once()
            sleep_duration = mock_sleep.call_args[0][0]
            # Should be close to expected_backoff (50ms) with jitter
            assert 0.035 < sleep_duration < 0.065  # 50ms ± 10%

    def test_retry_step_incremental_retry_count(self, db: Session, sample_run: AgentRun):
        """Test that retry_count increments correctly"""
        step = AgentStepService.create_step(
            db=db,
            run_id=sample_run.id,
            project_id=sample_run.project_id,
            step_index=0,
            step_type="model_call",
            max_retries=3,
        )

        step.status = "failed"
        step.retry_count = 0
        db.commit()

        def mock_execution(input_data):
            raise RuntimeError("Still failing")

        with patch('time.sleep'):
            with pytest.raises(WorkflowError):
                AgentStepService.retry_step(
                    db=db,
                    step_id=step.id,
                    run_id=sample_run.id,
                    project_id=sample_run.project_id,
                    execution_fn=mock_execution,
                )

        db.refresh(step)
        assert step.retry_count == 1
        assert step.status == "failed"


class TestGetStepData:
    """Tests for AgentStepService.get_step_data"""

    def test_get_step_data_pending(self, db: Session, sample_run: AgentRun):
        """Test retrieving data for pending step"""
        input_data = {"query": "test"}
        step = AgentStepService.create_step(
            db=db,
            run_id=sample_run.id,
            project_id=sample_run.project_id,
            step_index=0,
            step_type="retrieval",
            input_data=input_data,
        )

        step_data = AgentStepService.get_step_data(
            db=db,
            step_id=step.id,
            run_id=sample_run.id,
            project_id=sample_run.project_id,
        )

        assert step_data["step_id"] == step.id
        assert step_data["step_type"] == "retrieval"
        assert step_data["status"] == "pending"
        assert step_data["input_data"] == input_data
        assert step_data["output_data"] is None

    def test_get_step_data_completed(self, db: Session, sample_run: AgentRun):
        """Test retrieving data for completed step with output"""
        step = AgentStepService.create_step(
            db=db,
            run_id=sample_run.id,
            project_id=sample_run.project_id,
            step_index=0,
            step_type="model_call",
        )

        # Transition to running first
        step.status = "running"
        step.started_at = datetime.utcnow()
        db.commit()

        output_data = {"generated": "content"}
        AgentStepService.mark_step_complete(
            db=db,
            step_id=step.id,
            run_id=sample_run.id,
            project_id=sample_run.project_id,
            output_data=output_data,
            prompt_tokens=100,
            completion_tokens=50,
            step_cost=0.01,
        )

        step_data = AgentStepService.get_step_data(
            db=db,
            step_id=step.id,
            run_id=sample_run.id,
            project_id=sample_run.project_id,
        )

        assert step_data["status"] == "completed"
        assert step_data["output_data"] == output_data
        assert step_data["tokens"]["prompt_tokens"] == 100
        assert step_data["tokens"]["completion_tokens"] == 50
        assert step_data["cost"]["step_cost"] == 0.01

    def test_get_step_data_not_found(self, db: Session, sample_run: AgentRun):
        """Test that retrieving non-existent step raises NotFoundError"""
        with pytest.raises(NotFoundError):
            AgentStepService.get_step_data(
                db=db,
                step_id=99999,
                run_id=sample_run.id,
                project_id=sample_run.project_id,
            )


class TestMarkStepComplete:
    """Tests for AgentStepService.mark_step_complete"""

    def test_mark_step_complete_success(self, db: Session, sample_run: AgentRun):
        """Test marking running step as complete"""
        step = AgentStepService.create_step(
            db=db,
            run_id=sample_run.id,
            project_id=sample_run.project_id,
            step_index=0,
            step_type="model_call",
        )

        # Transition to running first
        step.status = "running"
        step.started_at = datetime.utcnow()
        db.commit()

        output_data = {"result": "success"}
        completed_step = AgentStepService.mark_step_complete(
            db=db,
            step_id=step.id,
            run_id=sample_run.id,
            project_id=sample_run.project_id,
            output_data=output_data,
            prompt_tokens=100,
            completion_tokens=50,
            step_cost=0.015,
        )

        assert completed_step.status == "completed"
        assert json.loads(completed_step.output_data) == output_data
        assert completed_step.prompt_tokens == 100
        assert completed_step.completion_tokens == 50
        assert completed_step.step_cost == 0.015
        assert completed_step.execution_time_ms is not None

    def test_mark_step_complete_not_running(self, db: Session, sample_run: AgentRun):
        """Test that marking non-running step complete fails"""
        step = AgentStepService.create_step(
            db=db,
            run_id=sample_run.id,
            project_id=sample_run.project_id,
            step_index=0,
            step_type="model_call",
        )

        with pytest.raises(WorkflowError, match="Status: pending"):
            AgentStepService.mark_step_complete(
                db=db,
                step_id=step.id,
                run_id=sample_run.id,
                project_id=sample_run.project_id,
            )


class TestMarkStepFailed:
    """Tests for AgentStepService.mark_step_failed"""

    def test_mark_step_failed_retriable(self, db: Session, sample_run: AgentRun):
        """Test marking step failed with retry available"""
        step = AgentStepService.create_step(
            db=db,
            run_id=sample_run.id,
            project_id=sample_run.project_id,
            step_index=0,
            step_type="model_call",
            max_retries=3,
        )

        # Transition to running
        step.status = "running"
        step.started_at = datetime.utcnow()
        db.commit()

        failed_step = AgentStepService.mark_step_failed(
            db=db,
            step_id=step.id,
            run_id=sample_run.id,
            project_id=sample_run.project_id,
            error_message="Transient error",
            should_retry=True,
        )

        assert failed_step.status == "failed"
        assert failed_step.error_message == "Transient error"
        # Should not have completed_at since we're going to retry
        assert failed_step.completed_at is None

    def test_mark_step_failed_terminal(self, db: Session, sample_run: AgentRun):
        """Test marking step failed with no retries available"""
        step = AgentStepService.create_step(
            db=db,
            run_id=sample_run.id,
            project_id=sample_run.project_id,
            step_index=0,
            step_type="model_call",
            max_retries=1,
        )

        # Simulate max retries already used
        step.status = "running"
        step.retry_count = 1
        step.started_at = datetime.utcnow()
        db.commit()

        failed_step = AgentStepService.mark_step_failed(
            db=db,
            step_id=step.id,
            run_id=sample_run.id,
            project_id=sample_run.project_id,
            error_message="Max retries exceeded",
            should_retry=True,
        )

        assert failed_step.status == "failed"
        # Should have completed_at since no more retries
        assert failed_step.completed_at is not None
        assert failed_step.execution_time_ms is not None

    def test_mark_step_failed_not_running(self, db: Session, sample_run: AgentRun):
        """Test that marking non-running step failed fails"""
        step = AgentStepService.create_step(
            db=db,
            run_id=sample_run.id,
            project_id=sample_run.project_id,
            step_index=0,
            step_type="model_call",
        )

        with pytest.raises(WorkflowError, match="Status: pending"):
            AgentStepService.mark_step_failed(
                db=db,
                step_id=step.id,
                run_id=sample_run.id,
                project_id=sample_run.project_id,
                error_message="Error",
            )


class TestListSteps:
    """Tests for AgentStepService.list_steps"""

    def test_list_steps_all(self, db: Session, sample_run: AgentRun):
        """Test listing all steps for a run"""
        # Create multiple steps
        for i in range(3):
            AgentStepService.create_step(
                db=db,
                run_id=sample_run.id,
                project_id=sample_run.project_id,
                step_index=i,
                step_type="model_call",
            )

        steps, total_count = AgentStepService.list_steps(
            db=db,
            run_id=sample_run.id,
            project_id=sample_run.project_id,
        )

        assert total_count == 3
        assert len(steps) == 3
        assert all(s.run_id == sample_run.id for s in steps)

    def test_list_steps_by_status(self, db: Session, sample_run: AgentRun):
        """Test listing steps filtered by status"""
        step1 = AgentStepService.create_step(
            db=db,
            run_id=sample_run.id,
            project_id=sample_run.project_id,
            step_index=0,
            step_type="model_call",
        )

        step2 = AgentStepService.create_step(
            db=db,
            run_id=sample_run.id,
            project_id=sample_run.project_id,
            step_index=1,
            step_type="model_call",
        )

        # Mark first step as completed
        step1.status = "completed"
        db.commit()

        # List pending steps only
        pending_steps, pending_count = AgentStepService.list_steps(
            db=db,
            run_id=sample_run.id,
            project_id=sample_run.project_id,
            status="pending",
        )

        assert pending_count == 1
        assert len(pending_steps) == 1
        assert pending_steps[0].id == step2.id

    def test_list_steps_pagination(self, db: Session, sample_run: AgentRun):
        """Test pagination of step listing"""
        # Create 5 steps
        for i in range(5):
            AgentStepService.create_step(
                db=db,
                run_id=sample_run.id,
                project_id=sample_run.project_id,
                step_index=i,
                step_type="model_call",
            )

        # Get first 2
        steps1, _ = AgentStepService.list_steps(
            db=db,
            run_id=sample_run.id,
            project_id=sample_run.project_id,
            limit=2,
            offset=0,
        )

        # Get next 2
        steps2, _ = AgentStepService.list_steps(
            db=db,
            run_id=sample_run.id,
            project_id=sample_run.project_id,
            limit=2,
            offset=2,
        )

        assert len(steps1) == 2
        assert len(steps2) == 2
        assert steps1[0].step_index == 0
        assert steps2[0].step_index == 2


class TestTenantIsolation:
    """Tests for tenant isolation in step operations"""

    def test_step_wrong_tenant_step_creation(self, db: Session, sample_run: AgentRun):
        """Test that steps can't be created for runs in different tenants"""
        with pytest.raises(NotFoundError):
            AgentStepService.create_step(
                db=db,
                run_id=sample_run.id,
                project_id=99999,  # Different project
                step_index=0,
                step_type="model_call",
            )

    def test_step_wrong_tenant_retrieval(self, db: Session, sample_run: AgentRun):
        """Test that steps can't be retrieved across tenant boundaries"""
        step = AgentStepService.create_step(
            db=db,
            run_id=sample_run.id,
            project_id=sample_run.project_id,
            step_index=0,
            step_type="model_call",
        )

        with pytest.raises(NotFoundError):
            AgentStepService.get_step_data(
                db=db,
                step_id=step.id,
                run_id=sample_run.id,
                project_id=99999,  # Different project
            )

    def test_list_steps_only_current_tenant(self, db: Session, sample_run: AgentRun):
        """Test that listing steps is tenant-scoped"""
        AgentStepService.create_step(
            db=db,
            run_id=sample_run.id,
            project_id=sample_run.project_id,
            step_index=0,
            step_type="model_call",
        )

        with pytest.raises(NotFoundError):
            AgentStepService.list_steps(
                db=db,
                run_id=sample_run.id,
                project_id=99999,  # Different project
            )


# Fixtures

@pytest.fixture
def sample_project(db: Session):
    """Create a sample project for testing"""
    from promptops_app.database import Project

    project = Project(
        name="Test Project",
        slug="test-project",
        is_active=True,
        status="active",
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@pytest.fixture
def sample_agent(db: Session, sample_project):
    """Create a sample agent for testing"""
    from promptops_app.agents_models import AgentTemplate

    # Create a template first
    template = AgentTemplate(
        key="test_template",
        name="Test Template",
        specialization="content_creation",
        instructions="Test instructions",
        input_schema="{}",
        output_schema="{}",
        owner="test_admin",
        version=1,
        is_active=True,
    )
    db.add(template)
    db.commit()

    # Create agent from template
    agent = AgentDefinition(
        project_id=sample_project.id,
        template_id=template.id,
        name="Test Agent",
        call_handle="test_agent",
        owner="test_admin",
        configuration="{}",
        lifecycle_state="active",
        created_by="test_admin",
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent


@pytest.fixture
def sample_run(db: Session, sample_project, sample_agent):
    """Create a sample run for testing"""
    from promptops_app.agents_models import AgentDefinitionVersion

    # Get or create active version
    version = db.query(AgentDefinitionVersion).filter(
        AgentDefinitionVersion.definition_id == sample_agent.id
    ).first()

    if not version:
        version = AgentDefinitionVersion(
            definition_id=sample_agent.id,
            version_number=1,
            configuration="{}",
            is_active=True,
            created_by="test_admin",
        )
        db.add(version)
        db.commit()

    run = AgentRun(
        project_id=sample_project.id,
        definition_id=sample_agent.id,
        version_id=version.id,
        initiated_by="test_admin",
        initiated_by_role="admin",
        state="running",
        created_at=datetime.utcnow(),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run
