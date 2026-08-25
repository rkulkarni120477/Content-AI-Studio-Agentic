"""Unit tests for the Tier 2 Step 5 JobStore (Memory + Redis backends).

Runs with no real Redis: RedisJobStore takes an injected client, so we feed it a
minimal in-memory fake implementing exactly the ops it uses (hset mapping / hgetall
/ expire / exists / sadd / smembers). The `parametrize` runs the SAME assertions
against both backends, proving identical semantics — which is what lets us trust the
ingestion refactor after only unit-testing the store.
"""
from datetime import datetime

import pytest

from models.schemas import JobStatus, JobStatusResponse
from services.job_store import MemoryJobStore, RedisJobStore


class FakeRedis:
    """Minimal hash + set Redis stand-in (decode_responses=True style: str in/out)."""

    def __init__(self):
        self.hashes = {}
        self.sets = {}

    def hset(self, key, mapping=None):
        h = self.hashes.setdefault(key, {})
        if mapping:
            h.update({str(k): str(v) for k, v in mapping.items()})
        return len(mapping or {})

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def exists(self, key):
        return 1 if key in self.hashes else 0

    def expire(self, key, ttl):
        return True

    def sadd(self, key, *vals):
        s = self.sets.setdefault(key, set())
        s.update(str(v) for v in vals)
        return len(vals)

    def smembers(self, key):
        return set(self.sets.get(key, set()))


def make_store(kind):
    return MemoryJobStore() if kind == "memory" else RedisJobStore(FakeRedis())


def _job_record(job_id="j1", tenant="t1", user="u1", status=JobStatus.PENDING):
    now = datetime.utcnow()
    return {
        "job_id": job_id, "tenant_id": tenant, "client_id": "c1", "user_id": user,
        "namespace": "ns", "filename": "f.pdf", "s3_key": "raw/ns/dev/j1/f.pdf",
        "storage_url": "s3://b/raw/j1", "status": status, "progress_pct": 0,
        "current_step": None, "errors": [], "created_at": now, "updated_at": now,
        "completed_at": None, "metadata": {}, "artifact_urls": {},
        "payload_storage_url": "", "studio_job_id": "", "validation_report_url": "",
        "source_relative_path": "f.pdf", "source_root": "",
    }


@pytest.mark.parametrize("kind", ["memory", "redis"])
def test_create_and_get(kind):
    s = make_store(kind)
    assert s.get("j1") is None
    s.create("j1", _job_record())
    rec = s.get("j1")
    assert rec["job_id"] == "j1"
    assert rec["status"] == "pending"                 # enum normalized to its value
    assert rec["status"] == JobStatus.PENDING         # str-enum compares equal to the string


@pytest.mark.parametrize("kind", ["memory", "redis"])
def test_get_returns_copy_not_live_ref(kind):
    s = make_store(kind)
    s.create("j1", _job_record())
    rec = s.get("j1")
    rec["status"] = "TAMPERED"                          # mutating the returned copy...
    assert s.get("j1")["status"] == "pending"          # ...must NOT affect the store


@pytest.mark.parametrize("kind", ["memory", "redis"])
def test_update_merges_fields(kind):
    s = make_store(kind)
    s.create("j1", _job_record())
    s.update("j1", status=JobStatus.PROCESSING, progress_pct=50)
    rec = s.get("j1")
    assert rec["status"] == JobStatus.PROCESSING
    assert rec["progress_pct"] == 50
    assert rec["filename"] == "f.pdf"                   # untouched field survives the merge


@pytest.mark.parametrize("kind", ["memory", "redis"])
def test_update_missing_job_is_noop(kind):
    s = make_store(kind)
    s.update("ghost", status=JobStatus.FAILED)          # must not raise, must not create
    assert s.get("ghost") is None


@pytest.mark.parametrize("kind", ["memory", "redis"])
def test_concurrent_different_fields_dont_clobber(kind):
    # Models the real race: API sets payload_storage_url while the worker sets status.
    s = make_store(kind)
    s.create("j1", _job_record())
    s.update("j1", payload_storage_url="s3://b/payload.json")   # API path
    s.update("j1", status=JobStatus.COMPLETED)                  # worker path
    rec = s.get("j1")
    assert rec["payload_storage_url"] == "s3://b/payload.json"  # neither update lost
    assert rec["status"] == JobStatus.COMPLETED


@pytest.mark.parametrize("kind", ["memory", "redis"])
def test_list_filters_by_tenant_status_and_user(kind):
    s = make_store(kind)
    s.create("j1", _job_record("j1", tenant="t1", user="u1", status=JobStatus.COMPLETED))
    s.create("j2", _job_record("j2", tenant="t1", user="u2", status=JobStatus.PENDING))
    s.create("j3", _job_record("j3", tenant="t2", user="u1", status=JobStatus.COMPLETED))

    ids = lambda recs: sorted(r["job_id"] for r in recs)
    assert ids(s.list("t1", is_admin=True)) == ["j1", "j2"]                 # tenant scoping
    assert ids(s.list("t1", status=JobStatus.COMPLETED, is_admin=True)) == ["j1"]  # status filter
    assert ids(s.list("t1", user_id="u1", is_admin=False)) == ["j1"]        # own-jobs filter
    assert ids(s.list("t2", is_admin=True)) == ["j3"]                       # other tenant isolated


@pytest.mark.parametrize("kind", ["memory", "redis"])
def test_record_round_trips_into_response_model(kind):
    # get_job() does JobStatusResponse(**job); ISO datetime strings must parse and
    # extra keys (s3_key, namespace, ...) must be ignored.
    s = make_store(kind)
    s.create("j1", _job_record())
    s.update("j1", status=JobStatus.COMPLETED, completed_at=datetime.utcnow())
    resp = JobStatusResponse(**s.get("j1"))
    assert resp.status == JobStatus.COMPLETED
    assert resp.job_id == "j1"


@pytest.mark.parametrize("kind", ["memory", "redis"])
def test_full_ingestion_sequence(kind):
    # Replays the exact _process lifecycle: create(PENDING) → update(PROCESSING)
    # → update(final + metadata) → read status like folder-scan does.
    s = make_store(kind)
    s.create("j1", _job_record(status=JobStatus.PENDING))
    s.update("j1", status=JobStatus.PROCESSING, updated_at=datetime.utcnow())
    assert s.get("j1")["status"] == JobStatus.PROCESSING
    s.update("j1", status=JobStatus.COMPLETED, progress_pct=100,
             completed_at=datetime.utcnow(), metadata={"chunk_count": 3},
             artifact_urls={"studio_payload": "s3://b/p.json"})
    rec = s.get("j1")
    assert rec["status"] == JobStatus.COMPLETED
    assert rec["metadata"]["chunk_count"] == 3
    assert rec["artifact_urls"]["studio_payload"] == "s3://b/p.json"
    # folder-scan reads status this way:
    assert s.get("j1").get("status") == JobStatus.COMPLETED
