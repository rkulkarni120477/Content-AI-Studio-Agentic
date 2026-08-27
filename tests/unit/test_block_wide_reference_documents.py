"""A document the user pinned must reach a block-wide build — and only ever ADD to it.

The block-wide form has always rendered a fully enabled "Reference Documents"
picker, and until 2026-08-27 the selection died three times over: the field was
absent from ``BlockWideGenerateRequest``, absent from the frontend's block payload
whitelist, and absent from the worker's request rebuild. The panel's own hint told
the user it "does not apply", which is the only reason nobody filed it as a bug.

The contract these tests pin is the half that makes the feature safe to have at all:

  * ADDITIVE. The digest pipeline still enumerates every ingested unit in the block;
    pinned ids are digested on top of that set. A block-wide deliverable narrowed to
    a document subset would drop days the user can see listed in the panel and still
    read as complete — the 2026-08-12 failure mode, arrived at from a new direction.
  * NEVER FATAL. A DIS (or a CAS process) that cannot carry the ids must degrade to
    exactly today's behavior, because a pinned extra document failing an otherwise
    good generation is a worse outcome than the pin being skipped.
  * NEVER SILENT. ``DigestBuildRequest`` is a plain BaseModel, so a DIS that predates
    the field accepts the request, ignores the pins, and answers indistinguishably
    from one that applied them. The pins are therefore verified against what DIS
    reports back, and a miss is recorded on coverage.
"""
from __future__ import annotations

import pytest

from app.schemas.block_wide import BlockWideGenerateRequest
from promptops_app.jobs import block_wide_jobs as jobs
from promptops_app.services import block_wide_service as svc


# ── the request schema ──────────────────────────────────────────────────────
def test_a_request_without_a_selection_defaults_to_no_pinned_documents():
    req = BlockWideGenerateRequest(block="Block 9", course_id=1, project_id=2)
    assert req.reference_document_ids == []


def test_a_selection_survives_validation_as_strings():
    req = BlockWideGenerateRequest(block="Block 9", course_id=1, project_id=2,
                                   reference_document_ids=["job-a", "job-b"])
    assert req.reference_document_ids == ["job-a", "job-b"]


def test_the_selection_is_documented_as_additive_not_as_a_filter():
    """The docstring is the contract a future caller reads before wiring a filter
    onto this field, which is the one change that would silently shrink a block."""
    doc = BlockWideGenerateRequest.__doc__ or ""
    assert "ADDITIVE" in doc
    assert "never a replacement" in doc.lower() or "never instead of" in doc.lower()


# ── what the service sends ──────────────────────────────────────────────────
class _Req:
    block = "Block 9"
    project_id = None
    course_id = None
    prompt_id = None
    quality_tier = None

    def __init__(self, reference_document_ids=None):
        self.reference_document_ids = reference_document_ids or []


class _User:
    username = "tester"


class _Result:
    """The shape REDUCE returns, reduced to what _build_and_reduce touches."""

    def __init__(self):
        self.coverage = {}


def _run(monkeypatch, *, request_body, bundle=None, report=None, accepts_extra=True):
    """Run _build_and_reduce against a stub DIS; returns (result, calls)."""
    calls = {}

    class _Stub:
        def build_digests_sync(self, block, **kw):
            if not accepts_extra and "extra_document_ids" in kw:
                raise TypeError(
                    "build_digests_sync() got an unexpected keyword argument "
                    "'extra_document_ids'")
            calls.setdefault("build", []).append(kw)
            return report if report is not None else {}

        def get_digests_bundle_sync(self, block, **kw):
            if not accepts_extra and "extra_document_ids" in kw:
                raise TypeError(
                    "get_digests_bundle_sync() got an unexpected keyword argument "
                    "'extra_document_ids'")
            calls.setdefault("bundle", []).append(kw)
            return bundle if bundle is not None else {"enumerate": {"days": [1]}, "digests": []}

    class _Gen:
        def __init__(self, **kw):
            pass

        def reduce(self, *a, **k):
            return _Result()

    import promptops_app.services.block_wide_generator as bwg
    monkeypatch.setattr(bwg, "BlockWideGenerator", _Gen)
    monkeypatch.setattr(svc, "dis_client", _Stub())
    monkeypatch.setattr(svc, "_map_usage_ctx", lambda *a, **k: None)
    monkeypatch.setattr(svc, "_reserve_map_budget", lambda *a, **k: [])
    monkeypatch.setattr(svc, "_settle_map_usage", lambda *a, **k: None)
    result, _report, _directives = svc._build_and_reduce(
        "cdd", "Block 9", None, _User(), "aim", db=None, request_body=request_body)
    return result, calls


def test_the_pinned_ids_reach_both_dis_calls(monkeypatch):
    """The build digests them and the bundle is what REDUCE reads. One without the
    other is a half-applied selection, which looks applied and is not."""
    _result, calls = _run(monkeypatch, request_body=_Req(["job-a", "job-b"]))
    assert calls["build"][0]["extra_document_ids"] == ["job-a", "job-b"]
    assert calls["bundle"][0]["extra_document_ids"] == ["job-a", "job-b"]


def test_the_block_scope_is_never_narrowed_to_the_selection(monkeypatch):
    """Additive means additive: the call still asks for the whole block, and nothing
    resembling a document filter is sent alongside the pins."""
    _result, calls = _run(monkeypatch, request_body=_Req(["job-a"]))
    for kw in calls["build"] + calls["bundle"]:
        assert "document_ids" not in kw and "filters" not in kw


def test_an_empty_selection_sends_nothing_new(monkeypatch):
    """Byte-identical to the pre-existing request for every run that ignores the
    picker, which is the overwhelming majority of them."""
    _result, calls = _run(monkeypatch, request_body=_Req([]))
    assert "extra_document_ids" not in calls["build"][0]
    assert "extra_document_ids" not in calls["bundle"][0]


def test_duplicate_and_numeric_ids_are_normalised_once(monkeypatch):
    """The picker's value type must not decide the wire value, and an id listed
    twice must not be digested (and billed) twice."""
    _result, calls = _run(monkeypatch, request_body=_Req([7, "7", " 8 ", ""]))
    assert calls["build"][0]["extra_document_ids"] == ["7", "8"]


# ── what happens when the pins cannot be honoured ───────────────────────────
def _ack(ids):
    return {"enumerate": {"days": [1]}, "digests": [], "extra_documents_applied": ids}


def test_an_applied_pin_is_recorded_as_honoured(monkeypatch):
    result, _calls = _run(monkeypatch, request_body=_Req(["job-a"]),
                          bundle=_ack(["job-a"]))
    state = result.coverage["extra_documents"]
    assert state["honoured"] is True
    assert state["requested"] == ["job-a"] and state["applied"] == ["job-a"]
    assert "note" not in state


def test_a_dis_that_ignores_the_field_still_produces_a_deliverable(monkeypatch):
    """The block's own sources were digested exactly as before, so refusing the run
    would throw away a build that is otherwise complete and already paid for."""
    result, calls = _run(monkeypatch, request_body=_Req(["job-a"]))
    assert result is not None
    assert calls["build"][0]["extra_document_ids"] == ["job-a"]
    assert svc.last_failure_reason() == ""


def test_a_dis_that_ignores_the_field_says_so_on_coverage(monkeypatch):
    """The silent half is the dangerous half: without this, a pinned document that
    did nothing is indistinguishable from one that was used."""
    result, _calls = _run(monkeypatch, request_body=_Req(["job-a"]))
    state = result.coverage["extra_documents"]
    assert state["honoured"] is False
    assert state["requested"] == ["job-a"] and state["applied"] == []
    assert "did not report applying" in state["note"]


def test_a_client_without_the_parameter_retries_without_it(monkeypatch):
    """A CAS process running an older app.core.dis_client — or any caller with a
    fixed signature — must degrade, not raise: the TypeError would otherwise land in
    the generic DIS-failure branch and fail a generation that would have succeeded."""
    result, calls = _run(monkeypatch, request_body=_Req(["job-a"]), accepts_extra=False)
    assert result is not None
    assert "extra_document_ids" not in calls["build"][0]
    state = result.coverage["extra_documents"]
    assert state["honoured"] is False
    assert "could not be sent" in state["note"]


def test_a_partly_applied_selection_names_the_documents_that_were_not(monkeypatch):
    result, _calls = _run(monkeypatch, request_body=_Req(["job-a", "job-b"]),
                          bundle=_ack(["job-a"]))
    state = result.coverage["extra_documents"]
    assert state["honoured"] is False
    assert "job-b" in state["note"] and "1 of 2" in state["note"]


def test_a_real_typeerror_from_inside_the_call_is_not_swallowed(monkeypatch):
    """Degrading on a signature rejection must not turn every TypeError into a
    silently skipped pin — that would hide a genuine bug in the DIS client."""
    def boom(*a, **k):
        raise TypeError("unsupported operand type(s) for +: 'int' and 'str'")

    with pytest.raises(TypeError):
        svc._call_with_extra_documents(boom, "Block 9", extra_document_ids=["job-a"])


def test_a_run_with_no_selection_records_no_coverage_entry(monkeypatch):
    """Nothing to report is reported as nothing — an 'extra_documents' block on every
    generation would make the real ones easy to scroll past."""
    result, _calls = _run(monkeypatch, request_body=_Req([]))
    assert "extra_documents" not in result.coverage


# ── the async path, which is the one the UI takes ───────────────────────────
def test_the_worker_rebuild_carries_the_selection():
    """The router stores the whole request body in request_json, so dropping the
    field in the rebuild is what would turn an explicit selection into a no-op on the
    only path the UI uses — the same whitelist bug style_id and prompt_id hit."""
    req = jobs._reconstruct_request({"block": "Block 9",
                                     "reference_document_ids": ["job-a"]})
    assert req.reference_document_ids == ["job-a"]


def test_a_job_enqueued_before_the_field_existed_still_rebuilds():
    req = jobs._reconstruct_request({"block": "Block 9"})
    assert req.reference_document_ids == []


# ── the wire body ───────────────────────────────────────────────────────────
def _client(recorder):
    from app.core.dis_client import DISClient

    client = DISClient()
    client.request_sync = recorder  # type: ignore[method-assign]
    return client


def test_the_build_body_gains_the_field_only_when_documents_are_pinned():
    sent = {}

    def record(method, path, **kw):
        sent.update(kw)
        return {}

    client = _client(record)
    client._start_digest_build("Block 9", False, None, "aim", "", [])
    assert "extra_document_ids" not in sent["json"], "an unused picker must not change the body"

    client._start_digest_build("Block 9", False, None, "aim", "", ["job-a", 7])
    assert sent["json"]["extra_document_ids"] == ["job-a", "7"]
    # Everything DIS has always received is still there, in the same shape.
    assert sent["json"]["block"] == "Block 9" and sent["json"]["wait"] is False


def test_the_bundle_query_gains_the_field_only_when_documents_are_pinned():
    sent = {}

    def record(method, path, **kw):
        sent.update(kw)
        return {}

    client = _client(record)
    client.get_digests_bundle_sync("Block 9", client_id="aim")
    assert sent["params"] == {"block": "Block 9"}

    client.get_digests_bundle_sync("Block 9", client_id="aim", extra_document_ids=["job-a"])
    assert sent["params"]["extra_document_ids"] == ["job-a"]
