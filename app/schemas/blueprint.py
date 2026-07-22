"""Module Blueprint schemas — mirrors the CDD schema pattern."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
    extra_instructions: str = Field(default="", max_length=5000)
    style_id: Optional[int] = None
    model_choice: str = Field(default="GPT-5.4")
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
    model_choice: str = Field(default="GPT-5.4")


class BlueprintRegenerateItemResponse(BaseModel):
    """New section content with only the targeted item replaced."""

    updated_content: str
    patched_item: str


class BlueprintRegenerateSectionRequest(BaseModel):
    """Body for POST /blueprints/{id}/regenerate-section — regenerate a section."""

    section_key: str = Field(description="Section to regenerate, e.g. 'Learning Objectives'.")
    feedback: str = Field(default="", max_length=2000, description="Optional regeneration instruction.")
    model_choice: str = Field(default="GPT-5.4")
    teacher_mode: bool = Field(default=False, description="Use teacher-facing prompts when true.")


class BlueprintRegenerateSectionResponse(BaseModel):
    """Freshly generated content for the section."""

    updated_content: str


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
    id: int
    title: str
    module_number: Optional[int] = None
    active_version: Optional[str] = None
    created_at: Optional[datetime] = None

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
