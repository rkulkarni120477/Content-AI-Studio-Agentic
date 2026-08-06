"""
Shared Pydantic schemas used across multiple API modules.

These schemas define the standard response envelopes that every endpoint
in the API must follow.  Consistency here means the React frontend only
needs one set of response-handling utilities.

Response conventions
--------------------
  Success (list)    → PaginatedResponse[SomeReadSchema]
  Success (single)  → SomeReadSchema directly (no wrapper needed for single objects)
  Async job         → JobAcceptedResponse  (202 Accepted)
  Error             → Handled by the global exception handler in main.py
                      Shape: { "error": { "code": "...", "message": "...", "detail": {...} } }
"""

from __future__ import annotations

from typing import Generic, Optional, TypeVar

from pydantic import BaseModel, Field

from app.schemas.budget import UsageSummary

# TypeVar for the generic paginated list items.
T = TypeVar("T")


# ---------------------------------------------------------------------------
# Paginated list response
# ---------------------------------------------------------------------------

class PaginatedResponse(BaseModel, Generic[T]):
    """
    Standard wrapper for any endpoint that returns a list of items.

    All list endpoints must use this shape so the React frontend can use
    a single pagination utility across every resource type.

    Example response:
        {
            "items": [...],
            "total": 42,
            "page": 1,
            "page_size": 20,
            "pages": 3
        }
    """

    items: list[T] = Field(description="The items on the current page.")
    total: int = Field(description="Total number of items across all pages.")
    page: int = Field(description="Current page number (1-indexed).")
    page_size: int = Field(description="Number of items per page.")
    pages: int = Field(description="Total number of pages.")

    @classmethod
    def create(
        cls,
        items: list[T],
        total: int,
        page: int,
        page_size: int,
    ) -> "PaginatedResponse[T]":
        """Convenience constructor that calculates the total page count."""
        pages = max(1, -(-total // page_size))  # ceiling division
        return cls(items=items, total=total, page=page, page_size=page_size, pages=pages)


# ---------------------------------------------------------------------------
# Background job response (202 Accepted)
# ---------------------------------------------------------------------------

class JobAcceptedResponse(BaseModel):
    """
    Returned when a long-running operation has been queued as a background job.

    The client should poll ``status_url`` until ``status`` is
    ``completed`` or ``failed``.

    Example response:
        {
            "job_id": "a1b2c3d4-...",
            "status": "queued",
            "status_url": "/api/v1/jobs/a1b2c3d4-..."
        }
    """

    job_id: str = Field(description="Unique identifier for the background job.")
    status: str = Field(default="queued", description="Initial job status.")
    status_url: str = Field(description="URL to poll for job progress updates.")


# ---------------------------------------------------------------------------
# Job status response (used by GET /api/v1/jobs/{job_id})
# ---------------------------------------------------------------------------

class JobStatusResponse(BaseModel):
    """
    Real-time status of a background generation or plagiarism job.

    The ``progress`` field is a percentage (0–100).
    ``generation_id`` is populated once the job completes successfully.
    ``error_message`` is populated if the job fails (always user-safe — no stack traces).
    """

    job_id: str
    status: str = Field(description="queued | running | completed | failed | cancelled")
    progress: int = Field(default=0, ge=0, le=100, description="Completion percentage.")
    current_step: Optional[str] = Field(default=None, description="Human-readable current stage.")
    generation_id: Optional[int] = Field(
        default=None,
        description="ID of the created Generation record. Populated on completion.",
    )
    error_message: Optional[str] = Field(
        default=None,
        description="User-safe error description. Populated on failure.",
    )
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    usage_summary: Optional[UsageSummary] = Field(
        default=None,
        description="Cost/tokens for this job's LLM call + remaining budget headroom. Populated on completion.",
    )


# ---------------------------------------------------------------------------
# Generic success message (for operations that return no data)
# ---------------------------------------------------------------------------

class MessageResponse(BaseModel):
    """
    Simple acknowledgement response for operations with no meaningful return value.

    Example: logout, archive, deactivate.
    """

    message: str = Field(description="Human-readable confirmation message.")
