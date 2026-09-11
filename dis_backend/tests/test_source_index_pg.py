"""Unit tests for Postgres Source Library catalogue adapter."""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from config.settings import get_tenant_config
from services import source_index_pg
from services import source_library


def _tenant(enabled=True, url="postgresql://u:p@localhost/dis_db", schema="dis"):
    return SimpleNamespace(
        tenant_id="aim",
        structure_store=SimpleNamespace(
            enabled=enabled,
            provider="postgres",
            url=url,
            schema_name=schema,
            auto_create_schema=True,
        ),
    )


def test_use_pg_requires_enabled_url():
    assert source_index_pg.use_pg_source_index(_tenant()) is True
    assert source_index_pg.use_pg_source_index(_tenant(enabled=False)) is False
    assert source_index_pg.use_pg_source_index(_tenant(url="")) is False


def test_record_to_params_maps_identity_and_json():
    rec = {
        "job_id": "job-1",
        "title": "Landing Gear",
        "source_file_name": "proj.pdf",
        "document_type": "project",
        "purpose": "course_generation",
        "status": "processed",
        "visibility": "instructor",
        "restricted": False,
        "block": "Block 2",
        "day": "Day 13",
        "course_name": "Block 2",
        "module_name": "LG",
        "payload_key": "processed/a/job-1/studio_payload/payload.json",
        "content_key": "processed/a/job-1/source_content/content.json",
        "raw_key": "raw/a/job-1/proj.pdf",
        "extracted_chars": 1200,
        "total_units": 3,
        "created_at": "2026-08-01T10:00:00",
        "updated_at": "2026-08-02T11:00:00",
        "topic": "landing gear",
        "project_number": "2-13",
    }
    params = source_index_pg.record_to_params(
        rec, tenant_id="aim", client_id="aim", environment="development",
    )
    assert params["job_id"] == "job-1"
    assert params["client_id"] == "aim"
    assert params["block"] == "Block 2"
    assert params["extracted_chars"] == 1200
    stored = json.loads(params["record_json"])
    assert stored["topic"] == "landing gear"
    assert stored["project_number"] == "2-13"
    assert isinstance(params["updated_at"], datetime)


def test_record_to_params_requires_job_id():
    with pytest.raises(ValueError, match="job_id"):
        source_index_pg.record_to_params(
            {"title": "x"}, tenant_id="aim", client_id="aim", environment="development",
        )


def test_record_to_params_accepts_document_id_fallback():
    params = source_index_pg.record_to_params(
        {"document_id": "doc-9", "title": "T"},
        tenant_id="aim",
        client_id="aim",
        environment="development",
    )
    assert params["job_id"] == "doc-9"


class _FakeCursor:
    def __init__(self, store):
        self.store = store
        self._last = None
        self._fetch = None

    def execute(self, sql, params=None):
        self._last = (sql, params)
        sql_l = " ".join(str(sql).lower().split())
        if "create schema" in sql_l or "create table" in sql_l or "create index" in sql_l:
            self._fetch = None
            return
        if "select record_json" in sql_l and "for update" in sql_l:
            job_id = params[2]
            key = (params[0], params[1], job_id)
            row = self.store.get(key)
            self._fetch = [(row,)] if row is not None else []
            return
        if "select record_json" in sql_l:
            client_id, env = params
            rows = [
                (v,)
                for (c, e, _), v in sorted(self.store.items())
                if c == client_id and e == env
            ]
            self._fetch = rows
            return
        if "select count" in sql_l:
            client_id, env = params
            n = sum(1 for (c, e, _) in self.store if c == client_id and e == env)
            self._fetch = [(n,)]
            return
        if "insert into" in sql_l:
            p = params
            key = (p["client_id"], p["environment"], p["job_id"])
            self.store[key] = json.loads(p["record_json"])
            self._fetch = None
            return
        if "delete from" in sql_l:
            if params is None:
                return
            if len(params) == 3 and isinstance(params[2], list):
                client_id, env, keep = params
                for key in list(self.store):
                    if key[0] == client_id and key[1] == env and key[2] not in keep:
                        del self.store[key]
            elif len(params) == 3:
                client_id, env, job_id = params
                self.store.pop((client_id, env, job_id), None)
            elif len(params) == 2:
                client_id, env = params
                for key in list(self.store):
                    if key[0] == client_id and key[1] == env:
                        del self.store[key]
            self._fetch = None
            return

    def fetchall(self):
        return list(self._fetch or [])

    def fetchone(self):
        rows = self._fetch or []
        return rows[0] if rows else None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeConn:
    def __init__(self, store):
        self.store = store
        self.committed = False

    def cursor(self):
        return _FakeCursor(self.store)

    def commit(self):
        self.committed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.fixture
def pg_store(monkeypatch):
    store = {}
    monkeypatch.setattr(source_index_pg, "connect", lambda cfg: _FakeConn(store))
    monkeypatch.setattr(source_index_pg, "environment_name", lambda: "development")
    return store


def test_upsert_and_read_roundtrip(pg_store):
    cfg = _tenant()
    source_index_pg.upsert_record(cfg, "aim", {
        "job_id": "j1",
        "title": "A",
        "purpose": "cdd",
        "updated_at": "2026-09-01T00:00:00",
    })
    source_index_pg.upsert_record(cfg, "aim", {
        "job_id": "j1",
        "title": "A renamed",
        "purpose": "cdd",
        "updated_at": "2026-09-02T00:00:00",
    })
    rows = source_index_pg.read_sources(cfg, "aim")
    assert len(rows) == 1
    assert rows[0]["title"] == "A renamed"
    assert source_index_pg.count_sources(cfg, "aim") == 1


def test_replace_index_deletes_stale_jobs(pg_store):
    cfg = _tenant()
    source_index_pg.upsert_record(cfg, "aim", {"job_id": "keep", "title": "K"})
    source_index_pg.upsert_record(cfg, "aim", {"job_id": "drop", "title": "D"})
    ref = source_index_pg.replace_index(cfg, "aim", [{"job_id": "keep", "title": "K2"}])
    assert ref.startswith("pg:dis.source_index:aim:")
    rows = source_index_pg.read_sources(cfg, "aim")
    assert [r["job_id"] for r in rows] == ["keep"]
    assert rows[0]["title"] == "K2"


def test_delete_record(pg_store):
    cfg = _tenant()
    source_index_pg.upsert_record(cfg, "aim", {"job_id": "j1", "title": "T"})
    source_index_pg.delete_record(cfg, "aim", "j1")
    assert source_index_pg.read_sources(cfg, "aim") == []


def test_update_status_respects_only_if(pg_store):
    cfg = _tenant()
    source_index_pg.upsert_record(cfg, "aim", {
        "job_id": "j1", "status": "processed", "title": "T",
    })
    ok, ref = source_index_pg.update_status(
        cfg, "aim", "j1", "failed", only_if_status="processing",
    )
    assert ok is False
    assert ref == ""
    ok, ref = source_index_pg.update_status(
        cfg, "aim", "j1", "failed", only_if_status="processed",
    )
    assert ok is True
    assert source_index_pg.read_sources(cfg, "aim")[0]["status"] == "failed"


def test_source_library_upsert_uses_pg_when_enabled(monkeypatch, pg_store):
    cfg = _tenant()
    monkeypatch.setattr(source_index_pg, "use_pg_source_index", lambda c: True)
    ref = source_library.upsert_source_record(cfg, "aim", {
        "job_id": "j9", "title": "From library", "purpose": "style",
    })
    assert ref.startswith("pg:")
    index = source_library.read_source_index(cfg, "aim")
    assert index["sources"][0]["job_id"] == "j9"


def test_source_library_falls_back_to_s3_when_disabled(monkeypatch):
    cfg = get_tenant_config("academian")  # structure_store.enabled=false in YAML
    written = {}

    class W:
        def write_json(self, key, payload):
            written["key"] = key
            written["payload"] = payload
            return f"s3://bucket/{key}"

        def read_json(self, key):
            return written.get("payload") or {"sources": []}

    monkeypatch.setattr(source_library, "ArtifactWriter", lambda cfg: W())
    ref = source_library.upsert_source_record(cfg, "academian", {"job_id": "s3-1", "title": "S3"})
    assert ref.startswith("s3://")
    assert written["payload"]["sources"][0]["job_id"] == "s3-1"
