"""
Tenant Isolation for Agent Builder

Ensures that agents, runs, workflows, and related data are properly scoped
to tenant (project_id) boundaries. No cross-tenant data leakage.
"""

from sqlalchemy.orm import Session
from sqlalchemy import and_

from promptops_app.agents_models import (
    AgentDefinition,
    AgentRun,
    AgentRunStep,
    AgentWorkflow,
    ProposedChange,
)
from app.core.exceptions import NotFoundError


class AgentsTenantService:
    """Tenant isolation enforcement for agent operations."""

    @staticmethod
    def validate_agent_ownership(session: Session, agent_id: int, project_id: int) -> AgentDefinition:
        """
        Verify an agent exists and belongs to the specified tenant.

        Raises NotFoundError if agent is not found or belongs to a different tenant.
        """
        agent = session.query(AgentDefinition).filter(
            and_(
                AgentDefinition.id == agent_id,
                AgentDefinition.project_id == project_id,
            )
        ).first()

        if not agent:
            raise NotFoundError("Agent", agent_id)

        return agent

    @staticmethod
    def validate_run_ownership(session: Session, run_id: int, project_id: int) -> AgentRun:
        """Verify a run exists and belongs to the specified tenant."""
        run = session.query(AgentRun).filter(
            and_(
                AgentRun.id == run_id,
                AgentRun.project_id == project_id,
            )
        ).first()

        if not run:
            raise NotFoundError("Run", run_id)

        return run

    @staticmethod
    def validate_step_ownership(session: Session, step_id: int, run_id: int, project_id: int) -> AgentRunStep:
        """Verify a step exists, belongs to the specified run, and run belongs to the tenant."""
        step = session.query(AgentRunStep).join(
            AgentRun,
            AgentRunStep.run_id == AgentRun.id,
        ).filter(
            and_(
                AgentRunStep.id == step_id,
                AgentRunStep.run_id == run_id,
                AgentRun.project_id == project_id,
            )
        ).first()

        if not step:
            raise NotFoundError("Step", step_id)

        return step

    @staticmethod
    def validate_workflow_ownership(session: Session, workflow_id: int, project_id: int) -> AgentWorkflow:
        """Verify a workflow exists and belongs to the specified tenant."""
        workflow = session.query(AgentWorkflow).filter(
            and_(
                AgentWorkflow.id == workflow_id,
                AgentWorkflow.project_id == project_id,
            )
        ).first()

        if not workflow:
            raise NotFoundError("Workflow", workflow_id)

        return workflow

    @staticmethod
    def get_tenant_agents(session: Session, project_id: int, lifecycle_state: str = None):
        """
        Get all agents for a tenant, optionally filtered by lifecycle state.

        Args:
            session: SQLAlchemy session
            project_id: Tenant ID
            lifecycle_state: Filter by state (draft|active|paused|archived) or None for all

        Returns:
            Query object (lazy-loaded)
        """
        query = session.query(AgentDefinition).filter(
            AgentDefinition.project_id == project_id
        )

        if lifecycle_state:
            query = query.filter(AgentDefinition.lifecycle_state == lifecycle_state)

        return query.order_by(AgentDefinition.created_at.desc())

    @staticmethod
    def get_tenant_runs(session: Session, project_id: int, limit: int = 100, offset: int = 0):
        """Get paginated run history for a tenant."""
        return session.query(AgentRun).filter(
            AgentRun.project_id == project_id
        ).order_by(
            AgentRun.created_at.desc()
        ).limit(limit).offset(offset)

    @staticmethod
    def get_user_runs(session: Session, project_id: int, username: str, limit: int = 50):
        """Get runs initiated by a specific user in a tenant."""
        return session.query(AgentRun).filter(
            and_(
                AgentRun.project_id == project_id,
                AgentRun.initiated_by == username,
            )
        ).order_by(
            AgentRun.created_at.desc()
        ).limit(limit)

    @staticmethod
    def get_tenant_proposed_changes(session: Session, project_id: int, status: str = None):
        """Get proposed changes for a tenant's runs."""
        query = session.query(ProposedChange).join(
            AgentRun,
            ProposedChange.run_id == AgentRun.id,
        ).filter(
            AgentRun.project_id == project_id
        )

        if status:
            query = query.filter(ProposedChange.status == status)

        return query.order_by(ProposedChange.created_at.desc())
