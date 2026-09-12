"""Unit tests for batched per-page LLM content tagging."""
from __future__ import annotations

import json

from services.ebook_page_tagger import tag_ebook_page_units, validate_acs_codes


def test_validate_acs_codes_drops_invalid():
    assert validate_acs_codes(["AM.I.D.K1", "NOT-A-CODE", "am.ii.a.r2", ""]) == [
        "AM.I.D.K1", "AM.II.A.R2",
    ]


def _units(n: int = 3):
    out = []
    for i in range(1, n + 1):
        out.append({
            "content_unit_id": f"j:page_{i}",
            "unit_number": i,
            "text": f"Page {i} covers landing gear AM.I.D.K1 details.",
            "topics": ["landing"],
            "keywords": ["landing"],
            "metadata": {"pdf_page": i, "page_number": f"13-{i}", "chapter": 13},
        })
    return out


def test_tagger_applies_batch_response():
    units = _units(2)

    def fake_llm(model, prompt, max_tokens=1200):
        payload = [
            {"pdf_page": 1, "topics": ["oleo strut", "servicing"],
             "acs_codes": ["AM.I.D.K1", "BOGUS"], "summary": "Oleo strut basics."},
            {"pdf_page": 2, "topics": ["brakes"],
             "acs_codes": ["AM.I.D.R1"], "summary": "Brake systems."},
        ]
        return json.dumps(payload), 10, 20

    tag_ebook_page_units(
        units, call_llm_fn=fake_llm, model_id="test-model", batch_size=10, enabled=True,
    )
    assert units[0]["topics"] == ["oleo strut", "servicing"]
    assert units[0]["metadata"]["acs_codes"] == ["AM.I.D.K1"]  # BOGUS dropped
    assert "Oleo" in units[0]["metadata"]["summary"]
    assert units[1]["metadata"]["acs_codes"] == ["AM.I.D.R1"]
    assert units[0]["metadata"]["tagging_status"] == "ok"
    assert units[1]["metadata"]["tagging_status"] == "ok"


def test_tagger_fail_soft_keeps_heuristic_topics():
    units = _units(1)
    original_topics = list(units[0]["topics"])

    def boom(model, prompt, max_tokens=1200):
        raise RuntimeError("bedrock down")

    errors = []
    tag_ebook_page_units(
        units, call_llm_fn=boom, model_id="test-model",
        batch_size=10, enabled=True, errors=errors,
    )
    assert units[0]["topics"] == original_topics
    assert "acs_codes" not in units[0]["metadata"]
    assert units[0]["metadata"]["tagging_status"] == "failed"
    assert errors and "bedrock down" in errors[0]


def test_tagger_batches_calls():
    units = _units(12)
    calls = []

    def fake_llm(model, prompt, max_tokens=1200):
        calls.append(prompt)
        # Return empty array — still counts as a successful parse.
        return "[]", 1, 1

    tag_ebook_page_units(
        units, call_llm_fn=fake_llm, model_id="m", batch_size=10, enabled=True,
    )
    assert len(calls) == 2  # 10 + 2


def test_tagger_accepts_pages_wrapper_dict():
    units = _units(1)

    def fake_llm(model, prompt, max_tokens=1200):
        return json.dumps({
            "pages": [{
                "pdf_page": 1, "topics": ["hydraulics"],
                "acs_codes": ["AM.I.D.K1"], "summary": "Hydraulics overview.",
            }],
        }), 5, 5

    tag_ebook_page_units(
        units, call_llm_fn=fake_llm, model_id="m", batch_size=10, enabled=True,
    )
    assert units[0]["metadata"]["tagging_status"] == "ok"
    assert units[0]["topics"] == ["hydraulics"]


def test_tagger_accepts_results_wrapper_and_page_keyed_dict():
    from services.ebook_page_tagger import _normalize_tag_response

    wrapped = _normalize_tag_response({
        "results": [{"pdf_page": 2, "topics": ["a"], "acs_codes": [], "summary": "s"}],
    })
    assert wrapped[0]["pdf_page"] == 2

    keyed = _normalize_tag_response({
        "1": {"topics": ["t1"], "acs_codes": ["AM.I.A.K1"], "summary": "one"},
        "page_2": {"topics": ["t2"], "acs_codes": [], "summary": "two"},
    })
    by_page = {int(x["pdf_page"]): x for x in keyed}
    assert by_page[1]["topics"] == ["t1"]
    assert by_page[2]["topics"] == ["t2"]

    units = _units(2)

    def fake_llm(model, prompt, max_tokens=1200):
        return json.dumps({
            "1": {"topics": ["oleo"], "acs_codes": ["AM.I.D.K1"], "summary": "A"},
            "2": {"topics": ["brakes"], "acs_codes": [], "summary": "B"},
        }), 5, 5

    tag_ebook_page_units(
        units, call_llm_fn=fake_llm, model_id="m", batch_size=10, enabled=True,
    )
    assert units[0]["metadata"]["tagging_status"] == "ok"
    assert units[1]["metadata"]["tagging_status"] == "ok"
    assert units[0]["topics"] == ["oleo"]


def test_tagger_rejects_unusable_dict():
    units = _units(1)
    errors = []

    def fake_llm(model, prompt, max_tokens=1200):
        return json.dumps({"status": "ok", "message": "no tags here"}), 1, 1

    tag_ebook_page_units(
        units, call_llm_fn=fake_llm, model_id="m", batch_size=10, enabled=True, errors=errors,
    )
    assert units[0]["metadata"]["tagging_status"] == "failed"
    assert errors and "got dict" in errors[0]


def test_parse_tag_json_recovers_truncated_array():
    from services.ebook_page_tagger import _parse_tag_json, _normalize_tag_response

    # Truncated mid-object — same failure mode as max_tokens=1200 cutoffs.
    raw = (
        '[{"pdf_page": 1, "topics": ["a"], "acs_codes": [], "summary": "one"},'
        '{"pdf_page": 2, "topics": ["b"], "acs_codes": [], "summary": "tw'
    )
    parsed = _normalize_tag_response(_parse_tag_json(raw))
    assert [x["pdf_page"] for x in parsed] == [1]


def test_parse_tag_json_extracts_array_after_preamble():
    from services.ebook_page_tagger import _parse_tag_json, _normalize_tag_response

    raw = 'Here you go:\n[{"pdf_page": 3, "topics": ["c"], "acs_codes": [], "summary": "s"}]\n'
    parsed = _normalize_tag_response(_parse_tag_json(raw))
    assert parsed[0]["pdf_page"] == 3


def test_tagger_rejects_empty_dict_from_blank_parse():
    units = _units(1)
    errors = []

    def fake_llm(model, prompt, max_tokens=1200):
        return "not json at all {{{", 1, 1

    tag_ebook_page_units(
        units, call_llm_fn=fake_llm, model_id="m", batch_size=10, enabled=True, errors=errors,
    )
    assert units[0]["metadata"]["tagging_status"] == "failed"
    assert errors and ("unparseable" in errors[0] or "empty" in errors[0])