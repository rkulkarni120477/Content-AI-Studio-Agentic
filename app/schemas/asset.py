"""Editor asset upload schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AssetUploadResponse(BaseModel):
    """Returned by POST /assets/upload — the stored image's public URL."""

    url: str
    filename: str
    content_type: str
    size: int


class AssetCleanupRequest(BaseModel):
    """Body for POST /assets/cleanup — URLs the editor uploaded but did not keep."""

    urls: list[str] = Field(default_factory=list, max_length=100)


class AssetCleanupResponse(BaseModel):
    checked: int
