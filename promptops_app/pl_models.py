"""
Prompt Library ORM models — ported from the standalone ``prompt-library`` app.

These models are registered on the SAME ``promptops_app.database.Base`` as the rest
of Content AI Studio, so ``init_db()`` (``Base.metadata.create_all``) creates them
automatically at startup, and they live in the shared Postgres database.

Two deliberate changes from the standalone app:
  1. **Table names are ``pl_``-prefixed** to avoid collisions with existing Content
     AI Studio tables (``prompts``, ``prompt_versions``, ``reviews``, ``users``).
  2. **Multi-tenancy is removed.** Every ``tenant_id`` column/FK and tenant-scoped
     unique constraint from the source models is dropped. Identity (users/roles) is
     owned by the host; ``created_by`` / ``requested_by`` / review ``username`` are
     plain strings referencing host usernames.

Class names are ``PL``-prefixed (``PLPrompt`` etc.) because the host declarative
registry already defines ``Prompt``, ``PromptVersion``, and ``Review``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.types import JSON

from promptops_app.database import Base


def _now():
    return datetime.now(timezone.utc)


def _new_uuid():
    return str(uuid.uuid4())


class PLPrompt(Base):
    __tablename__ = "pl_prompts"
    __table_args__ = (
        Index("ix_pl_prompts_visibility", "visibility"),
        Index("ix_pl_prompts_category", "category"),
        Index("ix_pl_prompts_team_id", "team_id"),
        Index("ix_pl_prompts_deleted_at", "deleted_at"),
        Index("ix_pl_prompts_updated_at", "updated_at"),
    )

    id = Column(String(36), primary_key=True, default=_new_uuid)
    parent_id = Column(
        String(36),
        ForeignKey("pl_prompts.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    title = Column(String(300), nullable=False, index=True)
    content = Column(Text, nullable=False)
    description = Column(Text)
    category = Column(String(100))
    visibility = Column(String(20), nullable=False, default="draft")
    team_id = Column(String(20), ForeignKey("pl_teams.id", ondelete="SET NULL"), nullable=True)
    created_by = Column(String(100))
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    parent = relationship(
        "PLPrompt",
        remote_side="PLPrompt.id",
        foreign_keys=[parent_id],
        back_populates="children",
        lazy="select",
    )
    children = relationship(
        "PLPrompt",
        back_populates="parent",
        foreign_keys=[parent_id],
        lazy="selectin",
    )
    team = relationship("PLTeam", back_populates="prompts", foreign_keys=[team_id], lazy="select")
    prompt_teams = relationship(
        "PLPromptTeam",
        back_populates="prompt",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    tags = relationship("PLPromptTag", back_populates="prompt", cascade="all, delete-orphan", lazy="selectin")
    variables = relationship(
        "PLPromptVariable", back_populates="prompt", cascade="all, delete-orphan",
        order_by="PLPromptVariable.sort_order", lazy="selectin",
    )
    versions = relationship(
        "PLPromptVersion", back_populates="prompt", cascade="all, delete-orphan",
        order_by="PLPromptVersion.version_number", lazy="selectin",
    )
    attachments = relationship("PLAttachment", back_populates="prompt", cascade="all, delete-orphan", lazy="selectin")
    reviews = relationship("PLReview", back_populates="prompt", cascade="all, delete-orphan", lazy="select")
    requests = relationship(
        "PLPromptRequest", back_populates="prompt", foreign_keys="PLPromptRequest.prompt_id", lazy="select",
    )


class PLPromptTeam(Base):
    __tablename__ = "pl_prompt_teams"

    prompt_id = Column(
        String(36),
        ForeignKey("pl_prompts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    team_id = Column(
        String(20),
        ForeignKey("pl_teams.id", ondelete="CASCADE"),
        primary_key=True,
    )

    prompt = relationship("PLPrompt", back_populates="prompt_teams")
    team = relationship("PLTeam", back_populates="prompt_teams")


class PLPromptTag(Base):
    __tablename__ = "pl_prompt_tags"

    prompt_id = Column(String(36), ForeignKey("pl_prompts.id", ondelete="CASCADE"), primary_key=True)
    tag = Column(String(100), primary_key=True)

    prompt = relationship("PLPrompt", back_populates="tags")


class PLPromptVariable(Base):
    __tablename__ = "pl_prompt_variables"

    id = Column(Integer, primary_key=True, autoincrement=True)
    prompt_id = Column(String(36), ForeignKey("pl_prompts.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    label = Column(String(200))
    hint = Column(Text)
    sort_order = Column(Integer, default=0, nullable=False)

    prompt = relationship("PLPrompt", back_populates="variables")


class PLPromptVersion(Base):
    __tablename__ = "pl_prompt_versions"
    __table_args__ = (UniqueConstraint("prompt_id", "version_number", name="uq_pl_prompt_version"),)

    id = Column(String(36), primary_key=True, default=_new_uuid)
    prompt_id = Column(String(36), ForeignKey("pl_prompts.id", ondelete="CASCADE"), nullable=False, index=True)
    version_number = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    note = Column(Text)
    created_by = Column(String(100))
    created_at = Column(DateTime(timezone=True), default=_now)

    prompt = relationship("PLPrompt", back_populates="versions")


class PLAttachment(Base):
    __tablename__ = "pl_attachments"

    id = Column(String(36), primary_key=True, default=_new_uuid)
    prompt_id = Column(String(36), ForeignKey("pl_prompts.id", ondelete="CASCADE"), nullable=False, index=True)
    original_name = Column(String(300))
    stored_name = Column(String(500))
    size_bytes = Column(BigInteger)
    uploaded_by = Column(String(100))
    uploaded_at = Column(DateTime(timezone=True), default=_now)

    prompt = relationship("PLPrompt", back_populates="attachments")


class PLTeam(Base):
    __tablename__ = "pl_teams"
    __table_args__ = (UniqueConstraint("name", name="uq_pl_teams_name"),)

    id = Column(String(20), primary_key=True)
    name = Column(String(100), nullable=False)
    created_at = Column(DateTime(timezone=True), default=_now)
    created_by = Column(String(100))

    prompts = relationship("PLPrompt", back_populates="team", foreign_keys="PLPrompt.team_id", lazy="select")
    prompt_teams = relationship("PLPromptTeam", back_populates="team", lazy="select")


class PLPromptRequest(Base):
    __tablename__ = "pl_prompt_requests"

    id = Column(String(36), primary_key=True, default=_new_uuid)
    title = Column(String(300), nullable=False)
    description = Column(Text)
    type = Column(String(20), nullable=False, default="new")
    prompt_id = Column(String(36), ForeignKey("pl_prompts.id", ondelete="SET NULL"), nullable=True)
    requested_by = Column(String(100), nullable=False)
    status = Column(String(20), nullable=False, default="open", index=True)
    admin_notes = Column(Text)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    prompt = relationship("PLPrompt", back_populates="requests", foreign_keys=[prompt_id])


class PLReview(Base):
    __tablename__ = "pl_reviews"
    __table_args__ = (UniqueConstraint("prompt_id", "username", name="uq_pl_review_prompt_user"),)

    id = Column(String(36), primary_key=True, default=_new_uuid)
    prompt_id = Column(String(36), ForeignKey("pl_prompts.id", ondelete="CASCADE"), nullable=False, index=True)
    username = Column(String(100), nullable=False)
    rating = Column(SmallInteger, nullable=False)
    feedback = Column(Text)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    prompt = relationship("PLPrompt", back_populates="reviews")


class PLAuditEvent(Base):
    __tablename__ = "pl_audit_events"

    id = Column(String(36), primary_key=True, default=_new_uuid)
    event_type = Column(String(64), nullable=False, index=True)
    actor_username = Column(String(100), nullable=True, index=True)
    actor_role = Column(String(64), nullable=True)
    entity_type = Column(String(64), nullable=True, index=True)
    entity_id = Column(String(64), nullable=True, index=True)
    action = Column(String(32), nullable=False)
    summary = Column(Text, nullable=False, default="")
    changes = Column(JSON, nullable=True)
    ip_address = Column(String(64), nullable=True)
    user_agent = Column(String(512), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False, index=True)
