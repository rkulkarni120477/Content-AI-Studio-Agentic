"""Add Agent Builder tables for multi-agent workflows.

Creates the complete data model for agent definitions, execution runs,
checkpoints, and proposed changes. All agent data is tenant-scoped via
project_id foreign key.

Tables created:
  - agent_templates: Platform-level agent definitions
  - agent_definitions: Tenant-scoped agent instances
  - agent_definition_versions: Immutable configuration snapshots
  - knowledge_bindings: Links agents to knowledge sources
  - allowed_handoffs: Authorized agent-to-agent calls
  - agent_runs: Execution instances
  - agent_run_steps: Individual steps within runs
  - agent_checkpoints: Durable state for resumption
  - proposed_changes: AI-suggested edits
  - agent_workflows: Multi-agent workflow definitions
  - workflow_runs: Workflow execution instances

SQLite-compatible: uses standard SQLAlchemy types, no PostgreSQL-specific
features. Foreign keys enforced at application level.

Revision ID: 000100000033
Revises: 000100000032
"""

import sqlalchemy as sa
from alembic import op

revision = "000100000033"
down_revision = "000100000032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create Agent Builder tables."""

    # ─────────────────────────────────────────────────────────────────────────
    # Platform Agent Templates
    # ─────────────────────────────────────────────────────────────────────────
    op.create_table(
        "agent_templates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(50), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("specialization", sa.String(50), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("input_schema", sa.Text(), nullable=False),
        sa.Column("output_schema", sa.Text(), nullable=False),
        sa.Column("owner", sa.String(100), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_templates_specialization", "agent_templates", ["specialization"])

    # ─────────────────────────────────────────────────────────────────────────
    # Tenant Agent Definitions & Versions
    # ─────────────────────────────────────────────────────────────────────────
    op.create_table(
        "agent_definitions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("template_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("call_handle", sa.String(50), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("owner", sa.String(100), nullable=False),
        sa.Column("configuration", sa.Text(), nullable=False),
        sa.Column("lifecycle_state", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("lifecycle_reason", sa.Text(), nullable=True),
        sa.Column("current_version_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("activated_version_number", sa.Integer(), nullable=True),
        sa.Column("activated_at", sa.DateTime(), nullable=True),
        sa.Column("activated_by", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_by", sa.String(100), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["template_id"], ["agent_templates.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "call_handle", name="uq_agent_definition_project_call_handle"),
    )
    op.create_index("ix_agent_definitions_project", "agent_definitions", ["project_id"])
    op.create_index("ix_agent_definitions_lifecycle_state", "agent_definitions", ["lifecycle_state"])

    op.create_table(
        "agent_definition_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("definition_id", sa.Integer(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("configuration", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("activated_at", sa.DateTime(), nullable=True),
        sa.Column("activated_by", sa.String(100), nullable=True),
        sa.Column("change_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.ForeignKeyConstraint(["definition_id"], ["agent_definitions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("definition_id", "version_number", name="uq_agent_version_definition_number"),
    )
    op.create_index("ix_agent_versions_definition", "agent_definition_versions", ["definition_id"])

    # ─────────────────────────────────────────────────────────────────────────
    # Knowledge & Tool Bindings
    # ─────────────────────────────────────────────────────────────────────────
    op.create_table(
        "knowledge_bindings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("definition_id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.String(100), nullable=False),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("source_name", sa.String(255), nullable=True),
        sa.Column("knowledge_only", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("course_id", sa.Integer(), nullable=True),
        sa.Column("module_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.ForeignKeyConstraint(["definition_id"], ["agent_definitions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_knowledge_bindings_definition", "knowledge_bindings", ["definition_id"])
    op.create_index("ix_knowledge_bindings_source", "knowledge_bindings", ["source_id", "source_type"])

    op.create_table(
        "allowed_handoffs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("caller_id", sa.Integer(), nullable=False),
        sa.Column("callee_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.ForeignKeyConstraint(["caller_id"], ["agent_definitions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["callee_id"], ["agent_definitions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("caller_id", "callee_id", name="uq_handoff_caller_callee"),
    )
    op.create_index("ix_handoff_caller", "allowed_handoffs", ["caller_id"])
    op.create_index("ix_handoff_callee", "allowed_handoffs", ["callee_id"])

    # ─────────────────────────────────────────────────────────────────────────
    # Agent Execution: Runs & Steps
    # ─────────────────────────────────────────────────────────────────────────
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("definition_id", sa.Integer(), nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("initiated_by", sa.String(100), nullable=False),
        sa.Column("initiated_by_role", sa.String(20), nullable=False),
        sa.Column("artifact_id", sa.Integer(), nullable=True),
        sa.Column("artifact_type", sa.String(50), nullable=True),
        sa.Column("artifact_version", sa.Integer(), nullable=True),
        sa.Column("input_content", sa.Text(), nullable=True),
        sa.Column("input_context", sa.Text(), nullable=True),
        sa.Column("state", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("state_reason", sa.Text(), nullable=True),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_traceback", sa.Text(), nullable=True),
        sa.Column("step_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_steps", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("handoff_depth", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_handoff_depth", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("execution_time_ms", sa.Integer(), nullable=True),
        sa.Column("estimated_cost", sa.Float(), nullable=True),
        sa.Column("actual_cost", sa.Float(), nullable=True),
        sa.Column("budget_reserved", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("request_id", sa.String(64), nullable=True, unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["definition_id"], ["agent_definitions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["version_id"], ["agent_definition_versions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_runs_project", "agent_runs", ["project_id"])
    op.create_index("ix_agent_runs_initiated_by", "agent_runs", ["initiated_by"])
    op.create_index("ix_agent_runs_state", "agent_runs", ["state"])
    op.create_index("ix_agent_runs_created_at", "agent_runs", ["created_at"])

    op.create_table(
        "agent_run_steps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("step_type", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("input_data", sa.Text(), nullable=True),
        sa.Column("output_data", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("model_used", sa.String(100), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("execution_time_ms", sa.Integer(), nullable=True),
        sa.Column("step_cost", sa.Float(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_run_steps_run", "agent_run_steps", ["run_id"])
    op.create_index("ix_agent_run_steps_status", "agent_run_steps", ["status"])

    # ─────────────────────────────────────────────────────────────────────────
    # Persistence & Resumption
    # ─────────────────────────────────────────────────────────────────────────
    op.create_table(
        "agent_checkpoints",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("checkpoint_index", sa.Integer(), nullable=False),
        sa.Column("step_state", sa.Text(), nullable=False),
        sa.Column("resumable", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("resume_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_checkpoints_run", "agent_checkpoints", ["run_id"])
    op.create_index("ix_checkpoints_resumable", "agent_checkpoints", ["run_id", "resumable"])

    # ─────────────────────────────────────────────────────────────────────────
    # Results & Proposed Changes
    # ─────────────────────────────────────────────────────────────────────────
    op.create_table(
        "proposed_changes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("artifact_id", sa.Integer(), nullable=False),
        sa.Column("artifact_type", sa.String(50), nullable=False),
        sa.Column("artifact_version", sa.Integer(), nullable=False),
        sa.Column("change_type", sa.String(50), nullable=False),
        sa.Column("before", sa.Text(), nullable=True),
        sa.Column("after", sa.Text(), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="proposed"),
        sa.Column("applied_at", sa.DateTime(), nullable=True),
        sa.Column("applied_by", sa.String(100), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(), nullable=True),
        sa.Column("dismissed_by", sa.String(100), nullable=True),
        sa.Column("artifact_version_at_application", sa.Integer(), nullable=True),
        sa.Column("artifact_changed_after_proposal", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_proposed_changes_run", "proposed_changes", ["run_id"])
    op.create_index("ix_proposed_changes_artifact", "proposed_changes", ["artifact_id"])
    op.create_index("ix_proposed_changes_status", "proposed_changes", ["status"])

    # ─────────────────────────────────────────────────────────────────────────
    # Multi-Agent Workflows
    # ─────────────────────────────────────────────────────────────────────────
    op.create_table(
        "agent_workflows",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("call_handle", sa.String(50), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("definition", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("owner", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "call_handle", name="uq_workflow_project_handle"),
    )
    op.create_index("ix_workflows_project", "agent_workflows", ["project_id"])

    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("workflow_id", sa.Integer(), nullable=False),
        sa.Column("initiated_by", sa.String(100), nullable=False),
        sa.Column("input_context", sa.Text(), nullable=True),
        sa.Column("state", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("total_cost", sa.Float(), nullable=True),
        sa.Column("execution_time_ms", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workflow_id"], ["agent_workflows.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workflow_runs_project", "workflow_runs", ["project_id"])
    op.create_index("ix_workflow_runs_workflow", "workflow_runs", ["workflow_id"])


def downgrade() -> None:
    """Drop Agent Builder tables."""

    # Drop in reverse order of creation
    op.drop_index("ix_workflow_runs_workflow", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_project", table_name="workflow_runs")
    op.drop_table("workflow_runs")

    op.drop_index("ix_workflows_project", table_name="agent_workflows")
    op.drop_table("agent_workflows")

    op.drop_index("ix_proposed_changes_status", table_name="proposed_changes")
    op.drop_index("ix_proposed_changes_artifact", table_name="proposed_changes")
    op.drop_index("ix_proposed_changes_run", table_name="proposed_changes")
    op.drop_table("proposed_changes")

    op.drop_index("ix_checkpoints_resumable", table_name="agent_checkpoints")
    op.drop_index("ix_checkpoints_run", table_name="agent_checkpoints")
    op.drop_table("agent_checkpoints")

    op.drop_index("ix_agent_run_steps_status", table_name="agent_run_steps")
    op.drop_index("ix_agent_run_steps_run", table_name="agent_run_steps")
    op.drop_table("agent_run_steps")

    op.drop_index("ix_agent_runs_created_at", table_name="agent_runs")
    op.drop_index("ix_agent_runs_state", table_name="agent_runs")
    op.drop_index("ix_agent_runs_initiated_by", table_name="agent_runs")
    op.drop_index("ix_agent_runs_project", table_name="agent_runs")
    op.drop_table("agent_runs")

    op.drop_index("ix_handoff_callee", table_name="allowed_handoffs")
    op.drop_index("ix_handoff_caller", table_name="allowed_handoffs")
    op.drop_table("allowed_handoffs")

    op.drop_index("ix_knowledge_bindings_source", table_name="knowledge_bindings")
    op.drop_index("ix_knowledge_bindings_definition", table_name="knowledge_bindings")
    op.drop_table("knowledge_bindings")

    op.drop_index("ix_agent_versions_definition", table_name="agent_definition_versions")
    op.drop_table("agent_definition_versions")

    op.drop_index("ix_agent_definitions_lifecycle_state", table_name="agent_definitions")
    op.drop_index("ix_agent_definitions_project", table_name="agent_definitions")
    op.drop_table("agent_definitions")

    op.drop_index("ix_agent_templates_specialization", table_name="agent_templates")
    op.drop_table("agent_templates")
