"""Instructional Style schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class StyleCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=300, examples=["Clinical Formal"])
    description: Optional[str] = Field(default=None, max_length=2000)
    custom_instructions: Optional[str] = Field(default=None, max_length=8000)
    document_ids: list[str] = Field(
        default_factory=list,
        description="DIS Source Library document/job IDs to link as style references.",
    )
    activate: bool = Field(
        default=True,
        description="When true, activates the style for the given course/project scope.",
    )
    course_id: Optional[int] = Field(default=None, description="Course scope for activation.")
    project_id: Optional[int] = Field(default=None, description="Project scope for activation.")


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
    extra_instructions: str = Field(default="", max_length=12000)
    document_ids: list[str] = Field(default_factory=list, description="Optional DIS source document/job IDs to use for this Understand/Refine call.")
    system_prompt_override: Optional[str] = None


class StyleUnderstandResponse(BaseModel):
    style_id: int
    understanding: str = Field(description="AI-generated style intelligence text.")
    model_used: str
    tokens_used: Optional[int] = None


class StyleDocumentUploadResponse(BaseModel):
    uploaded: list[str] = Field(description="Filenames successfully parsed and saved.")
    errors: list[str] = Field(default_factory=list)
    added: int = Field(default=0, description="Number of documents newly linked to the style.")


class StyleReferenceDocument(BaseModel):
    """Document linked to a style via style_documents."""

    id: str | int
    name: str
    source_type: Optional[str] = Field(default="general", description="doc_tag from library")
    file_type: Optional[str] = None


class StyleRead(BaseModel):
    id: int
    style_key: str = Field(
        description="Unique slug identifier (Streamlit Style ID).",
        alias="style_id",
    )
    name: str
    description: Optional[str] = None
    custom_instructions: Optional[str] = None
    understanding_status: Optional[str] = Field(
        default="fresh",
        description='"fresh" | "stale" — stale when new files were added after last understand.',
    )
    is_active: bool = False
    understanding: Optional[str] = Field(
        default=None,
        alias="generated_summary",
        description="AI-generated style intelligence. May be long.",
    )
    reference_documents: list[StyleReferenceDocument] = Field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class StyleListItem(BaseModel):
    """Lightweight style summary — understanding is truncated to 200 chars."""

    id: str | int
    name: str
    is_active: bool = False
    understanding_preview: Optional[str] = Field(
        default=None,
        description="First 200 characters of the style intelligence.",
    )
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
