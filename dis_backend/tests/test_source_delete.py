"""Source Library delete must clear S3 artifacts and source_list.json.

Postgres is the catalogue source of truth. A UI delete that only dropped the
PG row left raw files, processed artifacts, and S3 source_list.json behind —
especially when the row had an empty raw_key and only a raw_storage_url.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from services import source_index_pg
from services import source_library


JOB = "09c29936-3106-4321-b7b6-0e3ca069d6ba"


def _tenant():
    return SimpleNamespace(
        tenant_id="aim",
        namespace="aim_ns",
        storage=SimpleNamespace(base_prefix="DIS"),
        structure_store=SimpleNamespace(
            enabled=True,
            provider="postgres",
            url="postgresql://u:p@localhost/dis_db",
            schema_name="dis",
        ),
        get_namespace=lambda client_id="": "aim_ns/aim",
    )


class _FakeWriter:
    def __init__(self, listed=None, prefixes=None, index=None):
        self.listed = listed or {}
        self.prefixes = prefixes or {}
        self.index = index if index is not None else {"sources": []}
        self.written = []
        self.list_calls = []
        self.prefix_calls = []
        self.base_prefix = "DIS"
        self.processed_bucket = "content-ai-studio"
        self.raw_bucket = "content-ai-studio"

    def list_keys(self, prefix, suffix=""):
        self.list_calls.append(prefix)
        return list(self.listed.get(prefix, []))

    def list_common_prefixes(self, prefix):
        self.prefix_calls.append(prefix)
        return list(self.prefixes.get(prefix, []))

    def read_json(self, key):
        return self.index

    def write_json(self, key, payload):
        self.written.append((key, payload))
        self.index = payload
        return f"s3://bucket/{key}"


class _FakeProvider:
    def __init__(self):
        self.deleted = []

    async def delete(self, key):
        self.deleted.append(key)


def test_logical_storage_key_strips_s3_url_and_base_prefix():
    url = (
        "s3://content-ai-studio/DIS/raw/aim_ns/aim/development/"
        f"{JOB}/Projects/file.pdf"
    )
    assert source_library.logical_storage_key(url, "DIS") == (
        f"raw/aim_ns/aim/development/{JOB}/Projects/file.pdf"
    )
    assert source_library.logical_storage_key(
        f"processed/aim_ns/aim/development/{JOB}/studio_payload/payload.json",
        "DIS",
    ) == f"processed/aim_ns/aim/development/{JOB}/studio_payload/payload.json"


def test_job_prefix_from_key():
    key = f"raw/aim_ns/aim/development/{JOB}/nested/file.pdf"
    assert source_library._job_prefix_from_key(key, JOB) == (
        f"raw/aim_ns/aim/development/{JOB}/"
    )


def test_collect_source_s3_keys_lists_raw_prefix_when_raw_key_empty(monkeypatch):
    cfg = _tenant()
    processed = f"processed/aim_ns/aim/development/{JOB}/"
    raw = f"raw/aim_ns/aim/development/{JOB}/"
    writer = _FakeWriter(listed={
        processed: [f"{processed}source_content/content.json"],
        raw: [f"{raw}Projects/file.pdf"],
    })
    monkeypatch.setattr(source_library, "ArtifactWriter", lambda tenant: writer)
    monkeypatch.setattr(source_library, "get_settings", lambda: SimpleNamespace(environment="development"))

    keys = source_library.collect_source_s3_keys(cfg, "aim", JOB, {
        "job_id": JOB,
        "raw_key": "",
        "raw_storage_url": (
            f"s3://content-ai-studio/DIS/raw/aim_ns/aim/development/{JOB}/Projects/file.pdf"
        ),
        "payload_key": f"{processed}studio_payload/payload.json",
        "content_key": f"{processed}source_content/content.json",
    })
    assert f"{processed}source_content/content.json" in keys
    assert f"{raw}Projects/file.pdf" in keys
    assert processed in writer.list_calls
    assert raw in writer.list_calls


def test_delete_source_document_prunes_s3_index_when_pg_enabled(monkeypatch):
    cfg = _tenant()
    processed = f"processed/aim_ns/aim/development/{JOB}/"
    raw = f"raw/aim_ns/aim/development/{JOB}/"
    s3_index = {"sources": [{"job_id": JOB, "title": "T"}, {"job_id": "keep-me"}]}
    writer = _FakeWriter(
        listed={
            processed: [f"{processed}source_content/content.json"],
            raw: [f"{raw}file.pdf"],
        },
        index=s3_index,
    )
    provider = _FakeProvider()
    deleted_pg = []

    monkeypatch.setattr(source_library, "ArtifactWriter", lambda tenant: writer)
    monkeypatch.setattr(source_library, "get_settings", lambda: SimpleNamespace(environment="development"))
    monkeypatch.setattr(source_index_pg, "use_pg_source_index", lambda tenant: True)
    monkeypatch.setattr(
        source_library, "read_source_index",
        lambda tenant, client: {"sources": [{
            "job_id": JOB,
            "raw_key": "",
            "raw_storage_url": f"s3://content-ai-studio/DIS/{raw}file.pdf",
        }]},
    )
    monkeypatch.setattr(
        source_index_pg, "delete_record",
        lambda tenant, client, job: deleted_pg.append(job) or "pg:ref",
    )
    monkeypatch.setattr("storage.provider.get_storage_provider", lambda tenant: provider)
    monkeypatch.setattr(
        "services.indexing.opensearch_delete_by_job",
        lambda tenant, job: {"status": "completed", "deleted": 1},
    )

    result = asyncio.run(source_library.delete_source_document(cfg, "aim", JOB))

    assert result["deleted"] is True
    assert result["s3_index_removed"] is True
    assert result["s3_objects_deleted"] == 2
    assert f"{processed}source_content/content.json" in provider.deleted
    assert f"{raw}file.pdf" in provider.deleted
    assert deleted_pg == [JOB]
    assert writer.written, "S3 source_list.json must be rewritten"
    remaining = [r["job_id"] for r in writer.written[-1][1]["sources"]]
    assert remaining == ["keep-me"]


def test_plan_orphaned_source_cleanup(monkeypatch):
    cfg = _tenant()
    env_root_p = "processed/aim_ns/aim/development/"
    env_root_r = "raw/aim_ns/aim/development/"
    writer = _FakeWriter(
        prefixes={
            env_root_p: [f"{env_root_p}{JOB}/", f"{env_root_p}keep-me/", f"{env_root_p}source_index/"],
            env_root_r: [f"{env_root_r}{JOB}/", f"{env_root_r}keep-me/"],
        },
        index={"sources": [{"job_id": JOB}, {"job_id": "keep-me"}, {"job_id": "list-only"}]},
    )
    monkeypatch.setattr(source_library, "ArtifactWriter", lambda tenant: writer)
    monkeypatch.setattr(source_library, "get_settings", lambda: SimpleNamespace(environment="development"))
    monkeypatch.setattr(source_index_pg, "use_pg_source_index", lambda tenant: True)
    monkeypatch.setattr(source_index_pg, "read_job_ids", lambda tenant, client: ["keep-me"])
    monkeypatch.setattr(source_index_pg, "environment_name", lambda: "development")

    plan = source_library.plan_orphaned_source_cleanup(cfg, "aim")
    assert plan["pg_count"] == 1
    assert set(plan["orphan_job_ids"]) == {JOB, "list-only"}
    assert JOB in plan["processed_orphan_job_ids"]
    assert JOB in plan["raw_orphan_job_ids"]
    assert "list-only" in plan["s3_list_orphan_job_ids"]
    assert "keep-me" not in plan["orphan_job_ids"]
    assert "source_index" not in plan["orphan_job_ids"]


def test_apply_orphaned_cleanup_refuses_empty_pg(monkeypatch):
    cfg = _tenant()
    plan = {
        "client_id": "aim",
        "environment": "development",
        "pg_count": 0,
        "orphan_job_ids": [JOB],
        "orphan_records": {},
    }
    with pytest.raises(RuntimeError, match="REFUSING"):
        asyncio.run(source_library.apply_orphaned_source_cleanup(cfg, "aim", plan))


def test_apply_orphaned_cleanup_deletes_s3_and_prunes_list(monkeypatch):
    cfg = _tenant()
    processed = f"processed/aim_ns/aim/development/{JOB}/"
    raw = f"raw/aim_ns/aim/development/{JOB}/"
    writer = _FakeWriter(
        listed={
            processed: [f"{processed}payload.json"],
            raw: [f"{raw}file.pdf"],
        },
        index={"sources": [{"job_id": JOB}, {"job_id": "keep-me"}]},
    )
    provider = _FakeProvider()
    monkeypatch.setattr(source_library, "ArtifactWriter", lambda tenant: writer)
    monkeypatch.setattr(source_library, "get_settings", lambda: SimpleNamespace(environment="development"))
    monkeypatch.setattr(source_index_pg, "use_pg_source_index", lambda tenant: True)
    monkeypatch.setattr(source_index_pg, "read_job_ids", lambda tenant, client: ["keep-me"])
    monkeypatch.setattr("storage.provider.get_storage_provider", lambda tenant: provider)
    monkeypatch.setattr(
        "services.indexing.opensearch_delete_by_job",
        lambda tenant, job: {"status": "skipped"},
    )

    result = asyncio.run(source_library.apply_orphaned_source_cleanup(
        cfg, "aim",
        {
            "client_id": "aim",
            "environment": "development",
            "pg_count": 1,
            "orphan_job_ids": [JOB],
            "orphan_records": {JOB: {"job_id": JOB, "raw_key": ""}},
        },
        delete_opensearch=False,
    ))
    assert result["s3_list_rows_removed"] == 1
    assert result["jobs"][0]["s3_objects_deleted"] == 2
    remaining = [r["job_id"] for r in writer.index["sources"]]
    assert remaining == ["keep-me"]
    assert f"{processed}payload.json" in provider.deleted
    assert f"{raw}file.pdf" in provider.deleted
