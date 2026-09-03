"""Background job for Outline import.

Moves the slow part of ``/blueprints/import`` — extracting the uploaded file and
(for a day Outline) the LLM restructure — off the request thread, so a reverse
proxy's read timeout can never 504 it mid-run. Runs on the same generic infra as
the IMSCC import (``run_import_job``): a plain single-``job_id`` function,
Celery-compatible, opening its own ``SessionLocal`` and driving progress through
the shared ``job_status`` helpers.

The uploaded file is staged to a temp path by the API layer (job payloads must be
JSON-serialisable, so we pass a path, not bytes); this job owns that file and
deletes it on exit. The extract+normalize+persist work is identical to the sync
route — it calls the same ``normalize_import`` and ``persist_imported_outline``.
"""
from __future__ import annotations

import json
import logging
import os

from promptops_app.database import GenerationJob, SessionLocal
from promptops_app.jobs.job_status import (
    JobStatus,
    set_completed,
    set_failed,
    set_running,
)

_log = logging.getLogger(__name__)

# (progress_pct, label) — same convention as job_status.STAGE_* for the UI.
STAGE_READ = (10, "Reading the file...")
STAGE_STRUCTURE = (45, "Structuring the Outline...")
STAGE_SAVE = (90, "Saving the Outline...")


def _reconstruct_user(params: dict):
    """Rebuild the user-like object DIS/audit read via getattr — mirrors
    block_wide_jobs._reconstruct_user (real id when the router recorded one, so
    DIS tenant resolution works; falls back to the name otherwise)."""
    import types
    name = params.get("user_name", "") or "cas-user"
    user_id = params.get("user_id")
    return types.SimpleNamespace(username=name, id=name if user_id is None else user_id,
                                 email=name, role=params.get("role", "user"))


def _read_file(path: str | None) -> bytes:
    if not path or not os.path.isfile(path):
        raise ValueError("The uploaded file is no longer available on the server. Please upload it again.")
    with open(path, "rb") as fh:
        return fh.read()


def _cleanup(path: str | None) -> None:
    if path and os.path.isfile(path):
        try:
            os.remove(path)
        except OSError as exc:  # pragma: no cover - best-effort cleanup
            _log.warning("Could not remove staged outline file %s: %s", path, exc)


def run_outline_import_job(job_id: str) -> None:
    """Extract → normalize → persist an uploaded Outline. On success
    ``job.result_entity_id`` is the blueprint id (the UI reloads it)."""
    db = SessionLocal()
    job = None
    package_path = None
    try:
        job = db.query(GenerationJob).filter(GenerationJob.id == job_id).first()
        if not job:
            _log.error("Outline import job %s not found", job_id)
            return
        if job.status == JobStatus.CANCELLED:
            _log.info("Outline import job %s cancelled before start", job_id)
            return
        # Idempotency: Celery at-least-once delivery can re-run this task. An
        # already-completed import must not be rebuilt (would duplicate the row).
        if job.status == JobStatus.COMPLETED:
            _log.info("Outline import job %s already completed — skipping duplicate run", job_id)
            return

        params = json.loads(job.request_json)
        package_path = params.get("package_path")
        filename = params.get("filename") or "outline"
        course_id = params.get("course_id")
        project_id = params.get("project_id")
        cdd_id = params.get("cdd_id")
        model_choice = params.get("model_choice", "GPT-5.4")
        current_user = _reconstruct_user(params)

        from promptops_app.services.outline_import_persist import persist_imported_outline
        from promptops_app.services.outline_import_service import normalize_import
        from promptops_app.services.usage_service import UsageLogContext

        usage_ctx = UsageLogContext(user_name=current_user.username, project_id=project_id,
                                    course_id=course_id, entity_type="outline_import")

        set_running(db, job, *STAGE_READ)
        data = _read_file(package_path)

        set_running(db, job, *STAGE_STRUCTURE)
        try:
            result = normalize_import(
                filename, data,
                document_title=params.get("document_title", ""),
                hint_kind=params.get("unit_kind", "day"),
                hint_number=params.get("unit_number"),
                model_choice=model_choice, usage_ctx=usage_ctx,
            )
        except ValueError as exc:
            # User-actionable (unsupported type, empty file, undetermined/contradictory
            # unit) — surface the exact message on the job so the UI shows it, not a
            # generic failure.
            set_failed(db, job, str(exc))
            _log.info("outline_import job=%s user-error: %s", job_id, exc)
            return

        set_running(db, job, *STAGE_SAVE)
        bp, version_record, was_new = persist_imported_outline(
            db, result, course_id=course_id, project_id=project_id, cdd_id=cdd_id,
            source_filename=filename, current_user=current_user,
        )

        job.result_json = json.dumps({
            "blueprint_id": bp.id,
            "title": bp.title,
            "version": version_record.version,
            "outline_kind": result.kind,
            "unit_number": result.unit_number,
            "is_dlu": result.is_dlu,
            "new_document": was_new,
            "import_warnings": result.warnings,
            # Surfaced by the job status endpoint's `warning` field so the UI can
            # flag a degraded (single-section) import.
            "warning": (result.warnings[0] if result.warnings else None),
        })
        set_completed(db, job, bp.id)   # result_entity_id = blueprint id
        _log.info("outline_import_completed job=%s bp_id=%d new=%s version=%s warnings=%d",
                  job_id, bp.id, was_new, version_record.version, len(result.warnings))

    except Exception as exc:  # noqa: BLE001 - background boundary; log full, expose clean
        _log.exception("Outline import job %s failed: %s", job_id, exc)
        try:
            db.rollback()   # clear a poisoned session before the status write
        except Exception:  # pragma: no cover
            pass
        if job is not None:
            set_failed(db, job, "The file could not be processed at this time. Please try again.")
    finally:
        _cleanup(package_path)
        db.close()
