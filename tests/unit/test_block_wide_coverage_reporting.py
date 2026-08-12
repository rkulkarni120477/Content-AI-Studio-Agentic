"""A block-wide generation must never report plain success while carrying gaps.

Caught in production 2026-08-12: every MAP call failed (an unavailable extractor
model returned call_llm's valid-JSON stub), 8 of 20 days came back with
concept_type "Unknown", every AM.I.B ACS code was orphaned — and the job reported
"completed" with a green tick. The coverage data was recorded correctly the whole
time; nothing surfaced it. The only place the damage was visible was the exported
spreadsheet, which is exactly where a reviewer assumes the work is done.

Two rules, tested here:
  * no day survived  -> refuse to persist; that is not a deliverable
  * some days failed -> keep it, but say so, naming the days
"""
from __future__ import annotations

import json

import pytest

from promptops_app.jobs.block_wide_jobs import _all_days_failed, _coverage_warning


# --------------------------------------------------------------------------- #
# Total failure detection
# --------------------------------------------------------------------------- #
def test_all_days_failed_when_every_day_failed():
    assert _all_days_failed({"enumerated_days": 3, "failed_days": [1, 2, 3]}) is True


def test_not_total_failure_when_some_days_survived():
    assert _all_days_failed({"enumerated_days": 20, "failed_days": [1, 2, 3, 4, 5, 6, 7, 8]}) is False


def test_absent_coverage_is_not_read_as_total_failure():
    """An empty coverage block means "we don't know", not "everything failed".
    Reading it as failure would turn a reporting gap into a refused generation and
    throw away a good deliverable."""
    assert _all_days_failed({}) is False
    assert _all_days_failed({"failed_days": []}) is False
    # failed_days present but no day count at all -> still unknown, not failure
    assert _all_days_failed({"failed_days": [1, 2]}) is False


def test_total_days_is_used_when_enumerated_days_is_missing():
    assert _all_days_failed({"total_days": 2, "failed_days": [1, 2]}) is True


# --------------------------------------------------------------------------- #
# Warning text
# --------------------------------------------------------------------------- #
def test_intact_coverage_produces_no_warning():
    assert _coverage_warning({
        "enumerated_days": 20, "days_in_output": 20,
        "failed_days": [], "missing_days": [], "thin_days": [], "orphan_acs": [],
    }) is None
    assert _coverage_warning({}) is None


def test_warning_names_the_failed_days():
    """"Some days failed" makes someone scan 20 rows to find which — and the failed
    rows are populated with defaults, so they don't stand out."""
    w = _coverage_warning({"enumerated_days": 20, "failed_days": [1, 2, 3, 4, 5, 6, 7, 8]})
    assert "8 of 20 days" in w
    assert "1, 2, 3, 4, 5, 6, 7, 8" in w
    assert "default" in w, "must explain that those rows hold defaults, not real content"


def test_warning_covers_orphan_acs_and_truncates_long_lists():
    w = _coverage_warning({
        "enumerated_days": 20, "failed_days": [],
        "orphan_acs": [f"AM.I.B.K{i}" for i in range(1, 10)],
    })
    assert "9 declared ACS code(s)" in w
    assert "AM.I.B.K1" in w and "and 3 more" in w


def test_warning_reports_every_gap_kind_together():
    w = _coverage_warning({
        "enumerated_days": 10, "failed_days": [2], "missing_days": [9],
        "thin_days": [4], "orphan_acs": ["AM.I.B.K1"],
    })
    for fragment in ("could not be extracted", "missing from the output",
                     "little source material", "not covered by any day"):
        assert fragment in w
    assert w.endswith(".")


def test_singular_and_plural_day_wording():
    assert "day 2)" in _coverage_warning({"enumerated_days": 5, "failed_days": [2]})
    assert "days 2, 3)" in _coverage_warning({"enumerated_days": 5, "failed_days": [2, 3]})


# --------------------------------------------------------------------------- #
# The warning has to reach the client, not just the log
# --------------------------------------------------------------------------- #
class _Job:
    def __init__(self, result_json):
        self.result_json = result_json


@pytest.mark.parametrize("payload,expected", [
    (json.dumps({"warning": "8 of 20 days could not be extracted."}),
     "8 of 20 days could not be extracted."),
    (json.dumps({"warning": "   "}), None),          # blank is not a warning
    (json.dumps({"warning": None}), None),
    (json.dumps({"entity_id": 5}), None),            # key absent
    ("not json at all", None),                       # never raise on a status poll
    (json.dumps(["a", "list"]), None),               # wrong shape
    (None, None),
    ("", None),
])
def test_result_warning_extraction_is_total(payload, expected):
    from app.api.v1.routers.jobs import _result_warning
    assert _result_warning(_Job(payload)) == expected


def test_job_status_response_exposes_warning_separately_from_error():
    """Conflating the two would make a usable-but-incomplete result look failed, or
    hide it entirely — the schema has to carry both."""
    from app.schemas.common import JobStatusResponse
    r = JobStatusResponse(job_id="j", status="completed", warning="2 days failed.")
    assert r.warning == "2 days failed." and r.error_message is None
    assert JobStatusResponse(job_id="j", status="completed").warning is None


def test_ui_renders_the_warning_instead_of_a_bare_done():
    """The panel is the only place a user sees this; a green tick would undo the fix."""
    from pathlib import Path
    src = Path("frontend/src/components/generation/BlockWidePanel/BlockWidePanel.jsx").read_text()
    assert "warning" in src, "BlockWidePanel no longer reads the warning"
    assert "Done, with gaps" in src
    slice_src = Path("frontend/src/features/shared/blockJob.js").read_text()
    assert "payload?.warning" in slice_src, "blockJob state drops the warning field"


# --------------------------------------------------------------------------- #
# The job itself: refuse a fully-failed block, keep a partially-failed one
# --------------------------------------------------------------------------- #
def _run_job(db, monkeypatch, coverage, *, deliverable="cdd"):
    """Drive run_block_wide_job with the digest pipeline stubbed to a given coverage."""
    import types
    from promptops_app.jobs import block_wide_jobs
    from promptops_app.repositories import job_repository

    job_id = job_repository.create_job(
        db, user_name="platformadmin",
        request_params={"deliverable": deliverable, "block": "Block 2",
                        "dis_client_id": "aim", "course_id": 48, "project_id": 23,
                        "user_name": "platformadmin"},
        project_id=23, course_id=48, job_type=f"{deliverable}_block",
    )
    # block_wide_jobs binds SessionLocal at import time, so the module attribute is
    # the one that has to be replaced — patching promptops_app.database.SessionLocal
    # leaves the already-imported name untouched.
    monkeypatch.setattr(block_wide_jobs, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    monkeypatch.setattr("promptops_app.services.prompt_guidance.resolve_prompt_guidance",
                        lambda *a, **k: "")
    gen = {"raw_output": "x", "sections": {}, "coverage": coverage, "model_used": "m"}
    monkeypatch.setattr(block_wide_service_attr(deliverable), lambda *a, **k: gen)
    persisted = {}

    def _persist(*a, **k):
        persisted["called"] = True
        return types.SimpleNamespace(cdd_id=999, blueprint_id=999)

    monkeypatch.setattr(
        f"promptops_app.services.block_wide_service.persist_{deliverable}_and_respond",
        _persist,
    )
    monkeypatch.setattr("promptops_app.services.audit_service.log_audit_event",
                        lambda *a, **k: None)

    block_wide_jobs.run_block_wide_job(job_id)
    db.expire_all()
    from promptops_app.database import GenerationJob
    return db.query(GenerationJob).filter(GenerationJob.id == job_id).first(), persisted


def block_wide_service_attr(deliverable):
    return f"promptops_app.services.block_wide_service.generate_{deliverable}_via_digests"


def test_a_block_where_every_day_failed_is_refused_not_persisted(db, monkeypatch):
    """Persisting it would put a document that reads as finished work in front of a
    reviewer while every generated cell is a default."""
    job, persisted = _run_job(db, monkeypatch,
                              {"enumerated_days": 3, "failed_days": [1, 2, 3]})
    assert job.status == "failed"
    assert "no usable content" in (job.error_message or "")
    assert not persisted, "an all-failed block must not be saved"


def test_a_partially_failed_block_is_kept_but_carries_a_warning(db, monkeypatch):
    job, persisted = _run_job(db, monkeypatch, {
        "enumerated_days": 20, "failed_days": [1, 2, 3, 4, 5, 6, 7, 8],
        "orphan_acs": ["AM.I.B.K1"],
    })
    assert job.status == "completed", "19 good days are still worth keeping"
    assert persisted.get("called") is True
    payload = json.loads(job.result_json)
    assert "8 of 20 days" in payload["warning"]
    assert "AM.I.B.K1" in payload["warning"]


def test_a_clean_block_records_no_warning(db, monkeypatch):
    job, _ = _run_job(db, monkeypatch, {
        "enumerated_days": 20, "failed_days": [], "missing_days": [],
        "thin_days": [], "orphan_acs": [],
    })
    assert job.status == "completed"
    assert json.loads(job.result_json)["warning"] is None
