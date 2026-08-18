"""No context or result is ever cut to fit a number we chose.

The rule, stated once so it can be enforced instead of remembered:

    Nothing that goes INTO a model, and nothing that comes OUT of one, may be
    truncated by a character or token count — unless the bound is the model's own
    limit. Selecting fewer whole items is fine and often right. Cutting a selected
    item is not.

Why the distinction is worth a test rather than a code review habit: a truncated
input is *indistinguishable from a complete one* to the model. It does not answer
"I only saw part of this" — it answers the question confidently from the fragment,
so a fact living past the cut is not merely missing, it is actively denied. That is
how a live regeneration reported "none of it covers what was asked" about a syllabus
whose relevant paragraph had been clipped, and how a 20-day calendar truncated at row
500 during ingestion looked, everywhere downstream, like a 500-row calendar.

The cost of the rule is real and accepted: prompts get larger, and a genuinely
oversized document can now fail a call instead of degrading. A loud failure is
recoverable; a confident wrong answer is not.

Out of scope, deliberately — these are not context or results:
  * database column widths (``[:255]`` on a title being stored)
  * UI previews, where the full text is fetched separately by design
  * log lines and error snippets
  * counts of how many whole items to select (top_k, supplement_k, MAX_DEEP_DAYS)
"""
from __future__ import annotations

import inspect
import re

import pytest

# Modules on the CDD/Blueprint context path, i.e. everything that assembles what a
# model reads or hands back what it wrote.
CONTEXT_MODULES = [
    "app.services.cdd_deep_context",
    "app.services.cdd_regen_context",
    "app.services.cdd_scoped_regen",
]


def _source(dotted: str) -> str:
    import importlib
    return inspect.getsource(importlib.import_module(dotted))


def _dis_on_path() -> None:
    """dis_backend is a separate deployable rooted at its own directory, so its
    ``from services...`` imports only resolve once it is on sys.path. Same
    bootstrap the other cross-deployable tests use."""
    import sys
    from pathlib import Path
    dis = str(Path(__file__).resolve().parents[2] / "dis_backend")
    if dis not in sys.path:
        sys.path.insert(0, dis)


@pytest.mark.parametrize("dotted", CONTEXT_MODULES)
def test_no_context_module_calls_a_truncation_helper(dotted):
    """``clip_tokens`` / ``clip_tokens_strict`` must not appear on these paths.

    ``clip_tokens`` is additionally a trap rather than a bound: it is governed by
    PROMPTOPS_TOKEN_LIMIT_ENABLED, which defaults to False and is set nowhere, so
    every budget expressed through it was advisory. Code that reads as bounded and
    behaves as unbounded is worse than either — one environment variable away from
    silently changing what every prompt contains.
    """
    src = _source(dotted)
    # Comments explaining the removal are fine; calls are not.
    calls = re.findall(r"^\s*(?!#).*\bclip_tokens(?:_strict)?\s*\(", src, re.MULTILINE)
    assert not calls, (
        f"{dotted} truncates context with {calls[0].strip()!r}. Select fewer whole "
        "documents instead, and name what was left out."
    )


def test_assemble_documents_omits_whole_documents_and_reports_them():
    """The one place a document can still be lost — and it must be lost WHOLE and
    named, never trimmed to fit."""
    import app.services.cdd_deep_context as D

    def _unit(name, n, text):
        return {"source_file_name": name, "unit_number": n, "text": text}

    units = [_unit("Kept.docx", 1, "alpha " * 500), _unit("Lost.docx", 1, "beta " * 500)]
    left_out: list = []
    text, sources = D.assemble_documents(units, token_budget=600, omitted=left_out)

    assert sources == ("Kept.docx",)
    assert left_out == ["Lost.docx"]
    # Partial inclusion is the failure being prevented: nothing of Lost.docx leaks in.
    assert "beta" not in text
    # And what IS included is complete.
    assert text.count("alpha") == 500


def test_the_search_budget_is_a_model_capacity_backstop_not_a_cost_knob():
    """SEARCH_TOKENS may only exclude whole documents, so it must sit far above the
    measured corpus rather than near it. Block 2 — the largest measured — is ~7,900
    tokens; a bound anywhere near that turns routine blocks into partial ones."""
    import app.services.cdd_deep_context as D

    assert D.SEARCH_TOKENS >= 100_000
    # DIS must never be the component that decides what to drop: it would drop by its
    # own ordering, before the block filter and whole-document regrouping run here.
    assert D.SEARCH_TOKEN_BUDGET > D.SEARCH_TOKENS


def test_map_asks_for_the_models_own_output_ceiling():
    """The DIS extractor's output cap must be model-derived. A flat number is wrong in
    both directions: it throttles the modern family and exceeds what legacy Claude 3
    ids accept."""
    _dis_on_path()
    from services.digests import mapper

    assert mapper.max_output_tokens("global.anthropic.claude-sonnet-5") == 64_000
    assert mapper.max_output_tokens("anthropic.claude-3-sonnet-20240229-v1:0") == 4_096
    # An unrecognised id is never MORE throttled than the previous flat behaviour.
    assert mapper.max_output_tokens("something-new") == mapper.MAP_MAX_TOKENS


def test_dis_output_ceilings_match_the_registry_cas_probed():
    """DIS's table must MIRROR CAS's, not hold a second opinion.

    The two deployables cannot share a module, so DIS restates numbers that
    promptops_app.core.models already carries from live probes. Restating invites
    drift, and drift here is silent — an under-guess just makes digests shorter.
    This caught a real one: Haiku 4.5 was written as 64,000 by grouping it with the
    rest of the modern family, where the probed value is 16,384.
    """
    _dis_on_path()
    from services.digests.mapper import _MAX_OUTPUT_TOKENS

    from promptops_app.core.models import MODEL_CATALOG

    cas = {m.api_model_id: m.max_output_tokens for m in MODEL_CATALOG
           if getattr(m, "api_model_id", None)}

    shared = set(cas) & set(_MAX_OUTPUT_TOKENS)
    assert shared, "no overlapping model ids — the mirror check is not running"
    mismatched = {mid: (_MAX_OUTPUT_TOKENS[mid], cas[mid])
                  for mid in shared if _MAX_OUTPUT_TOKENS[mid] != cas[mid]}
    assert not mismatched, (
        "dis_backend mapper._MAX_OUTPUT_TOKENS disagrees with CAS's probed registry "
        f"(dis, cas): {mismatched}"
    )


def test_dis_honours_a_large_token_budget_instead_of_clamping_it():
    """The caller knows which model the context is for; DIS does not.

    A magic `min(..., 20000)` in the packing call silently reduced a 200,000-token
    request to 20,000 and then dropped the units past that point BY ITS OWN RANKING
    — before the caller's block filtering and whole-document regrouping had any say.
    The caller then regrouped a subset and labelled it as documents. That is the
    worst shape of truncation: the result looks whole.
    """
    _dis_on_path()
    import inspect

    from services.context_retrieval import ContextRetrievalService

    src = inspect.getsource(ContextRetrievalService.retrieve)
    assert "20000" not in src, "the hardcoded 20k budget clamp is back"
    assert "token_budget_cap" in src, "the budget ceiling must come from tenant config"


def test_pack_units_reports_what_the_budget_removed():
    """Dropping whole units is acceptable; dropping them silently is not."""
    _dis_on_path()
    from services.context_retrieval import ContextRetrievalService

    svc = ContextRetrievalService.__new__(ContextRetrievalService)
    units = [
        {"content_unit_id": "a", "source_file_name": "Kept.docx", "text": "alpha " * 50},
        {"content_unit_id": "b", "source_file_name": "Dropped.docx", "text": "beta " * 5000},
    ]
    dropped: list = []
    selected, _used, _combined = svc._pack_units(
        units, top_k=10, token_budget=200, include_visual_summary=False, dropped=dropped)

    assert [u["content_unit_id"] for u in selected] == ["a"]
    assert [d["source_file_name"] for d in dropped] == ["Dropped.docx"], (
        "the packer skipped a unit without recording it; the caller cannot tell "
        "a whole document from one whose tail was packed away"
    )


def test_feedback_context_selects_whole_blocks():
    """Each block used to be sliced to 6,000 chars before the model read it, so a
    recommendation about a long block came from its opening and read as a
    recommendation about the block."""
    from promptops_app.services import feedback_service as F

    blocks = [("Long block", "x" * 20_000), ("Short block", "y" * 100)]
    selected = F._select_relevant_blocks(blocks, None, max_chars=50_000)
    by_label = dict(selected)
    assert len(by_label.get("Long block", "")) == 20_000, "block was cut before the model"


def test_map_sends_source_units_whole():
    """MAP_MAX_UNIT_CHARS=0 means a unit is never cut mid-text. When a day genuinely
    exceeds the model window, mapper drops the LEAST-confidently-attributed whole
    units and reports the drop — the same select-don't-cut discipline."""
    _dis_on_path()
    from services.digests import mapper

    assert mapper.MAP_MAX_UNIT_CHARS == 0
    long_unit = {"unit_type": "slide", "title": "t", "text_content": "x" * 50_000,
                 "attribution_signal": "raw:day_number"}
    body, dropped = mapper._source_body([long_unit], limit=None)
    assert body.count("x") == 50_000
    assert dropped == {}


def test_spreadsheet_and_json_extraction_keep_every_row():
    """Ingestion truncation is the worst kind: it is baked into the index, so no
    later fix recovers it and nothing downstream can tell the document was partial."""
    import io
    import json
    _dis_on_path()
    from services.pipeline import extractors

    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["day", "topic"])
    for i in range(1, 901):          # comfortably past the old 500-row cap
        ws.append([i, f"topic {i}"])
    buf = io.BytesIO()
    wb.save(buf)

    result = extractors.extract_xlsx(buf.getvalue())
    assert "topic 900" in result.text, "rows past 500 were dropped from the TEXT"
    assert "topic 501" in result.text

    payload = json.dumps([{"i": i} for i in range(900)]).encode()
    assert '"i": 899' in extractors.extract_json(payload).text


def test_one_oversized_block_does_not_discard_the_blocks_behind_it():
    """Regression on the fix itself.

    Selection used to `break` at the first block over budget. That was near enough
    to harmless while every block was pre-cut to 6,000 chars — almost nothing
    overran. Sending blocks whole makes an early 40,000-char block able to end
    selection and discard every smaller, still-relevant block behind it, which would
    have traded one truncation bug for a worse selection bug.
    """
    from promptops_app.services import feedback_service as F

    blocks = [
        ("Big", "x" * 4_000),
        ("Too big to fit", "y" * 9_000),
        ("Small", "z" * 200),
    ]
    selected = dict(F._select_relevant_blocks(blocks, None, max_chars=5_000))
    assert len(selected["Big"]) == 4_000, "included blocks are whole"
    assert "Too big to fit" not in selected, "skipped whole, never trimmed"
    # The point: the oversized block is skipped, NOT treated as a stop signal.
    assert "Small" in selected, "a `break` here would discard everything behind it"


def test_the_recommendation_budget_is_sized_so_one_block_cannot_crowd_out_the_rest():
    """The first block is always kept, so a budget near one block's size lets a
    single large block spend all of it — reintroducing the crowding-out that the
    per-block cut existed to prevent, as a selection problem instead."""
    from promptops_app.services import feedback_service as F

    assert F._REC_CONTEXT_CHARS >= 400_000
