"""prompt_capability — reconciling the SELECTED prompt against what the block-wide
pipeline actually emits.

The bar for these tests is precision, not recall. A false finding trains reviewers
to ignore the section, and then the true finding is ignored with it — so the
"clean prompt produces nothing at all" cases below are as load-bearing as the
detection ones.
"""
import json

import pytest

from promptops_app.services.block_wide_service import (
    _ACS_REGISTRY_HEADER,
    _DAY_TABLE_HEADER,
    _SOURCE_INVENTORY_HEADER,
    _header_lines,
    emitted_columns,
)
from promptops_app.services.prompt_capability import (
    CapabilityReport,
    append_section,
    assess,
    assess_selected_prompt,
    known_variables,
)


def _emitted_header_row() -> str:
    return "| " + " | ".join(emitted_columns()["day_table"]) + " |"


# --------------------------------------------------------------------------- #
# The refactor that gave this module a single source of truth must not have
# changed one rendered byte of the three tables.
# --------------------------------------------------------------------------- #
def test_header_lines_reproduce_the_shipped_literals_exactly():
    assert _header_lines(_SOURCE_INVENTORY_HEADER) == [
        "| Document Type | File Count | Days Applicable | Status | Production Action | Status Notes |",
        "|---|---|---|---|---|---|",
    ]
    assert _header_lines(_ACS_REGISTRY_HEADER) == [
        "| ACS Code | Type | Task Description | Days Active | High-Miss | Quick Check Priority |",
        "|---|---|---|---|---|---|",
    ]
    day = _header_lines(_DAY_TABLE_HEADER)
    assert day[0].startswith("| Day | Topic | Handbook Reference |")
    assert day[1] == "|" + "---|" * len(_DAY_TABLE_HEADER)


def test_emitted_columns_returns_copies_not_the_live_lists():
    got = emitted_columns()
    got["day_table"].append("MUTATED")
    assert "MUTATED" not in _DAY_TABLE_HEADER


# --------------------------------------------------------------------------- #
# Nothing to say
# --------------------------------------------------------------------------- #
def test_empty_prompt_is_unassessed():
    r = assess("")
    assert r.assessed is False and r.has_findings is False
    assert r.to_provenance() == {"assessed": False}


def test_a_prompt_matching_the_emitted_schema_reports_nothing():
    text = f"Produce the day table.\n\nUse this exact header row:\n\n{_emitted_header_row()}\n"
    r = assess(text)
    assert r.matched_columns == emitted_columns()["day_table"]
    assert r.unmatched_columns == []
    assert r.has_findings is False, "agreement is not a finding"
    assert r.review_lines() == []


def test_prompt_with_no_table_at_all_reports_nothing():
    r = assess("Write a course design document in prose. Use APA citations.")
    assert r.assessed is True
    assert r.requested_day_columns == [] and r.has_findings is False


# --------------------------------------------------------------------------- #
# Column reconciliation
# --------------------------------------------------------------------------- #
def test_bare_header_row_without_a_separator_is_still_read():
    """The commonest way a prompt states a schema, and the form prompt 77 uses for
    all three of its worksheet contracts — a markdown-strict reader sees none."""
    r = assess("Use this exact header row:\n\n| Day | Topic | Flags |\n")
    assert "Day" in r.matched_columns and "Topic" in r.matched_columns
    assert r.unmatched_columns == ["Flags"]


def test_columns_absent_from_the_emitted_set_are_listed():
    r = assess("| Day | Topic | Content Summary | Source Completeness |")
    assert r.unmatched_columns == ["Content Summary", "Source Completeness"]
    assert r.has_findings is True
    line = " ".join(r.review_lines())
    assert "not emitted" in line and "not an error" in line, (
        "the wording must stay descriptive — this check cannot tell a rename from an omission"
    )


def test_a_renamed_column_is_matched_through_the_alias_table():
    r = assess("| Day | Day Title | ACS Code(s) | Application Connection |")
    assert r.unmatched_columns == []
    assert len(r.matched_columns) == 4


def test_parenthetical_commentary_does_not_break_a_match():
    r = assess("| Day | Topic (exact from calendar) | Concept Type (controlled) |")
    assert r.unmatched_columns == []


def test_schema_descriptor_table_is_read_from_its_backticked_names():
    """Prompt 79 declares its columns as a table ABOUT the columns. Read literally
    the requested set would be {"col", "exact header", "population rule"}."""
    text = (
        "### WORKSHEET 4\n\n"
        "| Col. | Exact header | Population rule |\n"
        "|---|---|---|\n"
        "| A | `Day #` | Sequential. |\n"
        "| B | `Topic Label (exact from calendar)` | Preserve wording. |\n"
        "| C | `Handbook Edition as Cited` | Exact edition. |\n"
    )
    r = assess(text)
    assert r.requested_day_columns == [
        "Day #", "Topic Label (exact from calendar)", "Handbook Edition as Cited",
    ]
    assert r.unmatched_columns == ["Handbook Edition as Cited"]


def test_prose_containing_pipes_is_not_mistaken_for_a_header_row():
    text = (
        "Choose one of the following statuses | Complete | Partial | None | and explain "
        "why you chose it in a full sentence that runs on well past any plausible column label.\n"
    )
    r = assess(text)
    assert r.requested_day_columns == []
    assert r.other_requested_tables == 0


def test_a_table_without_a_day_column_is_counted_but_not_reconciled():
    """Worksheet 2/3 tables and AKTR tables must not be scored against the day
    columns — they are a different table, so a per-name verdict would be noise."""
    text = "| Document Type | File Count | Days Applicable | Status | Flags |\n"
    r = assess(text)
    assert r.requested_day_columns == []
    assert r.unmatched_columns == []
    assert r.other_requested_tables == 1


def test_a_day_table_overlapping_only_on_day_itself_is_not_reconciled():
    """Confidence gate. The day column is the entry condition, so it cannot also be
    the evidence: a table whose ONLY common column is "Day" is more likely some other
    day-indexed table than a Worksheet 4 that diverges completely."""
    r = assess("| Day | Weather | Mood | Colour |\n")
    assert r.requested_day_columns == []
    assert r.unmatched_columns == []
    assert r.other_requested_tables == 1, "still counted, just not scored per-name"


def test_the_widest_day_table_wins_when_several_are_declared():
    text = (
        "| Day | Topic |\n\n"
        "| Day | Topic | Concept Type | Concept Scope | Flags |\n"
    )
    r = assess(text)
    assert len(r.requested_day_columns) == 5


# --------------------------------------------------------------------------- #
# Output-format demands the pipeline structurally cannot satisfy
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text, fragment", [
    ("Respond with `Completed: [Download f.xlsx](sandbox:/mnt/data/f.xlsx)`", "sandbox"),
    ("You have code execution and must use it to inspect the files.", "code execution"),
    ("Build the .xlsx workbook using the schema above.", "binary file"),
    ("Do not paste workbook content into the chat.", "NOT be returned inline"),
    ("Respond only with a download link to the workbook.", "download link"),
])
def test_artifact_demands_are_detected(text, fragment):
    r = assess(text)
    assert any(fragment in d for d in r.artifact_demands), r.artifact_demands
    assert r.has_findings is True


def test_a_markdown_emitting_prompt_triggers_no_artifact_demand():
    text = (
        "Produce the Blueprint as five worksheets, each a markdown table. Every cell "
        "that cannot be populated must read 'REVIEW NEEDED — no source available'. "
        "Cite sources as (FAA-H-8083-31B, p. 4-12). Attached files may include .xlsx "
        "calendars and .pdf handbooks."
    )
    assert assess(text).artifact_demands == [], (
        "merely naming .xlsx/.pdf SOURCE files must not read as demanding a binary deliverable"
    )


# --------------------------------------------------------------------------- #
# Template variables
# --------------------------------------------------------------------------- #
def test_platform_variables_are_not_a_finding():
    text = "Course: {{course_title}} for {{target_audience}}.\n{{extra_instructions_block}}"
    r = assess(text)
    assert r.unknown_variables == []
    assert r.has_findings is False


def test_foreign_injection_variables_are_reported():
    text = "Master system prompt: {{MASTER_SYSTEM_PROMPT}}\nBlock: {{BLOCK_NUMBER}}\n"
    r = assess(text)
    assert r.unknown_variables == ["MASTER_SYSTEM_PROMPT", "BLOCK_NUMBER"]
    assert "reach no model" in " ".join(r.review_lines())


def test_known_variables_come_from_the_registry_not_a_local_copy():
    from promptops_app.prompts.prompt_loader import _REGISTRY
    declared = set(_REGISTRY["cdd_generation"]["required_vars"])
    assert declared <= known_variables()


# --------------------------------------------------------------------------- #
# Rendering + provenance
# --------------------------------------------------------------------------- #
def test_append_section_is_a_no_op_without_findings():
    md = "## WORKSHEET 1: BLOCK OVERVIEW\n\n- **Block:** 2\n"
    assert append_section(md, assess(_emitted_header_row())) == md
    assert append_section(md, None) == md
    assert append_section(md, CapabilityReport()) == md


def test_append_section_adds_a_non_worksheet_heading():
    md = "## WORKSHEET 4: DAY-BY-DAY MAP\n"
    out = append_section(md, assess("| Day | Topic | Flags |"))
    assert out.startswith(md)
    assert "## PROMPT RECONCILIATION" in out
    assert "## WORKSHEET 6" not in out, (
        "must not look like a worksheet — split_cdd_worksheets would export it as a sheet"
    )


def test_the_appended_section_parses_as_one_ordinary_section():
    from promptops_app.parsers.cdd_parser import parse_sections_from_text
    md = "## WORKSHEET 4: DAY-BY-DAY MAP\n\n| Day |\n"
    sections = parse_sections_from_text(append_section(md, assess("| Day | Topic | Flags |")))
    assert "PROMPT RECONCILIATION" in sections
    assert "WORKSHEET 4: DAY-BY-DAY MAP" in sections


def test_provenance_is_json_serialisable_and_reports_its_own_caps():
    wide = "| Day | Topic | " + " | ".join(f"Made Up {i}" for i in range(60)) + " |"
    prov = assess(wide).to_provenance()
    json.dumps(prov)
    assert prov["unmatched_columns"]["total"] == 60
    assert len(prov["unmatched_columns"]["items"]) == 40, "capped list stays visibly capped"


def test_assess_never_raises_on_hostile_input():
    for bad in [None, "|", "|||", "| |\n|---|\n| |", "\x00| Day |", "|" * 5000]:
        assert assess(bad).has_findings in (True, False)


def test_assess_selected_prompt_degrades_to_unassessed(monkeypatch):
    import promptops_app.services.prompt_guidance as pg

    def boom(*a, **k):
        raise RuntimeError("registry down")

    monkeypatch.setattr(pg, "_resolve_prompt_text", boom)
    r = assess_selected_prompt(db=None, request_body=object(), deliverable="cdd")
    assert r.assessed is False and r.review_lines() == []


def test_assess_selected_prompt_reads_the_same_text_the_distiller_does(monkeypatch):
    """It must assess the resolution that actually ran; a report about a different
    resolution would be worse than no report."""
    import promptops_app.services.prompt_guidance as pg

    seen = {}

    def fake(db, request_body, deliverable):
        seen["deliverable"] = deliverable
        return "| Day | Topic | Flags |"

    monkeypatch.setattr(pg, "_resolve_prompt_text", fake)
    r = assess_selected_prompt(db=None, request_body=object(), deliverable="blueprint")
    assert seen["deliverable"] == "blueprint"
    assert r.unmatched_columns == ["Flags"]


# --------------------------------------------------------------------------- #
# Single-call path guard — refuse a prompt this path cannot honour
# --------------------------------------------------------------------------- #
def test_legacy_blockers_names_every_demand_it_finds():
    from promptops_app.services.prompt_capability import legacy_blockers

    blockers = legacy_blockers(
        "You have code execution and must use it.",
        "Respond only with `Completed: [Download b.xlsx](sandbox:/mnt/data/b.xlsx)`",
    )
    assert len(blockers) >= 2
    assert any("code execution" in b for b in blockers)
    assert any("sandbox" in b for b in blockers)


@pytest.mark.parametrize("text", [
    "Generate a summary of the attached .pdf handbook.",
    "Produce a report comparing the .xlsx calendar with the syllabus.",
    "Create a lesson from the .docx instructor guide provided.",
])
def test_naming_a_source_file_never_refuses(text):
    """A build verb near a file extension is ordinary in a prompt that merely READS
    one. Refusing that would be exactly the false positive this module avoids — so
    binary_deliverable is reported, never blocking."""
    from promptops_app.services.prompt_capability import legacy_blockers

    assert legacy_blockers(text, "") == []


def test_building_a_workbook_is_reported_but_not_refused_on_that_signal_alone():
    from promptops_app.services.prompt_capability import legacy_blockers

    text = "Build and verify the `.xlsx` workbook using the exact schema above."
    assert len(assess(text).artifact_demands) == 1, "reported"
    assert legacy_blockers(text, "") == [], "but not conclusive on its own"


def test_every_blocking_check_is_marked_deliberately():
    """The blocking flag is the difference between a report and a refusal, so the set
    is pinned: adding a blocking check should require changing this test."""
    from promptops_app.services.prompt_capability import _ARTIFACT_CHECKS

    blocking = {key for key, _p, _m, is_blocking in _ARTIFACT_CHECKS if is_blocking}
    assert blocking == {"sandbox_path", "code_execution", "withhold_inline", "reply_is_a_link"}


def test_legacy_blockers_ignores_column_divergence():
    """A different column set is a different document, not a broken one — the
    single-call path emits whatever the prompt asks for."""
    from promptops_app.services.prompt_capability import legacy_blockers

    assert legacy_blockers("| Day | Topic | Flags | Content Summary |", "Generate it.") == []


def test_legacy_blockers_ignores_a_missing_injection_slot():
    """7 of 17 live CDD/Blueprint prompts have no slot; refusing them would break
    working flows to prevent a degradation. context_was_dropped reports it instead."""
    from promptops_app.services.prompt_capability import legacy_blockers

    assert legacy_blockers("Produce five markdown worksheets.", "Generate the Blueprint.") == []


def test_reject_if_unsatisfiable_raises_the_prompt_misconfigured_error():
    from app.core.exceptions import PromptConfigurationError
    from promptops_app.services.prompt_capability import reject_if_unsatisfiable

    with pytest.raises(PromptConfigurationError) as exc:
        reject_if_unsatisfiable("You have code execution.", "Build the .xlsx workbook.",
                                what="generating this CDD")
    assert "generating this CDD" in str(exc.value)
    assert exc.value.detail["blockers"], "the response must name what to fix"


def test_reject_if_unsatisfiable_is_silent_for_an_ordinary_prompt():
    from promptops_app.services.prompt_capability import reject_if_unsatisfiable

    reject_if_unsatisfiable(
        "Produce the Blueprint as five markdown worksheets.",
        "Generate it for Block 2. Sources may include .xlsx calendars.",
        what="generating this CDD",
    )  # must not raise


# --------------------------------------------------------------------------- #
# Dropped source context
# --------------------------------------------------------------------------- #
def test_context_present_in_the_prompt_is_not_reported():
    from promptops_app.services.prompt_capability import context_was_dropped

    block = "CDD CONTEXT\n- unit 1: syllabus text"
    assert context_was_dropped(block, "sys", f"user\n\n{block}") is False


def test_context_assembled_but_absent_is_reported():
    from promptops_app.services.prompt_capability import context_was_dropped

    assert context_was_dropped("CDD CONTEXT\n- unit 1", "sys", "user with no slot") is True


def test_no_context_assembled_is_never_reported():
    from promptops_app.services.prompt_capability import context_was_dropped

    for empty in ("", "   ", None):
        assert context_was_dropped(empty, "sys", "user") is False


# --------------------------------------------------------------------------- #
# Wiring — asserted against the source, because the guard's whole value is that it
# runs BEFORE the model call. A later refactor that moves it after would leave every
# behavioural test passing while re-opening the bug.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path, what", [
    ("app/api/v1/routers/cdd.py", "generating this CDD"),
    ("app/api/v1/routers/blueprints.py", "generating this Blueprint"),
])
def test_the_guard_runs_before_the_model_call(path, what):
    src = open(path).read()
    guard = src.index(f'reject_if_unsatisfiable(system_prompt, user_prompt, what="{what}")')
    call = src.index("llm_result = generate_with_metadata(", guard - 4000)
    assert guard < call, "the refusal must precede the LLM call, not follow it"


@pytest.mark.parametrize("path", [
    "app/api/v1/routers/cdd.py",
    "app/api/v1/routers/blueprints.py",
])
def test_dropped_context_is_recorded_on_the_provenance_row(path):
    src = open(path).read()
    assert "context_was_dropped(dis_context_block, system_prompt, user_prompt)" in src
    assert 'prompt_provenance["source_context_dropped"] = True' in src


# --------------------------------------------------------------------------- #
# Distiller memo staleness
# --------------------------------------------------------------------------- #
def test_editing_the_distiller_busts_the_guidance_memo(monkeypatch):
    """The distilled guidance is folded into the per-day digest cache key, so a stale
    memo would bake superseded guidance into digests that report as current."""
    import promptops_app.services.prompt_guidance as pg

    before = pg._cache_key("prompt", "model", 24, 8000)
    monkeypatch.setattr(pg, "_DIGEST_SYSTEM_TEMPLATE", pg._DIGEST_SYSTEM_TEMPLATE + " Also X.")
    after = pg._cache_key("prompt", "model", 24, 8000)
    assert before != after


def test_the_distiller_version_is_derived_not_hand_maintained():
    import promptops_app.services.prompt_guidance as pg

    assert pg.distiller_version() == pg.distiller_version(), "must be deterministic"
    assert len(pg.distiller_version()) == 12
    src = open("promptops_app/services/prompt_guidance.py").read()
    assert "_DIGEST_SYSTEM_TEMPLATE.encode" in src, (
        "must hash the template itself — a hand-bumped constant can be forgotten"
    )


# --------------------------------------------------------------------------- #
# Selection-time surface — what the prompt picker is told before anything is spent
# --------------------------------------------------------------------------- #
def test_blocking_demands_are_the_refusable_subset():
    r = assess("You have code execution. Build the `.xlsx` workbook.")
    assert len(r.artifact_demands) == 2, "both are reported"
    assert len(r.blocking_demands) == 1, "only the conclusive one is refusable"
    assert r.would_refuse_single_call is True


def test_would_refuse_is_false_when_only_the_weak_signal_hits():
    r = assess("Assemble the deliverable as a .xlsx workbook.")
    assert r.artifact_demands and r.blocking_demands == []
    assert r.would_refuse_single_call is False


def test_a_prompt_with_a_source_slot_raises_no_slot_notice():
    r = assess("Generate the CDD.\n\n{{extra_instructions_block}}")
    assert r.has_source_context_slot is True
    assert r.selection_notices() == []


def test_either_slot_name_counts_as_a_slot():
    """The CDD router supplies the same block under both names."""
    assert assess("Body.\n{{extra_instructions}}").has_source_context_slot is True
    assert assess("Body.\n{{extra_instructions_block}}").has_source_context_slot is True


def test_a_missing_slot_is_a_selection_notice_but_not_a_document_finding():
    """On the block-wide path source never flows through the prompt, so this would be
    misleading noise in a generated document — but it is exactly what a requester
    needs before picking a prompt for a single-call generation."""
    r = assess("Produce the Blueprint. No slot here.")
    assert r.has_source_context_slot is False
    assert r.has_findings is False
    assert r.review_lines() == [], "must not appear in the document"
    notices = r.selection_notices()
    assert len(notices) == 1
    assert notices[0]["severity"] == "warning"
    assert "extra_instructions_block" in notices[0]["message"]
    assert "block-wide pipeline is unaffected" in notices[0]["message"]


def test_selection_notices_are_a_superset_of_the_document_findings():
    r = assess("| Day | Topic | Flags |\nYou have code execution.")
    doc = r.review_lines()
    notices = r.selection_notices()
    assert r.has_findings and doc
    # Every finding rendered into the document has a matching notice.
    assert len(notices) >= len([line for line in doc if line.startswith("-")])
    assert {n["severity"] for n in notices} <= {"error", "warning", "info"}


def test_findings_have_one_source_so_the_two_renderers_cannot_drift():
    r = assess("| Day | Topic | Flags |")
    bullets = [line for line in r.review_lines() if line.startswith("-")]
    findings = [n for n in r.selection_notices() if n["severity"] != "warning"
                or "extra_instructions_block" not in n["message"]]
    assert len(bullets) == len(findings)
    for bullet, finding in zip(bullets, findings):
        assert finding["message"] in bullet


def test_an_unassessed_report_offers_no_notices():
    from promptops_app.services.prompt_capability import CapabilityReport

    assert CapabilityReport().selection_notices() == []
    assert CapabilityReport().would_refuse_single_call is False
    assert CapabilityReport().has_source_context_slot is True, (
        "a default must never imply a problem"
    )


# --------------------------------------------------------------------------- #
# API layer — the picker reads this off the prompt detail, so no extra round trip
# --------------------------------------------------------------------------- #
def _fake_version(system="", user=""):
    import types
    return types.SimpleNamespace(system_prompt=system, user_prompt_template=user)


def _fake_prompt(component_type, pid=1):
    import types
    return types.SimpleNamespace(id=pid, component_type=component_type)


def test_capability_is_computed_for_cdd_and_blueprint_components():
    from app.api.v1.routers.prompts import _prompt_capability

    for component in ("cdd", "blueprint"):
        cap = _prompt_capability(_fake_prompt(component),
                                _fake_version("| Day | Topic | Flags |"))
        assert cap is not None, component
        assert cap.unmatched_columns == ["Flags"]
        assert cap.matched_columns == 2 and cap.requested_day_columns == 3


def test_capability_is_omitted_for_components_that_never_produce_a_day_table():
    from app.api.v1.routers.prompts import _prompt_capability

    for component in ("quiz", "style", "generate", "", None):
        assert _prompt_capability(_fake_prompt(component), _fake_version("| Day |")) is None


def test_capability_is_omitted_when_there_is_no_active_version():
    from app.api.v1.routers.prompts import _prompt_capability

    assert _prompt_capability(_fake_prompt("cdd"), None) is None


def test_capability_carries_the_refusal_prediction_and_notices():
    from app.api.v1.routers.prompts import _prompt_capability

    cap = _prompt_capability(
        _fake_prompt("cdd"),
        _fake_version("You have code execution.", "Respond only with a download link."),
    )
    assert cap.would_refuse_single_call is True
    assert cap.blocking_demands
    assert any(n.severity == "error" for n in cap.notices)


def test_capability_never_breaks_the_read(monkeypatch):
    """A read endpoint must not fail because a reconciliation could not be computed."""
    import promptops_app.services.prompt_capability as pc
    from app.api.v1.routers.prompts import _prompt_capability

    def boom(_text):
        raise RuntimeError("assess exploded")

    monkeypatch.setattr(pc, "assess", boom)
    assert _prompt_capability(_fake_prompt("cdd"), _fake_version("| Day | Topic |")) is None


def test_capability_is_optional_on_the_detail_schema():
    """Additive by construction: every existing consumer of PromptDetailRead keeps
    working, and a null means "not applicable", never "no problems"."""
    from app.schemas.prompt import PromptDetailRead

    field = PromptDetailRead.model_fields["capability"]
    assert field.default is None
    assert not field.is_required()


def test_capability_name_lists_are_capped_with_an_honest_total():
    from app.api.v1.routers.prompts import _CAPABILITY_NAME_CAP, _prompt_capability

    wide = "| Day | Topic | " + " | ".join(f"Made Up {i}" for i in range(60)) + " |"
    variables = " ".join("{{FOREIGN_%d}}" % i for i in range(60))
    cap = _prompt_capability(_fake_prompt("cdd"), _fake_version(wide, variables))
    assert cap.unmatched_columns_total == 60
    assert len(cap.unmatched_columns) == _CAPABILITY_NAME_CAP
    assert cap.unknown_variables_total == 60
    assert len(cap.unknown_variables) == _CAPABILITY_NAME_CAP


# --------------------------------------------------------------------------- #
# Prompt-declared additional day columns
# --------------------------------------------------------------------------- #
from promptops_app.services.prompt_capability import (  # noqa: E402
    parse_extension_columns, known_variables, _MAX_EXTENSION_COLUMNS,
)

_DECL = """ADDITIONAL DAY COLUMNS
- Instructional Model Stage: which stage of the Block's model this day belongs to
- Pilot Candidate: whether the SME recommends this day for pilot testing
"""


def test_a_declaration_carries_both_the_label_and_its_definition():
    """The description is what reaches the fill prompt. A bare label would ask the
    model to guess the column's purpose from its name."""
    accepted, rejected = parse_extension_columns(_DECL)
    assert [c["label"] for c in accepted] == ["Instructional Model Stage", "Pilot Candidate"]
    assert accepted[0]["description"].startswith("which stage")
    assert rejected == []


def test_blank_lines_do_not_end_the_list_but_prose_does():
    text = ("ADDITIONAL DAY COLUMNS\n"
            "- A Column: first\n"
            "\n"
            "- B Column: still in the list\n"
            "Now some prose about something else.\n"
            "- C Column: no longer in the list\n")
    accepted, _ = parse_extension_columns(text)
    assert [c["label"] for c in accepted] == ["A Column", "B Column"]


def test_a_declaration_colliding_with_an_emitted_column_is_refused():
    """Emitting it would put two columns of the same meaning in one row. Caught
    canonically, so an alias of an emitted label is caught too."""
    accepted, rejected = parse_extension_columns(
        "ADDITIONAL DAY COLUMNS\n- ACS Codes: a duplicate of an emitted column\n")
    assert accepted == []
    assert len(rejected) == 1 and "already emitted" in rejected[0]


def test_a_repeated_declaration_is_refused_once():
    accepted, rejected = parse_extension_columns(
        "ADDITIONAL DAY COLUMNS\n- Stage: one\n- Stage: two\n")
    assert [c["label"] for c in accepted] == ["Stage"]
    assert len(rejected) == 1 and "more than once" in rejected[0]


def test_over_cap_declarations_are_reported_not_silently_dropped():
    """Silent truncation of a requested column is the exact failure this module
    exists to prevent."""
    body = "".join(f"- Column {i}: definition {i}\n" for i in range(_MAX_EXTENSION_COLUMNS + 3))
    accepted, rejected = parse_extension_columns("ADDITIONAL DAY COLUMNS\n" + body)
    assert len(accepted) == _MAX_EXTENSION_COLUMNS
    assert len(rejected) == 3 and all("exceeds the limit" in r for r in rejected)


def test_a_prompt_with_no_marker_declares_nothing():
    """Opt-in by an explicit marker, not inferred from the column list: 4 of the 17
    live prompts name existing columns under other labels, and treating those as
    declarations would widen every one of their tables with duplicates."""
    accepted, rejected = parse_extension_columns(
        "| Day | Content Summary | Formative Assessment |\n|---|---|---|\n")
    assert (accepted, rejected) == ([], [])


def test_declaring_a_column_stops_it_being_reported_as_divergent():
    """The loop that makes the feature coherent: a declared column IS emitted, so it
    is matched rather than a finding."""
    report = assess(_DECL + """
| Day | Topic | Instructional Model Stage | Pilot Candidate |
|---|---|---|---|
""")
    assert report.unmatched_columns == []
    assert "Instructional Model Stage" in report.matched_columns
    assert not report.has_findings


def test_the_same_column_undeclared_is_reported_as_divergent():
    """Control for the test above — without the declaration it must still surface."""
    report = assess("""
| Day | Topic | Instructional Model Stage |
|---|---|---|
""")
    assert report.unmatched_columns == ["Instructional Model Stage"]
    assert report.has_findings


def test_a_refused_declaration_is_a_finding_a_reviewer_sees():
    report = assess("ADDITIONAL DAY COLUMNS\n- ACS Codes: duplicate\n")
    assert report.has_findings
    assert any("refused" in m for _s, _sc, m in report._findings())


def test_provenance_records_the_declared_labels_and_refusals():
    report = assess(_DECL + "- ACS Codes: duplicate\n")
    prov = report.to_provenance()
    assert prov["extension_columns"] == ["Instructional Model Stage", "Pilot Candidate"]
    assert prov["extension_rejections"]["total"] == 1


def test_a_declaration_with_no_definition_is_reported_and_does_not_end_the_list():
    """Found in review: an empty description failed the item pattern, which ended the
    list and silently dropped every declaration after it — the silent truncation this
    module exists to prevent, reintroduced by its own parser."""
    accepted, rejected = parse_extension_columns(
        "ADDITIONAL DAY COLUMNS\n- Broken Column:\n- Good Column: a real definition\n")
    assert [c["label"] for c in accepted] == ["Good Column"]
    assert len(rejected) == 1 and "declares no definition" in rejected[0]


# --------------------------------------------------------------------------- #
# The shipped AIM Blueprint prompt must stay reconciled with the pipeline
# --------------------------------------------------------------------------- #
def test_the_shipped_aim_blueprint_prompt_reconciles_clean():
    """AIM_BLOCK_BLUEPRINT_PROMPT.md states the day-table columns literally, so a
    change to _DAY_TABLE_HEADER silently invalidates it. This is the only thing that
    would notice."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2]
    doc = root / "docs" / "aim_prompts" / "AIM_BLOCK_BLUEPRINT_PROMPT.md"
    assert doc.exists(), f"{doc.name} is missing — update this test if it was renamed"
    fence = chr(96) * 3
    parts = doc.read_text(encoding="utf-8").split(fence + "text")
    assert len(parts) >= 3, "expected a fenced System Prompt and User Prompt block"
    text = parts[1].split(fence)[0] + parts[2].split(fence)[0]
    report = assess(text, component="cdd")
    assert report.unmatched_columns == [], (
        "the prompt names day-table columns the renderer no longer emits: "
        f"{report.unmatched_columns}")
    assert report.extension_rejections == []
    assert report.unknown_variables == []
    assert report.blocking_demands == []
    assert not report.has_findings
    assert [c["label"] for c in report.extension_columns] == ["Instructional Model Stage"]


def test_the_shipped_prompt_renders_on_both_generation_routes():
    """The defect this test exists for: the prompt named {{selected_module}}, which only
    the Blueprint router supplies — and every AIM Block Blueprint prompt in this project
    is component_type="cdd". render() is strict, so on the route AIM actually uses it
    raised rather than generating at all."""
    import pathlib
    from promptops_app.prompts.prompt_builder import render
    root = pathlib.Path(__file__).resolve().parents[2]
    fence = chr(96) * 3
    parts = (root / "docs" / "aim_prompts" / "AIM_BLOCK_BLUEPRINT_PROMPT.md").read_text(encoding="utf-8").split(fence + "text")
    system, user = parts[1].split(fence)[0], parts[2].split(fence)[0]

    # verbatim from each router's variables dict
    cdd_route = {"course_title": "c", "course_name": "c", "target_audience": "a",
                 "expert_domain": "d", "audience_level": "l", "estimated_duration": "1",
                 "extra_instructions_block": "x", "extra_instructions": "x",
                 "style_guidelines": "s", "grade_level": "g", "block": "Block 2"}
    blueprint_route = {"cdd_context": "c", "selected_module": "m", "extra_instructions": "x",
                       "teacher_mode": "Yes", "student_mode": "No",
                       "style_guidelines": "s", "block": "Block 2"}
    for route in (cdd_route, blueprint_route):
        rendered = render(system, route) + render(user, route)   # strict: raises if missing
        assert "{{" not in rendered
        assert "Block 2" in rendered


def test_the_shipped_prompt_reconciles_on_both_components():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2]
    fence = chr(96) * 3
    parts = (root / "docs" / "aim_prompts" / "AIM_BLOCK_BLUEPRINT_PROMPT.md").read_text(encoding="utf-8").split(fence + "text")
    text = parts[1].split(fence)[0] + parts[2].split(fence)[0]
    for component in ("cdd", "blueprint"):
        report = assess(text, component=component)
        assert not report.has_findings, f"{component}: {report.unknown_variables}"


# --------------------------------------------------------------------------- #
# The variable check must know which route a prompt renders on
# --------------------------------------------------------------------------- #
def test_a_variable_only_the_other_route_supplies_is_reported():
    """The union of both routes' variables cannot see this, and it is the check that
    should have caught the prompt defect above: a name the prompt's own route does not
    supply fails render()'s strict check and refuses the generation outright."""
    text = "Generate for {{selected_module}} using {{cdd_context}}."
    assert assess(text, component="cdd").unknown_variables == ["selected_module", "cdd_context"]
    assert assess(text, component="blueprint").unknown_variables == []


def test_no_component_still_answers_with_the_union():
    """Backwards-compatible default for a prompt whose route is unknown."""
    text = "Generate for {{selected_module}} using {{course_name}}."
    assert assess(text).unknown_variables == []


def test_block_is_a_known_variable_on_both_routes():
    """Both routers now supply it under the same name, so a block-wide prompt is
    portable between them instead of hardcoding its block."""
    assert "block" in known_variables("cdd")
    assert "block" in known_variables("blueprint")


def test_an_unrecognised_component_falls_back_to_the_union():
    assert known_variables("nonsense") == known_variables()


def test_a_pathological_declaration_count_yields_a_bounded_finding():
    """Found in review: the rejection message joined every refusal unbounded. This
    string is rendered into a UI notice AND into the generated document."""
    from promptops_app.services.prompt_capability import _FINDING_LIST_CAP
    report = assess("ADDITIONAL DAY COLUMNS\n"
                    + "".join(f"- Column {i}: definition {i}\n" for i in range(500)))
    assert len(report.extension_rejections) == 500 - _MAX_EXTENSION_COLUMNS
    message = next(m for _s, _sc, m in report._findings() if "refused" in m)
    assert len(message) < 1500
    assert f"and {500 - _MAX_EXTENSION_COLUMNS - _FINDING_LIST_CAP} more" in message
    # and the provenance row stays bounded independently
    prov = report.to_provenance()
    assert len(prov["extension_rejections"]["items"]) == 40
    assert prov["extension_rejections"]["total"] == 500 - _MAX_EXTENSION_COLUMNS


# --------------------------------------------------------------------------- #
# Each notice names the generation path it is true of
# --------------------------------------------------------------------------- #
_SCOPES = {"block", "single", "both"}


def _shipped_prompt_text() -> str:
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2]
    fence = chr(96) * 3
    parts = (root / "docs" / "aim_prompts" / "AIM_BLOCK_BLUEPRINT_PROMPT.md").read_text(encoding="utf-8").split(fence + "text")
    return parts[1].split(fence)[0] + parts[2].split(fence)[0]


def _for(notices, scope):
    """What the UI renders at *scope* — the same filter the panel applies."""
    return [n for n in notices if n["scope"] in (scope, "both")]


def test_every_notice_declares_a_scope():
    report = assess(_shipped_prompt_text(), component="cdd")
    notices = report.selection_notices()
    assert notices, "the shipped prompt should still produce its informational lines"
    assert {n["scope"] for n in notices} <= _SCOPES


def test_day_table_findings_are_scoped_to_the_block_wide_path():
    """The defect this exists for: the picker is shared by both Generate buttons, and
    these lines were rendering above the single-call one. That path does not emit the
    renderer's day table at all — the model writes the whole document — so 'matched 32
    of 32' described a document that button never produces."""
    notices = assess(_shipped_prompt_text(), component="cdd").selection_notices()
    block_only = _for(notices, "block")
    assert len(block_only) == len(notices)
    assert _for(notices, "single") == []
    assert any("additional day-table column" in n["message"] for n in block_only)
    assert any("Matched 32 of 32" in n["message"] for n in block_only)


def test_an_impossible_output_demand_reaches_both_placements():
    """Scoping must not hide a refusal. The block-wide pipeline falls back to the
    single-call path on a DIS or reduce failure, so this is true wherever it runs."""
    report = assess("Return the result as a downloadable .xlsx file at sandbox:/out.xlsx.")
    notices = report.selection_notices()
    demand = [n for n in notices if n["severity"] == "error"]
    assert demand and demand[0]["scope"] == "both"
    assert _for(notices, "single") and _for(notices, "block")


def test_the_missing_source_slot_warning_is_single_call_only():
    """It says so in its own text: the block-wide pipeline supplies sources directly,
    so beside the block-wide button it would be false."""
    report = assess("Produce a plan for {{course_title}} with no source slot at all.")
    assert report.has_source_context_slot is False
    slot = [n for n in report.selection_notices() if "No {{extra_instructions_block}}" in n["message"]]
    assert len(slot) == 1 and slot[0]["scope"] == "single"


def test_a_column_divergence_is_block_scoped_not_hidden():
    report = assess("| Day | Topic | Invented Column |\n|---|---|---|\n")
    unmatched = [n for n in report.selection_notices() if "not emitted" in n["message"]]
    assert len(unmatched) == 1 and unmatched[0]["scope"] == "block"


def test_an_unsupplied_variable_is_true_on_either_path():
    report = assess("Use {{nonexistent_thing}}.", component="cdd")
    unknown = [n for n in report.selection_notices() if "never" in n["message"]]
    assert len(unknown) == 1 and unknown[0]["scope"] == "both"


def test_the_document_section_is_unchanged_by_scoping():
    """review_lines shares _findings with the UI. Scope is a placement hint for the
    picker; the generated document reports every finding regardless."""
    report = assess("| Day | Topic | Invented Column |\n|---|---|---|\nUse {{nope}}.")
    lines = report.review_lines()
    assert any("not emitted" in ln for ln in lines)
    assert any("never" in ln for ln in lines)
