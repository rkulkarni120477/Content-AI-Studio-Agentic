"""Cross-process advisory locks for shared mutable S3 state (Tier 2 Step 3).

Two shared JSON objects are read-modify-written by several code paths and — once
the ingestion worker (Tier 2 Step 6) exists — by several PROCESSES at once:

  * the Source Library **index**  (services/source_library.py:
    ``upsert_source_record`` / ``update_source_record_status`` / delete), and
  * the client **dedup manifest** (services/agents/finalize_agent.py:
    ``FinalizeAgent._update_manifest``).

Each is a full read → mutate → write over one JSON blob per client. With no
coordination, two concurrent writers both read version N, each writes N+1, and
one update is silently lost — dropping a Source Library row or corrupting dedup
during exactly the folder-upload scenario this migration exists to fix.

This module serializes those writers with a **per-client** lock.

Safety design (so introducing this cannot break current ingestion):
  * **Default backend is a NO-OP** (`NullLock`). Until a Redis URL is configured
    (via ``DIS_REDIS_URL``), every lock is a no-op and behavior is byte-for-byte
    what it is today (single process → no contention). The gate is armed only
    when the worker/Redis land.
  * `redis` is imported **lazily**, so this module adds no new hard dependency
    until Redis is actually configured.
  * The Redis lock auto-expires (TTL), so a crashed holder can never wedge
    writers forever, and releases via a compare-and-delete so a process can only
    release the token it owns.
  * If Redis is configured but unreachable at acquire time, the lock **degrades
    to a no-op with a loud warning** by default (an operator alert, not a hard
    upload failure). Set ``DIS_LOCK_STRICT=1`` to raise instead.

S3 backends write whole objects atomically (PutObject is atomic), so READERS
never see a torn object and do not need this lock — only concurrent read-modify-
write WRITERS do.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Any, Optional

log = logging.getLogger(__name__)

_DEFAULT_TTL_MS = 30_000          # lock auto-expires; a crashed holder can't wedge writers
_DEFAULT_ACQUIRE_TIMEOUT_S = 20.0  # max wait to acquire before giving up
_RETRY_DELAY_S = 0.05

# Compare-and-delete: only delete the key if we still own the token. Prevents a
# process whose TTL already expired from deleting a lock a different process now holds.
_RELEASE_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) else return 0 end"
)


class LockError(RuntimeError):
    """Raised when a lock cannot be acquired within the timeout (or on Redis error in strict mode)."""


class NullLock:
    """No-op lock — preserves single-process behavior when no Redis is configured."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __enter__(self) -> "NullLock":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class RedisLock:
    """A single-holder, TTL-guarded, token-owned lock backed by Redis.

    Acquire = ``SET key token NX PX ttl`` in a bounded retry loop.
    Release = compare-and-delete via a Lua script (only if we still own the token).
    """

    def __init__(
        self,
        client: Any,
        name: str,
        *,
        ttl_ms: int = _DEFAULT_TTL_MS,
        acquire_timeout_s: float = _DEFAULT_ACQUIRE_TIMEOUT_S,
        strict: bool = False,
    ) -> None:
        self._client = client
        self._key = f"dis:lock:{name}"
        self._ttl_ms = ttl_ms
        self._acquire_timeout_s = acquire_timeout_s
        self._strict = strict
        self._token = uuid.uuid4().hex
        self._held = False

    def __enter__(self) -> "RedisLock":
        deadline = time.monotonic() + self._acquire_timeout_s
        while True:
            try:
                if self._client.set(self._key, self._token, nx=True, px=self._ttl_ms):
                    self._held = True
                    return self
            except Exception as exc:  # Redis unreachable / errored
                if self._strict:
                    raise LockError(f"Redis error acquiring {self._key}: {exc}") from exc
                log.warning(
                    "Lock %s: Redis error (%s) — proceeding WITHOUT the lock. "
                    "Concurrent writers to this client's shared state are unguarded "
                    "until Redis recovers. Set DIS_LOCK_STRICT=1 to fail hard instead.",
                    self._key, exc,
                )
                self._held = False
                return self
            if time.monotonic() >= deadline:
                if self._strict:
                    raise LockError(f"Could not acquire {self._key} within {self._acquire_timeout_s}s")
                log.warning(
                    "Lock %s: not acquired within %.0fs — proceeding WITHOUT the lock.",
                    self._key, self._acquire_timeout_s,
                )
                return self
            time.sleep(_RETRY_DELAY_S)

    def __exit__(self, *exc: Any) -> bool:
        if self._held:
            try:
                self._client.eval(_RELEASE_LUA, 1, self._key, self._token)
            except Exception as exc:
                # TTL will expire the key anyway; never mask the body's exception.
                log.warning("Lock %s: release failed (%s); relying on TTL expiry.", self._key, exc)
            finally:
                self._held = False
        return False


# --- client wiring (lazy, config-driven) ------------------------------------

_redis_client: Optional[Any] = None
_redis_init_done = False


def _strict_mode() -> bool:
    return str(os.environ.get("DIS_LOCK_STRICT", "")).lower() in ("1", "true", "yes", "on")


def _get_redis_client() -> Optional[Any]:
    """Return a shared Redis client, or None if not configured/available.

    Lazily imports ``redis`` so this module carries no hard dependency until a
    ``DIS_REDIS_URL`` is set (which happens when the worker/queue land).
    """
    global _redis_client, _redis_init_done
    if _redis_init_done:
        return _redis_client
    _redis_init_done = True
    url = os.environ.get("DIS_REDIS_URL", "").strip()
    if not url:
        _redis_client = None
        return None
    try:
        import redis  # lazy: not required until Redis is configured
        _redis_client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=5)
        log.info("DIS shared-state locking enabled via Redis.")
    except Exception as exc:
        log.warning("DIS_REDIS_URL set but Redis client could not be created (%s); locking disabled.", exc)
        _redis_client = None
    return _redis_client


def reset_for_tests() -> None:
    """Clear the cached client so tests can re-init with different env."""
    global _redis_client, _redis_init_done
    _redis_client = None
    _redis_init_done = False


def client_lock(name: str):
    """Return a context-manager lock for ``name`` — a RedisLock when configured, else a NullLock."""
    client = _get_redis_client()
    if client is None:
        return NullLock(name)
    return RedisLock(client, name, strict=_strict_mode())


def source_index_lock(tenant_id: str, client_id: str):
    """Serialize read-modify-write on one client's Source Library index."""
    return client_lock(f"source_index:{tenant_id}:{client_id}")


def dedup_manifest_lock(tenant_id: str, client_id: str):
    """Serialize read-modify-write on one client's dedup manifest."""
    return client_lock(f"dedup_manifest:{tenant_id}:{client_id}")
