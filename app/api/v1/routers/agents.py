"""
Agent Builder API Routes — Stage 6 of Phase 2

REST API endpoints for single-agent execution with full persistence,
error handling, and integration with LLM and budget systems.

Endpoints:
  Agent Management:
    POST   /agents                    - Create new agent
    GET    /agents                    - List tenant's agents (paginated)
    GET    /agents/{agent_id}         - Get agent details
    PUT    /agents/{agent_id}         - Update agent configuration
    DELETE /agents/{agent_id}         - Archive agent

  Agent Execution:
    POST   /agents/{agent_id}/runs    - Create and start a run
    GET    /agents/{agent_id}/runs    - List runs for agent (paginated)
    GET    /agents/{agent_id}/runs/{run_id}              - Get run details
    POST   /agents/{agent_id}/runs/{run_id}/cancel       - Cancel a run
    GET    /agents/{agent_id}/runs/{run_id}/steps        - List steps in run
    GET    /agents/{agent_id}/runs/{run_id}/checkpoints  - List checkpoints (paginated)
    POST   /agents/{agent_id}/runs/{run_id}/resume       - Resume from checkpoint

  Budget & Analytics:
    GET    /agents/budget/status      - Project budget status
    GET    /agents/budget/usage       - Project usage summary
    GET    /agents/{agent_id}/costs   - Run costs breakdown
"""

from __future__ import annotations

import logging
import json
from typing import Optional

from fastapi import APIRouter, Depends, Query, Path, HTTPException, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db, require_permission
from app.core.exceptions import (
    NotFoundError, PermissionDeniedError, ValidationError, WorkflowError
)
from app.schemas.common import MessageResponse, PaginatedResponse
from app.schemas.agents import (
    AgentCreateRequest, AgentUpdateRequest, AgentResponse, AgentListItem,
    RunCreateRequest, RunResponse, RunListItem, RunListResponse,
    StepResponse, StepListResponse,
    CheckpointResponse, CheckpointListResponse, ResumeRequest,
    BudgetStatusResponse, CostResponse, UsageResponse,
)

from promptops_app.agents_models import (
    AgentDefinition, AgentDefinitionVersion, AgentRun, AgentRunStep, AgentCheckpoint
)
from promptops_app.services.agent_run_service import AgentRunService
from promptops_app.services.agent_step_service import AgentStepService
from promptops_app.services.agent_checkpoint_service import AgentCheckpointService
from promptops_app.services.agent_budget_service import AgentBudgetService
from promptops_app.services.agents_tenant_service import AgentsTenantService
from promptops_app.services.budget_service import BudgetExceededError

_log = logging.getLogger(__name__)
router = APIRouter()


# =============================================================================
# Helper Functions
# =============================================================================

def _get_agent_or_404(db: Session, agent_id: int, project_id: int) -> AgentDefinition:
    """Fetch an agent or raise HTTP 404."""
    agent = AgentsTenantService.validate_agent_ownership(db, agent_id, project_id)
    if not agent:
        raise NotFoundError("Agent", agent_id)
    return agent


def _get_run_or_404(db: Session, run_id: int, project_id: int) -> AgentRun:
    """Fetch a run or raise HTTP 404 if not found or belongs to different tenant."""
    run = db.query(AgentRun).filter(
        AgentRun.id == run_id,
        AgentRun.project_id == project_id
    ).first()
    if not run:
        raise NotFoundError("Run", run_id)
    return run


def _serialize_agent(agent: AgentDefinition) -> AgentResponse:
    """Convert AgentDefinition ORM object to Pydantic response."""
    try:
        config = json.loads(agent.configuration) if isinstance(agent.configuration, str) else agent.configuration
    except (json.JSONDecodeError, TypeError):
        config = {}

    return AgentResponse(
        id=agent.id,
        project_id=agent.project_id,
        template_id=agent.template_id,
        name=agent.name,
        call_handle=agent.call_handle,
        description=agent.description,
        owner=agent.owner,
        configuration=config,
        lifecycle_state=agent.lifecycle_state,
        lifecycle_reason=agent.lifecycle_reason,
        current_version_number=agent.current_version_number,
        activated_version_number=agent.activated_version_number,
        activated_at=agent.activated_at,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )


def _serialize_run(run: AgentRun) -> RunResponse:
    """Convert AgentRun ORM object to Pydantic response."""
    try:
        result = json.loads(run.result) if run.result and isinstance(run.result, str) else run.result
    except (json.JSONDecodeError, TypeError):
        result = None

    try:
        input_context = json.loads(run.input_context) if run.input_context and isinstance(run.input_context, str) else run.input_context
    except (json.JSONDecodeError, TypeError):
        input_context = None

    return RunResponse(
        id=run.id,
        project_id=run.project_id,
        definition_id=run.definition_id,
        version_id=run.version_id,
        initiated_by=run.initiated_by,
        initiated_by_role=run.initiated_by_role,
        artifact_id=run.artifact_id,
        artifact_type=run.artifact_type,
        input_content=run.input_content,
        state=run.state,
        state_reason=run.state_reason,
        result=result,
        error_message=run.error_message,
        step_count=run.step_count,
        max_steps=run.max_steps,
        started_at=run.started_at,
        completed_at=run.completed_at,
        execution_time_ms=run.execution_time_ms,
        estimated_cost=run.estimated_cost,
        actual_cost=run.actual_cost,
        budget_reserved=run.budget_reserved,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


def _serialize_step(step: AgentRunStep) -> StepResponse:
    """Convert AgentRunStep ORM object to Pydantic response."""
    try:
        input_data = json.loads(step.input_data) if step.input_data and isinstance(step.input_data, str) else step.input_data
    except (json.JSONDecodeError, TypeError):
        input_data = None

    try:
        output_data = json.loads(step.output_data) if step.output_data and isinstance(step.output_data, str) else step.output_data
    except (json.JSONDecodeError, TypeError):
        output_data = None

    return StepResponse(
        id=step.id,
        run_id=step.run_id,
        step_index=step.step_index,
        step_type=step.step_type,
        status=step.status,
        retry_count=step.retry_count,
        max_retries=step.max_retries,
        input_data=input_data,
        output_data=output_data,
        error_message=step.error_message,
        model_used=step.model_used,
        prompt_tokens=step.prompt_tokens,
        completion_tokens=step.completion_tokens,
        execution_time_ms=step.execution_time_ms,
        step_cost=step.step_cost,
        started_at=step.started_at,
        completed_at=step.completed_at,
    )


def _serialize_checkpoint(cp: AgentCheckpoint) -> CheckpointResponse:
    """Convert AgentCheckpoint ORM object to Pydantic response."""
    try:
        step_state = json.loads(cp.step_state) if isinstance(cp.step_state, str) else cp.step_state
    except (json.JSONDecodeError, TypeError):
        step_state = {}

    return CheckpointResponse(
        id=cp.id,
        run_id=cp.run_id,
        checkpoint_index=cp.checkpoint_index,
        step_state=step_state,
        resumable=cp.resumable,
        resume_reason=cp.resume_reason,
        created_at=cp.created_at,
    )


# =============================================================================
# Agent Management Endpoints
# =============================================================================

@router.post(
    "",
    response_model=AgentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new agent",
    description="Create a new agent from a platform template. Admin only.",
)
def create_agent(
    request_body: AgentCreateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("agents.create")),
) -> AgentResponse:
    """Create a new agent definition based on a platform template."""
    project_id = request_body.project_id
    _log.info(f"Creating agent {request_body.name} for project {project_id}")

    # Validate template exists
    # TODO: Check if template exists in agent_templates table

    # Create agent definition
    config_dict = request_body.configuration.dict(exclude_none=True) if request_body.configuration else {}

    agent = AgentDefinition(
        project_id=project_id,
        template_id=request_body.template_id,
        name=request_body.name,
        call_handle=request_body.call_handle,
        description=request_body.description,
        owner=current_user.username,
        configuration=json.dumps(config_dict),
        lifecycle_state="draft",
        created_by=current_user.username,
    )

    db.add(agent)
    db.commit()
    db.refresh(agent)

    return _serialize_agent(agent)


@router.get(
    "",
    response_model=PaginatedResponse[AgentListItem],
    summary="List agents",
    description="List all agents in the tenant's project.",
)
def list_agents(
    project_id: int = Query(..., description="Project ID for tenant scoping"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    lifecycle_state: Optional[str] = Query(None, description="Filter by lifecycle state"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> PaginatedResponse[AgentListItem]:
    """List all agents for the tenant's project."""
    query = db.query(AgentDefinition).filter(
        AgentDefinition.project_id == project_id
    )

    if lifecycle_state:
        query = query.filter(AgentDefinition.lifecycle_state == lifecycle_state)

    total = query.count()
    agents = query.offset((page - 1) * page_size).limit(page_size).all()

    items = [
        AgentListItem(
            id=a.id,
            name=a.name,
            call_handle=a.call_handle,
            lifecycle_state=a.lifecycle_state,
            template_id=a.template_id,
            owner=a.owner,
            current_version_number=a.current_version_number,
            created_at=a.created_at,
        )
        for a in agents
    ]

    return PaginatedResponse.create(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{agent_id}",
    response_model=AgentResponse,
    summary="Get agent details",
    description="Retrieve full details of an agent.",
)
def get_agent(
    agent_id: int = Path(..., description="Agent ID"),
    project_id: int = Query(..., description="Project ID for tenant scoping"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> AgentResponse:
    """Get full details of an agent."""
    agent = _get_agent_or_404(db, agent_id, project_id)
    return _serialize_agent(agent)


@router.put(
    "/{agent_id}",
    response_model=AgentResponse,
    summary="Update agent",
    description="Update agent configuration or lifecycle state.",
)
def update_agent(
    agent_id: int = Path(..., description="Agent ID"),
    request_body: AgentUpdateRequest = None,
    project_id: int = Query(..., description="Project ID for tenant scoping"),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("agents.update")),
) -> AgentResponse:
    """Update an agent's configuration or state."""
    agent = _get_agent_or_404(db, agent_id, project_id)

    if request_body.name:
        agent.name = request_body.name

    if request_body.description is not None:
        agent.description = request_body.description

    if request_body.configuration:
        current_config = json.loads(agent.configuration) if isinstance(agent.configuration, str) else {}
        update_dict = request_body.configuration.dict(exclude_none=True)
        current_config.update(update_dict)
        agent.configuration = json.dumps(current_config)

    if request_body.lifecycle_state:
        agent.lifecycle_state = request_body.lifecycle_state
        if request_body.lifecycle_reason:
            agent.lifecycle_reason = request_body.lifecycle_reason

    agent.updated_by = current_user.username
    agent.updated_at = __import__('datetime').datetime.utcnow()

    db.commit()
    db.refresh(agent)

    return _serialize_agent(agent)


@router.delete(
    "/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Archive agent",
    description="Archive an agent (soft delete).",
)
def delete_agent(
    agent_id: int = Path(..., description="Agent ID"),
    project_id: int = Query(..., description="Project ID for tenant scoping"),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("agents.delete")),
):
    """Archive an agent by setting its state to archived."""
    agent = _get_agent_or_404(db, agent_id, project_id)
    agent.lifecycle_state = "archived"
    agent.lifecycle_reason = f"Archived by {current_user.username}"
    db.commit()


# =============================================================================
# Agent Execution Endpoints
# =============================================================================

@router.post(
    "/{agent_id}/runs",
    response_model=RunResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create and start a run",
    description="Execute an agent on input content.",
)
def create_run(
    agent_id: int = Path(..., description="Agent ID"),
    request_body: RunCreateRequest = None,
    project_id: int = Query(..., description="Project ID for tenant scoping"),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("agents.run")),
) -> RunResponse:
    """Create and start a new agent run."""
    agent = _get_agent_or_404(db, agent_id, project_id)

    try:
        input_context = request_body.input_context or {}
        run = AgentRunService.create_run(
            db=db,
            project_id=project_id,
            definition_id=agent_id,
            initiated_by=current_user.username,
            initiated_by_role=current_user.role,
            artifact_id=request_body.artifact_id,
            artifact_type=request_body.artifact_type,
            input_content=request_body.input_content,
            input_context=input_context,
            request_id=request_body.request_id,
            estimated_cost=0.05,  # Estimate $0.05 per run
        )
    except BudgetExceededError as e:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Budget exceeded: {str(e)}"
        )
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e)
        )

    return _serialize_run(run)


@router.get(
    "/{agent_id}/runs",
    response_model=PaginatedResponse[RunListItem],
    summary="List runs for agent",
    description="Get all runs for an agent (paginated).",
)
def list_runs(
    agent_id: int = Path(..., description="Agent ID"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    state: Optional[str] = Query(None, description="Filter by state"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> PaginatedResponse[RunListItem]:
    """List all runs for an agent."""
    agent = _get_agent_or_404(db, agent_id, current_user.project_id)

    query = db.query(AgentRun).filter(
        AgentRun.project_id == current_user.project_id,
        AgentRun.definition_id == agent_id,
    )

    if state:
        query = query.filter(AgentRun.state == state)

    total = query.count()
    runs = query.order_by(AgentRun.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()

    items = [
        RunListItem(
            id=r.id,
            state=r.state,
            initiated_by=r.initiated_by,
            step_count=r.step_count,
            actual_cost=r.actual_cost,
            started_at=r.started_at,
            completed_at=r.completed_at,
            created_at=r.created_at,
        )
        for r in runs
    ]

    return PaginatedResponse.create(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{agent_id}/runs/{run_id}",
    response_model=RunResponse,
    summary="Get run details",
    description="Retrieve full details of a specific run.",
)
def get_run(
    agent_id: int = Path(..., description="Agent ID"),
    run_id: int = Path(..., description="Run ID"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> RunResponse:
    """Get full details of a run."""
    # Verify agent ownership
    _get_agent_or_404(db, agent_id, current_user.project_id)
    run = _get_run_or_404(db, run_id, current_user.project_id)

    # Verify run belongs to agent
    if run.definition_id != agent_id:
        raise NotFoundError("Run", run_id)

    return _serialize_run(run)


@router.post(
    "/{agent_id}/runs/{run_id}/cancel",
    response_model=RunResponse,
    summary="Cancel a run",
    description="Cancel an in-progress run.",
)
def cancel_run(
    agent_id: int = Path(..., description="Agent ID"),
    run_id: int = Path(..., description="Run ID"),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("agents.run")),
) -> RunResponse:
    """Cancel an in-progress run."""
    # Verify agent ownership
    _get_agent_or_404(db, agent_id, current_user.project_id)
    run = _get_run_or_404(db, run_id, current_user.project_id)

    if run.state not in ("queued", "running", "awaiting_input"):
        raise WorkflowError(f"Cannot cancel run in state {run.state}")

    try:
        run = AgentRunService.cancel_run(db, run_id, current_user.project_id)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot cancel run: {str(e)}"
        )

    return _serialize_run(run)


@router.get(
    "/{agent_id}/runs/{run_id}/steps",
    response_model=StepListResponse,
    summary="List steps in run",
    description="Get all steps executed in a run.",
)
def list_steps(
    agent_id: int = Path(..., description="Agent ID"),
    run_id: int = Path(..., description="Run ID"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> StepListResponse:
    """List all steps in a run."""
    # Verify agent ownership
    _get_agent_or_404(db, agent_id, current_user.project_id)
    run = _get_run_or_404(db, run_id, current_user.project_id)

    steps = db.query(AgentRunStep).filter(
        AgentRunStep.run_id == run_id
    ).order_by(AgentRunStep.step_index).all()

    items = [_serialize_step(s) for s in steps]
    return StepListResponse(items=items, total=len(items))


@router.get(
    "/{agent_id}/runs/{run_id}/checkpoints",
    response_model=CheckpointListResponse,
    summary="List checkpoints",
    description="Get all checkpoints for a run (paginated).",
)
def list_checkpoints(
    agent_id: int = Path(..., description="Agent ID"),
    run_id: int = Path(..., description="Run ID"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    resumable_only: bool = Query(False, description="Only show resumable checkpoints"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> CheckpointListResponse:
    """List checkpoints for a run."""
    # Verify agent ownership
    _get_agent_or_404(db, agent_id, current_user.project_id)
    run = _get_run_or_404(db, run_id, current_user.project_id)

    query = db.query(AgentCheckpoint).filter(AgentCheckpoint.run_id == run_id)

    if resumable_only:
        query = query.filter(AgentCheckpoint.resumable == True)

    total = query.count()
    checkpoints = query.order_by(AgentCheckpoint.checkpoint_index).offset(
        (page - 1) * page_size
    ).limit(page_size).all()

    items = [_serialize_checkpoint(cp) for cp in checkpoints]

    return CheckpointListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=max(1, -(-total // page_size)),
    )


@router.post(
    "/{agent_id}/runs/{run_id}/resume",
    response_model=RunResponse,
    summary="Resume from checkpoint",
    description="Resume run execution from a checkpoint.",
)
def resume_from_checkpoint(
    agent_id: int = Path(..., description="Agent ID"),
    run_id: int = Path(..., description="Run ID"),
    request_body: ResumeRequest = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("agents.run")),
) -> RunResponse:
    """Resume a paused run from a checkpoint."""
    # Verify agent ownership
    _get_agent_or_404(db, agent_id, current_user.project_id)
    run = _get_run_or_404(db, run_id, current_user.project_id)

    if run.state != "awaiting_input":
        raise WorkflowError(f"Can only resume runs in 'awaiting_input' state, current: {run.state}")

    try:
        checkpoint_state = AgentCheckpointService.resume_from_checkpoint(
            db, run_id, request_body.checkpoint_index
        )
        # TODO: Resume execution from checkpoint_state
        run.state = "running"
        db.commit()
        db.refresh(run)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot resume run: {str(e)}"
        )

    return _serialize_run(run)


# =============================================================================
# Budget & Analytics Endpoints
# =============================================================================

@router.get(
    "/budget/status",
    response_model=BudgetStatusResponse,
    summary="Get budget status",
    description="Get current budget status for the project.",
)
def get_budget_status(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> BudgetStatusResponse:
    """Get current budget status for the project."""
    try:
        status = AgentBudgetService.get_budget_status(db, current_user.project_id)

        return BudgetStatusResponse(
            project_id=status.project_id,
            current_spend=status.current_spend,
            remaining_budget=status.remaining_budget,
            limit_usd=status.limit_usd,
            period=status.period,
            period_key=status.period_key,
            limit_type=status.limit_type,
            is_enforced=status.is_enforced,
            percent_used=status.percent_used,
        )
    except ValidationError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No budget policy configured for this project"
        )


@router.get(
    "/budget/usage",
    response_model=UsageResponse,
    summary="Get usage summary",
    description="Get project usage summary over time period.",
)
def get_usage_summary(
    days: int = Query(30, ge=1, le=365, description="Lookback period in days"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> UsageResponse:
    """Get usage summary for the project."""
    usage = AgentBudgetService.get_project_usage_summary(
        db, current_user.project_id, lookback_days=days
    )

    daily_costs = [
        {
            "date": date_str,
            "cost": cost,
            "run_count": 0,  # TODO: Calculate run count per day
        }
        for date_str, cost in usage.get("daily_costs", {}).items()
    ]

    return UsageResponse(
        project_id=current_user.project_id,
        period_days=days,
        total_cost=usage.get("total_cost", 0.0),
        total_runs=usage.get("total_runs", 0),
        average_cost_per_run=usage.get("average_cost_per_run", 0.0),
        daily_costs=daily_costs,
    )


@router.get(
    "/{agent_id}/runs/{run_id}/costs",
    response_model=CostResponse,
    summary="Get run costs",
    description="Get detailed cost breakdown for a run.",
)
def get_run_costs(
    agent_id: int = Path(..., description="Agent ID"),
    run_id: int = Path(..., description="Run ID"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> CostResponse:
    """Get detailed cost breakdown for a run."""
    # Verify agent ownership
    _get_agent_or_404(db, agent_id, current_user.project_id)
    run = _get_run_or_404(db, run_id, current_user.project_id)

    costs = AgentBudgetService.get_run_costs(db, run_id)

    steps = [
        {
            "step_index": step.step_index,
            "model_used": step.model_used,
            "prompt_tokens": step.prompt_tokens or 0,
            "completion_tokens": step.completion_tokens or 0,
            "cost_usd": step.step_cost or 0.0,
        }
        for step in db.query(AgentRunStep).filter(AgentRunStep.run_id == run_id).all()
    ]

    return CostResponse(
        run_id=run_id,
        total_cost=costs.get("total_cost", 0.0),
        total_tokens=costs.get("total_tokens", 0),
        prompt_tokens=costs.get("total_tokens", 0),  # Simplified
        completion_tokens=0,  # Simplified
        steps=steps,
        by_model=costs.get("by_model", {}),
    )
