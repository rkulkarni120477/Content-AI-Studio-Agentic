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
                set_failed(db, job, "Item regeneration failed. Please try again.")
            except Exception:  # pragma: no cover - best-effort status write
                pass
    finally:
        db.close()
