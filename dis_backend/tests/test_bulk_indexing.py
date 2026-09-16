"""P6.2 (Part A) — bulk OpenSearch indexing (F12), plus large-doc chunking.

`opensearch_upsert` issues `helpers.bulk(...)` in chunks of 100 (not one
`client.index()` per unit, and not one giant bulk for a 600+ page ebook).
These tests prove:
  * `_build_bulk_actions` maps each unit to a correct bulk action (same document
    shape as the old per-unit `client.index()` body) — the behaviour-preserving
    core, testable without a live OpenSearch;
  * small documents still make exactly one bulk call;
  * large documents (>100 units) are split across multiple bulks;
  * HTTP 429 retries with backoff, then succeeds;
  * non-429 errors still surface as `status="failed"`.

opensearchpy/requests_aws4auth/boto3 are faked in sys.modules so the dependency
gate passes and the bulk path is exercised without those packages installed —
matching the deps-light style of the other dis_backend tests.
"""

from __future__ import annotations

import sys
import types

from services import indexing
from services.indexing import (
    _OPENSEARCH_BULK_BATCH_SIZE,
    _build_bulk_actions,
    _is_opensearch_rate_limit,
    opensearch_upsert,
)


# ── _build_bulk_actions — pure document mapping ───────────────────────────────

def test_build_actions_one_per_unit_with_correct_shape():
    state = {
        "job_id": "J", "tenant_id": "T", "client_id": "C",
        "filename": "f.pdf", "file_type": "pdf", "doc_type": "chapter",
        "doc_metadata": {},
    }
    units = [
        {
            "content_unit_id": "u1", "unit_type": "para", "unit_number": 1,
            "title": "T1", "text": "hello", "visual_summary": "vs",
            "keywords": ["k"], "topics": ["t"], "embedding": [0.1],
            "metadata": {"course_name": "Course A", "block": "B1", "day_number": 2},
        },
        {"content_unit_id": "u2", "text": "world"},  # minimal → exercises defaults
    ]

    actions = _build_bulk_actions("idx", state, units)
    assert len(actions) == 2

    a0 = actions[0]
    assert a0["_index"] == "idx"
    assert a0["_id"] == "u1"
    src = a0["_source"]
    assert src["content_unit_id"] == "u1"
    assert src["job_id"] == "J"
    assert src["tenant_id"] == "T"
    assert src["source_file_name"] == "f.pdf"
    assert src["source_file_type"] == "pdf"
    assert src["document_type"] == "chapter"
    assert src["course_name"] == "Course A"
    assert src["block"] == "B1"
    assert src["day_number"] == 2
    assert src["keywords"] == ["k"]
    assert src["topics"] == ["t"]
    assert src["embedding"] == [0.1]

    a1 = actions[1]
    assert a1["_id"] == "u2"
    s1 = a1["_source"]
    assert s1["keywords"] == [] and s1["topics"] == [] and s1["embedding"] == []
    assert s1["metadata"] == {}
    assert s1["course_name"] is None  # no metadata, empty doc_metadata


def test_build_actions_empty_units():
    assert _build_bulk_actions("idx", {}, []) == []


def test_is_opensearch_rate_limit_detects_429():
    assert _is_opensearch_rate_limit(RuntimeError("TransportError(429, 'Too Many Requests')"))

    class _TransportLike(Exception):
        def __init__(self):
            super().__init__(429, "Too Many Requests")
            self.status_code = 429

    assert _is_opensearch_rate_limit(_TransportLike())
    assert not _is_opensearch_rate_limit(RuntimeError("bulk boom"))


# ── opensearch_upsert — chunked bulk requests ─────────────────────────────────

def _fake_cfg():
    return types.SimpleNamespace(
        vector_store=types.SimpleNamespace(
            enabled=True, provider="opensearch", index_name="idx", auth_mode="basic",
        ),
        embedding=types.SimpleNamespace(dimension=1536),
    )


def _install_fake_deps(monkeypatch, bulk_impl):
    os_mod = types.ModuleType("opensearchpy")
    helpers_mod = types.ModuleType("opensearchpy.helpers")
    helpers_mod.bulk = bulk_impl
    os_mod.helpers = helpers_mod
    monkeypatch.setitem(sys.modules, "opensearchpy", os_mod)
    monkeypatch.setitem(sys.modules, "opensearchpy.helpers", helpers_mod)
    monkeypatch.setitem(sys.modules, "requests_aws4auth", types.ModuleType("requests_aws4auth"))
    monkeypatch.setitem(sys.modules, "boto3", types.ModuleType("boto3"))


class _FakeClient:
    def __init__(self):
        self.index_calls = 0

    def index(self, **kw):  # must NOT be used anymore
        self.index_calls += 1


def _stub_client_and_index(monkeypatch, client):
    monkeypatch.setattr(indexing, "_vector_store_write_client", lambda cfg: client)
    monkeypatch.setattr(indexing, "ensure_index", lambda *a, **k: None)


def test_upsert_issues_single_bulk_call_for_small_doc(monkeypatch):
    seen = {"count": 0}

    def fake_bulk(client, actions, **kw):
        acts = list(actions)
        seen["count"] += 1
        seen["actions"] = acts
        seen["kw"] = kw
        return (len(acts), [])

    _install_fake_deps(monkeypatch, fake_bulk)
    fc = _FakeClient()
    _stub_client_and_index(monkeypatch, fc)

    state = {"embedding_ready_chunks": [
        {"content_unit_id": "u1", "text": "a"},
        {"content_unit_id": "u2", "text": "b"},
    ]}
    res = opensearch_upsert(_fake_cfg(), state)

    assert res["status"] == "completed"
    assert res["documents_indexed"] == 2
    assert res["index_name"] == "idx" and res["provider"] == "opensearch"
    assert seen["count"] == 1               # ONE bulk request for a small doc
    assert len(seen["actions"]) == 2
    assert seen["actions"][0]["_id"] == "u1"
    assert seen["kw"].get("refresh") is False
    assert fc.index_calls == 0              # the old per-doc client.index() path is gone


def test_upsert_chunks_large_doc_into_multiple_bulks(monkeypatch):
    """250 units → 3 bulks of 100 / 100 / 50 (default batch size)."""
    seen = {"count": 0, "sizes": []}

    def fake_bulk(client, actions, **kw):
        acts = list(actions)
        seen["count"] += 1
        seen["sizes"].append(len(acts))
        return (len(acts), [])

    _install_fake_deps(monkeypatch, fake_bulk)
    _stub_client_and_index(monkeypatch, _FakeClient())

    n = 250
    state = {
        "embedding_ready_chunks": [
            {"content_unit_id": f"u{i}", "text": f"t{i}", "embedding": [0.1]}
            for i in range(n)
        ],
    }
    res = opensearch_upsert(_fake_cfg(), state)

    assert res["status"] == "completed"
    assert res["documents_indexed"] == n
    assert seen["count"] == 3
    assert seen["sizes"] == [
        _OPENSEARCH_BULK_BATCH_SIZE,
        _OPENSEARCH_BULK_BATCH_SIZE,
        n - 2 * _OPENSEARCH_BULK_BATCH_SIZE,
    ]


def test_upsert_retries_429_then_succeeds(monkeypatch):
    attempts = {"n": 0}
    sleeps = []

    def fake_bulk(client, actions, **kw):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("TransportError(429, 'Too Many Requests')")
        return (len(list(actions)), [])

    _install_fake_deps(monkeypatch, fake_bulk)
    _stub_client_and_index(monkeypatch, _FakeClient())
    monkeypatch.setattr(indexing.time, "sleep", lambda s: sleeps.append(s))

    res = opensearch_upsert(
        _fake_cfg(),
        {"embedding_ready_chunks": [{"content_unit_id": "u1", "text": "a"}]},
    )
    assert res["status"] == "completed"
    assert res["documents_indexed"] == 1
    assert attempts["n"] == 2
    assert sleeps == [1]  # 2**0 after first 429


def test_upsert_429_exhausted_returns_failed(monkeypatch):
    monkeypatch.setattr(indexing.time, "sleep", lambda s: None)

    def always_429(client, actions, **kw):
        raise RuntimeError("TransportError(429, 'Too Many Requests')")

    _install_fake_deps(monkeypatch, always_429)
    _stub_client_and_index(monkeypatch, _FakeClient())

    res = opensearch_upsert(
        _fake_cfg(),
        {"embedding_ready_chunks": [{"content_unit_id": "u1"}]},
    )
    assert res["status"] == "failed"
    assert "429" in res["error"] or "Too Many Requests" in res["error"]


def test_upsert_empty_units_no_bulk_call(monkeypatch):
    called = {"n": 0}

    def fake_bulk(client, actions, **kw):
        called["n"] += 1
        return (0, [])

    _install_fake_deps(monkeypatch, fake_bulk)
    _stub_client_and_index(monkeypatch, _FakeClient())

    res = opensearch_upsert(_fake_cfg(), {"embedding_ready_chunks": [], "content_units": []})
    assert res["status"] == "completed"
    assert res["documents_indexed"] == 0
    assert called["n"] == 0                 # nothing to send → no bulk request


def test_upsert_bulk_error_returns_failed(monkeypatch):
    def fake_bulk(client, actions, **kw):
        raise RuntimeError("bulk boom")

    _install_fake_deps(monkeypatch, fake_bulk)
    _stub_client_and_index(monkeypatch, _FakeClient())

    res = opensearch_upsert(_fake_cfg(), {"embedding_ready_chunks": [{"content_unit_id": "u1"}]})
    assert res["status"] == "failed"
    assert "bulk boom" in res["error"]
