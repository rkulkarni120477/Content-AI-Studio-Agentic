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


# ---------------------------------------------------------------------------
# Which model actually wrote it (2026-08-14)
# ---------------------------------------------------------------------------

def test_the_model_that_actually_answered_is_recorded_not_the_one_requested():
    """A CDD built 2026-08-14 recorded reduce_model="Claude Sonnet 5 (Bedrock)"
    while Sonnet 4.5 wrote every word: the primary failed on a parse bug, the
    reliability layer fell back, and only the tier's REQUEST reached the record.

    Two runs of the same tier written by different models are then
    indistinguishable in the audit trail — which is exactly the question a
    "why does this block read differently?" review starts from.
    """
    from promptops_app.services.block_wide_generator import BlockWideGenerator
    import promptops_app.services.llm_service as llm_service

    gen = BlockWideGenerator()
    gen._max_tokens = 4096

    calls = []

    def fake(model_choice, system, user, max_tokens=None, usage_ctx=None):
        calls.append(model_choice)
        # The fallback answered, not the model asked for.
        return types.SimpleNamespace(status="success", text="{}",
                                     model="global.anthropic.claude-sonnet-4-5-20250929-v1:0")

    orig = llm_service.generate_with_metadata
    llm_service.generate_with_metadata = fake
    try:
        gen._call("Claude Sonnet 5 (Bedrock)", "sys", "usr")
        gen._call("Claude Sonnet 5 (Bedrock)", "sys", "usr")
    finally:
        llm_service.generate_with_metadata = orig

    assert calls == ["Claude Sonnet 5 (Bedrock)"] * 2, "the tier's choice is still what we ask for"
    assert gen.reduce_models_used == ["global.anthropic.claude-sonnet-4-5-20250929-v1:0"] * 2


def test_the_actual_models_survive_into_the_audit_row_with_their_call_counts():
    """Recording it on the generator is not enough — it has to reach the durable
    record without an auditor joining another table.

    Counts rather than a set: one section out of twelve coming from the fallback is
    a blip, twelve out of twelve is a dead primary, and both are urgent in different
    ways. A set answers "which models" and silently loses "how much".
    """
    from promptops_app.services.block_wide_service import _provenance

    result = types.SimpleNamespace(
        reduce_model="Claude Sonnet 5 (Bedrock)", tier="standard", prompt_provenance={},
        reduce_models_used=["m-4-5", "m-4-5", "m-5"],
    )
    prov = _provenance("cdd", result, {}, map_guidance="")
    assert prov["reduce_model"] == "Claude Sonnet 5 (Bedrock)"
    assert prov["reduce_models_used"] == {"m-4-5": 2, "m-5": 1}

    md = _audit_provenance(prov, None)
    assert md["reduce_models_used"] == {"m-4-5": 2, "m-5": 1}


def test_a_run_with_no_fallback_reports_the_requested_model_as_the_used_one():
    """The healthy case must not read as suspicious: when nothing fell back, the
    requested and used models agree, and that agreement is visible."""
    from promptops_app.services.block_wide_service import _provenance

    result = types.SimpleNamespace(reduce_model="Claude Sonnet 5 (Bedrock)", tier="standard",
                                   prompt_provenance={}, reduce_models_used=["sonnet-5"] * 3)
    assert _provenance("cdd", result, {}, map_guidance="")["reduce_models_used"] == {"sonnet-5": 3}


def test_model_counts_read_in_the_order_the_run_actually_went():
    """The primary answered twice, then died and the fallback took over. Sorting
    would put the fallback first and invert the story the row tells."""
    from promptops_app.services.block_wide_service import _model_call_counts
    assert list(_model_call_counts(["sonnet-5", "sonnet-5", "aaa-fallback"])) == [
        "sonnet-5", "aaa-fallback"]


def test_no_models_recorded_is_an_empty_mapping_not_a_missing_key():
    """The legacy path and the injected-llm seam have no model to report; the key
    must still exist so a consumer never has to distinguish absent from empty."""
    from promptops_app.services.block_wide_service import _model_call_counts
    assert _model_call_counts(None) == {} and _model_call_counts([]) == {}


def test_the_injected_llm_seam_records_no_model_rather_than_a_wrong_one():
    """Tests inject a bare text fn with no model to report. Inventing one (e.g.
    echoing model_choice) would make the seam claim a call that never happened."""
    from promptops_app.services.block_wide_generator import BlockWideGenerator
    gen = BlockWideGenerator(llm=lambda m, s, u: "{}")
    gen._call("Claude Sonnet 5 (Bedrock)", "sys", "usr")
    assert gen.reduce_models_used == []


def test_the_legacy_path_gains_no_model_key():
    """_audit_provenance({}) must stay empty — the legacy path has no reduce stage."""
    assert "reduce_models_used" not in _audit_provenance({}, _COVERAGE)


def test_a_digest_run_always_carries_the_key_even_with_nothing_to_report():
    """A digest-path row must never omit it: a consumer asking "did this fall back?"
    should read {} (nothing recorded), not KeyError."""
    out = _audit_provenance({"prompt_source": "digest_pipeline"}, None)
    assert out["reduce_models_used"] == {}


def test_prompt_guidance_distillation_is_attributed_to_the_requesting_course():
    """Its call landed in llm_usage_logs as entity_type="unattributed" with NULL
    project and course, so a block-wide generation's cost report excluded its own
    first LLM call (measured 2026-08-14: $0.0106 outside the block's total)."""
    from promptops_app.services.prompt_guidance import _usage_ctx
    from promptops_app.services.usage_service import UsageLogContext

    req = types.SimpleNamespace(project_id=23, course_id=48, block="Block 2")
    ctx = _usage_ctx(req, "cdd", types.SimpleNamespace(username="platformadmin"))

    assert isinstance(ctx, UsageLogContext)
    assert (ctx.project_id, ctx.course_id) == (23, 48)
    assert ctx.entity_type == "cdd" and ctx.entity_id == "Block 2"
    assert ctx.user_name == "platformadmin"
    # Distinguishable from the REDUCE rows of the same block, which carry the
    # reduce template name — otherwise the two stages are one undifferentiated bill.
    assert ctx.prompt_template == "prompt_guidance_distill"


def test_prompt_guidance_attribution_failure_never_breaks_a_generation():
    from promptops_app.services.prompt_guidance import _usage_ctx

    class Boom:
        @property
        def project_id(self):
            raise RuntimeError("boom")
    assert _usage_ctx(Boom(), "cdd", None) is None


def test_the_distillation_call_actually_carries_the_context():
    """_usage_ctx existing proves nothing if _digest_prompt drops it on the floor."""
    import promptops_app.services.prompt_guidance as pg
    seen = {}

    def fake_generate_text(model, system, user, usage_ctx=None):
        seen["ctx"] = usage_ctx
        return "1. do the thing"

    import promptops_app.services.llm_service as llm_service
    orig = llm_service.generate_text
    llm_service.generate_text = fake_generate_text
    try:
        pg._digest_prompt("some prompt text", "GPT-5.4", usage_ctx="SENTINEL")
    finally:
        llm_service.generate_text = orig

    assert seen["ctx"] == "SENTINEL"
