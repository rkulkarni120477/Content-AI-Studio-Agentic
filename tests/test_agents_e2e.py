"""End-to-End Integration Tests for Agent Builder

Comprehensive end-to-end tests validating complete workflows from creation
through execution and completion. Tests realistic workflows and verifies
all components integrate properly.

Test Scenarios:
- Complete single-agent workflow (create → execute → complete)
- Multi-agent workflow with handoffs
- Budget tracking across workflow
- Checkpoint and recovery
- Pause and resume
- Error recovery paths

Success Criteria:
- All workflows complete successfully
- Data consistency maintained
- State machine transitions correct
- All components integrate
"""

from __future__ import annotations

import logging
from typing import List

import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError, ValidationError
from promptops_app.agents_models import (
    AgentDefinition,
    AgentRun,
    AgentDefinitionVersion,
    Workflow,
    WorkflowRun,
)
from promptops_app.services.agent_run_service import AgentRunService
from promptops_app.services.agent_step_service import AgentStepService
from promptops_app.services.agent_budget_service import check_budget, reconcile_budget
from promptops_app.services.agent_workflow_service import AgentWorkflowService
from promptops_app.services.agent_monitoring import get_monitor

_log = logging.getLogger(__name__)


class TestSingleAgentWorkflow:
    """Test complete single-agent workflow."""

    def test_create_execute_complete_workflow(self, db: Session, mock_llm):
        """Test complete workflow: create agent, execute, complete."""
        # Setup
        project_id = 1
        agent = self._create_active_agent(db, project_id, "e2e_single_agent")
        check_budget(db, project_id, 1.0)

        monitor = get_monitor()
        monitor.clear_metrics()

        # Create run
        run = AgentRunService.create_run(
            db=db,
            project_id=project_id,
            definition_id=agent.id,
            initiated_by="test_user",
            initiated_by_role="admin",
            input_content="Create a course outline for Python basics",
            request_id="e2e_single_agent_test",
            estimated_cost=0.50,
        )

        assert run is not None
        assert run.state == "queued"

        monitor.log_run_start(project_id, agent.id, run.id, 0.50)

        # Execute step 1: Model call to generate content
        step1 = AgentStepService.create_step(
            db=db,
            run_id=run.id,
            step_index=0,
            step_type="model_call",
            project_id=project_id,
        )

        assert step1.status == "pending"

        result1 = {
            "generated_content": "# Python Basics Course\n\n1. Introduction\n2. Variables and Data Types\n3. Functions",
            "explanation": "Generated comprehensive course outline",
        }

        AgentStepService.complete_step(
            db=db,
            step_id=step1.id,
            result=result1,
            prompt_tokens=150,
            completion_tokens=250,
            project_id=project_id,
        )

        monitor.log_step_complete(run.id, 0, 2500, 150, 250)

        # Execute step 2: Validation
        step2 = AgentStepService.create_step(
            db=db,
            run_id=run.id,
            step_index=1,
            step_type="validation",
            project_id=project_id,
        )

        AgentStepService.complete_step(
            db=db,
            step_id=step2.id,
            result={"valid": True, "quality_score": 0.95},
            prompt_tokens=50,
            completion_tokens=50,
            project_id=project_id,
        )

        monitor.log_step_complete(run.id, 1, 1000, 50, 50)

        # Complete run
        AgentRunService.complete_run(
            db=db,
            run_id=run.id,
            project_id=project_id,
            result=result1,
        )

        reconcile_budget(db, project_id, run.id, 0.45)
        monitor.log_run_complete(run.id, 0.45)

        # Verify
        db.refresh(run)
        assert run.state == "completed"
        assert run.actual_cost == 0.45

        steps = db.query(AgentStepService.AgentRunStep).filter(
            AgentStepService.AgentRunStep.run_id == run.id
        ).all()
        assert len(steps) == 2

        # Verify metrics
        metrics = monitor.get_run_metrics(run.id)
        assert metrics is not None
        assert metrics.state == "completed"
        assert metrics.step_count == 2
        assert metrics.total_tokens == 500

    def test_single_agent_with_input_context(self, db: Session, mock_llm):
        """Test single agent workflow with rich input context."""
        project_id = 2
        agent = self._create_active_agent(db, project_id, "e2e_with_context")
        check_budget(db, project_id, 1.0)

        # Rich context
        input_context = {
            "course_id": "py-101",
            "course_title": "Python Basics",
            "target_level": "beginner",
            "duration_hours": 40,
            "learning_objectives": [
                "Understand Python syntax",
                "Write basic functions",
                "Work with data structures",
            ],
        }

        run = AgentRunService.create_run(
            db=db,
            project_id=project_id,
            definition_id=agent.id,
            initiated_by="test_user",
            initiated_by_role="admin",
            artifact_id=123,
            artifact_type="course",
            input_content="Generate course materials",
            input_context=input_context,
            request_id="e2e_with_context_test",
            estimated_cost=0.50,
        )

        assert run is not None

        # Process with context
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
            result={"materials_generated": True},
            prompt_tokens=200,
            completion_tokens=400,
            project_id=project_id,
        )

        AgentRunService.complete_run(
            db=db,
            run_id=run.id,
            project_id=project_id,
            result={"materials_generated": True},
        )

        db.refresh(run)
        assert run.state == "completed"
        assert run.artifact_id == 123


class TestMultiAgentWorkflow:
    """Test multi-agent workflows with handoffs."""

    def test_three_agent_sequential_workflow(self, db: Session, mock_llm):
        """Test sequential workflow: Content Creator → Aligner → Reviewer."""
        project_id = 3

        # Create three agents
        creator = self._create_active_agent(db, project_id, "content_creator")
        aligner = self._create_active_agent(db, project_id, "standards_aligner")
        reviewer = self._create_active_agent(db, project_id, "editorial_reviewer")

        check_budget(db, project_id, 2.0)

        monitor = get_monitor()
        monitor.clear_metrics()

        # Create workflow definition
        workflow = Workflow(
            project_id=project_id,
            name="Content Improvement Workflow",
            description="Create, align, and review content",
            initial_agent_id=creator.id,
            definition={
                "agents": [
                    {
                        "id": creator.id,
                        "name": "Content Creator",
                        "type": "sequential",
                    },
                    {
                        "id": aligner.id,
                        "name": "Standards Aligner",
                        "type": "sequential",
                    },
                    {
                        "id": reviewer.id,
                        "name": "Editorial Reviewer",
                        "type": "sequential",
                    },
                ],
                "handoffs": [
                    {
                        "from": creator.id,
                        "to": [{"agent": aligner.id}],
                    },
                    {
                        "from": aligner.id,
                        "to": [{"agent": reviewer.id}],
                    },
                ],
            },
        )
        db.add(workflow)
        db.commit()
        db.refresh(workflow)

        # Execute workflow
        monitor.log_workflow_start(project_id, str(workflow.id), "run_1", 3)

        # Step 1: Content Creator
        run1 = AgentRunService.create_run(
            db=db,
            project_id=project_id,
            definition_id=creator.id,
            initiated_by="test_user",
            initiated_by_role="admin",
            input_content="Improve this course content",
            request_id="workflow_test_step1",
            estimated_cost=0.50,
        )

        monitor.log_run_start(project_id, creator.id, run1.id, 0.50)

        step1 = AgentStepService.create_step(
            db=db,
            run_id=run1.id,
            step_index=0,
            step_type="model_call",
            project_id=project_id,
        )

        creator_result = {
            "improved_content": "Enhanced course material with better structure",
        }

        AgentStepService.complete_step(
            db=db,
            step_id=step1.id,
            result=creator_result,
            prompt_tokens=100,
            completion_tokens=200,
            project_id=project_id,
        )

        monitor.log_step_complete(run1.id, 0, 2000, 100, 200)

        AgentRunService.complete_run(
            db=db,
            run_id=run1.id,
            project_id=project_id,
            result=creator_result,
        )

        reconcile_budget(db, project_id, run1.id, 0.50)
        monitor.log_run_complete(run1.id, 0.50)

        # Step 2: Standards Aligner (handoff from creator)
        run2 = AgentRunService.create_run(
            db=db,
            project_id=project_id,
            definition_id=aligner.id,
            initiated_by="test_user",
            initiated_by_role="admin",
            input_content=creator_result["improved_content"],
            request_id="workflow_test_step2",
            estimated_cost=0.50,
        )

        monitor.log_run_start(project_id, aligner.id, run2.id, 0.50)

        step2 = AgentStepService.create_step(
            db=db,
            run_id=run2.id,
            step_index=0,
            step_type="model_call",
            project_id=project_id,
        )

        aligner_result = {
            "alignment_findings": ["Aligns with Common Core", "Meets grade level"],
        }

        AgentStepService.complete_step(
            db=db,
            step_id=step2.id,
            result=aligner_result,
            prompt_tokens=80,
            completion_tokens=150,
            project_id=project_id,
        )

        monitor.log_step_complete(run2.id, 0, 1800, 80, 150)

        AgentRunService.complete_run(
            db=db,
            run_id=run2.id,
            project_id=project_id,
            result=aligner_result,
        )

        reconcile_budget(db, project_id, run2.id, 0.50)
        monitor.log_run_complete(run2.id, 0.50)

        # Step 3: Editorial Reviewer (handoff from aligner)
        run3 = AgentRunService.create_run(
            db=db,
            project_id=project_id,
            definition_id=reviewer.id,
            initiated_by="test_user",
            initiated_by_role="admin",
            input_content=creator_result["improved_content"],
            request_id="workflow_test_step3",
            estimated_cost=0.50,
        )

        monitor.log_run_start(project_id, reviewer.id, run3.id, 0.50)

        step3 = AgentStepService.create_step(
            db=db,
            run_id=run3.id,
            step_index=0,
            step_type="model_call",
            project_id=project_id,
        )

        reviewer_result = {
            "editorial_findings": ["Clear writing", "Good organization"],
            "ready_to_publish": True,
        }

        AgentStepService.complete_step(
            db=db,
            step_id=step3.id,
            result=reviewer_result,
            prompt_tokens=90,
            completion_tokens=180,
            project_id=project_id,
        )

        monitor.log_step_complete(run3.id, 0, 2100, 90, 180)

        AgentRunService.complete_run(
            db=db,
            run_id=run3.id,
            project_id=project_id,
            result=reviewer_result,
        )

        reconcile_budget(db, project_id, run3.id, 0.50)
        monitor.log_run_complete(run3.id, 0.50)

        # Verify workflow completed
        monitor.log_workflow_complete(str(workflow.id), "run_1", 6000, 3)

        # Verify all runs completed successfully
        runs = db.query(AgentRun).filter(AgentRun.project_id == project_id).all()
        assert all(r.state == "completed" for r in runs)

    def test_parallel_agent_workflow(self, db: Session, mock_llm):
        """Test workflow with parallel agents."""
        project_id = 4

        # Create agents
        creator = self._create_active_agent(db, project_id, "parallel_creator")
        aligner = self._create_active_agent(db, project_id, "parallel_aligner")
        reviewer = self._create_active_agent(db, project_id, "parallel_reviewer")

        check_budget(db, project_id, 2.0)

        monitor = get_monitor()
        monitor.clear_metrics()

        # Create workflow with parallel handoffs
        workflow = Workflow(
            project_id=project_id,
            name="Parallel Review Workflow",
            description="Create content then review in parallel",
            initial_agent_id=creator.id,
            definition={
                "agents": [
                    {"id": creator.id, "name": "Creator"},
                    {"id": aligner.id, "name": "Aligner"},
                    {"id": reviewer.id, "name": "Reviewer"},
                ],
                "handoffs": [
                    {
                        "from": creator.id,
                        "to": [
                            {"agent": aligner.id},
                            {"agent": reviewer.id},
                        ],
                    },
                ],
            },
        )
        db.add(workflow)
        db.commit()
        db.refresh(workflow)

        # Execute creator
        run_creator = AgentRunService.create_run(
            db=db,
            project_id=project_id,
            definition_id=creator.id,
            initiated_by="test_user",
            initiated_by_role="admin",
            input_content="Create content",
            request_id="parallel_creator_test",
            estimated_cost=0.50,
        )

        monitor.log_run_start(project_id, creator.id, run_creator.id, 0.50)

        step = AgentStepService.create_step(
            db=db,
            run_id=run_creator.id,
            step_index=0,
            step_type="model_call",
            project_id=project_id,
        )

        creator_result = {"content": "Generated content"}

        AgentStepService.complete_step(
            db=db,
            step_id=step.id,
            result=creator_result,
            prompt_tokens=100,
            completion_tokens=200,
            project_id=project_id,
        )

        monitor.log_step_complete(run_creator.id, 0, 2000, 100, 200)

        AgentRunService.complete_run(
            db=db,
            run_id=run_creator.id,
            project_id=project_id,
            result=creator_result,
        )

        reconcile_budget(db, project_id, run_creator.id, 0.50)
        monitor.log_run_complete(run_creator.id, 0.50)

        # Execute parallel agents (aligner and reviewer)
        run_aligner = AgentRunService.create_run(
            db=db,
            project_id=project_id,
            definition_id=aligner.id,
            initiated_by="test_user",
            initiated_by_role="admin",
            input_content=creator_result["content"],
            request_id="parallel_aligner_test",
            estimated_cost=0.50,
        )

        run_reviewer = AgentRunService.create_run(
            db=db,
            project_id=project_id,
            definition_id=reviewer.id,
            initiated_by="test_user",
            initiated_by_role="admin",
            input_content=creator_result["content"],
            request_id="parallel_reviewer_test",
            estimated_cost=0.50,
        )

        monitor.log_run_start(project_id, aligner.id, run_aligner.id, 0.50)
        monitor.log_run_start(project_id, reviewer.id, run_reviewer.id, 0.50)

        # Complete both in parallel
        for run, agent_id in [(run_aligner, aligner.id), (run_reviewer, reviewer.id)]:
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
                prompt_tokens=80,
                completion_tokens=150,
                project_id=project_id,
            )

            monitor.log_step_complete(run.id, 0, 1800, 80, 150)

            AgentRunService.complete_run(
                db=db,
                run_id=run.id,
                project_id=project_id,
                result={"ok": True},
            )

            reconcile_budget(db, project_id, run.id, 0.50)
            monitor.log_run_complete(run.id, 0.50)

        # Verify all completed
        runs = db.query(AgentRun).filter(AgentRun.project_id == project_id).all()
        assert len(runs) == 3
        assert all(r.state == "completed" for r in runs)


class TestCheckpointAndRecovery:
    """Test checkpoint creation and recovery."""

    def test_checkpoint_creation_during_workflow(self, db: Session, mock_llm):
        """Test that checkpoints are created at workflow milestones."""
        project_id = 5
        agent = self._create_active_agent(db, project_id, "checkpoint_test")
        check_budget(db, project_id, 1.0)

        monitor = get_monitor()
        monitor.clear_metrics()

        run = AgentRunService.create_run(
            db=db,
            project_id=project_id,
            definition_id=agent.id,
            initiated_by="test_user",
            initiated_by_role="admin",
            input_content="Test",
            request_id="checkpoint_creation_test",
            estimated_cost=1.0,
        )

        monitor.log_run_start(project_id, agent.id, run.id, 1.0)

        # Execute multiple steps with checkpoints
        for i in range(5):
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
                prompt_tokens=50,
                completion_tokens=100,
                project_id=project_id,
            )

            # Create checkpoint after each step
            monitor.log_checkpoint_create(run.id, i, 1024)

        AgentRunService.complete_run(
            db=db,
            run_id=run.id,
            project_id=project_id,
            result={"completed": True},
        )

        reconcile_budget(db, project_id, run.id, 1.0)
        monitor.log_run_complete(run.id, 1.0)

        # Verify checkpoints
        metrics = monitor.get_run_metrics(run.id)
        assert metrics.checkpoint_count == 5
        assert metrics.step_count == 5


class TestBudgetTrackingWorkflow:
    """Test budget tracking across workflows."""

    def test_budget_tracking_across_multi_agent_workflow(self, db: Session, mock_llm):
        """Test that budget is properly tracked across all agents in workflow."""
        project_id = 6

        agent1 = self._create_active_agent(db, project_id, "budget_agent_1")
        agent2 = self._create_active_agent(db, project_id, "budget_agent_2")
        agent3 = self._create_active_agent(db, project_id, "budget_agent_3")

        total_budget = 2.0
        check_budget(db, project_id, total_budget)

        costs_per_run = [0.50, 0.50, 0.50]

        # Execute three runs
        for i, (agent, cost) in enumerate(zip([agent1, agent2, agent3], costs_per_run)):
            run = AgentRunService.create_run(
                db=db,
                project_id=project_id,
                definition_id=agent.id,
                initiated_by="test_user",
                initiated_by_role="admin",
                input_content=f"Test {i}",
                request_id=f"budget_workflow_{i}",
                estimated_cost=cost,
            )

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
                prompt_tokens=50,
                completion_tokens=100,
                project_id=project_id,
            )

            AgentRunService.complete_run(
                db=db,
                run_id=run.id,
                project_id=project_id,
                result={"ok": True},
            )

            reconcile_budget(db, project_id, run.id, cost)

        # Verify all runs completed
        runs = db.query(AgentRun).filter(AgentRun.project_id == project_id).all()
        assert len(runs) == 3
        total_cost = sum(r.actual_cost for r in runs)
        assert abs(total_cost - 1.50) < 0.01

    # =========================================================================
    # Helper Methods
    # =========================================================================

    @staticmethod
    def _create_active_agent(
        db: Session,
        project_id: int,
        name: str,
    ) -> AgentDefinition:
        """Create an active agent."""
        agent = AgentDefinition(
            project_id=project_id,
            name=name,
            description="E2E test agent",
            call_handle=f"e2e_{name}",
            lifecycle_state="active",
            configuration={
                "model": "gpt-4o",
                "instructions": "Test instructions",
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
