"""Generation job and content block list schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class SupplementaryFile(BaseModel):
    """An uploaded file's parsed text, passed alongside the generation request."""

    name: str
    content: str
    source_type: str = Field(
        description="File role: guidelines | checklist | chapter",
        examples=["guidelines"],
    )


class GenerationLaunchRequest(BaseModel):
    """
    Body for POST /api/v1/generations/launch.

    All fields mirror the values collected by the Generate page form.
    The 'selected_component' fields come from the blueprint parser output.
    """

    course_id: int
    project_id: int
    cdd_id: Optional[int] = Field(
        default=None,
        description="CDD to use. Falls back to the course's active_cdd_id if None.",
    )
    blueprint_id: Optional[int] = Field(
        default=None,
        description="Blueprint to use. Falls back to the course's active_blueprint_id if None.",
    )

    # Selected blueprint component
    component_value: str = Field(
        ...,
        description="Component slug, e.g. 'lesson_1' or 'module_assessment'.",
    )
    component_label: str = Field(..., description="Human-readable label shown in the dropdown.")
    component_type: str = Field(..., description="Component category: lesson | assessment")

    # Prompt template
    prompt_name: str = Field(..., description="Name of the prompt template to use.")

    # Sidebar config
    model_choice: str = Field(default="GPT-5.4")
    target_audience: str = Field(default="")
    expert_domain: str = Field(default="")
    audience_category: str = Field(default="Professional/Corporate")
    extra_instructions: str = Field(default="", max_length=5000)

    # Additional context sources
    context_document_names: list[str] = Field(
        default_factory=list,
        description="Names of library documents to inject as context.",
    )
    supplementary_files: list[SupplementaryFile] = Field(
        default_factory=list,
        description="Uploaded files parsed client-side and sent as text.",
    )

    # Assessment completion override
    assessment_override: bool = Field(
        default=False,
        description=(
            "If True, generate a module assessment even if not all lessons are complete. "
            "User must confirm this in the UI before sending."
        ),
    )


class GenerationBlockSummary(BaseModel):
    """Lightweight block summary embedded in GenerationRead."""

    id: int
    block_label: str
    workflow_state: str
    position: int

    model_config = ConfigDict(from_attributes=True)


class GenerationRead(BaseModel):
    """Full generation with its blocks."""

    id: int
    topic: str
    prompt_name: Optional[str] = None
    prompt_version: Optional[str] = None
    cdd_id: Optional[int] = None
    cdd_version: Optional[str] = None
    blueprint_id: Optional[int] = None
    blueprint_version: Optional[str] = None
    project_id: Optional[int] = None
    course_id: Optional[int] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    blocks: list[GenerationBlockSummary] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class GenerationListItem(BaseModel):
    id: int
    topic: str
    prompt_name: Optional[str] = None
    prompt_version: Optional[str] = None
    block_count: int = 0
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class CompletionStatusResponse(BaseModel):
    """Module or course completion status — used to gate assessment generation."""

    completed: bool
    generated_lessons: int
    total_lessons: int
    missing_lessons: list[str] = Field(default_factory=list)
