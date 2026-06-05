"""Reference document library schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class DocumentRead(BaseModel):
    id: int
    name: str = Field(validation_alias="filename")
    file_type: Optional[str] = None
    source_type: Optional[str] = Field(default=None, validation_alias="doc_tag")
    created_at: Optional[datetime] = Field(default=None, validation_alias="uploaded_at")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class DocumentListItem(BaseModel):
    id: int
    name: str = Field(validation_alias="filename")
    file_type: Optional[str] = None
    source_type: Optional[str] = Field(default=None, validation_alias="doc_tag")
    created_at: Optional[datetime] = Field(default=None, validation_alias="uploaded_at")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class DocumentContentResponse(BaseModel):
    """Full document text — used when injecting context into generation prompts."""

    id: int
    name: str
    content: str = Field(description="Parsed text extracted from the uploaded file.")


class ParsedFileResponse(BaseModel):
    """Parsed upload for one-off generation context (not saved to the library)."""

    name: str
    content: str
    source_type: str = Field(default="reference")
