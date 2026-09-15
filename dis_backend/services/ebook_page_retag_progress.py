"""In-process registry for ebook page re-tag jobs (live done/remaining).

Mirrors ``services.digests.progress``: a long Retry-all used to block one HTTP
request for hundreds of pages, so the browser sat on "Retrying…" until a gateway
timeout. Progress is reported BY the worker after each batch of 10 pages — not
inferred from tagging_status counts (those already include pages from earlier
attempts and would look "done" the moment a retry started).

Same single-uvicorn assumption as digest progress: restart loses the in-memory
entry, but already-persisted page tags survive; Retry-all again resumes leftover
non-ok pages.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

_MAX_TRACKED = 64

STATE_STARTING = "starting"
STATE_RUNNING = "running"
STATE_DONE = "done"
STATE_FAILED = "failed"

TERMINAL_STATES = (STATE_DONE, STATE_FAILED)

_lock = threading.Lock()
_jobs: "Dict[str, Dict[str, Any]]" = {}


def _key(client_id: str, job_id: str) -> str:
    return f"{client_id or ''}:{job_id or ''}"


def _evict_if_needed() -> None:
    if len(_jobs) < _MAX_TRACKED:
        return
    terminal = [k for k, v in _jobs.items() if v.get("state") in TERMINAL_STATES]
    candidates = terminal or list(_jobs)
    _jobs.pop(min(candidates, key=lambda k: _jobs[k].get("started_at", 0)), None)


def reserve(client_id: str, job_id: str) -> bool:
    """Claim the single-flight slot for this document. False if a retag is live."""
    with _lock:
        entry = _jobs.get(_key(client_id, job_id))
        if entry is not None and entry.get("state") not in TERMINAL_STATES:
            return False
        _evict_if_needed()
        _jobs[_key(client_id, job_id)] = {
            "state": STATE_STARTING,
            "total": 0,
            "done": 0,
            "failed": 0,
            "remaining": 0,
            "last_page": None,
            "tagging_failed_count": 0,
            "tagging_pending_count": 0,
            "started_at": time.time(),
            "error": "",
        }
        return True


def start(client_id: str, job_id: str, *, total: int) -> None:
    """Move to RUNNING once the unit count is known."""
    with _lock:
        key = _key(client_id, job_id)
        prev = _jobs.get(key)
        if prev is None:
            _evict_if_needed()
        total_n = int(total or 0)
        _jobs[key] = {
            "state": STATE_RUNNING,
            "total": total_n,
            "done": 0,
            "failed": 0,
            "remaining": total_n,
            "last_page": None,
            "tagging_failed_count": 0,
            "tagging_pending_count": 0,
            "started_at": (prev or {}).get("started_at") or time.time(),
            "error": "",
        }


def update(
    client_id: str,
    job_id: str,
    *,
    done: int,
    failed: int = 0,
    last_page: Any = None,
    tagging_failed_count: int = 0,
    tagging_pending_count: int = 0,
) -> None:
    """Overwrite counters after a batch persists. Silent no-op if untracked."""
    with _lock:
        entry = _jobs.get(_key(client_id, job_id))
        if entry is None:
            return
        total = int(entry.get("total") or 0)
        done_n = max(0, int(done))
        entry["done"] = done_n
        entry["failed"] = max(0, int(failed))
        entry["remaining"] = max(0, total - done_n)
        if last_page is not None:
            entry["last_page"] = last_page
        entry["tagging_failed_count"] = int(tagging_failed_count or 0)
        entry["tagging_pending_count"] = int(tagging_pending_count or 0)


def complete(
    client_id: str,
    job_id: str,
    *,
    tagging_failed_count: int = 0,
    tagging_pending_count: int = 0,
) -> None:
    with _lock:
        entry = _jobs.get(_key(client_id, job_id))
        if entry is None:
            return
        entry["state"] = STATE_DONE
        entry["done"] = int(entry.get("total") or 0)
        entry["remaining"] = 0
        entry["tagging_failed_count"] = int(tagging_failed_count or 0)
        entry["tagging_pending_count"] = int(tagging_pending_count or 0)


def fail(client_id: str, job_id: str, error: str) -> None:
    with _lock:
        entry = _jobs.get(_key(client_id, job_id))
        if entry is None:
            return
        entry["state"] = STATE_FAILED
        entry["error"] = error or "retag failed"


def snapshot(client_id: str, job_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        entry = _jobs.get(_key(client_id, job_id))
        if entry is None:
            return None
        total = int(entry.get("total") or 0)
        done = int(entry.get("done") or 0)
        return {
            "state": entry.get("state", STATE_RUNNING),
            "total": total,
            "done": done,
            "failed": int(entry.get("failed") or 0),
            "remaining": max(0, total - done),
            "last_page": entry.get("last_page"),
            "tagging_failed_count": int(entry.get("tagging_failed_count") or 0),
            "tagging_pending_count": int(entry.get("tagging_pending_count") or 0),
            "elapsed_seconds": round(time.time() - float(entry.get("started_at") or time.time()), 1),
            "error": entry.get("error") or "",
        }


def reset_for_tests() -> None:
    """Clear all tracked retag jobs. Test-only."""
    with _lock:
        _jobs.clear()
