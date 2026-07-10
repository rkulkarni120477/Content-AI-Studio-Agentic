# =============================================================================
# database.py — PostgreSQL / SQLAlchemy layer for PromptOps
# Models, engine, session, migrations, and DB-only helper functions.
# =============================================================================

import os
from dotenv import load_dotenv
import json
import re
import hashlib
import binascii
from datetime import datetime, timezone
from typing import Optional, List, Any

from sqlalchemy import (
    create_engine, Column, Integer, String, Text, DateTime,
    Boolean, Float, ForeignKey, JSON, text,
    BigInteger, SmallInteger, UniqueConstraint,
)
from sqlalchemy.orm import sessionmaker, declarative_base, relationship

from promptops_app.prompt_templates import (
    DEFAULT_STYLE_GUIDE,
    PERSONA_PREFIX_TEMPLATE,
    SEED_PROMPT_V1_SYSTEM,
    SEED_PROMPT_V1_USER,
    SEED_PROMPT_V2_SYSTEM,
    SEED_PROMPT_V2_USER,
    # Default component prompts — seeded as DB assets on first startup
    CDD_SYSTEM_PROMPT,
    CDD_USER_PROMPT_TEMPLATE,
    BLUEPRINT_SYSTEM_PROMPT,
    BLUEPRINT_USER_PROMPT_TEMPLATE,
    TEACHER_BLUEPRINT_SYSTEM_PROMPT,
    TEACHER_BLUEPRINT_USER_PROMPT_TEMPLATE,
    LESSON_WITH_CONTEXT_SYSTEM,
    LESSON_WITH_CONTEXT_USER,
)

# =============================================================================
# Configuration & Settings
# =============================================================================

# load_dotenv() is kept for any remaining os.getenv() calls deeper in this file.
# New code should use `from promptops_app.core.config import settings` instead.
load_dotenv()

from promptops_app.core.config import settings as _app_settings  # noqa: E402


class _DBSettings:
    """Backward-compatible shim — preserves the `settings.*` attribute interface
    used throughout the codebase while delegating all values to AppSettings.

    Do NOT add new reads here. Use `from promptops_app.core.config import settings`
    in new code instead.
    """
    app_name: str           = "Content AI Studio"
    db_url: str             = _app_settings.database_url
    jwt_secret_key: str     = _app_settings.jwt_secret_value
    jwt_algorithm: str      = "HS256"
    jwt_expire_minutes: int = 1440
    openai_api_key          = _app_settings.openai_api_key_value   # Optional[str]
    openai_model: str       = _app_settings.openai_model
    aws_access_key: str     = _app_settings.aws_access_key_value
    aws_secret_key: str     = _app_settings.aws_secret_key_value
    aws_region: str         = _app_settings.aws_region
    bedrock_model_id: str   = _app_settings.bedrock_model_id
    approval_sla_hours: int = 24


settings = _DBSettings()

# Public alias — `from database import Settings` continues to work for any
# existing code that references the class by name (e.g. core/shared.py).
Settings = _DBSettings

# =============================================================================
# Database Engine & Session Factory
# =============================================================================

# Pool sizing args only apply to real server databases; SQLite (used by the
# test suite) rejects them (its SingletonThreadPool takes no size arguments).
_engine_kwargs: dict = {"pool_pre_ping": True, "future": True}
if not settings.db_url.startswith("sqlite"):
    _engine_kwargs.update(pool_size=10, max_overflow=20)

engine = create_engine(settings.db_url, **_engine_kwargs)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

# =============================================================================
# Database Models
# =============================================================================

Base = declarative_base()

class User(Base):
    """
    User account — DB roles: admin, author, reviewer.
    'admin'    → full system access (Director)
    'author'   → displayed as "ID" — instructional design access
    'reviewer' → displayed as "Lead" — lead access, admin-like within assigned project scope
    """
    __tablename__ = "users"
    id            = Column(Integer, primary_key=True)
    username      = Column(String, unique=True)
    password_hash = Column(String)           # nullable — Microsoft-only users have none
    role          = Column(String)           # legacy/default role; effective role is per-tenant (TenantMembership)
    permissions   = Column(Text)             # JSON list — future-ready granular perms
    is_active     = Column(Boolean, default=True)
    created_at    = Column(DateTime, default=datetime.utcnow)
    # ── Tenant identity (project-as-tenant + Microsoft auth) ──────────────────
    project_id        = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)  # last-active tenant
    is_platform_admin = Column(Boolean, default=False, nullable=False)
    microsoft_oid     = Column(String(64), nullable=True, index=True)
    email             = Column(String(255), nullable=True, index=True)
    display_name      = Column(String(200), nullable=True)
    memberships       = relationship("TenantMembership", back_populates="user", cascade="all, delete-orphan")
    def __init__(self, **kwargs): super().__init__(**kwargs)

class Document(Base):
    __tablename__ = "documents"
    id = Column(Integer, primary_key=True)
    filename = Column(String(255), nullable=False)
    file_type = Column(String(120))
    doc_tag = Column(String(50), default="general")
    content = Column(Text, nullable=False)
    uploaded_by = Column(String(100))
    uploaded_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String(20), default="active")  # active, archived
    def __init__(self, **kwargs): super().__init__(**kwargs)

class Style(Base):
    """
    Instructional Style — defines tone, writing rules, and structural guidelines
    that propagate across CDD → Blueprint → Generate pipeline.
    """
    __tablename__ = "styles"
    id                  = Column(Integer, primary_key=True)
    style_id            = Column(String(120), unique=True, nullable=False)     # slug name
    name                = Column(String(255), nullable=False)
    description         = Column(Text)
    custom_instructions = Column(Text)                                          # user-authored rules
    generated_summary   = Column(Text)                                          # LLM understanding output (mirror of active StyleVersion)
    understanding_status = Column(String(20), default="fresh")                  # "fresh" | "stale"
    is_active           = Column(Boolean, default=False)
    created_by          = Column(String(100))
    created_at          = Column(DateTime, default=datetime.utcnow)
    updated_at          = Column(DateTime, default=datetime.utcnow)
    # Many-to-many with Document via StyleDocument join table
    style_documents     = relationship("StyleDocument", back_populates="style",
                                       cascade="all, delete-orphan")
    # Version history for the Style Intelligence Layer
    understanding_versions = relationship("StyleVersion", back_populates="style",
                                          cascade="all, delete-orphan",
                                          order_by="StyleVersion.version_number")
    def __init__(self, **kwargs): super().__init__(**kwargs)


class StyleVersion(Base):
    """Version snapshot of a Style Intelligence Layer (generated understanding).

    One active version per style at any time.  On every generation or restoration
    a new row is inserted and the previous active row is deactivated — the
    generated_summary on the parent Style is kept in sync for backward compat.
    """
    __tablename__ = "style_versions"

    id                    = Column(Integer,     primary_key=True)
    style_id              = Column(Integer,     ForeignKey("styles.id"), nullable=False)
    version_number        = Column(Integer,     nullable=False, default=1)
    is_active             = Column(Boolean,     default=False)
    understanding_content = Column(Text)        # the generated_summary at this version
    change_summary        = Column(String(500)) # why this version was created / restored
    created_by            = Column(String(100))
    created_at            = Column(DateTime,    default=datetime.utcnow)

    style = relationship("Style", back_populates="understanding_versions")

    def __init__(self, **kwargs): super().__init__(**kwargs)


class StyleDocument(Base):
    """Join table — links a Style to one or more Documents."""
    __tablename__ = "style_documents"
    id          = Column(Integer, primary_key=True)
    style_id    = Column(Integer, ForeignKey("styles.id"), nullable=False)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False)
    style       = relationship("Style", back_populates="style_documents")
    document    = relationship("Document")
    def __init__(self, **kwargs): super().__init__(**kwargs)


class Prompt(Base):
    """A versioned prompt asset belonging to one pipeline component.

    component_type : "style" | "cdd" | "blueprint" | "generate"
    is_default     : True only for the one system-seeded canonical prompt per component.
                     Edits to a default always produce a new version — the original v1
                     is never overwritten.
    """
    __tablename__ = "prompts"
    id             = Column(Integer, primary_key=True)
    name           = Column(String, unique=True)
    description    = Column(Text)
    owner          = Column(String)
    active_version = Column(String)
    tags           = Column(String)             # comma-separated (legacy, pipeline rows only)
    component_type = Column(String(50), nullable=True)   # style|cdd|blueprint|generate
    is_default     = Column(Boolean, default=False)      # True = system seed
    # -- unified prompt model (prompt-consolidation Phase 1/3) --------------
    prompt_kind    = Column(String(20), nullable=False, default="pipeline",
                            server_default="pipeline")   # pipeline|library
    title          = Column(String(300), nullable=True)  # library display title
    category       = Column(String(100), nullable=True)  # library category (freeform)
    visibility     = Column(String(20), nullable=False, default="draft",
                            server_default="draft")      # global|team|draft (library only)
    variant        = Column(String(50), nullable=True)   # pipeline: student|teacher|lesson|assessment|interactive
    parent_id      = Column(Integer, ForeignKey("prompts.id", ondelete="CASCADE"),
                            nullable=True, index=True)   # library follow-up hierarchy
    last_used_at   = Column(DateTime, nullable=True)
    deleted_at     = Column(DateTime, nullable=True, index=True)  # soft delete (library flows)
    created_at     = Column(DateTime, default=datetime.utcnow)
    updated_at     = Column(DateTime, default=datetime.utcnow)
    versions = relationship("PromptVersion", back_populates="prompt", cascade="all, delete-orphan")
    # -- library-feature relationships (consolidation Phase 4) ---------------
    # `tags` is taken by the legacy comma-string column, hence `tag_rows`.
    parent = relationship("Prompt", remote_side="Prompt.id", foreign_keys=[parent_id],
                          back_populates="children", lazy="select")
    children = relationship("Prompt", back_populates="parent", foreign_keys=[parent_id],
                            lazy="selectin")
    tag_rows = relationship("PromptTag", cascade="all, delete-orphan", lazy="selectin")
    variables = relationship("PromptVariable", cascade="all, delete-orphan",
                             order_by="PromptVariable.sort_order", lazy="selectin")
    attachments = relationship("PromptAttachment", cascade="all, delete-orphan",
                               lazy="selectin")
    team_links = relationship("PromptTeamLink", cascade="all, delete-orphan",
                              lazy="selectin")
    def __init__(self, **kwargs): super().__init__(**kwargs)

class PromptVersion(Base):
    """Single version snapshot of a Prompt asset.

    Versions are append-only; the active flag moves forward, never back.
    created_by tracks which user committed this version.
    """
    __tablename__ = "prompt_versions"
    __table_args__ = (
        UniqueConstraint("prompt_id", "version_number",
                         name="uq_prompt_versions_prompt_id_version_number"),
    )
    id                   = Column(Integer, primary_key=True)
    prompt_id            = Column(Integer, ForeignKey("prompts.id"))
    version              = Column(String)
    # Numeric ordering key (consolidation Phase 3; NOT NULL since revision
    # 000100000004 — every write path populates it).
    version_number       = Column(Integer, nullable=False)
    # draft|in_review|approved|active — gate enforced for pipeline rows only
    # (Phase 8); library rows are instant-publish 'active'.
    workflow_state       = Column(String(20), nullable=False, default="active",
                                  server_default="active")
    system_prompt        = Column(Text)
    user_prompt_template = Column(Text)
    change_reason        = Column(Text)
    is_active            = Column(Boolean, default=False)
    created_by           = Column(String(100), nullable=True)
    created_at           = Column(DateTime, default=datetime.utcnow)
    prompt = relationship("Prompt", back_populates="versions")
    def __init__(self, **kwargs): super().__init__(**kwargs)


class PromptFixing(Base):
    """Records an admin/lead decision to lock a prompt template to a scope.

    Scope resolution priority (most specific wins):
        course → cluster → project → global

    Uniqueness per (component, scope_level, project_id, cluster_id, course_id) is
    enforced at the application layer (upsert in prompt_repository) because
    PostgreSQL's unique constraints treat two NULL values as distinct.
    """
    __tablename__ = "prompt_fixings"

    id            = Column(Integer,     primary_key=True, autoincrement=True)
    component     = Column(String(50),  nullable=False, index=True)   # style|cdd|blueprint|generate
    scope_level   = Column(String(20),  nullable=False)               # global|project|cluster|course
    project_id    = Column(Integer,     nullable=True,  index=True)
    cluster_id    = Column(Integer,     nullable=True,  index=True)
    course_id     = Column(Integer,     nullable=True,  index=True)
    prompt_id     = Column(Integer,     ForeignKey("prompts.id", ondelete="SET NULL"), nullable=True)
    fixed_by      = Column(String(100), nullable=False)
    fixed_by_role = Column(String(20),  nullable=False)               # admin|reviewer
    fixed_at      = Column(DateTime,    default=datetime.utcnow)

    prompt = relationship("Prompt", foreign_keys=[prompt_id])
    def __init__(self, **kwargs): super().__init__(**kwargs)


class UserPromptPreference(Base):
    """Persists a user's last-chosen prompt per component + course.

    Implements Priority 4 in the prompt selection hierarchy:
      (1) Course-fixed  (2) Cluster-fixed  (3) Project-fixed
      (4) User's last selected  ← this model
      (5) Component default

    Scoped to course_id because users typically work inside one course at a time.
    project_id is stored for context / bulk cleanup only and is not used for lookup.
    """
    __tablename__ = "user_prompt_preferences"

    id         = Column(Integer,     primary_key=True, autoincrement=True)
    user_name  = Column(String(100), nullable=False, index=True)
    component  = Column(String(50),  nullable=False)          # style|cdd|blueprint|generate
    course_id  = Column(Integer,     nullable=True,  index=True)
    project_id = Column(Integer,     nullable=True)           # for context / cleanup only
    prompt_id  = Column(Integer,     ForeignKey("prompts.id", ondelete="SET NULL"), nullable=True)
    updated_at = Column(DateTime,    default=datetime.utcnow)

    prompt = relationship("Prompt", foreign_keys=[prompt_id])
    def __init__(self, **kwargs): super().__init__(**kwargs)


# ---------------------------------------------------------------------------
# Prompt-consolidation Phase 1/3 — native replacements for the pl_* tables
# (see PROMPT_CONSOLIDATION_PLAN.md). Integer PKs, no tenant column. These
# back the library features (tags, variables, attachments, teams, reviews,
# requests) once Phase 4 cuts the Prompt Library service over from pl_*.
# ---------------------------------------------------------------------------

class PromptTag(Base):
    """Tag on a prompt (library rows). Replaces pl_prompt_tags."""
    __tablename__ = "prompt_tags"

    prompt_id = Column(Integer, ForeignKey("prompts.id", ondelete="CASCADE"),
                       primary_key=True)
    tag       = Column(String(100), primary_key=True)
    def __init__(self, **kwargs): super().__init__(**kwargs)


class PromptVariable(Base):
    """Declared template variable ({{name}}). Replaces pl_prompt_variables.

    Also the future DB-backed home for pipeline template variable
    declarations (replacing prompt_loader's static _REGISTRY — Phase 8).
    """
    __tablename__ = "prompt_variables"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    prompt_id  = Column(Integer, ForeignKey("prompts.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    name       = Column(String(100), nullable=False)
    label      = Column(String(200))
    hint       = Column(Text)
    sort_order = Column(Integer, default=0, nullable=False, server_default="0")
    def __init__(self, **kwargs): super().__init__(**kwargs)


class PromptAttachment(Base):
    """File attached to a prompt. Replaces pl_attachments.

    Files live under PROMPT_ATTACHMENTS_DIR (default ./prompt_attachments).
    """
    __tablename__ = "prompt_attachments"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    prompt_id     = Column(Integer, ForeignKey("prompts.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    original_name = Column(String(300))
    stored_name   = Column(String(500))
    size_bytes    = Column(BigInteger)
    uploaded_by   = Column(String(100))
    uploaded_at   = Column(DateTime, default=datetime.utcnow)
    def __init__(self, **kwargs): super().__init__(**kwargs)


class PromptFragment(Base):
    """Shared prompt fragment (consolidation Phase 9).

    fragment_key : stable code-facing key — the seeded set is
                   persona_tone | style_guide; the reserved keys guardrails |
                   context_header | output_contract_json |
                   output_contract_markdown | regenerate_wrapper are authored
                   through the admin API when the content is ready.
    Fragments are {{double}}-brace text rendered by prompt_builder.render;
    the legacy .format constants remain the code fallback tier when no row
    (or no active version) exists.
    """
    __tablename__ = "prompt_fragments"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    fragment_key   = Column(String(50), unique=True, nullable=False)
    description    = Column(Text)
    active_version = Column(String(50))
    created_by     = Column(String(100))
    created_at     = Column(DateTime, default=datetime.utcnow)
    updated_at     = Column(DateTime, default=datetime.utcnow)
    versions = relationship("PromptFragmentVersion", cascade="all, delete-orphan",
                            back_populates="fragment", lazy="selectin")
    def __init__(self, **kwargs): super().__init__(**kwargs)


class PromptFragmentVersion(Base):
    """Append-only version snapshot of a PromptFragment."""
    __tablename__ = "prompt_fragment_versions"
    __table_args__ = (
        UniqueConstraint("fragment_id", "version_number",
                         name="uq_prompt_fragment_versions_fragment_id_version_number"),
    )

    id             = Column(Integer, primary_key=True, autoincrement=True)
    fragment_id    = Column(Integer, ForeignKey("prompt_fragments.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    version        = Column(String(50), nullable=False)
    version_number = Column(Integer, nullable=False)
    content        = Column(Text, nullable=False)
    change_reason  = Column(Text)
    is_active      = Column(Boolean, default=False)
    created_by     = Column(String(100))
    created_at     = Column(DateTime, default=datetime.utcnow)
    fragment = relationship("PromptFragment", back_populates="versions")
    def __init__(self, **kwargs): super().__init__(**kwargs)


class Team(Base):
    """Prompt-sharing team. Replaces pl_teams (String(20) slug PK → Integer;
    the slug is preserved in name — verified id == name for all prod rows)."""
    __tablename__ = "teams"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    name       = Column(String(100), unique=True, nullable=False)
    created_by = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    def __init__(self, **kwargs): super().__init__(**kwargs)


class PromptTeamLink(Base):
    """M:N prompt↔team visibility link. Replaces pl_prompt_teams (and the
    redundant scalar pl_prompts.team_id, which was unused in prod)."""
    __tablename__ = "prompt_team_links"

    prompt_id = Column(Integer, ForeignKey("prompts.id", ondelete="CASCADE"),
                       primary_key=True)
    team_id   = Column(Integer, ForeignKey("teams.id", ondelete="CASCADE"),
                       primary_key=True)
    def __init__(self, **kwargs): super().__init__(**kwargs)


class PromptReview(Base):
    """User rating/feedback on a prompt. Replaces pl_reviews.

    Named prompt_reviews — a `reviews` table already exists for
    content-block review.
    """
    __tablename__ = "prompt_reviews"
    __table_args__ = (
        UniqueConstraint("prompt_id", "username",
                         name="uq_prompt_reviews_prompt_id_username"),
    )

    id         = Column(Integer, primary_key=True, autoincrement=True)
    prompt_id  = Column(Integer, ForeignKey("prompts.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    username   = Column(String(100), nullable=False)
    rating     = Column(SmallInteger, nullable=False)
    feedback   = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    def __init__(self, **kwargs): super().__init__(**kwargs)


class PromptRequest(Base):
    """User request for a new/changed prompt. Replaces pl_prompt_requests."""
    __tablename__ = "prompt_requests"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    title        = Column(String(300), nullable=False)
    description  = Column(Text)
    type         = Column(String(20), nullable=False, default="new",
                          server_default="new")     # new|change
    prompt_id    = Column(Integer, ForeignKey("prompts.id", ondelete="SET NULL"),
                          nullable=True)
    requested_by = Column(String(100), nullable=False)
    status       = Column(String(20), nullable=False, default="open",
                          server_default="open", index=True)
    admin_notes  = Column(Text)
    created_at   = Column(DateTime, default=datetime.utcnow)
    updated_at   = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    def __init__(self, **kwargs): super().__init__(**kwargs)


class PlagiarismReport(Base):
    """One row per plagiarism scan submitted to Copyleaks.

    Lifecycle: pending → processing → completed | failed
    The block's legacy ``plagiarism_score`` / ``plagiarism_report`` columns are
    updated from ``similarity_score`` / ``ai_score`` when a scan completes, so
    the existing dashboard keeps working without changes.
    """
    __tablename__ = "plagiarism_reports"

    id              = Column(Integer,  primary_key=True, autoincrement=True)
    block_id        = Column(Integer,  ForeignKey("blocks.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    project_id      = Column(Integer,  nullable=True,  index=True)
    course_id       = Column(Integer,  nullable=True,  index=True)

    # Copyleaks tracking
    scan_id         = Column(String(64),  nullable=True, unique=True)  # UUID hex
    celery_task_id  = Column(String(155), nullable=True)

    # Status: pending | processing | completed | failed
    status          = Column(String(20),  nullable=False, default="pending", index=True)

    # Results (populated when status == "completed")
    similarity_score = Column(Float, nullable=True)    # 0-100, content similarity
    ai_score         = Column(Float, nullable=True)    # 0-100, AI-generation probability
    source_urls      = Column(JSON,  nullable=True)    # list[{url, similarity, title}]
    highlights       = Column(JSON,  nullable=True)    # list[{text, similarity, source_url}]
    raw_response     = Column(Text,  nullable=True)    # full Copyleaks JSON for debugging

    # Error info
    error_message    = Column(Text, nullable=True)

    # Timestamps
    created_at       = Column(DateTime, default=datetime.utcnow)
    updated_at       = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    submitted_at     = Column(DateTime, nullable=True)
    completed_at     = Column(DateTime, nullable=True)

    block = relationship("Block", foreign_keys=[block_id])
    def __init__(self, **kwargs): super().__init__(**kwargs)


class Generation(Base):
    __tablename__ = "generations"
    id = Column(Integer, primary_key=True)
    prompt_name = Column(String(150), nullable=False)
    prompt_version = Column(String(50), nullable=False)
    block_type = Column(String(80), nullable=False)
    topic = Column(String(255), nullable=False)
    output_text = Column(Text, nullable=False)
    # Traceability — CDD + Blueprint linkage
    cdd_id = Column(Integer, ForeignKey("course_design_documents.id"), nullable=True)
    cdd_version = Column(String(20), nullable=True)
    blueprint_id = Column(Integer, ForeignKey("module_blueprints.id"), nullable=True)
    blueprint_version = Column(String(20), nullable=True)
    project_id = Column(Integer, nullable=True)   # FK to projects.id
    course_id  = Column(Integer, nullable=True)   # FK to courses.id
    created_by = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    blocks = relationship("Block", back_populates="generation", cascade="all, delete-orphan")
    def __init__(self, **kwargs): super().__init__(**kwargs)

class CourseModule(Base):
    """A Canvas-style module grouping published blocks for IMSCC export.

    Modules are ordered by ``position`` and each published block may belong to at
    most one module (``blocks.module_id``). Unassigned blocks fall into a default
    module at export time.
    """
    __tablename__ = "course_modules"
    id         = Column(Integer, primary_key=True)
    course_id  = Column(Integer, nullable=False, index=True)   # FK to courses.id
    title      = Column(String(255), nullable=False)
    position   = Column(Integer, default=0)                    # display order within a course
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    def __init__(self, **kwargs): super().__init__(**kwargs)

class Block(Base):
    """A generated content block — the atomic unit of the approval workflow.

    Workflow states (all stored lowercase in DB):
      draft              → initial state after generation
      in_review          → submitted to a reviewer
      changes_requested  → reviewer asked for edits before approving
      approved           → reviewer approved; ready to publish
      published          → live / exported
      archived           → soft-deleted from active views
      rejected           → hard decline (kept for audit; rarely used)
    """
    __tablename__ = "blocks"
    id = Column(Integer, primary_key=True)
    generation_id = Column(Integer, ForeignKey("generations.id"))
    block_type = Column(String) # outline, lesson, quiz, assignment
    block_label = Column(String)
    content = Column(Text)
    sources = Column(Text) # JSON string of cited filenames
    plagiarism_score = Column(Integer) # 0-100 (AI content score)
    plagiarism_report = Column(Text) # Detail explanation
    position = Column(Integer, default=0)  # display order within a generation
    eval_score = Column(Integer) # Structural score
    eval_report = Column(Text) # JSON of missing sections etc.
    ai_review = Column(Text) # AI Reviewer feedback
    workflow_state = Column(String, default="draft")
    rating = Column(Integer, default=0) # 1-5 stars
    reviewer_comment = Column(Text)
    # ── Workflow tracking (Phase 2 — original) ────────────────────────────────
    version_num           = Column(Integer, default=1)
    assigned_reviewer     = Column(String(100))
    review_requested_at   = Column(DateTime)
    approved_by           = Column(String(100))
    approved_at           = Column(DateTime)
    rejected_reason       = Column(Text)
    # ── Extended workflow tracking (Phase 11) ─────────────────────────────────
    submitted_by          = Column(String(100))    # who clicked Submit for Review
    reviewed_by           = Column(String(100))    # reviewer who took the last action
    reviewed_at           = Column(DateTime)        # timestamp of last review action
    review_comments       = Column(Text)            # general reviewer comments (approve/changes/reject)
    archived_by           = Column(String(100))    # who archived the block
    archived_at           = Column(DateTime)        # when archived
    # ── Autosave draft (Phase 12) ─────────────────────────────────────────────
    draft_content         = Column(Text)            # in-progress draft; never overwrites content
    draft_saved_at        = Column(DateTime)        # timestamp of last autosave
    draft_saved_by        = Column(String(100))    # username who triggered last autosave
    # ── Canvas HTML rendition (generated at publish) ──────────────────────────
    content_html          = Column(Text)            # LMS-ready standalone HTML built from content on publish
    content_html_at       = Column(DateTime)        # when content_html was last generated
    # ── Export module grouping (Canvas-style modules) ─────────────────────────
    module_id             = Column(Integer, ForeignKey("course_modules.id"), nullable=True)
    # ─────────────────────────────────────────────────────────────────────────
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    generation = relationship("Generation", back_populates="blocks")
    comments  = relationship("BlockComment",  back_populates="block", cascade="all, delete-orphan")
    versions  = relationship("BlockVersion",  back_populates="block", cascade="all, delete-orphan")
    def __init__(self, **kwargs): super().__init__(**kwargs)

class BlockComment(Base):
    __tablename__ = "block_comments"
    id = Column(Integer, primary_key=True)
    block_id = Column(Integer, ForeignKey("blocks.id"), nullable=False)
    comment = Column(Text, nullable=False)
    author = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    block = relationship("Block", back_populates="comments")
    def __init__(self, **kwargs): super().__init__(**kwargs)

class BlockVersion(Base):
    """Content snapshot — saved on every edit, regeneration, or restore.
    Enables full version history and one-click restore for any block.
    """
    __tablename__ = "block_versions"
    id                     = Column(Integer, primary_key=True)
    block_id               = Column(Integer, ForeignKey("blocks.id"), nullable=False)
    version_num            = Column(Integer, nullable=False)
    content                = Column(Text, nullable=False)
    change_source          = Column(String(50))   # generation | edit | regeneration | restore | pre_restore_snapshot
    change_note            = Column(Text)
    workflow_state_at_save = Column(String(50))
    word_count             = Column(Integer, default=0)
    created_by             = Column(String(100))
    created_at             = Column(DateTime, default=datetime.utcnow)
    block = relationship("Block", back_populates="versions")
    def __init__(self, **kwargs): super().__init__(**kwargs)


class WorkflowEvent(Base):
    __tablename__ = "workflow_events"
    id = Column(Integer, primary_key=True)
    block_id = Column(Integer, ForeignKey("blocks.id"), nullable=False)
    from_state = Column(String(50), nullable=False)
    to_state = Column(String(50), nullable=False)
    action = Column(String(80), nullable=False)
    actor = Column(String(100))
    comment = Column(Text, default="")  # review comment / reason stored with each transition
    created_at = Column(DateTime, default=datetime.utcnow)
    def __init__(self, **kwargs): super().__init__(**kwargs)

class ABTestRun(Base):
    __tablename__ = "ab_test_runs"
    id = Column(Integer, primary_key=True)
    prompt_name = Column(String(150), nullable=False)
    variant_a = Column(String(50), nullable=False)
    variant_b = Column(String(50), nullable=False)
    topic = Column(String(255), nullable=False)
    output_a = Column(Text, nullable=False)
    output_b = Column(Text, nullable=False)
    created_by = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    def __init__(self, **kwargs): super().__init__(**kwargs)

class Review(Base):
    """Formal review/feedback record per generation — supports continuous learning loop."""
    __tablename__ = "reviews"
    id = Column(Integer, primary_key=True)
    generation_id = Column(Integer, ForeignKey("generations.id"), nullable=False)
    block_id = Column(Integer, ForeignKey("blocks.id"), nullable=True)
    reviewer = Column(String(100))
    reviewer_role = Column(String(50))  # e.g. 'QA Reviewer', 'Author', 'Admin'
    score = Column(Integer)  # 1-5
    approved = Column(Boolean, default=False)
    comments = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    def __init__(self, **kwargs): super().__init__(**kwargs)

class SystemLog(Base):
    """Observability: logs every significant system event for tracing and analytics."""
    __tablename__ = "system_logs"
    id = Column(Integer, primary_key=True)
    event_type = Column(String(80), nullable=False)  # generation, upload, review, login, export, prompt_update
    actor = Column(String(100))
    details = Column(Text)  # JSON or freeform details
    metadata_json = Column(Text)  # optional structured metadata
    created_at = Column(DateTime, default=datetime.utcnow)
    def __init__(self, **kwargs): super().__init__(**kwargs)


class AuditLog(Base):
    """Enterprise governance audit trail.

    Every significant user action is recorded here with full context so
    compliance, SOC-2, and internal investigations can trace who did what,
    when, and within which project/course.

    Notes
    -----
    * user_id stores the username string (not a FK) so records survive
      account deletion.
    * entity_id is String so it can hold both integer PKs and UUID strings
      (e.g. GenerationJob IDs).
    * ip_address is optional and only populated when the request context
      makes it available.
    """
    __tablename__ = "audit_logs"

    id            = Column(Integer,     primary_key=True)
    user_id       = Column(String(100), nullable=False, index=True)
    action        = Column(String(120), nullable=False, index=True)
    entity_type   = Column(String(60),  nullable=True,  index=True)
    entity_id     = Column(String(64),  nullable=True)
    project_id    = Column(Integer,     nullable=True,  index=True)
    course_id     = Column(Integer,     nullable=True)
    metadata_json = Column(Text,        nullable=True)
    ip_address    = Column(String(45),  nullable=True)   # IPv4 or IPv6
    # -- prompt-consolidation Phase 1/3 additive columns --------------------
    actor_role    = Column(String(64),  nullable=True)
    user_agent    = Column(String(512), nullable=True)
    changes       = Column(JSON,        nullable=True)   # structured diff (compute_prompt_changes)
    summary       = Column(Text,        nullable=True)   # human-readable one-liner (audit UI)
    created_at    = Column(DateTime,    default=datetime.utcnow, index=True)

    def __init__(self, **kwargs): super().__init__(**kwargs)


class FeedbackSignal(Base):
    """
    Captures user feedback from two sources:
      1. Explicit:  regenerate/improvise instructions
      2. Implicit:  manual content edits

    feedback_scope:
      "one_time"  → apply to current block only; never influences future prompts
      "learning"  → stored as a reusable signal; used to improve future generations

    Only records where feedback_scope == "learning" are surfaced in the
    Feedback Library and injected into future generation prompts.
    """
    __tablename__ = "feedback_signals"

    id             = Column(Integer, primary_key=True)
    block_id       = Column(Integer, ForeignKey("blocks.id"), nullable=False)
    generation_id  = Column(Integer, ForeignKey("generations.id"), nullable=True)
    prompt_name    = Column(String(150), nullable=True)   # which prompt template this applies to
    block_type     = Column(String(80),  nullable=True)   # e.g. "Lesson", "Quiz"

    # Signal type
    signal_source  = Column(String(20), nullable=False)   # "regenerate" | "edit"
    feedback_scope = Column(String(20), nullable=False)   # "one_time" | "learning"

    # Content traceability
    original_content  = Column(Text, nullable=True)
    final_content     = Column(Text, nullable=True)

    # User instructions / reason
    user_instruction  = Column(Text, nullable=True)   # explicit regen instruction
    edit_reason       = Column(Text, nullable=True)   # why the edit was made (for implicit)

    # Metadata
    topic          = Column(String(255), nullable=True)
    author         = Column(String(100), nullable=True)
    created_at     = Column(DateTime, default=datetime.utcnow)

    def __init__(self, **kwargs): super().__init__(**kwargs)


class GenerationJob(Base):
    """Background job record for non-blocking generation and review workflows.

    Column notes
    ------------
    status           : queued | running | completed | failed | cancelled
    progress         : 0-100 integer, updated at each pipeline stage
    current_step     : human-readable stage label shown in the UI
    input_payload_json: serialised generation parameters (alias: request_json)
    result_entity_id : id of the Generation row created on success
    error_message    : clean user-facing failure reason (no stack traces)
    completed_at     : set when status transitions to completed/failed/cancelled
    """
    __tablename__ = "generation_jobs"

    id           = Column(String(64), primary_key=True)
    job_type     = Column(String(50),  default="generation")
    status       = Column(String(30),  default="queued")
    progress     = Column(Integer,     default=0)

    # Canonical column names (old aliases kept for backward compat below)
    current_step       = Column(String(160), default="Queued")
    input_payload_json = Column(Text)          # primary params column
    result_json        = Column(Text)          # full result JSON
    result_entity_id   = Column(Integer,  nullable=True)  # FK → generations.id
    error_message      = Column(Text)

    # Scope
    project_id = Column(Integer, nullable=True)
    course_id  = Column(Integer, nullable=True)
    created_by = Column(String(100))

    # Timestamps
    created_at   = Column(DateTime, default=datetime.utcnow)
    updated_at   = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    # ── Back-compat aliases so existing code using .stage / .request_json /
    #    .generation_id continues to work without changes ──────────────────
    @property
    def stage(self):
        return self.current_step

    @stage.setter
    def stage(self, v):
        self.current_step = v

    @property
    def request_json(self):
        return self.input_payload_json

    @request_json.setter
    def request_json(self, v):
        self.input_payload_json = v

    @property
    def generation_id(self):
        return self.result_entity_id

    @generation_id.setter
    def generation_id(self, v):
        self.result_entity_id = v

    def __init__(self, **kwargs): super().__init__(**kwargs)


class JobMetric(Base):
    """Stage-level timing and token metrics for observability."""
    __tablename__ = "job_metrics"

    id = Column(Integer, primary_key=True)
    job_id = Column(String(64), nullable=False)
    stage = Column(String(120), nullable=False)
    started_at = Column(DateTime, default=datetime.utcnow)
    ended_at = Column(DateTime, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    model_name = Column(String(160), nullable=True)
    input_chars = Column(Integer, nullable=True)
    output_chars = Column(Integer, nullable=True)
    status = Column(String(30), default="running")
    error_message = Column(Text, nullable=True)

    def __init__(self, **kwargs): super().__init__(**kwargs)


class LLMUsageLog(Base):
    """One row per LLM call — token usage, cost estimate, latency, and scope.

    Written by usage_service.log_llm_usage() after every generate_text() call.
    Never raises: a failed insert is logged at WARNING level and ignored.
    """
    __tablename__ = "llm_usage_logs"

    id              = Column(Integer,     primary_key=True)
    user_id         = Column(String(100), nullable=True,  index=True)   # username
    project_id      = Column(Integer,     nullable=True,  index=True)
    course_id       = Column(Integer,     nullable=True)
    entity_type     = Column(String(60),  nullable=True)   # generation | cdd | blueprint | evaluation | style
    entity_id       = Column(String(64),  nullable=True)
    prompt_template = Column(String(150), nullable=True)
    prompt_version  = Column(String(50),  nullable=True)
    model_name      = Column(String(160), nullable=False,  index=True)
    input_tokens    = Column(Integer,     nullable=True)
    output_tokens   = Column(Integer,     nullable=True)
    total_tokens    = Column(Integer,     nullable=True)
    estimated_cost  = Column(Float,       nullable=True)   # USD, 6 decimal places
    duration_ms     = Column(Integer,     nullable=True)
    status          = Column(String(30),  nullable=False)  # success|retry_success|fallback_success|error
    error_message   = Column(Text,        nullable=True)
    created_at      = Column(DateTime,    default=datetime.utcnow, index=True)

    def __init__(self, **kwargs): super().__init__(**kwargs)


class DocumentChunk(Base):
    """Searchable document chunk used to avoid injecting whole files into prompts."""
    __tablename__ = "document_chunks"

    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    token_estimate = Column(Integer, default=0)
    embedding_json = Column(Text, nullable=True)  # optional future pgvector/embedding storage
    created_at = Column(DateTime, default=datetime.utcnow)

    def __init__(self, **kwargs): super().__init__(**kwargs)


class CourseDesignDocument(Base):
    """Master Course Design Document — the top-level strategy document for a course."""
    __tablename__ = "course_design_documents"
    id = Column(Integer, primary_key=True)
    title = Column(String(255), nullable=False)
    course_title = Column(String(255), nullable=False)
    description = Column(Text)
    active_version = Column(String(20), default="v1")
    workflow_state = Column(String(50), default="draft")
    created_by = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    project_id = Column(Integer, nullable=True)   # FK to projects.id (nullable for backward compat)
    course_id  = Column(Integer, nullable=True)   # FK to courses.id
    versions = relationship("CDDVersion", back_populates="cdd", cascade="all, delete-orphan")
    blueprints = relationship("ModuleBlueprint", back_populates="cdd", cascade="all, delete-orphan")
    def __init__(self, **kwargs): super().__init__(**kwargs)

class CDDVersion(Base):
    """Version-controlled snapshot of a CDD — supports editing and regeneration."""
    __tablename__ = "cdd_versions"
    id = Column(Integer, primary_key=True)
    cdd_id = Column(Integer, ForeignKey("course_design_documents.id"))
    version = Column(String(20), nullable=False)
    version_number = Column(Integer, nullable=True)      # integer ordering (1, 2, 3…)
    parent_version_id = Column(Integer, nullable=True)   # FK to previous cdd_versions.id
    full_content = Column(Text)           # Full raw generated text
    sections = Column(Text)               # JSON dict: {section_title: content}
    generation_params = Column(Text)      # JSON: params used to generate this version
    change_reason = Column(Text)
    is_active = Column(Boolean, default=False)
    created_by = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    cdd = relationship("CourseDesignDocument", back_populates="versions")
    def __init__(self, **kwargs): super().__init__(**kwargs)


class ModuleBlueprint(Base):
    """Module Blueprint — module-level structural plan derived from a CDD."""
    __tablename__ = "module_blueprints"
    id = Column(Integer, primary_key=True)
    cdd_id = Column(Integer, ForeignKey("course_design_documents.id"), nullable=True)
    title = Column(String(255), nullable=False)
    module_title = Column(String(255), nullable=False)
    module_number = Column(Integer, default=1)
    module_objective = Column(Text)
    active_version = Column(String(20), default="v1")
    workflow_state = Column(String(50), default="draft")
    created_by = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    project_id = Column(Integer, nullable=True)   # FK to projects.id
    course_id  = Column(Integer, nullable=True)   # FK to courses.id
    cdd = relationship("CourseDesignDocument", back_populates="blueprints")
    versions = relationship("BlueprintVersion", back_populates="blueprint", cascade="all, delete-orphan")
    def __init__(self, **kwargs): super().__init__(**kwargs)

class BlueprintVersion(Base):
    """Version-controlled snapshot of a Module Blueprint."""
    __tablename__ = "blueprint_versions"
    id = Column(Integer, primary_key=True)
    blueprint_id = Column(Integer, ForeignKey("module_blueprints.id"))
    version = Column(String(20), nullable=False)
    version_number = Column(Integer, nullable=True)      # integer ordering (1, 2, 3…)
    parent_version_id = Column(Integer, nullable=True)   # FK to previous blueprint_versions.id
    full_content = Column(Text)
    sections = Column(Text)               # JSON dict: {section_title: content}
    generation_params = Column(Text)
    change_reason = Column(Text)
    is_active = Column(Boolean, default=False)
    created_by = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    blueprint = relationship("ModuleBlueprint", back_populates="versions")
    def __init__(self, **kwargs): super().__init__(**kwargs)


class UserPromptHistory(Base):
    """Saved user additional-instruction records, scoped per component + project/cluster/course.

    These capture the free-text instructions users type in generation forms
    (e.g. "Focus on clinical simulations, add DEI examples") so they can be
    reloaded, versioned, and improved across sessions.

    Versioning: multiple rows may share (name + component + project_id + course_id).
    version_number distinguishes them — higher = newer.
    """
    __tablename__ = "user_prompt_history"

    id             = Column(Integer,     primary_key=True)
    name           = Column(String(255), nullable=False)
    component      = Column(String(50),  nullable=False)   # style|cdd|blueprint|generate
    content        = Column(Text,        nullable=False)
    version_number = Column(Integer,     default=1, nullable=False)
    project_id     = Column(Integer,     nullable=True)
    cluster_id     = Column(Integer,     nullable=True)
    course_id      = Column(Integer,     nullable=True)
    created_by     = Column(String(100), nullable=True)
    created_at     = Column(DateTime,    default=datetime.utcnow)
    updated_at     = Column(DateTime,    default=datetime.utcnow)
    is_active      = Column(Boolean,     default=True)

    def __init__(self, **kwargs): super().__init__(**kwargs)


class Project(Base):
    """Top-level client project container — this IS the tenant.

    Tenant fields: slug (organization code, unique), status (active|suspended),
    max_users (license), and optional per-tenant Azure app credentials.
    """
    __tablename__ = "projects"
    id               = Column(Integer, primary_key=True)
    name             = Column(String(255), nullable=False)   # display name
    description      = Column(Text)
    client_name      = Column(String(255))
    created_by       = Column(String(100))
    created_at       = Column(DateTime, default=datetime.utcnow)
    is_active        = Column(Boolean, default=True)
    active_style_id  = Column(Integer, nullable=True)   # Project-level active style (FK to styles.id)
    # ── Tenant (organization) fields ──────────────────────────────────────────
    slug                   = Column(String(64), unique=True, index=True, nullable=True)  # org code
    status                 = Column(String(20), nullable=False, default="active")        # active|suspended
    max_users              = Column(Integer, nullable=False, default=50)
    microsoft_auth_enabled = Column(Boolean, nullable=False, default=True)
    azure_tenant_id        = Column(String(64), nullable=True)
    azure_client_id        = Column(String(64), nullable=True)
    azure_client_secret    = Column(String(512), nullable=True)
    azure_new_user_role    = Column(String(32), nullable=True)
    allowed_email_domains  = Column(String(500), nullable=True)
    clusters         = relationship("Cluster", back_populates="project", cascade="all, delete-orphan")
    courses          = relationship("Course", back_populates="project", cascade="all, delete-orphan")
    assignments      = relationship("ProjectUserAssignment", back_populates="project", cascade="all, delete-orphan")
    memberships      = relationship("TenantMembership", back_populates="project", cascade="all, delete-orphan")
    def __init__(self, **kwargs): super().__init__(**kwargs)


class Cluster(Base):
    """Domain/category container that groups related Courses within a Project.

    Hierarchy: Project → Cluster → Course.
    Every Course must belong to exactly one Cluster.  Existing courses are
    automatically migrated into a 'General' cluster on first startup.
    """
    __tablename__ = "clusters"
    id          = Column(Integer, primary_key=True)
    project_id  = Column(Integer, ForeignKey("projects.id"), nullable=False)
    name        = Column(String(255), nullable=False)
    description = Column(Text)
    created_by  = Column(String(100))
    created_at  = Column(DateTime, default=datetime.utcnow)
    is_active   = Column(Boolean, default=True)
    # Relationships — no cascade on courses; Project.courses handles hard-delete cascade
    courses         = relationship("Course", back_populates="cluster")
    project         = relationship("Project", back_populates="clusters")
    cluster_prompts = relationship("ClusterPrompt", back_populates="cluster",
                                   cascade="all, delete-orphan")
    def __init__(self, **kwargs): super().__init__(**kwargs)


class ClusterPrompt(Base):
    """Prompt content scoped to a single cluster, auto-injected into course style context.

    Hierarchy: Project → Cluster → ClusterPrompt (auto-injected into every Course style).
    """
    __tablename__ = "cluster_prompts"
    id                   = Column(Integer, primary_key=True, autoincrement=True)
    cluster_id           = Column(Integer, ForeignKey("clusters.id", ondelete="CASCADE"),
                                  nullable=True, index=True)
    name                 = Column(String(255), nullable=False)
    description          = Column(Text)
    system_prompt        = Column(Text)
    user_prompt_template = Column(Text)
    created_by           = Column(String(100))
    created_at           = Column(DateTime, default=datetime.utcnow)
    updated_at           = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active            = Column(Boolean, default=True)

    cluster = relationship("Cluster", back_populates="cluster_prompts")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class Course(Base):
    """Course within a Cluster, within a Project."""
    __tablename__ = "courses"
    id               = Column(Integer, primary_key=True)
    project_id       = Column(Integer, ForeignKey("projects.id"), nullable=False)
    cluster_id       = Column(Integer, ForeignKey("clusters.id"), nullable=True)   # nullable for backward compat
    name             = Column(String(255), nullable=False)
    description      = Column(Text)
    created_by       = Column(String(100))
    created_at       = Column(DateTime, default=datetime.utcnow)
    is_active        = Column(Boolean, default=True)
    active_style_id       = Column(Integer, nullable=True)   # Course-level active style override
    active_cdd_id         = Column(Integer, nullable=True)   # Course-level pinned CDD (Req 3)
    active_blueprint_id   = Column(Integer, nullable=True)   # Course-level pinned Blueprint (Req 3)
    # Target & Model config — persisted per course (Req 1)
    config_model_choice      = Column(String(100), nullable=True)
    config_expert_domain     = Column(String(255), nullable=True)
    config_target_audience   = Column(String(255), nullable=True)
    config_audience_category = Column(String(100), nullable=True)
    project          = relationship("Project", back_populates="courses")
    cluster          = relationship("Cluster", back_populates="courses")
    def __init__(self, **kwargs): super().__init__(**kwargs)


class ProjectUserAssignment(Base):
    """Maps non-admin users to the projects they can access."""
    __tablename__ = "project_user_assignments"
    id          = Column(Integer, primary_key=True)
    project_id  = Column(Integer, ForeignKey("projects.id"), nullable=False)
    username    = Column(String(100), nullable=False)
    assigned_at = Column(DateTime, default=datetime.utcnow)
    project     = relationship("Project", back_populates="assignments")
    def __init__(self, **kwargs): super().__init__(**kwargs)


class CourseUserAssignment(Base):
    """Maps non-admin users to specific courses they can access."""
    __tablename__ = "course_user_assignments"
    id          = Column(Integer, primary_key=True)
    course_id   = Column(Integer, ForeignKey("courses.id"), nullable=False)
    username    = Column(String(100), nullable=False)
    assigned_at = Column(DateTime, default=datetime.utcnow)
    def __init__(self, **kwargs): super().__init__(**kwargs)


class TenantMembership(Base):
    """Membership of a User in a Project (tenant), with a per-tenant role.

    A person may belong to several tenants (via several org codes) with a
    different role in each. One global User row, many memberships.
    role: admin | reviewer | author (tenant_admin/prompt_manager/user).
    """
    __tablename__ = "tenant_memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "project_id", name="uq_membership_user_project"),
    )
    id         = Column(Integer, primary_key=True)
    user_id    = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    role       = Column(String(20), nullable=False, default="author")
    # Cosmetic job title shown in the UI (e.g. "Instructional Designer"). Purely
    # a display label — actual permissions always come from `role` above.
    display_title = Column(String(100), nullable=True)
    active     = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(String(100))
    user       = relationship("User", back_populates="memberships")
    project    = relationship("Project", back_populates="memberships")
    def __init__(self, **kwargs): super().__init__(**kwargs)


class CentralRepository(Base):
    """Admin-managed central repository — single source of truth for reusable prompts, assets, and learnings."""
    __tablename__ = "central_repositories"
    id            = Column(Integer, primary_key=True)
    title         = Column(String(255), nullable=False)
    item_type     = Column(String(50), nullable=False, default="Prompt")  # Prompt | Asset | Learning
    content       = Column(Text, nullable=False)
    description   = Column(Text)
    source_module = Column(String(100))   # Project | Cluster | Course | Component | Style | CDD | Blueprint | Generate
    project_id    = Column(Integer, ForeignKey("projects.id"), nullable=True)
    cluster_id    = Column(Integer, ForeignKey("clusters.id"), nullable=True)
    course_id     = Column(Integer, ForeignKey("courses.id"), nullable=True)
    client_name   = Column(String(255))   # denormalized for display
    cluster_name  = Column(String(255))   # denormalized for display
    tags          = Column(String(500))
    status        = Column(String(20), default="active")  # active | archived
    usage_count   = Column(Integer, default=0)
    created_by    = Column(String(100))
    created_at    = Column(DateTime, default=datetime.utcnow)
    updated_at    = Column(DateTime, default=datetime.utcnow)
    last_used_at  = Column(DateTime, nullable=True)
    def __init__(self, **kwargs): super().__init__(**kwargs)


# =============================================================================
# Database Initialization & Migrations
# =============================================================================

def init_db():
    # ── Schema DDL is Alembic-owned ────────────────────────────────────────
    # As of the 000100000001 baseline revision, the schema is managed by
    # `alembic upgrade head` (the deploy pipeline runs it before boot).
    # The legacy create_all()/ALTER/CREATE INDEX path below is kept only as
    # an explicit opt-in escape hatch and is OFF by default: running both
    # would desync alembic_version from the real schema (a container booting
    # before `upgrade` creates a new table via create_all, then the CREATE
    # TABLE migration fails with "already exists").
    auto_ddl = os.getenv("DB_AUTO_DDL", "false").strip().lower() in ("1", "true", "yes")
    if auto_ddl:
        _run_legacy_ddl()

    # Data backfills (idempotent DML, not schema) run unconditionally on
    # PostgreSQL — they repair legacy rows and are no-ops once applied.
    # Skipped on SQLite (the test suite) which has no legacy data and does
    # not support the PostgreSQL syntax used.
    if engine.dialect.name == "postgresql":
        _run_data_backfills()


def _run_legacy_ddl():
    """Legacy pre-Alembic schema DDL — only runs when DB_AUTO_DDL is set."""
    # Create all tables that don't exist yet.
    Base.metadata.create_all(bind=engine)

    # Column-level migrations — ADD COLUMN IF NOT EXISTS is idempotent in PostgreSQL 9.6+.
    # Safe to run every startup; PostgreSQL silently skips columns that already exist.
    _column_migrations = [
        # documents
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS doc_tag VARCHAR(50) DEFAULT 'general'",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'active'",
        # blocks
        "ALTER TABLE blocks ADD COLUMN IF NOT EXISTS position INTEGER DEFAULT 0",
        "ALTER TABLE blocks ADD COLUMN IF NOT EXISTS sources TEXT",
        "ALTER TABLE blocks ADD COLUMN IF NOT EXISTS plagiarism_score INTEGER",
        "ALTER TABLE blocks ADD COLUMN IF NOT EXISTS plagiarism_report TEXT",
        "ALTER TABLE blocks ADD COLUMN IF NOT EXISTS eval_score INTEGER",
        "ALTER TABLE blocks ADD COLUMN IF NOT EXISTS eval_report TEXT",
        "ALTER TABLE blocks ADD COLUMN IF NOT EXISTS ai_review TEXT",
        # generations — traceability (v7)
        "ALTER TABLE generations ADD COLUMN IF NOT EXISTS cdd_id INTEGER",
        "ALTER TABLE generations ADD COLUMN IF NOT EXISTS cdd_version VARCHAR(20)",
        "ALTER TABLE generations ADD COLUMN IF NOT EXISTS blueprint_id INTEGER",
        "ALTER TABLE generations ADD COLUMN IF NOT EXISTS blueprint_version VARCHAR(20)",
        # users (v13)
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS permissions TEXT DEFAULT '[]'",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMP",
        # course_design_documents — project/course scoping (v18)
        "ALTER TABLE course_design_documents ADD COLUMN IF NOT EXISTS project_id INTEGER",
        "ALTER TABLE course_design_documents ADD COLUMN IF NOT EXISTS course_id INTEGER",
        # module_blueprints (v18)
        "ALTER TABLE module_blueprints ADD COLUMN IF NOT EXISTS project_id INTEGER",
        "ALTER TABLE module_blueprints ADD COLUMN IF NOT EXISTS course_id INTEGER",
        # generations (v18)
        "ALTER TABLE generations ADD COLUMN IF NOT EXISTS project_id INTEGER",
        "ALTER TABLE generations ADD COLUMN IF NOT EXISTS course_id INTEGER",
        # projects — scoped style activation (v19)
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS active_style_id INTEGER",
        # courses (v19)
        "ALTER TABLE courses ADD COLUMN IF NOT EXISTS active_style_id INTEGER",
        "ALTER TABLE courses ADD COLUMN IF NOT EXISTS active_cdd_id INTEGER",
        # courses — per-course pinning + config (Req 1, 3)
        "ALTER TABLE courses ADD COLUMN IF NOT EXISTS active_blueprint_id INTEGER",
        "ALTER TABLE courses ADD COLUMN IF NOT EXISTS config_model_choice VARCHAR(100)",
        "ALTER TABLE courses ADD COLUMN IF NOT EXISTS config_expert_domain VARCHAR(255)",
        "ALTER TABLE courses ADD COLUMN IF NOT EXISTS config_target_audience VARCHAR(255)",
        "ALTER TABLE courses ADD COLUMN IF NOT EXISTS config_audience_category VARCHAR(100)",
        # courses — cluster hierarchy (v20)
        "ALTER TABLE courses ADD COLUMN IF NOT EXISTS cluster_id INTEGER",
        # styles — understanding status (v20)
        "ALTER TABLE styles ADD COLUMN IF NOT EXISTS understanding_status VARCHAR(20) DEFAULT 'fresh'",
        # blocks — enterprise approval & versioning (Phase 2)
        "ALTER TABLE blocks ADD COLUMN IF NOT EXISTS version_num INTEGER DEFAULT 1",
        "ALTER TABLE blocks ADD COLUMN IF NOT EXISTS assigned_reviewer VARCHAR(100)",
        "ALTER TABLE blocks ADD COLUMN IF NOT EXISTS review_requested_at TIMESTAMP",
        "ALTER TABLE blocks ADD COLUMN IF NOT EXISTS approved_by VARCHAR(100)",
        "ALTER TABLE blocks ADD COLUMN IF NOT EXISTS approved_at TIMESTAMP",
        "ALTER TABLE blocks ADD COLUMN IF NOT EXISTS rejected_reason TEXT",
        # blocks — autosave draft (Phase 12)
        "ALTER TABLE blocks ADD COLUMN IF NOT EXISTS draft_content TEXT",
        "ALTER TABLE blocks ADD COLUMN IF NOT EXISTS draft_saved_at TIMESTAMP",
        "ALTER TABLE blocks ADD COLUMN IF NOT EXISTS draft_saved_by VARCHAR(100)",
        "ALTER TABLE blocks ADD COLUMN IF NOT EXISTS content_html TEXT",
        "ALTER TABLE blocks ADD COLUMN IF NOT EXISTS content_html_at TIMESTAMP",
        "ALTER TABLE blocks ADD COLUMN IF NOT EXISTS module_id INTEGER",
        # blocks — extended approval workflow (Phase 11)
        "ALTER TABLE blocks ADD COLUMN IF NOT EXISTS submitted_by VARCHAR(100)",
        "ALTER TABLE blocks ADD COLUMN IF NOT EXISTS reviewed_by VARCHAR(100)",
        "ALTER TABLE blocks ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMP",
        "ALTER TABLE blocks ADD COLUMN IF NOT EXISTS review_comments TEXT",
        "ALTER TABLE blocks ADD COLUMN IF NOT EXISTS archived_by VARCHAR(100)",
        "ALTER TABLE blocks ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP",
        # Normalise legacy capitalised workflow_state values
        "UPDATE blocks SET workflow_state = LOWER(workflow_state) WHERE workflow_state != LOWER(workflow_state)",
        # style_versions — Style Intelligence versioning (Phase 10)
        # Table created by create_all; these cover existing DBs
        "ALTER TABLE style_versions ADD COLUMN IF NOT EXISTS version_number INTEGER DEFAULT 1",
        "ALTER TABLE style_versions ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT FALSE",
        "ALTER TABLE style_versions ADD COLUMN IF NOT EXISTS understanding_content TEXT",
        "ALTER TABLE style_versions ADD COLUMN IF NOT EXISTS change_summary VARCHAR(500)",
        "ALTER TABLE style_versions ADD COLUMN IF NOT EXISTS created_by VARCHAR(100)",
        # cdd_versions — extended versioning (Phase 10)
        "ALTER TABLE cdd_versions ADD COLUMN IF NOT EXISTS version_number INTEGER",
        "ALTER TABLE cdd_versions ADD COLUMN IF NOT EXISTS parent_version_id INTEGER",
        # blueprint_versions — extended versioning (Phase 10)
        "ALTER TABLE blueprint_versions ADD COLUMN IF NOT EXISTS version_number INTEGER",
        "ALTER TABLE blueprint_versions ADD COLUMN IF NOT EXISTS parent_version_id INTEGER",
        # audit_logs — enterprise governance (Phase 9)
        # Table is created by Base.metadata.create_all; these add any missing cols
        # on existing databases that pre-date the AuditLog model.
        "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS entity_type VARCHAR(60)",
        "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS entity_id VARCHAR(64)",
        "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS project_id INTEGER",
        "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS course_id INTEGER",
        "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS metadata_json TEXT",
        "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS ip_address VARCHAR(45)",
        # workflow_events — review comment stored with each transition (Phase 11)
        "ALTER TABLE workflow_events ADD COLUMN IF NOT EXISTS comment TEXT DEFAULT ''",
        # generation_jobs — extended schema (Phase 6)
        "ALTER TABLE generation_jobs ADD COLUMN IF NOT EXISTS current_step VARCHAR(160) DEFAULT 'Queued'",
        "ALTER TABLE generation_jobs ADD COLUMN IF NOT EXISTS input_payload_json TEXT",
        "ALTER TABLE generation_jobs ADD COLUMN IF NOT EXISTS result_entity_id INTEGER",
        "ALTER TABLE generation_jobs ADD COLUMN IF NOT EXISTS project_id INTEGER",
        "ALTER TABLE generation_jobs ADD COLUMN IF NOT EXISTS course_id INTEGER",
        "ALTER TABLE generation_jobs ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP",
        # llm_usage_logs — Task 16 cost tracking
        "ALTER TABLE llm_usage_logs ADD COLUMN IF NOT EXISTS user_id VARCHAR(100)",
        "ALTER TABLE llm_usage_logs ADD COLUMN IF NOT EXISTS project_id INTEGER",
        "ALTER TABLE llm_usage_logs ADD COLUMN IF NOT EXISTS course_id INTEGER",
        "ALTER TABLE llm_usage_logs ADD COLUMN IF NOT EXISTS entity_type VARCHAR(60)",
        "ALTER TABLE llm_usage_logs ADD COLUMN IF NOT EXISTS entity_id VARCHAR(64)",
        "ALTER TABLE llm_usage_logs ADD COLUMN IF NOT EXISTS prompt_template VARCHAR(150)",
        "ALTER TABLE llm_usage_logs ADD COLUMN IF NOT EXISTS prompt_version VARCHAR(50)",
        "ALTER TABLE llm_usage_logs ADD COLUMN IF NOT EXISTS model_name VARCHAR(160)",
        "ALTER TABLE llm_usage_logs ADD COLUMN IF NOT EXISTS input_tokens INTEGER",
        "ALTER TABLE llm_usage_logs ADD COLUMN IF NOT EXISTS output_tokens INTEGER",
        "ALTER TABLE llm_usage_logs ADD COLUMN IF NOT EXISTS total_tokens INTEGER",
        "ALTER TABLE llm_usage_logs ADD COLUMN IF NOT EXISTS estimated_cost FLOAT",
        "ALTER TABLE llm_usage_logs ADD COLUMN IF NOT EXISTS duration_ms INTEGER",
        "ALTER TABLE llm_usage_logs ADD COLUMN IF NOT EXISTS status VARCHAR(30)",
        "ALTER TABLE llm_usage_logs ADD COLUMN IF NOT EXISTS error_message TEXT",
        "ALTER TABLE llm_usage_logs ADD COLUMN IF NOT EXISTS created_at TIMESTAMP",
        # prompts — component typing and default flag
        "ALTER TABLE prompts ADD COLUMN IF NOT EXISTS component_type VARCHAR(50)",
        "ALTER TABLE prompts ADD COLUMN IF NOT EXISTS is_default BOOLEAN DEFAULT FALSE",
        "ALTER TABLE prompts ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP",
        # prompt_versions — authorship tracking
        "ALTER TABLE prompt_versions ADD COLUMN IF NOT EXISTS created_by VARCHAR(100)",
        # central_repositories — admin central repo (Phase CR)
        "ALTER TABLE central_repositories ADD COLUMN IF NOT EXISTS description TEXT",
        "ALTER TABLE central_repositories ADD COLUMN IF NOT EXISTS source_module VARCHAR(100)",
        "ALTER TABLE central_repositories ADD COLUMN IF NOT EXISTS project_id INTEGER",
        "ALTER TABLE central_repositories ADD COLUMN IF NOT EXISTS cluster_id INTEGER",
        "ALTER TABLE central_repositories ADD COLUMN IF NOT EXISTS course_id INTEGER",
        "ALTER TABLE central_repositories ADD COLUMN IF NOT EXISTS client_name VARCHAR(255)",
        "ALTER TABLE central_repositories ADD COLUMN IF NOT EXISTS cluster_name VARCHAR(255)",
        "ALTER TABLE central_repositories ADD COLUMN IF NOT EXISTS tags VARCHAR(500)",
        "ALTER TABLE central_repositories ADD COLUMN IF NOT EXISTS usage_count INTEGER DEFAULT 0",
        "ALTER TABLE central_repositories ADD COLUMN IF NOT EXISTS last_used_at TIMESTAMP",
        # cluster_prompts — make cluster assignment optional (Req 2)
        "ALTER TABLE cluster_prompts ALTER COLUMN cluster_id DROP NOT NULL",
        # ── Tenant (organization) system — Microsoft auth + org codes ─────────
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS project_id INTEGER",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_platform_admin BOOLEAN DEFAULT FALSE NOT NULL",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS microsoft_oid VARCHAR(64)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR(255)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS display_name VARCHAR(200)",
        "ALTER TABLE tenant_memberships ADD COLUMN IF NOT EXISTS display_title VARCHAR(100)",
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS slug VARCHAR(64)",
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'active'",
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS max_users INTEGER DEFAULT 50",
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS microsoft_auth_enabled BOOLEAN DEFAULT TRUE",
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS azure_tenant_id VARCHAR(64)",
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS azure_client_id VARCHAR(64)",
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS azure_client_secret VARCHAR(512)",
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS azure_new_user_role VARCHAR(32)",
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS allowed_email_domains VARCHAR(500)",
    ]

    # Each migration runs in its own transaction so AccessExclusiveLock is held
    # for the minimum time — prevents deadlocks when concurrent Streamlit reruns
    # would otherwise hold locks across multiple tables simultaneously.
    for stmt in _column_migrations:
        with engine.begin() as conn:
            conn.execute(text(stmt))

    # Performance indexes — idempotent and safe on every startup.
    _index_migrations = [
            "CREATE INDEX IF NOT EXISTS idx_generations_project_course ON generations(project_id, course_id)",
            "CREATE INDEX IF NOT EXISTS idx_generations_blueprint_id ON generations(blueprint_id)",
            "CREATE INDEX IF NOT EXISTS idx_generations_cdd_id ON generations(cdd_id)",
            "CREATE INDEX IF NOT EXISTS idx_generations_created_at ON generations(created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_blocks_generation_id ON blocks(generation_id)",
            "CREATE INDEX IF NOT EXISTS idx_blocks_updated_at ON blocks(updated_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_blocks_workflow_state ON blocks(workflow_state)",
            "CREATE INDEX IF NOT EXISTS idx_cdd_project_course ON course_design_documents(project_id, course_id)",
            "CREATE INDEX IF NOT EXISTS idx_blueprint_project_course ON module_blueprints(project_id, course_id)",
            "CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status)",
            "CREATE INDEX IF NOT EXISTS idx_project_user_assignments_user_project ON project_user_assignments(username, project_id)",
            "CREATE INDEX IF NOT EXISTS idx_course_user_assignments_user_course ON course_user_assignments(username, course_id)",
            "CREATE INDEX IF NOT EXISTS idx_generation_jobs_status_created ON generation_jobs(status, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_generation_jobs_created_by ON generation_jobs(created_by, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_style_versions_style_id ON style_versions(style_id, version_number DESC)",
            "CREATE INDEX IF NOT EXISTS idx_style_versions_active ON style_versions(style_id, is_active)",
            "CREATE INDEX IF NOT EXISTS idx_cdd_versions_version_number ON cdd_versions(cdd_id, version_number)",
            "CREATE INDEX IF NOT EXISTS idx_blueprint_versions_version_number ON blueprint_versions(blueprint_id, version_number)",
            "CREATE INDEX IF NOT EXISTS idx_audit_logs_user_created ON audit_logs(user_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_audit_logs_action ON audit_logs(action, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_audit_logs_entity ON audit_logs(entity_type, entity_id)",
            "CREATE INDEX IF NOT EXISTS idx_audit_logs_project ON audit_logs(project_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_generation_jobs_project_course ON generation_jobs(project_id, course_id)",
            "CREATE INDEX IF NOT EXISTS idx_clusters_project_id ON clusters(project_id, is_active)",
            "CREATE INDEX IF NOT EXISTS idx_courses_cluster_id ON courses(cluster_id)",
            "CREATE INDEX IF NOT EXISTS idx_uph_component_project ON user_prompt_history(component, project_id)",
            "CREATE INDEX IF NOT EXISTS idx_uph_component_course ON user_prompt_history(component, course_id)",
            "CREATE INDEX IF NOT EXISTS idx_uph_name_component ON user_prompt_history(name, component)",
            "CREATE INDEX IF NOT EXISTS idx_job_metrics_job_id ON job_metrics(job_id)",
            "CREATE INDEX IF NOT EXISTS idx_document_chunks_document_id ON document_chunks(document_id)",
            # Phase 2
            "CREATE INDEX IF NOT EXISTS idx_block_versions_block_id ON block_versions(block_id, version_num DESC)",
            "CREATE INDEX IF NOT EXISTS idx_blocks_assigned_reviewer ON blocks(assigned_reviewer)",
            "CREATE INDEX IF NOT EXISTS idx_blocks_review_requested ON blocks(review_requested_at DESC)",
            # Central Repository — admin repo indexes
            "CREATE INDEX IF NOT EXISTS idx_central_repo_status ON central_repositories(status, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_central_repo_type ON central_repositories(item_type, status)",
            "CREATE INDEX IF NOT EXISTS idx_central_repo_project ON central_repositories(project_id)",
            "CREATE INDEX IF NOT EXISTS idx_central_repo_cluster ON central_repositories(cluster_id)",
            # Task 16 — LLM usage cost tracking
            "CREATE INDEX IF NOT EXISTS idx_llm_usage_user_created ON llm_usage_logs(user_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_llm_usage_project_created ON llm_usage_logs(project_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_llm_usage_model ON llm_usage_logs(model_name, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_llm_usage_status ON llm_usage_logs(status, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_llm_usage_entity ON llm_usage_logs(entity_type, entity_id)",
            # Tenant (organization) system
            "CREATE INDEX IF NOT EXISTS idx_users_project_id ON users(project_id)",
            "CREATE INDEX IF NOT EXISTS idx_users_microsoft_oid ON users(microsoft_oid)",
            "CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)",
            "CREATE INDEX IF NOT EXISTS idx_tenant_memberships_user ON tenant_memberships(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_tenant_memberships_project ON tenant_memberships(project_id)",
        ]
    for stmt in _index_migrations:
        with engine.begin() as conn:
            conn.execute(text(stmt))


def _run_data_backfills():
    """Idempotent data repairs (DML only — no schema changes)."""
    # Backfill updated_at for documents where it landed NULL
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE documents SET updated_at = uploaded_at WHERE updated_at IS NULL"
        ))

    # ── Cluster hierarchy backfill (idempotent) ───────────────────────────────
    # Step 1: For every active project that has no cluster yet, create "General".
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO clusters (project_id, name, description, created_by, created_at, is_active)
            SELECT p.id, 'General', 'Default cluster for existing courses', 'system', NOW(), TRUE
            FROM projects p
            WHERE NOT EXISTS (
                SELECT 1 FROM clusters c WHERE c.project_id = p.id
            )
        """))

    # Step 2: Assign any course with NULL cluster_id to its project's first cluster.
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE courses
            SET cluster_id = (
                SELECT c.id FROM clusters c
                WHERE c.project_id = courses.project_id
                ORDER BY c.created_at ASC
                LIMIT 1
            )
            WHERE cluster_id IS NULL
        """))

    # ── Tenant (organization) backfill (idempotent) ───────────────────────────
    # Give every project a slug + tenant defaults so it can act as a tenant.
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE projects
            SET slug = regexp_replace(lower(coalesce(name, 'org')), '[^a-z0-9]+', '-', 'g') || '-' || id::text
            WHERE slug IS NULL OR slug = ''
        """))
        conn.execute(text("UPDATE projects SET status = 'active' WHERE status IS NULL"))
        conn.execute(text("UPDATE projects SET max_users = 50 WHERE max_users IS NULL"))
        conn.execute(text("UPDATE projects SET microsoft_auth_enabled = TRUE WHERE microsoft_auth_enabled IS NULL"))
    with engine.begin() as conn:
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_projects_slug ON projects(slug)"))
    # Membership for every existing non-platform-admin user that has a project_id.
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO tenant_memberships (user_id, project_id, role, active, created_at, created_by)
            SELECT u.id, u.project_id, COALESCE(u.role, 'author'), COALESCE(u.is_active, TRUE), NOW(), 'migration'
            FROM users u
            WHERE u.project_id IS NOT NULL
              AND u.is_platform_admin = FALSE
              AND NOT EXISTS (
                  SELECT 1 FROM tenant_memberships m
                  WHERE m.user_id = u.id AND m.project_id = u.project_id
              )
        """))


# =============================================================================
# Auth Helpers
# =============================================================================

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 100000)
    return binascii.hexlify(salt + dk).decode()

def verify_password(password: str, password_hash: str) -> bool:
    try:
        raw = binascii.unhexlify(password_hash.encode())
        salt, key = raw[:16], raw[16:]
        dk = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 100000)
        return dk == key
    except: return False


# =============================================================================
# Observability
# =============================================================================

def log_event(db, event_type: str, actor: str, details: str, metadata: dict = None):
    """Log a system event for observability."""
    entry = SystemLog(
        event_type=event_type,
        actor=actor,
        details=details,
        metadata_json=json.dumps(metadata) if metadata else None
    )
    db.add(entry)
    db.commit()


# =============================================================================
# RBAC — DB-layer helpers (query-only, no Streamlit)
# =============================================================================

def _is_lead_for_project(db, username: str, project_id: int) -> bool:
    """Return True if username is a reviewer (Lead) assigned to this project."""
    user = db.query(User).filter(User.username == username, User.role == "reviewer").first()
    if not user:
        return False
    return db.query(ProjectUserAssignment).filter(
        ProjectUserAssignment.username == username,
        ProjectUserAssignment.project_id == project_id
    ).first() is not None


def _is_lead_for_course(db, username: str, course_id: int) -> bool:
    """Return True if username is a reviewer (Lead) assigned to this course."""
    user = db.query(User).filter(User.username == username, User.role == "reviewer").first()
    if not user:
        return False
    # Lead is assigned at project level; also check direct course assignment
    course = db.query(Course).filter(Course.id == course_id).first()
    if not course:
        return False
    proj_ok = _is_lead_for_project(db, username, course.project_id)
    course_ok = db.query(CourseUserAssignment).filter(
        CourseUserAssignment.username == username,
        CourseUserAssignment.course_id == course_id
    ).first() is not None
    return proj_ok or course_ok


def can_modify_style(db, username: str, user_role: str, project_id: int = None) -> bool:
    """Return True if user may create, upload to, activate, or regenerate Styles.
    Admin: always. Lead: only when assigned to the current project."""
    if user_role == "admin":
        return True
    if user_role == "reviewer":
        if project_id:
            return _is_lead_for_project(db, username, project_id)
        return db.query(ProjectUserAssignment).filter(
            ProjectUserAssignment.username == username
        ).first() is not None
    return False  # author (ID) and any other role: no modification


# =============================================================================
# CDD & Blueprint Version Accessors
# =============================================================================

def get_active_cdd_version(db, cdd_id: int) -> Optional["CDDVersion"]:
    """Return the active CDDVersion object for a given CDD."""
    cdd = db.query(CourseDesignDocument).filter(CourseDesignDocument.id == cdd_id).first()
    if not cdd:
        return None
    return db.query(CDDVersion).filter(
        CDDVersion.cdd_id == cdd_id,
        CDDVersion.version == cdd.active_version
    ).first()


def get_active_blueprint_version(db, blueprint_id: int) -> Optional["BlueprintVersion"]:
    """Return the active BlueprintVersion for a given Blueprint."""
    bp = db.query(ModuleBlueprint).filter(ModuleBlueprint.id == blueprint_id).first()
    if not bp:
        return None
    return db.query(BlueprintVersion).filter(
        BlueprintVersion.blueprint_id == blueprint_id,
        BlueprintVersion.version == bp.active_version
    ).first()


# =============================================================================
# Style CRUD & Context Helpers
# =============================================================================

def _slugify(name: str) -> str:
    """Convert a style name to a safe slug identifier."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower().strip()).strip("_")
    return slug[:80] or "style"


def create_style(db, name: str, description: str, custom_instructions: str,
                 document_ids: list, created_by: str) -> "Style":
    """Create and persist a new Style."""
    slug = _slugify(name)
    # Ensure uniqueness
    existing = db.query(Style).filter(Style.style_id == slug).first()
    if existing:
        slug = f"{slug}_{datetime.now().strftime('%H%M%S')}"
    style = Style(
        style_id=slug, name=name, description=description,
        custom_instructions=custom_instructions,
        created_by=created_by, is_active=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(style); db.commit(); db.refresh(style)
    for doc_id in (document_ids or []):
        db.add(StyleDocument(style_id=style.id, document_id=doc_id))
    db.commit()
    return style


def add_files_to_style(db, style: "Style", new_doc_ids: list):
    """Append new documents to an existing style without removing existing ones. Marks understanding as stale."""
    existing_doc_ids = {sd.document_id for sd in style.style_documents}
    added = 0
    for doc_id in new_doc_ids:
        if doc_id not in existing_doc_ids:
            db.add(StyleDocument(style_id=style.id, document_id=doc_id))
            added += 1
    if added > 0:
        style.understanding_status = "stale"
        style.updated_at = datetime.now(timezone.utc)
        db.commit()
    return added


def get_styles(db) -> list:
    """Return all styles ordered by most recently updated."""
    return db.query(Style).order_by(Style.updated_at.desc()).all()


def get_active_style(db, project_id=None, course_id=None) -> "Style | None":
    """
    Return the active Style for the given scope.
    Lookup order: course-level → project-level → global fallback.
    """
    if course_id:
        course = db.query(Course).filter(Course.id == course_id).first()
        if course and course.active_style_id:
            sty = db.query(Style).filter(Style.id == course.active_style_id).first()
            if sty:
                return sty
    if project_id:
        proj = db.query(Project).filter(Project.id == project_id).first()
        if proj and proj.active_style_id:
            sty = db.query(Style).filter(Style.id == proj.active_style_id).first()
            if sty:
                return sty
    return db.query(Style).filter(Style.is_active == True).first()


def set_active_style(db, style_id: int, scope: str = "global",
                     project_id=None, course_id=None) -> "Style":
    """
    Activate a style at the requested scope.
    scope: "global" | "project" | "course"
    - "course"  → sets Course.active_style_id  (only affects that course)
    - "project" → sets Project.active_style_id (affects all courses in project without a course-level override)
    - "global"  → legacy global boolean on Style.is_active (affects all unscoped contexts)
    """
    style = db.query(Style).filter(Style.id == style_id).first()
    if not style:
        return None
    if scope == "course" and course_id:
        course = db.query(Course).filter(Course.id == course_id).first()
        if course:
            course.active_style_id = style_id
            db.commit()
    elif scope == "project" and project_id:
        proj = db.query(Project).filter(Project.id == project_id).first()
        if proj:
            proj.active_style_id = style_id
            db.commit()
    else:
        # Global: deactivate all, activate this one
        db.query(Style).update({Style.is_active: False})
        style.is_active = True
        style.updated_at = datetime.now(timezone.utc)
        db.commit()
    return style


def deactivate_style(db, scope: str = "global", project_id=None, course_id=None):
    """Remove active style for the given scope."""
    if scope == "course" and course_id:
        course = db.query(Course).filter(Course.id == course_id).first()
        if course:
            course.active_style_id = None
            db.commit()
    elif scope == "project" and project_id:
        proj = db.query(Project).filter(Project.id == project_id).first()
        if proj:
            proj.active_style_id = None
            db.commit()
    else:
        db.query(Style).update({Style.is_active: False})
        db.commit()


def _build_unified_style_docs(db, style: "Style") -> str:
    """
    Combine all documents linked to a style into a single unified text block.
    Passed as one context to the LLM — no file-by-file processing.
    """
    import re as _re
    parts = []
    if style.custom_instructions and style.custom_instructions.strip():
        visible_instructions = _re.sub(r"<!--\s*DIS_SOURCE_DOCUMENT_IDS=.*?-->", "", style.custom_instructions, flags=_re.S).strip()
        if visible_instructions:
            parts.append(f"[CUSTOM INSTRUCTIONS]\n{visible_instructions}")
    for sd in style.style_documents:
        doc = sd.document
        if doc and doc.content:
            parts.append(
                f"[DOCUMENT: {doc.filename}]\n"
                f"{doc.content[:6000]}"
                + ("...[truncated]" if len(doc.content) > 6000 else "")
            )
    return "\n\n---\n\n".join(parts)


def get_cluster_prompts(db, cluster_id: int) -> list:
    """Return all active ClusterPrompts for *cluster_id*, ordered by creation date."""
    return (
        db.query(ClusterPrompt)
        .filter(ClusterPrompt.cluster_id == cluster_id, ClusterPrompt.is_active == True)  # noqa: E712
        .order_by(ClusterPrompt.created_at.asc())
        .all()
    )


def create_cluster_prompt(
    db,
    cluster_id: int | None,
    name: str,
    description: str | None,
    system_prompt: str | None,
    user_prompt_template: str | None,
    created_by: str,
) -> "ClusterPrompt":
    """Create and persist a new ClusterPrompt."""
    cp = ClusterPrompt(
        cluster_id=cluster_id,
        name=name,
        description=description,
        system_prompt=system_prompt,
        user_prompt_template=user_prompt_template,
        created_by=created_by,
        is_active=True,
    )
    db.add(cp)
    db.commit()
    db.refresh(cp)
    return cp


def build_style_context(db, style: "Style", cluster_id: int | None = None) -> str:
    """
    Assemble the style context string for injection into LLM generation prompts.

    Injection order:
      1. Auto-injected cluster prompts (when cluster_id is provided)
      2. Active style intelligence layer / docs / instructions

    Backward-compatible: cluster_id defaults to None, preserving existing call sites.
    """
    parts: list[str] = []

    # 1. Auto-injected cluster prompts
    if cluster_id:
        for cp in get_cluster_prompts(db, cluster_id):
            content = cp.system_prompt or cp.user_prompt_template or ""
            if content:
                parts.append(f"## Cluster Prompt: {cp.name}\n{content[:3000]}")

    if not style:
        return "\n\n".join(parts)

    # 2. Active style
    parts.append(f"## Active Instructional Style: {style.name}")
    if style.generated_summary:
        parts.append(f"### Style Intelligence Layer (validated understanding)\n{style.generated_summary}")
    else:
        if style.custom_instructions:
            parts.append(f"### Custom Instructions\n{style.custom_instructions}")
        for sd in style.style_documents:
            doc = sd.document
            if doc and doc.content:
                parts.append(
                    f"### Style Reference: {doc.filename}\n"
                    f"{doc.content[:4000]}"
                    + ("..." if len(doc.content) > 4000 else "")
                )
    return "\n\n".join(parts)


# =============================================================================
# Document & Block Helpers
# =============================================================================

def resolve_document_references(db, prompt_text: str) -> tuple:
    """
    Scan a prompt for backtick-quoted filenames referencing context database documents.
    E.g. "Generate a summary based on `Instructional_Design_Spec_v2.pdf`"

    Returns:
        (resolved_context: str, referenced_filenames: list[str])
        resolved_context is a formatted block of all matched document contents.
        referenced_filenames is the list of filenames that were found and resolved.
    """
    # Match `filename.ext` patterns in the prompt
    pattern = re.compile(r"`([^`]+\.[a-zA-Z]{2,5})`")
    matches = pattern.findall(prompt_text)
    if not matches:
        return "", []

    resolved_parts = []
    found_names = []
    for fname in matches:
        # Case-insensitive filename match against active documents
        doc = db.query(Document).filter(
            Document.filename.ilike(fname),
            Document.status == "active"
        ).first()
        if doc:
            resolved_parts.append(
                f"\n\n[CONTEXT DATABASE — {doc.filename} ({doc.doc_tag or 'general'}, "
                f"uploaded {doc.uploaded_at.strftime('%Y-%m-%d')})]\n"
                f"{doc.content[:8000]}\n"
                f"[END CONTEXT — {doc.filename}]"
            )
            found_names.append(doc.filename)

    return "\n".join(resolved_parts), found_names


def search_blocks(db, query: str):
    """Search across all generated blocks by content or label."""
    query_lower = f"%{query.lower()}%"
    return db.query(Block).filter(
        (Block.content.ilike(query_lower)) | (Block.block_label.ilike(query_lower))
    ).all()

def clone_block(db, block_id: int):
    """Clone an existing block into a new draft copy."""
    original = db.query(Block).filter(Block.id == block_id).first()
    if not original:
        return None
    new_block = Block(
        generation_id=original.generation_id,
        block_type=original.block_type,
        block_label=f"[Copy] {original.block_label}",
        content=original.content,
        workflow_state="draft"
    )
    db.add(new_block)
    db.commit()
    db.refresh(new_block)
    return new_block


# =============================================================================
# Workflow State Machine
# =============================================================================

TRANSITIONS = {
    "draft":               {"submit_for_review": "in_review"},
    "in_review":           {"approve":           "approved",
                            "reject":            "rejected",
                            "request_changes":   "changes_requested"},
    "changes_requested":   {"submit_for_review": "in_review",
                            "reset_to_draft":    "draft"},
    "approved":            {"publish":           "published",
                            "reset_to_draft":    "draft",
                            "archive":           "archived"},
    "published":           {"archive":           "archived"},
    "archived":            {},
    "rejected":            {"reset_to_draft":    "draft"},
}

# Statuses that allow content to be exported (without admin override)
EXPORTABLE_STATES = {"approved", "published"}

def apply_transition_local(db, block, action: str, actor: str):
    nxt = TRANSITIONS.get(block.workflow_state.lower(), {}).get(action)
    if not nxt: raise ValueError(f"Invalid transition from {block.workflow_state}")
    ev = WorkflowEvent(block_id=block.id, from_state=block.workflow_state, to_state=nxt, action=action, actor=actor)
    block.workflow_state = nxt; block.updated_at = datetime.now(timezone.utc)
    db.add(ev); db.commit(); db.refresh(block)
    return block


# =============================================================================
# Project / Course Query Helpers
# =============================================================================

def _get_user_projects(db, username: str, role: str):
    """Return projects accessible to this user. Admin sees all active projects."""
    if role == "admin":
        return db.query(Project).filter(Project.is_active == True).order_by(Project.created_at.desc()).all()
    assigned = db.query(ProjectUserAssignment).filter(ProjectUserAssignment.username == username).all()
    proj_ids = [a.project_id for a in assigned]
    if not proj_ids:
        return []
    return db.query(Project).filter(Project.id.in_(proj_ids), Project.is_active == True).order_by(Project.created_at.desc()).all()


# =============================================================================
# Seed Data & Startup
# =============================================================================

def seed_data(db):
    """Seed initial data if tables are empty."""
    # ── Users: 1 Admin + 10 Normal (5 author / 5 reviewer) ───────────────────
    if not db.query(User).first():
        _seed_users = [
            # Admin (Director)
            ("admin",      "admin123",      "admin"),
            # Authors / ID (Normal users — development access)
            ("author1",    "author123",     "author"),
            ("author2",    "author456",     "author"),
            ("author3",    "author789",     "author"),
            ("author4",    "author321",     "author"),
            ("author5",    "author654",     "author"),
            # Reviewers / Lead (Normal users — review access)
            ("reviewer1",  "review123",     "reviewer"),
            ("reviewer2",  "review456",     "reviewer"),
            ("reviewer3",  "review789",     "reviewer"),
            ("reviewer4",  "review321",     "reviewer"),
            ("reviewer5",  "review654",     "reviewer"),
        ]
        for uname, pwd, role in _seed_users:
            db.add(User(
                username=uname,
                password_hash=hash_password(pwd),
                role=role,
                permissions=json.dumps([]),
                is_active=True
            ))
        db.commit()

    # ── Default Prompt & Versions ─────────────────────────────────────────────
    if not db.query(Prompt).first():
        p = Prompt(name="lesson_generator", description="Generates detailed eLearning lessons.", owner="admin", active_version="v1", tags="core,lesson")
        p.tag_rows = [PromptTag(tag="core"), PromptTag(tag="lesson")]
        db.add(p); db.commit(); db.refresh(p)
        v1 = PromptVersion(
            prompt_id=p.id, version="v1", version_number=1, is_active=True,
            system_prompt=SEED_PROMPT_V1_SYSTEM,
            user_prompt_template=SEED_PROMPT_V1_USER,
            change_reason="Initial release"
        )
        v2 = PromptVersion(
            prompt_id=p.id, version="v2", version_number=2, is_active=False,
            system_prompt=SEED_PROMPT_V2_SYSTEM,
            user_prompt_template=SEED_PROMPT_V2_USER,
            change_reason="Enhanced engagement version"
        )
        db.add(v1); db.add(v2); db.commit()

    # ── Default component prompt assets ──────────────────────────────────────
    seed_default_component_prompts(db)
    # ── Shared prompt fragments (Phase 9) ─────────────────────────────────────
    seed_prompt_fragments(db)
    db.commit()


def seed_prompt_fragments(db) -> list[str]:
    """Seed the fragment rows migrated from legacy constants (idempotent).

    persona_tone carries PERSONA_PREFIX_TEMPLATE ({single}->{{double}}
    converted — the fragment tier renders with the {{double}} engine) and
    style_guide carries DEFAULT_STYLE_GUIDE verbatim (no placeholders).
    The constants themselves stay in prompt_templates.py as the code
    fallback tier. Skips keys that already exist; flushes, caller commits.
    Returns the keys created.
    """
    from promptops_app.prompts.brace_conversion import convert_legacy_braces

    _persona, _ = convert_legacy_braces(PERSONA_PREFIX_TEMPLATE)
    _FRAGMENT_SEEDS = [
        (
            "persona_tone",
            "Persona prefix injected ahead of generation user prompts "
            "(migrated from PERSONA_PREFIX_TEMPLATE).",
            _persona,
        ),
        (
            "style_guide",
            "Fallback instructional style guide "
            "(migrated from DEFAULT_STYLE_GUIDE).",
            DEFAULT_STYLE_GUIDE.strip() + "\n",
        ),
    ]

    created: list[str] = []
    for _f_key, _f_desc, _f_content in _FRAGMENT_SEEDS:
        if db.query(PromptFragment).filter(
            PromptFragment.fragment_key == _f_key
        ).first():
            continue
        fragment = PromptFragment(
            fragment_key=_f_key,
            description=_f_desc,
            active_version="v1",
            created_by="system",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(fragment); db.flush()
        db.add(PromptFragmentVersion(
            fragment_id=fragment.id,
            version="v1",
            version_number=1,
            content=_f_content,
            change_reason="System-seeded — migrated from hardcoded constant.",
            is_active=True,
            created_by="system",
            created_at=datetime.utcnow(),
        ))
        db.flush()
        created.append(_f_key)
    return created


def seed_default_component_prompts(db) -> list[str]:
    """Seed the one ``is_default`` prompt row per taxonomy line (idempotent).

    Skips any name that already exists, so it is safe on every call — fresh
    databases get the full set, existing databases only the rows they lack
    (``scripts/seed_taxonomy_defaults.py`` applies it to a running DB).
    Flushes but does NOT commit — the caller owns the transaction (that is
    what lets the seeding script offer a real --dry-run). Returns the names
    created.

    The legacy constants are {single}-brace (.format) text and stay unchanged
    as the inline-fallback tier; the SEEDED rows are what component-keyed
    resolution (PROMPT_RESOLVE_BY_COMPONENT) renders with the {{double}}-brace
    engine, so bodies are converted at seed time. quiz_generation.md is already
    {{double}} and is seeded verbatim.

    Deliberately NOT seeded (Phase 8 decision): (generate, interactive) — the
    bespoke builder routes ~10 label-specific prompts and one generic row would
    degrade them all; Cluster/Course lines — no endpoint resolves them yet.
    """
    from promptops_app.prompts.brace_conversion import convert_legacy_braces

    _STYLE_DEFAULT_SYSTEM = (
        "You are an expert instructional style consultant. "
        "Analyse the provided style guidelines and documents, then apply the defined "
        "tone, vocabulary, structure, and formatting rules consistently across all "
        "content generation tasks for this course."
    )
    _STYLE_DEFAULT_USER = (
        "Apply the following style guidelines to all content generated for this course:\n\n"
        "{style_context}\n\n"
        "Ensure every piece of content respects the tone, vocabulary, structural requirements, "
        "and formatting conventions defined above."
    )

    # The quiz default is the shipped file template (the tier serving quiz
    # today) — seeding it changes nothing except giving the console a slot.
    from promptops_app.prompts.prompt_loader import _TEMPLATE_DIR, _split
    _quiz_sys, _quiz_usr = _split(
        (_TEMPLATE_DIR / "quiz_generation.md").read_text(encoding="utf-8")
    )

    # (name, component_type, variant, description, system, user, needs_conversion)
    _DEFAULT_COMPONENT_PROMPTS = [
        (
            "default_style_prompt", "style", None,
            "Default Style Prompt — applied when generating style-guided content.",
            _STYLE_DEFAULT_SYSTEM, _STYLE_DEFAULT_USER, True,
        ),
        (
            "default_cdd_prompt", "cdd", None,
            "Default CDD Prompt — generates Course Design Documents.",
            CDD_SYSTEM_PROMPT, CDD_USER_PROMPT_TEMPLATE, True,
        ),
        (
            "default_blueprint_prompt", "blueprint", None,
            "Default Blueprint Prompt — generates Module Blueprints from a CDD.",
            BLUEPRINT_SYSTEM_PROMPT, BLUEPRINT_USER_PROMPT_TEMPLATE, True,
        ),
        (
            "default_generate_prompt", "generate", None,
            "Default Generate Prompt — generates lesson and course component content.",
            LESSON_WITH_CONTEXT_SYSTEM, LESSON_WITH_CONTEXT_USER, True,
        ),
        # Variant defaults (Phase 8 taxonomy seeds, signed off 2026-07-07):
        # exact (component, variant) rows win over the NULL-variant fallback,
        # so each blueprint mode becomes independently versionable while the
        # seeded text keeps live output identical to the legacy constants.
        (
            "default_blueprint_teacher_prompt", "blueprint", "teacher",
            "Default Teacher Blueprint Prompt — teacher-facing module blueprints.",
            TEACHER_BLUEPRINT_SYSTEM_PROMPT, TEACHER_BLUEPRINT_USER_PROMPT_TEMPLATE, True,
        ),
        (
            "default_blueprint_student_prompt", "blueprint", "student",
            "Default Student Blueprint Prompt — student-facing module blueprints.",
            BLUEPRINT_SYSTEM_PROMPT, BLUEPRINT_USER_PROMPT_TEMPLATE, True,
        ),
        (
            "default_quiz_prompt", "quiz", None,
            "Default Quiz Prompt — generates quiz and assessment content.",
            _quiz_sys, _quiz_usr, False,
        ),
    ]

    created: list[str] = []
    for _p_name, _p_comp, _p_variant, _p_desc, _p_sys, _p_usr, _p_conv in _DEFAULT_COMPONENT_PROMPTS:
        if _p_conv:
            _p_sys, _ = convert_legacy_braces(_p_sys)
            _p_usr, _ = convert_legacy_braces(_p_usr)
        if not db.query(Prompt).filter(Prompt.name == _p_name).first():
            _p_new = Prompt(
                name=_p_name,
                description=_p_desc,
                owner="system",
                active_version="v1",
                tags=_p_comp,
                component_type=_p_comp,
                variant=_p_variant,
                is_default=True,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            _p_new.tag_rows = [PromptTag(tag=_p_comp)]
            db.add(_p_new); db.flush()
            db.add(PromptVersion(
                prompt_id=_p_new.id,
                version="v1",
                version_number=1,
                system_prompt=_p_sys,
                user_prompt_template=_p_usr,
                change_reason="System-seeded default — converted from hardcoded prompt.",
                is_active=True,
                created_by="system",
            ))
            db.flush()
            created.append(_p_name)
    return created


def init_db_with_seed():
    init_db()
    with SessionLocal() as db:
        seed_data(db)
