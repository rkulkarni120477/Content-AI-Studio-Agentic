"""A document scoped to every course must be retrievable from every course.

course_id "-1" is how a document that serves the whole programme is scoped —
an FAA handbook, a style guide, an ASA textbook — because there is no single
course to give it. Two functions decided what that meant and they disagreed:

    _source_matches   (Source Library listing)  honoured the sentinel
    _passes_filters   (retrieval)               did not

_passes_filters carried course_id in `exact_fields`, so "-1" != "101" and the
document was refused; and it separately fed course_id into a substring test
against the document's own text, which refused it a second time. The result in
production: the Source Library listed AIM's entire shared reference library
against a course — 23 documents, 3,334 units, including the 1,233-unit FAA
Airframe handbook Block 9's calendar assigns as reading on nearly every day —
and retrieval could return none of it. Every course-scoped generation ran
without the reference material the UI said it had.

Both gates now call course_scope_matches. These pin the rule itself, and that
each gate actually uses it.
"""
from __future__ import annotations

import pytest

from config.settings import get_tenant_config
from services.context_retrieval import ContextRetrievalService, course_scope_matches


@pytest.fixture
def service():
    return ContextRetrievalService(get_tenant_config("aim"))


def payload(course_id, **meta):
    return {"job_id": "j1", "metadata": {"course_id": course_id, **meta},
            "source_file": {"name": "doc.pdf"}}


class TestTheRule:
    def test_the_sentinel_matches_any_course(self):
        assert course_scope_matches("-1", "101")
        assert course_scope_matches("-1", "48")

    def test_a_scoped_document_matches_only_its_own_course(self):
        assert course_scope_matches("101", "101")
        assert not course_scope_matches("101", "48")

    def test_an_unscoped_document_matches_nothing(self):
        """Deliberate. Reachable-from-nowhere beats reachable-from-everywhere:
        the other default would put every untagged upload into every course."""
        assert not course_scope_matches("", "101")
        assert not course_scope_matches(None, "101")

    def test_no_requested_course_matches_everything(self):
        assert course_scope_matches("101", None)
        assert course_scope_matches("101", "")
        for wildcard in ("all", "ALL", "*", "any"):
            assert course_scope_matches("101", wildcard)

    def test_it_compares_whole_values_not_substrings(self):
        """The old substring test accepted course 1010 for a request for 101."""
        assert not course_scope_matches("1010", "101")
        assert not course_scope_matches("101", "1010")

    def test_it_tolerates_the_int_the_database_hands_back(self):
        assert course_scope_matches(101, "101")
        assert course_scope_matches("101", 101)
        assert course_scope_matches(-1, "101")


class TestRetrievalUsesIt:
    """_passes_filters is the gate that builds retrieval's allow-set."""

    def test_the_handbook_reaches_a_course_it_is_not_scoped_to(self, service):
        assert service._passes_filters({}, payload("-1"), {"course_id": "101"})

    def test_another_courses_document_still_does_not(self, service):
        assert not service._passes_filters({}, payload("48"), {"course_id": "101"})

    def test_its_own_course_still_does(self, service):
        assert service._passes_filters({}, payload("101"), {"course_id": "101"})

    def test_an_unscoped_document_is_still_refused(self, service):
        assert not service._passes_filters({}, payload(""), {"course_id": "101"})

    def test_a_course_name_filter_is_unaffected(self, service):
        """course_name keeps its substring behaviour — it is a name, not an id."""
        p = payload("-1", course_name="Block 9 Aircraft Systems-II")
        assert service._passes_filters({}, p, {"course_name": "Aircraft Systems"})
        assert not service._passes_filters({}, p, {"course_name": "Powerplant"})

    def test_a_course_id_filter_no_longer_leaks_into_the_name_test(self, service):
        """Passing course_id must not require the id to appear in the text.

        This is what refused the handbook the second time: course_name fell back
        to course_id, then asked whether "101" appeared in the document's name,
        title, module or subject.
        """
        p = payload("-1", course_name="Airframe Handbook", title="FAA-H-8083-31B")
        assert service._passes_filters({}, p, {"course_id": "101"})


class TestTheListingUsesTheSameRule:
    """_source_matches always honoured the sentinel; it must keep doing so, and
    now via the shared function rather than a second copy of the logic."""

    def test_listing_and_retrieval_agree(self, service):
        for scope, requested in [("-1", "101"), ("101", "101"), ("48", "101"),
                                 ("", "101"), ("101", None)]:
            listing = service._source_matches({"course_id": scope},
                                              {"course_id": requested})
            gate = service._passes_filters({}, payload(scope),
                                           {"course_id": requested} if requested else {})
            assert listing == gate, (
                f"listing and retrieval disagree for scope={scope!r} "
                f"requested={requested!r} — the exact divergence this fixes")
