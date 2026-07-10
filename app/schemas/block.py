"""Content block editor schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Block read/list
# ---------------------------------------------------------------------------

class BlockRead(BaseModel):
    """Full block including content — returned by GET /blocks/{id}."""

    id: int
    block_label: str
    content: Optional[str] = None
    workflow_state: str
    block_type: Optional[str] = None
    position: int = 0
    rating: Optional[int] = None
    assigned_reviewer: Optional[str] = None
    submitted_by: Optional[str] = None
    review_requested_at: Optional[datetime] = None
    review_comments: Optional[str] = None
    rejected_reason: Optional[str] = None
    generation_id: Optional[int] = None
    sources: Optional[str] = None
    eval_score: Optional[int] = None
    eval_report: Optional[str] = None
    ai_review: Optional[str] = None
    draft_content: Optional[str] = None
    draft_updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class BlockListItem(BaseModel):
    """Lightweight block — content truncated to 300 chars for list views."""

    id: int
    block_label: str
    content_preview: Optional[str] = Field(
        default=None,
        description="First 300 characters of content.",
    )
    workflow_state: str
    position: int = 0
    rating: Optional[int] = None
    generation_id: Optional[int] = None
    has_html: bool = False

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Edit / save
# ---------------------------------------------------------------------------

class BlockUpdateRequest(BaseModel):
    """Body for PUT /blocks/{id} — save manual edits."""

    content: str = Field(..., min_length=1)
    change_reason: str = Field(default="Manual edit", max_length=500)


class BlockAutosaveRequest(BaseModel):
    """Body for POST /blocks/{id}/autosave — periodic draft save, no version created."""

    content: str = Field(..., min_length=1)


class BlockAutosaveResponse(BaseModel):
    saved: bool
    saved_at: str


# ---------------------------------------------------------------------------
# Regeneration
# ---------------------------------------------------------------------------

class BlockRegenerateRequest(BaseModel):
    """Body for POST /blocks/{id}/regenerate — AI full-block regeneration."""

    model_choice: str = Field(default="GPT-5.4")
    feedback_instruction: str = Field(
        default="",
        max_length=3000,
        description="Instructions guiding how to improve the content.",
        examples=["Make it more interactive with knowledge check questions."],
    )


class BlockRegenerateResponse(BaseModel):
    block_id: int
    content: str
    model_used: str
    tokens_used: Optional[int] = None
    version_created: str


class BlockRegenerateItemRequest(BaseModel):
    """Body for POST /blocks/{id}/regenerate-item — regenerate one item in a block."""

    item_index: int = Field(..., ge=0)
    section_key: str
    feedback: str = Field(default="", max_length=2000)
    model_choice: str = Field(default="GPT-5.4")


class BlockRegenerateItemResponse(BaseModel):
    block_id: int
    updated_content: str
    patched_item: str


# ---------------------------------------------------------------------------
# Version history
# ---------------------------------------------------------------------------

class BlockVersionRead(BaseModel):
    version_id: int
    version_number: Optional[str] = None
    change_source: Optional[str] = None
    content: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class BlockVersionListItem(BaseModel):
    version_id: int
    version_number: Optional[str] = None
    change_source: Optional[str] = None
    change_note: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    word_count: Optional[int] = None
    workflow_state_at_save: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class BlockRestoreResponse(BaseModel):
    block_id: int
    restored_to_version: int
    content: str


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

class BlockSnapshotRequest(BaseModel):
    label: str = Field(
        default="Manual snapshot",
        max_length=200,
        examples=["Before reviewer changes"],
    )


class BlockSnapshotResponse(BaseModel):
    version_id: int
    label: str
    created_at: str


# ---------------------------------------------------------------------------
# Quality and validation
# ---------------------------------------------------------------------------

class BlockScoreResponse(BaseModel):
    block_id: int
    score: int = Field(description="Overall quality score 0-100.")
    feedback: str
    metadata: dict = Field(default_factory=dict)


class ValidationIssue(BaseModel):
    block_id: int
    block_label: str
    errors: list[str]
    warnings: list[str]


class BlockValidateResponse(BaseModel):
    """Returned by POST /blocks/{id}/validate."""

    block_id: int
    passed: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    summary: dict = Field(default_factory=dict)


class GenerationValidateResponse(BaseModel):
    """Returned by POST /generations/{id}/validate — validates all blocks at once."""

    generation_id: int
    passed: bool
    summary: dict
    blocks: list[ValidationIssue] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Rating and plagiarism
# ---------------------------------------------------------------------------

class BlockReorderRequest(BaseModel):
    """Body for PUT /courses/{course_id}/blocks/reorder — TOC display order."""

    block_ids: list[int] = Field(
        ...,
        min_length=1,
        description="Ordered list of block IDs (first = top of TOC).",
    )


class BlockReorderResponse(BaseModel):
    updated: int
    block_ids: list[int]


class BlockCanvasHtmlResponse(BaseModel):
    """Canvas-ready HTML rendition for a single block (preview)."""
    block_id: int
    block_label: str
    has_html: bool
    content_html: Optional[str] = None
    content_html_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Course modules (Canvas-style export grouping)
# ---------------------------------------------------------------------------

class ModuleBlock(BaseModel):
    """A published block as it appears within a module or the unassigned list."""
    id: int
    block_label: str
    position: int = 0
    workflow_state: str
    has_html: bool = False


class CourseModuleRead(BaseModel):
    id: int
    title: str
    position: int = 0
    blocks: list[ModuleBlock] = Field(default_factory=list)


class CourseModulesResponse(BaseModel):
    modules: list[CourseModuleRead] = Field(default_factory=list)
    unassigned: list[ModuleBlock] = Field(default_factory=list)


class CourseModuleCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)


class CourseModuleRenameRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)


class CourseModuleReorderRequest(BaseModel):
    module_ids: list[int] = Field(..., min_length=1)


class ModuleLayoutRow(BaseModel):
    module_id: int
    block_ids: list[int] = Field(default_factory=list)


class ModuleLayoutRequest(BaseModel):
    modules: list[ModuleLayoutRow] = Field(default_factory=list)


class ModuleLayoutResponse(BaseModel):
    updated: int


class BlockRatingRequest(BaseModel):
    rating: int = Field(..., ge=1, le=5)


class PlagiarismTriggerResponse(BaseModel):
    report_id: int
    scan_id: str
    status: str = "pending"
    status_url: str


class PlagiarismStatusResponse(BaseModel):
    report_id: int
    status: str
    similarity_score: Optional[float] = None
    ai_score: Optional[float] = None
    sources: list[dict] = Field(default_factory=list)
