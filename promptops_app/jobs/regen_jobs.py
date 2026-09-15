"""Background job for single-item block regeneration (P4.5/F18).

``POST /blocks/{id}/regenerate-item`` used to run the LLM call inline in the
request, holding a shared FastAPI threadpool thread for the full call (tens of
seconds) — a burst could starve unrelated sync routes. It is now a background
job: the endpoint returns a ``job_id`` immediately and the frontend polls
``GET /jobs/{job_id}`` to completion (then refetches the block), matching the
generation UX. Runs on the ThreadPoolExecutor today, or Celery when
``PROMPTOPS_USE_CELERY=1``.

Single ``job_id`` argument → Celery-compatible, like ``run_generation_job``.
Idempotent under Celery at-least-once retry: an already-completed job is a no-op.
"""
from __future__ import annotations

import json
import logging

from promptops_app.database import Block, GenerationJob, SessionLocal
from promptops_app.jobs.job_status import (
    JobStatus,
    set_completed,
    set_failed,
    set_running,
)
from promptops_app.services.budget_service import BudgetExceededError

_log = logging.getLogger(__name__)


def run_regenerate_item_job(job_id: str) -> None:
    """Regenerate one item within a block, driven entirely by the job row."""
    db = SessionLocal()
    job = None
    try:
        job = db.query(GenerationJob).filter(GenerationJob.id == job_id).first()
        if not job:
            _log.error("Regenerate-item job %s not found", job_id)
            return
        if job.status == JobStatus.CANCELLED:
            _log.info("Regenerate-item job %s cancelled before start", job_id)
            return
        # Idempotency (P4.1 pattern): a re-delivered/retried task for an
        # already-completed job must not regenerate the item a second time.
        if job.status == JobStatus.COMPLETED:
            _log.info("Regenerate-item job %s already completed — skipping duplicate run", job_id)
            return

        from promptops_app.core.constants import ChangeSource
        from promptops_app.parsers.blueprint_parser import (
            parse_items_from_section,
            patch_item_in_section,
            regen_single_item,
        )
        from promptops_app.repositories.block_repo import save_block_version
        from promptops_app.services.content_sanitizer import sanitize_stored_content
        from promptops_app.services.usage_service import UsageLogContext

        params = json.loads(job.request_json)
        block_id = params["block_id"]
        item_index = params["item_index"]
        section_key = params.get("section_key") or ""
        feedback = params.get("feedback") or ""
        model_choice = params.get("model_choice", "GPT-5.4")
        user_name = params.get("user_name", "")

        set_running(db, job, 20, "Loading block...")
        block = db.query(Block).filter(Block.id == block_id).first()
        if block is None:
            set_failed(db, job, f"Block {block_id} not found.")
            return

        original = block.content or ""
        items = parse_items_from_section(original)
        if not items or item_index < 0 or item_index >= len(items):
            set_failed(db, job, f"Item index {item_index} not found in block {block_id}.")
            return

        set_running(db, job, 45, "Regenerating item...")
        target = items[item_index]
        usage_ctx = UsageLogContext(
            user_name=user_name,
            project_id=job.project_id,
            course_id=job.course_id,
            entity_type="block_item_regen", entity_id=str(block_id),
        )
        new_item_text = regen_single_item(
            section_title=section_key or (block.block_label or "content"),
            section_content=original,
            item_index=item_index,
            item_text=target["text"],
            usage_ctx=usage_ctx,
            custom_instruction=feedback,
            model_choice=model_choice,
        )

        set_running(db, job, 80, "Saving...")
        updated_content = sanitize_stored_content(
            patch_item_in_section(original, item_index, new_item_text)
        )
        # Save pre-change content as a version before overwriting, same as the
        # synchronous path did — otherwise item regens never show in history.
        save_block_version(
            db, block,
            change_source=ChangeSource.ITEM_REGENERATION,
            change_note=f"Item {item_index + 1} regenerated",
            created_by=user_name,
        )
        block.content = updated_content
        db.commit()

        # Store the result so a poller could read it directly; the frontend
        # simply refetches the block (it knows which one), so this is for
        # traceability/debugging. result_entity_id = block_id.
        job.result_json = json.dumps({
            "block_id": block_id,
            "updated_content": updated_content,
            "patched_item": new_item_text or "",
        })
        set_completed(db, job, block_id)
        _log.info("block_item_regenerated  user=%s  block_id=%s  item=%s  job=%s",
                  user_name, block_id, item_index, job_id)

    except Exception as exc:  # noqa: BLE001 - background boundary; log full, expose clean
        _log.exception("Regenerate-item job %s failed: %s", job_id, exc)
        if job is not None:
            try:
                # BudgetExceededError's real "$X of $Y used" / "N of M tokens
                # used" message must reach the user — a generic retry prompt
                # hides that. Every other exception keeps the generic message
                # rather than leaking raw DB/provider error detail.
                message = str(exc) if isinstance(exc, BudgetExceededError) else "Item regeneration failed. Please try again."
                set_failed(db, job, message)
            except Exception:  # pragma: no cover - best-effort status write
                pass
    finally:
        db.close()


def run_regenerate_block_job(job_id: str) -> None:
    """Full-block IMPROVISE regeneration as a background job."""
    db = SessionLocal()
    job = None
    try:
        job = db.query(GenerationJob).filter(GenerationJob.id == job_id).first()
        if not job:
            _log.error("Regenerate-block job %s not found", job_id)
            return
        if job.status == JobStatus.CANCELLED:
            _log.info("Regenerate-block job %s cancelled before start", job_id)
            return
        if job.status == JobStatus.COMPLETED:
            _log.info("Regenerate-block job %s already completed — skipping", job_id)
            return

        from app.api.v1.routers.blocks import execute_regenerate_block
        from app.schemas.block import BlockRegenerateRequest
        import types

        params = json.loads(job.request_json or "{}")
        block_id = int(params["block_id"])
        request = BlockRegenerateRequest(
            model_choice=params.get("model_choice", "GPT-5.4"),
            feedback_instruction=params.get("feedback_instruction") or "",
        )
        user = types.SimpleNamespace(
            username=params.get("user_name") or job.created_by or "cas-user",
            id=params.get("user_id"),
            role=params.get("role", "user"),
        )
        set_running(db, job, 25, "Regenerating block...")
        result = execute_regenerate_block(db, block_id, request, user)
        job.result_json = json.dumps(
            result.model_dump() if hasattr(result, "model_dump") else {
                "block_id": block_id,
                "content": getattr(result, "content", None),
            },
            ensure_ascii=False,
            default=str,
        )
        db.commit()
        set_completed(db, job, block_id)
        _log.info("block_regenerated_job  block_id=%s job=%s", block_id, job_id)

    except Exception as exc:  # noqa: BLE001
        _log.exception("Regenerate-block job %s failed: %s", job_id, exc)
        if job is not None:
            try:
                message = str(exc) if isinstance(exc, BudgetExceededError) else (
                    "Block regeneration failed. Please try again."
                )
                detail = getattr(exc, "detail", None)
                if isinstance(detail, str) and detail.strip() and not isinstance(exc, BudgetExceededError):
                    message = detail.strip()[:2000]
                set_failed(db, job, message)
            except Exception:  # pragma: no cover
                pass
    finally:
        db.close()


def run_apply_feedback_job(job_id: str) -> None:
    """Apply selected feedback and regenerate a module's blocks — as a job.

    ``POST /feedback/apply`` used to run this inline in the request, one LLM call
    per block, so a module with several blocks blew past the browser's 120s
    timeout and the request was cancelled client-side while the server kept
    working (observed 2026-08). It is now a background job: the endpoint
    validates + enqueues and returns a ``job_id``; the frontend polls
    ``GET /jobs/{job_id}`` and reads the summary from
    ``GET /feedback/apply-result/{job_id}`` on completion.

    The endpoint has already authorised the caller and resolved the target
    blueprint, so this worker only re-loads the rows by id (no tenant filter)
    and does the heavy regeneration. Idempotent under Celery retry: an
    already-completed job is a no-op.
    """
    db = SessionLocal()
    job = None
    try:
        job = db.query(GenerationJob).filter(GenerationJob.id == job_id).first()
        if not job:
            _log.error("Apply-feedback job %s not found", job_id)
            return
        if job.status == JobStatus.CANCELLED:
            _log.info("Apply-feedback job %s cancelled before start", job_id)
            return
        if job.status == JobStatus.COMPLETED:
            _log.info("Apply-feedback job %s already completed — skipping duplicate run", job_id)
            return

        from promptops_app.database import Course, FeedbackItem, ModuleBlueprint
        from promptops_app.repositories import feedback_repository
        from promptops_app.services.feedback_service import apply_feedback_to_module

        params = json.loads(job.request_json)
        item_ids = params["item_ids"]
        blueprint_id = params["blueprint_id"]
        course_id = params["course_id"]
        user_name = params.get("user_name", "")

        set_running(db, job, 15, "Loading feedback and module...")
        items = (
            db.query(FeedbackItem)
            .filter(FeedbackItem.id.in_(item_ids), FeedbackItem.status == "active")
            .all()
        )
        if not items:
            set_failed(db, job, "No active feedback items found to apply.")
            return
        blueprint = db.query(ModuleBlueprint).filter(ModuleBlueprint.id == blueprint_id).first()
        if blueprint is None:
            set_failed(db, job, "Target module was not found.")
            return
        course = db.query(Course).filter(Course.id == course_id).first()
        if course is None:
            set_failed(db, job, "Course was not found.")
            return

        set_running(db, job, 45, "Regenerating module content...")
        instruction, regenerated, skipped = apply_feedback_to_module(
            db, items=items, blueprint=blueprint, course=course, created_by=user_name,
        )

        set_running(db, job, 90, "Saving...")
        module_label = feedback_repository.format_module_label(blueprint)
        # Persist the summary the frontend toast needs; the status poll only
        # reports terminal state, so the counts are read back via
        # GET /feedback/apply-result/{job_id} keyed on this job.
        job.result_json = json.dumps({
            "instruction": instruction,
            "regenerated": regenerated,
            "skipped": skipped,
            "blueprint_id": blueprint_id,
            "module_label": module_label,
        }, ensure_ascii=False)
        db.commit()
        # result_entity_id carries the blueprint for traceability (apply has no
        # single generation row); the real summary lives in result_json above.
        set_completed(db, job, blueprint_id)
        _log.info(
            "feedback_apply_job_done user=%s course=%s blueprint=%s items=%d regenerated=%d skipped=%d job=%s",
            user_name, course_id, blueprint_id, len(items), len(regenerated), skipped, job_id,
        )

    except Exception as exc:  # noqa: BLE001 - background boundary; log full, expose clean
        _log.exception("Apply-feedback job %s failed: %s", job_id, exc)
        if job is not None:
            try:
                message = str(exc) if isinstance(exc, BudgetExceededError) else "Applying feedback failed. Please try again."
                set_failed(db, job, message)
            except Exception:  # pragma: no cover - best-effort status write
                pass
    finally:
        db.close()
