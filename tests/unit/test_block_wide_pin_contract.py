"""The DIS/CAS acknowledgement contract, checked against the REAL producer.

Both halves of the pinned-document feature shipped green and incompatible. DIS
emits ``extra_documents_applied`` as a dict; CAS's parser tested
``isinstance(value, (list, tuple))`` and so read every real response as "applied
nothing", reporting honoured=false on every run where the pin actually worked.

Neither suite caught it because both fabricated the payload:
``test_block_wide_reference_documents.py`` built ``{"extra_documents_applied":
ids}`` as a flat list — the shape CAS expected, never the shape DIS sends.

So these tests construct the acknowledgement by calling the producer
(``EnumerateResult.to_summary``) and feed it to the consumer
(``_extra_documents_ack``). A shape change on either side fails here.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dis_backend"))

from promptops_app.services.block_wide_service import (  # noqa: E402
    _accepts_extra_documents, _call_with_extra_documents, _extra_documents_ack,
)
from services.digests.enumerate import EnumerateResult  # noqa: E402


def _result(requested, applied, units):
    en = EnumerateResult(block="Block 9", client_id="aim", calendar_id="c",
                         total_days=20, enumerated_days=20)
    en.pinned_document_ids = list(requested)
    en.pinned_applied_ids = list(applied)
    en.pinned_unit_count = units
    return en


def test_cas_reads_what_dis_actually_sends():
    """The bug: a dict ack read as "not a list" and silently became []."""
    en = _result(["docA", "docB"], ["docA", "docB"], 40)
    bundle = {"enumerate": en.to_summary(), "digests": []}
    assert _extra_documents_ack(bundle) == ["docA", "docB"]


def test_a_partly_applied_pin_is_reported_per_id():
    # An aggregate count cannot express this: "2 requested, 20 units added" is
    # the same number whether both landed or one landed twice.
    en = _result(["docA", "docB"], ["docA"], 20)
    assert _extra_documents_ack({"enumerate": en.to_summary()}) == ["docA"]


def test_a_server_that_never_heard_of_pinning_is_distinguishable_from_one_that_applied_none():
    en = _result(["docA"], [], 0)
    assert _extra_documents_ack({"enumerate": en.to_summary()}) == []
    assert _extra_documents_ack({"enumerate": {"days": [1]}}) is None


def test_the_build_report_carries_the_same_shape_as_the_bundle():
    # Both are fed to _extra_documents_ack, so they must not drift apart.
    from services.digests.build import _finalize_report
    en = _result(["docA"], ["docA"], 12)
    report = _finalize_report("Block 9", en, [], {"calls": 0, "tok_in": 0, "tok_out": 0},
                              strategy="sequential")
    assert _extra_documents_ack(report) == ["docA"]
    assert set(report["extra_documents_applied"]) == {"requested", "applied", "units_added"}


class TestSignatureNarrowing:
    """A TypeError from inside the call must never be read as "old client"."""

    def test_an_internal_typeerror_naming_the_field_still_propagates(self):
        def buggy(block, extra_document_ids=None):
            if extra_document_ids:
                raise TypeError("cannot build cache key: extra_document_ids "
                                "contains non-hashable entries")
            return "degraded"
        with pytest.raises(TypeError):
            _call_with_extra_documents(buggy, "Block 9", extra_document_ids=["a"])

    def test_a_client_without_the_parameter_degrades_once(self):
        calls = []

        def old(block, extra=None):
            calls.append(block)
            return "ok"
        reply, sent = _call_with_extra_documents(old, "Block 9", extra_document_ids=["a"])
        assert (reply, sent) == ("ok", False)
        assert calls == ["Block 9"], "the callee must not be invoked twice"

    def test_kwargs_only_callables_are_assumed_to_accept_it(self):
        assert _accepts_extra_documents(lambda **kw: None) is True
        assert _accepts_extra_documents(lambda block: None) is False
