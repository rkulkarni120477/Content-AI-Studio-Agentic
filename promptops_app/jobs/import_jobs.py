"""Background job for the reverse pipeline (Canvas IMSCC import).

Runs on the *same* generic infra as generation jobs — a plain function submitted
to ``job_runner``'s ThreadPoolExecutor, opening its own ``SessionLocal`` and
driving progress via the shared ``job_status`` helpers. It shares **only** that
infra: it never calls ``run_generation_job`` and adds no branch to it
(reverse_cas.md isolation rule).

Session 3 scope — stages 1–4 (Goal B):
    Extract → Parse → Reconstruct Editor → Finalize.
Reverse-generation stages (Blueprint / CDD / Style) are appended in S5–S6.

The uploaded package is staged to a temp file by the API layer; its path is
passed in ``request_params`` (job payload must be JSON-serialisable, so we pass a
path, not bytes). This job owns that temp file's lifecycle and deletes it on exit.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from promptops_app.database import Course, CourseImport, GenerationJob, ModuleBlueprint, SessionLocal
from promptops_app.importers import (
    editor_builder,
    reverse_blueprint,
    reverse_cdd,
    reverse_common,
    style_analyzer,
)
from promptops_app.importers.imscc_importer import parse_package
from promptops_app.importers.package_extractor import PackageValidationError
from promptops_app.jobs.job_status import (
    JobStatus,
    is_job_cancelled,
    set_completed,
    set_failed,
    set_running,
)
from promptops_app.repositories import course_repository

_log = logging.getLogger(__name__)


class _ImportCancelled(Exception):
    """Unwinds the job to its purge handler from any stage, including inside
    editor_builder.build() (via progress_cb) and _reverse_generate. Unlike a
    failure, a cancel purges even after reconstruction: the user asked for the
    title not to exist."""

# (progress_pct, label) — same convention as job_status.STAGE_* for the UI.
STAGE_EXTRACT = (10, "Extracting package...")
STAGE_PARSE = (30, "Parsing course content...")
STAGE_RECONSTRUCT_START = (50, "Reconstructing editor...")
STAGE_RECONSTRUCT_END = 74     # reconstruction progress spans 50→74
STAGE_BLUEPRINT = (78, "Reconstructing blueprints...")
STAGE_CDD = (86, "Reconstructing course design...")
STAGE_STYLE = (92, "Detecting course style...")
STAGE_FINALIZE = (96, "Finalizing import...")

# Reverse-gen progress span used by the retry job (reverse-gen only).
STAGE_RETRY_BLUEPRINT = (25, "Reconstructing blueprints...")
STAGE_RETRY_CDD = (55, "Reconstructing course design...")
STAGE_RETRY_STYLE = (85, "Detecting course style...")


def run_import_job(job_id: str) -> None:
    """Import an IMSCC package into a populated, editable CAS course.

    Single ``job_id`` argument → Celery-compatible, exactly like
    ``run_generation_job``. On success the course's Editor is fully populated and
    ``job.result_entity_id`` is the ``course_id``.
    """
    db = SessionLocal()
    job = None
    course_import = None
    package_path = None
    course_id = None
    reconstructed = False
    try:
        job = db.query(GenerationJob).filter(GenerationJob.id == job_id).first()
        if not job:
            _log.error("Import job %s not found", job_id)
            return
        if job.status == JobStatus.CANCELLED:
            _log.info("Import job %s cancelled before start", job_id)
            return
        # Idempotency (P4.1): Celery at-least-once delivery can re-run this task.
        # An already-completed import must not be rebuilt (would duplicate the
        # reconstructed course). This guards the common re-delivery-after-
        # completion case; deeper mid-run idempotency for the reconstruction
        # stages themselves is tracked as a follow-up.
        if job.status == JobStatus.COMPLETED:
            _log.info("Import job %s already completed — skipping duplicate run", job_id)
            return

        params = json.loads(job.request_json)
        import_id = params["import_id"]
        course_id = params["course_id"]
        project_id = params.get("project_id")
        user_name = params.get("user_name", "")
        package_path = params.get("package_path")

        course_import = db.get(CourseImport, import_id)

        # ── Stage 1 — Extract ─────────────────────────────────────────
        set_running(db, job, *STAGE_EXTRACT)
        data = _read_package(package_path)
        if is_job_cancelled(db, job_id):
            raise _ImportCancelled()

        # ── Stage 2 — Parse (extract + full content parse) ────────────
        set_running(db, job, *STAGE_PARSE)
        course = parse_package(data)
        if is_job_cancelled(db, job_id):
            raise _ImportCancelled()
        if course_import is not None:
            course_import.structure_counts_json = json.dumps(course.structure_counts())
            course_import.status = "reconstructing"
            db.commit()

        # ── Stage 3 — Reconstruct Editor ──────────────────────────────
        set_running(db, job, *STAGE_RECONSTRUCT_START)

        def _progress(done: int, total: int, label: str) -> None:
            # Checked on every call (the Editor loop, minutes for a big package)
            # so Cancel actually lands instead of running to completion regardless.
            if is_job_cancelled(db, job_id):
                raise _ImportCancelled()
            base, _ = STAGE_RECONSTRUCT_START
            pct = base + int((STAGE_RECONSTRUCT_END - base) * (done / total)) if total else STAGE_RECONSTRUCT_END
            set_running(db, job, pct, label)

        result = editor_builder.build(
            db,
            course,
            course_id=course_id,
            project_id=project_id,
            import_id=import_id,
            user_name=user_name,
            progress_cb=_progress,
        )
        # Cancel can still land in the gap between the Editor loop's last
        # progress_cb call and here — build() returning doesn't re-check.
        # Caught here it's the difference between "never existed" and "exists,
        # unhidden, and Cancel silently did nothing" (the bug this closes: a
        # fast CPU-only build on a real package can clear this whole function
        # in seconds, well inside the round trip of the user's own click).
        if is_job_cancelled(db, job_id):
            raise _ImportCancelled()
        reconstructed = True   # Editor has real content — never hide/archive past this point

        # The course was created invisible (is_active=False — see start_import
        # in app/api/v1/routers/imports.py) so a request that fails, or a job
        # that never runs, can't leave a contentless shell badged "Imported" in
        # the Titles list. Reconstruction just succeeding is exactly the point
        # "the package has been successfully imported and its content has been
        # rebuilt" — unhide it now, before the non-fatal reverse-gen stages
        # below, which must never gate visibility of an already-usable Editor.
        course_row = db.get(Course, course_id)
        if course_row is not None:
            course_row.is_active = True
            db.commit()

        # ── Stages 5–7 — Reverse-generate design artifacts (non-fatal) ─
        # Runs only after reconstruction succeeded. Any failure here leaves the
        # Editor fully usable and is retryable — it must NEVER fail the import.
        # Cancel is the one exception _reverse_generate re-raises instead of
        # swallowing (see its own except clause) — these are the LLM-heavy
        # stages, the ones actually worth interrupting instead of paying for.
        _reverse_generate(db, job, course_id, user_name, result)

        if is_job_cancelled(db, job_id):
            raise _ImportCancelled()

        # ── Stage 4/Finalize ──────────────────────────────────────────
        set_running(db, job, *STAGE_FINALIZE)
        _finalize(db, course_id, course_import, import_id, result)

        set_completed(db, job, course_id)   # result_entity_id = course_id
        _log.info(
            "import_completed  job=%s  course_id=%s  modules=%d  blocks=%d  warnings=%d",
            job_id, course_id, result.modules_created, result.blocks_created, len(result.warnings),
        )

    except _ImportCancelled:
        # job.status is already CANCELLED (set by POST .../imports/{id}/cancel,
        # which is what we polled to get here). Purge unconditionally — this
        # can now fire even after is_active flipped True (a fast reconstruct
        # can beat the cancel request's own round trip), and the confirm
        # dialog promises deletion regardless of how far the job got, not
        # just while the shell was still invisible.
        _log.info("Import job %s cancelled — purging course %s", job_id, course_id)
        course_repository.purge_course(db, course_id)   # commits internally
    except PackageValidationError as exc:
        _log.warning("Import job %s: invalid package: %s", job_id, exc)
        _mark_failed(db, job, course_import, f"Invalid IMSCC package: {exc}",
                     course_id=None if reconstructed else course_id)
    except Exception as exc:  # noqa: BLE001 - background boundary; log full, expose clean
        _log.exception("Import job %s failed: %s", job_id, exc)
        # A failure after the Editor was actually built (e.g. _finalize/
        # set_completed hitting a transient DB error) leaves a fully usable
        # course — must not be archived alongside a genuinely empty shell.
        _mark_failed(db, job, course_import, "Import failed while reconstructing the course.",
                     course_id=None if reconstructed else course_id)
    finally:
        _cleanup_package(package_path)
        db.close()


def run_reverse_gen_job(job_id: str) -> None:
    """Re-run ONLY reverse-generation (Blueprint/CDD/Style) for a built course.

    Backs POST /imports/{importId}/retry. It does **not** touch the reconstructed
    modules/blocks — it regenerates the AI design artifacts from the existing
    content and re-pins the course actives. Celery-compatible single-arg
    signature, like run_import_job.
    """
    db = SessionLocal()
    job = None
    try:
        job = db.query(GenerationJob).filter(GenerationJob.id == job_id).first()
        if not job:
            _log.error("Reverse-gen job %s not found", job_id)
            return
        if job.status == JobStatus.CANCELLED:
            return
        # Idempotency (P4.1): skip an already-completed reverse-gen retry so a
        # re-delivered task doesn't regenerate design artifacts twice.
        if job.status == JobStatus.COMPLETED:
            _log.info("Reverse-gen job %s already completed — skipping duplicate run", job_id)
            return

        params = json.loads(job.request_json)
        import_id = params.get("import_id")
        course_id = params["course_id"]
        user_name = params.get("user_name", "")
        course_import = db.get(CourseImport, import_id) if import_id else None

        set_running(db, job, 10, "Preparing reverse-generation...")

        result = editor_builder.BuildResult()   # reuse its .warnings accumulator
        _reverse_generate(
            db, job, course_id, user_name, result,
            stages=(STAGE_RETRY_BLUEPRINT, STAGE_RETRY_CDD, STAGE_RETRY_STYLE),
        )

        set_running(db, job, 96, "Finalizing...")
        if course_import is not None:
            course_import.warnings_json = json.dumps(result.warnings)
            course_import.status = "completed"
            course_import.provenance_ready = True
            db.commit()

        set_completed(db, job, course_id)
        _log.info("import_retry_completed  job=%s  course_id=%s  warnings=%d",
                  job_id, course_id, len(result.warnings))

    except Exception as exc:  # noqa: BLE001 - background boundary
        _log.exception("Reverse-gen retry job %s failed: %s", job_id, exc)
        _mark_failed(db, job, None, "Retry failed while regenerating design artifacts.")
    finally:
        db.close()


def _reverse_generate(db, job, course_id: int, user_name: str, result, *, stages=None) -> None:
    """Stages 5–7: reconstruct Blueprint + CDD + Style from the built content.

    Non-fatal by contract: any failure leaves the Editor + blocks fully usable
    and is retryable — it must NEVER fail the import. Blueprints first (per
    module), then the CDD (aggregating the outline, backfilling blueprint→CDD
    links), then a reusable Style from sampled lessons. ``stages`` overrides the
    (blueprint, cdd, style) progress tuples so the retry job can reuse this.
    """
    bp_stage, cdd_stage, style_stage = stages or (STAGE_BLUEPRINT, STAGE_CDD, STAGE_STYLE)
    try:
        course_row = db.get(Course, course_id)
        if course_row is None:
            return
        model_choice = (course_row.config_model_choice or reverse_common.DEFAULT_IMPORT_MODEL)
        modules = reverse_common.collect_course_modules(db, course_id)
        if not modules:
            return

        if is_job_cancelled(db, job.id):
            raise _ImportCancelled()
        set_running(db, job, *bp_stage)
        bp_result = reverse_blueprint.build_blueprints(
            db, course=course_row, model_choice=model_choice, user_name=user_name, modules=modules,
        )
        result.warnings.extend(bp_result.warnings)

        if is_job_cancelled(db, job.id):
            raise _ImportCancelled()
        set_running(db, job, *cdd_stage)
        cdd_result = reverse_cdd.build_cdd(
            db, course=course_row, model_choice=model_choice, user_name=user_name, modules=modules,
        )
        result.warnings.extend(cdd_result.warnings)

        # Link blueprints → CDD now that the CDD row exists (FK sanity for the
        # blueprint regenerate path, which resolves CDD context by cdd_id).
        if cdd_result.cdd_id and bp_result.blueprint_ids:
            db.query(ModuleBlueprint).filter(
                ModuleBlueprint.id.in_(bp_result.blueprint_ids)
            ).update({ModuleBlueprint.cdd_id: cdd_result.cdd_id}, synchronize_session=False)
            db.commit()

        if is_job_cancelled(db, job.id):
            raise _ImportCancelled()
        set_running(db, job, *style_stage)
        style_result = style_analyzer.build_style(
            db, course=course_row, model_choice=model_choice, user_name=user_name, modules=modules,
        )
        result.warnings.extend(style_result.warnings)

    except _ImportCancelled:
        # Re-raise rather than swallow: the one caller that cares
        # (run_import_job) needs this to reach ITS except clause and purge.
        # run_reverse_gen_job's own generic except still catches it same as
        # any other failure — no special-casing needed there, and correctly
        # so: cancelling a RETRY must never purge an already-real course.
        raise
    except Exception as exc:  # noqa: BLE001 - reverse-gen is best-effort
        db.rollback()
        _log.warning("Import job: reverse-generation failed (Editor still usable): %s", exc)
        result.warnings.append(
            "AI design artifacts (Blueprint/CDD/Style) could not be generated; the Editor "
            "is fully usable. Use Retry to try again."
        )


def _read_package(package_path: str | None) -> bytes:
    if not package_path or not os.path.isfile(package_path):
        raise PackageValidationError("Uploaded package is no longer available on the server.")
    with open(package_path, "rb") as handle:
        return handle.read()


def _finalize(db, course_id: int, course_import, import_id: int, result) -> None:
    """Pin import metadata on the course + import record. Additive fields only."""
    course_row = db.get(Course, course_id)
    if course_row is not None:
        course_row.source_type = "imscc"   # display/analytics only — never branched on
        course_row.import_id = import_id

    if course_import is not None:
        course_import.status = "completed"
        course_import.warnings_json = json.dumps(result.warnings)
        course_import.provenance_ready = True   # provenance map written during reconstruction
        course_import.completed_at = datetime.now(timezone.utc)

    db.commit()


def _mark_failed(db, job, course_import, message: str, *, course_id: int | None = None) -> None:
    try:
        if course_import is not None:
            course_import.status = "failed"
            db.commit()
    except Exception:  # pragma: no cover - best-effort status write
        db.rollback()
    if course_id is not None:
        # The initial reconstruction never produced a usable Editor — hide
        # the empty shell rather than leaving a broken title badged
        # "Imported" in the Titles list (same is_active flag the archive
        # endpoint uses; the course row + any partial content stay in place
        # for support/debugging, just no longer listed — see
        # course_repository._is_empty_import_shell for the list-side
        # exclusion, since the Titles page itself fetches with
        # include_archived=true). A separate commit so this write can't be
        # lost to, or roll back, the course_import status write above.
        try:
            course_row = db.get(Course, course_id)
            if course_row is not None:
                course_row.is_active = False
                db.commit()
        except Exception:  # pragma: no cover - best-effort archive
            db.rollback()
    if job is not None:
        set_failed(db, job, message)


def _cleanup_package(package_path: str | None) -> None:
    if package_path and os.path.isfile(package_path):
        try:
            os.remove(package_path)
        except OSError as exc:  # pragma: no cover - best-effort cleanup
            _log.warning("Could not remove staged package %s: %s", package_path, exc)
