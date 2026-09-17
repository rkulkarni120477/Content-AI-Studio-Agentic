"""Cluster Prompt schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ClusterPromptCreateRequest(BaseModel):
    cluster_id: Optional[int] = Field(
        default=None,
        description="Cluster to assign this prompt to. Omit/null to save unassigned (library prompt).",
    )
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=2000)
    system_prompt: Optional[str] = Field(default=None)
    user_prompt_template: Optional[str] = Field(default=None)


class ClusterPromptRead(BaseModel):
    id: int
    cluster_id: Optional[int] = None
    name: str
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    user_prompt_template: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    is_active: bool = True

    model_config = ConfigDict(from_attributes=True)


class ClusterPromptAIGenerateRequest(BaseModel):
    mode: str = Field(
        default="generate",
        description="'generate' for a fresh prompt, 'refine' to improve an existing draft.",
    )
    context: str = Field(..., min_length=1, description="Context / instructions for the AI.")
    draft_system_prompt: Optional[str] = Field(default=None)
    draft_user_prompt_template: Optional[str] = Field(default=None)
    model_choice: str = Field(default="GPT-5.6 Terra")


class ClusterPromptAIGenerateResponse(BaseModel):
    system_prompt: str = ""
    user_prompt_template: str = ""
    fallback_used: bool = Field(
        default=False,
        description="True if the LLM did not return valid JSON and the raw text was used as the system prompt.",
    )
