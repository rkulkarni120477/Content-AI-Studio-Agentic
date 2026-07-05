"""Prompt template registry schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class PromptCreateRequest(BaseModel):
    """Body for POST /api/v1/prompts — create asset; optional v1 when prompts are supplied."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Unique identifier slug, e.g. 'adaptive_tutor_v1'.",
        examples=["quiz_builder_clinical"],
    )
    description: str = Field(default="", max_length=2000)
    tags: str = Field(default="", max_length=500, description="Comma-separated tags.")
    component_type: Optional[str] = Field(
        default=None,
        description="Pipeline component: style, cdd, blueprint, generate, or quiz.",
    )
    variant: Optional[str] = Field(
        default=None,
        max_length=50,
        description="Pipeline variant within the component, e.g. teacher/student "
                    "(blueprint) or interactive (generate). NULL = the component's "
                    "base slot.",
    )
    system_prompt: Optional[str] = Field(default=None, description="Initial system prompt (creates v1).")
    user_prompt_template: Optional[str] = Field(default=None, description="Initial user template (creates v1).")
    change_reason: str = Field(default="Initial commit.", max_length=500)


class PromptCreateFromTemplateRequest(BaseModel):
    """Body for POST /api/v1/prompts/from-template."""

    template_name: str = Field(
        ...,
        description="Key from the PROMPT_TEMPLATES dict in prompt_templates.py.",
    )


class PromptAIGenerateRequest(BaseModel):
    """Body for POST /api/v1/prompts/generate — AI-generate a prompt from a description."""

    description: str = Field(
        ...,
        min_length=10,
        max_length=2000,
        description="Plain-language description of the prompt's purpose.",
        examples=["I need a prompt that creates interactive coding exercises with hints."],
    )
    model_choice: str = Field(default="GPT-5.4")


class PromptAISuggestResponse(BaseModel):
    """AI suggestion preview — does not persist to the registry."""

    system_prompt: str
    user_prompt_template: str
    description: Optional[str] = None


class PromptUpdateRequest(BaseModel):
    description: Optional[str] = Field(default=None, max_length=2000)
    tags: Optional[str] = Field(default=None, max_length=500)
    # Re-keying fields — change what generation resolves, so the endpoint
    # accepts them only from prompt.pipeline.edit holders (403 otherwise).
    # Omit/None = untouched; empty string = clear to NULL.
    component_type: Optional[str] = Field(default=None, max_length=50)
    variant: Optional[str] = Field(default=None, max_length=50)


class PromptVersionCreateRequest(BaseModel):
    """Body for POST /api/v1/prompts/{id}/versions — commit a new version."""

    version: str = Field(..., min_length=1, max_length=50, examples=["v3"])
    system_prompt: str = Field(..., min_length=1)
    user_prompt_template: str = Field(..., min_length=1)
    change_reason: str = Field(default="", max_length=500)


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class PromptVersionRead(BaseModel):
    id: int
    version: str
    is_active: bool
    system_prompt: Optional[str] = None
    user_prompt_template: Optional[str] = None
    change_reason: Optional[str] = None
    created_at: Optional[datetime] = None
    workflow_state: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class PromptVersionListItem(BaseModel):
    id: int
    version: str
    is_active: bool
    change_reason: Optional[str] = None
    created_at: Optional[datetime] = None
    created_by: Optional[str] = None
    system_prompt: Optional[str] = None
    user_prompt_template: Optional[str] = None
    workflow_state: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class PromptRead(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    tags: Optional[str] = None
    owner: Optional[str] = None
    active_version: Optional[str] = None
    component_type: Optional[str] = None
    variant: Optional[str] = None
    is_default: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class PromptDetailRead(PromptRead):
    """Prompt metadata plus the active version's prompt text."""

    system_prompt: Optional[str] = None
    user_prompt_template: Optional[str] = None
    version_created_by: Optional[str] = None
    version_created_at: Optional[datetime] = None
    version_change_reason: Optional[str] = None


class PromptListItem(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    tags: Optional[str] = None
    owner: Optional[str] = None
    active_version: Optional[str] = None
    component_type: Optional[str] = None
    variant: Optional[str] = None
    is_default: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class PromptDeployResponse(BaseModel):
    prompt_id: int
    active_version: str


# ---------------------------------------------------------------------------
# Pipeline management (Phase 8) — default flag, workflow state, scope locks
# ---------------------------------------------------------------------------

class PromptDefaultRequest(BaseModel):
    """Body for PUT /api/v1/prompts/{id}/default."""

    is_default: bool = Field(
        default=True,
        description="True → make this row the default for its (component_type, "
                    "variant), demoting any current default. False → clear the flag.",
    )


class PromptWorkflowStateRequest(BaseModel):
    """Body for POST /api/v1/prompts/{id}/versions/{version}/state."""

    state: str = Field(
        ...,
        description="Target workflow state: draft, in_review, approved, or active. "
                    "Transitioning to 'active' deploys the version.",
        examples=["in_review"],
    )


class PromptFixingSetRequest(BaseModel):
    """Body for PUT /api/v1/prompts/fixings — bind a prompt to a scope."""

    component: str = Field(..., description="Pipeline component: style, cdd, blueprint, or generate.")
    scope_level: str = Field(..., description="global, project, cluster, or course.")
    project_id: Optional[int] = None
    cluster_id: Optional[int] = None
    course_id: Optional[int] = None
    prompt_id: int = Field(..., description="The pipeline prompt to bind (by reference).")


class PromptFixingRead(BaseModel):
    id: int
    component: str
    scope_level: str
    project_id: Optional[int] = None
    cluster_id: Optional[int] = None
    course_id: Optional[int] = None
    prompt_id: Optional[int] = None
    fixed_by: Optional[str] = None
    fixed_by_role: Optional[str] = None
    fixed_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
