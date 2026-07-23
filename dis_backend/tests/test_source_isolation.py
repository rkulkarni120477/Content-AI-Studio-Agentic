"""Course-level isolation for Source Library document listing.

Regression tests for the leak where every document uploaded under a tenant
was visible from every course, because course_id was never stored or matched
as a filter. Untagged documents (uploaded before this field existed) must
stay visible everywhere rather than disappear once filtering is enforced.
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


def test_course_filter_keeps_legacy_untagged_docs_visible():
    svc = _svc()
    legacy = {"course_id": "", "document_type": "pdf"}

    assert svc._source_matches(legacy, {"course_id": "101"}) is True
    assert svc._source_matches(legacy, {"course_id": "202"}) is True


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
