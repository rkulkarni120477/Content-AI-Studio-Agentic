"""The restricted-material policy is defined in four places. This makes them agree.

CAS and DIS are separate deployables — ``app/`` cannot import ``dis_backend/`` at
runtime and vice versa — so the rule "answer keys and instructor guides never seed
generated content" is necessarily restated on both sides:

* ``ContextRetrievalService._restricted_doc_types`` (DIS) — the enforcing gate.
  Config list UNION a built-in floor, so protection never depends on a client's
  YAML being complete.
* ``worksheets._DEFAULT_RESTRICTED_DOC_TYPES`` (DIS) — drives the
  ``production_action`` a delivered worksheet prints. Not a gate, but it is an
  INSTRUCTION to a production team, and "Include in input bundle" printed next to
  an instructor guide is the same mistake with a human executing it.
* ``cdd_deep_context.RESTRICTED_DOC_TYPES`` (CAS) — what regeneration may ASK for
  when an instruction widens the document-type gate.
* ``mapper.text_allowed_for_digest`` (DIS) — the digest-tier text gate. Keyed on
  unit_type/flags rather than document_type, so it is checked for behaviour rather
  than set equality.

Three of those four carried a "kept in sync with ..." comment and nothing else.
A comment is not an invariant: when this was written, ``worksheets`` held two of
the seven families the retrieval gate blocks, so five could be recommended into a
student-facing bundle. These tests fail when the definitions drift again.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


def _dis_on_path() -> None:
    """Same bootstrap the other cross-deployable tests use (test_map_cost_accounting)."""
    dis = str(Path(__file__).resolve().parents[2] / "dis_backend")
    if dis not in sys.path:
        sys.path.insert(0, dis)


class _NoDocProcessing:
    """A tenant config carrying no restricted_document_types.

    Passing this to the gate isolates its BUILT-IN floor from whatever any single
    client's YAML happens to add — the floor is the part CAS has to mirror, because
    CAS never sees the tenant's list.
    """
    document_processing = None
    client_rules: dict = {}


def _dis_builtin_floor() -> set:
    _dis_on_path()
    from services.context_retrieval import ContextRetrievalService

    svc = ContextRetrievalService.__new__(ContextRetrievalService)
    svc.tenant_cfg = _NoDocProcessing()
    return {str(t).strip().lower() for t in svc._restricted_doc_types()}


def test_cas_never_requests_a_type_dis_treats_as_restricted():
    """CAS's list must COVER the DIS floor.

    CAS decides what regeneration asks for. DIS would still refuse a restricted type
    (defence in depth holds either way), but a CAS list missing a family means an
    instruction naming it spends a retrieval, opens the purpose gate for it, and
    returns nothing — a paid no-op the user cannot diagnose. It also removes the
    only guard against a future _DOC_TYPE_HINTS edit quietly mapping a phrase onto
    an answer key.
    """
    from app.services.cdd_deep_context import RESTRICTED_DOC_TYPES

    cas = {t.strip().lower() for t in RESTRICTED_DOC_TYPES}
    missing = _dis_builtin_floor() - cas
    assert not missing, (
        "app/services/cdd_deep_context.py RESTRICTED_DOC_TYPES is missing document "
        f"types the DIS retrieval gate blocks by construction: {sorted(missing)}. "
        "Add them there, or CAS will spend retrievals asking for material DIS refuses."
    )


def test_the_worksheet_label_covers_everything_the_gate_blocks():
    """A worksheet must not advise INTO a bundle what retrieval refuses to serve.

    ``production_action`` is read by a human assembling student-facing material.
    When this test was written the constant held {quiz_answer_key, project_key}
    against a seven-family floor.
    """
    _dis_on_path()
    from services.digests.worksheets import _DEFAULT_RESTRICTED_DOC_TYPES

    labelled = {str(t).strip().lower() for t in _DEFAULT_RESTRICTED_DOC_TYPES}
    missing = _dis_builtin_floor() - labelled
    assert not missing, (
        "worksheets._DEFAULT_RESTRICTED_DOC_TYPES omits types the retrieval gate "
        f"blocks: {sorted(missing)} — the Source File Inventory would print "
        "'Include in input bundle' for them."
    )


def test_a_tenant_list_adds_to_the_floor_and_never_replaces_it():
    """The union, not the override.

    aim.yaml lists two types. Reading it verbatim (the previous behaviour) silently
    narrowed the worksheet's notion of restricted to those two.
    """
    _dis_on_path()
    from services.digests import worksheets

    class _Cfg:
        class document_processing:  # noqa: N801 — mirrors the real attribute path
            restricted_document_types = ["quiz_answer_key", "project_key"]

    en = _StubEnumerate(units_by_day={}, unattributed=[
        _unit("instructor_guide", "Block 1 Project 1-1 Instructor Notes.docx"),
        _unit("slide_deck", "Block 1 Lesson 3.pptx"),
    ])
    rows = worksheets.build_source_file_inventory(en, _Cfg(), cur=None)
    by_type = {r["document_type"]: r for r in rows}

    assert by_type["instructor_guide"]["production_action"].startswith("Instructor-only"), (
        "instructor_guide is not in aim.yaml's list but IS in the built-in floor; "
        "the worksheet must still mark it instructor-only"
    )
    assert by_type["slide_deck"]["production_action"] == "Include in input bundle"


def test_doc_type_matching_is_case_folded():
    """The classifier's casing must not decide whether material is marked restricted."""
    _dis_on_path()
    from services.digests import worksheets

    en = _StubEnumerate(units_by_day={}, unattributed=[
        _unit("Instructor_Guide", "Weird Casing Guide.docx"),
    ])
    rows = worksheets.build_source_file_inventory(en, None, cur=None)
    assert rows, "the unit should have produced an inventory row"
    assert rows[0]["production_action"].startswith("Instructor-only"), (
        f"{rows[0]['document_type']!r} was matched case-sensitively and printed "
        f"{rows[0]['production_action']!r}"
    )


def test_answer_key_text_never_enters_a_digest_at_any_audience():
    """The digest gate is keyed on unit_type/flags, so assert behaviour, not sets.

    aim.yaml maps the quiz_answer_key / final_exam_answer_key DOCUMENT types onto the
    ``answer_key_item`` UNIT type, which is what links this gate to the doc-type sets
    above. That mapping is the join; if it is ever removed, this stays green while
    real answer-key text starts flowing, so it is asserted separately below.
    """
    _dis_on_path()
    from services.digests import mapper

    key_unit = {"unit_type": "answer_key_item", "metadata_json": {}}
    flagged = {"unit_type": "guide_section", "metadata_json": {"is_answer_key": True}}
    for audience in ("instructor", "student"):
        assert mapper.text_allowed_for_digest(key_unit, audience) is False
        assert mapper.text_allowed_for_digest(flagged, audience) is False


def test_the_doc_type_to_unit_type_mapping_that_links_the_two_gates_still_exists():
    """Answer-key DOCUMENT types must still land on the answer-key UNIT type.

    Without this, a document typed ``quiz_answer_key`` would be chunked into ordinary
    units and the digest gate — which never sees document_type — would pass its text
    straight into a MAP prompt.
    """
    _dis_on_path()
    import yaml

    cfg_dir = Path(__file__).resolve().parents[2] / "dis_backend" / "config" / "clients"
    checked = 0
    for path in sorted(cfg_dir.glob("*.yaml")):
        mapping = ((yaml.safe_load(path.read_text()) or {})
                   .get("document_processing", {}) or {}).get("unit_type_map", {}) or {}
        for doc_type, unit_type in mapping.items():
            if "answer_key" in str(doc_type) or str(doc_type).endswith("_key"):
                checked += 1
                assert unit_type == "answer_key_item", (
                    f"{path.name}: document type {doc_type!r} maps to unit type "
                    f"{unit_type!r}, so mapper.text_allowed_for_digest will not "
                    "recognise it as answer-key material"
                )
    if not checked:
        pytest.skip("no answer-key document types configured in any client yaml")


# ── stubs ────────────────────────────────────────────────────────────────────

def _unit(doc_type: str, file_name: str) -> dict:
    return {"unit_type": "chunk", "title": file_name,
            "metadata_json": {"document_type": doc_type, "source_file_name": file_name}}


class _StubEnumerate:
    """The two attributes build_source_file_inventory reads, and nothing else."""

    def __init__(self, units_by_day, unattributed):
        self.units_by_day = units_by_day
        self.unattributed = unattributed
        self.client_id = "aim"
        self.block = "Block 1"
