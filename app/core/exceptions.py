"""
Domain exception hierarchy for the Content AI Studio API.

Design principles
-----------------
- Every exception the application can raise intentionally inherits from
  ``AppError``.
- The global handler in ``app/main.py`` catches ``AppError`` and converts it
  into a consistent JSON error envelope:
      { "error": { "code": "...", "message": "...", "detail": {...} } }
- Unhandled Python exceptions fall through to FastAPI's default 500 handler.
- Each subclass carries a meaningful ``code`` string (SCREAMING_SNAKE_CASE)
  that the React frontend uses to decide how to display the error.

Usage
-----
    from app.core.exceptions import NotFoundError, WorkflowError

    cdd = cdd_repository.get_by_id(db, cdd_id)
    if not cdd:
        raise NotFoundError("CDD", cdd_id)

    ok, reason = workflow_service.approve_block(db, block, actor)
    if not ok:
        raise WorkflowError(reason)
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """
    Base class for all intentional application errors.

    Every subclass must set a unique ``code`` and appropriate ``status_code``.
    The ``detail`` dict can carry structured data the frontend may need
    (e.g. which field failed validation, which block_id caused a conflict).
    """

    status_code: int = 400
    code: str = "APP_ERROR"

    def __init__(
        self,
        message: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


class NotFoundError(AppError):
    """
    Raised when a requested resource does not exist in the database.

    Example:
        raise NotFoundError("CDD", 42)
        # → 404 { "code": "NOT_FOUND", "message": "CDD with id 42 not found." }
    """

    status_code = 404
    code = "NOT_FOUND"

    def __init__(self, resource_name: str, resource_id: int | str) -> None:
        super().__init__(
            message=f"{resource_name} with id {resource_id} not found.",
            detail={"resource": resource_name, "id": resource_id},
        )


class AuthenticationError(AppError):
    """
    Raised when a JWT token is missing, expired, or invalid.

    Results in a 401 response, which tells the React app to redirect to login.
    """

    status_code = 401
    code = "AUTHENTICATION_REQUIRED"


class PermissionDeniedError(AppError):
    """
    Raised when the authenticated user lacks the required RBAC permission.

    Carries the permission key so the frontend can show a meaningful message.

    Example:
        raise PermissionDeniedError("workflow.bulk_approve", user_role="author")
    """

    status_code = 403
    code = "PERMISSION_DENIED"

    def __init__(self, permission: str, user_role: str = "") -> None:
        super().__init__(
            message=(
                f"Your role '{user_role}' does not have the '{permission}' permission. "
                "Contact an Admin to request access."
            ),
            detail={"required_permission": permission, "user_role": user_role},
        )


class WorkflowError(AppError):
    """
    Raised when a workflow state transition is not allowed.

    Corresponds to HTTP 409 Conflict — the request is valid but conflicts
    with the current resource state.

    Example:
        raise WorkflowError("Block must be 'In Review' to approve.")
    """

    status_code = 409
    code = "WORKFLOW_VIOLATION"


class ValidationError(AppError):
    """
    Raised when business-rule validation fails (not Pydantic schema validation).

    Use for domain-level rules that Pydantic cannot express, e.g.
    "A CDD must have at least one section before generating a blueprint."
    """

    status_code = 422
    code = "VALIDATION_ERROR"


class LLMGenerationError(AppError):
    """
    Raised when all LLM attempts fail (primary + retry + fallback).

    The message is always safe to show to the user — it never contains
    raw API error details or stack traces.
    """

    status_code = 502
    code = "LLM_GENERATION_FAILED"


class ExportError(AppError):
    """Raised when a file export operation fails (DOCX, PDF, HTML, etc.)."""

    status_code = 500
    code = "EXPORT_FAILED"


class JobNotFoundError(NotFoundError):
    """Raised when a background job ID does not exist."""

    def __init__(self, job_id: str) -> None:
        super().__init__("GenerationJob", job_id)


class DuplicateResourceError(AppError):
    """
    Raised when a create operation would produce a duplicate (e.g. prompt name collision).

    HTTP 409 Conflict.
    """

    status_code = 409
    code = "DUPLICATE_RESOURCE"

    def __init__(self, resource_name: str, identifier: str) -> None:
        super().__init__(
            message=f"A {resource_name} with identifier '{identifier}' already exists.",
            detail={"resource": resource_name, "identifier": identifier},
        )
