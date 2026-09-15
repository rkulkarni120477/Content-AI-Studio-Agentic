"""Celery task wrappers for the generation/import pipeline (P4.1).

These are deliberately *thin*: each task delegates straight to the existing
plain ``run_*`` function (``generation_jobs.run_generation_job`` etc.), which
already has the Celery-compatible single-``job_id`` signature, opens its own
``SessionLocal``, and drives progress through the shared ``job_status`` helpers.

Keeping the real work in the plain functions (rather than decorating them
directly) means:
  * the in-process ThreadPoolExecutor fallback (``job_runner``) still calls the
    exact same code path, so behaviour is identical whichever backend runs it;
  * the unit/characterization tests that call ``run_generation_job(job_id)``
    directly need no Celery/broker and are unaffected.

At-least-once delivery
----------------------
``celery_app`` sets ``task_acks_late=True`` + ``task_reject_on_worker_lost=True``
globally, so a worker crash mid-task re-queues the job. Idempotency lives inside
the ``run_*`` functions (they detect and adopt their own prior output keyed by
``job_id``), so a retry never double-charges usage or duplicates content.
"""
from __future__ import annotations

from promptops_app.celery_app import celery_app
from promptops_app.jobs import (
    block_wide_jobs,
    design_jobs,
    generation_jobs,
    import_jobs,
    outline_import_jobs,
    regen_jobs,
)


@celery_app.task(name="gen.run")
def run_generation_task(job_id: str) -> None:
    """Celery entry point for the main generation pipeline."""
    generation_jobs.run_generation_job(job_id)


@celery_app.task(name="import.run")
def run_import_task(job_id: str) -> None:
    """Celery entry point for the IMSCC import pipeline."""
    import_jobs.run_import_job(job_id)


@celery_app.task(name="import.reverse_gen")
def run_reverse_gen_task(job_id: str) -> None:
    """Celery entry point for the reverse-generation retry pipeline."""
    import_jobs.run_reverse_gen_job(job_id)


@celery_app.task(name="block.regenerate_item")
def run_regenerate_item_task(job_id: str) -> None:
    """Celery entry point for single-item block regeneration (P4.5)."""
    regen_jobs.run_regenerate_item_job(job_id)


@celery_app.task(name="block.regenerate")
def run_regenerate_block_task(job_id: str) -> None:
    """Celery entry point for full-block regeneration."""
    regen_jobs.run_regenerate_block_job(job_id)


@celery_app.task(name="feedback.apply")
def run_apply_feedback_task(job_id: str) -> None:
    """Celery entry point for apply-feedback module regeneration.

    One LLM call per block in the module, so it can run for minutes — moved off
    the request thread to stop the browser's 120s timeout cancelling it mid-run.
    """
    regen_jobs.run_apply_feedback_job(job_id)


@celery_app.task(name="import.outline")
def run_outline_import_task(job_id: str) -> None:
    """Celery entry point for async Outline import (extract + LLM restructure +
    persist). Moved off the request thread so a reverse proxy can't 504 the slow
    restructure call on a large file."""
    outline_import_jobs.run_outline_import_job(job_id)


@celery_app.task(name="block.run_block_wide")
def run_block_wide_task(job_id: str) -> None:
    """Celery entry point for block-wide CDD/Blueprint generation (digest pipeline).

    This is the longest and most memory-hungry job in the product — a cold 20-day
    build runs ENUMERATE + one MAP call per day + REDUCE, measured at ~5 minutes
    with a 1200s client timeout. Running it in the API container's
    ThreadPoolExecutor meant any web restart, redeploy, or OOM killed it mid-run
    with no handler, orphaning the job row at status=running forever (observed in
    prod 2026-08-12). On Celery it gets the worker's own memory budget, and
    task_reject_on_worker_lost re-queues it instead of losing it.
    """
    block_wide_jobs.run_block_wide_job(job_id)


@celery_app.task(name="design.cdd_generate")
def run_cdd_generate_task(job_id: str) -> None:
    design_jobs.run_cdd_generate_job(job_id)


@celery_app.task(name="design.blueprint_generate")
def run_blueprint_generate_task(job_id: str) -> None:
    design_jobs.run_blueprint_generate_job(job_id)


@celery_app.task(name="design.style_understand")
def run_style_understand_task(job_id: str) -> None:
    design_jobs.run_style_understand_job(job_id)


@celery_app.task(name="design.cdd_regen_item")
def run_cdd_regen_item_task(job_id: str) -> None:
    design_jobs.run_cdd_regen_item_job(job_id)


@celery_app.task(name="design.cdd_regen_section")
def run_cdd_regen_section_task(job_id: str) -> None:
    design_jobs.run_cdd_regen_section_job(job_id)


@celery_app.task(name="design.blueprint_regen_item")
def run_blueprint_regen_item_task(job_id: str) -> None:
    design_jobs.run_blueprint_regen_item_job(job_id)


@celery_app.task(name="design.blueprint_regen_section")
def run_blueprint_regen_section_task(job_id: str) -> None:
    design_jobs.run_blueprint_regen_section_job(job_id)


# Maps a plain job function → its Celery task, so the dispatch layer can accept
# the same ``run_*`` reference the ThreadPoolExecutor path uses and route it to
# the matching task without the call sites needing to know about Celery.
TASK_FOR_FUNC = {
    generation_jobs.run_generation_job: run_generation_task,
    import_jobs.run_import_job: run_import_task,
    import_jobs.run_reverse_gen_job: run_reverse_gen_task,
    regen_jobs.run_regenerate_item_job: run_regenerate_item_task,
    regen_jobs.run_regenerate_block_job: run_regenerate_block_task,
    regen_jobs.run_apply_feedback_job: run_apply_feedback_task,
    outline_import_jobs.run_outline_import_job: run_outline_import_task,
    block_wide_jobs.run_block_wide_job: run_block_wide_task,
    design_jobs.run_cdd_generate_job: run_cdd_generate_task,
    design_jobs.run_blueprint_generate_job: run_blueprint_generate_task,
    design_jobs.run_style_understand_job: run_style_understand_task,
    design_jobs.run_cdd_regen_item_job: run_cdd_regen_item_task,
    design_jobs.run_cdd_regen_section_job: run_cdd_regen_section_task,
    design_jobs.run_blueprint_regen_item_job: run_blueprint_regen_item_task,
    design_jobs.run_blueprint_regen_section_job: run_blueprint_regen_section_task,
}
