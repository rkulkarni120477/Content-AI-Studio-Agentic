"""Registry for block digest builds: liveness, per-day progress, and the result.

Why this exists at all
----------------------
A cold 20-day build takes 5-20 minutes. It used to run inside the HTTP request that
asked for it, which made the request itself the only channel for both progress and
the result — so a single transient network fault discarded a build that had already
completed and been paid for. Observed in production 2026-08-13: all 20 days built
successfully, then ``503 DIS unavailable: timed out`` at 8m07s, and the report was
gone.

This registry decouples the two. The build reports into it as it runs; callers read
liveness, counts, and finally the report out of it. The HTTP request that starts a
build no longer has to survive the build.

Why progress is not derived from the digest store
-------------------------------------------------
The obvious implementation — count the digests that exist for a block — is wrong,
and wrong in the direction that looks fine in a demo. Digests persist across builds,
so a block that already holds 20 documents (say 15 ``ok`` and 5 ``failed`` from an
earlier attempt) would report 20/20 the instant a retry started and never move.
Progress has to be reported by the build, not inferred from what it writes.

Why in-process
--------------
This service runs as a single uvicorn process (``uvicorn main:app`` — no
``--workers``, no replicas), so the process serving a poll is the one doing the work.
That keeps this to a dict and a lock: no schema, no store round-trip on a poll, and
nothing to clean up if the process dies mid-build.

Two consequences are load-bearing, and callers are built to expect them:

1. **A restart loses in-flight state.** A poller that saw an entry and then sees
   ``None`` must treat that as "DIS restarted", not "build finished" — see
   ``DISClient.build_digests_sync``, which re-issues the build. Per-day digests are
   content-addressed and cached, so re-issuing is cheap and converges.
2. **If DIS is ever run with multiple workers or replicas this must move to a shared
   store** (the digest index, or Redis). A poll would otherwise hit a worker that
   knows nothing about the build and report "nothing running" while one is in flight.
   That is the one change that silently breaks it, so it is stated here rather than
   discovered.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

#: Bounded so a long-lived process cannot accumulate an entry per block forever. Only
#: the most recent builds are interesting, and a terminal entry is kept deliberately —
#: it carries the report the caller has not collected yet.
_MAX_TRACKED = 32

#: Reserved but the build has not reported a day count yet (enumerate is still
#: running). Distinct from RUNNING so a poller can tell "starting up" from "running
#: with zero days done", and so ``reserve`` can hold the single-flight slot during the
#: window before ``start``.
STATE_STARTING = "starting"
STATE_RUNNING = "running"
STATE_DONE = "done"
STATE_FAILED = "failed"

#: A build in one of these states is over; its slot may be re-reserved and its entry
#: may be evicted under pressure.
TERMINAL_STATES = (STATE_DONE, STATE_FAILED)

_lock = threading.Lock()
_builds: "Dict[str, Dict[str, Any]]" = {}


def _key(client_id: str, block: str) -> str:
    return f"{client_id or ''}:{block or ''}"


def _evict_if_needed() -> None:
    """Make room, preferring entries whose build is over. Called with ``_lock`` held.

    Terminal entries go first. Evicting a *live* one is worse than merely losing
    progress: its poller reads the absence as "DIS restarted" and re-issues the build,
    so a wrongly evicted entry can cost a duplicate MAP pass.

    But the cap is still enforced when every entry is live, because an unbounded dict
    in a long-lived process is the worse failure of the two. Reaching that state means
    32 builds are simultaneously live — which either is not a real workload, or is one
    that needs the shared store this module's docstring calls for. The duplicate-build
    risk is also bounded on the other side: the poller re-issues at most twice, and
    per-day digests are cached, so a re-issue resumes rather than rebuilding.
    """
    if len(_builds) < _MAX_TRACKED:
        return
    terminal = [k for k, v in _builds.items() if v.get("state") in TERMINAL_STATES]
    candidates = terminal or list(_builds)
    _builds.pop(min(candidates, key=lambda k: _builds[k].get("started_at", 0)), None)


def reserve(client_id: str, block: str) -> bool:
    """Claim the single-flight slot for this block. False if a build is already live.

    This is the concurrency guard for the whole build path. Two users pressing
    Generate on the same block, or a poller re-issuing a build whose original is
    still running, would otherwise run concurrent MAP passes over the same days:
    double the Bedrock spend for one result, plus racing writers on the same
    content-addressed digest documents.

    Reserving is separate from ``start`` because ``start`` needs the day count, which
    is only known after ENUMERATE — a multi-second OpenSearch round trip. The slot has
    to be held during that window, and a poll arriving in it has to see a live build
    rather than nothing.
    """
    with _lock:
        entry = _builds.get(_key(client_id, block))
        if entry is not None and entry.get("state") not in TERMINAL_STATES:
            return False
        _evict_if_needed()
        _builds[_key(client_id, block)] = {
            "state": STATE_STARTING,
            "total": 0, "built": 0, "failed": 0, "cached": 0,
            "started_at": time.time(),
            "finished": False,
            "report": None,
            "error": "",
        }
        return True


def start(client_id: str, block: str, *, total: int) -> None:
    """Record the day count and move to RUNNING, creating the entry if unreserved.

    Preserves ``started_at`` from an existing reservation so elapsed time covers the
    ENUMERATE that preceded this call — that is real time the user waited, and a
    build that looked instantaneous would hide a slow enumerate.
    """
    with _lock:
        key = _key(client_id, block)
        prev = _builds.get(key)
        if prev is None:
            _evict_if_needed()
        _builds[key] = {
            "state": STATE_RUNNING,
            "total": int(total or 0),
            "built": 0, "failed": 0, "cached": 0,
            "started_at": (prev or {}).get("started_at") or time.time(),
            "finished": False,
            "report": None,
            "error": "",
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
    """Mark the day loop done, keeping the final counts readable.

    Deliberately does NOT delete the entry: a poller on an interval would otherwise
    see no build at all and have to guess whether it finished or vanished.

    This says "the day loop is over", not "the build succeeded" — the build function
    calls it from a ``finally``, so it runs on the failure path too. ``complete`` and
    ``fail`` set the outcome, and both run after this.
    """
    with _lock:
        entry = _builds.get(_key(client_id, block))
        if entry is not None:
            entry["finished"] = True


def complete(client_id: str, block: str, report: Dict[str, Any]) -> None:
    """Publish a successful build's report for the caller to collect.

    The report is the deliverable of the whole build. Holding it here is what makes
    the starting request disposable: the caller collects it on a later poll, so a
    dropped connection costs a poll interval instead of the entire build.

    A vanished entry (evicted under cap pressure, or lost to a restart that this
    process somehow survived) is a no-op rather than a re-creation, and that direction
    is deliberate: the slot may since have been re-reserved by a newer build for the
    same block, and re-creating would hand that build's poller this build's report.
    Dropping the report costs a re-issue over cached days; serving the wrong one is
    silently incorrect output.
    """
    with _lock:
        entry = _builds.get(_key(client_id, block))
        if entry is None:
            return
        entry["state"] = STATE_DONE
        entry["finished"] = True
        entry["report"] = report


def fail(client_id: str, block: str, error: str) -> None:
    """Record that the build raised, with the reason a caller should surface.

    A failed build must reach a terminal state, not simply stop reporting: a poller
    that only knows "not done" would wait out its whole deadline on a build that died
    in the first second.
    """
    with _lock:
        entry = _builds.get(_key(client_id, block))
        if entry is None:
            return
        entry["state"] = STATE_FAILED
        entry["finished"] = True
        entry["error"] = error or "build failed"


def snapshot(client_id: str, block: str, *,
             include_result: bool = False) -> Optional[Dict[str, Any]]:
    """Current state of a block's build, or None if none has been tracked.

    ``include_result`` adds the build report. Off by default because the browser
    polls this every 2 seconds through CAS and has no use for the report, while the
    server-to-server poller that collects it calls once at the end.
    """
    with _lock:
        entry = _builds.get(_key(client_id, block))
        if entry is None:
            return None
        done = entry["built"] + entry["failed"] + entry["cached"]
        total = entry["total"]
        out = {
            "state": entry.get("state", STATE_RUNNING),
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
            "error": entry.get("error", ""),
        }
        if include_result:
            out["report"] = entry.get("report")
        return out


def reset_for_tests() -> None:
    """Clear all tracked builds. Test-only."""
    with _lock:
        _builds.clear()
