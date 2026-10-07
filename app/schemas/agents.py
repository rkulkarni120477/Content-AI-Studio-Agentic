"""
Pydantic schemas for Agent Builder API endpoints.

Covers:
- Agent management (create, read, update, delete)
- Agent execution (runs, steps, checkpoints)
- Budget tracking and cost visibility
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Dict, List, Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


# =============================================================================
# Agent Management Schemas
# =============================================================================

class AgentConfigurationUpdate(BaseModel):
    """Configuration fields that can be updated in an agent."""
    model_id: Optional[str] = Field(None, description="LLM model identifier (e.g., openai:gpt-4o)")
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0, description="Model temperature")
    max_tokens: Optional[int] = Field(None, ge=1, description="Maximum output tokens")
    additional_config: Optional[Dict[str, Any]] = Field(None, description="Additional JSON config")


class AgentCreateRequest(BaseModel):
    """Request to create a new agent."""
    project_id: int = Field(..., description="Project ID for tenant scoping")
    name: str = Field(..., description="Display name for the agent")
    template_id: int = Field(..., description="Platform template ID to base on")
    call_handle: str = Field(..., description="Internal identifier (must be unique per tenant)")
    description: Optional[str] = Field(None, description="Agent description")
    configuration: AgentConfigurationUpdate = Field(..., description="Initial configuration")


class AgentUpdateRequest(BaseModel):
    """Request to update an agent configuration."""
    name: Optional[str] = Field(None, description="New display name")
    description: Optional[str] = Field(None, description="Updated description")
    configuration: Optional[AgentConfigurationUpdate] = Field(None, description="Configuration changes")
    lifecycle_state: Optional[str] = Field(None, description="draft|active|paused|archived")
    lifecycle_reason: Optional[str] = Field(None, description="Reason for state change")


class AgentResponse(BaseModel):
    """Full agent definition response."""
    id: int
    project_id: int
    template_id: int
    name: str
    call_handle: str
    description: Optional[str]
    owner: str
    configuration: Dict[str, Any]
    lifecycle_state: str
    lifecycle_reason: Optional[str]
    current_version_number: int
    activated_version_number: Optional[int]
    activated_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime


class AgentListItem(BaseModel):
    """Lightweight agent item for list responses."""
    id: int
    name: str
    call_handle: str
    lifecycle_state: str
    template_id: int
    owner: str
    current_version_number: int
    created_at: datetime


# =============================================================================
# Run Execution Schemas
# =============================================================================

class RunCreateRequest(BaseModel):
    """Request to create and start a new run."""
    artifact_id: Optional[int] = Field(None, description="Target artifact ID")
    artifact_type: Optional[str] = Field(None, description="Target artifact type (block|course|module)")
    input_content: str = Field(..., description="Content to process")
    input_context: Optional[Dict[str, Any]] = Field(None, description="Additional context")
    request_id: Optional[str] = Field(None, description="Idempotency key for safe replay")


class RunResponse(BaseModel):
    """Full run details response."""
    id: int
    project_id: int
    definition_id: int
    version_id: int
    initiated_by: str
    initiated_by_role: str
    artifact_id: Optional[int]
    artifact_type: Optional[str]
    input_content: Optional[str]
    state: str  # queued|running|awaiting_input|completed|failed|cancelled
    state_reason: Optional[str]
    result: Optional[Dict[str, Any]]
    error_message: Optional[str]
    step_count: int
    max_steps: int
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    execution_time_ms: Optional[int]
    estimated_cost: Optional[float]
    actual_cost: Optional[float]
    budget_reserved: bool
    created_at: datetime
    updated_at: datetime


class RunListItem(BaseModel):
    """Lightweight run item for list responses."""
    id: int
    state: str
    initiated_by: str
    step_count: int
    actual_cost: Optional[float]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    created_at: datetime


class RunListResponse(BaseModel):
    """Paginated list of runs."""
    items: List[RunListItem]
    total: int
    page: int
    page_size: int
    pages: int


# =============================================================================
# Step Execution Schemas
# =============================================================================

class StepResponse(BaseModel):
    """Step details in a run."""
    id: int
    run_id: int
    step_index: int
    step_type: str  # model_call|retrieval|validation|handoff
    status: str  # pending|running|completed|failed|retrying
    retry_count: int
    max_retries: int
    input_data: Optional[Dict[str, Any]]
    output_data: Optional[Dict[str, Any]]
    error_message: Optional[str]
    model_used: Optional[str]
    prompt_tokens: Optional[int]
    completion_tokens: Optional[int]
    execution_time_ms: Optional[int]
    step_cost: Optional[float]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]


class StepListResponse(BaseModel):
    """List of steps in a run."""
    items: List[StepResponse]
    total: int


# =============================================================================
# Checkpoint & Resumption Schemas
# =============================================================================

class CheckpointResponse(BaseModel):
    """Checkpoint for run resumption."""
    id: int
    run_id: int
    checkpoint_index: int
    step_state: Dict[str, Any]
    resumable: bool
    resume_reason: Optional[str]
    created_at: datetime


class CheckpointListResponse(BaseModel):
    """Paginated list of checkpoints."""
    items: List[CheckpointResponse]
    total: int
    page: int
    page_size: int
    pages: int


class ResumeRequest(BaseModel):
    """Request to resume a run from a checkpoint."""
    checkpoint_index: int = Field(..., description="Index of checkpoint to resume from")


# =============================================================================
# Workflow Schemas (Phase 3: Multi-Agent Execution)
# =============================================================================

class WorkflowAgentStep(BaseModel):
    """Definition of an agent step in a workflow."""
    agent_id: int = Field(..., description="Agent definition ID")
    step_id: Optional[str] = Field(None, description="Optional human-readable step identifier")
    description: Optional[str] = Field(None, description="Purpose of this agent in the workflow")


class WorkflowHandoffRule(BaseModel):
    """Handoff rule for workflow routing."""
    from_agent_id: int = Field(..., description="Source agent ID")
    to_agent_ids: List[int] = Field(..., description="Target agent ID(s) - list for concurrent, single for sequential")
    rule_type: str = Field("sequential", description="sequential|conditional|concurrent")
    condition: Optional[str] = Field(None, description="Condition expression for routing (e.g., 'output.type == review')")


class WorkflowDefinition(BaseModel):
    """Complete workflow definition structure."""
    agent_steps: List[WorkflowAgentStep] = Field(..., description="Agents in the workflow")
    handoff_rules: List[WorkflowHandoffRule] = Field(..., description="Rules for agent handoffs")


class WorkflowCreateRequest(BaseModel):
    """Request to create a new workflow."""
    name: str = Field(..., description="Display name for the workflow")
    call_handle: str = Field(..., description="Code identifier (unique per tenant)")
    description: Optional[str] = Field(None, description="Workflow description")
    definition: WorkflowDefinition = Field(..., description="Workflow DAG and routing")


class WorkflowUpdateRequest(BaseModel):
    """Request to update a workflow."""
    name: Optional[str] = Field(None, description="New display name")
    description: Optional[str] = Field(None, description="Updated description")
    definition: Optional[WorkflowDefinition] = Field(None, description="Updated workflow definition")


class WorkflowResponse(BaseModel):
    """Full workflow definition response."""
    id: int
    project_id: int
    name: str
    call_handle: str
    description: Optional[str]
    definition: Dict[str, Any]
    is_active: bool
    owner: str
    created_at: datetime
    updated_at: datetime


class WorkflowListItem(BaseModel):
    """Lightweight workflow item for list responses."""
    id: int
    name: str
    call_handle: str
    is_active: bool
    owner: str
    created_at: datetime


class WorkflowListResponse(BaseModel):
    """Paginated list of workflows."""
    items: List[WorkflowListItem]
    total: int
    page: int
    page_size: int
    pages: int


class WorkflowRunCreateRequest(BaseModel):
    """Request to start a workflow execution."""
    input_content: str = Field(..., description="Initial content to process")
    input_context: Optional[Dict[str, Any]] = Field(None, description="Additional context")
    request_id: Optional[str] = Field(None, description="Idempotency key for safe replay")


class WorkflowRunResponse(BaseModel):
    """Full workflow run details."""
    id: int
    project_id: int
    workflow_id: int
    initiated_by: str
    state: str  # queued|running|completed|failed|paused
    result: Optional[Dict[str, Any]]
    total_cost: Optional[float]
    execution_time_ms: Optional[int]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    created_at: datetime


class WorkflowRunListItem(BaseModel):
    """Lightweight workflow run item for list responses."""
    id: int
    workflow_id: int
    state: str
    initiated_by: str
    total_cost: Optional[float]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    created_at: datetime


class WorkflowRunListResponse(BaseModel):
    """Paginated list of workflow runs."""
    items: List[WorkflowRunListItem]
    total: int
    page: int
    page_size: int
    pages: int


class CompleteAgentStepRequest(BaseModel):
    """Request to mark an agent step complete in a workflow."""
    agent_run_id: int = Field(..., description="ID of the completed agent run")
    result: Dict[str, Any] = Field(..., description="Output from the agent")


# =============================================================================
# Budget & Cost Schemas
# =============================================================================

class BudgetStatusResponse(BaseModel):
    """Current budget status for a project."""
    project_id: int
    current_spend: float = Field(..., description="USD spent this period")
    remaining_budget: float = Field(..., description="USD available")
    limit_usd: float = Field(..., description="Total limit")
    period: str = Field(..., description="daily|monthly|quarterly")
    period_key: str = Field(..., description="Current budget period")
    limit_type: str = Field(..., description="usd|tokens")
    is_enforced: bool = Field(..., description="Hard limit vs soft")
    percent_used: float = Field(..., ge=0, le=100, description="0-100 usage percentage")


class StepCostBreakdown(BaseModel):
    """Cost for a single step."""
    step_index: int
    model_used: Optional[str]
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float


class CostResponse(BaseModel):
    """Detailed cost breakdown for a run."""
    run_id: int
    total_cost: float = Field(..., description="Total USD cost")
    total_tokens: int = Field(..., description="Total tokens used")
    prompt_tokens: int = Field(..., description="Total prompt tokens")
    completion_tokens: int = Field(..., description="Total completion tokens")
    steps: List[StepCostBreakdown] = Field(..., description="Per-step costs")
    by_model: Dict[str, float] = Field(..., description="Costs aggregated by model")


class DailyCostEntry(BaseModel):
    """Daily cost aggregate."""
    date: str
    cost: float
    run_count: int


class UsageResponse(BaseModel):
    """Project usage summary."""
    project_id: int
    period_days: int = Field(..., description="Lookback period in days")
    total_cost: float = Field(..., description="Period total USD")
    total_runs: int = Field(..., description="Total runs in period")
    average_cost_per_run: float = Field(..., description="Mean cost per run")
    daily_costs: List[DailyCostEntry] = Field(..., description="Daily breakdown")


# =============================================================================
# Common Response Wrappers
# =============================================================================

class MessageResponse(BaseModel):
    """Simple success message response."""
    message: str


class PaginatedResponse(BaseModel, Generic[T]):
    """Standard wrapper for paginated list responses."""
    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int

    @classmethod
    def create(
        cls,
        items: list[T],
        total: int,
        page: int,
        page_size: int,
    ) -> "PaginatedResponse[T]":
        """Convenience constructor that calculates page count."""
        pages = max(1, -(-total // page_size))  # ceiling division
        return cls(items=items, total=total, page=page, page_size=page_size, pages=pages)
