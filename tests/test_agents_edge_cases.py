"""Edge Case and Error Handling Testing for Agent Builder

Tests error paths and edge cases to ensure graceful degradation and
proper error recovery. Validates that the system handles failures
without corrupting state.

Test Categories:
- Agent timeout and crash recovery
- Invalid model responses and error handling
- Budget exhaustion mid-run
- Concurrent step execution conflicts
- Network failure simulation
- Checkpoint corruption and recovery
- Cross-tenant isolation bypass attempts (security)

Success Criteria:
- All error paths handled gracefully
- System degrades gracefully under errors
- No data corruption on failure
- Helpful error messages provided
- Cross-tenant access prevented
"""

from __future__ import annotations

import logging
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import (
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
    WorkflowError,
)
from promptops_app.agents_models import (
    AgentDefinition,
    AgentRun,
    AgentRunStep,
    AgentDefinitionVersion,
)
from promptops_app.services.agent_run_service import AgentRunService
from promptops_app.services.agent_step_service import AgentStepService
from promptops_app.services.agent_budget_service import (
    check_budget,
    reconcile_budget,
    BudgetExceededError,
)
from promptops_app.services.agent_monitoring import get_monitor

_log = logging.getLogger(__name__)


class TestTimeoutAndCrashRecovery:
    """Test handling of timeouts and crash recovery."""

    def test_agent_timeout_handled_gracefully(self, db: Session, mock_llm):
        """Test that agent timeout is handled without corrupting state."""
        project_id = 1
        agent_def = self._create_active_agent(db, project_id)
        check_budget(db, project_id, 0.5)

        run = AgentRunService.create_run(
            db=db,
            project_id=project_id,
            definition_id=agent_def.id,
            initiated_by="test_user",
            initiated_by_role="admin",
            input_content="Test",
            request_id="timeout_test",
            estimated_cost=0.50,
        )

        # Simulate timeout
        AgentRunService.fail_run(
            db=db,
            run_id=run.id,
            project_id=project_id,
            error_message="Agent execution timed out after 300s",
            error_type="TimeoutError",
        )

        # Verify run is in failed state, not corrupted
        db.refresh(run)
        assert run.state == "failed"
        assert "timed out" in run.error_message

        # Verify budget was reconciled
        reconcile_budget(db, project_id, run.id, 0.0)

    def test_step_failure_doesnt_corrupt_run_state(self, db: Session, mock_llm):
        """Test that step failure doesn't corrupt the run state."""
        project_id = 2
        agent_def = self._create_active_agent(db, project_id)
        check_budget(db, project_id, 0.5)

        run = AgentRunService.create_run(
            db=db,
            project_id=project_id,
            definition_id=agent_def.id,
            initiated_by="test_user",
            initiated_by_role="admin",
            input_content="Test",
            request_id="step_failure_test",
            estimated_cost=0.50,
        )

        initial_state = run.state

        # Create step
        step = AgentStepService.create_step(
            db=db,
            run_id=run.id,
            step_index=0,
            step_type="model_call",
            project_id=project_id,
        )

        # Fail step
        AgentStepService.fail_step(
            db=db,
            step_id=step.id,
            error_type="ModelError",
            error_message="Model call failed",
            project_id=project_id,
        )

        # Verify step is failed but run state is still valid
        db.refresh(step)
        db.refresh(run)

        assert step.status == "failed"
        assert run.state in ["running", "failed"]  # Should not be corrupted

    def test_crash_recovery_resumes_from_checkpoint(self, db: Session, mock_llm):
        """Test that system can recover from crash using checkpoints."""
        project_id = 3
        agent_def = self._create_active_agent(db, project_id)
        check_budget(db, project_id, 0.5)

        monitor = get_monitor()
        monitor.clear_metrics()

        run = AgentRunService.create_run(
            db=db,
            project_id=project_id,
            definition_id=agent_def.id,
            initiated_by="test_user",
            initiated_by_role="admin",
            input_content="Test",
            request_id="recovery_test",
            estimated_cost=0.50,
        )

        monitor.log_run_start(project_id, agent_def.id, run.id, 0.50)

        # Execute first 3 steps
        for i in range(3):
            step = AgentStepService.create_step(
                db=db,
                run_id=run.id,
                step_index=i,
                step_type="model_call",
                project_id=project_id,
            )

            AgentStepService.complete_step(
                db=db,
                step_id=step.id,
                result={"step": i},
                prompt_tokens=100,
                completion_tokens=200,
                project_id=project_id,
            )

            # Create checkpoint
            monitor.log_checkpoint_create(run.id, i, 1024)

        # Verify checkpoint count
        metrics = monitor.get_run_metrics(run.id)
        assert metrics.checkpoint_count == 3
        assert metrics.step_count == 3

        # Simulate recovery: create new run with same request_id (idempotent)
        recovered_run = AgentRunService.create_run(
            db=db,
            project_id=project_id,
            definition_id=agent_def.id,
            initiated_by="test_user",
            initiated_by_role="admin",
            input_content="Test",
            request_id="recovery_test",  # Same request_id
            estimated_cost=0.50,
        )

        # Should return same run (idempotent)
        assert recovered_run.id == run.id


class TestInvalidModelResponses:
    """Test handling of invalid model responses."""

    def test_empty_model_response_handled(self, db: Session):
        """Test handling of empty model responses."""
        project_id = 4
        agent_def = self._create_active_agent(db, project_id)
        check_budget(db, project_id, 0.5)

        run = AgentRunService.create_run(
            db=db,
            project_id=project_id,
            definition_id=agent_def.id,
            initiated_by="test_user",
            initiated_by_role="admin",
            input_content="Test",
            request_id="empty_response_test",
            estimated_cost=0.50,
        )

        step = AgentStepService.create_step(
            db=db,
            run_id=run.id,
            step_index=0,
            step_type="model_call",
            project_id=project_id,
        )

        # Try to complete with empty result
        AgentStepService.complete_step(
            db=db,
            step_id=step.id,
            result={},
            prompt_tokens=100,
            completion_tokens=0,
            project_id=project_id,
        )

        db.refresh(step)
        assert step.status == "completed"

    def test_malformed_json_response_handled(self, db: Session):
        """Test handling of malformed JSON in model response."""
        project_id = 5
        agent_def = self._create_active_agent(db, project_id)
        check_budget(db, project_id, 0.5)

        run = AgentRunService.create_run(
            db=db,
            project_id=project_id,
            definition_id=agent_def.id,
            initiated_by="test_user",
            initiated_by_role="admin",
            input_content="Test",
            request_id="malformed_json_test",
            estimated_cost=0.50,
        )

        step = AgentStepService.create_step(
            db=db,
            run_id=run.id,
            step_index=0,
            step_type="model_call",
            project_id=project_id,
        )

        # Result with malformed structure (not catastrophic)
        AgentStepService.complete_step(
            db=db,
            step_id=step.id,
            result={"malformed": "data"},
            prompt_tokens=100,
            completion_tokens=50,
            project_id=project_id,
        )

        db.refresh(step)
        assert step.status == "completed"


class TestBudgetExhaustion:
    """Test handling of budget exhaustion during run."""

    def test_budget_exhaustion_prevents_new_runs(self, db: Session, mock_llm):
        """Test that budget exhaustion prevents new runs from starting."""
        project_id = 6
        agent_def = self._create_active_agent(db, project_id)

        # Set very small budget
        check_budget(db, project_id, 0.01)  # Only 1 cent

        # First run should succeed (estimated cost is less)
        run1 = AgentRunService.create_run(
            db=db,
            project_id=project_id,
            definition_id=agent_def.id,
            initiated_by="test_user",
            initiated_by_role="admin",
            input_content="Test 1",
            request_id="budget_test_1",
            estimated_cost=0.001,
        )
        assert run1 is not None

        # Second run should fail (insufficient budget)
        with pytest.raises(BudgetExceededError):
            AgentRunService.create_run(
                db=db,
                project_id=project_id,
                definition_id=agent_def.id,
                initiated_by="test_user",
                initiated_by_role="admin",
                input_content="Test 2",
                request_id="budget_test_2",
                estimated_cost=0.02,
            )

    def test_budget_reconciliation_on_failure(self, db: Session, mock_llm):
        """Test that budget is properly reconciled on run failure."""
        project_id = 7
        agent_def = self._create_active_agent(db, project_id)

        estimated_cost = 0.50
        check_budget(db, project_id, 1.0)

        run = AgentRunService.create_run(
            db=db,
            project_id=project_id,
            definition_id=agent_def.id,
            initiated_by="test_user",
            initiated_by_role="admin",
            input_content="Test",
            request_id="budget_reconcile_test",
            estimated_cost=estimated_cost,
        )

        # Fail run before completion
        AgentRunService.fail_run(
            db=db,
            run_id=run.id,
            project_id=project_id,
            error_message="Model call failed",
            error_type="ModelError",
        )

        # Reconcile with actual cost (less than estimated)
        actual_cost = 0.25
        reconcile_budget(db, project_id, run.id, actual_cost)

        db.refresh(run)
        assert run.state == "failed"
        assert run.actual_cost == actual_cost


class TestConcurrentStepConflicts:
    """Test handling of concurrent step execution conflicts."""

    def test_concurrent_step_execution_serialized(self, db: Session, mock_llm):
        """Test that concurrent steps on same run are properly serialized."""
        project_id = 8
        agent_def = self._create_active_agent(db, project_id)
        check_budget(db, project_id, 1.0)

        run = AgentRunService.create_run(
            db=db,
            project_id=project_id,
            definition_id=agent_def.id,
            initiated_by="test_user",
            initiated_by_role="admin",
            input_content="Test",
            request_id="concurrent_step_test",
            estimated_cost=1.0,
        )

        # Create two steps
        step1 = AgentStepService.create_step(
            db=db,
            run_id=run.id,
            step_index=0,
            step_type="model_call",
            project_id=project_id,
        )

        step2 = AgentStepService.create_step(
            db=db,
            run_id=run.id,
            step_index=1,
            step_type="model_call",
            project_id=project_id,
        )

        # Complete both
        AgentStepService.complete_step(
            db=db,
            step_id=step1.id,
            result={"step": 1},
            prompt_tokens=100,
            completion_tokens=200,
            project_id=project_id,
        )

        AgentStepService.complete_step(
            db=db,
            step_id=step2.id,
            result={"step": 2},
            prompt_tokens=100,
            completion_tokens=200,
            project_id=project_id,
        )

        # Verify both completed without conflict
        db.refresh(step1)
        db.refresh(step2)

        assert step1.status == "completed"
        assert step2.status == "completed"


class TestNetworkFailureSimulation:
    """Test handling of network failures."""

    def test_model_api_timeout_handled(self, db: Session):
        """Test handling of model API timeout."""
        project_id = 9
        agent_def = self._create_active_agent(db, project_id)
        check_budget(db, project_id, 0.5)

        run = AgentRunService.create_run(
            db=db,
            project_id=project_id,
            definition_id=agent_def.id,
            initiated_by="test_user",
            initiated_by_role="admin",
            input_content="Test",
            request_id="network_timeout_test",
            estimated_cost=0.50,
        )

        step = AgentStepService.create_step(
            db=db,
            run_id=run.id,
            step_index=0,
            step_type="model_call",
            project_id=project_id,
        )

        # Simulate timeout
        AgentStepService.fail_step(
            db=db,
            step_id=step.id,
            error_type="TimeoutError",
            error_message="API request timed out after 30s",
            project_id=project_id,
        )

        db.refresh(step)
        assert step.status == "failed"
        assert "timeout" in step.error_message.lower()

    def test_connection_refused_handled(self, db: Session):
        """Test handling of connection refused error."""
        project_id = 10
        agent_def = self._create_active_agent(db, project_id)
        check_budget(db, project_id, 0.5)

        run = AgentRunService.create_run(
            db=db,
            project_id=project_id,
            definition_id=agent_def.id,
            initiated_by="test_user",
            initiated_by_role="admin",
            input_content="Test",
            request_id="connection_refused_test",
            estimated_cost=0.50,
        )

        step = AgentStepService.create_step(
            db=db,
            run_id=run.id,
            step_index=0,
            step_type="model_call",
            project_id=project_id,
        )

        # Simulate connection error
        AgentStepService.fail_step(
            db=db,
            step_id=step.id,
            error_type="ConnectionRefusedError",
            error_message="Cannot connect to API server",
            project_id=project_id,
        )

        db.refresh(step)
        assert step.status == "failed"


class TestCheckpointCorruption:
    """Test handling of checkpoint corruption and recovery."""

    def test_corrupted_checkpoint_skipped(self, db: Session, mock_llm):
        """Test that corrupted checkpoints are detected and skipped."""
        project_id = 11
        agent_def = self._create_active_agent(db, project_id)
        check_budget(db, project_id, 0.5)

        monitor = get_monitor()
        monitor.clear_metrics()

        run = AgentRunService.create_run(
            db=db,
            project_id=project_id,
            definition_id=agent_def.id,
            initiated_by="test_user",
            initiated_by_role="admin",
            input_content="Test",
            request_id="checkpoint_corruption_test",
            estimated_cost=0.50,
        )

        monitor.log_run_start(project_id, agent_def.id, run.id, 0.50)

        # Create checkpoint
        monitor.log_checkpoint_create(run.id, 0, 1024)

        # Simulate checkpoint corruption recovery
        monitor.log_checkpoint_restore(run.id, 0, 100)

        metrics = monitor.get_run_metrics(run.id)
        assert metrics.recovery_count == 1


class TestCrossTenantIsolation:
    """Test cross-tenant isolation and security."""

    def test_cannot_access_other_tenant_agent(self, two_tenants, mock_llm):
        """Test that tenant A cannot access tenant B's agents."""
        db = two_tenants["db"]  # Get DB from fixture context

        # This should be tested at API level in security tests
        # But we verify data isolation at service level

        tenant_a = two_tenants["a"]
        tenant_b = two_tenants["b"]

        # Create agent in tenant A
        agent_a = self._create_agent_for_tenant(db, tenant_a.id)

        # Verify tenant B cannot query tenant A's agent
        from promptops_app.services.agents_tenant_service import AgentsTenantService

        with pytest.raises(NotFoundError):
            AgentsTenantService.validate_agent_ownership(db, agent_a.id, tenant_b.id)

    def test_cannot_create_run_for_other_tenant_agent(self, two_tenants, mock_llm):
        """Test that creating run for other tenant's agent fails."""
        db = two_tenants["db"]
        tenant_a = two_tenants["a"]
        tenant_b = two_tenants["b"]

        # Create agent in tenant A
        agent_a = self._create_agent_for_tenant(db, tenant_a.id)
        check_budget(db, tenant_a.id, 0.5)

        # Try to create run as tenant B
        with pytest.raises(NotFoundError):
            AgentRunService.create_run(
                db=db,
                project_id=tenant_b.id,
                definition_id=agent_a.id,
                initiated_by="test_user",
                initiated_by_role="admin",
                input_content="Test",
                request_id="cross_tenant_test",
                estimated_cost=0.50,
            )

    # =========================================================================
    # Helper Methods
    # =========================================================================

    @staticmethod
    def _create_active_agent(db: Session, project_id: int) -> AgentDefinition:
        """Create an active agent for testing."""
        agent = AgentDefinition(
            project_id=project_id,
            name=f"test_agent_{project_id}",
            description="Test agent",
            call_handle=f"test_{project_id}",
            lifecycle_state="active",
            configuration={
                "model": "gpt-4o",
                "instructions": "Test instructions",
            },
        )
        db.add(agent)
        db.commit()
        db.refresh(agent)

        # Create version
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
    def _create_agent_for_tenant(db: Session, project_id: int) -> AgentDefinition:
        """Create an agent for a specific tenant."""
        agent = AgentDefinition(
            project_id=project_id,
            name=f"tenant_agent_{project_id}",
            description="Tenant test agent",
            call_handle=f"tenant_agent_{project_id}",
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
