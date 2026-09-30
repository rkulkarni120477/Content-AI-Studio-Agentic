"""CE Review checklist schemas (Step 1).

A checklist is uploaded once per client (tenant), split by the LLM into rules,
and later used to review generated lessons. These schemas cover the checklist
management surface only — the review run itself arrives in later steps.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ReviewChecklistItemRead(BaseModel):
    """One rule in a checklist, as shown in the Manage-rules editor."""

    id: int
    item_key: str
    section: Optional[str] = None
    rule_text: str
    is_mandatory: bool = False
    applies_to: Optional[List[str]] = None      # block types this rule targets; null = all
    guidance: Optional[str] = None
    position: int = 0

    model_config = ConfigDict(from_attributes=True)


class ReviewChecklistSummary(BaseModel):
    """Checklist header without its rules — used in list responses."""

    id: int
    project_id: int
    name: str
    version: int
    status: str                                 # active | archived
    item_count: int = 0
    source_document_id: Optional[int] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class ReviewChecklistRead(ReviewChecklistSummary):
    """A checklist with its rules, newest-active first."""

    items: List[ReviewChecklistItemRead] = Field(default_factory=list)


class ChecklistItemUpdateRequest(BaseModel):
    """Edit one rule. Every field is optional — only provided fields change."""

    rule_text: Optional[str] = Field(default=None, min_length=1, max_length=4000)
    section: Optional[str] = Field(default=None, max_length=255)
    guidance: Optional[str] = None
    is_mandatory: Optional[bool] = None
    applies_to: Optional[List[str]] = None
    position: Optional[int] = Field(default=None, ge=0)


class ChecklistItemIdsRequest(BaseModel):
    """Rule ids to act on (e.g. bulk delete)."""

    item_ids: List[int] = Field(..., min_length=1)


class DeleteResult(BaseModel):
    deleted: int = 0
