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
from promptops_app.jobs import block_wide_jobs, generation_jobs, import_jobs, regen_jobs


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


# Maps a plain job function → its Celery task, so the dispatch layer can accept
# the same ``run_*`` reference the ThreadPoolExecutor path uses and route it to
# the matching task without the call sites needing to know about Celery.
TASK_FOR_FUNC = {
    generation_jobs.run_generation_job: run_generation_task,
    import_jobs.run_import_job: run_import_task,
    import_jobs.run_reverse_gen_job: run_reverse_gen_task,
    regen_jobs.run_regenerate_item_job: run_regenerate_item_task,
    block_wide_jobs.run_block_wide_job: run_block_wide_task,
}
