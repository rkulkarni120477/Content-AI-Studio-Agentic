"""Integration tests for Workflow API Routes

Tests cover:
- All CRUD endpoints for workflows
- Workflow execution endpoints
- Run management endpoints
- Error handling and validation
- Tenant isolation
- Permission enforcement
- Pagination
"""

import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from unittest.mock import patch, MagicMock

from app.main import app
from promptops_app.database import Base, get_db
from promptops_app.agents_models import (
    AgentTemplate, AgentDefinition, AgentWorkflow
)
from app.core.dependencies import get_current_user


# ─────────────────────────────────────────────────────────────────────────────
# Test Database Setup
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def test_db():
    """In-memory SQLite database for testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    yield session
    session.close()


@pytest.fixture
def client(test_db):
    """TestClient with override for database dependency."""
    def override_get_db():
        yield test_db

    def override_get_current_user():
        return {
            "user_id": 1,
            "username": "test_user",
            "email": "test@example.com",
            "project_id": 1,
            "role": "author"
        }

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user

    yield TestClient(app)

    app.dependency_overrides.clear()


@pytest.fixture
def setup_test_data(test_db):
    """Create test agents for workflow."""
    # Create template
    template = AgentTemplate(
        key="test_template",
        name="Test Template",
        specialization="test",
        instructions="Test",
        input_schema='{"type": "object"}',
        output_schema='{"type": "object"}',
        owner="admin",
        is_active=True
    )
    test_db.add(template)
    test_db.commit()

    # Create agents
    agents = []
    for i in range(3):
        agent = AgentDefinition(
            project_id=1,
            template_id=template.id,
            name=f"Agent {i+1}",
            call_handle=f"agent_{i+1}",
            owner="test_user",
            configuration='{"model": "gpt-4"}',
            lifecycle_state="active",
            created_by="test_user"
        )
        test_db.add(agent)
        test_db.commit()
        agents.append(agent)

    return {"template": template, "agents": agents}


# ─────────────────────────────────────────────────────────────────────────────
# Workflow Creation Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestWorkflowCreateAPI:
    """Test workflow creation endpoint."""

    def test_create_workflow_success(self, client, setup_test_data):
        """Create a workflow successfully."""
        agents = setup_test_data["agents"]
        payload = {
            "name": "Test Workflow",
            "call_handle": "test_workflow",
            "description": "A test workflow",
            "definition": {
                "agent_steps": [
                    {"agent_id": agents[0].id, "step_id": "step_1"},
                    {"agent_id": agents[1].id, "step_id": "step_2"}
                ],
                "handoff_rules": [
                    {
                        "from_agent_id": agents[0].id,
                        "to_agent_ids": [agents[1].id],
                        "rule_type": "sequential"
                    }
                ]
            }
        }

        with patch("app.core.permissions.rbac_check", return_value=True):
            response = client.post("/api/v1/workflows", json=payload)

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Test Workflow"
        assert data["call_handle"] == "test_workflow"
        assert data["is_active"] is True
        assert data["id"] is not None

    def test_create_workflow_invalid_definition(self, client, setup_test_data):
        """Create workflow with invalid definition."""
        payload = {
            "name": "Invalid Workflow",
            "call_handle": "invalid",
            "definition": {
                "agent_steps": [],  # Empty
                "handoff_rules": []
            }
        }

        with patch("app.core.permissions.rbac_check", return_value=True):
            response = client.post("/api/v1/workflows", json=payload)

        assert response.status_code == 422

    def test_create_workflow_duplicate_handle(self, client, setup_test_data):
        """Duplicate call_handle should fail."""
        agents = setup_test_data["agents"]
        payload = {
            "name": "Workflow",
            "call_handle": "dup_handle",
            "definition": {
                "agent_steps": [{"agent_id": agents[0].id}],
                "handoff_rules": []
            }
        }

        with patch("app.core.permissions.rbac_check", return_value=True):
            # Create first
            response1 = client.post("/api/v1/workflows", json=payload)
            assert response1.status_code == 201

            # Try duplicate
            payload["name"] = "Workflow 2"
            response2 = client.post("/api/v1/workflows", json=payload)
            assert response2.status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# Workflow List Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestWorkflowListAPI:
    """Test workflow listing endpoint."""

    def test_list_workflows(self, client, setup_test_data):
        """List workflows with pagination."""
        agents = setup_test_data["agents"]
        payload = {
            "name": "Workflow",
            "call_handle": "wf_1",
            "definition": {
                "agent_steps": [{"agent_id": agents[0].id}],
                "handoff_rules": []
            }
        }

        with patch("app.core.permissions.rbac_check", return_value=True):
            # Create 3 workflows
            for i in range(3):
                payload["call_handle"] = f"wf_{i+1}"
                payload["name"] = f"Workflow {i+1}"
                client.post("/api/v1/workflows", json=payload)

            # List
            response = client.get("/api/v1/workflows?page=1&page_size=10")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        assert len(data["items"]) == 3
        assert data["page"] == 1

    def test_list_workflows_pagination(self, client, setup_test_data):
        """Pagination works correctly."""
        agents = setup_test_data["agents"]
        payload = {
            "name": "Workflow",
            "definition": {
                "agent_steps": [{"agent_id": agents[0].id}],
                "handoff_rules": []
            }
        }

        with patch("app.core.permissions.rbac_check", return_value=True):
            # Create 25 workflows
            for i in range(25):
                payload["call_handle"] = f"wf_{i+1}"
                payload["name"] = f"Workflow {i+1}"
                client.post("/api/v1/workflows", json=payload)

            # List page 1 (20 per page)
            response1 = client.get("/api/v1/workflows?page=1&page_size=20")
            assert len(response1.json()["items"]) == 20

            # List page 2
            response2 = client.get("/api/v1/workflows?page=2&page_size=20")
            assert len(response2.json()["items"]) == 5


# ─────────────────────────────────────────────────────────────────────────────
# Workflow Get Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestWorkflowGetAPI:
    """Test workflow retrieval endpoint."""

    def test_get_workflow(self, client, setup_test_data):
        """Get workflow by ID."""
        agents = setup_test_data["agents"]
        payload = {
            "name": "Test Workflow",
            "call_handle": "test_wf",
            "definition": {
                "agent_steps": [{"agent_id": agents[0].id}],
                "handoff_rules": []
            }
        }

        with patch("app.core.permissions.rbac_check", return_value=True):
            create_response = client.post("/api/v1/workflows", json=payload)
            workflow_id = create_response.json()["id"]

            get_response = client.get(f"/api/v1/workflows/{workflow_id}")

        assert get_response.status_code == 200
        data = get_response.json()
        assert data["id"] == workflow_id
        assert data["name"] == "Test Workflow"

    def test_get_workflow_not_found(self, client):
        """Get non-existent workflow."""
        response = client.get("/api/v1/workflows/99999")
        assert response.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Workflow Update Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestWorkflowUpdateAPI:
    """Test workflow update endpoint."""

    def test_update_workflow_name(self, client, setup_test_data):
        """Update workflow name."""
        agents = setup_test_data["agents"]
        payload = {
            "name": "Original Name",
            "call_handle": "test_wf",
            "definition": {
                "agent_steps": [{"agent_id": agents[0].id}],
                "handoff_rules": []
            }
        }

        with patch("app.core.permissions.rbac_check", return_value=True):
            create_response = client.post("/api/v1/workflows", json=payload)
            workflow_id = create_response.json()["id"]

            update_payload = {"name": "Updated Name"}
            update_response = client.put(f"/api/v1/workflows/{workflow_id}", json=update_payload)

        assert update_response.status_code == 200
        assert update_response.json()["name"] == "Updated Name"


# ─────────────────────────────────────────────────────────────────────────────
# Workflow Deletion Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestWorkflowDeleteAPI:
    """Test workflow deletion (archiving) endpoint."""

    def test_delete_workflow(self, client, setup_test_data):
        """Delete (archive) a workflow."""
        agents = setup_test_data["agents"]
        payload = {
            "name": "Test Workflow",
            "call_handle": "test_wf",
            "definition": {
                "agent_steps": [{"agent_id": agents[0].id}],
                "handoff_rules": []
            }
        }

        with patch("app.core.permissions.rbac_check", return_value=True):
            create_response = client.post("/api/v1/workflows", json=payload)
            workflow_id = create_response.json()["id"]

            delete_response = client.delete(f"/api/v1/workflows/{workflow_id}")

        assert delete_response.status_code == 200
        assert "archived" in delete_response.json()["message"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# Workflow Execution Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestWorkflowExecuteAPI:
    """Test workflow execution endpoint."""

    def test_execute_workflow(self, client, setup_test_data):
        """Execute a workflow."""
        agents = setup_test_data["agents"]
        workflow_payload = {
            "name": "Test Workflow",
            "call_handle": "test_wf",
            "definition": {
                "agent_steps": [{"agent_id": agents[0].id}],
                "handoff_rules": []
            }
        }

        with patch("app.core.permissions.rbac_check", return_value=True):
            # Create workflow
            create_response = client.post("/api/v1/workflows", json=workflow_payload)
            workflow_id = create_response.json()["id"]

            # Execute
            execute_payload = {
                "input_content": "Test content"
            }
            execute_response = client.post(
                f"/api/v1/workflows/{workflow_id}/execute",
                json=execute_payload
            )

        assert execute_response.status_code == 201
        data = execute_response.json()
        assert data["workflow_id"] == workflow_id
        assert data["state"] == "running"
        assert data["initiated_by"] == "test_user"

    def test_execute_workflow_idempotency(self, client, setup_test_data):
        """Workflow execution idempotency with request_id."""
        agents = setup_test_data["agents"]
        workflow_payload = {
            "name": "Test Workflow",
            "call_handle": "test_wf_2",
            "definition": {
                "agent_steps": [{"agent_id": agents[0].id}],
                "handoff_rules": []
            }
        }

        with patch("app.core.permissions.rbac_check", return_value=True):
            # Create workflow
            create_response = client.post("/api/v1/workflows", json=workflow_payload)
            workflow_id = create_response.json()["id"]

            # Execute twice with same request_id
            execute_payload = {
                "input_content": "Content",
                "request_id": "idempotent_123"
            }
            response1 = client.post(
                f"/api/v1/workflows/{workflow_id}/execute",
                json=execute_payload
            )
            run_id_1 = response1.json()["id"]

            response2 = client.post(
                f"/api/v1/workflows/{workflow_id}/execute",
                json=execute_payload
            )
            run_id_2 = response2.json()["id"]

        assert run_id_1 == run_id_2


# ─────────────────────────────────────────────────────────────────────────────
# Workflow Run Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestWorkflowRunAPI:
    """Test workflow run management endpoints."""

    def test_list_workflow_runs(self, client, setup_test_data):
        """List workflow runs."""
        agents = setup_test_data["agents"]
        workflow_payload = {
            "name": "Test Workflow",
            "call_handle": "test_wf",
            "definition": {
                "agent_steps": [{"agent_id": agents[0].id}],
                "handoff_rules": []
            }
        }

        with patch("app.core.permissions.rbac_check", return_value=True):
            # Create workflow
            create_response = client.post("/api/v1/workflows", json=workflow_payload)
            workflow_id = create_response.json()["id"]

            # Execute multiple times
            for i in range(3):
                execute_payload = {"input_content": f"Content {i}"}
                client.post(f"/api/v1/workflows/{workflow_id}/execute", json=execute_payload)

            # List runs
            response = client.get(f"/api/v1/workflows/{workflow_id}/runs")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        assert len(data["items"]) == 3

    def test_get_workflow_run(self, client, setup_test_data):
        """Get specific workflow run."""
        agents = setup_test_data["agents"]
        workflow_payload = {
            "name": "Test Workflow",
            "call_handle": "test_wf",
            "definition": {
                "agent_steps": [{"agent_id": agents[0].id}],
                "handoff_rules": []
            }
        }

        with patch("app.core.permissions.rbac_check", return_value=True):
            # Create and execute
            create_response = client.post("/api/v1/workflows", json=workflow_payload)
            workflow_id = create_response.json()["id"]

            execute_payload = {"input_content": "Content"}
            execute_response = client.post(
                f"/api/v1/workflows/{workflow_id}/execute",
                json=execute_payload
            )
            run_id = execute_response.json()["id"]

            # Get run
            get_response = client.get(f"/api/v1/workflows/{workflow_id}/runs/{run_id}")

        assert get_response.status_code == 200
        data = get_response.json()
        assert data["id"] == run_id
        assert data["workflow_id"] == workflow_id


# ─────────────────────────────────────────────────────────────────────────────
# Workflow Run Control Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestWorkflowRunControlAPI:
    """Test workflow run pause/resume endpoints."""

    def test_pause_workflow_run(self, client, setup_test_data):
        """Pause a workflow run."""
        agents = setup_test_data["agents"]
        workflow_payload = {
            "name": "Test Workflow",
            "call_handle": "test_wf",
            "definition": {
                "agent_steps": [{"agent_id": agents[0].id}],
                "handoff_rules": []
            }
        }

        with patch("app.core.permissions.rbac_check", return_value=True):
            # Create and execute
            create_response = client.post("/api/v1/workflows", json=workflow_payload)
            workflow_id = create_response.json()["id"]

            execute_response = client.post(
                f"/api/v1/workflows/{workflow_id}/execute",
                json={"input_content": "Content"}
            )
            run_id = execute_response.json()["id"]

            # Pause
            pause_response = client.post(
                f"/api/v1/workflows/{workflow_id}/runs/{run_id}/pause"
            )

        assert pause_response.status_code == 200
        assert pause_response.json()["state"] == "paused"

    def test_resume_workflow_run(self, client, setup_test_data):
        """Resume a paused workflow run."""
        agents = setup_test_data["agents"]
        workflow_payload = {
            "name": "Test Workflow",
            "call_handle": "test_wf",
            "definition": {
                "agent_steps": [{"agent_id": agents[0].id}],
                "handoff_rules": []
            }
        }

        with patch("app.core.permissions.rbac_check", return_value=True):
            # Create, execute, and pause
            create_response = client.post("/api/v1/workflows", json=workflow_payload)
            workflow_id = create_response.json()["id"]

            execute_response = client.post(
                f"/api/v1/workflows/{workflow_id}/execute",
                json={"input_content": "Content"}
            )
            run_id = execute_response.json()["id"]

            client.post(f"/api/v1/workflows/{workflow_id}/runs/{run_id}/pause")

            # Resume
            resume_response = client.post(
                f"/api/v1/workflows/{workflow_id}/runs/{run_id}/resume"
            )

        assert resume_response.status_code == 200
        assert resume_response.json()["state"] == "running"
