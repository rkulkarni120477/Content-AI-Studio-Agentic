"""Audit-trail coverage for the block-wide digest pipeline.

The gap this guards: the legacy CDD/Blueprint paths are synchronous, so one
``*.created`` event records both "someone asked" and "here is the result". Block-wide
generation is always async, which splits those apart — and an expensive,
user-attributed operation that fails must not vanish from the audit trail. It also
records prompts differently (N batched REDUCE calls + a per-day MAP stage on the DIS
side, so there is no single verbatim pair to store), which is fine only as long as the
substitute provenance is actually recorded and the substitution is self-evident.
"""
from __future__ import annotations

import types

from promptops_app.services.audit_service import AUDIT_EVENTS
from promptops_app.services.block_wide_service import _audit_provenance


# --------------------------------------------------------------------------- #
# Event taxonomy
# --------------------------------------------------------------------------- #
def test_block_wide_events_are_registered_in_the_taxonomy():
    """Unregistered actions still write, but lose their level/icon/label — so they
    render as unlabelled rows in the audit UI and can't be filtered by severity."""
    for action, entity in (("cdd.block_requested", "cdd"),
                           ("cdd.block_failed", "cdd"),
                           ("blueprint.block_requested", "blueprint"),
                           ("blueprint.block_failed", "blueprint")):
        assert action in AUDIT_EVENTS, f"{action} missing from AUDIT_EVENTS"
        assert AUDIT_EVENTS[action]["entity"] == entity
        assert AUDIT_EVENTS[action]["label"]


def test_failure_events_are_not_logged_at_info_level():
    """A failed generation must be filterable as a problem, not buried among the
    info-level successes."""
    for action in ("cdd.block_failed", "blueprint.block_failed"):
        assert AUDIT_EVENTS[action]["level"] in {"warning", "critical"}


# --------------------------------------------------------------------------- #
# Provenance recorded on the success path
# --------------------------------------------------------------------------- #
def _prov(**over):
    base = {
        "prompt_source": "digest_pipeline",
        "quality_tier": "standard",
        "reduce_model": "Claude Sonnet 4.5 (Bedrock)",
        "digest_build": {"built": 18, "cached": 2, "failed": 0, "map_calls": 18},
        "map_guidance_applied": True,
        "map_guidance": "1. Preserve exact FAA/ACS terminology.",
        "reduce_prompts": {"narrative": {"template_name": "cdd_reduce",
                                         "template_version": "v2",
                                         "system_source": "db", "user_source": "db"}},
    }
    base.update(over)
    return base


_COVERAGE = {
    "total_days": 20, "enumerated_days": 20, "days_in_output": 20,
    "declared_acs": ["A.1", "A.2"], "covered_acs": ["A.1"], "orphan_acs": ["A.2"],
    "failed_days": [7], "thin_days": [19], "complete": False,
}


def test_legacy_path_audit_metadata_is_unchanged():
    """The legacy path passes prompt_provenance=None and must gain no new keys — its
    audit row already holds the verbatim prompts."""
    assert _audit_provenance(None, None) == {}
    assert _audit_provenance({}, _COVERAGE) == {}


def test_digest_path_records_the_template_that_drove_the_reduce():
    """Name+version is what makes a digest generation reconstructible: DB prompt
    versions are append-only and immutable, so the pair pins exact text."""
    out = _audit_provenance(_prov(), _COVERAGE)
    narrative = out["reduce_prompts"]["narrative"]
    assert (narrative["template_name"], narrative["template_version"]) == ("cdd_reduce", "v2")
    assert narrative["system_source"] == "db"
    assert out["generation_path"] == "digest_pipeline"
    assert out["quality_tier"] == "standard"
    assert out["reduce_model"] == "Claude Sonnet 4.5 (Bedrock)"


def test_digest_path_records_the_guidance_text_not_just_a_flag():
    """map_guidance is the ONLY channel by which the admin's DB-maintained prompt
    reaches MAP/REDUCE. A boolean would leave 'why did it say that?' unanswerable."""
    out = _audit_provenance(_prov(), _COVERAGE)
    assert out["map_guidance_applied"] is True
    assert "FAA/ACS terminology" in out["map_guidance"]


def test_digest_path_records_honest_source_accounting():
    """dis_source_units_count is 0 on this path (the pipeline reads sources per-day on
    the DIS side), which alone reads as 'no sources used' — the opposite of true."""
    out = _audit_provenance(_prov(), _COVERAGE)
    acct = out["source_accounting"]
    assert acct["enumerated_days"] == 20 and acct["days_in_output"] == 20
    assert acct["failed_days"] == [7] and acct["thin_days"] == [19]
    assert acct["declared_acs_count"] == 2 and acct["orphan_acs_count"] == 1
    assert acct["complete"] is False
    assert out["coverage"]["total_days"] == 20


def test_provenance_tolerates_a_partial_report():
    """A DIS hiccup can leave digest_build/coverage empty; auditing must still work
    rather than raising inside the persist tail and losing the whole generation."""
    out = _audit_provenance({"prompt_source": "digest_pipeline"}, None)
    assert out["reduce_prompts"] == {} and out["digest_build"] == {}
    assert out["map_guidance"] == "" and out["map_guidance_applied"] is False
    assert "coverage" not in out and "source_accounting" not in out


# --------------------------------------------------------------------------- #
# Failure auditing in the worker
# --------------------------------------------------------------------------- #
def test_audit_failure_writes_the_right_action_per_deliverable(monkeypatch):
    from promptops_app.jobs import block_wide_jobs

    seen = []
    monkeypatch.setattr("promptops_app.services.audit_service.log_audit_event",
                        lambda db, user, action, **kw: seen.append((user, action, kw)))
    req = types.SimpleNamespace(block="Block 2", quality_tier=None, model_choice="m",
                                prompt_id=77, project_id=3, course_id=9)
    user = types.SimpleNamespace(username="platformadmin")

    for deliverable, expected in (("cdd", "cdd.block_failed"),
                                  ("blueprint", "blueprint.block_failed")):
        block_wide_jobs._audit_failure(None, deliverable, req, user, "job-1", "DIS down")
        actor, action, kw = seen[-1]
        assert (actor, action) == ("platformadmin", expected)
        # Attribution and enough context to correlate with the request event.
        assert kw["project_id"] == 3 and kw["course_id"] == 9
        assert kw["metadata"]["job_id"] == "job-1"
        assert kw["metadata"]["block"] == "Block 2"
        assert kw["metadata"]["reason"] == "DIS down"
        assert kw["metadata"]["prompt_id"] == 77
        assert kw["metadata"]["quality_tier"] == "standard"   # None -> default


def test_audit_failure_never_raises_into_the_worker(monkeypatch):
    """Mirrors log_audit_event's own contract: audit logging must never be the reason
    a job crashes — and this runs inside an except block, so a raise here would mask
    the original error."""
    from promptops_app.jobs import block_wide_jobs

    def boom(*a, **k):
        raise RuntimeError("audit table gone")
    monkeypatch.setattr("promptops_app.services.audit_service.log_audit_event", boom)
    block_wide_jobs._audit_failure(None, "cdd", types.SimpleNamespace(),
                                   types.SimpleNamespace(), "job-2", "why")


def test_audit_failure_falls_back_to_a_named_actor(monkeypatch):
    """An audit row with a blank actor is not attributable; the worker rebuilds the
    user from job params, which may be missing."""
    from promptops_app.jobs import block_wide_jobs

    seen = []
    monkeypatch.setattr("promptops_app.services.audit_service.log_audit_event",
                        lambda db, user, action, **kw: seen.append(user))
    block_wide_jobs._audit_failure(None, "cdd", types.SimpleNamespace(),
                                   types.SimpleNamespace(username=""), "job-3", "why")
    assert seen[-1] == "cas-user"


# --------------------------------------------------------------------------- #
# Cost + prompt-identity accounting. The digest path spans two services, so the
# audit row is the only place the FULL picture of a generation exists.
# --------------------------------------------------------------------------- #
def test_map_token_counts_reach_the_audit_row():
    """MAP runs on the DIS side through its own boto3 client, so it never reaches
    llm_usage_logs — the DIS build report is the only record of that spend, and it
    is the majority of a block-wide generation's tokens."""
    from promptops_app.services.block_wide_service import _provenance
    report = {"built": 20, "cached": 0, "failed": 0, "map_calls": 20,
              "map_tokens_in": 167620, "map_tokens_out": 8845, "strategy": "sequential"}
    result = types.SimpleNamespace(reduce_model="m", tier="standard", prompt_provenance={})
    prov = _provenance("cdd", result, report, map_guidance="")
    assert prov["digest_build"]["map_tokens_in"] == 167620
    assert prov["digest_build"]["map_tokens_out"] == 8845
    # ...and survives into the audit metadata, not just the provenance dict.
    md = _audit_provenance(prov, None)
    assert md["digest_build"]["map_tokens_in"] == 167620


def test_reduce_calls_report_usage_for_cost_attribution():
    """Every other LLM surface in the app passes a UsageLogContext; the block-wide
    reduce — the most expensive call per request — did not, so it produced no
    token/cost/latency rows at all."""
    from promptops_app.services.block_wide_generator import BlockWideGenerator
    from promptops_app.services.usage_service import UsageLogContext

    gen = BlockWideGenerator(project_id=7, course_id=9, user_name="sansari")
    gen._block_label = "Block 2"
    prompt = types.SimpleNamespace(template_name="cdd_reduce", template_version="v2")
    ctx = gen._build_usage_ctx("cdd", prompt)
    assert isinstance(ctx, UsageLogContext)
    assert ctx.entity_type == "cdd" and ctx.entity_id == "Block 2"
    assert ctx.user_name == "sansari" and ctx.project_id == 7 and ctx.course_id == 9
    assert ctx.prompt_template == "cdd_reduce" and ctx.prompt_version == "v2"


def test_usage_context_failure_never_breaks_a_generation():
    """Accounting must never be load-bearing."""
    from promptops_app.services.block_wide_generator import BlockWideGenerator
    gen = BlockWideGenerator()

    class Boom:
        @property
        def template_name(self):
            raise RuntimeError("boom")
    assert gen._build_usage_ctx("cdd", Boom()) is None


def test_injected_llm_seam_still_bypasses_usage_logging():
    """Tests and evals inject their own llm fn; that path must not try to log usage
    (it never called the real client, so there is no result to account for)."""
    from promptops_app.services.block_wide_generator import BlockWideGenerator
    seen = []
    gen = BlockWideGenerator(llm=lambda m, s, u: seen.append((m, s, u)) or "{}")
    assert gen._call("m", "sys", "usr") == "{}"
    assert seen == [("m", "sys", "usr")]
