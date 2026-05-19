"""Global ThreadPoolExecutor for background generation jobs.

Design constraints
------------------
* Module-level singleton: one executor per process, shared across all
  Streamlit sessions.  Streamlit re-runs the script on every interaction
  but Python module state persists across reruns within the same process.
* Max 3 concurrent workers to avoid hitting LLM rate limits or OOM.
* Exceptions in worker threads are logged so they don't get swallowed.

Celery migration path
---------------------
Replace ``submit(fn, job_id)`` with ``fn.delay(job_id)`` where ``fn`` is
decorated with ``@shared_task``.  Remove this module entirely once migrated.
"""

import logging
from concurrent.futures import ThreadPoolExecutor

from promptops_app.core.config import settings as _cfg

_log = logging.getLogger(__name__)

MAX_WORKERS = _cfg.max_background_workers

_executor: ThreadPoolExecutor = ThreadPoolExecutor(
    max_workers=MAX_WORKERS,
    thread_name_prefix="gen_job",
)


def submit(fn, *args, **kwargs):
    """Submit *fn(*args, **kwargs)* to the background pool.

    Returns the :class:`~concurrent.futures.Future` immediately so callers
    can optionally inspect it.  The caller should store the ``job_id`` in
    ``st.session_state`` for UI polling instead.
    """
    future = _executor.submit(fn, *args, **kwargs)
    future.add_done_callback(_on_done)
    return future


def _on_done(future):
    exc = future.exception()
    if exc:
        _log.error("Background job raised unhandled exception: %s", exc, exc_info=exc)


def shutdown(wait: bool = True) -> None:
    """Drain the executor gracefully — call from app teardown if needed."""
    _executor.shutdown(wait=wait)
