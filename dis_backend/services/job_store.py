"""Ingestion job status store (Tier 2 Step 5).

Replaces the process-local ``_jobs`` dict in ``api/routers/ingestion.py`` so job
status is visible across processes once the pipeline runs in a separate worker
(Tier 2 Step 6). One interface, two backends:

  * ``MemoryJobStore`` — the DEFAULT; a single-process dict, behaviorally identical
    to the old ``_jobs``.
  * ``RedisJobStore`` — cross-process. Each job is a Redis HASH (per-field ``HSET``),
    so the API setting ``payload_storage_url`` and the worker setting ``status`` at
    the same time never lose each other's update (different fields → no read-modify-
    write race). Jobs are indexed per tenant for listing (no ``KEYS`` scan) and
    carry a TTL.

The backend is chosen by ``DIS_REDIS_URL`` — the SAME switch as ``services.locks``
(and it reuses that module's Redis client). Unset → ``MemoryJobStore``, so
introducing this changes nothing until the worker/Redis land.

Contract (identical across both backends — so the memory tests validate what the
ingestion code relies on):
  * ``get()`` returns a JSON-normalized **copy** (datetimes → ISO strings, enums →
    their value). Callers must NOT mutate it in place; all writes go through
    ``update()``. (This is why the old ``_jobs[id]["x"] = y`` mutate-after-get
    sites had to become ``update(id, x=y)``.)
  * ``update()`` merges the given top-level fields; missing job → no-op.
  * Records may carry extra keys beyond the response models — Pydantic ignores them.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime
from typing import Any, Dict, List, Optional


def _json_default(o: Any) -> Any:
    if isinstance(o, datetime):
        return o.isoformat()
    return str(o)


def _normalize(record: Dict[str, Any]) -> Dict[str, Any]:
    """Round-trip through JSON so both backends store identical, JSON-safe dicts
    (datetimes → ISO strings; str-enums like JobStatus → their string value)."""
    return json.loads(json.dumps(record, default=_json_default))


class MemoryJobStore:
    """Single-process store — behaviorally identical to the old ``_jobs`` dict."""

    def __init__(self) -> None:
        self._jobs: Dict[str, Dict[str, Any]] = {}

    def create(self, job_id: str, record: Dict[str, Any]) -> None:
        self._jobs[job_id] = _normalize(record)

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        rec = self._jobs.get(job_id)
        return copy.deepcopy(rec) if rec is not None else None

    def update(self, job_id: str, **fields: Any) -> None:
        rec = self._jobs.get(job_id)
        if rec is None:
            return
        rec.update(_normalize(fields))

    def list(self, tenant_id: str, *, status: str = "", user_id: Optional[str] = None,
             is_admin: bool = False, limit: int = 20) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for rec in self._jobs.values():
            if rec.get("tenant_id") != tenant_id:
                continue
            if status and rec.get("status") != status:
                continue
            if not is_admin and user_id is not None and rec.get("user_id") != user_id:
                continue
            out.append(copy.deepcopy(rec))
        return out[:limit]


class RedisJobStore:
    """Cross-process store: one Redis HASH per job, per-tenant index set for listing."""

    def __init__(self, client: Any, ttl_seconds: int = 7 * 24 * 3600) -> None:
        self._r = client
        self._ttl = ttl_seconds

    def _job_key(self, job_id: str) -> str:
        return f"dis:job:{job_id}"

    def _tenant_key(self, tenant_id: str) -> str:
        return f"dis:jobs:tenant:{tenant_id}"

    def create(self, job_id: str, record: Dict[str, Any]) -> None:
        rec = _normalize(record)
        mapping = {k: json.dumps(v) for k, v in rec.items()}
        jk = self._job_key(job_id)
        self._r.hset(jk, mapping=mapping)
        self._r.expire(jk, self._ttl)
        tenant_id = rec.get("tenant_id") or ""
        if tenant_id:
            tk = self._tenant_key(tenant_id)
            self._r.sadd(tk, job_id)
            self._r.expire(tk, self._ttl)

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        raw = self._r.hgetall(self._job_key(job_id))
        if not raw:
            return None
        return {k: json.loads(v) for k, v in raw.items()}

    def update(self, job_id: str, **fields: Any) -> None:
        jk = self._job_key(job_id)
        if not self._r.exists(jk):
            return  # match dict semantics: updating a missing/expired job is a no-op
        mapping = {k: json.dumps(v) for k, v in _normalize(fields).items()}
        if mapping:
            self._r.hset(jk, mapping=mapping)
            self._r.expire(jk, self._ttl)

    def list(self, tenant_id: str, *, status: str = "", user_id: Optional[str] = None,
             is_admin: bool = False, limit: int = 20) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for jid in self._r.smembers(self._tenant_key(tenant_id)):
            rec = self.get(jid)
            if rec is None:
                continue  # expired out from under the index; skip
            if status and rec.get("status") != status:
                continue
            if not is_admin and user_id is not None and rec.get("user_id") != user_id:
                continue
            out.append(rec)
            if len(out) >= limit:
                break
        return out


# --- singleton wiring (same DIS_REDIS_URL switch as services.locks) ----------

_store: Optional[Any] = None


def get_job_store() -> Any:
    """Return the process-wide job store — RedisJobStore when DIS_REDIS_URL is set
    (reusing services.locks' client), else MemoryJobStore."""
    global _store
    if _store is not None:
        return _store
    from services import locks
    client = locks._get_redis_client()
    _store = RedisJobStore(client) if client is not None else MemoryJobStore()
    return _store


def reset_for_tests() -> None:
    global _store
    _store = None
