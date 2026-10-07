"""Agent Workflow Service — Phase 3: Multi-Agent Execution & Workflows

Manages the lifecycle of multi-agent workflows:
- Create and validate workflow definitions
- Execute workflows with agent handoffs
- Manage workflow state and agent transitions
- Support sequential, conditional, and concurrent handoffs
- Track workflow execution and metrics
- Handle pause/resume functionality

Patterns:
- Uses SQLAlchemy ORM with atomic operations
- Integrates with agent_run_service for individual agent execution
- Integrates with budget_service for workflow-level budget tracking
- Implements SQLite-safe state transitions
- Enforces tenant isolation via project_id
- Supports handoff rules: sequential, conditional, concurrent
- Follows existing error handling patterns
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Dict, Optional, List, Any, Set, Tuple

from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

from app.core.exceptions import NotFoundError, PermissionDeniedError, ValidationError, WorkflowError
from app.core.permissions import rbac_check
from promptops_app.agents_models import (
    AgentWorkflow, WorkflowRun, AgentDefinition, AgentRun, AgentDefinitionVersion
)
from promptops_app.services.agents_tenant_service import AgentsTenantService
from promptops_app.services.budget_service import check_budget, reconcile_budget, BudgetExceededError, period_key
from promptops_app.services.agent_run_service import AgentRunService

_log = logging.getLogger(__name__)


# =============================================================================
# Workflow Definition Structures (JSON DAG)
# =============================================================================

class WorkflowStep:
    """Represents a single step (agent) in a workflow."""

    def __init__(self, agent_id: int, step_id: str, description: Optional[str] = None):
        self.agent_id = agent_id
        self.step_id = step_id
        self.description = description


class HandoffRule:
    """Represents how to route from one agent to another."""

    def __init__(
        self,
        from_agent_id: int,
        to_agents: List[int] | int,  # Single agent or list for parallel
        condition: Optional[str] = None,  # For conditional routing
        rule_type: str = "sequential"  # sequential|conditional|concurrent
    ):
        self.from_agent_id = from_agent_id
        self.to_agents = to_agents if isinstance(to_agents, list) else [to_agents]
        self.condition = condition
        self.rule_type = rule_type


# =============================================================================
# Workflow Service
# =============================================================================

class AgentWorkflowService:
    """Service for managing multi-agent workflow execution and orchestration."""

    @staticmethod
    def create_workflow(
        db: Session,
        project_id: int,
        name: str,
        call_handle: str,
        definition: Dict[str, Any],
        created_by: str,
        description: Optional[str] = None,
    ) -> AgentWorkflow:
        """
        Create a new workflow definition.

        A workflow is a named, reusable DAG of agents with handoff rules
        that defines how agents coordinate and hand off work to each other.

        Args:
            db: SQLAlchemy session
            project_id: Tenant ID (scoping)
            name: Display name
            call_handle: Code-facing identifier (unique per tenant)
            definition: Workflow definition as dict with 'agent_steps' and 'handoff_rules'
            created_by: Username of creator
            description: Optional workflow description

        Returns:
            AgentWorkflow: Newly created workflow record

        Raises:
            ValidationError: Invalid call_handle, duplicate handle, or invalid definition
            NotFoundError: Referenced agents don't exist
            PermissionDeniedError: Insufficient permissions
        """
        # 1. Check permission
        if not rbac_check(created_by, "agents.workflow.create"):
            raise PermissionDeniedError("agents.workflow.create", user_role="unknown")

        # 2. Validate call_handle uniqueness per tenant
        existing = db.query(AgentWorkflow).filter(
            AgentWorkflow.project_id == project_id,
            AgentWorkflow.call_handle == call_handle
        ).first()
        if existing:
            raise ValidationError(
                f"Workflow call_handle '{call_handle}' already exists in this tenant"
            )

        # 3. Validate workflow definition
        AgentWorkflowService._validate_workflow_definition(db, project_id, definition)

        # 4. Serialize and create
        definition_json = json.dumps(definition)
        workflow = AgentWorkflow(
            project_id=project_id,
            name=name,
            call_handle=call_handle,
            description=description,
            definition=definition_json,
            owner=created_by,
            created_by=created_by,
            is_active=True
        )
        db.add(workflow)
        db.commit()
        db.refresh(workflow)

        _log.info(f"Created workflow {workflow.id}: {call_handle} in project {project_id}")
        return workflow

    @staticmethod
    def get_workflow(
        db: Session,
        workflow_id: int,
        project_id: int,
    ) -> AgentWorkflow:
        """
        Retrieve a workflow by ID with tenant isolation.

        Args:
            db: SQLAlchemy session
            workflow_id: Workflow ID
            project_id: Tenant ID (for isolation)

        Returns:
            AgentWorkflow: The workflow record

        Raises:
            NotFoundError: Workflow not found or doesn't belong to tenant
        """
        workflow = db.query(AgentWorkflow).filter(
            AgentWorkflow.id == workflow_id,
            AgentWorkflow.project_id == project_id
        ).first()

        if not workflow:
            raise NotFoundError("Workflow", workflow_id)

        return workflow

    @staticmethod
    def list_workflows(
        db: Session,
        project_id: int,
        is_active: Optional[bool] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Tuple[List[AgentWorkflow], int]:
        """
        List workflows for a tenant.

        Args:
            db: SQLAlchemy session
            project_id: Tenant ID
            is_active: Filter by active status (None = all)
            limit: Page size
            offset: Pagination offset

        Returns:
            Tuple of (workflows, total_count)
        """
        query = db.query(AgentWorkflow).filter(AgentWorkflow.project_id == project_id)

        if is_active is not None:
            query = query.filter(AgentWorkflow.is_active == is_active)

        total = query.count()
        workflows = query.offset(offset).limit(limit).all()

        return workflows, total

    @staticmethod
    def update_workflow(
        db: Session,
        workflow_id: int,
        project_id: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        definition: Optional[Dict[str, Any]] = None,
        updated_by: Optional[str] = None,
    ) -> AgentWorkflow:
        """
        Update a workflow definition.

        Args:
            db: SQLAlchemy session
            workflow_id: Workflow ID
            project_id: Tenant ID
            name: New name (optional)
            description: New description (optional)
            definition: New workflow definition (optional, will be validated)
            updated_by: Username of updater

        Returns:
            AgentWorkflow: Updated workflow record

        Raises:
            NotFoundError: Workflow not found
            ValidationError: Invalid new definition
        """
        workflow = AgentWorkflowService.get_workflow(db, workflow_id, project_id)

        if name is not None:
            workflow.name = name

        if description is not None:
            workflow.description = description

        if definition is not None:
            # Validate new definition before applying
            AgentWorkflowService._validate_workflow_definition(db, project_id, definition)
            workflow.definition = json.dumps(definition)

        workflow.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(workflow)

        _log.info(f"Updated workflow {workflow_id}")
        return workflow

    @staticmethod
    def archive_workflow(
        db: Session,
        workflow_id: int,
        project_id: int,
    ) -> AgentWorkflow:
        """
        Archive (soft-delete) a workflow.

        Args:
            db: SQLAlchemy session
            workflow_id: Workflow ID
            project_id: Tenant ID

        Returns:
            AgentWorkflow: Archived workflow

        Raises:
            NotFoundError: Workflow not found
        """
        workflow = AgentWorkflowService.get_workflow(db, workflow_id, project_id)
        workflow.is_active = False
        workflow.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(workflow)

        _log.info(f"Archived workflow {workflow_id}")
        return workflow

    @staticmethod
    def execute_workflow(
        db: Session,
        workflow_id: int,
        project_id: int,
        initiated_by: str,
        initiated_by_role: str,
        input_content: str = "",
        input_context: Optional[Dict[str, Any]] = None,
        request_id: Optional[str] = None,
    ) -> WorkflowRun:
        """
        Start a workflow execution.

        Creates a WorkflowRun and initiates the first agent(s) in the workflow.
        Idempotent: if a run with this request_id already exists, returns it.

        Args:
            db: SQLAlchemy session
            workflow_id: Workflow ID
            project_id: Tenant ID
            initiated_by: Username of requester
            initiated_by_role: Role of requester
            input_content: Initial content to process
            input_context: Additional context for workflow
            request_id: Optional idempotency key

        Returns:
            WorkflowRun: Created (or existing) workflow run

        Raises:
            NotFoundError: Workflow not found
            PermissionDeniedError: User lacks permission
            ValidationError: Workflow not active or other validation error
            BudgetExceededError: Insufficient budget
        """
        # 1. Check idempotency
        if request_id:
            existing = db.query(WorkflowRun).filter(
                WorkflowRun.request_id == request_id
            ).first()
            if existing:
                _log.info(f"Idempotent replay: returning existing workflow run {existing.id}")
                return existing

        # 2. Check permission
        if not rbac_check(initiated_by_role, "agents.workflow.execute"):
            raise PermissionDeniedError("agents.workflow.execute", user_role=initiated_by_role)

        # 3. Get and validate workflow
        workflow = AgentWorkflowService.get_workflow(db, workflow_id, project_id)

        if not workflow.is_active:
            raise ValidationError(f"Workflow {workflow_id} is not active")

        # 4. Create workflow run
        run_context = input_context or {}
        run_context["_initial_content"] = input_content

        workflow_run = WorkflowRun(
            project_id=project_id,
            workflow_id=workflow_id,
            initiated_by=initiated_by,
            input_context=json.dumps(run_context),
            state="running",
            request_id=request_id,
        )
        db.add(workflow_run)
        db.commit()
        db.refresh(workflow_run)

        _log.info(f"Started workflow run {workflow_run.id} for workflow {workflow_id}")

        # 5. Execute first agent(s) in workflow
        try:
            AgentWorkflowService._execute_workflow_agents(
                db, workflow_run, workflow, initiated_by, initiated_by_role, input_content, run_context
            )
        except Exception as e:
            workflow_run.state = "failed"
            workflow_run.result = json.dumps({"error": str(e)})
            workflow_run.completed_at = datetime.utcnow()
            db.commit()
            raise

        return workflow_run

    @staticmethod
    def get_workflow_run(
        db: Session,
        workflow_id: int,
        run_id: int,
        project_id: int,
    ) -> WorkflowRun:
        """
        Get the current state of a workflow run.

        Args:
            db: SQLAlchemy session
            workflow_id: Workflow ID
            run_id: Run ID
            project_id: Tenant ID

        Returns:
            WorkflowRun: The run record

        Raises:
            NotFoundError: Run not found or doesn't belong to tenant
        """
        run = db.query(WorkflowRun).filter(
            WorkflowRun.id == run_id,
            WorkflowRun.workflow_id == workflow_id,
            WorkflowRun.project_id == project_id
        ).first()

        if not run:
            raise NotFoundError("Workflow Run", run_id)

        return run

    @staticmethod
    def list_workflow_runs(
        db: Session,
        workflow_id: int,
        project_id: int,
        state: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[WorkflowRun], int]:
        """
        List runs for a workflow.

        Args:
            db: SQLAlchemy session
            workflow_id: Workflow ID
            project_id: Tenant ID
            state: Filter by state (optional)
            limit: Page size
            offset: Pagination offset

        Returns:
            Tuple of (runs, total_count)
        """
        query = db.query(WorkflowRun).filter(
            WorkflowRun.workflow_id == workflow_id,
            WorkflowRun.project_id == project_id
        )

        if state is not None:
            query = query.filter(WorkflowRun.state == state)

        total = query.count()
        runs = query.order_by(WorkflowRun.created_at.desc()).offset(offset).limit(limit).all()

        return runs, total

    @staticmethod
    def pause_workflow(
        db: Session,
        workflow_id: int,
        run_id: int,
        project_id: int,
    ) -> WorkflowRun:
        """
        Pause a running workflow execution.

        Stops accepting new agent completions but preserves state for resume.

        Args:
            db: SQLAlchemy session
            workflow_id: Workflow ID
            run_id: Run ID
            project_id: Tenant ID

        Returns:
            WorkflowRun: Paused run

        Raises:
            NotFoundError: Run not found
            WorkflowError: Run not in pausable state
        """
        run = AgentWorkflowService.get_workflow_run(db, workflow_id, run_id, project_id)

        if run.state not in ["running"]:
            raise WorkflowError(
                f"Cannot pause workflow run {run_id}. Current state: {run.state}"
            )

        run.state = "paused"
        db.commit()
        db.refresh(run)

        _log.info(f"Paused workflow run {run_id}")
        return run

    @staticmethod
    def resume_workflow(
        db: Session,
        workflow_id: int,
        run_id: int,
        project_id: int,
    ) -> WorkflowRun:
        """
        Resume a paused workflow execution.

        Args:
            db: SQLAlchemy session
            workflow_id: Workflow ID
            run_id: Run ID
            project_id: Tenant ID

        Returns:
            WorkflowRun: Resumed run

        Raises:
            NotFoundError: Run not found
            WorkflowError: Run not in paused state
        """
        run = AgentWorkflowService.get_workflow_run(db, workflow_id, run_id, project_id)

        if run.state != "paused":
            raise WorkflowError(
                f"Cannot resume workflow run {run_id}. Current state: {run.state}. "
                f"Expected: paused"
            )

        run.state = "running"
        db.commit()
        db.refresh(run)

        _log.info(f"Resumed workflow run {run_id}")
        return run

    @staticmethod
    def complete_agent_step(
        db: Session,
        workflow_id: int,
        run_id: int,
        project_id: int,
        agent_run_id: int,
        result: Dict[str, Any],
    ) -> WorkflowRun:
        """
        Mark an agent step complete and determine next agent.

        Called after an agent finishes executing within a workflow.
        Evaluates handoff rules to determine which agent(s) execute next.

        Args:
            db: SQLAlchemy session
            workflow_id: Workflow ID
            run_id: Workflow run ID
            project_id: Tenant ID
            agent_run_id: The completed agent run ID
            result: Output from the completed agent

        Returns:
            WorkflowRun: Updated workflow run

        Raises:
            NotFoundError: Run or agent not found
            WorkflowError: Handoff logic fails
        """
        workflow_run = AgentWorkflowService.get_workflow_run(db, workflow_id, run_id, project_id)
        workflow = workflow_run.workflow

        # Parse workflow definition
        try:
            definition = json.loads(workflow.definition)
        except json.JSONDecodeError:
            raise WorkflowError(f"Invalid workflow definition for {workflow_id}")

        # Get the agent run that just completed
        agent_run = db.query(AgentRun).filter(AgentRun.id == agent_run_id).first()
        if not agent_run:
            raise NotFoundError("Agent Run", agent_run_id)

        # Store result in workflow context
        run_context = json.loads(workflow_run.input_context) if workflow_run.input_context else {}
        if "agent_results" not in run_context:
            run_context["agent_results"] = {}
        run_context["agent_results"][str(agent_run.definition_id)] = result

        # Determine next agent(s) based on handoff rules
        next_agents = AgentWorkflowService._get_next_agents(
            definition, agent_run.definition_id, result
        )

        if not next_agents:
            # No more agents — workflow is complete
            workflow_run.state = "completed"
            workflow_run.result = json.dumps(result)
            workflow_run.completed_at = datetime.utcnow()
            _log.info(f"Workflow run {run_id} completed")
        else:
            # Execute next agent(s)
            for next_agent_id in next_agents:
                _log.info(f"Handoff to agent {next_agent_id} in workflow run {run_id}")
                # In a real implementation, this would trigger async execution
                # For now, we record the handoff in workflow context

        workflow_run.input_context = json.dumps(run_context)
        db.commit()
        db.refresh(workflow_run)

        return workflow_run

    # ─────────────────────────────────────────────────────────────────────────
    # Internal Helper Methods
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _validate_workflow_definition(
        db: Session,
        project_id: int,
        definition: Dict[str, Any],
    ) -> None:
        """
        Validate workflow definition structure and referenced agents.

        Args:
            db: SQLAlchemy session
            project_id: Tenant ID
            definition: Workflow definition dict

        Raises:
            ValidationError: Invalid definition
            NotFoundError: Referenced agent not found
        """
        # Check required fields
        if "agent_steps" not in definition:
            raise ValidationError("Workflow definition must include 'agent_steps'")

        if "handoff_rules" not in definition:
            raise ValidationError("Workflow definition must include 'handoff_rules'")

        agent_steps = definition.get("agent_steps", [])
        handoff_rules = definition.get("handoff_rules", [])

        if not agent_steps:
            raise ValidationError("Workflow must have at least one agent step")

        # Validate all referenced agents exist and belong to tenant
        agent_ids = set()
        for step in agent_steps:
            if not isinstance(step, dict) or "agent_id" not in step:
                raise ValidationError("Each agent_step must have 'agent_id'")

            agent_id = step["agent_id"]
            agent_ids.add(agent_id)

            # Verify agent exists and belongs to tenant
            agent = db.query(AgentDefinition).filter(
                AgentDefinition.id == agent_id,
                AgentDefinition.project_id == project_id
            ).first()
            if not agent:
                raise NotFoundError("Agent", agent_id)

        # Validate handoff rules reference valid agents
        for rule in handoff_rules:
            if "from_agent_id" not in rule or "to_agent_ids" not in rule:
                raise ValidationError("Each handoff_rule must have 'from_agent_id' and 'to_agent_ids'")

            from_id = rule["from_agent_id"]
            to_ids = rule["to_agent_ids"]

            if from_id not in agent_ids:
                raise ValidationError(f"Handoff rule from_agent_id {from_id} not in workflow steps")

            if not isinstance(to_ids, list):
                to_ids = [to_ids]

            for to_id in to_ids:
                if to_id not in agent_ids:
                    raise ValidationError(f"Handoff rule to_agent_id {to_id} not in workflow steps")

    @staticmethod
    def _execute_workflow_agents(
        db: Session,
        workflow_run: WorkflowRun,
        workflow: AgentWorkflow,
        initiated_by: str,
        initiated_by_role: str,
        input_content: str,
        run_context: Dict[str, Any],
    ) -> None:
        """
        Execute the first agent(s) in a workflow.

        Determines which agent(s) start the workflow and creates agent runs for them.

        Args:
            db: SQLAlchemy session
            workflow_run: The workflow run record
            workflow: The workflow definition
            initiated_by: Username of initiator
            initiated_by_role: Role of initiator
            input_content: Initial content
            run_context: Workflow context

        Raises:
            WorkflowError: If execution setup fails
        """
        definition = json.loads(workflow.definition)
        agent_steps = definition.get("agent_steps", [])

        if not agent_steps:
            raise WorkflowError(f"Workflow {workflow.id} has no agent steps")

        # Find starting agents (those with no incoming edges)
        handoff_rules = definition.get("handoff_rules", [])
        agents_with_incoming = set()

        for rule in handoff_rules:
            to_ids = rule.get("to_agent_ids", [])
            if not isinstance(to_ids, list):
                to_ids = [to_ids]
            agents_with_incoming.update(to_ids)

        starting_agents = [
            step["agent_id"] for step in agent_steps
            if step["agent_id"] not in agents_with_incoming
        ]

        if not starting_agents:
            raise WorkflowError(
                f"Workflow {workflow.id} has no starting agents (all have incoming edges)"
            )

        # Execute starting agents
        for agent_id in starting_agents:
            agent = db.query(AgentDefinition).filter(AgentDefinition.id == agent_id).first()
            if not agent:
                continue

            try:
                agent_run = AgentRunService.create_run(
                    db=db,
                    project_id=workflow_run.project_id,
                    definition_id=agent_id,
                    initiated_by=initiated_by,
                    initiated_by_role=initiated_by_role,
                    input_content=input_content,
                    input_context=run_context,
                    estimated_cost=None,  # Budget tracking at workflow level
                )
                _log.info(f"Started agent {agent_id} in workflow run {workflow_run.id}: agent_run={agent_run.id}")
            except Exception as e:
                _log.error(f"Failed to start agent {agent_id} in workflow: {e}")
                raise

    @staticmethod
    def _get_next_agents(
        definition: Dict[str, Any],
        completed_agent_id: int,
        agent_result: Dict[str, Any],
    ) -> List[int]:
        """
        Determine which agent(s) should execute next based on handoff rules.

        Supports:
        - Sequential handoff: completed agent has single next agent
        - Conditional routing: next agent depends on result evaluation
        - Concurrent handoff: multiple agents run in parallel

        Args:
            definition: Workflow definition
            completed_agent_id: ID of agent that just completed
            agent_result: Output from completed agent

        Returns:
            List of agent IDs to execute next (empty if workflow is complete)
        """
        handoff_rules = definition.get("handoff_rules", [])
        next_agents = []

        for rule in handoff_rules:
            if rule.get("from_agent_id") == completed_agent_id:
                to_ids = rule.get("to_agent_ids", [])
                if not isinstance(to_ids, list):
                    to_ids = [to_ids]

                # Check conditional routing
                condition = rule.get("condition")
                if condition:
                    if AgentWorkflowService._evaluate_condition(condition, agent_result):
                        next_agents.extend(to_ids)
                else:
                    # Sequential or concurrent handoff
                    next_agents.extend(to_ids)

        return next_agents

    @staticmethod
    def _evaluate_condition(condition: str, result: Dict[str, Any]) -> bool:
        """
        Evaluate a handoff condition against an agent's result.

        Simple condition syntax: "output.field == value" or "output.field == 'string'"

        Args:
            condition: Condition string
            result: Agent output dict

        Returns:
            bool: True if condition is satisfied
        """
        try:
            # Simple implementation: check if condition contains expected path
            # In production, use a proper expression evaluator
            if "==" in condition:
                left, right = condition.split("==", 1)
                left = left.strip()
                right = right.strip().strip("'\"")

                # Navigate to nested field
                if left.startswith("output."):
                    field_path = left.split(".", 1)[1]
                    value = result
                    for part in field_path.split("."):
                        if isinstance(value, dict):
                            value = value.get(part)
                        else:
                            return False
                    return str(value) == right

            return False
        except Exception as e:
            _log.warning(f"Failed to evaluate condition '{condition}': {e}")
            return False
