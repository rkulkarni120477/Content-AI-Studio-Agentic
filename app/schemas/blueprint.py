"""Module Blueprint schemas — mirrors the CDD schema pattern."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.archive import DocumentReferences
from app.schemas.budget import UsageSummary
from app.schemas.json_fields import parse_optional_json_dict


def _coerce_module_number(value: object) -> Optional[int]:
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        digits = "".join(ch for ch in value.strip() if ch.isdigit())
        return int(digits) if digits else None
    return None


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class BlueprintGenerateRequest(BaseModel):
    """All parameters needed to generate a Module Blueprint with AI."""

    course_id: int
    project_id: int
    cdd_id: Optional[int] = Field(
        default=None,
        description="CDD to inject as context. If None, the pinned CDD for the course is used.",
    )
    selected_module: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Module name/number to generate the blueprint for.",
        examples=["Module 2 — Pharmacology Basics"],
    )
    document_title: Optional[str] = Field(
        default=None,
        max_length=500,
        description=(
            "Optional label for this blueprint document. Blank → "
            "'<selected_module> Blueprint'. Stored on the row as-is, so a day "
            "blueprint given a title without a 'Day N' prefix falls back to its "
            "module_number for the Generate page's day dropdown."
        ),
        examples=["Day 4 — Exploded Views and Assembly Diagrams"],
    )
    extra_instructions: str = Field(default="", max_length=5000)
    style_id: Optional[int] = None
    model_choice: str = Field(default="GPT-5.6 Terra")
    teacher_mode: bool = Field(
        default=False,
        description="If True, uses the teacher-facing prompt variant.",
    )
    is_course_end: bool = Field(
        default=False,
        description=(
            "True when selected_module is a course-level end item (e.g. a capstone) "
            "rather than a numbered module. Persisted as module_number=0 — a sentinel "
            "distinguishing it from real modules, which are always >= 1."
        ),
    )
    prompt_id: Optional[int] = Field(
        default=None,
        description=(
            "Id of the pipeline prompt selected in the 'Prompt Template' dropdown. "
            "When set (and not a system default), that prompt's active version drives "
            "generation instead of the scope/component-default resolution."
        ),
    )
    system_prompt_override: Optional[str] = None
    user_prompt_override: Optional[str] = None

    # ── Digest pipeline (block-wide Blueprint) ────────────────────────────────
    block: Optional[str] = Field(
        default=None,
        description=(
            "Block label (e.g. 'Block 2') for the block-wide digest Blueprint. "
            "When set and the digest pipeline is enabled for the course's client, a "
            "day-by-day Block Blueprint is produced from source digests (selected_module "
            "is ignored). When omitted the legacy module-blueprint path runs unchanged."
        ),
        examples=["Block 2"],
    )
    quality_tier: Optional[str] = Field(
        default=None,
        description="Quality tier for the block-wide REDUCE model: 'draft' | 'standard' | 'premium'.",
        examples=["standard", "premium"],
    )
    day_number: Optional[int] = Field(
        default=None,
        ge=1,
        description=(
            "Day number within the block, when selected_module refers to a Day (not a "
            "Module). Enriches this single-item generation's grounding with the digest "
            "pipeline's day-scoped context (topic, ACS codes, source units). Does not "
            "change the output shape and does not engage the whole-block digest "
            "pipeline — that path is driven by 'block' alone and ignores this field."
        ),
        examples=[3],
    )


class BlueprintVersionCreateRequest(BaseModel):
    """Commit manually edited blueprint content as a new version."""

    version_tag: str = Field(..., min_length=1, max_length=50, examples=["v2"])
    full_content: str = Field(..., min_length=1)
    sections: dict = Field(default_factory=dict)
    change_reason: str = Field(default="", max_length=500)


class BlueprintRegenerateItemRequest(BaseModel):
    """Body for POST /blueprints/{id}/regenerate-item — regenerate one item."""

    section_key: str = Field(description="Section the item belongs to.")
    section_content: str = Field(description="Current markdown content of that section.")
    item_index: int = Field(..., ge=0, description="Index of the item to regenerate.")
    feedback: str = Field(default="", max_length=2000, description="Optional regeneration instruction.")
    model_choice: str = Field(default="GPT-5.6 Terra")


class BlueprintRegenerateItemResponse(BaseModel):
    """New section content with only the targeted item replaced."""

    updated_content: str
    patched_item: str
    usage_summary: Optional[UsageSummary] = None


class BlueprintRegenerateSectionRequest(BaseModel):
    """Body for POST /blueprints/{id}/regenerate-section — regenerate a section."""

    section_key: str = Field(description="Section to regenerate, e.g. 'Learning Objectives'.")
    section_content: str = Field(
        default="",
        description=(
            "Current markdown of that section — the text being revised. Without it the "
            "model never sees what it is regenerating and drafts a replacement from the "
            "title and the CDD summary alone, which is how a Day 20 exam blueprint "
            "(ACS tables, open items) came back as generic 'Lesson 1/2/3' filler. Blank "
            "keeps the old draft-from-scratch behaviour for callers that cannot supply it."
        ),
    )
    feedback: str = Field(default="", max_length=2000, description="Optional regeneration instruction.")
    model_choice: str = Field(default="GPT-5.6 Terra")
    teacher_mode: bool = Field(default=False, description="Use teacher-facing prompts when true.")


class BlueprintRegenerateSectionResponse(BaseModel):
    """Freshly generated content for the section."""

    updated_content: str
    usage_summary: Optional[UsageSummary] = None


class BlueprintPinRequest(BaseModel):
    """Pin a blueprint as active for generation on a course."""

    course_id: int


# ---------------------------------------------------------------------------
# Component — the parsed unit from a blueprint (lesson, assessment, etc.)
# ---------------------------------------------------------------------------

class BlueprintComponent(BaseModel):
    """A single parseable component from the blueprint (used in the Generate dropdown)."""

    label: str = Field(description="Display name shown in the Content Type dropdown.")
    value: str = Field(description="Slug used internally to identify the component type.")
    type: str = Field(description="Component category: lesson | assessment | activity")
    metadata: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class BlueprintVersionRead(BaseModel):
    version: str
    is_active: bool
    full_content: Optional[str] = None
    sections: Optional[dict] = None
    change_reason: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

    @field_validator("sections", mode="before")
    @classmethod
    def _coerce_sections(cls, value: object) -> Optional[dict]:
        return parse_optional_json_dict(value)


class BlueprintVersionListItem(BaseModel):
    version: str
    is_active: bool
    change_reason: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class BlueprintRead(BaseModel):
    id: int
    title: str
    module_number: Optional[int] = None
    active_version: Optional[str] = None
    project_id: Optional[int] = None
    course_id: Optional[int] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    active_content: Optional[BlueprintVersionRead] = None

    model_config = ConfigDict(from_attributes=True)

    @field_validator("module_number", mode="before")
    @classmethod
    def _coerce_module_number_field(cls, value: object) -> Optional[int]:
        return _coerce_module_number(value)


class BlueprintListItem(BaseModel):
    """One row of the blueprint picker.

    ``created_by`` and ``references.version_count`` are here for the same reason
    as on ``CDDListItem``: titles alone do not identify a row when a course holds
    twenty blueprints generated from the same module.
    """

    id: int
    title: str
    module_number: Optional[int] = None
    active_version: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None

    # ── Archive state ────────────────────────────────────────────────────────
    is_archived: bool = False
    deleted_at: Optional[datetime] = None
    deleted_by: Optional[str] = None

    # What points at this blueprint — populated in one batched pass per page.
    references: DocumentReferences = Field(default_factory=DocumentReferences)

    model_config = ConfigDict(from_attributes=True)

    @field_validator("module_number", mode="before")
    @classmethod
    def _coerce_module_number_field(cls, value: object) -> Optional[int]:
        return _coerce_module_number(value)


class BlueprintGenerateResponse(BaseModel):
    blueprint_id: int
    title: str
    version: str
    sections_count: int
    components: list[BlueprintComponent]
    full_content: str
    model_used: str
    tokens_used: Optional[int] = None
    auto_pinned: bool = True
    #: Set when the Source Library could not be reached, so this Blueprint was
    #: grounded in the CDD and style alone. None means grounding was available —
    #: including the ordinary case of a library with nothing matching to add.
    #: Generation deliberately degrades rather than failing here, so this is the
    #: only thing that distinguishes the two outcomes for the requester.
    source_context_unavailable: Optional[str] = None
    # Set only by the import path when a file could not be fully structured (e.g.
    # imported as one unstructured section); None for a normal generation.
    import_warnings: Optional[list[str]] = None


class OutlineImportJobResponse(BaseModel):
    """202 handle for an async Outline import — the client polls poll_url
    (GET /api/v1/jobs/{job_id}); on completion the job's generation_id is the
    imported blueprint id."""
    job_id: str
    status: str
    poll_url: str


class BlueprintComponentsResponse(BaseModel):
    """Returned by GET /blueprints/{id}/components — used to populate the Generate dropdown."""

    blueprint_id: int
    components: list[BlueprintComponent]


class BlueprintActivateVersionResponse(BaseModel):
    blueprint_id: int
    active_version: str


class BlueprintPinResponse(BaseModel):
    blueprint_id: int
    course_id: int
    pinned: bool = True
