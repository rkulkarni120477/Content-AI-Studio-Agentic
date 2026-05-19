"""Instructional Style schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class StyleCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=300, examples=["Clinical Formal"])
    description: Optional[str] = Field(default=None, max_length=2000)


class StyleUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=300)
    description: Optional[str] = Field(default=None, max_length=2000)


class StyleActivateRequest(BaseModel):
    """Body for POST /api/v1/styles/{id}/activate."""

    project_id: Optional[int] = None
    course_id: Optional[int] = None


class StyleUnderstandRequest(BaseModel):
    """Body for POST /api/v1/styles/{id}/understand — trigger AI intelligence generation."""

    model_choice: str = Field(default="GPT-5.4")
    extra_instructions: str = Field(default="", max_length=3000)
    system_prompt_override: Optional[str] = None


class StyleUnderstandResponse(BaseModel):
    style_id: int
    understanding: str = Field(description="AI-generated style intelligence text.")
    model_used: str
    tokens_used: Optional[int] = None


class StyleDocumentUploadResponse(BaseModel):
    uploaded: list[str] = Field(description="Filenames successfully parsed and saved.")
    errors: list[str] = Field(default_factory=list)


class StyleRead(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    is_active: bool = False
    understanding: Optional[str] = Field(
        default=None,
        description="AI-generated style intelligence. May be long.",
    )
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class StyleListItem(BaseModel):
    """Lightweight style summary — understanding is truncated to 200 chars."""

    id: int
    name: str
    is_active: bool = False
    understanding_preview: Optional[str] = Field(
        default=None,
        description="First 200 characters of the style intelligence.",
    )
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
