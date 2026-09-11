"""Coverage-golden eval for the block-wide digest pipeline (app side).

Offline + deterministic (stub LLM, no DIS/DB/Bedrock) — safe for CI. Asserts:
  * BlockWideGenerator reduce+verify is structurally complete (a row per
    enumerated day, even for failed/thin days) and computes VERIFY correctly;
  * the quality-tier resolver maps tiers to reduce models + token headroom;
  * the digest feature-flag gate honors the master switch + client allowlist;
  * the CDD digest path renders a well-formed artifact and falls back to legacy
    on any DIS failure.

Companion live checks (real dis_db / Bedrock / OpenSearch) live outside CI.
"""
import json
import re
import types

import pytest

from promptops_app.core.models import resolve_tier
from promptops_app.services.block_wide_generator import (
    BlockWideGenerator, EXTENSION_MISSING, _guidance_block,
)
from promptops_app.services.block_wide_service import (
    _acs_registry_table, _cell, _day_table_from_rows, _source_inventory_table,
)


def test_cell_collapses_newlines_and_pipes_never_breaks_row_structure():
    """Regression: a raw newline OR pipe in any table cell breaks that markdown
    row's single-line syntax — a strict table renderer then silently ends the
    table there, dropping every subsequent day (caught live: a 20-day block
    rendered as if it had ~17 because one day's calendar-derived field carried
    an embedded newline no field in this table sanitized before this fix)."""
    assert _cell("a\nb\nc") == "a b c"
    assert _cell("a | b") == "a / b"
    assert _cell("") == "—"
    assert _cell("", default="N/A") == "N/A"
    assert "\n" not in _cell("multi\nline\nvalue with | pipe")


def test_day_table_from_rows_produces_one_line_per_day_even_with_embedded_newlines():
    rows = [
        {"day_number": 1, "topic": "Normal day", "assessment_today": ["Quiz 1"]},
        {"day_number": 2, "topic": "Has a\nnewline", "assessment_today": ["Quiz 2\n\nExtra | text"]},
        {"day_number": 3, "topic": "Day three"},
    ]
    lines = _day_table_from_rows(rows)
    day_rows = [l for l in lines if re.match(r"^\| Day [123] ", l)]
    assert len(day_rows) == 3  # one physical line per day — none swallowed a newline
    # Derived from the header rather than hardcoded: N columns -> N+1 pipes. The
    # previous literal (28, for 27 columns) had to be edited by hand every time a
    # column was added to match the AIM reference, which turns an intentional schema
    # change into a spurious test failure. What this test actually guards is that a
    # row stays ONE line with the SAME cell count as the header.
    from promptops_app.services.block_wide_service import _DAY_TABLE_HEADER
    expected_pipes = len(_DAY_TABLE_HEADER) + 1
    for line in day_rows:
        assert "\n" not in line
        assert line.count("|") == expected_pipes


ENUMERATE_SUMMARY = {
    "block": "Block T", "total_days": 4, "enumerated_days": 4,
    "declared_acs": ["A", "B", "C", "D"],
    "flags": ["THIN_DAY:3 — no substantive source units"],
    "days": [
        {"day_number": 1, "topic": "Intro", "acs_codes": ["A", "B"]},
        {"day_number": 2, "topic": "Methods", "acs_codes": ["C"]},
        {"day_number": 3, "topic": "Thin review", "acs_codes": []},
        {"day_number": 4, "topic": "Assessment", "acs_codes": ["D"]},
    ],
}
DIGESTS = [
    {"day_number": 1, "topic": "Intro", "acs_codes": ["A", "B"], "concept_type": "Conceptual",
     "digest_status": "ok", "source_availability": {"slide": "present"}, "derived_objective": "Understand intro"},
    {"day_number": 2, "topic": "Methods", "acs_codes": ["C"], "concept_type": "Procedural",
     "digest_status": "ok", "source_availability": {"slide": "present"}, "derived_objective": "Apply methods"},
    {"day_number": 3, "topic": "Thin review", "acs_codes": [], "concept_type": "Unknown",
     "digest_status": "ok", "review_flags": ["THIN_DAY — calendar row only"], "source_availability": {}},
    {"day_number": 4, "topic": "Assessment", "acs_codes": ["D"], "concept_type": "Factual",
     "digest_status": "failed", "error": "throttled", "source_availability": {}},
]


def _stub_llm(_model, _system, user):
    """Handles both call shapes BlockWideGenerator makes: the per-day narrative
    batch (a JSON object per day: {"narrative": str, "how_it_is_applied": str},
    preceded by a BLOCK_CONTEXT array the day payload array is NOT the first "["
    in the prompt anymore) and the Worksheet-5 patterns_notes call (a
    {"FACTS": {...}} object) — tried in that order so a "[" inside FACTS (e.g. an
    empty missing_days list) never gets mistaken for the day array."""
    if '"content_arc_summary"' in user:
        return json.dumps({"content_arc_summary": "stub arc summary",
                           "production_readiness": "stub readiness"})
    marker = "DAYS TO FILL IN:\n"
    start = user.index(marker) + len(marker) if marker in user else user.index("[")
    payload = json.loads(user[start:])
    return json.dumps({str(d["day_number"]): {"narrative": f"Cell for day {d['day_number']}",
                                              "how_it_is_applied": f"Applied on day {d['day_number']}"}
                       for d in payload})


def _day_table_rows(res):
    return next(s for s in res.sections if s["key"] == "day_table")["rows"]


def test_reduce_coverage_golden():
    gen = BlockWideGenerator(llm=_stub_llm, batch_size=10)
    res = gen.reduce(ENUMERATE_SUMMARY, DIGESTS, deliverable="cdd", tier="premium")
    cov = res.coverage
    rows = {r["day_number"]: r for r in _day_table_rows(res)}

    # Structural completeness: a row per enumerated day, none missing.
    assert cov["days_in_output"] == 4
    assert cov["missing_days"] == []
    # VERIFY: D is declared but only on the failed day → orphan; day 4 failed; day 3 thin.
    assert cov["covered_acs"] == ["A", "B", "C"]
    assert cov["orphan_acs"] == ["D"]
    assert cov["failed_days"] == [4]
    assert cov["thin_days"] == [3]
    assert cov["complete"] is False
    # Narrative: LLM fills healthy days; failed day degrades to REVIEW NEEDED.
    assert rows[1]["narrative"] == "Cell for day 1"
    assert rows[4]["narrative"].startswith("REVIEW NEEDED")
    # Tier → model + token headroom; the per-day batch is 1 call + 1 patterns_notes call.
    # 'premium' is Opus 5 — measured 3/3 in both regions on the credentials CAS runs
    # on. A cap below the model's real output ceiling silently truncates long
    # sectioned output, which reads as missing worksheet rows rather than an error.
    assert res.reduce_model == "Claude Opus 5 (Bedrock)"
    assert res.max_output_tokens == 64000
    assert res.llm_calls == 2
    # Multi-worksheet shape: 5 sections in a fixed order, even with no overview/
    # inventory/registry data supplied (this test predates Phase 1's aggregates).
    assert [s["key"] for s in res.sections] == [
        "block_overview", "source_file_inventory", "acs_registry", "day_table", "patterns_notes",
    ]
    notes = next(s for s in res.sections if s["key"] == "patterns_notes")["fields"]
    assert notes["content_arc_summary"] == "stub arc summary"
    # learn_while_doing/handbook_edition_conflicts/high_risk_days are CODE
    # conclusions, never delegated to the LLM's prose judgment (a real run once
    # had the model say "no conflicts" despite two distinct handbook editions
    # being in the given facts) — this fixture has no handbook_reference data,
    # so the deterministic string should say exactly "no conflicts", not "stub".
    assert "No edition conflicts detected" in notes["handbook_edition_conflicts"]


def test_fill_narratives_enriches_learn_while_doing_and_hangar_activity_from_llm():
    """Learn-While-Doing and Hangar Activity used to be purely mechanical
    ("Yes"/"No", a raw joined filename list) — flat next to the AIM reference's
    reasoned prose. _fill_narratives should now also return a reason/note for
    each, phrased by the LLM but never contradicting the code-computed fact."""
    def llm(_model, _system, user):
        if '"content_arc_summary"' in user:
            return json.dumps({"content_arc_summary": "x", "production_readiness": "y"})
        marker = "DAYS TO FILL IN:\n"
        payload = json.loads(user[user.index(marker) + len(marker):])
        return json.dumps({str(d["day_number"]): {
            "narrative": "n", "how_it_is_applied": "a",
            "learn_while_doing_reason": f"reason for day {d['day_number']}",
            "hangar_activity_note": f"note for day {d['day_number']}",
        } for d in payload})

    gen = BlockWideGenerator(llm=llm, batch_size=10)
    days = [{"day_number": 1, "topic": "Intro", "acs_codes": ["A"], "projects_today": ["Project 1-1"],
             "hangar_activity_today": ["Wing Inspection.pdf"], "assessment_today": []}]
    rows = [gen._skeleton_row(days[0], {"digest_status": "ok"})]
    gen._fill_narratives(rows, "cdd", "system", "Claude Haiku 4.5 (Bedrock)")
    assert rows[0]["learn_while_doing_reason"] == "reason for day 1"
    assert rows[0]["hangar_activity_note"] == "note for day 1"


def test_fill_narratives_falls_back_to_mechanical_values_when_llm_omits_new_fields():
    """Backward-compatible: an LLM response that only returns the pre-existing
    narrative/how_it_is_applied keys must not blank out the new cells — they
    degrade to the same mechanical values these columns rendered before this
    change (never blank, per this file's structural-completeness discipline)."""
    def llm(_model, _system, user):
        marker = "DAYS TO FILL IN:\n"
        payload = json.loads(user[user.index(marker) + len(marker):])
        return json.dumps({str(d["day_number"]): {"narrative": "n", "how_it_is_applied": "a"}
                           for d in payload})

    gen = BlockWideGenerator(llm=llm, batch_size=10)
    day_with_project = {"day_number": 1, "topic": "Intro", "acs_codes": [], "projects_today": ["Project 1-1"],
                        "hangar_activity_today": [], "assessment_today": []}
    day_without = {"day_number": 2, "topic": "Methods", "acs_codes": [], "projects_today": [],
                  "hangar_activity_today": ["Wing Inspection.pdf"], "assessment_today": []}
    rows = [gen._skeleton_row(day_with_project, {"digest_status": "ok"}),
            gen._skeleton_row(day_without, {"digest_status": "ok"})]
    gen._fill_narratives(rows, "cdd", "system", "Claude Haiku 4.5 (Bedrock)")
    assert rows[0]["learn_while_doing_reason"] == "opens Project 1-1 the same day this content is introduced."
    assert rows[1]["learn_while_doing_reason"] == "no project opens this day."
    assert rows[1]["hangar_activity_note"] == "Wing Inspection.pdf"


def test_day_table_prefixes_learn_while_doing_reason_with_yes_no_and_uses_hangar_note():
    rows = [{
        "day_number": 1, "topic": "Intro", "learn_while_doing": False,
        "learn_while_doing_reason": "no project opens this day (Project 2-1 opens Day 2).",
        "hangar_activity_today": ["Aircraft Location Scout.pdf"],
        "hangar_activity_note": "Locating drawing-referenced components on an aircraft.",
    }]
    lines = _day_table_from_rows(rows)
    day_row = next(l for l in lines if l.startswith("| Day 1 "))
    assert "No — no project opens this day (Project 2-1 opens Day 2)." in day_row
    assert "Locating drawing-referenced components on an aircraft." in day_row
    # The code-computed fact (which files actually exist) must survive
    # regardless of what the LLM's note says — the note is an ADDITION to the
    # file list, never a replacement (flagged by adversarial review: the
    # pre-fix rendering let the note fully hide the real, traceable filename).
    assert "Aircraft Location Scout.pdf" in day_row


def test_day_table_hangar_activity_survives_even_when_note_contradicts_ground_truth():
    """If hangar_activity_note somehow contains a contradicting/negating claim
    (e.g. a model slip returning its own "no hangar activity" fallback phrase
    for a day that DOES have one), the real filename must still render — the
    note is informational, never load-bearing for whether the cell shows a
    file at all."""
    rows = [{
        "day_number": 1, "topic": "Intro", "learn_while_doing": False,
        "hangar_activity_today": ["Aircraft Location Scout.pdf"],
        "hangar_activity_note": "N/A — no hangar activity listed for this day.",
    }]
    lines = _day_table_from_rows(rows)
    day_row = next(l for l in lines if l.startswith("| Day 1 "))
    assert "Aircraft Location Scout.pdf" in day_row


def test_fill_narratives_guards_against_learn_while_doing_reason_contradicting_the_fact():
    """Regression, caught by adversarial review: nothing previously stopped the
    LLM from returning the FALSE-case example phrasing ("no project opens...")
    for a day where learn_while_doing is True, shipping a self-contradicting
    cell ("Yes — no project opens this day..."). The parser must detect and
    override that specific contradiction rather than trust the LLM's text."""
    def llm(_model, _system, user):
        marker = "DAYS TO FILL IN:\n"
        payload = json.loads(user[user.index(marker) + len(marker):])
        return json.dumps({str(d["day_number"]): {
            "narrative": "n", "how_it_is_applied": "a",
            "learn_while_doing_reason": "no project opens this day (a contradicting model slip).",
        } for d in payload})

    gen = BlockWideGenerator(llm=llm, batch_size=10)
    day = {"day_number": 1, "topic": "Intro", "acs_codes": [], "projects_today": ["Project 1-1"],
          "hangar_activity_today": [], "assessment_today": []}
    rows = [gen._skeleton_row(day, {"digest_status": "ok"})]
    gen._fill_narratives(rows, "cdd", "system", "Claude Haiku 4.5 (Bedrock)")
    assert rows[0]["learn_while_doing"] is True
    assert not rows[0]["learn_while_doing_reason"].lower().startswith("no project")
    assert rows[0]["learn_while_doing_reason"] == "opens Project 1-1 the same day this content is introduced."


def test_guidance_block_renders_nothing_for_empty_guidance():
    """No guidance must produce byte-identical prompt text to before this
    feature existed, not an empty-but-present instructional section."""
    assert _guidance_block("") == ""
    assert _guidance_block("   ") == ""
    assert _guidance_block(None) == ""
    block = _guidance_block("Always ground the objective in the block's grading policy.")
    assert "Always ground the objective in the block's grading policy." in block
    assert "never add a field" in block.lower()


def test_fill_narratives_carries_map_guidance_into_prompt_and_omits_when_absent():
    """The prompt-derived guidance (see promptops_app.services.prompt_guidance)
    must actually reach the REDUCE narrative-fill call when supplied, and the
    prompt must be unaffected when it is not."""
    captured = {}

    def llm(_model, _system, user):
        captured["user"] = user
        if '"content_arc_summary"' in user:
            return json.dumps({"content_arc_summary": "x", "production_readiness": "y"})
        marker = "DAYS TO FILL IN:\n"
        payload = json.loads(user[user.index(marker) + len(marker):])
        return json.dumps({str(d["day_number"]): {"narrative": "n", "how_it_is_applied": "a"}
                           for d in payload})

    gen = BlockWideGenerator(llm=llm, batch_size=10)
    day = {"day_number": 1, "topic": "Intro", "acs_codes": [], "projects_today": [],
          "hangar_activity_today": [], "assessment_today": []}
    rows = [gen._skeleton_row(day, {"digest_status": "ok"})]

    gen._fill_narratives(rows, "cdd", "system", "Claude Haiku 4.5 (Bedrock)",
                        map_guidance="Emphasize block-level ACS stakes in every objective.")
    assert "ADDITIONAL GENERATION GUIDANCE" in captured["user"]
    assert "Emphasize block-level ACS stakes in every objective." in captured["user"]
    assert "DAYS TO FILL IN:" in captured["user"]  # fixed contract marker still intact

    gen._fill_narratives(rows, "cdd", "system", "Claude Haiku 4.5 (Bedrock)")
    assert "ADDITIONAL GENERATION GUIDANCE" not in captured["user"]


def test_reduce_threads_map_guidance_through_to_fill_narratives():
    """reduce() is the public entry point _build_and_reduce calls — confirm the
    guidance actually flows all the way from reduce()'s own parameter into the
    narrative-fill prompt, not just when _fill_narratives is called directly."""
    captured = {}

    def llm(_model, _system, user):
        if '"content_arc_summary"' in user:
            return json.dumps({"content_arc_summary": "x", "production_readiness": "y"})
        captured["user"] = user
        marker = "DAYS TO FILL IN:\n"
        payload = json.loads(user[user.index(marker) + len(marker):])
        return json.dumps({str(d["day_number"]): {"narrative": "n", "how_it_is_applied": "a"}
                           for d in payload})

    gen = BlockWideGenerator(llm=llm, batch_size=10)
    gen.reduce(ENUMERATE_SUMMARY, DIGESTS, deliverable="cdd", tier="draft",
              map_guidance="Name the specific AC number whenever one is cited.")
    assert "Name the specific AC number whenever one is cited." in captured["user"]


def test_source_inventory_table_survives_embedded_pipe_and_newline_in_any_field():
    """Every cell in this table must go through _cell(), per _cell's own
    docstring — file_count/status are hardcoded-safe today, but a row
    containing a "|" or newline in ANY field must still not corrupt the row
    (one physical output line per input row, header column count preserved)."""
    rows = [{"document_type": "syllabus", "file_count": 1, "days_applicable": ["All"],
            "status": "EXISTS", "production_action": "Include", "status_notes": "a | b\nc"}]
    lines = _source_inventory_table(rows)
    data_rows = [l for l in lines if l.startswith("| syllabus")]
    assert len(data_rows) == 1
    assert "\n" not in data_rows[0]
    assert data_rows[0].count("|") == 7  # 6 columns -> 7 pipe chars


def test_acs_registry_table_survives_embedded_pipe_and_newline_in_task_description():
    """Real extracted ACS-1 text (task_description) can in principle contain a
    stray "|" from OCR/table-flattening artifacts — must not break the row."""
    rows = [{"acs_code": "AM.I.B.K1", "acs_type": "K — Knowledge",
            "task_description": "Drawings | blueprints\nand schematics.",
            "days_active": [1, 2], "high_miss": "NO AKTR DATA", "quick_check_priority": "RECALL"}]
    lines = _acs_registry_table(rows)
    data_rows = [l for l in lines if l.startswith("| AM.I.B.K1")]
    assert len(data_rows) == 1
    assert "\n" not in data_rows[0]
    assert data_rows[0].count("|") == 7  # 6 columns -> 7 pipe chars


def test_fill_narratives_appends_block_level_framing_and_cross_day_note():
    """objective_block_framing/cross_day_misconception_note must APPEND to the
    day's own derived_objective/misconceptions, never replace them — the per-day
    digest's own content is the source of truth; the reduce stage only adds
    block-level/cross-day context it alone has access to (block_overview +
    BLOCK_CONTEXT across all days)."""
    def llm(_model, _system, user):
        assert '"acs_subjects_covered": [' in user  # block_facts actually reached the prompt
        marker = "DAYS TO FILL IN:\n"
        payload = json.loads(user[user.index(marker) + len(marker):])
        return json.dumps({str(d["day_number"]): {
            "narrative": "n", "how_it_is_applied": "a",
            "objective_block_framing": "to a 70% or higher standard, per the block's grading policy.",
            "cross_day_misconception_note": "Connects to Day 1's concept, reinforced here.",
        } for d in payload})

    gen = BlockWideGenerator(llm=llm, batch_size=10)
    day = {"day_number": 1, "topic": "Final Exam", "acs_codes": [], "projects_today": [],
          "hangar_activity_today": [], "assessment_today": []}
    digest = {"digest_status": "ok", "derived_objective": "Assess mastery of Block 2.",
              "misconceptions": ["Original misconception."]}
    rows = [gen._skeleton_row(day, digest)]
    block_overview = {"acs_subjects_covered": ["B", "E", "G"], "grading_policy": "70% to pass"}
    gen._fill_narratives(rows, "cdd", "system", "Claude Haiku 4.5 (Bedrock)", block_overview)

    assert rows[0]["derived_objective"] == (
        "Assess mastery of Block 2. to a 70% or higher standard, per the block's grading policy."
    )
    assert rows[0]["misconceptions"] == [
        "Original misconception.", "Connects to Day 1's concept, reinforced here.",
    ]


def test_fill_narratives_never_appends_cross_day_note_to_an_empty_misconceptions_list():
    """A day with no misconceptions (e.g. an assessment day) must stay empty —
    the LLM must not be able to pad an empty list just because it returned a
    cross_day_misconception_note; code only appends when the day already has
    at least one real entry."""
    def llm(_model, _system, user):
        marker = "DAYS TO FILL IN:\n"
        payload = json.loads(user[user.index(marker) + len(marker):])
        return json.dumps({str(d["day_number"]): {
            "narrative": "n", "how_it_is_applied": "a",
            "cross_day_misconception_note": "Should not be added.",
        } for d in payload})

    gen = BlockWideGenerator(llm=llm, batch_size=10)
    day = {"day_number": 20, "topic": "Final Exam", "acs_codes": [], "projects_today": [],
          "hangar_activity_today": [], "assessment_today": ["Final Exam"]}
    digest = {"digest_status": "ok", "derived_objective": "Assess mastery.", "misconceptions": []}
    rows = [gen._skeleton_row(day, digest)]
    gen._fill_narratives(rows, "cdd", "system", "Claude Haiku 4.5 (Bedrock)")
    assert rows[0]["misconceptions"] == []


def test_patterns_notes_edition_detection_is_not_hardcoded_to_one_handbook_series():
    """Regression: the edition-conflict detector's regex used to hardcode
    "FAA-H-8083-..." — any block citing a different handbook series fell back to
    the RAW citation text as the "edition" key, so two different page ranges of
    the SAME handbook (which real citations often are) would be miscounted as
    two different editions. Two days citing the same non-8083 handbook, at
    different page ranges, must collapse into one edition with no conflict."""
    gen = BlockWideGenerator(llm=_stub_llm, batch_size=10)
    rows = [
        {"day_number": 1, "handbook_reference": "FAA-H-8091-1A Ch. 2 pgs 2-1 to 2-9", "projects_today": []},
        {"day_number": 2, "handbook_reference": "FAA-H-8091-1A Ch. 2 pgs 2-9 to 2-15", "projects_today": []},
    ]
    coverage = types.SimpleNamespace(thin_days=[], failed_days=[], total_days=2,
                                     enumerated_days=2, missing_days=[], orphan_acs=[],
                                     enumerate_flags=[])
    fields, _ = gen._patterns_notes(rows, coverage, "Claude Haiku 4.5 (Bedrock)")
    assert "No edition conflicts detected" in fields["handbook_edition_conflicts"]
    assert "FAA-H-8091-1A" in fields["handbook_edition_conflicts"]


def test_patterns_notes_carries_map_guidance_into_prompt_and_omits_when_absent():
    """Regression, caught by independent adversarial review: reduce() threaded
    map_guidance into _fill_narratives but not into _patterns_notes, even though
    both are REDUCE-stage LLM calls filling the same generated document (day-table
    narratives vs. Worksheet 5 synthesis prose) — an inconsistency a reviewer
    would flag as accidental scope, not a deliberate cut."""
    captured = {}

    def llm(_model, _system, user):
        captured["user"] = user
        return json.dumps({"content_arc_summary": "x", "production_readiness": "y"})

    gen = BlockWideGenerator(llm=llm, batch_size=10)
    coverage = types.SimpleNamespace(thin_days=[], failed_days=[], total_days=1,
                                     enumerated_days=1, missing_days=[], orphan_acs=[],
                                     enumerate_flags=[])

    gen._patterns_notes([], coverage, "Claude Haiku 4.5 (Bedrock)",
                        map_guidance="Always cite the specific AC number.")
    assert "ADDITIONAL GENERATION GUIDANCE" in captured["user"]
    assert "Always cite the specific AC number." in captured["user"]
    assert '"content_arc_summary"' in captured["user"]  # fixed contract marker still intact

    gen._patterns_notes([], coverage, "Claude Haiku 4.5 (Bedrock)")
    assert "ADDITIONAL GENERATION GUIDANCE" not in captured["user"]


def test_reduce_threads_map_guidance_into_patterns_notes_too():
    """End-to-end: reduce() is the public entry point _build_and_reduce calls —
    confirm the guidance reaches _patterns_notes's prompt via reduce(), not just
    when _patterns_notes is called directly."""
    captured = {}

    def llm(_model, _system, user):
        if '"content_arc_summary"' in user:
            captured["notes_user"] = user
            return json.dumps({"content_arc_summary": "x", "production_readiness": "y"})
        marker = "DAYS TO FILL IN:\n"
        payload = json.loads(user[user.index(marker) + len(marker):])
        return json.dumps({str(d["day_number"]): {"narrative": "n", "how_it_is_applied": "a"}
                           for d in payload})

    gen = BlockWideGenerator(llm=llm, batch_size=10)
    gen.reduce(ENUMERATE_SUMMARY, DIGESTS, deliverable="cdd", tier="draft",
              map_guidance="Name the specific AC number whenever one is cited.")
    assert "Name the specific AC number whenever one is cited." in captured["notes_user"]


def test_resolve_tier():
    # Tiers differentiate: each target measured 3/3 in both regions on the credentials
    # the REDUCE path actually uses (root .env principal, not DIS's).
    assert resolve_tier("draft").reduce_model == "Claude Haiku 4.5 (Bedrock)"
    assert resolve_tier("standard").reduce_model == "Claude Sonnet 5 (Bedrock)"
    assert resolve_tier("premium").reduce_model == "Claude Opus 5 (Bedrock)"
    # Blank/unknown falls back to the default tier, never raises.
    assert resolve_tier(None).tier == "standard"
    assert resolve_tier("bogus").tier == "standard"


def test_digest_pipeline_flag_gate():
    from app.core.config import settings
    orig_enabled, orig_clients = settings.digest_pipeline_enabled, settings.digest_pipeline_clients
    try:
        settings.digest_pipeline_enabled = False
        assert settings.digest_pipeline_on_for("aim") is False
        settings.digest_pipeline_enabled = True
        settings.digest_pipeline_clients = "aim"
        assert settings.digest_pipeline_on_for("aim") is True
        assert settings.digest_pipeline_on_for("cengage") is False
        # Clearing the allowlist fails CLOSED (no clients), not open (every
        # client) -- an empty config is not a wildcard. See S2 in
        # AIM_PIPELINE_SHORTCOMINGS.txt.
        settings.digest_pipeline_clients = ""
        assert settings.digest_pipeline_on_for("cengage") is False
        assert settings.digest_pipeline_on_for("aim") is False
    finally:
        settings.digest_pipeline_enabled, settings.digest_pipeline_clients = orig_enabled, orig_clients


def test_run_block_wide_sync_never_engages_for_a_day_scoped_request(monkeypatch):
    """A day-scoped single-item request (day_number set) must never fall into
    the whole-block reduce pipeline — that pipeline ignores selected_module/day
    entirely and would produce the wrong document. The guard must short-circuit
    BEFORE any DB access.

    Regression on the test itself, caught by adversarial review: a prior
    version of this test passed `db=None` and claimed that alone proved the
    guard fired first, reasoning that resolve_course_dis_client "would raise on
    a None db if the guard didn't return early." False — resolve_course_dis_client
    (app/core/dis_access.py) wraps its whole body in try/except Exception and
    returns "" on any failure, so the OLD test would have kept passing even
    with the day_number guard deleted entirely (it would just take a different,
    equally None-returning path through the digest_pipeline_on_for check).
    A monkeypatched spy that raises if actually called is the only way to prove
    the short-circuit happens BEFORE resolve_course_dis_client, not merely that
    the end result happens to also be None."""
    import app.core.dis_access as dis_access
    from promptops_app.services.block_wide_service import run_block_wide_sync

    def _fail_if_called(*a, **k):
        raise AssertionError("resolve_course_dis_client must not be called — "
                              "the day_number guard should have returned first")

    monkeypatch.setattr(dis_access, "resolve_course_dis_client", _fail_if_called)

    req = types.SimpleNamespace(day_number=3, block="Block 2", course_id=1, project_id=1)
    assert run_block_wide_sync(db=None, deliverable="blueprint", request_body=req, current_user=None) is None


def test_run_block_wide_sync_never_resolves_prompt_guidance_when_pipeline_disabled(monkeypatch):
    """Guidance resolution (a DB lookup + an LLM call) must never be attempted
    for a client the digest pipeline isn't enabled for — same "prove the guard
    fires first" discipline as the day-scoped guard above, applied to the new
    step this feature adds."""
    import promptops_app.services.prompt_guidance as prompt_guidance
    from app.core.config import settings
    from app.core import dis_access
    from promptops_app.services.block_wide_service import run_block_wide_sync

    def _fail_if_called(*a, **k):
        raise AssertionError("resolve_prompt_guidance must not be called — "
                              "the digest_pipeline_on_for gate should have returned first")

    monkeypatch.setattr(prompt_guidance, "resolve_prompt_guidance", _fail_if_called)
    monkeypatch.setattr(dis_access, "resolve_course_dis_client", lambda *a, **k: "some_client")
    orig_enabled = settings.digest_pipeline_enabled
    try:
        settings.digest_pipeline_enabled = False
        req = types.SimpleNamespace(block="Block 2", course_id=1, project_id=1)
        assert run_block_wide_sync(db=None, deliverable="cdd", request_body=req, current_user=None) is None
    finally:
        settings.digest_pipeline_enabled = orig_enabled


def test_run_block_wide_sync_resolves_and_threads_prompt_guidance(monkeypatch):
    """The guidance resolved once per request must reach the deliverable-
    specific generator — proving the wiring, not just that resolution ran.

    Also covers the capability report (prompt_capability), resolved beside the
    guidance and threaded the same way: it is reporting-only, so a wiring break
    would surface as a silently missing section rather than as a failure."""
    import promptops_app.services.prompt_guidance as prompt_guidance
    import promptops_app.services.prompt_capability as prompt_capability
    from app.core.config import settings
    from app.core import dis_access
    from promptops_app.services import block_wide_service as w

    monkeypatch.setattr(dis_access, "resolve_course_dis_client", lambda *a, **k: "aim")
    monkeypatch.setattr(prompt_guidance, "resolve_prompt_guidance",
                        lambda db, req, deliverable, user: f"guidance-for-{deliverable}")
    sentinel = prompt_capability.CapabilityReport(assessed=True, prompt_chars=7)
    monkeypatch.setattr(prompt_capability, "assess_selected_prompt",
                        lambda db, req, deliverable: sentinel)

    received = {}

    def fake_generate_cdd_via_digests(db, req, user, dcid, guidance="", *, capability=None):
        received["cdd"] = guidance
        received["capability"] = capability
        return {"coverage": {}, "model_used": "m", "prompt_provenance": {"quality_tier": "standard"}}

    monkeypatch.setattr(w, "generate_cdd_via_digests", fake_generate_cdd_via_digests)
    monkeypatch.setattr(w, "persist_cdd_and_respond", lambda db, req, user, **kw: "ok")

    orig_enabled, orig_clients = settings.digest_pipeline_enabled, settings.digest_pipeline_clients
    try:
        settings.digest_pipeline_enabled = True
        # Matches resolve_course_dis_client's mock above ("aim") -- an empty
        # allowlist no longer means "every client" (S2), so the client this
        # request actually resolves to must be named explicitly.
        settings.digest_pipeline_clients = "aim"
        req = types.SimpleNamespace(block="Block 2", course_id=1, project_id=1)
        w.run_block_wide_sync(db=None, deliverable="cdd", request_body=req, current_user=None)
    finally:
        settings.digest_pipeline_enabled, settings.digest_pipeline_clients = orig_enabled, orig_clients
    assert received["cdd"] == "guidance-for-cdd"
    assert received["capability"] is sentinel


def test_cdd_digest_path_render_and_fallback(monkeypatch):
    import promptops_app.services.llm_service as llm_service
    from app.core import dis_client as dc
    from app.api.v1.routers import cdd as cdd_router

    def fake_gwm(model_choice, system, user, usage_ctx=None, max_tokens=None):
        if '"content_arc_summary"' in user:
            text = json.dumps({"content_arc_summary": "stub arc", "production_readiness": "stub readiness"})
        else:
            marker = "DAYS TO FILL IN:\n"
            start = user.index(marker) + len(marker) if marker in user else user.index("[")
            payload = json.loads(user[start:])
            text = json.dumps({str(d["day_number"]): {"narrative": f"N{d['day_number']}",
                                                       "how_it_is_applied": f"App{d['day_number']}"}
                               for d in payload})
        return types.SimpleNamespace(text=text, model=model_choice, status="ok",
                                     prompt_tokens=1, completion_tokens=1, error_type=None)
    monkeypatch.setattr(llm_service, "generate_with_metadata", fake_gwm)

    bundle = {"block": "Block 2", "client_id": "aim",
              "enumerate": ENUMERATE_SUMMARY, "digests": DIGESTS}
    monkeypatch.setattr(dc.dis_client, "build_digests_sync",
                        lambda block, current_user=None, client_id="", map_guidance="": {"built": 4, "cached": 0, "failed": 1, "map_calls": 1})
    monkeypatch.setattr(dc.dis_client, "get_digests_bundle_sync",
                        lambda block, current_user=None, client_id="": bundle)

    req = types.SimpleNamespace(block="Block 2", quality_tier="standard", course_title="T",
                                course_id=1, project_id=1, document_title=None)
    user = types.SimpleNamespace(username="tester")

    gen = cdd_router._generate_cdd_via_digests(db=None, request_body=req, current_user=user, dis_client_id="aim")
    assert gen is not None
    assert gen["prompt_provenance"]["prompt_source"] == "digest_pipeline"
    assert gen["model_used"] == "Claude Sonnet 5 (Bedrock)"   # standard tier
    assert gen["coverage"]["orphan_acs"] == ["D"]
    assert "| Day 1 |" in gen["raw_output"] and "| Day 4 |" in gen["raw_output"]
    assert "REVIEW NEEDED" in gen["raw_output"]
    assert isinstance(gen["sections"], dict) and gen["sections"]

    # Any DIS failure → None so the caller falls back to the legacy path.
    def boom(*a, **k):
        raise RuntimeError("DIS down")
    monkeypatch.setattr(dc.dis_client, "build_digests_sync", boom)
    assert cdd_router._generate_cdd_via_digests(db=None, request_body=req, current_user=user, dis_client_id="aim") is None


def test_generate_cdd_via_digests_threads_map_guidance_to_dis_and_provenance(monkeypatch):
    """map_guidance must reach dis_client.build_digests_sync's MAP call AND be
    visible afterward in prompt_provenance — the latter is the traceability
    this feature depends on (a reviewer must be able to see what the pipeline
    actually derived from the prompt, not trust it blindly)."""
    import promptops_app.services.llm_service as llm_service
    from app.core import dis_client as dc
    from app.api.v1.routers import cdd as cdd_router

    def fake_gwm(model_choice, system, user, usage_ctx=None, max_tokens=None):
        if '"content_arc_summary"' in user:
            text = json.dumps({"content_arc_summary": "x", "production_readiness": "y"})
        else:
            marker = "DAYS TO FILL IN:\n"
            payload = json.loads(user[user.index(marker) + len(marker):])
            text = json.dumps({str(d["day_number"]): {"narrative": "n", "how_it_is_applied": "a"}
                               for d in payload})
        return types.SimpleNamespace(text=text, model=model_choice, status="ok",
                                     prompt_tokens=1, completion_tokens=1, error_type=None)
    monkeypatch.setattr(llm_service, "generate_with_metadata", fake_gwm)

    received = {}
    bundle = {"block": "Block 2", "client_id": "aim", "enumerate": ENUMERATE_SUMMARY, "digests": DIGESTS}
    monkeypatch.setattr(dc.dis_client, "build_digests_sync",
                        lambda block, current_user=None, client_id="", map_guidance="":
                            received.setdefault("map_guidance", map_guidance) or
                            {"built": 4, "cached": 0, "failed": 1, "map_calls": 1})
    monkeypatch.setattr(dc.dis_client, "get_digests_bundle_sync",
                        lambda block, current_user=None, client_id="": bundle)

    req = types.SimpleNamespace(block="Block 2", quality_tier="standard", course_title="T",
                                course_id=1, project_id=1, document_title=None)
    user = types.SimpleNamespace(username="tester")

    gen = cdd_router._generate_cdd_via_digests(db=None, request_body=req, current_user=user,
                                               dis_client_id="aim", map_guidance="Cite the specific AC number.")
    assert received["map_guidance"] == "Cite the specific AC number."
    assert gen["prompt_provenance"]["map_guidance"] == "Cite the specific AC number."
    assert gen["prompt_provenance"]["map_guidance_applied"] is True


def test_blueprint_digest_path_render(monkeypatch):
    import promptops_app.services.llm_service as llm_service
    from app.core import dis_client as dc
    from promptops_app.services import block_wide_service

    def fake_gwm(model_choice, system, user, usage_ctx=None, max_tokens=None):
        if '"content_arc_summary"' in user:
            text = json.dumps({"content_arc_summary": "stub arc", "production_readiness": "stub readiness"})
        else:
            marker = "DAYS TO FILL IN:\n"
            start = user.index(marker) + len(marker) if marker in user else user.index("[")
            payload = json.loads(user[start:])
            text = json.dumps({str(d["day_number"]): {"narrative": f"BP{d['day_number']}",
                                                       "how_it_is_applied": f"App{d['day_number']}"}
                               for d in payload})
        return types.SimpleNamespace(
            text=text, model=model_choice, status="ok",
            prompt_tokens=1, completion_tokens=1, error_type=None)
    monkeypatch.setattr(llm_service, "generate_with_metadata", fake_gwm)

    bundle = {"block": "Block 2", "client_id": "aim", "enumerate": ENUMERATE_SUMMARY, "digests": DIGESTS}
    monkeypatch.setattr(dc.dis_client, "build_digests_sync",
                        lambda block, current_user=None, client_id="", map_guidance="": {"built": 4, "cached": 0, "failed": 1, "map_calls": 1})
    monkeypatch.setattr(dc.dis_client, "get_digests_bundle_sync",
                        lambda block, current_user=None, client_id="": bundle)

    req = types.SimpleNamespace(block="Block 2", quality_tier="draft")
    gen = block_wide_service.generate_blueprint_via_digests(db=None, request_body=req,
                                                            current_user=types.SimpleNamespace(username="t"),
                                                            dis_client_id="aim")
    assert gen is not None
    assert gen["prompt_provenance"]["deliverable"] == "blueprint"
    assert gen["model_used"] == "Claude Haiku 4.5 (Bedrock)"  # draft tier (see resolve_tier)
    assert "Block Blueprint" in gen["raw_output"] and "WORKSHEET 4: DAY-BY-DAY MAP" in gen["raw_output"]
    assert gen["coverage"]["orphan_acs"] == ["D"]
    # Multi-worksheet shape carries through the real generate_blueprint_via_digests
    # call path, not just BlockWideGenerator.reduce() directly.
    from promptops_app.parsers.cdd_parser import is_dlu_cdd
    assert is_dlu_cdd(gen["raw_output"])


def test_reconstruct_request_forwards_prompt_id():
    """Regression coverage gap flagged by independent adversarial review: no test
    spanned the whole prompt_id chain (BlockWideGenerateRequest -> job params ->
    _reconstruct_request -> resolve_prompt_guidance). Pins the middle link: a
    concrete prompt_id in the job's stored params reaches the reconstructed
    request object resolve_prompt_guidance reads via getattr."""
    from promptops_app.jobs.block_wide_jobs import _reconstruct_request

    req = _reconstruct_request({"deliverable": "cdd", "block": "Block 2",
                                "course_id": 1, "project_id": 1, "prompt_id": 77})
    assert req.prompt_id == 77

    req_omitted = _reconstruct_request({"deliverable": "cdd", "block": "Block 2",
                                        "course_id": 1, "project_id": 1})
    assert req_omitted.prompt_id is None


def test_block_wide_worker_orchestration(monkeypatch):
    """Worker routes by deliverable, persists, and marks the job completed with the
    entity id — or failed when the pipeline returns nothing. Fully mocked (no DB)."""
    from promptops_app.jobs import block_wide_jobs as w

    calls = {}

    class FakeQuery:
        def __init__(self, job): self._job = job
        def filter(self, *a, **k): return self
        def first(self): return self._job

    class FakeDB:
        def __init__(self, job): self._job = job
        def query(self, *a, **k): return FakeQuery(self._job)
        def close(self): calls["closed"] = True

    def make_job(deliverable):
        return types.SimpleNamespace(
            id="j1", status="queued",
            request_json=json.dumps({"deliverable": deliverable, "block": "Block 2",
                                     "course_id": 1, "project_id": 1, "dis_client_id": "aim",
                                     "user_name": "t", "quality_tier": "standard"}),
            result_json=None)

    monkeypatch.setattr(w, "set_running", lambda db, job, *a: None)
    monkeypatch.setattr(w, "set_completed", lambda db, job, eid: calls.__setitem__("completed", eid))
    monkeypatch.setattr(w, "set_failed", lambda db, job, msg: calls.__setitem__("failed", msg))
    # Not the focus of this test (job routing/persistence) — stub explicitly
    # rather than let the real prompt-resolution/digestion path run unmocked
    # against a FakeDB (it would degrade to "" anyway on the AttributeError
    # that follows, but that's an incidental side effect of FakeDB's shape,
    # not something this test should depend on).
    monkeypatch.setattr("promptops_app.services.prompt_guidance.resolve_prompt_guidance",
                        lambda *a, **k: "")
    monkeypatch.setattr(w.block_wide_service, "generate_cdd_via_digests",
                        lambda db, req, user, dcid, guidance="", *, capability=None:
                            {"coverage": {}, "model_used": "m"})
    monkeypatch.setattr(w.block_wide_service, "persist_cdd_and_respond",
                        lambda db, req, user, **kw: types.SimpleNamespace(cdd_id=42))
    monkeypatch.setattr(w.block_wide_service, "generate_blueprint_via_digests",
                        lambda db, req, user, dcid, guidance="", *, capability=None:
                            {"coverage": {}, "model_used": "m"})
    monkeypatch.setattr(w.block_wide_service, "persist_blueprint_and_respond",
                        lambda db, req, user, **kw: types.SimpleNamespace(blueprint_id=99))

    # CDD path → completed with cdd_id.
    monkeypatch.setattr(w, "SessionLocal", lambda: FakeDB(make_job("cdd")))
    calls.clear()
    w.run_block_wide_job("j1")
    assert calls.get("completed") == 42 and calls.get("closed") is True

    # Blueprint path → completed with blueprint_id.
    monkeypatch.setattr(w, "SessionLocal", lambda: FakeDB(make_job("blueprint")))
    calls.clear()
    w.run_block_wide_job("j1")
    assert calls.get("completed") == 99

    # Pipeline returns None → job failed, not crashed.
    monkeypatch.setattr(w.block_wide_service, "generate_cdd_via_digests",
                        lambda db, req, user, dcid, guidance="", *, capability=None: None)
    monkeypatch.setattr(w, "SessionLocal", lambda: FakeDB(make_job("cdd")))
    calls.clear()
    w.run_block_wide_job("j1")
    assert "failed" in calls and "completed" not in calls


# --------------------------------------------------------------------------- #
# §7 day-scoped retrieval — structured render + gate + graceful fallback.
# --------------------------------------------------------------------------- #
DAY_BUNDLE = {
    "block": "Block 2", "day_number": 3, "topic": "Corrosion control",
    "acs_codes": ["3a", "3b"],
    "digest": {"derived_objective": "Identify corrosion types"},
    "units": [
        {"content_unit_id": "s1", "unit_type": "slide", "title": "Slide 1",
         "text": "corrosion slide body", "text_withheld": False},
        {"content_unit_id": "k1", "unit_type": "answer_key_item", "title": "Key 1",
         "text": None, "text_withheld": True},
    ],
    "supplement": [
        {"content_unit_id": "p9", "unit_type": "page", "title": "Handbook p9", "text": "related page"},
    ],
    "flags": [],
}


def test_render_day_context():
    from app.api.v1.routers import generations as g
    txt = g._render_day_context(DAY_BUNDLE, "COURSE GENERATION CONTEXT")
    assert "Block 2 / Day 3" in txt
    assert "ACS covered: 3a, 3b" in txt
    assert "Identify corrosion types" in txt
    # Complete + supplement present; restricted answer-key body never leaks.
    assert "corrosion slide body" in txt
    assert "related page" in txt
    assert "restricted — title only" in txt
    assert "Key 1" in txt  # title shown


def test_render_day_context_survives_explicit_units_none():
    """Regression, caught by adversarial review: DIS can return an explicit
    "units": null (not just an absent key) alongside a non-empty digest —
    _render_day_context used `.get("units", [])`, whose default only covers
    the MISSING-key case, so an explicit null iterated as None -> TypeError,
    caught by the caller and discarding the whole day context (including a
    perfectly good digest) over what should have been a harmless empty list."""
    from app.api.v1.routers import generations as g

    bundle = {**DAY_BUNDLE, "units": None}
    txt = g._render_day_context(bundle, "COURSE GENERATION CONTEXT")
    assert "Identify corrosion types" in txt  # digest still renders
    assert "related page" in txt  # supplement still renders


def test_dis_day_context_block_fallback(monkeypatch):
    from app.api.v1.routers import generations as g

    # Success → structured render + flattened units (day + supplement).
    monkeypatch.setattr(g.dis_client, "get_day_context_sync",
                        lambda block, day, audience="instructor", current_user=None, client_id="": DAY_BUNDLE)
    ctx, units = g._dis_day_context_block("Block 2", 3, None, "COURSE GENERATION CONTEXT", client_id="aim")
    assert "Block 2 / Day 3" in ctx
    assert {u["content_unit_id"] for u in units} == {"s1", "k1", "p9"}

    # Empty bundle → ('', []) so the caller falls back to legacy retrieval.
    monkeypatch.setattr(g.dis_client, "get_day_context_sync",
                        lambda *a, **k: {"units": [], "digest": None, "supplement": [], "flags": []})
    assert g._dis_day_context_block("Block 2", 3, None, "L", client_id="aim") == ("", [])

    # DIS failure → ('', []), never raises.
    def boom(*a, **k):
        raise RuntimeError("DIS down")
    monkeypatch.setattr(g.dis_client, "get_day_context_sync", boom)
    assert g._dis_day_context_block("Block 2", 3, None, "L", client_id="aim") == ("", [])


# --------------------------------------------------------------------------- #
# Worksheet 5's consolidated missing-source summary (a CODE conclusion)
# --------------------------------------------------------------------------- #
def _patterns_gen():
    gen = BlockWideGenerator(llm=lambda *a, **k: json.dumps(
        {"content_arc_summary": "x", "production_readiness": "y"}), batch_size=10)
    return gen


def _cov(**kw):
    base = dict(thin_days=[], failed_days=[], total_days=3, enumerated_days=3,
                missing_days=[], orphan_acs=[], enumerate_flags=[])
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_missing_source_summary_groups_days_under_one_reason():
    """The same absent artefact usually spans a run of days; one line per day is the
    form in which a reviewer stops reading them."""
    rows = [{"day_number": d, "projects_today": [], "handbook_reference": "",
             "review_flags": ["MISSING_SOURCE — instructor guide"]} for d in (5, 6, 7)]
    fields, _ = _patterns_gen()._patterns_notes(rows, _cov(), "Claude Haiku 4.5 (Bedrock)")
    assert fields["missing_source_summary"] == "instructor guide (days 5, 6, 7)"


def test_missing_source_summary_states_the_negative_rather_than_going_blank():
    fields, _ = _patterns_gen()._patterns_notes(
        [{"day_number": 1, "projects_today": [], "handbook_reference": "", "review_flags": []}],
        _cov(), "Claude Haiku 4.5 (Bedrock)")
    assert fields["missing_source_summary"] == "No MISSING_SOURCE flags raised."


def test_missing_source_summary_includes_block_level_gaps_from_worksheet_1():
    """AIM's spec says "anywhere in worksheets 1-4". Worksheet 1's own gaps reach
    CoverageReport as enumerate flags, not as any day's review_flags."""
    fields, _ = _patterns_gen()._patterns_notes(
        [{"day_number": 1, "projects_today": [], "handbook_reference": "", "review_flags": []}],
        _cov(enumerate_flags=["SYLLABUS_NOT_READ — no structure-store cursor"]),
        "Claude Haiku 4.5 (Bedrock)")
    assert "SYLLABUS_NOT_READ" in fields["missing_source_summary"]
    assert "(block level)" in fields["missing_source_summary"]


def test_missing_source_summary_ignores_flags_that_are_not_missing_source():
    fields, _ = _patterns_gen()._patterns_notes(
        [{"day_number": 1, "projects_today": [], "handbook_reference": "",
          "review_flags": ["THIN_DAY — no substantive source", "CONFLICTING_SOURCES — x"]}],
        _cov(), "Claude Haiku 4.5 (Bedrock)")
    assert fields["missing_source_summary"] == "No MISSING_SOURCE flags raised."


def test_missing_source_summary_survives_a_coverage_object_without_enumerate_flags():
    """_patterns_notes is best-effort and its caller omits worksheet 5 when it raises —
    a partial coverage object must not cost the whole worksheet."""
    cov = types.SimpleNamespace(thin_days=[], failed_days=[], total_days=1,
                                enumerated_days=1, missing_days=[], orphan_acs=[])
    fields, _ = _patterns_gen()._patterns_notes(
        [{"day_number": 1, "projects_today": [], "handbook_reference": "",
          "review_flags": ["MISSING_SOURCE — quiz"]}], cov, "Claude Haiku 4.5 (Bedrock)")
    assert fields["missing_source_summary"] == "quiz (day 1)"


def test_missing_source_summary_reaches_the_reduce_prompt_facts():
    """It is also an input to production_readiness, which AIM requires to name
    block-level risk such as widespread MISSING_SOURCE."""
    captured = {}

    def llm(model, system, user, **kw):
        captured["user"] = user
        return json.dumps({"content_arc_summary": "x", "production_readiness": "y"})

    gen = BlockWideGenerator(llm=llm, batch_size=10)
    gen._patterns_notes([{"day_number": 2, "projects_today": [], "handbook_reference": "",
                          "review_flags": ["MISSING_SOURCE — quiz"]}],
                        _cov(), "Claude Haiku 4.5 (Bedrock)")
    assert "missing_source_summary" in captured["user"]
    assert "quiz (day 2)" in captured["user"]


# --------------------------------------------------------------------------- #
# Prompt-declared additional day columns — fill stage
# --------------------------------------------------------------------------- #
_EXT_COLS = [{"label": "Instructional Model Stage", "description": "which stage this day is"},
             {"label": "Pilot Candidate", "description": "whether the SME recommends a pilot"}]


def _ext_rows(n=2):
    return [{"day_number": d, "topic": f"T{d}", "acs_codes": [], "concept_type": "Conceptual",
             "concept_scope": "", "derived_objective": "o", "misconceptions": [],
             "projects_today": [], "hangar_activity_today": [], "digest_status": "ok"}
            for d in range(1, n + 1)]


def test_declaring_nothing_costs_no_call_and_touches_no_row():
    """Byte-identical behaviour for the 17 live prompts, none of which declares a
    column — the same discipline _guidance_block follows."""
    called = []
    gen = BlockWideGenerator(llm=lambda *a, **k: called.append(1) or "{}", batch_size=10)
    rows = _ext_rows()
    assert gen._fill_extensions(rows, [], "Claude Haiku 4.5 (Bedrock)") == 0
    assert called == []
    assert all("extensions" not in r for r in rows)


def test_declared_columns_are_filled_from_one_batched_call():
    gen = BlockWideGenerator(llm=lambda *a, **k: json.dumps(
        {"1": {"Instructional Model Stage": "Interpret", "Pilot Candidate": "No"},
         "2": {"Instructional Model Stage": "Perform", "Pilot Candidate": "Yes"}}), batch_size=10)
    rows = _ext_rows()
    assert gen._fill_extensions(rows, _EXT_COLS, "Claude Haiku 4.5 (Bedrock)") == 1
    assert rows[0]["extensions"]["Instructional Model Stage"] == "Interpret"
    assert rows[1]["extensions"]["Pilot Candidate"] == "Yes"


def test_a_key_the_model_omitted_renders_a_review_marker_not_a_blank():
    """A reviewer cannot tell an empty cell that was answered "nothing" from one the
    model never returned, and only the second is a defect worth chasing."""
    gen = BlockWideGenerator(llm=lambda *a, **k: json.dumps(
        {"1": {"Instructional Model Stage": "Interpret"}}), batch_size=10)
    rows = _ext_rows(1)
    gen._fill_extensions(rows, _EXT_COLS, "Claude Haiku 4.5 (Bedrock)")
    assert rows[0]["extensions"]["Pilot Candidate"] == EXTENSION_MISSING


def test_a_failed_extension_call_never_costs_the_run_its_other_columns():
    """These columns are additive; the 31 already built and paid for must survive."""
    def boom(*a, **k):
        raise RuntimeError("provider down")
    gen = BlockWideGenerator(llm=boom, batch_size=10)
    rows = _ext_rows()
    assert gen._fill_extensions(rows, _EXT_COLS, "Claude Haiku 4.5 (Bedrock)") == 1
    assert all(v == EXTENSION_MISSING
               for r in rows for v in r["extensions"].values())


def test_the_fill_prompt_shows_each_column_with_its_definition():
    captured = {}

    def llm(model, system, user, **kw):
        captured["user"] = user
        return "{}"

    BlockWideGenerator(llm=llm, batch_size=10)._fill_extensions(
        _ext_rows(1), _EXT_COLS, "Claude Haiku 4.5 (Bedrock)")
    assert "Instructional Model Stage: which stage this day is" in captured["user"]
    assert "DAYS TO FILL IN" in captured["user"]


def test_extension_prompt_resolution_is_recorded_in_provenance():
    gen = BlockWideGenerator(llm=lambda *a, **k: "{}", batch_size=10)
    gen._fill_extensions(_ext_rows(1), _EXT_COLS, "Claude Haiku 4.5 (Bedrock)")
    assert "extension_columns" in gen.prompt_provenance


def test_reduce_carries_declared_columns_onto_the_day_table_section():
    """End of the wiring: what reduce() built is what the renderer is told to emit,
    so the emitted columns cannot disagree with the reported ones."""
    gen = BlockWideGenerator(llm=lambda *a, **k: json.dumps(
        {str(d): {"narrative": "n", "how_it_is_applied": "h",
                  "Instructional Model Stage": "Interpret"} for d in range(1, 25)}),
        batch_size=10)
    res = gen.reduce(ENUMERATE_SUMMARY, DIGESTS, deliverable="blueprint", tier="draft",
                     extension_columns=_EXT_COLS)
    day_section = next(s for s in res.sections if s["key"] == "day_table")
    assert day_section["extension_columns"] == ["Instructional Model Stage", "Pilot Candidate"]
    assert all("extensions" in r for r in day_section["rows"])


def test_reduce_declares_nothing_by_default_and_adds_no_call():
    """Default path for every live prompt: the section carries no extra columns and
    the extension stage is never invoked."""
    calls = []
    gen = BlockWideGenerator(llm=lambda *a, **k: calls.append(1) or json.dumps(
        {str(d): {"narrative": "n", "how_it_is_applied": "h"} for d in range(1, 25)}),
        batch_size=10)
    baseline = gen.reduce(ENUMERATE_SUMMARY, DIGESTS, deliverable="blueprint", tier="draft")
    day_section = next(s for s in baseline.sections if s["key"] == "day_table")
    assert day_section["extension_columns"] == []
    assert "extension_columns" not in gen.prompt_provenance


def test_a_failed_day_states_the_reason_rather_than_being_asked_to_guess():
    """Same discipline as _fill_narratives: no verified facts means no fill."""
    gen = BlockWideGenerator(llm=lambda *a, **k: json.dumps(
        {"1": {"Instructional Model Stage": "Interpret", "Pilot Candidate": "No"},
         "2": {"Instructional Model Stage": "invented", "Pilot Candidate": "invented"}}),
        batch_size=10)
    rows = _ext_rows(2)
    rows[1]["digest_status"] = "failed"
    gen._fill_extensions(rows, _EXT_COLS, "Claude Haiku 4.5 (Bedrock)")
    assert rows[0]["extensions"]["Instructional Model Stage"] == "Interpret"
    assert set(rows[1]["extensions"].values()) == {"REVIEW NEEDED — per-day digest failed."}


def test_a_near_miss_reply_key_is_still_matched():
    """Reply keys are model-generated. Reporting a value the model DID return as never
    returned is the one thing that marker must not say falsely."""
    gen = BlockWideGenerator(llm=lambda *a, **k: json.dumps(
        {"1": {"instructional model stage": "Interpret", "pilot_candidate": "No"}}),
        batch_size=10)
    rows = _ext_rows(1)
    gen._fill_extensions(rows, _EXT_COLS, "Claude Haiku 4.5 (Bedrock)")
    assert rows[0]["extensions"]["Instructional Model Stage"] == "Interpret"
    assert rows[0]["extensions"]["Pilot Candidate"] == "No"


def test_an_exact_key_still_wins_over_a_loose_one():
    gen = BlockWideGenerator(llm=lambda *a, **k: json.dumps(
        {"1": {"Instructional Model Stage": "exact", "instructionalmodelstage": "loose",
               "Pilot Candidate": "x"}}), batch_size=10)
    rows = _ext_rows(1)
    gen._fill_extensions(rows, _EXT_COLS, "Claude Haiku 4.5 (Bedrock)")
    assert rows[0]["extensions"]["Instructional Model Stage"] == "exact"


def test_a_non_dict_day_payload_degrades_to_the_marker():
    gen = BlockWideGenerator(llm=lambda *a, **k: json.dumps({"1": "just a string"}),
                             batch_size=10)
    rows = _ext_rows(1)
    gen._fill_extensions(rows, _EXT_COLS, "Claude Haiku 4.5 (Bedrock)")
    assert set(rows[0]["extensions"].values()) == {EXTENSION_MISSING}
