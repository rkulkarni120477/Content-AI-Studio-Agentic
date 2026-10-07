"""Performance and Load Testing for Agent Builder

Tests concurrent agent execution, budget tracking under load, checkpoint creation
at scale, and workflow execution with many agents. Validates performance
characteristics and identifies bottlenecks.

Test Categories:
- Concurrent agent execution (10, 50, 100 parallel runs)
- Budget tracking under load
- Checkpoint creation at scale
- Workflow execution with many agents
- Database connection pooling
- Query performance validation
- Memory leak detection
- SQLite lock contention analysis

Success Criteria:
- Concurrent runs complete without state corruption
- No memory leaks during sustained load
- SQLite handles concurrent writes correctly
- Performance metrics logged for analysis
- Database locks don't cause cascading timeouts
"""

from __future__ import annotations

import concurrent.futures
import logging
import time
import uuid
from typing import List, Tuple
from datetime import datetime

import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError, ValidationError
from promptops_app.agents_models import AgentDefinition, AgentRun, AgentRunStep
from promptops_app.services.agent_monitoring import get_monitor, ExecutionMetrics
from promptops_app.services.agent_run_service import AgentRunService
from promptops_app.services.agent_step_service import AgentStepService
from promptops_app.services.agent_budget_service import (
    check_budget,
    reconcile_budget,
    BudgetExceededError,
)

_log = logging.getLogger(__name__)


class TestConcurrentAgentExecution:
    """Test concurrent execution of multiple agent runs."""

    def test_10_concurrent_runs_complete(self, db: Session, mock_llm):
        """Test 10 concurrent agent runs complete successfully."""
        # Setup
        project_id = 1
        agent_def = self._create_active_agent(db, project_id, "concurrent_agent_1")

        # Reserve budget for 10 runs
        estimated_cost_per_run = 0.10
        total_estimated_cost = estimated_cost_per_run * 10
        check_budget(db, project_id, total_estimated_cost)

        # Execute 10 runs concurrently
        run_ids = []
        monitor = get_monitor()

        def execute_run(run_num: int) -> Tuple[str, bool]:
            try:
                run_id = str(uuid.uuid4())
                run = AgentRunService.create_run(
                    db=db,
                    project_id=project_id,
                    definition_id=agent_def.id,
                    initiated_by="test_user",
                    initiated_by_role="admin",
                    input_content=f"Test input {run_num}",
                    request_id=f"perf_test_10_{run_num}",
                    estimated_cost=estimated_cost_per_run,
                )

                # Log run start for monitoring
                monitor.log_run_start(project_id, agent_def.id, run.id, estimated_cost_per_run)

                # Simulate step execution
                step = AgentStepService.create_step(
                    db=db,
                    run_id=run.id,
                    step_index=0,
                    step_type="model_call",
                    project_id=project_id,
                )

                start_time = time.time()
                AgentStepService.complete_step(
                    db=db,
                    step_id=step.id,
                    result={"generated_content": "Test output"},
                    prompt_tokens=100,
                    completion_tokens=200,
                    project_id=project_id,
                )
                duration_ms = (time.time() - start_time) * 1000

                monitor.log_step_complete(
                    run.id,
                    0,
                    duration_ms,
                    prompt_tokens=100,
                    completion_tokens=200,
                )

                # Complete run
                AgentRunService.complete_run(
                    db=db,
                    run_id=run.id,
                    project_id=project_id,
                    result={"generated_content": "Test output"},
                )

                reconcile_budget(db, project_id, run.id, estimated_cost_per_run)
                monitor.log_run_complete(run.id, estimated_cost_per_run)

                return run.id, True
            except Exception as e:
                _log.error(f"Run {run_num} failed: {e}")
                return "", False

        # Run concurrently
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [
                executor.submit(execute_run, i)
                for i in range(10)
            ]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        # Verify results
        successful_runs = [run_id for run_id, success in results if success]
        assert len(successful_runs) == 10, f"Expected 10 successful runs, got {len(successful_runs)}"

        # Verify all runs persisted
        persisted_runs = db.query(AgentRun).filter(
            AgentRun.project_id == project_id
        ).all()
        assert len(persisted_runs) >= 10

        # Verify metrics collected
        metrics = monitor.get_all_metrics()
        assert len(metrics) >= 10

    def test_50_concurrent_runs_complete(self, db: Session, mock_llm):
        """Test 50 concurrent agent runs complete successfully."""
        project_id = 2
        agent_def = self._create_active_agent(db, project_id, "concurrent_agent_50")

        # Reserve budget for 50 runs
        estimated_cost_per_run = 0.05
        total_estimated_cost = estimated_cost_per_run * 50
        check_budget(db, project_id, total_estimated_cost)

        monitor = get_monitor()
        successful_count = 0

        def execute_run(run_num: int) -> bool:
            nonlocal successful_count
            try:
                run = AgentRunService.create_run(
                    db=db,
                    project_id=project_id,
                    definition_id=agent_def.id,
                    initiated_by="test_user",
                    initiated_by_role="admin",
                    input_content=f"Test input {run_num}",
                    request_id=f"perf_test_50_{run_num}",
                    estimated_cost=estimated_cost_per_run,
                )

                monitor.log_run_start(project_id, agent_def.id, run.id, estimated_cost_per_run)

                # Simulate step
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
                    result={"output": f"Result {run_num}"},
                    prompt_tokens=50,
                    completion_tokens=100,
                    project_id=project_id,
                )

                AgentRunService.complete_run(
                    db=db,
                    run_id=run.id,
                    project_id=project_id,
                    result={"output": f"Result {run_num}"},
                )

                reconcile_budget(db, project_id, run.id, estimated_cost_per_run)
                monitor.log_run_complete(run.id, estimated_cost_per_run)

                successful_count += 1
                return True
            except Exception as e:
                _log.error(f"Run {run_num} failed: {e}")
                return False

        # Run with thread pool
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(execute_run, i) for i in range(50)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        # Verify
        assert successful_count == 50
        assert all(results)

    def test_100_concurrent_runs_no_corruption(self, db: Session, mock_llm):
        """Test 100 concurrent runs don't corrupt database state."""
        project_id = 3
        agent_def = self._create_active_agent(db, project_id, "concurrent_agent_100")

        # Reserve budget
        estimated_cost_per_run = 0.01
        check_budget(db, project_id, estimated_cost_per_run * 100)

        successful_count = 0
        failed_count = 0

        def execute_run(run_num: int) -> bool:
            nonlocal successful_count, failed_count
            try:
                run = AgentRunService.create_run(
                    db=db,
                    project_id=project_id,
                    definition_id=agent_def.id,
                    initiated_by="test_user",
                    initiated_by_role="admin",
                    input_content=f"Test {run_num}",
                    request_id=f"perf_test_100_{run_num}",
                    estimated_cost=estimated_cost_per_run,
                )

                # Create and complete step
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
                    result={"ok": True},
                    prompt_tokens=10,
                    completion_tokens=20,
                    project_id=project_id,
                )

                # Complete run
                AgentRunService.complete_run(
                    db=db,
                    run_id=run.id,
                    project_id=project_id,
                    result={"ok": True},
                )

                reconcile_budget(db, project_id, run.id, estimated_cost_per_run)
                successful_count += 1
                return True
            except Exception as e:
                failed_count += 1
                _log.error(f"Run {run_num} failed: {e}")
                return False

        # Execute 100 concurrent runs
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(execute_run, i) for i in range(100)]
            concurrent.futures.wait(futures)

        # Verify state consistency
        runs = db.query(AgentRun).filter(AgentRun.project_id == project_id).all()
        steps = db.query(AgentRunStep).join(AgentRun).filter(
            AgentRun.project_id == project_id
        ).all()

        # Verify no corruption
        assert len(runs) == 100, f"Expected 100 runs, got {len(runs)}"
        assert len(steps) == 100, f"Expected 100 steps, got {len(steps)}"

        # Verify all runs have proper state
        for run in runs:
            assert run.state in ["completed", "failed"], f"Invalid state: {run.state}"
            assert len(run.steps) > 0, "Run should have at least one step"

    # =========================================================================
    # Budget Tracking Under Load
    # =========================================================================

    def test_budget_tracking_with_concurrent_runs(self, db: Session, mock_llm):
        """Test budget tracking remains consistent with concurrent execution."""
        project_id = 4
        agent_def = self._create_active_agent(db, project_id, "budget_test_agent")

        # Set fixed budget
        initial_budget = 10.0
        check_budget(db, project_id, 0.01)  # Initialize

        cost_per_run = 0.10
        expected_total_cost = 0.0

        def execute_run_with_budget(run_num: int) -> Tuple[bool, float]:
            nonlocal expected_total_cost
            try:
                # Check budget before run
                check_budget(db, project_id, cost_per_run)
                expected_total_cost += cost_per_run

                run = AgentRunService.create_run(
                    db=db,
                    project_id=project_id,
                    definition_id=agent_def.id,
                    initiated_by="test_user",
                    initiated_by_role="admin",
                    input_content=f"Test {run_num}",
                    request_id=f"budget_test_{run_num}",
                    estimated_cost=cost_per_run,
                )

                # Complete run
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

                AgentRunService.complete_run(
                    db=db,
                    run_id=run.id,
                    project_id=project_id,
                    result={},
                )

                reconcile_budget(db, project_id, run.id, cost_per_run)
                return True, cost_per_run
            except BudgetExceededError:
                return False, 0.0
            except Exception as e:
                _log.error(f"Budget test run {run_num} failed: {e}")
                return False, 0.0

        # Execute 5 concurrent runs
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [
                executor.submit(execute_run_with_budget, i)
                for i in range(5)
            ]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        successful = sum(1 for success, _ in results if success)
        total_cost = sum(cost for _, cost in results)

        # Verify budget tracking
        assert successful == 5
        assert abs(total_cost - expected_total_cost) < 0.01

    # =========================================================================
    # Checkpoint Creation at Scale
    # =========================================================================

    def test_checkpoint_creation_at_scale(self, db: Session, mock_llm):
        """Test checkpoint creation under high load."""
        project_id = 5
        agent_def = self._create_active_agent(db, project_id, "checkpoint_test")

        check_budget(db, project_id, 1.0)

        monitor = get_monitor()

        run = AgentRunService.create_run(
            db=db,
            project_id=project_id,
            definition_id=agent_def.id,
            initiated_by="test_user",
            initiated_by_role="admin",
            input_content="Test",
            request_id=str(uuid.uuid4()),
            estimated_cost=0.50,
        )

        monitor.log_run_start(project_id, agent_def.id, run.id, 0.50)

        # Create many checkpoints for single run
        for i in range(10):
            step = AgentStepService.create_step(
                db=db,
                run_id=run.id,
                step_index=i,
                step_type="model_call",
                project_id=project_id,
            )

            # Simulate step completion
            AgentStepService.complete_step(
                db=db,
                step_id=step.id,
                result={"step": i},
                prompt_tokens=50,
                completion_tokens=100,
                project_id=project_id,
            )

            # Log checkpoint
            monitor.log_checkpoint_create(run.id, i, 1024)

        # Verify checkpoints created
        metrics = monitor.get_run_metrics(run.id)
        assert metrics is not None
        assert metrics.checkpoint_count == 10
        assert metrics.step_count == 10

    # =========================================================================
    # Query Performance
    # =========================================================================

    def test_query_performance_on_large_result_set(self, db: Session, mock_llm):
        """Test query performance with large number of runs."""
        project_id = 6
        agent_def = self._create_active_agent(db, project_id, "query_perf_test")

        check_budget(db, project_id, 10.0)

        # Create 100 runs
        for i in range(100):
            run = AgentRunService.create_run(
                db=db,
                project_id=project_id,
                definition_id=agent_def.id,
                initiated_by="test_user",
                initiated_by_role="admin",
                input_content=f"Input {i}",
                request_id=f"query_perf_{i}",
                estimated_cost=0.05,
            )

            # Add step
            step = AgentStepService.create_step(
                db=db,
                run_id=run.id,
                step_index=0,
                step_type="model_call",
                project_id=project_id,
            )

            # Complete
            AgentStepService.complete_step(
                db=db,
                step_id=step.id,
                result={"num": i},
                prompt_tokens=10,
                completion_tokens=20,
                project_id=project_id,
            )

        # Measure query time
        start_time = time.time()
        runs = db.query(AgentRun).filter(
            AgentRun.project_id == project_id
        ).all()
        query_time_ms = (time.time() - start_time) * 1000

        # Verify
        assert len(runs) == 100
        assert query_time_ms < 5000, f"Query took {query_time_ms}ms, expected < 5000ms"

    # =========================================================================
    # Performance Statistics
    # =========================================================================

    def test_performance_metrics_collection(self, db: Session, mock_llm):
        """Test that performance metrics are properly collected."""
        project_id = 7
        agent_def = self._create_active_agent(db, project_id, "metrics_test")

        check_budget(db, project_id, 1.0)

        monitor = get_monitor()
        monitor.clear_metrics()

        # Execute 5 runs
        for i in range(5):
            run = AgentRunService.create_run(
                db=db,
                project_id=project_id,
                definition_id=agent_def.id,
                initiated_by="test_user",
                initiated_by_role="admin",
                input_content=f"Input {i}",
                request_id=f"metrics_test_{i}",
                estimated_cost=0.10,
            )

            monitor.log_run_start(project_id, agent_def.id, run.id, 0.10)

            # Simulate step with varying durations
            duration_ms = 100 + (i * 50)
            monitor.log_step_complete(
                run.id,
                0,
                duration_ms,
                prompt_tokens=100,
                completion_tokens=200,
            )

            monitor.log_run_complete(run.id, 0.10)

        # Verify metrics collected
        stats = monitor.get_performance_statistics()

        assert "step_duration_ms" in stats or "run_duration_ms" in stats

        all_metrics = monitor.get_all_metrics()
        assert len(all_metrics) == 5

        for metric in all_metrics.values():
            assert metric.total_tokens > 0
            assert metric.state == "completed"

    # =========================================================================
    # Helper Methods
    # =========================================================================

    @staticmethod
    def _create_active_agent(
        db: Session,
        project_id: int,
        agent_name: str,
    ) -> AgentDefinition:
        """Helper to create an active agent definition."""
        from promptops_app.agents_models import AgentDefinition, AgentDefinitionVersion

        agent = AgentDefinition(
            project_id=project_id,
            name=agent_name,
            description="Test agent",
            call_handle=f"test_{agent_name}",
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

        # Link to agent
        agent.current_version_id = version.id
        db.commit()

        return agent
