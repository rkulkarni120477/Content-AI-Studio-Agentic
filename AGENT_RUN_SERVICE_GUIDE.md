# AgentRunService — Quick Reference Guide

## Overview

The `AgentRunService` manages the complete lifecycle of agent executions. It handles state transitions, error management, budget tracking, and tenant isolation.

**Location**: `promptops_app/services/agent_run_service.py`  
**Module**: `AgentRunService` class

---

## Basic Usage

### 1. Creating a Run

```python
from promptops_app.services.agent_run_service import AgentRunService

# Create a new run
run = AgentRunService.create_run(
    db=session,
    project_id=1,  # Tenant ID
    definition_id=123,  # Agent to run
    initiated_by="alice@example.com",  # User who initiated
    initiated_by_role="author",  # User's role
    input_content="Create a lesson on photosynthesis",
    artifact_id=456,  # Optional: target artifact
    artifact_type="block",  # Optional: artifact type
    estimated_cost=0.05,  # Optional: estimated USD cost
    request_id="req-abc123",  # Optional: for idempotency
)

print(f"Created run {run.id} in state: {run.state}")  # Output: queued
```

### 2. Starting a Run

```python
# Transition from queued → running
run = AgentRunService.start_run(
    db=session,
    run_id=run.id,
    project_id=1,
    timeout_seconds=300,  # Optional: how long before expires
)

print(f"Run {run.id} started at {run.started_at}")
```

### 3. Completing a Run

```python
# Execute agent logic here...
result = {
    "generated_content": "Photosynthesis is the process...",
    "confidence": 0.95,
}

# Mark as completed
run = AgentRunService.complete_run(
    db=session,
    run_id=run.id,
    project_id=1,
    result=result,
    actual_cost=0.031,  # Actual cost from LLM
    prompt_tokens=150,
    completion_tokens=200,
)

print(f"Run completed. Result: {run.result}")
```

### 4. Handling Failures

```python
import traceback

try:
    # ... run agent logic ...
except Exception as e:
    # Mark run as failed
    run = AgentRunService.fail_run(
        db=session,
        run_id=run.id,
        project_id=1,
        error_message=str(e),
        error_traceback=traceback.format_exc(),
    )
    print(f"Run {run.id} failed: {run.error_message}")
```

### 5. Cancelling a Run

```python
# Cancel an in-progress run
run = AgentRunService.cancel_run(
    db=session,
    run_id=run.id,
    project_id=1,
    initiated_by="admin@example.com",  # Who canceled it
    initiated_by_role="admin",
)

print(f"Run cancelled by {run.state_reason}")
```

### 6. Retrieving a Run

```python
# Get a single run
run = AgentRunService.get_run(
    db=session,
    run_id=run.id,
    project_id=1,
)
```

### 7. Listing Runs

```python
# Get all runs for a project
runs, total = AgentRunService.list_runs(
    db=session,
    project_id=1,
    limit=10,
    offset=0,
    state="completed",  # Optional: filter by state
)

print(f"Found {total} completed runs")
```

---

## State Machine

```
queued
  ├─→ running
  │    ├─→ completed  [✓ final]
  │    ├─→ failed     [✗ final]
  │    └─→ cancelled
  │         └─→ (cancelled) [✗ final]
  │
  ├─→ failed          [✗ final]
  └─→ cancelled       [✗ final]
```

**Valid Transitions**:
- `queued` → `running` (start_run)
- `queued` → `failed` (fail_run)
- `queued` → `cancelled` (cancel_run)
- `running` → `completed` (complete_run)
- `running` → `failed` (fail_run)
- `running` → `cancelled` (cancel_run)
- `awaiting_input` → `completed` (complete_run)
- `awaiting_input` → `failed` (fail_run)
- `awaiting_input` → `cancelled` (cancel_run)

**Final States** (no transitions possible):
- `completed`
- `failed`
- `cancelled`

---

## Error Handling

### Permission Errors

```python
from app.core.exceptions import PermissionDeniedError

try:
    AgentRunService.create_run(
        db=session,
        project_id=1,
        definition_id=123,
        initiated_by="viewer@example.com",
        initiated_by_role="viewer",  # ← Does not have agents.run permission
        input_content="Test",
    )
except PermissionDeniedError as e:
    print(f"Access denied: {e.message}")
    # Handle: show user a permission error message
```

### Not Found Errors

```python
from app.core.exceptions import NotFoundError

try:
    AgentRunService.get_run(
        db=session,
        run_id=9999,  # ← Does not exist
        project_id=1,
    )
except NotFoundError as e:
    print(f"Not found: {e.message}")
    # Handle: return 404 to client
```

### Validation Errors

```python
from app.core.exceptions import ValidationError

try:
    AgentRunService.create_run(
        db=session,
        project_id=1,
        definition_id=123,  # ← Agent is paused, not active
        initiated_by="alice@example.com",
        initiated_by_role="author",
        input_content="Test",
    )
except ValidationError as e:
    print(f"Invalid: {e.message}")
    # Handle: show user a validation error
```

### Workflow Errors

```python
from app.core.exceptions import WorkflowError

try:
    AgentRunService.start_run(
        db=session,
        run_id=run.id,
        project_id=1,
    )
    # ↑ Tries to start an already-running run
except WorkflowError as e:
    print(f"Workflow violation: {e.message}")
    # Handle: run is already in wrong state
```

### Budget Exceeded

```python
from promptops_app.services.budget_service import BudgetExceededError

try:
    AgentRunService.create_run(
        db=session,
        project_id=1,
        definition_id=123,
        initiated_by="alice@example.com",
        initiated_by_role="author",
        input_content="Test",
        estimated_cost=1000.00,  # ← Exceeds project budget
    )
except BudgetExceededError as e:
    print(f"Budget exceeded: ${e.current_spend:.2f} of ${e.limit_usd:.2f}")
    # Handle: show user budget exceeded message
```

---

## Idempotency

The `request_id` parameter enables safe request replay. If a client retries with the same `request_id`, they get the same run back.

```python
request_id = "req-user-generated-uuid"

# First call
run1 = AgentRunService.create_run(
    db=session,
    project_id=1,
    definition_id=123,
    initiated_by="alice@example.com",
    initiated_by_role="author",
    input_content="Create a lesson on photosynthesis",
    request_id=request_id,
)

# Network error, client retries with same request_id
run2 = AgentRunService.create_run(
    db=session,
    project_id=1,
    definition_id=123,
    initiated_by="alice@example.com",
    initiated_by_role="author",
    input_content="Create a lesson on photosynthesis",  # Must be same
    request_id=request_id,
)

assert run1.id == run2.id  # Same run returned
```

---

## Tenant Isolation

Every operation is scoped by `project_id`. The service enforces that agents and runs belong to the specified project.

```python
# Agent belongs to project 1
agent = AgentsTenantService.validate_agent_ownership(
    db=session,
    agent_id=123,
    project_id=1,  # ✓ OK: agent belongs here
)

# Try to run it in project 2
try:
    run = AgentRunService.create_run(
        db=session,
        project_id=2,  # ✗ Different project
        definition_id=123,  # ← Agent from project 1
        initiated_by="alice@example.com",
        initiated_by_role="author",
        input_content="Test",
    )
except NotFoundError:
    print("Agent not found in project 2")
    # ↑ Correct behavior: cross-tenant access denied
```

---

## Budget Integration

Runs integrate with the existing budget_service for cost tracking.

### How Budget Works

1. **Reservation** (create_run): Reserves worst-case estimated cost
2. **Execution**: Agent runs and uses actual tokens
3. **Reconciliation** (complete_run): Adjusts reserved cost down to actual

```python
# 1. Create run with estimated cost
run = AgentRunService.create_run(
    db=session,
    project_id=1,
    definition_id=123,
    initiated_by="alice@example.com",
    initiated_by_role="author",
    input_content="Test",
    estimated_cost=0.10,  # Worst-case estimate
)
# ↑ budget_period_spend incremented by $0.10

# 2. Agent executes...

# 3. Complete run with actual cost
run = AgentRunService.complete_run(
    db=session,
    run_id=run.id,
    project_id=1,
    result={...},
    actual_cost=0.027,  # Actual cost from LLM
)
# ↑ budget_period_spend adjusted down by $0.073
```

### Release on Failure/Cancellation

If a run fails or is cancelled, the reserved budget is released (reversed).

```python
# Run fails
run = AgentRunService.fail_run(
    db=session,
    run_id=run.id,
    project_id=1,
    error_message="LLM timeout",
)
# ↑ budget_period_spend decremented by $0.10 (full reversal)

# Run cancelled
run = AgentRunService.cancel_run(
    db=session,
    run_id=run.id,
    project_id=1,
    initiated_by="alice@example.com",
    initiated_by_role="author",
)
# ↑ budget_period_spend decremented by $0.10 (full reversal)
```

---

## Concurrency & SQLite

All state transitions use atomic UPDATE operations that are race-safe with SQLite.

**No SELECT FOR UPDATE** (not supported by SQLite)
**Instead**: Atomic UPDATE with row count check

```python
# Behind the scenes in start_run():
rows_updated = db.query(AgentRun).filter(
    and_(
        AgentRun.id == run_id,
        AgentRun.state == "queued"  # Only transition if queued
    )
).update({
    AgentRun.state: "running",
    AgentRun.started_at: now(),
})

if rows_updated != 1:
    raise WorkflowError("Claim failed")
    # ↑ Another worker already started this run
```

**Benefit**: Multiple workers can safely start different runs concurrently.

---

## Logging

All operations are logged for debugging.

```python
# Logs include:
# - INFO: Created run, Started run, Completed run
# - WARNING: Budget reservation issues
# - ERROR: Run failures, budget release issues

# In logs you'll see:
# INFO Created run 789 for agent 123 in project 1. Estimated cost: $0.05
# INFO Started run 789. Will expire at 2026-10-07 15:42:30
# INFO Completed run 789. Cost: $0.027, Execution time: 5230ms
# ERROR Failed run 789. Error: API timeout. Execution time: 29876ms
```

---

## API Integration Pattern

For FastAPI routes, wrap service calls with error handling:

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.exceptions import AppError

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])

@router.post("/{agent_id}/run")
async def create_agent_run(
    agent_id: int,
    request: RunRequest,
    project_id: int = Depends(get_current_project_id),
    user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        run = AgentRunService.create_run(
            db=db,
            project_id=project_id,
            definition_id=agent_id,
            initiated_by=user.email,
            initiated_by_role=user.role,
            input_content=request.input_content,
            estimated_cost=request.estimated_cost,
            request_id=request.request_id,
        )
        return {"run_id": run.id, "state": run.state}
    except AppError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
```

---

## Testing

Run the unit tests to verify the implementation:

```bash
pytest tests/test_agent_run_service.py -v
```

**Test Coverage**:
- Happy path (create → start → complete)
- Error cases (permission, not found, validation)
- Idempotency (same request_id)
- Tenant isolation (cross-tenant rejection)
- State transitions (invalid transitions)
- Budget integration (reservation, reconciliation, release)

---

## Next Steps

Phase 2 continues with:

1. **Stage 2**: AgentStepService (step execution within runs)
2. **Stage 3**: AgentLLMService (LLM integration)
3. **Stage 4**: AgentBudgetService (enhanced budget tracking)
4. **Stage 5**: AgentCheckpointService (resumable execution)
5. **Stage 6**: API routes and end-to-end tests

See `PHASE_2_IMPLEMENTATION_GUIDE.md` for complete plan.

---

## Troubleshooting

### "Agent with id X not found in project Y"
- Agent doesn't exist, or belongs to different project
- Check: agent_id, project_id

### "Cannot start run X. Current state: running"
- Run already started (or already completed/failed)
- Check: only call start_run() once per run

### "Budget exceeded"
- Project has used allocated budget for current period
- Options: increase budget, wait for period reset, use different project

### "Run not found in project X"
- Run doesn't belong to specified project
- Check: run_id, project_id

### "Your role does not have the 'agents.run' permission"
- User's role doesn't allow running agents
- Contact admin for permission grant

---

## References

- **Models**: `promptops_app/agents_models.py`
- **Permissions**: `app/core/permissions.py`
- **Budget Service**: `promptops_app/services/budget_service.py`
- **Tenant Service**: `promptops_app/services/agents_tenant_service.py`
- **Exceptions**: `app/core/exceptions.py`
- **Tests**: `tests/test_agent_run_service.py`

---

**Last Updated**: 2026-10-07  
**Implementation**: Complete ✅  
**Ready for Production**: Yes
