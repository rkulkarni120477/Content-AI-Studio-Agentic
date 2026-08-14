"""The generation form's controls must reach a model, or say they didn't.

Audited 2026-08-13: on the block-wide (digest pipeline) path, of everything the
CDD/Blueprint form offers, only the selected prompt reached an LLM. Style,
Additional Instructions and Estimated Duration were validated by the API, written
into ``generation_params`` — so the provenance row showed them as honoured — and
then read by nothing. The page even rendered "🎨 <style> will be applied (active
style)" above the button that ignored it.

That is the failure mode these tests exist to prevent: not a crash, not an error,
but controls that look live over an output they never touched. So the assertions
here are mostly of the form "this specific input is present in the text sent to
that specific stage", and the negative ones ("the API accepted it but nothing used
it") are the regressions that matter most.
"""
from __future__ import annotations

import logging
import types

import pytest

from promptops_app.services import block_wide_service as svc
from promptops_app.services import user_directives as ud


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def _style(name="Block 2 Style", summary="Write in the second person.",
           custom="", documents=()):
    return types.SimpleNamespace(
        id=116, name=name, generated_summary=summary,
        custom_instructions=custom, style_documents=list(documents),
    )


def _reduce_result():
    return types.SimpleNamespace(prompt_provenance={}, reduce_model="m", tier="standard")


def _req(style_id=116, instructions="Emphasise Bloom's levels 4-6.", hours=8):
    return types.SimpleNamespace(
        block="Block 2", course_id=48, project_id=7, quality_tier=None,
        prompt_id=77, style_id=style_id, extra_instructions=instructions,
        estimated_duration_hours=hours, model_choice="GPT-5.4",
    )


@pytest.fixture
def styles(monkeypatch):
    """Patchable style lookup. ``styles.row = None`` simulates a deleted style."""
    from promptops_app.repositories import style_repository

    holder = types.SimpleNamespace(row=_style(), asked=[])

    def _get(db, style_id, **kw):
        holder.asked.append(style_id)
        return holder.row

    monkeypatch.setattr(style_repository, "get_style_by_id", _get)
    return holder


# --------------------------------------------------------------------------- #
# What each input reaches
# --------------------------------------------------------------------------- #
def test_the_requesters_instructions_reach_both_stages(styles):
    """MAP writes derived_objective/misconceptions/concept_scope and REDUCE can only
    APPEND framing to them, so instructions that reached REDUCE alone could not
    change the cells they are aimed at."""
    d = ud.resolve_user_directives(object(), _req())
    assert "Bloom's levels 4-6" in d.map_text
    assert "Bloom's levels 4-6" in d.reduce_text
    assert d.applied["extra_instructions_applied"] is True


def test_the_style_reaches_both_stages_but_compactly_at_map(styles):
    styles.row = _style(summary="SUMMARY-MARKER", custom="CUSTOM-MARKER")
    d = ud.resolve_user_directives(object(), _req())
    assert "SUMMARY-MARKER" in d.map_text, "style must reach the per-day extraction"
    assert "SUMMARY-MARKER" in d.reduce_text
    # The full context carries the heading build_style_context adds; the compact
    # form is the summary alone. MAP pays for every character once PER DAY.
    assert "Active Instructional Style: Block 2 Style" in d.reduce_text
    assert len(d.map_text) < len(d.reduce_text)
    assert d.applied["style_applied_to_map"] is True
    assert d.applied["style_applied_to_reduce"] is True
    assert d.applied["style_name"] == "Block 2 Style"


def test_the_declared_duration_reaches_reduce_only(styles):
    """A per-day extractor can do nothing with a block-level hour count."""
    d = ud.resolve_user_directives(object(), _req(hours=8))
    assert "8 hour" in d.reduce_text
    assert "8 hour" not in d.map_text
    assert d.applied["estimated_duration_hours"] == 8


def test_the_duration_instructs_the_model_to_report_a_clash_not_reconcile_it(styles):
    """A declared 8 hours against 20 enumerated days is a real planning discrepancy.
    Asking the model to make the two agree would bury exactly the thing worth
    surfacing, so the calendar is named as authoritative and a clash is reportable."""
    text = ud.resolve_user_directives(object(), _req(hours=8)).reduce_text
    assert "authoritative" in text
    assert "SAY SO" in text


def test_the_compact_style_prefers_the_summary_over_raw_documents(styles):
    """Truncating the first 2000 chars of a reference PDF is not a summary of a
    style, it is a random excerpt — and it would consume MAP's context headroom to
    say almost nothing."""
    doc = types.SimpleNamespace(
        document=types.SimpleNamespace(filename="ref.pdf", content="DOC-BODY " * 500))
    styles.row = _style(summary="", custom="", documents=[doc])
    d = ud.resolve_user_directives(object(), _req())
    assert d.applied["style_chars_map"] == 0, (
        "no summary and no custom instructions ⇒ no compact form to send"
    )
    assert "DOC-BODY" not in d.map_text
    assert "DOC-BODY" in d.reduce_text, "REDUCE still gets the full context"


def test_custom_instructions_are_the_compact_fallback(styles):
    styles.row = _style(summary="", custom="Use imperative headings.")
    d = ud.resolve_user_directives(object(), _req())
    assert "Use imperative headings." in d.map_text


# --------------------------------------------------------------------------- #
# Bounds
# --------------------------------------------------------------------------- #
def test_an_enormous_style_is_capped_for_each_stage(styles, monkeypatch):
    """build_style_context embeds whole style DOCUMENTS, so an uncapped style could
    be tens of thousands of characters. At MAP that is not merely expensive: it is a
    hard "Input is too long" from Bedrock that fails every day of the build."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "block_wide_style_chars_map", 50)
    monkeypatch.setattr(settings, "block_wide_style_chars_reduce", 200)
    styles.row = _style(summary="x" * 10_000)
    d = ud.resolve_user_directives(object(), _req())
    assert d.applied["style_chars_map"] == 50
    assert d.applied["style_chars_reduce"] == 200


def test_capping_is_logged_with_counts_rather_than_done_silently(styles, monkeypatch, caplog):
    """Silent capping is what cost ~60% of AIM's own prompt template before
    prompt_guidance started windowing; the same discipline applies here."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "block_wide_style_chars_map", 10)
    styles.row = _style(summary="y" * 500)
    with caplog.at_level(logging.WARNING):
        ud.resolve_user_directives(object(), _req())
    record = next(r for r in caplog.records if "user_directives_capped" in r.getMessage())
    assert "chars=500" in record.getMessage() and "cap=10" in record.getMessage()
    assert "BLOCK_WIDE_STYLE_CHARS" in record.getMessage(), "say which limit to raise"


def test_absurdly_long_instructions_are_capped_rather_than_overflowing_map(caplog):
    """The schema caps this at 5000, but the worker rebuilds the request from job-row
    JSON with no re-validation — and overshooting a model's context window is a hard
    Bedrock rejection that fails every day of the block, not just one."""
    with caplog.at_level(logging.WARNING):
        d = ud.resolve_user_directives(object(), _req(style_id=None, instructions="z" * 20_000))
    assert d.applied["extra_instructions_chars"] == 5000
    assert any("what=extra_instructions" in r.getMessage() for r in caplog.records)


def test_truncation_is_recorded_on_the_provenance_row_not_only_in_a_log(styles, monkeypatch):
    """The char counts are POST-cap — they are what the model saw — so on their own a
    12k style and a 40k style cut down to 12k are indistinguishable. The WARNING says
    so at the time and then rotates away; the provenance row is the durable record, and
    "your style was truncated" is exactly what someone asking why it barely showed up
    needs to read."""
    from app.core.config import settings

    styles.row = _style(summary="x" * 1_000)
    # Measured against an uncapped control rather than a magic number: the style
    # context is whatever build_style_context wraps around the summary, and hardcoding
    # today's total would make this test fail on an unrelated wording change there.
    full_chars = ud.resolve_user_directives(
        object(), _req(instructions="", hours=None),
    ).applied["style_chars_reduce"]

    monkeypatch.setattr(settings, "block_wide_style_chars_reduce", 100)
    d = ud.resolve_user_directives(object(), _req(instructions="", hours=None))

    assert d.applied["style_chars_reduce"] == 100, "post-cap: what the model saw"
    assert d.applied["truncated_chars"]["style_reduce"] == full_chars - 100
    assert "style_map" not in d.applied["truncated_chars"], "only what was actually cut"


def test_nothing_truncated_means_no_truncation_key_at_all(styles):
    """A key that is always present with a falsey value gets skimmed past. Present
    ⇒ fidelity was lost, absent ⇒ it was not; that asymmetry is the point."""
    d = ud.resolve_user_directives(object(), _req())
    assert "truncated_chars" not in d.applied


def test_a_truncated_input_tells_the_model_it_is_reading_a_fragment(styles, monkeypatch):
    """A cut at an arbitrary character leaves a fragment that still reads as a whole
    instruction — "cover every objective in" is followed by nothing, and the model has
    no way to know it is acting on half a sentence."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "block_wide_style_chars_reduce", 300)
    styles.row = _style(summary="x" * 5_000)
    d = ud.resolve_user_directives(object(), _req(instructions="", hours=None))

    assert "truncated" in d.reduce_text
    # The marker is budgeted INSIDE the cap, not added on top of it: a cap that can be
    # exceeded by its own overflow notice does not bound what reaches the model.
    assert d.applied["style_chars_reduce"] == 300


# --------------------------------------------------------------------------- #
# Honesty when an input cannot be applied
# --------------------------------------------------------------------------- #
def test_a_deleted_style_is_recorded_as_requested_but_not_applied(styles, caplog):
    """Course 48 pointed at style 116 after it was deleted. "Requested but not
    applied" and "never requested" look identical in the output, so provenance has
    to distinguish them — this is the row someone reads when asking why the style
    had no effect."""
    styles.row = None
    with caplog.at_level(logging.WARNING):
        d = ud.resolve_user_directives(object(), _req(style_id=116))
    assert d.applied["style_id"] == 116
    assert d.applied["style_applied_to_map"] is False
    assert d.applied["style_applied_to_reduce"] is False
    assert any("user_directives_style_missing" in r.getMessage() for r in caplog.records)


def test_no_style_selected_is_not_an_error(styles):
    d = ud.resolve_user_directives(object(), _req(style_id=None))
    assert d.applied["style_id"] is None
    assert styles.asked == [], "must not query for a style that was not selected"


def test_the_active_style_is_never_resolved_server_side(monkeypatch, styles):
    """There is deliberately no fallback to get_active_style: its unscoped global
    is_active lookup currently resolves 75 of 104 courses to one tenant's style, and a
    second invisible source of truth for a control the user can SEE is worse than no
    fallback at all.

    Asserted by making the call itself fatal rather than by grepping the source, which
    would pass on a docstring that merely mentions it."""
    import promptops_app.database as database

    def _forbidden(*a, **kw):
        raise AssertionError("resolved the active style behind the user's back")

    monkeypatch.setattr(database, "get_active_style", _forbidden)
    d = ud.resolve_user_directives(object(), _req(style_id=None))
    assert d.applied["style_id"] is None
    assert d.applied["style_applied_to_reduce"] is False


def test_a_broken_style_lookup_degrades_instead_of_failing_the_generation(monkeypatch, caplog):
    from promptops_app.repositories import style_repository

    def _boom(db, style_id, **kw):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(style_repository, "get_style_by_id", _boom)
    with caplog.at_level(logging.WARNING):
        d = ud.resolve_user_directives(object(), _req())
    assert d.applied["style_applied_to_reduce"] is False
    assert "Bloom's" in d.map_text, "the instructions are independent and must survive"


@pytest.mark.parametrize("junk", ["", "not-a-number", 0, -3, None, {}])
def test_a_junk_style_id_does_not_take_the_instructions_down_with_it(styles, junk):
    """The three inputs are independent. Coercing style_id where `applied` is built
    would have let one malformed value discard a perfectly good instruction — and the
    async worker rebuilds this request from job-row JSON, so this is a real boundary
    rather than a hypothetical one."""
    d = ud.resolve_user_directives(object(), _req(style_id=junk))
    assert d.applied["style_id"] is None
    assert "Bloom's levels 4-6" in d.map_text


def test_a_junk_duration_is_dropped_rather_than_rendered(styles):
    d = ud.resolve_user_directives(object(), _req(hours="eight"))
    assert d.applied["estimated_duration_hours"] is None
    assert "hour(s)" not in d.reduce_text


def test_nothing_supplied_produces_no_directives_at_all(styles):
    """The no-input path must be byte-identical to the pre-feature behaviour, so an
    unchanged request cannot invalidate a cached digest."""
    d = ud.resolve_user_directives(
        object(), _req(style_id=None, instructions="", hours=None))
    assert d.map_text == "" and d.reduce_text == ""
    assert not d


# --------------------------------------------------------------------------- #
# Composition onto the wire
# --------------------------------------------------------------------------- #
def test_composition_puts_the_requesters_words_after_the_standing_guidance():
    """Where a per-run instruction and a standing template conflict, the person
    clicking Generate should win — and later text is the weaker signal only if the
    labels do not say otherwise, which they do."""
    out = ud.compose_guidance("STANDING", "PER-RUN")
    assert out.index("STANDING") < out.index("PER-RUN")


def test_composition_of_empties_adds_no_whitespace():
    assert ud.compose_guidance("", "") == ""
    assert ud.compose_guidance("A", "") == "A"
    assert ud.compose_guidance("", "B") == "B"


# --------------------------------------------------------------------------- #
# End to end: what each stage actually receives
# --------------------------------------------------------------------------- #
def _run(monkeypatch, request_body):
    """Drive _build_and_reduce with DIS and REDUCE stubbed. Returns what each saw."""
    seen = {}

    class _Dis:
        def build_digests_sync(self, block, **kw):
            seen["map_guidance"] = kw.get("map_guidance", "")
            return {"built": 1, "cached": 0, "failed": 0}

        def get_digests_bundle_sync(self, block, **kw):
            return {"enumerate": {"days": [{"day_number": 1}]}, "digests": []}

    class _Gen:
        def __init__(self, **kw):
            pass

        def reduce(self, *a, **kw):
            seen["reduce_guidance"] = kw.get("map_guidance", "")
            seen["reduce_directives"] = kw.get("user_directives", "")
            return types.SimpleNamespace(coverage={}, prompt_provenance={},
                                         reduce_model="m", tier="standard", llm_calls=1)

    import promptops_app.services.block_wide_generator as bwg
    monkeypatch.setattr(bwg, "BlockWideGenerator", _Gen)
    monkeypatch.setattr(svc, "dis_client", _Dis())
    monkeypatch.setattr(svc, "_map_usage_ctx", lambda *a, **k: None)
    monkeypatch.setattr(svc, "_reserve_map_budget", lambda *a, **k: [])
    monkeypatch.setattr(svc, "_settle_map_usage", lambda *a, **k: None)
    result, report, directives = svc._build_and_reduce(
        "cdd", "Block 2", None, types.SimpleNamespace(username="t"), "aim",
        "PROMPT-GUIDANCE", db=None, request_body=request_body)
    return seen, directives


def test_map_receives_the_prompt_guidance_and_the_users_own_inputs(monkeypatch, styles):
    """The regression. Before this change the DIS call carried the distilled prompt
    guidance and nothing else, no matter what the user typed or selected."""
    seen, _ = _run(monkeypatch, _req())
    assert "PROMPT-GUIDANCE" in seen["map_guidance"]
    assert "Bloom's levels 4-6" in seen["map_guidance"]
    assert "Write in the second person." in seen["map_guidance"]


def test_map_is_not_charged_for_what_it_cannot_use(monkeypatch, styles):
    """One MAP call per DAY, so anything sent here is paid for ~20 times on Block 2.
    The hour count has no per-day meaning and the full style context is large."""
    seen, _ = _run(monkeypatch, _req())
    assert "8 hour" not in seen["map_guidance"]
    assert "Active Instructional Style: Block 2 Style" not in seen["map_guidance"]


def test_reduce_receives_the_full_style_and_the_duration(monkeypatch, styles):
    seen, _ = _run(monkeypatch, _req())
    assert "Active Instructional Style: Block 2 Style" in seen["reduce_directives"]
    assert "8 hour" in seen["reduce_directives"]
    assert "Bloom's levels 4-6" in seen["reduce_directives"]


def test_the_two_guidance_layers_stay_separate_arguments_at_reduce(monkeypatch, styles):
    """Pre-concatenating them would make the standing template and the requester's
    per-run intent indistinguishable in the prompt and in provenance."""
    seen, _ = _run(monkeypatch, _req())
    assert seen["reduce_guidance"] == "PROMPT-GUIDANCE"
    assert "PROMPT-GUIDANCE" not in seen["reduce_directives"]


def test_a_request_with_no_user_inputs_sends_exactly_the_prompt_guidance(monkeypatch, styles):
    """The upgrade must not invalidate every cached digest for users who supply
    nothing: an unchanged request has to produce the unchanged cache key."""
    seen, _ = _run(monkeypatch, _req(style_id=None, instructions="", hours=None))
    assert seen["map_guidance"] == "PROMPT-GUIDANCE"


def test_the_directives_survive_a_failed_build_for_the_provenance_row(monkeypatch, styles):
    """A failed generation is exactly when someone needs to know whether their style
    and instructions were resolved at all."""
    class _Dis:
        def build_digests_sync(self, block, **kw):
            raise ConnectionError("refused")

    monkeypatch.setattr(svc, "dis_client", _Dis())
    monkeypatch.setattr(svc, "_map_usage_ctx", lambda *a, **k: None)
    monkeypatch.setattr(svc, "_reserve_map_budget", lambda *a, **k: [])
    monkeypatch.setattr(svc, "_settle_map_usage", lambda *a, **k: None)
    result, report, directives = svc._build_and_reduce(
        "cdd", "Block 2", None, types.SimpleNamespace(username="t"), "aim",
        db=None, request_body=_req())
    assert (result, report) == (None, None)
    assert directives.applied["style_id"] == 116


# --------------------------------------------------------------------------- #
# The rest of the path
# --------------------------------------------------------------------------- #
def test_provenance_records_which_inputs_reached_which_stage(styles):
    d = ud.resolve_user_directives(object(), _req())
    prov = svc._provenance("cdd", _reduce_result(), {}, "PG", d)
    assert prov["user_directives"]["style_id"] == 116
    assert prov["user_directives"]["style_applied_to_map"] is True
    assert prov["user_directives"]["estimated_duration_hours"] == 8


def test_the_audit_row_alone_shows_which_inputs_shaped_the_document(styles):
    """_audit_provenance's stated purpose is that "an auditor reading audit_logs alone
    does not have to join another table to see what drove the generation". Once style
    and instructions reach MAP/REDUCE, a row that omits them fails that standard — it
    cannot answer "why did it say that?"."""
    d = ud.resolve_user_directives(object(), _req())
    meta = svc._audit_provenance(svc._provenance("cdd", _reduce_result(), {}, "PG", d), {})
    assert meta["user_directives"]["style_id"] == 116
    assert meta["user_directives"]["style_name"] == "Block 2 Style"
    assert meta["user_directives"]["style_applied_to_reduce"] is True
    assert meta["user_directives"]["estimated_duration_hours"] == 8
    assert meta["user_directives"]["extra_instructions_applied"] is True


def test_the_audit_row_pins_the_style_text_not_just_its_id(styles):
    """style_id + style_name cannot reproduce what the model saw once someone edits or
    deletes the style, and both happen — course 48's style 116 was deleted. The
    fingerprint is what makes two documents comparable after the fact, without storing
    12k characters of style on every audit row."""
    first = ud.resolve_user_directives(object(), _req()).applied["style_fingerprint"]
    assert first, "a resolved style must be fingerprinted"

    # Same style, unchanged ⇒ same fingerprint (two documents are comparable).
    assert ud.resolve_user_directives(object(), _req()).applied["style_fingerprint"] == first

    # Edited style, same id and name ⇒ different fingerprint (the difference is
    # visible even though nothing else on the row changed).
    styles.row = _style(summary="Write in the first person instead.")
    assert ud.resolve_user_directives(object(), _req()).applied["style_fingerprint"] != first


def test_no_style_means_no_fingerprint_rather_than_a_hash_of_nothing(styles):
    d = ud.resolve_user_directives(object(), _req(style_id=None))
    assert d.applied["style_fingerprint"] == ""


def test_the_audit_row_distinguishes_a_style_requested_from_one_applied(styles):
    """The two are indistinguishable in the output, so the audit row is the only place
    the difference can be seen — course 48's style 116 was deleted."""
    styles.row = None
    d = ud.resolve_user_directives(object(), _req(style_id=116))
    meta = svc._audit_provenance(svc._provenance("cdd", _reduce_result(), {}, "PG", d), {})
    assert meta["user_directives"]["style_id"] == 116
    assert meta["user_directives"]["style_applied_to_map"] is False
    assert meta["user_directives"]["style_applied_to_reduce"] is False


def test_the_legacy_path_reports_no_directives_rather_than_omitting_the_key(styles):
    """A missing key reads as "this build predates the feature"; an empty one reads as
    "nothing was supplied". Only the second is true on the legacy single-call path."""
    meta = svc._audit_provenance({"prompt_source": "registry"}, {})
    assert meta["user_directives"] == {}


def test_a_failed_build_records_the_directives_it_ran_with(styles):
    """The failure row is where "did my instructions break it?" gets asked."""
    import promptops_app.jobs.block_wide_jobs as jobs

    captured = {}

    class _Audit:
        @staticmethod
        def log_audit_event(db, user, action, **kw):
            captured.update(action=action, metadata=kw.get("metadata") or {})

    import sys
    real = sys.modules.get("promptops_app.services.audit_service")
    sys.modules["promptops_app.services.audit_service"] = _Audit
    try:
        d = ud.resolve_user_directives(object(), _req())
        jobs._audit_failure(object(), "cdd", _req(), types.SimpleNamespace(username="t"),
                            "job1", "every day failed", user_directives=d.applied)
    finally:
        if real is not None:
            sys.modules["promptops_app.services.audit_service"] = real
    assert captured["action"] == "cdd.block_failed"
    assert captured["metadata"]["user_directives"]["style_id"] == 116


def test_an_outage_failure_does_not_blame_inputs_it_never_reached(styles):
    """DIS unreachable and zero-enumerated-days fail before any directive could
    matter; recording them there would invite blaming a user's wording for an outage."""
    import promptops_app.jobs.block_wide_jobs as jobs

    captured = {}

    class _Audit:
        @staticmethod
        def log_audit_event(db, user, action, **kw):
            captured.update(metadata=kw.get("metadata") or {})

    import sys
    real = sys.modules.get("promptops_app.services.audit_service")
    sys.modules["promptops_app.services.audit_service"] = _Audit
    try:
        jobs._audit_failure(object(), "cdd", _req(), types.SimpleNamespace(username="t"),
                            "job1", "DIS unavailable")
    finally:
        if real is not None:
            sys.modules["promptops_app.services.audit_service"] = real
    assert "user_directives" not in captured["metadata"]


def test_provenance_still_works_for_a_caller_that_passes_no_directives():
    prov = svc._provenance("cdd", _reduce_result(), {}, "PG")
    assert prov["user_directives"] == {}


def test_provenance_identifies_what_map_was_actually_sent(styles):
    """``map_guidance`` is only the prompt-derived LAYER; MAP receives it composed with
    the requester's directives. Recording the layer under a name that reads like the
    whole thing is the same category of dishonesty this change removed, so the composed
    string is identified too — by fingerprint, since embedding a 12k style context in
    every audit row is the cost user_directives deliberately avoids."""
    d = ud.resolve_user_directives(object(), _req())
    prov = svc._provenance("cdd", _reduce_result(), {}, "PROMPT-GUIDANCE", d)

    # Composed, so strictly longer than the layer recorded beside it.
    assert prov["map_guidance"] == "PROMPT-GUIDANCE"
    assert prov["map_guidance_sent_chars"] > len("PROMPT-GUIDANCE")
    assert len(prov["map_guidance_sent_fingerprint"]) == 16


def test_the_sent_fingerprint_changes_when_the_requesters_input_changes(styles):
    """This is the exact discriminator for the per-day digest cache key, which is
    computed from the composed string. Equal fingerprints across two runs rule out
    "the inputs changed" as the reason a block rebuilt from cold; unequal ones confirm
    it. A fingerprint that ignored the directives could do neither."""
    a = svc._provenance("cdd", _reduce_result(), {}, "PG",
                        ud.resolve_user_directives(object(), _req(instructions="Focus on DEI.")))
    b = svc._provenance("cdd", _reduce_result(), {}, "PG",
                        ud.resolve_user_directives(object(), _req(instructions="Focus on labs.")))

    assert a["map_guidance_sent_fingerprint"] != b["map_guidance_sent_fingerprint"]


def test_a_run_with_no_inputs_at_all_still_reports_the_prompt_layer_it_sent(styles):
    """With no directives the composed string IS the prompt guidance — the fingerprint
    must still be present, or "nothing was sent" and "we did not record it" become
    indistinguishable."""
    d = ud.resolve_user_directives(object(), _req(style_id=None, instructions="", hours=None))
    prov = svc._provenance("cdd", _reduce_result(), {}, "PG", d)

    assert prov["map_guidance_sent_chars"] == len("PG")
    assert prov["map_guidance_sent_fingerprint"] == ud.fingerprint("PG")


def test_the_audit_row_carries_the_sent_fingerprint_without_a_join(styles):
    """Same standard as every other key in _audit_provenance: an auditor reading
    audit_logs alone must not have to join the version row."""
    d = ud.resolve_user_directives(object(), _req())
    prov = svc._provenance("cdd", _reduce_result(), {}, "PG", d)
    row = svc._audit_provenance(prov, None)

    assert row["map_guidance_sent_fingerprint"] == prov["map_guidance_sent_fingerprint"]
    assert row["map_guidance_sent_chars"] == prov["map_guidance_sent_chars"]


def test_a_total_extraction_failure_names_the_users_own_inputs_as_a_candidate(styles):
    """Appending the requester's wording to MAP's extraction contract creates a new
    failure mode: their own text can break every day of a block. "20 of 20 days could
    not be extracted" with no mention of it sends the reader to Bedrock credentials
    and the block id first — the exact wild goose chase failure_reasons exists to
    prevent."""
    from promptops_app.jobs.block_wide_jobs import _directives_hint

    d = ud.resolve_user_directives(object(), _req())
    hint = _directives_hint({"user_directives": d.applied}, {"failed_days": [1, 2, 3]})
    assert "additional instructions" in hint and "style" in hint
    assert "without them" in hint, "the hint must be actionable, not just informative"


def test_thin_days_also_earn_the_hint(styles):
    """Extraction that returned almost nothing is the same class of symptom as
    extraction that failed outright, and just as plausibly caused by the wording
    appended to the extraction contract."""
    from promptops_app.jobs.block_wide_jobs import _directives_hint

    d = ud.resolve_user_directives(object(), _req())
    assert _directives_hint({"user_directives": d.applied}, {"thin_days": [4]})


def test_uncovered_acs_alone_does_not_blame_the_requesters_wording(styles):
    """_coverage_warning also fires when every day extracted cleanly but the block
    declares ACS codes no day covers. That comes from the source material, not from
    how the requester phrased their instructions, and offering "try again without
    them" there is a wrong turn dressed up as help."""
    from promptops_app.jobs.block_wide_jobs import _directives_hint

    d = ud.resolve_user_directives(object(), _req())
    coverage = {"orphan_acs": ["AM.I.B.1", "AM.I.B.2"], "failed_days": [], "thin_days": []}
    assert _directives_hint({"user_directives": d.applied}, coverage) == ""


def test_the_failure_hint_is_absent_when_the_user_supplied_nothing(styles):
    """Otherwise every unrelated failure would blame inputs that were never sent."""
    from promptops_app.jobs.block_wide_jobs import _directives_hint

    failed = {"failed_days": [1, 2]}
    d = ud.resolve_user_directives(object(), _req(style_id=None, instructions="", hours=None))
    assert _directives_hint({"user_directives": d.applied}, failed) == ""
    assert _directives_hint(None, failed) == ""
    assert _directives_hint({}, failed) == ""


def test_the_failure_hint_ignores_inputs_that_never_reached_map(styles):
    """A duration that only reaches REDUCE cannot have broken the per-day extraction,
    and a style that failed to resolve was never applied at all."""
    from promptops_app.jobs.block_wide_jobs import _directives_hint

    styles.row = None
    d = ud.resolve_user_directives(object(), _req(instructions="", hours=8))
    assert _directives_hint({"user_directives": d.applied}, {"failed_days": [1]}) == ""


def test_the_style_survives_the_job_rows_json_round_trip():
    """The worker rebuilds the request from JSON, so a field the rebuild omits is
    indistinguishable from one that was never sent — this is where style_id was
    being lost even once the API accepted it."""
    from promptops_app.jobs.block_wide_jobs import _reconstruct_request

    req = _reconstruct_request({"style_id": 116, "estimated_duration_hours": 8,
                                "extra_instructions": "Focus on DEI."})
    assert req.style_id == 116
    assert req.estimated_duration_hours == 8
    assert req.extra_instructions == "Focus on DEI."


def test_the_api_accepts_style_id_on_the_block_wide_request():
    from app.schemas.block_wide import BlockWideGenerateRequest

    body = BlockWideGenerateRequest(block="Block 2", course_id=48, project_id=7,
                                    style_id=116)
    assert body.style_id == 116


def test_reduce_renders_the_directives_into_the_prompt_after_the_guidance():
    """_guidance_block is the single slot both layers land in; a reduce template is
    DB-editable, so adding a second template variable would be rejected as a
    variable violation for any stored template that does not mention it."""
    from promptops_app.services.block_wide_generator import _guidance_block

    out = _guidance_block("STANDING", "PER-RUN")
    assert out.index("STANDING") < out.index("PER-RUN")
    assert _guidance_block("", "") == ""
    # Byte-identical to the pre-change output when no directives are supplied.
    assert _guidance_block("STANDING").endswith("STANDING")
