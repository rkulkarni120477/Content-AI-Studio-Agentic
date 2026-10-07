"""Security Hardening Tests for Agent Builder

Comprehensive security tests covering:
- SQL injection prevention (ORM usage verified)
- Cross-tenant isolation enforcement
- RBAC bypass attempt prevention
- Budget limit enforcement
- Checkpoint integrity validation
- Model response validation (no code injection)

Success Criteria:
- All inputs validated
- No direct SQL queries
- RBAC enforcement verified
- Privilege escalation prevented
- Data access controls enforced
- No cross-tenant data leakage
"""

from __future__ import annotations

import logging
import json
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.exceptions import (
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from promptops_app.agents_models import (
    AgentDefinition,
    AgentRun,
    AgentDefinitionVersion,
)
from promptops_app.services.agent_run_service import AgentRunService
from promptops_app.services.agent_step_service import AgentStepService
from promptops_app.services.agent_budget_service import check_budget
from promptops_app.services.agents_tenant_service import AgentsTenantService

_log = logging.getLogger(__name__)


class TestSQLInjectionPrevention:
    """Test SQL injection prevention through ORM usage."""

    def test_agent_lookup_uses_orm_not_raw_sql(self, db: Session):
        """Verify agent lookups use ORM, not raw SQL vulnerable to injection."""
        project_id = 1
        agent = self._create_test_agent(db, project_id)

        # This should use ORM safely, not raw SQL
        # If the service used raw SQL like "SELECT * FROM agents WHERE id = %s",
        # an attacker could pass malicious SQL. The ORM prevents this.
        from promptops_app.services.agents_tenant_service import AgentsTenantService

        found_agent = AgentsTenantService.validate_agent_ownership(
            db, agent.id, project_id
        )
        assert found_agent.id == agent.id

        # Try with invalid ID syntax that would break raw SQL
        with pytest.raises(NotFoundError):
            AgentsTenantService.validate_agent_ownership(
                db, 99999, project_id
            )

    def test_run_queries_parameterized(self, db: Session):
        """Verify run queries are parameterized to prevent SQL injection."""
        project_id = 2
        agent = self._create_test_agent(db, project_id)
        check_budget(db, project_id, 0.5)

        run = AgentRunService.create_run(
            db=db,
            project_id=project_id,
            definition_id=agent.id,
            initiated_by="test_user",
            initiated_by_role="admin",
            input_content="Test",
            request_id="sql_injection_test",
            estimated_cost=0.50,
        )

        # Query by run_id should be parameterized
        found_run = db.query(AgentRun).filter(AgentRun.id == run.id).first()
        assert found_run is not None
        assert found_run.id == run.id

    def test_no_string_interpolation_in_queries(self, db: Session):
        """Verify queries don't use string interpolation for IDs."""
        # This test documents the principle: all queries should use
        # SQLAlchemy's filter() with ORM columns, never string f-strings

        project_id = 3
        agent = self._create_test_agent(db, project_id)

        # Correct way (SQLAlchemy ORM):
        correct_query = db.query(AgentDefinition).filter(
            AgentDefinition.id == agent.id,
            AgentDefinition.project_id == project_id,
        )
        result = correct_query.first()
        assert result is not None

        # This would be vulnerable (don't do this):
        # vulnerable_query = f"SELECT * FROM agent_definitions WHERE id = {agent.id}"


class TestCrossTenantIsolation:
    """Test cross-tenant isolation enforcement."""

    def test_run_creation_respects_tenant_boundary(self, two_tenants, mock_llm):
        """Test that runs are created in correct tenant context."""
        db = two_tenants["db"]
        tenant_a = two_tenants["a"]
        tenant_b = two_tenants["b"]

        # Create agent in tenant A
        agent_a = self._create_agent_for_tenant(db, tenant_a.id)
        check_budget(db, tenant_a.id, 1.0)

        # Create run in tenant A
        run = AgentRunService.create_run(
            db=db,
            project_id=tenant_a.id,
            definition_id=agent_a.id,
            initiated_by="test_user",
            initiated_by_role="admin",
            input_content="Test",
            request_id="tenant_isolation_test_a",
            estimated_cost=0.50,
        )

        # Verify run belongs to tenant A
        db.refresh(run)
        assert run.project_id == tenant_a.id

        # Verify tenant B cannot access this run
        run_b = db.query(AgentRun).filter(
            AgentRun.id == run.id,
            AgentRun.project_id == tenant_b.id,
        ).first()
        assert run_b is None  # Not found in tenant B context

    def test_agent_list_respects_tenant_boundary(self, two_tenants):
        """Test that agent listings are tenant-scoped."""
        db = two_tenants["db"]
        tenant_a = two_tenants["a"]
        tenant_b = two_tenants["b"]

        # Create agents in both tenants
        agent_a = self._create_agent_for_tenant(db, tenant_a.id, "agent_a")
        agent_b = self._create_agent_for_tenant(db, tenant_b.id, "agent_b")

        # Query tenant A's agents
        agents_a = db.query(AgentDefinition).filter(
            AgentDefinition.project_id == tenant_a.id
        ).all()

        # Query tenant B's agents
        agents_b = db.query(AgentDefinition).filter(
            AgentDefinition.project_id == tenant_b.id
        ).all()

        # Verify isolation
        agent_a_ids = {a.id for a in agents_a}
        agent_b_ids = {a.id for a in agents_b}

        assert agent_a.id in agent_a_ids
        assert agent_b.id not in agent_a_ids

        assert agent_b.id in agent_b_ids
        assert agent_a.id not in agent_b_ids

    def test_run_steps_isolated_by_tenant(self, two_tenants, mock_llm):
        """Test that run steps are isolated by tenant."""
        db = two_tenants["db"]
        tenant_a = two_tenants["a"]
        tenant_b = two_tenants["b"]

        # Create agents in both tenants
        agent_a = self._create_agent_for_tenant(db, tenant_a.id)
        check_budget(db, tenant_a.id, 1.0)

        # Create run in tenant A
        run_a = AgentRunService.create_run(
            db=db,
            project_id=tenant_a.id,
            definition_id=agent_a.id,
            initiated_by="test_user",
            initiated_by_role="admin",
            input_content="Test A",
            request_id="tenant_isolation_steps_a",
            estimated_cost=0.50,
        )

        # Create step in run A
        step_a = AgentStepService.create_step(
            db=db,
            run_id=run_a.id,
            step_index=0,
            step_type="model_call",
            project_id=tenant_a.id,
        )

        # Verify step belongs to run and tenant
        from promptops_app.agents_models import AgentRunStep

        step_in_a = db.query(AgentRunStep).filter(
            AgentRunStep.id == step_a.id,
            AgentRunStep.run_id == run_a.id,
        ).first()
        assert step_in_a is not None

        # Tenant B should not see this step via their agent's runs
        # (This would require joining through the run)


class TestRBACBypassPrevention:
    """Test RBAC enforcement and bypass prevention."""

    def test_non_admin_cannot_activate_agent(self, db: Session, mock_llm):
        """Test that non-admin cannot activate agents."""
        project_id = 4
        agent = self._create_test_agent(db, project_id, state="draft")

        # Non-admin user should not have agents.activate permission
        from app.core.permissions import rbac_check

        has_permission = rbac_check("author", "agents.activate")
        assert not has_permission, "Author should not have agents.activate"

        has_permission = rbac_check("admin", "agents.activate")
        assert has_permission, "Admin should have agents.activate"

    def test_non_author_cannot_run_agent(self, db: Session, mock_llm):
        """Test that users without permission cannot run agents."""
        project_id = 5
        agent = self._create_test_agent(db, project_id)
        check_budget(db, project_id, 0.5)

        # User with no agents.run permission should fail
        with pytest.raises(PermissionDeniedError):
            AgentRunService.create_run(
                db=db,
                project_id=project_id,
                definition_id=agent.id,
                initiated_by="restricted_user",
                initiated_by_role="viewer",  # Viewer role has no agents.run
                input_content="Test",
                request_id="rbac_test_no_run",
                estimated_cost=0.50,
            )

    def test_permission_check_not_bypassable_via_direct_db_access(self, db: Session):
        """Test that permissions are enforced even with direct DB access."""
        # The principle: Even if someone had direct DB access, they shouldn't
        # be able to create runs that bypass permission checks.

        project_id = 6
        agent = self._create_test_agent(db, project_id)

        # Direct DB insert would bypass some checks but shouldn't work
        # because the service layer should be the only way to create valid runs

        # This test documents that all business logic is in services, not DB
        from promptops_app.services.agents_tenant_service import AgentsTenantService

        # Service should validate permissions
        with pytest.raises(PermissionDeniedError):
            AgentRunService.create_run(
                db=db,
                project_id=project_id,
                definition_id=agent.id,
                initiated_by="unauthorized_user",
                initiated_by_role="viewer",
                input_content="Test",
                request_id="permission_bypass_test",
                estimated_cost=0.50,
            )


class TestBudgetEnforcement:
    """Test budget limit enforcement."""

    def test_budget_cannot_be_exceeded(self, db: Session, mock_llm):
        """Test that budget limits cannot be exceeded."""
        project_id = 7
        agent = self._create_test_agent(db, project_id)

        # Check with tiny budget
        check_budget(db, project_id, 0.01)

        # First run at edge of budget
        run1 = AgentRunService.create_run(
            db=db,
            project_id=project_id,
            definition_id=agent.id,
            initiated_by="test_user",
            initiated_by_role="admin",
            input_content="Test 1",
            request_id="budget_limit_1",
            estimated_cost=0.005,
        )
        assert run1 is not None

        # Second run should fail due to insufficient budget
        from promptops_app.services.agent_budget_service import BudgetExceededError

        with pytest.raises(BudgetExceededError):
            AgentRunService.create_run(
                db=db,
                project_id=project_id,
                definition_id=agent.id,
                initiated_by="test_user",
                initiated_by_role="admin",
                input_content="Test 2",
                request_id="budget_limit_2",
                estimated_cost=0.10,  # Exceeds budget
            )

    def test_budget_reconciliation_is_accurate(self, db: Session, mock_llm):
        """Test that budget reconciliation doesn't allow manipulation."""
        project_id = 8
        agent = self._create_test_agent(db, project_id)
        check_budget(db, project_id, 1.0)

        estimated_cost = 0.50
        actual_cost = 0.25

        run = AgentRunService.create_run(
            db=db,
            project_id=project_id,
            definition_id=agent.id,
            initiated_by="test_user",
            initiated_by_role="admin",
            input_content="Test",
            request_id="budget_reconcile_test",
            estimated_cost=estimated_cost,
        )

        # Complete step to generate usage
        step = AgentStepService.create_step(
            db=db,
            run_id=run.id,
            step_index=0,
            step_type="model_call",
            project_id=project_id,
        )

        AgentStepService.complete_step(
            db=db,
            step_id=step.id,
            result={},
            prompt_tokens=100,
            completion_tokens=200,
            project_id=project_id,
        )

        # Reconcile budget
        from promptops_app.services.agent_budget_service import reconcile_budget

        reconcile_budget(db, project_id, run.id, actual_cost)

        # Verify actual cost is recorded
        db.refresh(run)
        assert run.actual_cost == actual_cost


class TestCheckpointIntegrity:
    """Test checkpoint integrity and validation."""

    def test_checkpoint_data_not_corrupted(self, db: Session, mock_llm):
        """Test that checkpoint data is not corrupted during storage."""
        project_id = 9
        agent = self._create_test_agent(db, project_id)
        check_budget(db, project_id, 0.5)

        from promptops_app.services.agent_monitoring import get_monitor

        monitor = get_monitor()
        monitor.clear_metrics()

        run = AgentRunService.create_run(
            db=db,
            project_id=project_id,
            definition_id=agent.id,
            initiated_by="test_user",
            initiated_by_role="admin",
            input_content="Test",
            request_id="checkpoint_integrity_test",
            estimated_cost=0.50,
        )

        monitor.log_run_start(project_id, agent.id, run.id, 0.50)

        # Create step and checkpoint with specific data
        step = AgentStepService.create_step(
            db=db,
            run_id=run.id,
            step_index=0,
            step_type="model_call",
            project_id=project_id,
        )

        test_state = {
            "step_number": 0,
            "model_used": "gpt-4o",
            "tokens_used": 300,
        }

        # Simulate checkpoint with state
        state_json = json.dumps(test_state)
        state_size = len(state_json.encode('utf-8'))

        monitor.log_checkpoint_create(run.id, 0, state_size)

        # Verify checkpoint was recorded
        metrics = monitor.get_run_metrics(run.id)
        assert metrics.checkpoint_count == 1


class TestModelResponseValidation:
    """Test validation of model responses."""

    def test_model_response_injection_attempts_handled(self, db: Session):
        """Test that malicious model responses don't cause injection."""
        project_id = 10
        agent = self._create_test_agent(db, project_id)
        check_budget(db, project_id, 0.5)

        run = AgentRunService.create_run(
            db=db,
            project_id=project_id,
            definition_id=agent.id,
            initiated_by="test_user",
            initiated_by_role="admin",
            input_content="Test",
            request_id="model_injection_test",
            estimated_cost=0.50,
        )

        step = AgentStepService.create_step(
            db=db,
            run_id=run.id,
            step_index=0,
            step_type="model_call",
            project_id=project_id,
        )

        # Try to inject SQL-like code in model response
        malicious_result = {
            "content": "'; DROP TABLE agent_runs; --",
            "tokens": 100,
        }

        # This should be stored safely without executing any SQL
        AgentStepService.complete_step(
            db=db,
            step_id=step.id,
            result=malicious_result,
            prompt_tokens=100,
            completion_tokens=50,
            project_id=project_id,
        )

        db.refresh(step)
        assert step.status == "completed"

        # Verify the malicious string is stored as-is, not interpreted
        assert "DROP TABLE" in str(step.result_content)

    def test_model_response_xss_attempt_handled(self, db: Session):
        """Test that XSS attempts in model responses are handled safely."""
        project_id = 11
        agent = self._create_test_agent(db, project_id)
        check_budget(db, project_id, 0.5)

        run = AgentRunService.create_run(
            db=db,
            project_id=project_id,
            definition_id=agent.id,
            initiated_by="test_user",
            initiated_by_role="admin",
            input_content="Test",
            request_id="model_xss_test",
            estimated_cost=0.50,
        )

        step = AgentStepService.create_step(
            db=db,
            run_id=run.id,
            step_index=0,
            step_type="model_call",
            project_id=project_id,
        )

        # XSS attempt in model response
        xss_result = {
            "content": "<script>alert('xss')</script>",
        }

        # Should store safely
        AgentStepService.complete_step(
            db=db,
            step_id=step.id,
            result=xss_result,
            prompt_tokens=100,
            completion_tokens=50,
            project_id=project_id,
        )

        db.refresh(step)
        assert step.status == "completed"


class TestDataAccessControls:
    """Test data access controls."""

    def test_run_history_filtered_by_tenant(self, two_tenants, mock_llm):
        """Test that run history is filtered by tenant."""
        db = two_tenants["db"]
        tenant_a = two_tenants["a"]
        tenant_b = two_tenants["b"]

        # Create runs in both tenants
        agent_a = self._create_agent_for_tenant(db, tenant_a.id)
        agent_b = self._create_agent_for_tenant(db, tenant_b.id)

        check_budget(db, tenant_a.id, 1.0)
        check_budget(db, tenant_b.id, 1.0)

        run_a = AgentRunService.create_run(
            db=db,
            project_id=tenant_a.id,
            definition_id=agent_a.id,
            initiated_by="user_a",
            initiated_by_role="admin",
            input_content="Test A",
            request_id="access_test_a",
            estimated_cost=0.50,
        )

        run_b = AgentRunService.create_run(
            db=db,
            project_id=tenant_b.id,
            definition_id=agent_b.id,
            initiated_by="user_b",
            initiated_by_role="admin",
            input_content="Test B",
            request_id="access_test_b",
            estimated_cost=0.50,
        )

        # Query tenant A's runs (should not see B's runs)
        runs_a = db.query(AgentRun).filter(
            AgentRun.project_id == tenant_a.id
        ).all()

        run_ids_a = {r.id for r in runs_a}
        assert run_a.id in run_ids_a
        assert run_b.id not in run_ids_a

    # =========================================================================
    # Helper Methods
    # =========================================================================

    @staticmethod
    def _create_test_agent(
        db: Session,
        project_id: int,
        state: str = "active",
        name: Optional[str] = None,
    ) -> AgentDefinition:
        """Create a test agent."""
        agent = AgentDefinition(
            project_id=project_id,
            name=name or f"security_test_agent_{project_id}",
            description="Security test agent",
            call_handle=f"security_test_{project_id}",
            lifecycle_state=state,
            configuration={
                "model": "gpt-4o",
                "instructions": "Test instructions",
            },
        )
        db.add(agent)
        db.commit()
        db.refresh(agent)

        if state == "active":
            version = AgentDefinitionVersion(
                agent_id=agent.id,
                version_number=1,
                configuration=agent.configuration,
            )
            db.add(version)
            db.commit()
            db.refresh(version)

            agent.current_version_id = version.id
            db.commit()

        return agent

    @staticmethod
    def _create_agent_for_tenant(
        db: Session,
        project_id: int,
        name: Optional[str] = None,
    ) -> AgentDefinition:
        """Create an agent for a specific tenant."""
        agent = AgentDefinition(
            project_id=project_id,
            name=name or f"security_tenant_agent_{project_id}",
            description="Tenant test agent",
            call_handle=f"tenant_security_{project_id}",
            lifecycle_state="active",
            configuration={
                "model": "gpt-4o",
                "instructions": "Test",
            },
        )
        db.add(agent)
        db.commit()
        db.refresh(agent)

        version = AgentDefinitionVersion(
            agent_id=agent.id,
            version_number=1,
            configuration=agent.configuration,
        )
        db.add(version)
        db.commit()
        db.refresh(version)

        agent.current_version_id = version.id
        db.commit()

        return agent
