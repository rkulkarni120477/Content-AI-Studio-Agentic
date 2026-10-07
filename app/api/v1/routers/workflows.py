"""
Workflow API Routes — Phase 3: Multi-Agent Execution & Workflows

REST API endpoints for multi-agent workflow management and execution.

Endpoints:
  Workflow Definition:
    POST   /workflows                    - Create new workflow
    GET    /workflows                    - List workflows (paginated)
    GET    /workflows/{workflow_id}      - Get workflow details
    PUT    /workflows/{workflow_id}      - Update workflow
    DELETE /workflows/{workflow_id}      - Archive workflow

  Workflow Execution:
    POST   /workflows/{workflow_id}/execute              - Start workflow execution
    GET    /workflows/{workflow_id}/runs                 - List workflow runs
    GET    /workflows/{workflow_id}/runs/{run_id}        - Get run status
    POST   /workflows/{workflow_id}/runs/{run_id}/pause  - Pause workflow
    POST   /workflows/{workflow_id}/runs/{run_id}/resume - Resume workflow
    POST   /workflows/{workflow_id}/runs/{run_id}/complete-step - Mark agent step complete
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
from app.schemas.common import MessageResponse
from app.schemas.agents import (
    WorkflowCreateRequest, WorkflowUpdateRequest, WorkflowResponse, WorkflowListItem,
    WorkflowListResponse, WorkflowRunCreateRequest, WorkflowRunResponse, WorkflowRunListItem,
    WorkflowRunListResponse, CompleteAgentStepRequest,
)

from promptops_app.agents_models import AgentWorkflow, WorkflowRun
from promptops_app.services.agent_workflow_service import AgentWorkflowService

_log = logging.getLogger(__name__)
router = APIRouter()


def _user_value(current_user, key: str, default=None):
    if isinstance(current_user, dict):
        return current_user.get(key, default)
    attribute = "_project_id" if key == "project_id" else key
    return getattr(current_user, attribute, default)


# =============================================================================
# Helper Functions
# =============================================================================

def _get_workflow_or_404(db: Session, workflow_id: int, project_id: int) -> AgentWorkflow:
    """Fetch a workflow or raise HTTP 404."""
    try:
        workflow = AgentWorkflowService.get_workflow(db, workflow_id, project_id)
        return workflow
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


def _get_run_or_404(db: Session, workflow_id: int, run_id: int, project_id: int) -> WorkflowRun:
    """Fetch a workflow run or raise HTTP 404."""
    try:
        run = AgentWorkflowService.get_workflow_run(db, workflow_id, run_id, project_id)
        return run
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


def _serialize_workflow(workflow: AgentWorkflow) -> WorkflowResponse:
    """Convert workflow ORM object to Pydantic response."""
    try:
        definition = json.loads(workflow.definition) if isinstance(workflow.definition, str) else workflow.definition
    except (json.JSONDecodeError, TypeError):
        definition = {}

    return WorkflowResponse(
        id=workflow.id,
        project_id=workflow.project_id,
        name=workflow.name,
        call_handle=workflow.call_handle,
        description=workflow.description,
        definition=definition,
        is_active=workflow.is_active,
        owner=workflow.owner,
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
    )


def _serialize_run(run: WorkflowRun) -> WorkflowRunResponse:
    """Convert workflow run ORM object to Pydantic response."""
    try:
        result = json.loads(run.result) if run.result and isinstance(run.result, str) else run.result
    except (json.JSONDecodeError, TypeError):
        result = None

    return WorkflowRunResponse(
        id=run.id,
        project_id=run.project_id,
        workflow_id=run.workflow_id,
        initiated_by=run.initiated_by,
        state=run.state,
        result=result,
        total_cost=run.total_cost,
        execution_time_ms=run.execution_time_ms,
        started_at=run.started_at,
        completed_at=run.completed_at,
        created_at=run.created_at,
    )


# =============================================================================
# Workflow Definition Endpoints
# =============================================================================

@router.post(
    "/workflows",
    response_model=WorkflowResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new workflow",
    tags=["Workflows"],
)
async def create_workflow(
    request: WorkflowCreateRequest,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Create a new multi-agent workflow definition.

    Workflow defines a DAG of agents and handoff rules for coordinated execution.
    call_handle must be unique within the tenant.
    """
    try:
        project_id = _user_value(current_user, "project_id")
        if not project_id:
            raise HTTPException(status_code=400, detail="Project ID not found in user context")

        # Convert Pydantic model to dict for service
        definition_dict = {
            "agent_steps": [
                {"agent_id": step.agent_id, "step_id": step.step_id, "description": step.description}
                for step in request.definition.agent_steps
            ],
            "handoff_rules": [
                {
                    "from_agent_id": rule.from_agent_id,
                    "to_agent_ids": rule.to_agent_ids,
                    "rule_type": rule.rule_type,
                    "condition": rule.condition,
                }
                for rule in request.definition.handoff_rules
            ]
        }

        workflow = AgentWorkflowService.create_workflow(
            db=db,
            project_id=project_id,
            name=request.name,
            call_handle=request.call_handle,
            description=request.description,
            definition=definition_dict,
            created_by=_user_value(current_user, "username", "unknown"),
        )
        return _serialize_workflow(workflow)

    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        _log.error(f"Error creating workflow: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get(
    "/workflows",
    response_model=WorkflowListResponse,
    summary="List workflows",
    tags=["Workflows"],
)
async def list_workflows(
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all workflows for the current tenant."""
    try:
        project_id = _user_value(current_user, "project_id")
        if not project_id:
            raise HTTPException(status_code=400, detail="Project ID not found")

        workflows, total = AgentWorkflowService.list_workflows(
            db=db,
            project_id=project_id,
            is_active=is_active,
            limit=page_size,
            offset=(page - 1) * page_size,
        )

        items = [
            WorkflowListItem(
                id=w.id,
                name=w.name,
                call_handle=w.call_handle,
                is_active=w.is_active,
                owner=w.owner,
                created_at=w.created_at,
            )
            for w in workflows
        ]

        pages = max(1, -(-total // page_size))
        return WorkflowListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            pages=pages,
        )

    except Exception as e:
        _log.error(f"Error listing workflows: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get(
    "/workflows/{workflow_id}",
    response_model=WorkflowResponse,
    summary="Get workflow details",
    tags=["Workflows"],
)
async def get_workflow(
    workflow_id: int = Path(..., description="Workflow ID"),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get detailed information about a specific workflow."""
    try:
        project_id = _user_value(current_user, "project_id")
        if not project_id:
            raise HTTPException(status_code=400, detail="Project ID not found")

        workflow = _get_workflow_or_404(db, workflow_id, project_id)
        return _serialize_workflow(workflow)

    except HTTPException:
        raise
    except Exception as e:
        _log.error(f"Error getting workflow {workflow_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.put(
    "/workflows/{workflow_id}",
    response_model=WorkflowResponse,
    summary="Update workflow",
    tags=["Workflows"],
)
async def update_workflow(
    workflow_id: int = Path(..., description="Workflow ID"),
    request: WorkflowUpdateRequest = None,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update a workflow definition."""
    try:
        project_id = _user_value(current_user, "project_id")
        if not project_id:
            raise HTTPException(status_code=400, detail="Project ID not found")

        # Validate workflow exists
        _get_workflow_or_404(db, workflow_id, project_id)

        # Convert definition to dict if provided
        definition_dict = None
        if request and request.definition:
            definition_dict = {
                "agent_steps": [
                    {"agent_id": step.agent_id, "step_id": step.step_id, "description": step.description}
                    for step in request.definition.agent_steps
                ],
                "handoff_rules": [
                    {
                        "from_agent_id": rule.from_agent_id,
                        "to_agent_ids": rule.to_agent_ids,
                        "rule_type": rule.rule_type,
                        "condition": rule.condition,
                    }
                    for rule in request.definition.handoff_rules
                ]
            }

        workflow = AgentWorkflowService.update_workflow(
            db=db,
            workflow_id=workflow_id,
            project_id=project_id,
            name=request.name if request else None,
            description=request.description if request else None,
            definition=definition_dict,
            updated_by=_user_value(current_user, "username", "unknown"),
        )
        return _serialize_workflow(workflow)

    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        _log.error(f"Error updating workflow {workflow_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete(
    "/workflows/{workflow_id}",
    response_model=MessageResponse,
    summary="Archive workflow",
    tags=["Workflows"],
)
async def delete_workflow(
    workflow_id: int = Path(..., description="Workflow ID"),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Archive (soft-delete) a workflow."""
    try:
        project_id = _user_value(current_user, "project_id")
        if not project_id:
            raise HTTPException(status_code=400, detail="Project ID not found")

        _get_workflow_or_404(db, workflow_id, project_id)

        AgentWorkflowService.archive_workflow(db, workflow_id, project_id)
        return MessageResponse(message=f"Workflow {workflow_id} archived successfully")

    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        _log.error(f"Error archiving workflow {workflow_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# =============================================================================
# Workflow Execution Endpoints
# =============================================================================

@router.post(
    "/workflows/{workflow_id}/execute",
    response_model=WorkflowRunResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Execute workflow",
    tags=["Workflows"],
)
async def execute_workflow(
    workflow_id: int = Path(..., description="Workflow ID"),
    request: WorkflowRunCreateRequest = None,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Start a workflow execution."""
    try:
        project_id = _user_value(current_user, "project_id")
        if not project_id:
            raise HTTPException(status_code=400, detail="Project ID not found")

        # Verify workflow exists
        _get_workflow_or_404(db, workflow_id, project_id)

        user_role = _user_value(current_user, "role", "author")

        workflow_run = AgentWorkflowService.execute_workflow(
            db=db,
            workflow_id=workflow_id,
            project_id=project_id,
            initiated_by=_user_value(current_user, "username", "unknown"),
            initiated_by_role=user_role,
            input_content=request.input_content if request else "",
            input_context=request.input_context if request else None,
            request_id=request.request_id if request else None,
        )
        return _serialize_run(workflow_run)

    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        _log.error(f"Error executing workflow {workflow_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get(
    "/workflows/{workflow_id}/runs",
    response_model=WorkflowRunListResponse,
    summary="List workflow runs",
    tags=["Workflows"],
)
async def list_workflow_runs(
    workflow_id: int = Path(..., description="Workflow ID"),
    state: Optional[str] = Query(None, description="Filter by state"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List runs for a workflow."""
    try:
        project_id = _user_value(current_user, "project_id")
        if not project_id:
            raise HTTPException(status_code=400, detail="Project ID not found")

        # Verify workflow exists
        _get_workflow_or_404(db, workflow_id, project_id)

        runs, total = AgentWorkflowService.list_workflow_runs(
            db=db,
            workflow_id=workflow_id,
            project_id=project_id,
            state=state,
            limit=page_size,
            offset=(page - 1) * page_size,
        )

        items = [
            WorkflowRunListItem(
                id=r.id,
                workflow_id=r.workflow_id,
                state=r.state,
                initiated_by=r.initiated_by,
                total_cost=r.total_cost,
                started_at=r.started_at,
                completed_at=r.completed_at,
                created_at=r.created_at,
            )
            for r in runs
        ]

        pages = max(1, -(-total // page_size))
        return WorkflowRunListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            pages=pages,
        )

    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        _log.error(f"Error listing workflow runs: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get(
    "/workflows/{workflow_id}/runs/{run_id}",
    response_model=WorkflowRunResponse,
    summary="Get workflow run status",
    tags=["Workflows"],
)
async def get_workflow_run(
    workflow_id: int = Path(..., description="Workflow ID"),
    run_id: int = Path(..., description="Run ID"),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get the current state of a workflow run."""
    try:
        project_id = _user_value(current_user, "project_id")
        if not project_id:
            raise HTTPException(status_code=400, detail="Project ID not found")

        run = _get_run_or_404(db, workflow_id, run_id, project_id)
        return _serialize_run(run)

    except HTTPException:
        raise
    except Exception as e:
        _log.error(f"Error getting workflow run {run_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post(
    "/workflows/{workflow_id}/runs/{run_id}/pause",
    response_model=WorkflowRunResponse,
    summary="Pause workflow execution",
    tags=["Workflows"],
)
async def pause_workflow_run(
    workflow_id: int = Path(..., description="Workflow ID"),
    run_id: int = Path(..., description="Run ID"),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Pause a running workflow execution."""
    try:
        project_id = _user_value(current_user, "project_id")
        if not project_id:
            raise HTTPException(status_code=400, detail="Project ID not found")

        # Verify run exists
        _get_run_or_404(db, workflow_id, run_id, project_id)

        run = AgentWorkflowService.pause_workflow(db, workflow_id, run_id, project_id)
        return _serialize_run(run)

    except WorkflowError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        _log.error(f"Error pausing workflow run {run_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post(
    "/workflows/{workflow_id}/runs/{run_id}/resume",
    response_model=WorkflowRunResponse,
    summary="Resume workflow execution",
    tags=["Workflows"],
)
async def resume_workflow_run(
    workflow_id: int = Path(..., description="Workflow ID"),
    run_id: int = Path(..., description="Run ID"),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Resume a paused workflow execution."""
    try:
        project_id = _user_value(current_user, "project_id")
        if not project_id:
            raise HTTPException(status_code=400, detail="Project ID not found")

        # Verify run exists
        _get_run_or_404(db, workflow_id, run_id, project_id)

        run = AgentWorkflowService.resume_workflow(db, workflow_id, run_id, project_id)
        return _serialize_run(run)

    except WorkflowError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        _log.error(f"Error resuming workflow run {run_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post(
    "/workflows/{workflow_id}/runs/{run_id}/complete-step",
    response_model=WorkflowRunResponse,
    summary="Mark agent step complete",
    tags=["Workflows"],
)
async def complete_agent_step(
    workflow_id: int = Path(..., description="Workflow ID"),
    run_id: int = Path(..., description="Run ID"),
    request: CompleteAgentStepRequest = None,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mark an agent step complete and trigger next handoff."""
    try:
        project_id = _user_value(current_user, "project_id")
        if not project_id:
            raise HTTPException(status_code=400, detail="Project ID not found")

        # Verify run exists
        _get_run_or_404(db, workflow_id, run_id, project_id)

        run = AgentWorkflowService.complete_agent_step(
            db=db,
            workflow_id=workflow_id,
            run_id=run_id,
            project_id=project_id,
            agent_run_id=request.agent_run_id if request else 0,
            result=request.result if request else {},
        )
        return _serialize_run(run)

    except WorkflowError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        _log.error(f"Error completing agent step in workflow run {run_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
