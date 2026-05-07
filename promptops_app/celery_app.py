"""Celery application singleton for Content AI Studio.

Import this module wherever you need to enqueue or define tasks:

    from promptops_app.celery_app import celery_app

To start a worker (inside the container or locally):

    celery -A promptops_app.celery_app worker --loglevel=info --concurrency=4
"""
from celery import Celery
from promptops_app.core.config import settings

celery_app = Celery(
    "contentai",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "promptops_app.jobs.plagiarism_jobs",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Reliability settings
    task_acks_late=True,               # ack only after the task completes
    task_reject_on_worker_lost=True,   # re-queue if worker dies mid-task
    worker_prefetch_multiplier=1,      # one task at a time per worker slot
    # Result expiry (keep results 24 h for polling)
    result_expires=86_400,
)
