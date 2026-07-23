"""
Assets router — editor image uploads.

Backs the image button in the unified block editor. Accepts a single image
file, validates type and size, stores it in S3 (public-read, via
``app.services.asset_storage``), and returns a browser-facing URL the editor
embeds directly in the block's Markdown/HTML.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependencies import get_db, require_permission
from app.core.exceptions import ValidationError
from app.schemas.asset import AssetCleanupRequest, AssetCleanupResponse, AssetUploadResponse
from app.services.asset_storage import ALLOWED_IMAGE_TYPES, sniff_image_type, upload_image

_log = logging.getLogger(__name__)
router = APIRouter()


@router.post(
    "/upload",
    response_model=AssetUploadResponse,
    status_code=201,
    summary="Upload an editor image to S3",
    description=(
        "Accepts a single PNG, JPEG, GIF, or WebP image, stores it public-read in "
        "S3, and returns its URL for embedding in block content."
    ),
)
async def upload_asset(
    file: UploadFile = File(...),
    current_user=Depends(require_permission("editor.edit")),
) -> AssetUploadResponse:
    """Validate and store an uploaded editor image; return its public URL."""
    content_type = (file.content_type or "").lower()
    if content_type not in ALLOWED_IMAGE_TYPES:
        allowed = ", ".join(sorted(ALLOWED_IMAGE_TYPES))
        raise ValidationError(f"Unsupported image type '{content_type}'. Allowed: {allowed}.")

    data = await file.read()
    if not data:
        raise ValidationError("Uploaded file is empty.")

    max_bytes = settings.assets_max_upload_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise ValidationError(
            f"Image is too large ({len(data) // 1024} KB). "
            f"Maximum is {settings.assets_max_upload_mb} MB."
        )

    # Verify the actual bytes are an image — don't trust the client's header.
    # The sniffed type is authoritative for storage.
    sniffed = sniff_image_type(data)
    if sniffed is None:
        raise ValidationError("File content is not a valid PNG, JPEG, GIF, or WebP image.")
    content_type = sniffed

    url = upload_image(data, content_type)
    _log.info("asset_uploaded  user=%s  type=%s  bytes=%d",
              current_user.username, content_type, len(data))

    return AssetUploadResponse(
        url=url,
        filename=file.filename or "image",
        content_type=content_type,
        size=len(data),
    )


@router.post(
    "/cleanup",
    response_model=AssetCleanupResponse,
    summary="Delete uploaded images that are no longer referenced",
    description=(
        "Called on Save with images the editor uploaded this session. Each is "
        "deleted from S3 only if it is referenced nowhere (safety-checked), so "
        "uploaded-then-removed images don't linger as orphans."
    ),
)
def cleanup_assets(
    body: AssetCleanupRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("editor.edit")),
) -> AssetCleanupResponse:
    """Reference-aware deletion of the given uploaded image URLs."""
    from app.services.asset_cleanup import assets_in_content, delete_unreferenced

    keys: set[str] = set()
    for url in body.urls:
        keys |= assets_in_content(url)
    delete_unreferenced(db, keys)
    return AssetCleanupResponse(checked=len(keys))
