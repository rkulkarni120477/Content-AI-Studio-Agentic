"""Live per-day progress for an in-flight block digest build.

Why this is not derived from the digest store
---------------------------------------------
The obvious implementation — count the digests that exist for a block — is wrong,
and wrong in the direction that looks fine in a demo. Digests persist across builds,
so a block that already holds 20 documents (say 15 ``ok`` and 5 ``failed`` from an
earlier attempt) would report 20/20 the instant a retry started and never move.
Progress has to be reported by the build, not inferred from what it writes.

Why in-process
--------------
A build's progress is ephemeral and only interesting while it runs, and this service
runs as a single uvicorn process (``uvicorn main:app`` — no ``--workers``, no
replicas), so the process serving the progress request is the one doing the work.
That keeps this to a dict and a lock: no schema, no store round-trip on a poll, and
nothing to clean up if the process dies mid-build.

**If DIS is ever run with multiple workers or replicas, this must move to a shared
store** (the digest index, or Redis) — a poll would otherwise hit a worker that knows
nothing about the build and report "no build running" while one is in flight. That is
the one change that silently breaks it, so it is stated here rather than discovered.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

#: Bounded so a long-lived process cannot accumulate an entry per block forever. Only
#: the most recent builds are interesting, and a finished entry is kept deliberately —
#: see ``finish`` — so a poll arriving just after the last day still sees 20/20 rather
#: than "nothing running", which would read as a lost build.
_MAX_TRACKED = 32

_lock = threading.Lock()
_builds: "Dict[str, Dict[str, Any]]" = {}


def _key(client_id: str, block: str) -> str:
    return f"{client_id or ''}:{block or ''}"


def start(client_id: str, block: str, *, total: int) -> None:
    """Begin tracking a build of *total* days, discarding any earlier attempt's state."""
    with _lock:
        if len(_builds) >= _MAX_TRACKED:
            # Evict the oldest by start time. Cheap at this size and keeps the common
            # case (a handful of blocks) allocation-free.
            oldest = min(_builds, key=lambda k: _builds[k].get("started_at", 0))
            _builds.pop(oldest, None)
        _builds[_key(client_id, block)] = {
            "total": int(total or 0),
            "built": 0, "failed": 0, "cached": 0,
            "started_at": time.time(),
            "finished": False,
        }


def record(client_id: str, block: str, status: str) -> None:
    """Record one day's outcome: ``built``, ``failed`` or ``cached``.

    Silently ignores a block that is not being tracked, so a caller that reports
    progress can never be the reason a build fails.
    """
    with _lock:
        entry = _builds.get(_key(client_id, block))
        if entry is None:
            return
        if status in ("built", "failed", "cached"):
            entry[status] = entry.get(status, 0) + 1


def finish(client_id: str, block: str) -> None:
    """Mark the build complete, keeping the final counts readable.

    Deliberately does NOT delete the entry: the frontend polls on an interval, so the
    poll that lands just after the last day would otherwise see no build at all and
    have to guess whether it finished or vanished.
    """
    with _lock:
        entry = _builds.get(_key(client_id, block))
        if entry is not None:
            entry["finished"] = True


def snapshot(client_id: str, block: str) -> Optional[Dict[str, Any]]:
    """Current counts for a block, or None if no build has been tracked for it."""
    with _lock:
        entry = _builds.get(_key(client_id, block))
        if entry is None:
            return None
        done = entry["built"] + entry["failed"] + entry["cached"]
        total = entry["total"]
        return {
            "total": total,
            "done": done,
            "built": entry["built"],
            "failed": entry["failed"],
            "cached": entry["cached"],
            # Never below zero even if a caller over-reports: a negative "remaining"
            # would render as nonsense in a progress bar.
            "remaining": max(0, total - done),
            "finished": bool(entry["finished"]),
            "elapsed_seconds": round(time.time() - entry["started_at"], 1),
        }


def reset_for_tests() -> None:
    """Clear all tracked builds. Test-only."""
    with _lock:
        _builds.clear()
