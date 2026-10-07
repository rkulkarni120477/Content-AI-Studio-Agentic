"""
Agent Builder Database Models

Persistent storage for agent definitions, configurations, runs, steps,
checkpoints, and results. All models are tenant-scoped except platform templates.

Patterns:
- Platform templates (AgentTemplate) are global, explicitly shared to tenants
- Tenant agents (AgentDefinition) are project_id scoped
- Runs and checkpoints use SQLite-compatible atomic mechanisms
- All timestamps use datetime.utcnow for consistency
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Boolean, Float, ForeignKey,
    JSON, UniqueConstraint, Index
)
from sqlalchemy.orm import relationship

from promptops_app.database import Base


# =============================================================================
# Platform Agent Templates (Global)
# =============================================================================

class AgentTemplate(Base):
    """
    Platform-level agent template definition. Immutable registry of available
    agent types (Content Creator, Standards Aligner, Editorial Reviewer, etc).

    Templates are authored by platform administrators and explicitly shared
    to tenant organizations. Each tenant can then create its own instances
    from a template.
    """
    __tablename__ = "agent_templates"
    __table_args__ = (
        Index("ix_agent_templates_specialization", "specialization"),
    )

    id = Column(Integer, primary_key=True)

    # Identity
    key = Column(String(50), unique=True, nullable=False)  # content_creator, standards_aligner
    name = Column(String(255), nullable=False)
    description = Column(Text)
    specialization = Column(String(50), nullable=False)  # content_creation, standards_alignment, editorial_review

    # Definition
    instructions = Column(Text, nullable=False)  # System prompt / behavior guide
    input_schema = Column(Text, nullable=False)  # JSON schema for inputs
    output_schema = Column(Text, nullable=False)  # JSON schema for outputs

    # Metadata
    owner = Column(String(100), nullable=False)  # Platform admin identifier
    version = Column(Integer, default=1)  # Template version
    is_active = Column(Boolean, default=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    definitions = relationship("AgentDefinition", back_populates="template", cascade="all, delete-orphan")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


# =============================================================================
# Tenant Agent Definitions & Versions
# =============================================================================

class AgentDefinition(Base):
    """
    Tenant-scoped agent instance. A configuration based on a template,
    customized for the tenant's context (knowledge bindings, model choice, etc).

    Lifecycle: draft → active → paused → archived
    Changes to an active agent create a new version; existing runs reference
    their original immutable version.
    """
    __tablename__ = "agent_definitions"
    __table_args__ = (
        UniqueConstraint("project_id", "call_handle", name="uq_agent_definition_project_call_handle"),
        Index("ix_agent_definitions_project", "project_id"),
        Index("ix_agent_definitions_lifecycle_state", "lifecycle_state"),
    )

    id = Column(Integer, primary_key=True)

    # Tenant scoping
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)

    # Template reference
    template_id = Column(Integer, ForeignKey("agent_templates.id", ondelete="RESTRICT"), nullable=False)

    # Identity
    name = Column(String(255), nullable=False)  # Display name
    call_handle = Column(String(50), nullable=False)  # Internal identifier, unique per tenant
    description = Column(Text)

    # Ownership & permissions
    owner = Column(String(100), nullable=False)  # Username of creator

    # Configuration (active version's settings)
    configuration = Column(Text, nullable=False)  # JSON: model, tools, knowledge_bindings, etc.

    # Lifecycle state
    lifecycle_state = Column(String(20), nullable=False, default="draft")  # draft|active|paused|archived
    lifecycle_reason = Column(Text)  # Why paused or archived

    # Current version tracking (denormalized for performance)
    current_version_number = Column(Integer, default=1)
    activated_version_number = Column(Integer, nullable=True)  # Which version is active
    activated_at = Column(DateTime, nullable=True)
    activated_by = Column(String(100), nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(String(100), nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = Column(String(100), nullable=True)

    # Relationships
    template = relationship("AgentTemplate", back_populates="definitions")
    versions = relationship("AgentDefinitionVersion", back_populates="definition", cascade="all, delete-orphan")
    knowledge_bindings = relationship("KnowledgeBinding", back_populates="definition", cascade="all, delete-orphan")
    allowed_handoffs = relationship("AllowedHandoff", foreign_keys="AllowedHandoff.caller_id", back_populates="caller", cascade="all, delete-orphan")
    runs = relationship("AgentRun", back_populates="definition", cascade="all, delete-orphan")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class AgentDefinitionVersion(Base):
    """
    Immutable snapshot of an agent configuration at a point in time.

    When an active agent is modified, a new version is created rather than
    updating the existing one. All runs reference the version they were
    executed with, ensuring reproducibility.
    """
    __tablename__ = "agent_definition_versions"
    __table_args__ = (
        UniqueConstraint("definition_id", "version_number", name="uq_agent_version_definition_number"),
        Index("ix_agent_versions_definition", "definition_id"),
    )

    id = Column(Integer, primary_key=True)

    # Reference to parent definition
    definition_id = Column(Integer, ForeignKey("agent_definitions.id", ondelete="CASCADE"), nullable=False)

    # Version tracking
    version_number = Column(Integer, nullable=False)

    # Immutable configuration snapshot
    configuration = Column(Text, nullable=False)  # JSON copy of agent_definitions.configuration

    # Activation history
    is_active = Column(Boolean, default=False)  # Only one version is active at a time
    activated_at = Column(DateTime, nullable=True)
    activated_by = Column(String(100), nullable=True)

    # Change tracking
    change_summary = Column(Text)  # Why this version was created

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(String(100), nullable=False)

    # Relationships
    definition = relationship("AgentDefinition", back_populates="versions")
    runs = relationship("AgentRun", back_populates="version")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


# =============================================================================
# Knowledge & Tool Bindings
# =============================================================================

class KnowledgeBinding(Base):
    """
    Links an agent to permitted knowledge sources (documents, collections,
    styles, blueprints, standards, etc).

    Models both direct bindings and "knowledge-only" modes where the agent
    can only answer from provided sources.
    """
    __tablename__ = "knowledge_bindings"
    __table_args__ = (
        Index("ix_knowledge_bindings_definition", "definition_id"),
        Index("ix_knowledge_bindings_source", "source_id", "source_type"),
    )

    id = Column(Integer, primary_key=True)

    # Reference to agent
    definition_id = Column(Integer, ForeignKey("agent_definitions.id", ondelete="CASCADE"), nullable=False)

    # Source identification
    source_id = Column(String(100), nullable=False)  # Stable ID (not filename)
    source_type = Column(String(50), nullable=False)  # document|collection|style|blueprint|standard|taxonomy
    source_name = Column(String(255), nullable=True)  # Display name

    # Knowledge-only flag: if True, agent must restrict retrieval to this binding
    knowledge_only = Column(Boolean, default=False)

    # Optional: for course/module context, linked resources
    course_id = Column(Integer, nullable=True, index=True)
    module_id = Column(Integer, nullable=True, index=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(String(100), nullable=False)

    # Relationships
    definition = relationship("AgentDefinition", back_populates="knowledge_bindings")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class AllowedHandoff(Base):
    """
    Explicitly authorized caller→callee handoff for multi-agent workflows.

    If set, restricts an agent's ability to call other agents to an
    allowlist. If empty, self-handoffs are not permitted.
    """
    __tablename__ = "allowed_handoffs"
    __table_args__ = (
        UniqueConstraint("caller_id", "callee_id", name="uq_handoff_caller_callee"),
        Index("ix_handoff_caller", "caller_id"),
        Index("ix_handoff_callee", "callee_id"),
    )

    id = Column(Integer, primary_key=True)

    # Authorized handoff direction
    caller_id = Column(Integer, ForeignKey("agent_definitions.id", ondelete="CASCADE"), nullable=False)
    callee_id = Column(Integer, ForeignKey("agent_definitions.id", ondelete="CASCADE"), nullable=False)

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(String(100), nullable=False)

    # Relationships
    caller = relationship("AgentDefinition", foreign_keys="AllowedHandoff.caller_id", back_populates="allowed_handoffs")
    callee = relationship("AgentDefinition", foreign_keys="AllowedHandoff.callee_id")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


# =============================================================================
# Agent Execution: Runs & Steps
# =============================================================================

class AgentRun(Base):
    """
    One execution of an agent or workflow. Tracks state, input, output,
    cost, duration, and authorization context.
    """
    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("ix_agent_runs_project", "project_id"),
        Index("ix_agent_runs_initiated_by", "initiated_by"),
        Index("ix_agent_runs_state", "state"),
        Index("ix_agent_runs_created_at", "created_at"),
    )

    id = Column(Integer, primary_key=True)

    # Context
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    definition_id = Column(Integer, ForeignKey("agent_definitions.id", ondelete="RESTRICT"), nullable=False)
    version_id = Column(Integer, ForeignKey("agent_definition_versions.id", ondelete="RESTRICT"), nullable=False)

    # Initiator & authorization
    initiated_by = Column(String(100), nullable=False)  # Username
    initiated_by_role = Column(String(20), nullable=False)  # author|reviewer|admin (immutable)

    # Target artifact
    artifact_id = Column(Integer, nullable=True)  # If applicable
    artifact_type = Column(String(50), nullable=True)  # block|course|module
    artifact_version = Column(Integer, nullable=True)  # Version snapshot at execution time

    # Input
    input_content = Column(Text, nullable=True)  # The content being processed
    input_context = Column(Text, nullable=True)  # JSON additional context (course, style, etc)

    # Execution state
    state = Column(String(20), nullable=False, default="queued")  # queued|running|awaiting_input|completed|failed|cancelled
    state_reason = Column(Text, nullable=True)  # Error message or reason for state

    # Output & results
    result = Column(Text, nullable=True)  # JSON final result
    error_message = Column(Text, nullable=True)  # If state == failed
    error_traceback = Column(Text, nullable=True)  # Stack trace (if applicable)

    # Execution metrics
    step_count = Column(Integer, default=0)  # Number of steps executed
    max_steps = Column(Integer, default=10)  # Maximum steps before timeout
    handoff_depth = Column(Integer, default=0)  # Current depth in handoff chain
    max_handoff_depth = Column(Integer, default=2)  # Maximum handoff nesting

    # Timing
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)  # For awaiting_input state timeout
    execution_time_ms = Column(Integer, nullable=True)

    # Cost tracking
    estimated_cost = Column(Float, nullable=True)
    actual_cost = Column(Float, nullable=True)
    budget_reserved = Column(Boolean, default=False)

    # Idempotency
    request_id = Column(String(64), nullable=True, index=True, unique=True)  # Prevent duplicate submissions

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    definition = relationship("AgentDefinition", back_populates="runs")
    version = relationship("AgentDefinitionVersion", back_populates="runs")
    steps = relationship("AgentRunStep", back_populates="run", cascade="all, delete-orphan")
    checkpoints = relationship("AgentCheckpoint", back_populates="run", cascade="all, delete-orphan")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class AgentRunStep(Base):
    """
    One atomic step within a run (model call, retrieval, validation, etc).

    Steps are executed sequentially. Each step produces output that feeds
    into the next step's input.
    """
    __tablename__ = "agent_run_steps"
    __table_args__ = (
        Index("ix_agent_run_steps_run", "run_id"),
        Index("ix_agent_run_steps_status", "status"),
    )

    id = Column(Integer, primary_key=True)

    # Reference
    run_id = Column(Integer, ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False)

    # Step sequencing
    step_index = Column(Integer, nullable=False)  # 0-based position in run

    # Step type
    step_type = Column(String(50), nullable=False)  # model_call|retrieval|validation|handoff

    # Status tracking
    status = Column(String(20), nullable=False, default="pending")  # pending|running|completed|failed|retrying
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)

    # Input & output
    input_data = Column(Text, nullable=True)  # JSON input for this step
    output_data = Column(Text, nullable=True)  # JSON output from this step
    error_message = Column(Text, nullable=True)

    # Model execution
    model_used = Column(String(100), nullable=True)  # Provider:model-id
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)

    # Execution metrics
    execution_time_ms = Column(Integer, nullable=True)
    step_cost = Column(Float, nullable=True)

    # Timestamps
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    run = relationship("AgentRun", back_populates="steps")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


# =============================================================================
# Persistence & Resumption
# =============================================================================

class AgentCheckpoint(Base):
    """
    Durable checkpoint for resumable execution. Stores LangGraph-compatible
    state at each step boundary.

    Used for crash recovery and pause/resume workflows.
    """
    __tablename__ = "agent_checkpoints"
    __table_args__ = (
        Index("ix_checkpoints_run", "run_id"),
        Index("ix_checkpoints_resumable", "run_id", "resumable"),
    )

    id = Column(Integer, primary_key=True)

    # Reference
    run_id = Column(Integer, ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False)

    # Checkpoint sequencing
    checkpoint_index = Column(Integer, nullable=False)

    # Serialized state
    step_state = Column(Text, nullable=False)  # JSON: LangGraph-compatible checkpoint

    # Resumability
    resumable = Column(Boolean, default=True)  # Can execution continue from here?
    resume_reason = Column(Text, nullable=True)  # awaiting_input|paused|error

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    run = relationship("AgentRun", back_populates="checkpoints")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


# =============================================================================
# Results & Proposed Changes
# =============================================================================

class ProposedChange(Base):
    """
    AI-suggested modification to an artifact. Can be previewed and applied
    by an authorized user.

    Tracks all changes in a version-safe way, requiring explicit user
    approval before application.
    """
    __tablename__ = "proposed_changes"
    __table_args__ = (
        Index("ix_proposed_changes_run", "run_id"),
        Index("ix_proposed_changes_artifact", "artifact_id"),
        Index("ix_proposed_changes_status", "status"),
    )

    id = Column(Integer, primary_key=True)

    # Reference to source run
    run_id = Column(Integer, ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False)

    # Target artifact
    artifact_id = Column(Integer, nullable=False)  # ID in blocks/courses/modules
    artifact_type = Column(String(50), nullable=False)  # block|course|module
    artifact_version = Column(Integer, nullable=False)  # Version being modified

    # Change details
    change_type = Column(String(50), nullable=False)  # content|metadata|tags|structure
    before = Column(Text, nullable=True)  # Original content
    after = Column(Text, nullable=True)  # Proposed content

    # AI reasoning
    explanation = Column(Text, nullable=True)  # Why this change was suggested
    confidence = Column(Float, nullable=True)  # 0-1 confidence score
    evidence = Column(Text, nullable=True)  # JSON: source citations, reasoning

    # Application tracking
    status = Column(String(20), nullable=False, default="proposed")  # proposed|applied|dismissed|stale
    applied_at = Column(DateTime, nullable=True)
    applied_by = Column(String(100), nullable=True)
    dismissed_at = Column(DateTime, nullable=True)
    dismissed_by = Column(String(100), nullable=True)

    # Artifact version safety
    artifact_version_at_application = Column(Integer, nullable=True)  # Version when applied
    artifact_changed_after_proposal = Column(Boolean, default=False)  # If target version changed

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    run = relationship("AgentRun", foreign_keys=[run_id])

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


# =============================================================================
# Multi-Agent Workflows
# =============================================================================

class AgentWorkflow(Base):
    """
    Named, reusable multi-agent workflow. Defines a DAG of agents,
    their execution order, and data flow.
    """
    __tablename__ = "agent_workflows"
    __table_args__ = (
        UniqueConstraint("project_id", "call_handle", name="uq_workflow_project_handle"),
        Index("ix_workflows_project", "project_id"),
    )

    id = Column(Integer, primary_key=True)

    # Tenant scoping
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)

    # Identity
    name = Column(String(255), nullable=False)
    call_handle = Column(String(50), nullable=False)  # Code-facing identifier
    description = Column(Text)

    # Definition
    definition = Column(Text, nullable=False)  # JSON DAG structure

    # Metadata
    is_active = Column(Boolean, default=True)
    owner = Column(String(100), nullable=False)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(String(100), nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    runs = relationship("WorkflowRun", back_populates="workflow", cascade="all, delete-orphan")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class WorkflowRun(Base):
    """
    Execution instance of a multi-agent workflow.
    """
    __tablename__ = "workflow_runs"
    __table_args__ = (
        Index("ix_workflow_runs_project", "project_id"),
        Index("ix_workflow_runs_workflow", "workflow_id"),
    )

    id = Column(Integer, primary_key=True)

    # References
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    workflow_id = Column(Integer, ForeignKey("agent_workflows.id", ondelete="RESTRICT"), nullable=False)

    # Execution context
    initiated_by = Column(String(100), nullable=False)
    input_context = Column(Text, nullable=True)  # JSON initial context

    # State
    state = Column(String(20), nullable=False, default="queued")  # queued|running|completed|failed|paused
    result = Column(Text, nullable=True)  # Final merged result

    # Metrics
    total_cost = Column(Float, nullable=True)
    execution_time_ms = Column(Integer, nullable=True)

    # Idempotency
    request_id = Column(String(64), nullable=True, index=True, unique=True)  # Prevent duplicate submissions

    # Timestamps
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    workflow = relationship("AgentWorkflow", back_populates="runs")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
