"""
Imports router — reverse pipeline (Canvas IMSCC + Cengage CendocXML course import).

This whole router is mounted ONLY when ``settings.import_courses_enabled`` is
true (see app/api/v1/router.py). When the flag is off the routes below do not
exist, so the API surface is identical to today.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependencies import get_current_user, get_db, require_permission
from app.core.exceptions import NotFoundError, ValidationError
from app.schemas.common import MessageResponse
from app.schemas.import_ import (
    ImportHealthResponse,
    ImportRecordResponse,
    ImportStartResponse,
    ImportValidateResponse,
    StructureCounts,
)

_log = logging.getLogger(__name__)

# Imports-scoped routes (mounted under the "/imports" prefix in router.py).
router = APIRouter()

# Project-scoped route (mounted with no prefix, like courses.py) so the path can
# be POST /projects/{projectId}/imports. Both routers are flag-gated together.
project_router = APIRouter()


@router.get(
    "/health",
    response_model=ImportHealthResponse,
    summary="Reverse-pipeline capability probe",
)
def import_health(
    current_user=Depends(get_current_user),
) -> ImportHealthResponse:
    """Report whether the course-import feature is enabled for this deployment."""
    return ImportHealthResponse(enabled=settings.import_courses_enabled)


@router.post(
    "/validate",
    response_model=ImportValidateResponse,
    summary="Validate a course package (pre-flight, no DB writes)",
    description=(
        "Uploads a Canvas IMSCC or Cengage CendocXML package, extracts and "
        "structurally parses it, and returns the structure counts plus any items "
        "flagged for review. Nothing is persisted — this powers the import "
        "wizard's confirmation step."
    ),
)
async def validate_import_package(
    file: UploadFile = File(...),
    current_user=Depends(require_permission("course.create")),
) -> ImportValidateResponse:
    """Extract + structural-parse only. Fatal package problems return HTTP 422."""
    from promptops_app.importers.imscc_importer import validate_package
    from promptops_app.importers.package_extractor import PackageValidationError

    raw_bytes = await file.read()
    try:
        course = validate_package(raw_bytes)
    except PackageValidationError as exc:
        raise ValidationError(f"Invalid course package: {exc}") from exc

    _log.info(
        "import_validate  user=%s  package=%s  format=%s  modules=%d",
        current_user.username, file.filename, course.package_format, len(course.modules),
    )
    return ImportValidateResponse(
        package_name=file.filename or "package.zip",
        course_title=course.title,
        structure_counts=StructureCounts(**course.structure_counts()),
        warnings=course.warnings,
        package_format=course.package_format or "imscc",
    )


@project_router.post(
    "/projects/{project_id}/imports",
    response_model=ImportStartResponse,
    status_code=201,
    summary="Create a course from an IMSCC or CendocXML package (async reconstruction)",
    description=(
        "Creates a course shell + a course_imports record, stages the uploaded "
        "package, and enqueues a background import job. Poll GET /jobs/{jobId} for "
        "progress; the job populates the Editor and sets result_entity_id=course_id."
    ),
)
async def start_import(
    project_id: int,
    file: UploadFile = File(...),
    name: str = Form(...),
    cluster_id: int | None = Form(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("course.create")),
) -> ImportStartResponse:
    """Create the course shell + import record and enqueue the reconstruction job."""
    from promptops_app.database import Course, CourseImport
    from promptops_app.importers.package_extractor import (
        FORMAT_CENDOC,
        PackageValidationError,
        detect_package_format,
    )
    from promptops_app.jobs import import_jobs, job_runner
    from promptops_app.repositories import job_repository

    raw_bytes = await file.read()
    if not raw_bytes:
        raise ValidationError("Uploaded package is empty.")

    try:
        package_format = detect_package_format(raw_bytes)
    except PackageValidationError as exc:
        raise ValidationError(f"Invalid course package: {exc}") from exc

    source_type = "cendoc" if package_format == FORMAT_CENDOC else "imscc"
    suffix = ".zip" if package_format == FORMAT_CENDOC else ".imscc"
    default_name = "package.zip" if package_format == FORMAT_CENDOC else "package.imscc"

    # Course shell — same construction as scratch create, plus additive import
    # metadata (source_type is display/analytics only; never branched on).
    course = Course(
        name=name,
        project_id=project_id,
        cluster_id=cluster_id,
        created_by=current_user.username,
        source_type=source_type,
    )
    db.add(course)
    db.commit()
    db.refresh(course)

    course_import = CourseImport(
        course_id=course.id,
        project_id=project_id,
        uploaded_by=current_user.username,
        package_name=file.filename or default_name,
        package_size=len(raw_bytes),
        status="queued",
    )
    db.add(course_import)
    db.commit()
    db.refresh(course_import)

    course.import_id = course_import.id
    db.commit()

    # Stage the package to a temp file — the background job reads it by path
    # (job payloads must be JSON-serialisable) and deletes it when done.
    fd, package_path = tempfile.mkstemp(prefix="import_pkg_", suffix=suffix)
    with os.fdopen(fd, "wb") as handle:
        handle.write(raw_bytes)

    job_id = job_repository.create_job(
        db,
        user_name=current_user.username,
        request_params={
            "import_id": course_import.id,
            "course_id": course.id,
            "project_id": project_id,
            "user_name": current_user.username,
            "package_path": package_path,
            "package_name": file.filename or default_name,
            "package_format": package_format,
            "options": {},
        },
        project_id=project_id,
        course_id=course.id,
        job_type="import",
    )
    job_runner.submit(import_jobs.run_import_job, job_id)

    _log.info(
        "import_started  user=%s  project_id=%d  course_id=%d  import_id=%d  job=%s  "
        "package=%s  format=%s",
        current_user.username, project_id, course.id, course_import.id, job_id,
        file.filename, package_format,
    )
    return ImportStartResponse(course_id=course.id, import_id=course_import.id, job_id=job_id)


@router.get(
    "/{import_id}",
    response_model=ImportRecordResponse,
    summary="Get an import record + latest status/warnings",
)
def get_import(
    import_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> ImportRecordResponse:
    """Return the course_imports record — status, structure counts, warnings."""
    from promptops_app.database import CourseImport

    record = db.get(CourseImport, import_id)
    if record is None:
        raise NotFoundError("Import", import_id)

    counts = None
    if record.structure_counts_json:
        try:
            counts = StructureCounts(**json.loads(record.structure_counts_json))
        except (ValueError, TypeError):
            counts = None

    warnings: list[str] = []
    if record.warnings_json:
        try:
            warnings = json.loads(record.warnings_json)
        except (ValueError, TypeError):
            warnings = []

    return ImportRecordResponse(
        id=record.id,
        course_id=record.course_id,
        project_id=record.project_id,
        package_name=record.package_name,
        status=record.status,
        provenance_ready=bool(record.provenance_ready),
        structure_counts=counts,
        warnings=warnings,
    )


@router.post(
    "/{import_id}/retry",
    response_model=ImportStartResponse,
    status_code=202,
    summary="Re-run reverse-generation (Blueprint/CDD/Style) for an import",
    description=(
        "Re-runs ONLY the AI design artifacts on the already-reconstructed course "
        "— it does not rebuild the Editor blocks. Poll GET /jobs/{jobId} for progress."
    ),
)
def retry_import(
    import_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("course.create")),
) -> ImportStartResponse:
    """Enqueue a reverse-generation-only job for an existing import."""
    from promptops_app.database import CourseImport
    from promptops_app.jobs import import_jobs, job_runner
    from promptops_app.repositories import job_repository

    record = db.get(CourseImport, import_id)
    if record is None:
        raise NotFoundError("Import", import_id)
    if record.course_id is None:
        raise ValidationError("This import has no reconstructed course to regenerate.")

    job_id = job_repository.create_job(
        db,
        user_name=current_user.username,
        request_params={
            "import_id": record.id,
            "course_id": record.course_id,
            "project_id": record.project_id,
            "user_name": current_user.username,
        },
        project_id=record.project_id,
        course_id=record.course_id,
        job_type="import_reverse",
    )
    job_runner.submit(import_jobs.run_reverse_gen_job, job_id)

    _log.info("import_retry_started  user=%s  import_id=%d  course_id=%s  job=%s",
              current_user.username, record.id, record.course_id, job_id)
    return ImportStartResponse(course_id=record.course_id, import_id=record.id, job_id=job_id)


@router.post(
    "/{import_id}/cancel",
    response_model=MessageResponse,
    summary="Cancel a running import job",
)
def cancel_import(
    import_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("course.create")),
) -> MessageResponse:
    """Cancel the import's active job (best-effort; reuses the shared job cancel)."""
    from promptops_app.database import CourseImport, GenerationJob
    from promptops_app.jobs.job_status import JobStatus, set_cancelled

    record = db.get(CourseImport, import_id)
    if record is None:
        raise NotFoundError("Import", import_id)

    job = (
        db.query(GenerationJob)
        .filter(
            GenerationJob.course_id == record.course_id,
            GenerationJob.job_type.in_(["import", "import_reverse"]),
            GenerationJob.status.in_(list(JobStatus.ACTIVE)),
        )
        .order_by(GenerationJob.created_at.desc())
        .first()
    )
    if job is None:
        return MessageResponse(message="No active import job to cancel.")

    set_cancelled(db, job)
    _log.info("import_cancelled  user=%s  import_id=%d  job=%s",
              current_user.username, record.id, job.id)
    return MessageResponse(message="Import cancelled.")
