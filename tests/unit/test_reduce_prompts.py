"""Tests for DB-editable block-wide REDUCE prompts + their contract guard.

The point of the feature is that an admin can edit the instructional half of the
REDUCE prompts at runtime. The point of the guard is that they cannot break the
machine contract while doing so. Both halves need tests, because a silent
regression in either one produces a *structurally valid but empty* deliverable —
the failure mode with no stack trace.
"""
from __future__ import annotations

import types

import pytest

from promptops_app.services.reduce_prompts import (
    NARRATIVE_CONTRACT,
    PATTERNS_CONTRACT,
    PromptContract,
    ReducePrompt,
    render_user_prompt,
    resolve_reduce_prompt,
)

BUILTIN_SYSTEM = "builtin system"
BUILTIN_USER = 'reply with {"narrative": str} for {{day_records}}'
CONTRACT = PromptContract(
    required_placeholders=("day_records",),
    required_json_keys=("narrative",),
)


def _tpl(system: str, user: str, *, source: str = "db", version: str = "v3"):
    return types.SimpleNamespace(
        system_template=system, user_template=user, source=source, version=version)


# --------------------------------------------------------------------------- #
# Contract checking
# --------------------------------------------------------------------------- #
def test_contract_flags_missing_placeholder_and_missing_key():
    problems = CONTRACT.check("no placeholder and no keys here")
    assert any("placeholder" in p for p in problems)
    assert any("JSON keys" in p for p in problems)


def test_contract_passes_on_a_valid_template():
    assert CONTRACT.check(BUILTIN_USER) == []


def test_narrative_contract_covers_every_key_the_parser_reads():
    """Guards the sync between the contract and _fill_narratives' own parsing.

    If a new generated cell is added to the reduce reply but not to this tuple, an
    admin could drop it from their template and the cell would silently go blank.
    """
    from promptops_app.services import block_wide_generator as bwg
    for key in NARRATIVE_CONTRACT.required_json_keys:
        assert key in bwg._DEFAULT_NARRATIVE_USER, f"{key} missing from built-in prompt"
    for key in PATTERNS_CONTRACT.required_json_keys:
        assert key in bwg._DEFAULT_PATTERNS_USER


# --------------------------------------------------------------------------- #
# Resolution — DB tier reachable, per-layer fallback
# --------------------------------------------------------------------------- #
def test_resolution_passes_db_session_through(monkeypatch):
    """The whole bug this feature fixes: load_template only consults its DB tier
    when a session is supplied, so the reduce prompts advertised runtime
    editability they could not deliver."""
    seen = {}

    def fake_load(name, **kwargs):
        seen.update(kwargs)
        seen["name"] = name
        return _tpl("db system", BUILTIN_USER)

    monkeypatch.setattr("promptops_app.prompts.prompt_loader.load_template", fake_load)
    sentinel = object()
    resolve_reduce_prompt(
        "cdd_reduce", builtin_system=BUILTIN_SYSTEM, builtin_user=BUILTIN_USER,
        contract=CONTRACT, db=sentinel, project_id=1, cluster_id=2, course_id=3, prompt_id=59,
    )
    assert seen["db"] is sentinel
    assert (seen["project_id"], seen["cluster_id"], seen["course_id"], seen["prompt_id"]) == (1, 2, 3, 59)


def test_admin_edited_prompt_is_used_when_contract_holds(monkeypatch):
    edited_user = 'Give me {"narrative": str}, tersely. Days: {{day_records}}'
    monkeypatch.setattr("promptops_app.prompts.prompt_loader.load_template",
                        lambda name, **kw: _tpl("edited system", edited_user))
    got = resolve_reduce_prompt("cdd_reduce", builtin_system=BUILTIN_SYSTEM,
                                builtin_user=BUILTIN_USER, contract=CONTRACT, db=object())
    assert got.system == "edited system"
    assert got.user_template == edited_user
    assert got.system_source == "db" and got.user_source == "db"


def test_contract_violating_edit_falls_back_to_builtin_user_only(monkeypatch):
    """A bad edit must degrade loudly to the built-in USER prompt — while still
    honouring the admin's SYSTEM edit, which carries no contract."""
    monkeypatch.setattr("promptops_app.prompts.prompt_loader.load_template",
                        lambda name, **kw: _tpl("edited system", "forgot the keys entirely"))
    got = resolve_reduce_prompt("cdd_reduce", builtin_system=BUILTIN_SYSTEM,
                                builtin_user=BUILTIN_USER, contract=CONTRACT, db=object())
    assert got.user_template == BUILTIN_USER
    assert got.user_source == "builtin"
    assert got.system == "edited system"      # system edit survives
    assert "user_template" in got.rejected     # and the rejection is recorded
    assert "narrative" in got.rejected["user_template"]


def test_missing_template_falls_back_entirely(monkeypatch):
    def boom(name, **kw):
        raise FileNotFoundError(name)
    monkeypatch.setattr("promptops_app.prompts.prompt_loader.load_template", boom)
    got = resolve_reduce_prompt("cdd_reduce", builtin_system=BUILTIN_SYSTEM,
                                builtin_user=BUILTIN_USER, contract=CONTRACT)
    assert (got.system, got.user_template) == (BUILTIN_SYSTEM, BUILTIN_USER)
    assert got.template_version == "builtin"


def test_empty_system_layer_falls_back_but_keeps_user(monkeypatch):
    monkeypatch.setattr("promptops_app.prompts.prompt_loader.load_template",
                        lambda name, **kw: _tpl("   ", BUILTIN_USER))
    got = resolve_reduce_prompt("cdd_reduce", builtin_system=BUILTIN_SYSTEM,
                                builtin_user=BUILTIN_USER, contract=CONTRACT, db=object())
    assert got.system == BUILTIN_SYSTEM and got.system_source == "builtin"
    assert got.user_source == "db"


def test_provenance_records_which_layer_came_from_where(monkeypatch):
    monkeypatch.setattr("promptops_app.prompts.prompt_loader.load_template",
                        lambda name, **kw: _tpl("s", BUILTIN_USER, source="file", version="v9"))
    prov = resolve_reduce_prompt("cdd_reduce", builtin_system=BUILTIN_SYSTEM,
                                 builtin_user=BUILTIN_USER, contract=CONTRACT).to_provenance()
    assert prov["template_version"] == "v9"
    assert prov["system_source"] == "file" and prov["user_source"] == "file"


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def test_render_substitutes_and_leaves_no_placeholder():
    prompt = ReducePrompt(system="s", user_template=BUILTIN_USER, template_name="t")
    out = render_user_prompt(prompt, {"day_records": "[1,2,3]"}, CONTRACT, BUILTIN_USER)
    assert "[1,2,3]" in out and "{{day_records}}" not in out


def test_render_falls_back_when_variables_erase_the_contract():
    """A pre-render check cannot catch a template whose only mention of a
    required key arrives via a variable that resolves empty. The post-render
    check can, and must."""
    sneaky = ReducePrompt(system="s", user_template="{{injected}} {{day_records}}",
                          template_name="t")
    out = render_user_prompt(sneaky, {"injected": "", "day_records": "[]"},
                             CONTRACT, BUILTIN_USER)
    assert "narrative" in out            # i.e. the built-in was substituted in


def test_unknown_placeholder_is_left_verbatim_not_raised():
    prompt = ReducePrompt(system="s", user_template=BUILTIN_USER + " {{mystery}}",
                          template_name="t")
    out = render_user_prompt(prompt, {"day_records": "[]"}, CONTRACT, BUILTIN_USER)
    assert "{{mystery}}" in out


# --------------------------------------------------------------------------- #
# The shipped templates must themselves satisfy their contracts
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name,contract", [
    ("cdd_reduce", NARRATIVE_CONTRACT),
    ("blueprint_reduce_worksheet", NARRATIVE_CONTRACT),
    ("patterns_notes_reduce", PATTERNS_CONTRACT),
])
def test_shipped_templates_satisfy_their_contracts(name, contract):
    """Ships-broken guard: if a shipped template violated its own contract, every
    generation would silently run on the built-in and the template would be dead
    weight that looks live."""
    from promptops_app.prompts.prompt_loader import load_template
    tpl = load_template(name)
    assert tpl.system_template.strip(), f"{name} has no SYSTEM half"
    assert contract.check(tpl.user_template) == [], f"{name} violates its contract"


def test_provenance_carries_a_verifiable_content_hash_per_layer():
    """A template_version label is only trustworthy for the DB tier (append-only,
    immutable). A file-tier template can be edited in place without the registry
    version moving, which would let an audit row assert a prompt identity whose
    text has since changed. The hash makes it verifiable, and is per-layer because
    the two halves resolve independently."""
    a = ReducePrompt(system="SYS one", user_template="USER {{day_records}}",
                     template_name="cdd_reduce", template_version="v2")
    b = ReducePrompt(system="SYS two", user_template="USER {{day_records}}",
                     template_name="cdd_reduce", template_version="v2")
    pa, pb = a.to_provenance(), b.to_provenance()
    assert pa["template_version"] == pb["template_version"], "the label cannot tell them apart"
    assert pa["system_sha"] != pb["system_sha"], "but the hash must"
    assert pa["user_template_sha"] == pb["user_template_sha"], "untouched half stays stable"
    # Same text -> same hash, regardless of which tier it came from.
    from_file = ReducePrompt(system="SYS one", user_template="x", system_source="file")
    assert from_file.to_provenance()["system_sha"] == pa["system_sha"]
    assert len(pa["system_sha"]) == 12


def test_the_narrative_user_contract_has_exactly_one_wording():
    """The USER half of the narrative prompt exists in THREE places — the CDD
    template file, the Blueprint template file, and the ``_DEFAULT_NARRATIVE_USER``
    code fallback — and all three describe the same six JSON keys the parser reads.

    They are identical today. Nothing structural keeps them that way: editing the
    contract in one file and not the others would silently diverge CDD from
    Blueprint (the label AIM's UI actually uses), or diverge both from the fallback
    that runs whenever a template is missing or fails its contract check. The
    failure would be a subtly-different deliverable, not an error. Lock it.
    """
    import re
    from pathlib import Path
    from promptops_app.services.block_wide_generator import _DEFAULT_NARRATIVE_USER

    def user_half(path):
        text = Path(path).read_text()
        assert "--- USER ---" in text, f"{path} lost its USER section"
        return re.sub(r"\s+", " ", text.split("--- USER ---", 1)[1]).strip()

    base = Path("promptops_app/prompts/templates")
    cdd = user_half(base / "cdd_reduce.md")
    blueprint = user_half(base / "blueprint_reduce_worksheet.md")
    code = re.sub(r"\s+", " ", _DEFAULT_NARRATIVE_USER).strip()

    assert cdd == blueprint, "cdd_reduce.md and blueprint_reduce_worksheet.md USER halves diverged"
    assert cdd == code, "the .md USER half and _DEFAULT_NARRATIVE_USER diverged"
