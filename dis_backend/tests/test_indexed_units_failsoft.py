"""The Source Library must still render when the vector store cannot be queried.

_attach_indexed_units annotates each listed document with how many units are
actually searchable, so the UI can say "Searchable · N" instead of the flat
"processed" that hid a corpus-wide indexing loss. It is deliberately fail-soft:
on any error every count stays None and the UI shows "unknown", because claiming
a document is searchable when we could not check is the exact failure it exists
to end.

The handler that implements that was itself broken. It called `log.warning`, and
this module defines `logger` — so the one path meant to degrade gracefully raised
NameError, and the whole listing returned 500. It reached a deployment: dev was
pointed at an OpenSearch index that does not exist yet, every request hit the
handler, and the Source Library answered `{"detail":"Internal error","type":
"NameError"}`.

Nothing caught it because no test ever made the query fail — in any environment
with a working index the branch is dead code. These make it fail on purpose:
an index that does not exist, a refused connection, a malformed response.
"""
from __future__ import annotations

import logging

import pytest

from config.settings import get_tenant_config
from services import context_retrieval as cr
from services.context_retrieval import ContextRetrievalService


@pytest.fixture
def service():
    return ContextRetrievalService(get_tenant_config("aim"))


def page():
    return [{"job_id": "j1", "source_file_name": "a.pdf"},
            {"job_id": "j2", "source_file_name": "b.pdf"}]


def _fail_with(monkeypatch, exc):
    """Make the vector-store client raise the way a real failure would."""
    class _Client:
        def search(self, **_kw):
            raise exc
    monkeypatch.setattr("services.indexing._vector_store_read_client",
                        lambda cfg: _Client())


@pytest.mark.parametrize("exc", [
    Exception("index_not_found_exception: no such index [dis-content-devenv-aim]"),
    ConnectionError("Connection refused"),
    TimeoutError("read timed out"),
])
def test_a_failing_vector_store_leaves_counts_unknown(monkeypatch, service, exc):
    """The exact production shape: the index does not exist yet."""
    docs = page()
    _fail_with(monkeypatch, exc)
    service._attach_indexed_units(docs)          # must not raise
    assert [d["indexed_units"] for d in docs] == [None, None], \
        "unknown must stay None — never 0, which reads as 'checked, found nothing'"


def test_the_failure_is_logged_with_the_index_it_could_not_reach(monkeypatch, service, caplog):
    _fail_with(monkeypatch, Exception("index_not_found_exception"))
    with caplog.at_level(logging.WARNING, logger=cr.__name__):
        service._attach_indexed_units(page())
    assert caplog.records, "a degraded listing must leave a trace"
    message = caplog.records[0].getMessage()
    assert service.tenant_cfg.vector_store.index_name in message, \
        "the log must name the index, which is what identifies a mispointed deployment"
    assert "index_not_found_exception" in message


def test_a_malformed_response_is_not_mistaken_for_zero(monkeypatch, service):
    """A response missing the aggregation must degrade, not report every doc as 0."""
    class _Client:
        def search(self, **_kw):
            return {"hits": {"total": 0}}        # no "aggregations" key
    monkeypatch.setattr("services.indexing._vector_store_read_client", lambda cfg: _Client())
    docs = page()
    service._attach_indexed_units(docs)
    assert [d["indexed_units"] for d in docs] == [None, None]


def test_counts_are_attached_when_the_query_works(monkeypatch, service):
    """Negative control. Without this, an implementation that always failed
    would satisfy every assertion above."""
    class _Client:
        def search(self, **_kw):
            return {"aggregations": {"per_job": {"buckets": [
                {"key": "j1", "doc_count": 1233}]}}}
    monkeypatch.setattr("services.indexing._vector_store_read_client", lambda cfg: _Client())
    docs = page()
    service._attach_indexed_units(docs)
    # j1 was returned; j2 was asked for and genuinely has nothing, which is 0 —
    # a real answer, and distinct from the None above.
    assert [d["indexed_units"] for d in docs] == [1233, 0]


def test_an_empty_page_asks_the_vector_store_nothing(monkeypatch, service):
    def _boom(cfg):
        raise AssertionError("must not build a client for an empty page")
    monkeypatch.setattr("services.indexing._vector_store_read_client", _boom)
    service._attach_indexed_units([])
