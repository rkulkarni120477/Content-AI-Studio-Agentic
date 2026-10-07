"""Agent Monitoring & Observability Service

Provides comprehensive logging, metrics collection, and observability for agent operations.
Tracks execution times, tokens, costs, errors, and performance metrics.

Key Features:
- Structured logging for all agent operations
- Performance metrics collection (execution time, tokens, cost)
- Error rate tracking and categorization
- Budget utilization tracking per run/workflow
- Checkpoint recovery tracking
- Correlation IDs for request tracing

Usage:
    from promptops_app.services.agent_monitoring import AgentMonitor

    monitor = AgentMonitor()

    # Log run start
    monitor.log_run_start(project_id, agent_id, run_id)

    # Track step execution
    monitor.log_step_execution(run_id, step_index, duration_ms, tokens_used)

    # Log errors
    monitor.log_error(run_id, error_type, error_message)

    # Get metrics
    metrics = monitor.get_run_metrics(run_id)
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, asdict, field
from datetime import datetime
from enum import Enum
from typing import Dict, Optional, Any, List
from threading import local

_log = logging.getLogger(__name__)
_correlation_context = local()


class EventType(Enum):
    """Types of events logged by the monitoring system."""
    RUN_START = "run_start"
    RUN_COMPLETE = "run_complete"
    RUN_FAILED = "run_failed"
    RUN_CANCELLED = "run_cancelled"
    STEP_START = "step_start"
    STEP_COMPLETE = "step_complete"
    STEP_FAILED = "step_failed"
    STEP_RETRY = "step_retry"
    MODEL_CALL = "model_call"
    CHECKPOINT_CREATE = "checkpoint_create"
    CHECKPOINT_RESTORE = "checkpoint_restore"
    BUDGET_RESERVED = "budget_reserved"
    BUDGET_RECONCILED = "budget_reconciled"
    BUDGET_EXCEEDED = "budget_exceeded"
    ERROR_OCCURRED = "error_occurred"
    WORKFLOW_START = "workflow_start"
    WORKFLOW_COMPLETE = "workflow_complete"
    WORKFLOW_FAILED = "workflow_failed"


class ErrorCategory(Enum):
    """Categorization of errors for tracking."""
    VALIDATION = "validation"
    AUTHORIZATION = "authorization"
    NOT_FOUND = "not_found"
    TIMEOUT = "timeout"
    BUDGET = "budget"
    MODEL = "model"
    DATABASE = "database"
    NETWORK = "network"
    UNKNOWN = "unknown"


@dataclass
class MetricPoint:
    """A single metric data point."""
    timestamp: datetime
    value: float
    unit: str
    labels: Dict[str, str] = field(default_factory=dict)


@dataclass
class ExecutionMetrics:
    """Metrics for a single run execution."""
    run_id: str
    project_id: int
    agent_id: int
    state: str

    # Timing metrics
    total_duration_ms: float = 0.0
    step_count: int = 0
    average_step_duration_ms: float = 0.0

    # Token metrics
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0

    # Cost metrics
    estimated_cost: float = 0.0
    actual_cost: float = 0.0

    # Error metrics
    error_count: int = 0
    error_categories: Dict[str, int] = field(default_factory=dict)
    last_error: Optional[str] = None

    # Checkpoint metrics
    checkpoint_count: int = 0
    recovery_count: int = 0

    # Timestamps
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class AgentMonitor:
    """Central monitoring and observability service for agent operations."""

    def __init__(self):
        """Initialize the monitoring service."""
        self._metrics_store: Dict[str, ExecutionMetrics] = {}
        self._error_log: List[Dict[str, Any]] = []
        self._event_log: List[Dict[str, Any]] = []
        self._performance_stats: Dict[str, List[float]] = {}

    # =========================================================================
    # Correlation ID Management
    # =========================================================================

    @staticmethod
    def get_correlation_id() -> str:
        """Get or create correlation ID for current request context."""
        if not hasattr(_correlation_context, 'id'):
            _correlation_context.id = str(uuid.uuid4())
        return _correlation_context.id

    @staticmethod
    def set_correlation_id(correlation_id: str) -> None:
        """Set correlation ID for current request context."""
        _correlation_context.id = correlation_id

    # =========================================================================
    # Run Lifecycle Logging
    # =========================================================================

    def log_run_start(
        self,
        project_id: int,
        agent_id: int,
        run_id: str,
        estimated_cost: Optional[float] = None,
    ) -> None:
        """Log the start of an agent run."""
        correlation_id = self.get_correlation_id()

        metrics = ExecutionMetrics(
            run_id=run_id,
            project_id=project_id,
            agent_id=agent_id,
            state="running",
            started_at=datetime.utcnow(),
            estimated_cost=estimated_cost or 0.0,
        )
        self._metrics_store[run_id] = metrics

        event = {
            "correlation_id": correlation_id,
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": EventType.RUN_START.value,
            "run_id": run_id,
            "project_id": project_id,
            "agent_id": agent_id,
            "estimated_cost": estimated_cost,
        }
        self._event_log.append(event)

        _log.info(
            "Agent run started",
            extra={
                "run_id": run_id,
                "project_id": project_id,
                "agent_id": agent_id,
                "correlation_id": correlation_id,
                "estimated_cost": estimated_cost,
            }
        )

    def log_run_complete(self, run_id: str, actual_cost: float) -> None:
        """Log successful completion of an agent run."""
        if run_id not in self._metrics_store:
            _log.warning(f"Run {run_id} not found in metrics store")
            return

        correlation_id = self.get_correlation_id()
        metrics = self._metrics_store[run_id]
        metrics.state = "completed"
        metrics.completed_at = datetime.utcnow()
        metrics.actual_cost = actual_cost

        if metrics.started_at:
            duration = (metrics.completed_at - metrics.started_at).total_seconds() * 1000
            metrics.total_duration_ms = duration
            self._track_performance("run_duration_ms", duration)

        event = {
            "correlation_id": correlation_id,
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": EventType.RUN_COMPLETE.value,
            "run_id": run_id,
            "actual_cost": actual_cost,
            "duration_ms": metrics.total_duration_ms,
            "token_count": metrics.total_tokens,
        }
        self._event_log.append(event)

        _log.info(
            "Agent run completed",
            extra={
                "run_id": run_id,
                "duration_ms": metrics.total_duration_ms,
                "actual_cost": actual_cost,
                "correlation_id": correlation_id,
            }
        )

    def log_run_failed(
        self,
        run_id: str,
        error_type: str,
        error_message: str,
    ) -> None:
        """Log failure of an agent run."""
        if run_id not in self._metrics_store:
            _log.warning(f"Run {run_id} not found in metrics store")
            return

        correlation_id = self.get_correlation_id()
        metrics = self._metrics_store[run_id]
        metrics.state = "failed"
        metrics.completed_at = datetime.utcnow()
        metrics.last_error = error_message

        if metrics.started_at:
            duration = (metrics.completed_at - metrics.started_at).total_seconds() * 1000
            metrics.total_duration_ms = duration

        event = {
            "correlation_id": correlation_id,
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": EventType.RUN_FAILED.value,
            "run_id": run_id,
            "error_type": error_type,
            "error_message": error_message,
        }
        self._event_log.append(event)

        _log.error(
            "Agent run failed",
            extra={
                "run_id": run_id,
                "error_type": error_type,
                "error_message": error_message,
                "correlation_id": correlation_id,
            }
        )

    def log_run_cancelled(self, run_id: str) -> None:
        """Log cancellation of an agent run."""
        if run_id not in self._metrics_store:
            _log.warning(f"Run {run_id} not found in metrics store")
            return

        correlation_id = self.get_correlation_id()
        metrics = self._metrics_store[run_id]
        metrics.state = "cancelled"
        metrics.completed_at = datetime.utcnow()

        event = {
            "correlation_id": correlation_id,
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": EventType.RUN_CANCELLED.value,
            "run_id": run_id,
        }
        self._event_log.append(event)

        _log.info(
            "Agent run cancelled",
            extra={"run_id": run_id, "correlation_id": correlation_id}
        )

    # =========================================================================
    # Step Lifecycle Logging
    # =========================================================================

    def log_step_start(
        self,
        run_id: str,
        step_index: int,
        step_type: str,
    ) -> None:
        """Log the start of a step in an agent run."""
        if run_id not in self._metrics_store:
            _log.warning(f"Run {run_id} not found in metrics store")
            return

        correlation_id = self.get_correlation_id()
        event = {
            "correlation_id": correlation_id,
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": EventType.STEP_START.value,
            "run_id": run_id,
            "step_index": step_index,
            "step_type": step_type,
        }
        self._event_log.append(event)

    def log_step_complete(
        self,
        run_id: str,
        step_index: int,
        duration_ms: float,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> None:
        """Log successful completion of a step."""
        if run_id not in self._metrics_store:
            _log.warning(f"Run {run_id} not found in metrics store")
            return

        correlation_id = self.get_correlation_id()
        metrics = self._metrics_store[run_id]

        # Update metrics
        metrics.step_count += 1
        metrics.total_prompt_tokens += prompt_tokens
        metrics.total_completion_tokens += completion_tokens
        metrics.total_tokens = metrics.total_prompt_tokens + metrics.total_completion_tokens

        if metrics.step_count > 0:
            metrics.average_step_duration_ms = (
                metrics.average_step_duration_ms * (metrics.step_count - 1) + duration_ms
            ) / metrics.step_count

        self._track_performance("step_duration_ms", duration_ms)
        self._track_performance("tokens_per_step", completion_tokens)

        event = {
            "correlation_id": correlation_id,
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": EventType.STEP_COMPLETE.value,
            "run_id": run_id,
            "step_index": step_index,
            "duration_ms": duration_ms,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        }
        self._event_log.append(event)

        _log.debug(
            "Step completed",
            extra={
                "run_id": run_id,
                "step_index": step_index,
                "duration_ms": duration_ms,
                "tokens": completion_tokens,
            }
        )

    def log_step_failed(
        self,
        run_id: str,
        step_index: int,
        error_type: str,
        error_message: str,
    ) -> None:
        """Log failure of a step."""
        if run_id not in self._metrics_store:
            _log.warning(f"Run {run_id} not found in metrics store")
            return

        correlation_id = self.get_correlation_id()
        metrics = self._metrics_store[run_id]
        metrics.error_count += 1

        # Categorize error
        category = self._categorize_error(error_type)
        metrics.error_categories[category.value] = (
            metrics.error_categories.get(category.value, 0) + 1
        )

        event = {
            "correlation_id": correlation_id,
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": EventType.STEP_FAILED.value,
            "run_id": run_id,
            "step_index": step_index,
            "error_type": error_type,
            "error_message": error_message,
        }
        self._event_log.append(event)

        _log.error(
            "Step failed",
            extra={
                "run_id": run_id,
                "step_index": step_index,
                "error_type": error_type,
            }
        )

    def log_step_retry(
        self,
        run_id: str,
        step_index: int,
        attempt_number: int,
    ) -> None:
        """Log retry of a failed step."""
        correlation_id = self.get_correlation_id()
        event = {
            "correlation_id": correlation_id,
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": EventType.STEP_RETRY.value,
            "run_id": run_id,
            "step_index": step_index,
            "attempt_number": attempt_number,
        }
        self._event_log.append(event)

    # =========================================================================
    # Model Call Logging
    # =========================================================================

    def log_model_call(
        self,
        run_id: str,
        model_name: str,
        prompt_tokens: int,
        completion_tokens: int,
        duration_ms: float,
    ) -> None:
        """Log a model API call."""
        correlation_id = self.get_correlation_id()
        total_tokens = prompt_tokens + completion_tokens

        event = {
            "correlation_id": correlation_id,
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": EventType.MODEL_CALL.value,
            "run_id": run_id,
            "model_name": model_name,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "duration_ms": duration_ms,
        }
        self._event_log.append(event)

        _log.debug(
            "Model call completed",
            extra={
                "model": model_name,
                "tokens": total_tokens,
                "duration_ms": duration_ms,
            }
        )

    # =========================================================================
    # Checkpoint Logging
    # =========================================================================

    def log_checkpoint_create(
        self,
        run_id: str,
        checkpoint_index: int,
        state_size_bytes: int,
    ) -> None:
        """Log creation of a checkpoint."""
        if run_id not in self._metrics_store:
            _log.warning(f"Run {run_id} not found in metrics store")
            return

        correlation_id = self.get_correlation_id()
        metrics = self._metrics_store[run_id]
        metrics.checkpoint_count += 1

        event = {
            "correlation_id": correlation_id,
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": EventType.CHECKPOINT_CREATE.value,
            "run_id": run_id,
            "checkpoint_index": checkpoint_index,
            "state_size_bytes": state_size_bytes,
        }
        self._event_log.append(event)

        _log.debug(
            "Checkpoint created",
            extra={
                "run_id": run_id,
                "checkpoint_index": checkpoint_index,
                "size_bytes": state_size_bytes,
            }
        )

    def log_checkpoint_restore(
        self,
        run_id: str,
        checkpoint_index: int,
        duration_ms: float,
    ) -> None:
        """Log restoration of a checkpoint."""
        if run_id not in self._metrics_store:
            _log.warning(f"Run {run_id} not found in metrics store")
            return

        correlation_id = self.get_correlation_id()
        metrics = self._metrics_store[run_id]
        metrics.recovery_count += 1

        event = {
            "correlation_id": correlation_id,
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": EventType.CHECKPOINT_RESTORE.value,
            "run_id": run_id,
            "checkpoint_index": checkpoint_index,
            "duration_ms": duration_ms,
        }
        self._event_log.append(event)

        _log.info(
            "Checkpoint restored",
            extra={
                "run_id": run_id,
                "checkpoint_index": checkpoint_index,
                "duration_ms": duration_ms,
            }
        )

    # =========================================================================
    # Budget Logging
    # =========================================================================

    def log_budget_reserved(
        self,
        run_id: str,
        project_id: int,
        amount_usd: float,
    ) -> None:
        """Log budget reservation."""
        correlation_id = self.get_correlation_id()
        event = {
            "correlation_id": correlation_id,
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": EventType.BUDGET_RESERVED.value,
            "run_id": run_id,
            "project_id": project_id,
            "amount_usd": amount_usd,
        }
        self._event_log.append(event)

    def log_budget_reconciled(
        self,
        run_id: str,
        project_id: int,
        estimated_cost: float,
        actual_cost: float,
    ) -> None:
        """Log budget reconciliation."""
        correlation_id = self.get_correlation_id()
        event = {
            "correlation_id": correlation_id,
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": EventType.BUDGET_RECONCILED.value,
            "run_id": run_id,
            "project_id": project_id,
            "estimated_cost": estimated_cost,
            "actual_cost": actual_cost,
        }
        self._event_log.append(event)

    def log_budget_exceeded(
        self,
        run_id: str,
        project_id: int,
        budget_limit: float,
        attempted_cost: float,
    ) -> None:
        """Log budget exceeded event."""
        correlation_id = self.get_correlation_id()
        event = {
            "correlation_id": correlation_id,
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": EventType.BUDGET_EXCEEDED.value,
            "run_id": run_id,
            "project_id": project_id,
            "budget_limit": budget_limit,
            "attempted_cost": attempted_cost,
        }
        self._event_log.append(event)

        _log.warning(
            "Budget exceeded",
            extra={
                "project_id": project_id,
                "limit": budget_limit,
                "attempted": attempted_cost,
            }
        )

    # =========================================================================
    # Error Logging
    # =========================================================================

    def log_error(
        self,
        run_id: str,
        error_type: str,
        error_message: str,
        error_details: Optional[Dict] = None,
    ) -> None:
        """Log an error that occurred during agent execution."""
        correlation_id = self.get_correlation_id()
        category = self._categorize_error(error_type)

        error_record = {
            "correlation_id": correlation_id,
            "timestamp": datetime.utcnow().isoformat(),
            "run_id": run_id,
            "error_type": error_type,
            "error_category": category.value,
            "error_message": error_message,
            "error_details": error_details or {},
        }
        self._error_log.append(error_record)

        event = {
            "correlation_id": correlation_id,
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": EventType.ERROR_OCCURRED.value,
            "run_id": run_id,
            "error_type": error_type,
            "error_category": category.value,
        }
        self._event_log.append(event)

        _log.error(
            f"Error in run: {error_message}",
            extra={
                "run_id": run_id,
                "error_type": error_type,
                "error_category": category.value,
            }
        )

    # =========================================================================
    # Workflow Logging
    # =========================================================================

    def log_workflow_start(
        self,
        project_id: int,
        workflow_id: str,
        run_id: str,
        agent_count: int,
    ) -> None:
        """Log the start of a workflow execution."""
        correlation_id = self.get_correlation_id()
        event = {
            "correlation_id": correlation_id,
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": EventType.WORKFLOW_START.value,
            "project_id": project_id,
            "workflow_id": workflow_id,
            "run_id": run_id,
            "agent_count": agent_count,
        }
        self._event_log.append(event)

        _log.info(
            "Workflow execution started",
            extra={
                "workflow_id": workflow_id,
                "run_id": run_id,
                "agent_count": agent_count,
                "correlation_id": correlation_id,
            }
        )

    def log_workflow_complete(
        self,
        workflow_id: str,
        run_id: str,
        duration_ms: float,
        agents_completed: int,
    ) -> None:
        """Log successful completion of a workflow."""
        correlation_id = self.get_correlation_id()
        event = {
            "correlation_id": correlation_id,
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": EventType.WORKFLOW_COMPLETE.value,
            "workflow_id": workflow_id,
            "run_id": run_id,
            "duration_ms": duration_ms,
            "agents_completed": agents_completed,
        }
        self._event_log.append(event)

        _log.info(
            "Workflow execution completed",
            extra={
                "workflow_id": workflow_id,
                "run_id": run_id,
                "duration_ms": duration_ms,
            }
        )

    def log_workflow_failed(
        self,
        workflow_id: str,
        run_id: str,
        error_type: str,
        error_message: str,
    ) -> None:
        """Log failure of a workflow."""
        correlation_id = self.get_correlation_id()
        event = {
            "correlation_id": correlation_id,
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": EventType.WORKFLOW_FAILED.value,
            "workflow_id": workflow_id,
            "run_id": run_id,
            "error_type": error_type,
            "error_message": error_message,
        }
        self._event_log.append(event)

        _log.error(
            "Workflow execution failed",
            extra={
                "workflow_id": workflow_id,
                "run_id": run_id,
                "error_type": error_type,
            }
        )

    # =========================================================================
    # Metrics Retrieval
    # =========================================================================

    def get_run_metrics(self, run_id: str) -> Optional[ExecutionMetrics]:
        """Get metrics for a specific run."""
        return self._metrics_store.get(run_id)

    def get_all_metrics(self) -> Dict[str, ExecutionMetrics]:
        """Get all collected metrics."""
        return self._metrics_store.copy()

    def get_error_log(self) -> List[Dict[str, Any]]:
        """Get all logged errors."""
        return self._error_log.copy()

    def get_event_log(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get event log, optionally limited to most recent events."""
        if limit is None:
            return self._event_log.copy()
        return self._event_log[-limit:] if limit > 0 else []

    def get_performance_statistics(self) -> Dict[str, Dict[str, float]]:
        """Get performance statistics for tracked metrics."""
        stats = {}
        for metric_name, values in self._performance_stats.items():
            if values:
                stats[metric_name] = {
                    "min": min(values),
                    "max": max(values),
                    "avg": sum(values) / len(values),
                    "count": len(values),
                }
        return stats

    def get_error_summary(self) -> Dict[str, int]:
        """Get summary of errors by category."""
        summary = {}
        for error_record in self._error_log:
            category = error_record.get("error_category", "unknown")
            summary[category] = summary.get(category, 0) + 1
        return summary

    # =========================================================================
    # Clear and Export
    # =========================================================================

    def clear_metrics(self) -> None:
        """Clear all stored metrics (useful for testing)."""
        self._metrics_store.clear()
        self._error_log.clear()
        self._event_log.clear()
        self._performance_stats.clear()

    def export_metrics_json(self) -> str:
        """Export metrics as JSON string."""
        data = {
            "metrics": {
                run_id: asdict(metrics)
                for run_id, metrics in self._metrics_store.items()
            },
            "errors": self._error_log,
            "events": self._event_log,
            "statistics": self.get_performance_statistics(),
        }
        return json.dumps(data, default=str)

    # =========================================================================
    # Private Methods
    # =========================================================================

    def _track_performance(self, metric_name: str, value: float) -> None:
        """Track a performance metric."""
        if metric_name not in self._performance_stats:
            self._performance_stats[metric_name] = []
        self._performance_stats[metric_name].append(value)

    @staticmethod
    def _categorize_error(error_type: str) -> ErrorCategory:
        """Categorize an error by type."""
        error_type_lower = error_type.lower()

        if "validation" in error_type_lower:
            return ErrorCategory.VALIDATION
        elif "permission" in error_type_lower or "unauthorized" in error_type_lower:
            return ErrorCategory.AUTHORIZATION
        elif "not found" in error_type_lower or "notfound" in error_type_lower:
            return ErrorCategory.NOT_FOUND
        elif "timeout" in error_type_lower:
            return ErrorCategory.TIMEOUT
        elif "budget" in error_type_lower:
            return ErrorCategory.BUDGET
        elif "model" in error_type_lower:
            return ErrorCategory.MODEL
        elif "database" in error_type_lower or "db" in error_type_lower:
            return ErrorCategory.DATABASE
        elif "network" in error_type_lower or "connection" in error_type_lower:
            return ErrorCategory.NETWORK
        else:
            return ErrorCategory.UNKNOWN


# Global monitor instance
_global_monitor: Optional[AgentMonitor] = None


def get_monitor() -> AgentMonitor:
    """Get or create the global monitor instance."""
    global _global_monitor
    if _global_monitor is None:
        _global_monitor = AgentMonitor()
    return _global_monitor
