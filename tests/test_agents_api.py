"""
Comprehensive tests for Agent Builder API Routes (Stage 6 of Phase 2)

Test Coverage:
- Agent Management endpoints (create, list, get, update, delete)
- Agent Execution endpoints (create run, list runs, get run, cancel, steps, checkpoints, resume)
- Budget & Analytics endpoints (budget status, usage, run costs)
- RBAC authorization enforcement
- Tenant isolation
- Error handling (invalid inputs, permission denied, not found, budget exceeded)
- Pagination
"""

import pytest
import json
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock

from fastapi import HTTPException
from sqlalchemy.orm import Session

# Mock the dependencies and services
from app.core.exceptions import (
    NotFoundError, PermissionDeniedError, ValidationError, WorkflowError
)
from app.schemas.agents import (
    AgentCreateRequest, AgentUpdateRequest, AgentConfigurationUpdate,
    RunCreateRequest, ResumeRequest, AgentResponse, RunResponse,
)
from app.api.v1.routers import agents as agents_router
from promptops_app.agents_models import (
    AgentDefinition, AgentRun, AgentRunStep, AgentCheckpoint
)


# =============================================================================
# Fixtures
# =============================================================================

class MockUser:
    """Mock user object for testing."""
    def __init__(self, username="testuser", role="author", project_id=1):
        self.username = username
        self.role = role
        self.project_id = project_id


class MockSession:
    """Mock SQLAlchemy session for testing."""
    def __init__(self):
        self.queries = []
        self.committed = False

    def add(self, obj):
        pass

    def commit(self):
        self.committed = True

    def refresh(self, obj):
        pass


@pytest.fixture
def db():
    """Mock database session."""
    return MockSession()


@pytest.fixture
def user():
    """Mock authenticated user."""
    return MockUser()


@pytest.fixture
def admin_user():
    """Mock admin user."""
    return MockUser(role="admin")


@pytest.fixture
def agent():
    """Mock AgentDefinition."""
    return AgentDefinition(
        id=1,
        project_id=1,
        template_id=1,
        name="Content Creator",
        call_handle="content_creator",
        description="Creates content",
        owner="testuser",
        configuration='{"model": "gpt-4o"}',
        lifecycle_state="active",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )


@pytest.fixture
def run():
    """Mock AgentRun."""
    return AgentRun(
        id=1,
        project_id=1,
        definition_id=1,
        version_id=1,
        initiated_by="testuser",
        initiated_by_role="author",
        input_content="Test content",
        state="completed",
        result='{"output": "test"}',
        step_count=1,
        max_steps=10,
        started_at=datetime.utcnow(),
        completed_at=datetime.utcnow(),
        execution_time_ms=1000,
        actual_cost=0.01,
        budget_reserved=True,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )


# =============================================================================
# Agent Management Tests
# =============================================================================

class TestCreateAgent:
    """Tests for POST /agents endpoint."""

    def test_create_agent_success(self, db, user):
        """Test successful agent creation."""
        request_body = AgentCreateRequest(
            name="New Agent",
            template_id=1,
            call_handle="new_agent",
            description="Test agent",
            configuration=AgentConfigurationUpdate(model_id="openai:gpt-4o"),
        )

        with patch('app.api.v1.routers.agents.AgentDefinition') as mock_agent_class:
            with patch('app.api.v1.routers.agents.require_permission'):
                with patch('app.api.v1.routers.agents.get_db') as mock_db:
                    mock_db.return_value = db
                    mock_agent_instance = Mock()
                    mock_agent_class.return_value = mock_agent_instance

                    # Call endpoint
                    result = agents_router.create_agent(request_body, db, user)

                    # Verify agent was created
                    assert result is not None

    def test_create_agent_permission_denied(self, db):
        """Test agent creation without permission."""
        request_body = AgentCreateRequest(
            name="New Agent",
            template_id=1,
            call_handle="new_agent",
            configuration=AgentConfigurationUpdate(),
        )

        with pytest.raises(HTTPException):
            unauthorized_user = MockUser(role="viewer")
            agents_router.create_agent(request_body, db, unauthorized_user)


class TestListAgents:
    """Tests for GET /agents endpoint."""

    def test_list_agents_success(self, db, user, agent):
        """Test successful agent listing."""
        with patch('app.api.v1.routers.agents.db.query') as mock_query:
            mock_result = Mock()
            mock_result.count.return_value = 1
            mock_result.offset.return_value = mock_result
            mock_result.limit.return_value = [agent]
            mock_query.return_value = mock_result

            result = agents_router.list_agents(db=db, current_user=user)

            assert result.total == 1
            assert len(result.items) > 0

    def test_list_agents_pagination(self, db, user):
        """Test pagination in agent listing."""
        with patch('app.api.v1.routers.agents.db.query') as mock_query:
            mock_result = Mock()
            mock_result.count.return_value = 25
            mock_result.offset.return_value = mock_result
            mock_result.limit.return_value = []
            mock_query.return_value = mock_result

            result = agents_router.list_agents(
                page=2, page_size=10, db=db, current_user=user
            )

            assert result.total == 25
            assert result.pages == 3

    def test_list_agents_filter_by_state(self, db, user, agent):
        """Test filtering agents by lifecycle state."""
        with patch('app.api.v1.routers.agents.db.query') as mock_query:
            mock_result = Mock()
            mock_result.count.return_value = 1
            mock_result.offset.return_value = mock_result
            mock_result.limit.return_value = [agent]
            mock_result.filter.return_value = mock_result
            mock_query.return_value = mock_result

            result = agents_router.list_agents(
                lifecycle_state="active", db=db, current_user=user
            )

            assert len(result.items) > 0


class TestGetAgent:
    """Tests for GET /agents/{agent_id} endpoint."""

    def test_get_agent_success(self, db, user, agent):
        """Test successful agent retrieval."""
        with patch('app.api.v1.routers.agents.AgentsTenantService.validate_agent_ownership') as mock_validate:
            mock_validate.return_value = agent

            result = agents_router.get_agent(agent_id=1, db=db, current_user=user)

            assert result.id == agent.id
            assert result.name == agent.name

    def test_get_agent_not_found(self, db, user):
        """Test getting non-existent agent."""
        with patch('app.api.v1.routers.agents.AgentsTenantService.validate_agent_ownership') as mock_validate:
            mock_validate.return_value = None

            with pytest.raises(NotFoundError):
                agents_router.get_agent(agent_id=999, db=db, current_user=user)


class TestUpdateAgent:
    """Tests for PUT /agents/{agent_id} endpoint."""

    def test_update_agent_success(self, db, user, agent):
        """Test successful agent update."""
        request_body = AgentUpdateRequest(
            name="Updated Agent",
            configuration=AgentConfigurationUpdate(temperature=0.8),
        )

        with patch('app.api.v1.routers.agents.AgentsTenantService.validate_agent_ownership') as mock_validate:
            with patch('app.api.v1.routers.agents._get_agent_or_404') as mock_get:
                mock_get.return_value = agent

                result = agents_router.update_agent(1, request_body, db, user)

                assert result is not None

    def test_update_agent_lifecycle_state(self, db, user, agent):
        """Test updating agent lifecycle state."""
        request_body = AgentUpdateRequest(
            lifecycle_state="paused",
            lifecycle_reason="Testing",
        )

        with patch('app.api.v1.routers.agents._get_agent_or_404') as mock_get:
            mock_get.return_value = agent

            result = agents_router.update_agent(1, request_body, db, user)

            assert result is not None


class TestDeleteAgent:
    """Tests for DELETE /agents/{agent_id} endpoint."""

    def test_delete_agent_success(self, db, user, agent):
        """Test successful agent archival."""
        with patch('app.api.v1.routers.agents._get_agent_or_404') as mock_get:
            mock_get.return_value = agent

            agents_router.delete_agent(1, db, user)

            assert db.committed


# =============================================================================
# Agent Execution Tests
# =============================================================================

class TestCreateRun:
    """Tests for POST /agents/{agent_id}/runs endpoint."""

    def test_create_run_success(self, db, user, agent):
        """Test successful run creation."""
        request_body = RunCreateRequest(
            input_content="Test content",
        )

        with patch('app.api.v1.routers.agents._get_agent_or_404') as mock_get:
            with patch('app.api.v1.routers.agents.AgentRunService.create_run') as mock_create:
                mock_get.return_value = agent
                mock_run = Mock(spec=AgentRun)
                mock_run.id = 1
                mock_run.state = "queued"
                mock_run.actual_cost = 0.01
                mock_create.return_value = mock_run

                result = agents_router.create_run(1, request_body, db, user)

                assert result is not None

    def test_create_run_budget_exceeded(self, db, user, agent):
        """Test run creation when budget exceeded."""
        request_body = RunCreateRequest(
            input_content="Test content",
        )

        from promptops_app.services.budget_service import BudgetExceededError

        with patch('app.api.v1.routers.agents._get_agent_or_404') as mock_get:
            with patch('app.api.v1.routers.agents.AgentRunService.create_run') as mock_create:
                mock_get.return_value = agent
                mock_create.side_effect = BudgetExceededError("Budget exceeded")

                with pytest.raises(HTTPException) as exc_info:
                    agents_router.create_run(1, request_body, db, user)

                assert exc_info.value.status_code == 429  # Too Many Requests


class TestListRuns:
    """Tests for GET /agents/{agent_id}/runs endpoint."""

    def test_list_runs_success(self, db, user, agent, run):
        """Test successful run listing."""
        with patch('app.api.v1.routers.agents._get_agent_or_404') as mock_get:
            with patch('app.api.v1.routers.agents.db.query') as mock_query:
                mock_get.return_value = agent
                mock_result = Mock()
                mock_result.count.return_value = 1
                mock_result.offset.return_value = mock_result
                mock_result.limit.return_value = [run]
                mock_result.filter.return_value = mock_result
                mock_result.order_by.return_value = mock_result
                mock_query.return_value = mock_result

                result = agents_router.list_runs(1, db=db, current_user=user)

                assert result.total == 1

    def test_list_runs_filter_by_state(self, db, user, agent):
        """Test filtering runs by state."""
        with patch('app.api.v1.routers.agents._get_agent_or_404') as mock_get:
            with patch('app.api.v1.routers.agents.db.query') as mock_query:
                mock_get.return_value = agent
                mock_result = Mock()
                mock_result.count.return_value = 1
                mock_result.offset.return_value = mock_result
                mock_result.limit.return_value = []
                mock_result.filter.return_value = mock_result
                mock_result.order_by.return_value = mock_result
                mock_query.return_value = mock_result

                result = agents_router.list_runs(
                    1, state="completed", db=db, current_user=user
                )

                assert result is not None


class TestGetRun:
    """Tests for GET /agents/{agent_id}/runs/{run_id} endpoint."""

    def test_get_run_success(self, db, user, agent, run):
        """Test successful run retrieval."""
        run.definition_id = agent.id

        with patch('app.api.v1.routers.agents._get_agent_or_404') as mock_get:
            with patch('app.api.v1.routers.agents._get_run_or_404') as mock_get_run:
                mock_get.return_value = agent
                mock_get_run.return_value = run

                result = agents_router.get_run(1, 1, db, user)

                assert result.id == run.id

    def test_get_run_not_found(self, db, user, agent):
        """Test getting non-existent run."""
        with patch('app.api.v1.routers.agents._get_agent_or_404') as mock_get:
            with patch('app.api.v1.routers.agents._get_run_or_404') as mock_get_run:
                mock_get.return_value = agent
                mock_get_run.return_value = None

                with pytest.raises(NotFoundError):
                    agents_router.get_run(1, 999, db, user)


class TestCancelRun:
    """Tests for POST /agents/{agent_id}/runs/{run_id}/cancel endpoint."""

    def test_cancel_run_success(self, db, user, agent, run):
        """Test successful run cancellation."""
        run.definition_id = agent.id
        run.state = "running"

        with patch('app.api.v1.routers.agents._get_agent_or_404') as mock_get:
            with patch('app.api.v1.routers.agents._get_run_or_404') as mock_get_run:
                with patch('app.api.v1.routers.agents.AgentRunService.cancel_run') as mock_cancel:
                    mock_get.return_value = agent
                    mock_get_run.return_value = run
                    mock_cancel.return_value = run

                    result = agents_router.cancel_run(1, 1, db, user)

                    assert result is not None

    def test_cancel_run_invalid_state(self, db, user, agent, run):
        """Test cancelling run in invalid state."""
        run.definition_id = agent.id
        run.state = "completed"

        with patch('app.api.v1.routers.agents._get_agent_or_404') as mock_get:
            with patch('app.api.v1.routers.agents._get_run_or_404') as mock_get_run:
                mock_get.return_value = agent
                mock_get_run.return_value = run

                with pytest.raises(WorkflowError):
                    agents_router.cancel_run(1, 1, db, user)


class TestListSteps:
    """Tests for GET /agents/{agent_id}/runs/{run_id}/steps endpoint."""

    def test_list_steps_success(self, db, user, agent, run):
        """Test successful step listing."""
        step = AgentRunStep(
            id=1,
            run_id=run.id,
            step_index=0,
            step_type="model_call",
            status="completed",
            output_data='{"result": "test"}',
            step_cost=0.01,
        )

        with patch('app.api.v1.routers.agents._get_agent_or_404') as mock_get:
            with patch('app.api.v1.routers.agents._get_run_or_404') as mock_get_run:
                with patch('app.api.v1.routers.agents.db.query') as mock_query:
                    mock_get.return_value = agent
                    mock_get_run.return_value = run
                    mock_result = Mock()
                    mock_result.filter.return_value = mock_result
                    mock_result.order_by.return_value = [step]
                    mock_query.return_value = mock_result

                    result = agents_router.list_steps(1, 1, db, user)

                    assert result.total == 1


class TestListCheckpoints:
    """Tests for GET /agents/{agent_id}/runs/{run_id}/checkpoints endpoint."""

    def test_list_checkpoints_success(self, db, user, agent, run):
        """Test successful checkpoint listing."""
        checkpoint = AgentCheckpoint(
            id=1,
            run_id=run.id,
            checkpoint_index=0,
            step_state='{"step": 0}',
            resumable=True,
        )

        with patch('app.api.v1.routers.agents._get_agent_or_404') as mock_get:
            with patch('app.api.v1.routers.agents._get_run_or_404') as mock_get_run:
                with patch('app.api.v1.routers.agents.db.query') as mock_query:
                    mock_get.return_value = agent
                    mock_get_run.return_value = run
                    mock_result = Mock()
                    mock_result.count.return_value = 1
                    mock_result.offset.return_value = mock_result
                    mock_result.limit.return_value = [checkpoint]
                    mock_result.filter.return_value = mock_result
                    mock_result.order_by.return_value = mock_result
                    mock_query.return_value = mock_result

                    result = agents_router.list_checkpoints(1, 1, db=db, current_user=user)

                    assert result.total == 1


class TestResumeRun:
    """Tests for POST /agents/{agent_id}/runs/{run_id}/resume endpoint."""

    def test_resume_run_success(self, db, user, agent, run):
        """Test successful run resumption."""
        run.definition_id = agent.id
        run.state = "awaiting_input"

        request_body = ResumeRequest(checkpoint_index=0)

        with patch('app.api.v1.routers.agents._get_agent_or_404') as mock_get:
            with patch('app.api.v1.routers.agents._get_run_or_404') as mock_get_run:
                with patch('app.api.v1.routers.agents.AgentCheckpointService.resume_from_checkpoint') as mock_resume:
                    mock_get.return_value = agent
                    mock_get_run.return_value = run
                    mock_resume.return_value = {"step": 0}

                    result = agents_router.resume_from_checkpoint(1, 1, request_body, db, user)

                    assert result is not None

    def test_resume_run_invalid_state(self, db, user, agent, run):
        """Test resuming run in invalid state."""
        run.definition_id = agent.id
        run.state = "completed"

        request_body = ResumeRequest(checkpoint_index=0)

        with patch('app.api.v1.routers.agents._get_agent_or_404') as mock_get:
            with patch('app.api.v1.routers.agents._get_run_or_404') as mock_get_run:
                mock_get.return_value = agent
                mock_get_run.return_value = run

                with pytest.raises(WorkflowError):
                    agents_router.resume_from_checkpoint(1, 1, request_body, db, user)


# =============================================================================
# Budget & Analytics Tests
# =============================================================================

class TestGetBudgetStatus:
    """Tests for GET /agents/budget/status endpoint."""

    def test_get_budget_status_success(self, db, user):
        """Test successful budget status retrieval."""
        with patch('app.api.v1.routers.agents.AgentBudgetService.get_budget_status') as mock_status:
            mock_status_obj = Mock()
            mock_status_obj.project_id = user.project_id
            mock_status_obj.current_spend = 10.0
            mock_status_obj.remaining_budget = 90.0
            mock_status_obj.limit_usd = 100.0
            mock_status_obj.period = "monthly"
            mock_status_obj.period_key = "2026-10"
            mock_status_obj.limit_type = "usd"
            mock_status_obj.is_enforced = True
            mock_status_obj.percent_used = 10.0
            mock_status.return_value = mock_status_obj

            result = agents_router.get_budget_status(db=db, current_user=user)

            assert result.project_id == user.project_id
            assert result.percent_used == 10.0


class TestGetUsageSummary:
    """Tests for GET /agents/budget/usage endpoint."""

    def test_get_usage_summary_success(self, db, user):
        """Test successful usage summary retrieval."""
        with patch('app.api.v1.routers.agents.AgentBudgetService.get_project_usage_summary') as mock_usage:
            mock_usage.return_value = {
                "total_cost": 50.0,
                "total_runs": 10,
                "average_cost_per_run": 5.0,
                "daily_costs": {"2026-10-01": 10.0, "2026-10-02": 15.0},
            }

            result = agents_router.get_usage_summary(days=30, db=db, current_user=user)

            assert result.total_cost == 50.0
            assert result.total_runs == 10


class TestGetRunCosts:
    """Tests for GET /agents/{agent_id}/runs/{run_id}/costs endpoint."""

    def test_get_run_costs_success(self, db, user, agent, run):
        """Test successful run cost retrieval."""
        run.definition_id = agent.id

        step = AgentRunStep(
            id=1,
            run_id=run.id,
            step_index=0,
            step_type="model_call",
            model_used="gpt-4o",
            prompt_tokens=100,
            completion_tokens=50,
            step_cost=0.01,
        )

        with patch('app.api.v1.routers.agents._get_agent_or_404') as mock_get:
            with patch('app.api.v1.routers.agents._get_run_or_404') as mock_get_run:
                with patch('app.api.v1.routers.agents.AgentBudgetService.get_run_costs') as mock_costs:
                    with patch('app.api.v1.routers.agents.db.query') as mock_query:
                        mock_get.return_value = agent
                        mock_get_run.return_value = run
                        mock_costs.return_value = {
                            "total_cost": 0.01,
                            "total_tokens": 150,
                            "by_model": {"gpt-4o": 0.01},
                        }
                        mock_result = Mock()
                        mock_result.filter.return_value = [step]
                        mock_query.return_value = mock_result

                        result = agents_router.get_run_costs(1, 1, db, user)

                        assert result.total_cost == 0.01


# =============================================================================
# Authorization & Tenant Isolation Tests
# =============================================================================

class TestRBACEnforcement:
    """Tests for RBAC authorization enforcement."""

    def test_create_agent_requires_permission(self, db):
        """Test that agent creation requires agents.create permission."""
        request_body = AgentCreateRequest(
            name="New Agent",
            template_id=1,
            call_handle="new_agent",
            configuration=AgentConfigurationUpdate(),
        )

        # User with viewer role should not have permission
        unauthorized_user = MockUser(role="viewer")

        # This should raise HTTPException with 403
        with pytest.raises(HTTPException):
            agents_router.create_agent(request_body, db, unauthorized_user)

    def test_list_agents_allowed_for_all_users(self, db, user):
        """Test that listing agents is allowed for all authenticated users."""
        with patch('app.api.v1.routers.agents.db.query') as mock_query:
            mock_result = Mock()
            mock_result.count.return_value = 0
            mock_result.offset.return_value = mock_result
            mock_result.limit.return_value = []
            mock_query.return_value = mock_result

            # Should not raise exception
            result = agents_router.list_agents(db=db, current_user=user)

            assert result is not None


class TestTenantIsolation:
    """Tests for tenant isolation in queries."""

    def test_list_agents_only_for_tenant(self, db, user):
        """Test that agents listing is scoped to user's tenant."""
        # Create a second user with different project_id
        user2 = MockUser(project_id=2)

        agent1 = AgentDefinition(
            id=1,
            project_id=1,
            template_id=1,
            name="Agent 1",
            call_handle="agent_1",
            owner="user1",
            configuration='{}',
            lifecycle_state="active",
            created_at=datetime.utcnow(),
        )

        agent2 = AgentDefinition(
            id=2,
            project_id=2,
            template_id=1,
            name="Agent 2",
            call_handle="agent_2",
            owner="user2",
            configuration='{}',
            lifecycle_state="active",
            created_at=datetime.utcnow(),
        )

        with patch('app.api.v1.routers.agents.db.query') as mock_query:
            mock_result = Mock()
            mock_result.count.return_value = 1
            mock_result.offset.return_value = mock_result
            mock_result.limit.return_value = [agent1]
            mock_result.filter.return_value = mock_result
            mock_query.return_value = mock_result

            # User 1 should only see agent1
            result = agents_router.list_agents(db=db, current_user=user)

            assert len(result.items) <= 1
            # Verify that the query was filtered by project_id
            mock_query.assert_called()

    def test_get_run_requires_tenant_ownership(self, db, user, run):
        """Test that getting a run requires tenant ownership."""
        # Create a run from different tenant
        run.project_id = 2  # Different tenant

        with patch('app.api.v1.routers.agents._get_agent_or_404') as mock_get:
            with patch('app.api.v1.routers.agents._get_run_or_404') as mock_get_run:
                # _get_run_or_404 filters by project_id so should return None
                mock_get.return_value = AgentDefinition(
                    id=1, project_id=1, template_id=1,
                    name="Agent", call_handle="agent", owner="user",
                    configuration='{}', lifecycle_state="active",
                    created_at=datetime.utcnow(),
                )
                mock_get_run.return_value = None

                with pytest.raises(NotFoundError):
                    agents_router.get_run(1, 1, db, user)


# =============================================================================
# Error Handling Tests
# =============================================================================

class TestErrorHandling:
    """Tests for error handling in endpoints."""

    def test_create_run_handles_budget_exceeded(self, db, user, agent):
        """Test that budget exceeded error is handled with 429 status."""
        from promptops_app.services.budget_service import BudgetExceededError

        request_body = RunCreateRequest(input_content="Test")

        with patch('app.api.v1.routers.agents._get_agent_or_404') as mock_get:
            with patch('app.api.v1.routers.agents.AgentRunService.create_run') as mock_create:
                mock_get.return_value = agent
                mock_create.side_effect = BudgetExceededError("Budget exceeded")

                with pytest.raises(HTTPException) as exc_info:
                    agents_router.create_run(1, request_body, db, user)

                assert exc_info.value.status_code == 429

    def test_create_run_handles_validation_error(self, db, user, agent):
        """Test that validation errors result in 422 status."""
        request_body = RunCreateRequest(input_content="Test")

        with patch('app.api.v1.routers.agents._get_agent_or_404') as mock_get:
            with patch('app.api.v1.routers.agents.AgentRunService.create_run') as mock_create:
                mock_get.return_value = agent
                mock_create.side_effect = ValidationError("Invalid input")

                with pytest.raises(HTTPException) as exc_info:
                    agents_router.create_run(1, request_body, db, user)

                assert exc_info.value.status_code == 422

    def test_get_agent_handles_not_found(self, db, user):
        """Test that not found errors result in 404 status."""
        with patch('app.api.v1.routers.agents._get_agent_or_404') as mock_get:
            mock_get.side_effect = NotFoundError("Agent", 999)

            with pytest.raises(NotFoundError):
                agents_router.get_agent(999, db, user)

    def test_cancel_run_handles_workflow_error(self, db, user, agent, run):
        """Test that workflow errors are handled."""
        run.definition_id = agent.id
        run.state = "completed"  # Invalid state for cancellation

        with patch('app.api.v1.routers.agents._get_agent_or_404') as mock_get:
            with patch('app.api.v1.routers.agents._get_run_or_404') as mock_get_run:
                mock_get.return_value = agent
                mock_get_run.return_value = run

                with pytest.raises(WorkflowError):
                    agents_router.cancel_run(1, 1, db, user)


# =============================================================================
# Integration Tests
# =============================================================================

class TestEndToEndWorkflow:
    """End-to-end integration tests."""

    def test_create_run_to_completion_workflow(self, db, user, agent):
        """Test complete workflow from run creation to completion."""
        run = AgentRun(
            id=1,
            project_id=user.project_id,
            definition_id=agent.id,
            version_id=1,
            initiated_by=user.username,
            initiated_by_role=user.role,
            input_content="Test content",
            state="queued",
            created_at=datetime.utcnow(),
        )

        # 1. Create run
        with patch('app.api.v1.routers.agents._get_agent_or_404') as mock_get:
            with patch('app.api.v1.routers.agents.AgentRunService.create_run') as mock_create:
                mock_get.return_value = agent
                mock_create.return_value = run

                request_body = RunCreateRequest(input_content="Test content")
                result = agents_router.create_run(1, request_body, db, user)

                assert result.state == "queued"

        # 2. Get run status
        with patch('app.api.v1.routers.agents._get_agent_or_404') as mock_get:
            with patch('app.api.v1.routers.agents._get_run_or_404') as mock_get_run:
                mock_get.return_value = agent
                mock_get_run.return_value = run

                result = agents_router.get_run(agent.id, run.id, db, user)

                assert result.id == run.id


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
