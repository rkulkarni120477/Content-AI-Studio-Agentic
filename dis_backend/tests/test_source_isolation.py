"""Course-level isolation for Source Library document listing.

A course-scoped request matches only documents tagged with that exact
course_id, plus documents tagged "-1" — the global sentinel for content
intentionally shared across every course (style guides, reference books).
Untagged documents (course_id="") are NOT visible under a course filter;
they only appear once backfilled with a real course_id or the global
sentinel.
"""

from services.context_retrieval import ContextRetrievalService


def _svc():
    # _source_matches is a pure function of (src, filters) — no tenant config
    # or I/O needed, so bypass __init__ rather than construct a real TenantConfig.
    return ContextRetrievalService.__new__(ContextRetrievalService)


def test_no_course_filter_returns_everything():
    svc = _svc()
    own = {"course_id": "101", "document_type": "pdf"}
    sibling = {"course_id": "202", "document_type": "pdf"}
    legacy = {"course_id": "", "document_type": "pdf"}

    assert svc._source_matches(own, {}) is True
    assert svc._source_matches(sibling, {}) is True
    assert svc._source_matches(legacy, {}) is True


def test_course_filter_excludes_sibling_course():
    svc = _svc()
    own = {"course_id": "101", "document_type": "pdf"}
    sibling = {"course_id": "202", "document_type": "pdf"}

    assert svc._source_matches(own, {"course_id": "101"}) is True
    assert svc._source_matches(sibling, {"course_id": "101"}) is False


def test_course_filter_excludes_untagged_legacy_docs():
    svc = _svc()
    legacy = {"course_id": "", "document_type": "pdf"}

    assert svc._source_matches(legacy, {"course_id": "101"}) is False
    assert svc._source_matches(legacy, {"course_id": "202"}) is False


def test_course_filter_keeps_global_sentinel_docs_visible():
    svc = _svc()
    global_doc = {"course_id": "-1", "document_type": "pdf"}

    assert svc._source_matches(global_doc, {"course_id": "101"}) is True
    assert svc._source_matches(global_doc, {"course_id": "202"}) is True


def test_course_filter_wildcard_values_disable_filtering():
    svc = _svc()
    sibling = {"course_id": "202", "document_type": "pdf"}

    for wildcard in ("all", "*", "any", "ALL"):
        assert svc._source_matches(sibling, {"course_id": wildcard}) is True


def test_course_filter_whitespace_only_is_treated_as_blank():
    """A stray whitespace-only filter value must not exclude every tagged doc."""
    svc = _svc()
    own = {"course_id": "101", "document_type": "pdf"}
    sibling = {"course_id": "202", "document_type": "pdf"}

    assert svc._source_matches(own, {"course_id": "   "}) is True
    assert svc._source_matches(sibling, {"course_id": "   "}) is True
