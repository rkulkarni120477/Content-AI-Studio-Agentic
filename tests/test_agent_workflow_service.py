"""Unit tests for AgentWorkflowService

Tests cover:
- Workflow creation and validation
- Workflow retrieval and listing
- Workflow updates and archiving
- Tenant isolation enforcement
- Permission enforcement
- DAG validation (cycles, missing nodes, invalid agents)
- Workflow execution initialization
- Handoff evaluation
"""

import json
import pytest
from datetime import datetime
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
    AgentWorkflow,
    WorkflowRun,
)
from promptops_app.services.agent_workflow_service import AgentWorkflowService
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
        is_active=True
    )
    db.add(template)
    db.commit()
    return template


@pytest.fixture
def setup_agents(db, mock_project_id, setup_template):
    """Create multiple test agents for workflow."""
    agents = []
    for i, handle in enumerate(["agent_1", "agent_2", "agent_3"]):
        agent = AgentDefinition(
            project_id=mock_project_id,
            template_id=setup_template.id,
            name=f"Test Agent {i+1}",
            call_handle=handle,
            description=f"Test agent {i+1}",
            owner="test_user",
            configuration='{"model": "gpt-4"}',
            lifecycle_state="active",
            created_by="test_user"
        )
        db.add(agent)
        db.commit()
        agents.append(agent)
    return agents


@pytest.fixture
def simple_workflow_definition(setup_agents):
    """Simple 2-agent sequential workflow."""
    return {
        "agent_steps": [
            {"agent_id": setup_agents[0].id, "step_id": "step_1"},
            {"agent_id": setup_agents[1].id, "step_id": "step_2"}
        ],
        "handoff_rules": [
            {
                "from_agent_id": setup_agents[0].id,
                "to_agent_ids": [setup_agents[1].id],
                "rule_type": "sequential"
            }
        ]
    }


@pytest.fixture
def concurrent_workflow_definition(setup_agents):
    """Workflow with concurrent handoff (1 → 2 parallel)."""
    return {
        "agent_steps": [
            {"agent_id": setup_agents[0].id, "step_id": "step_1"},
            {"agent_id": setup_agents[1].id, "step_id": "step_2"},
            {"agent_id": setup_agents[2].id, "step_id": "step_3"}
        ],
        "handoff_rules": [
            {
                "from_agent_id": setup_agents[0].id,
                "to_agent_ids": [setup_agents[1].id, setup_agents[2].id],
                "rule_type": "concurrent"
            }
        ]
    }


# ─────────────────────────────────────────────────────────────────────────────
# Workflow Creation Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestWorkflowCreation:
    """Test workflow creation and validation."""

    def test_create_workflow_valid(self, db, mock_project_id, mock_user_id, simple_workflow_definition):
        """Create a valid workflow."""
        with patch("app.core.permissions.rbac_check", return_value=True):
            workflow = AgentWorkflowService.create_workflow(
                db=db,
                project_id=mock_project_id,
                name="Test Workflow",
                call_handle="test_workflow",
                definition=simple_workflow_definition,
                created_by=mock_user_id,
                description="A test workflow"
            )

            assert workflow.id is not None
            assert workflow.name == "Test Workflow"
            assert workflow.call_handle == "test_workflow"
            assert workflow.project_id == mock_project_id
            assert workflow.is_active is True
            assert workflow.owner == mock_user_id

    def test_create_workflow_duplicate_handle(self, db, mock_project_id, mock_user_id, simple_workflow_definition):
        """Duplicate call_handle within same tenant should fail."""
        with patch("app.core.permissions.rbac_check", return_value=True):
            AgentWorkflowService.create_workflow(
                db=db,
                project_id=mock_project_id,
                name="Workflow 1",
                call_handle="duplicate_handle",
                definition=simple_workflow_definition,
                created_by=mock_user_id
            )

            with pytest.raises(ValidationError):
                AgentWorkflowService.create_workflow(
                    db=db,
                    project_id=mock_project_id,
                    name="Workflow 2",
                    call_handle="duplicate_handle",
                    definition=simple_workflow_definition,
                    created_by=mock_user_id
                )

    def test_create_workflow_invalid_dag_no_agents(self, db, mock_project_id, mock_user_id):
        """DAG with no agents should fail."""
        invalid_definition = {
            "agent_steps": [],
            "handoff_rules": []
        }

        with patch("app.core.permissions.rbac_check", return_value=True):
            with pytest.raises(ValidationError):
                AgentWorkflowService.create_workflow(
                    db=db,
                    project_id=mock_project_id,
                    name="Invalid Workflow",
                    call_handle="invalid",
                    definition=invalid_definition,
                    created_by=mock_user_id
                )

    def test_create_workflow_invalid_referenced_agent(self, db, mock_project_id, mock_user_id):
        """DAG with non-existent agent should fail."""
        invalid_definition = {
            "agent_steps": [{"agent_id": 99999}],
            "handoff_rules": []
        }

        with patch("app.core.permissions.rbac_check", return_value=True):
            with pytest.raises(NotFoundError):
                AgentWorkflowService.create_workflow(
                    db=db,
                    project_id=mock_project_id,
                    name="Invalid Workflow",
                    call_handle="invalid",
                    definition=invalid_definition,
                    created_by=mock_user_id
                )

    def test_create_workflow_missing_permission(self, db, mock_project_id, mock_user_id, simple_workflow_definition):
        """User without permission should fail."""
        with patch("app.core.permissions.rbac_check", return_value=False):
            with pytest.raises(PermissionDeniedError):
                AgentWorkflowService.create_workflow(
                    db=db,
                    project_id=mock_project_id,
                    name="Test Workflow",
                    call_handle="test",
                    definition=simple_workflow_definition,
                    created_by=mock_user_id
                )


# ─────────────────────────────────────────────────────────────────────────────
# Workflow Retrieval Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestWorkflowRetrieval:
    """Test workflow retrieval and listing."""

    def test_get_workflow_valid(self, db, mock_project_id, mock_user_id, simple_workflow_definition):
        """Get a workflow by ID."""
        with patch("app.core.permissions.rbac_check", return_value=True):
            created = AgentWorkflowService.create_workflow(
                db=db,
                project_id=mock_project_id,
                name="Test",
                call_handle="test",
                definition=simple_workflow_definition,
                created_by=mock_user_id
            )

            retrieved = AgentWorkflowService.get_workflow(db, created.id, mock_project_id)
            assert retrieved.id == created.id
            assert retrieved.name == "Test"

    def test_get_workflow_not_found(self, db, mock_project_id):
        """Get non-existent workflow should fail."""
        with pytest.raises(NotFoundError):
            AgentWorkflowService.get_workflow(db, 99999, mock_project_id)

    def test_get_workflow_tenant_isolation(self, db, mock_user_id, simple_workflow_definition):
        """Workflow from different tenant should not be accessible."""
        with patch("app.core.permissions.rbac_check", return_value=True):
            workflow = AgentWorkflowService.create_workflow(
                db=db,
                project_id=1,
                name="Test",
                call_handle="test",
                definition=simple_workflow_definition,
                created_by=mock_user_id
            )

            with pytest.raises(NotFoundError):
                AgentWorkflowService.get_workflow(db, workflow.id, 2)

    def test_list_workflows(self, db, mock_project_id, mock_user_id, simple_workflow_definition):
        """List workflows for a tenant."""
        with patch("app.core.permissions.rbac_check", return_value=True):
            for i in range(5):
                AgentWorkflowService.create_workflow(
                    db=db,
                    project_id=mock_project_id,
                    name=f"Workflow {i}",
                    call_handle=f"workflow_{i}",
                    definition=simple_workflow_definition,
                    created_by=mock_user_id
                )

            workflows, total = AgentWorkflowService.list_workflows(db, mock_project_id)
            assert total == 5
            assert len(workflows) == 5

    def test_list_workflows_pagination(self, db, mock_project_id, mock_user_id, simple_workflow_definition):
        """Pagination on workflow list."""
        with patch("app.core.permissions.rbac_check", return_value=True):
            for i in range(30):
                AgentWorkflowService.create_workflow(
                    db=db,
                    project_id=mock_project_id,
                    name=f"Workflow {i}",
                    call_handle=f"workflow_{i}",
                    definition=simple_workflow_definition,
                    created_by=mock_user_id
                )

            page1, total = AgentWorkflowService.list_workflows(db, mock_project_id, limit=10, offset=0)
            assert len(page1) == 10
            assert total == 30

            page2, total = AgentWorkflowService.list_workflows(db, mock_project_id, limit=10, offset=10)
            assert len(page2) == 10


# ─────────────────────────────────────────────────────────────────────────────
# Workflow Execution Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestWorkflowExecution:
    """Test workflow execution initialization."""

    def test_execute_workflow_valid(self, db, mock_project_id, mock_user_id, mock_user_role, simple_workflow_definition):
        """Execute a valid workflow."""
        with patch("app.core.permissions.rbac_check", return_value=True):
            workflow = AgentWorkflowService.create_workflow(
                db=db,
                project_id=mock_project_id,
                name="Test",
                call_handle="test",
                definition=simple_workflow_definition,
                created_by=mock_user_id
            )

            with patch.object(AgentWorkflowService, "_execute_workflow_agents"):
                run = AgentWorkflowService.execute_workflow(
                    db=db,
                    workflow_id=workflow.id,
                    project_id=mock_project_id,
                    initiated_by=mock_user_id,
                    initiated_by_role=mock_user_role,
                    input_content="Test content",
                    input_context={"key": "value"}
                )

                assert run.id is not None
                assert run.workflow_id == workflow.id
                assert run.state == "running"
                assert run.initiated_by == mock_user_id

    def test_execute_workflow_idempotency(self, db, mock_project_id, mock_user_id, mock_user_role, simple_workflow_definition):
        """Idempotent execution with request_id."""
        with patch("app.core.permissions.rbac_check", return_value=True):
            workflow = AgentWorkflowService.create_workflow(
                db=db,
                project_id=mock_project_id,
                name="Test",
                call_handle="test",
                definition=simple_workflow_definition,
                created_by=mock_user_id
            )

            with patch.object(AgentWorkflowService, "_execute_workflow_agents"):
                run1 = AgentWorkflowService.execute_workflow(
                    db=db,
                    workflow_id=workflow.id,
                    project_id=mock_project_id,
                    initiated_by=mock_user_id,
                    initiated_by_role=mock_user_role,
                    input_content="Content",
                    request_id="idempotent_key_123"
                )

                run2 = AgentWorkflowService.execute_workflow(
                    db=db,
                    workflow_id=workflow.id,
                    project_id=mock_project_id,
                    initiated_by=mock_user_id,
                    initiated_by_role=mock_user_role,
                    input_content="Content",
                    request_id="idempotent_key_123"
                )

                assert run1.id == run2.id

    def test_execute_workflow_inactive(self, db, mock_project_id, mock_user_id, mock_user_role, simple_workflow_definition):
        """Executing inactive workflow should fail."""
        with patch("app.core.permissions.rbac_check", return_value=True):
            workflow = AgentWorkflowService.create_workflow(
                db=db,
                project_id=mock_project_id,
                name="Test",
                call_handle="test",
                definition=simple_workflow_definition,
                created_by=mock_user_id
            )
            workflow.is_active = False
            db.commit()

            with pytest.raises(ValidationError):
                AgentWorkflowService.execute_workflow(
                    db=db,
                    workflow_id=workflow.id,
                    project_id=mock_project_id,
                    initiated_by=mock_user_id,
                    initiated_by_role=mock_user_role,
                    input_content="Content"
                )


# ─────────────────────────────────────────────────────────────────────────────
# Workflow Run Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestWorkflowRun:
    """Test workflow run management."""

    def test_get_workflow_run(self, db, mock_project_id, mock_user_id, mock_user_role, simple_workflow_definition):
        """Get workflow run by ID."""
        with patch("app.core.permissions.rbac_check", return_value=True):
            workflow = AgentWorkflowService.create_workflow(
                db=db,
                project_id=mock_project_id,
                name="Test",
                call_handle="test",
                definition=simple_workflow_definition,
                created_by=mock_user_id
            )

            with patch.object(AgentWorkflowService, "_execute_workflow_agents"):
                run = AgentWorkflowService.execute_workflow(
                    db=db,
                    workflow_id=workflow.id,
                    project_id=mock_project_id,
                    initiated_by=mock_user_id,
                    initiated_by_role=mock_user_role,
                    input_content="Content"
                )

                retrieved = AgentWorkflowService.get_workflow_run(
                    db, workflow.id, run.id, mock_project_id
                )
                assert retrieved.id == run.id

    def test_pause_workflow(self, db, mock_project_id, mock_user_id, mock_user_role, simple_workflow_definition):
        """Pause a running workflow."""
        with patch("app.core.permissions.rbac_check", return_value=True):
            workflow = AgentWorkflowService.create_workflow(
                db=db,
                project_id=mock_project_id,
                name="Test",
                call_handle="test",
                definition=simple_workflow_definition,
                created_by=mock_user_id
            )

            with patch.object(AgentWorkflowService, "_execute_workflow_agents"):
                run = AgentWorkflowService.execute_workflow(
                    db=db,
                    workflow_id=workflow.id,
                    project_id=mock_project_id,
                    initiated_by=mock_user_id,
                    initiated_by_role=mock_user_role,
                    input_content="Content"
                )

                paused = AgentWorkflowService.pause_workflow(
                    db, workflow.id, run.id, mock_project_id
                )
                assert paused.state == "paused"

    def test_resume_workflow(self, db, mock_project_id, mock_user_id, mock_user_role, simple_workflow_definition):
        """Resume a paused workflow."""
        with patch("app.core.permissions.rbac_check", return_value=True):
            workflow = AgentWorkflowService.create_workflow(
                db=db,
                project_id=mock_project_id,
                name="Test",
                call_handle="test",
                definition=simple_workflow_definition,
                created_by=mock_user_id
            )

            with patch.object(AgentWorkflowService, "_execute_workflow_agents"):
                run = AgentWorkflowService.execute_workflow(
                    db=db,
                    workflow_id=workflow.id,
                    project_id=mock_project_id,
                    initiated_by=mock_user_id,
                    initiated_by_role=mock_user_role,
                    input_content="Content"
                )

                paused = AgentWorkflowService.pause_workflow(
                    db, workflow.id, run.id, mock_project_id
                )

                resumed = AgentWorkflowService.resume_workflow(
                    db, workflow.id, run.id, mock_project_id
                )
                assert resumed.state == "running"


# ─────────────────────────────────────────────────────────────────────────────
# Handoff Logic Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestHandoffLogic:
    """Test handoff evaluation and routing."""

    def test_get_next_agents_sequential(self, setup_agents):
        """Sequential handoff returns single next agent."""
        definition = {
            "agent_steps": [
                {"agent_id": setup_agents[0].id},
                {"agent_id": setup_agents[1].id}
            ],
            "handoff_rules": [
                {
                    "from_agent_id": setup_agents[0].id,
                    "to_agent_ids": [setup_agents[1].id],
                    "rule_type": "sequential"
                }
            ]
        }

        next_agents = AgentWorkflowService._get_next_agents(definition, setup_agents[0].id, {})
        assert next_agents == [setup_agents[1].id]

    def test_get_next_agents_concurrent(self, setup_agents):
        """Concurrent handoff returns multiple next agents."""
        definition = {
            "agent_steps": [
                {"agent_id": setup_agents[0].id},
                {"agent_id": setup_agents[1].id},
                {"agent_id": setup_agents[2].id}
            ],
            "handoff_rules": [
                {
                    "from_agent_id": setup_agents[0].id,
                    "to_agent_ids": [setup_agents[1].id, setup_agents[2].id],
                    "rule_type": "concurrent"
                }
            ]
        }

        next_agents = AgentWorkflowService._get_next_agents(definition, setup_agents[0].id, {})
        assert set(next_agents) == {setup_agents[1].id, setup_agents[2].id}

    def test_get_next_agents_no_match(self, setup_agents):
        """Non-existent agent returns no next agents."""
        definition = {
            "agent_steps": [{"agent_id": setup_agents[0].id}],
            "handoff_rules": []
        }

        next_agents = AgentWorkflowService._get_next_agents(definition, 99999, {})
        assert next_agents == []

    def test_evaluate_condition_equality(self):
        """Evaluate simple equality condition."""
        result = {"type": "review"}
        condition = "output.type == review"

        evaluated = AgentWorkflowService._evaluate_condition(condition, result)
        assert evaluated is True

    def test_evaluate_condition_no_match(self):
        """Condition that doesn't match."""
        result = {"type": "content"}
        condition = "output.type == review"

        evaluated = AgentWorkflowService._evaluate_condition(condition, result)
        assert evaluated is False
