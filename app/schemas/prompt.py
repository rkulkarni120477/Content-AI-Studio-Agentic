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
        description="Pipeline component: style, cdd, blueprint, or generate.",
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

    model_config = ConfigDict(from_attributes=True)


class PromptRead(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    tags: Optional[str] = None
    owner: Optional[str] = None
    active_version: Optional[str] = None
    component_type: Optional[str] = None
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
    is_default: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class PromptDeployResponse(BaseModel):
    prompt_id: int
    active_version: str
