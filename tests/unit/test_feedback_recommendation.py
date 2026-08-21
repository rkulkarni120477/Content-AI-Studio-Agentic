"""
Unit tests for the feedback AI-recommendation logic.

These exercise the pure helpers and the ``recommend_for_items`` orchestration
without a database or a live LLM: the ``db`` is a MagicMock, the prompt build
is stubbed, and ``generate_with_metadata`` is patched to return canned results.

Why unit test the service directly? The business rules — relevance selection,
defensive JSON parsing, hallucinated-citation filtering, model-override
resolution, honest provenance, and per-item error isolation — all live here.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from promptops_app.core.models import DEFAULT_MODEL_NAME
from promptops_app.services import feedback_service as fs


def _fake_llm(text="", *, is_error=False, status="success", model="gpt-4o"):
    return SimpleNamespace(
        text=text, is_error=is_error, status=status, model=model, error_type=None,
    )


def _item(**kw):
    base = dict(
        id=1, course_id=None, blueprint_id=None, project_id=7,
        feedback_text="", theme=None, source_location=None,
        recommendation=None, recommendation_refs=None, recommendation_model=None,
        recommendation_status="none", recommended_at=None, recommended_by=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


# ── _select_relevant_blocks ───────────────────────────────────────────────────

def test_select_ranks_by_overlap():
    blocks = [
        ("Intro", "what is marketing exchange value " * 20),
        ("Pricing", "markup versus margin pricing strategy " * 20),
        ("Positioning", "nike adidas competitive positioning " * 20),
    ]
    item = _item(feedback_text="markup and margin used interchangeably", theme="Pricing")
    sel = fs._select_relevant_blocks(blocks, item, max_chars=5000)
    assert sel[0][0] == "Pricing"  # most token overlap wins


def test_select_falls_back_to_first_blocks_when_no_overlap():
    blocks = [("A", "zzz " * 10), ("B", "yyy " * 10)]
    item = _item(feedback_text="", theme=None, source_location=None)  # no query tokens
    sel = fs._select_relevant_blocks(blocks, item, max_chars=5000)
    assert [lbl for lbl, _ in sel] == ["A", "B"]  # deterministic original order


def test_select_respects_char_budget():
    blocks = [(f"B{i}", "x" * 5000) for i in range(20)]
    item = _item(feedback_text="anything")
    sel = fs._select_relevant_blocks(blocks, item, max_chars=12_000)
    assert 0 < len(sel) <= 3  # stops once the budget is exceeded


def test_select_caps_block_count():
    blocks = [(f"B{i}", "word " * 5) for i in range(50)]
    item = _item(feedback_text="")
    sel = fs._select_relevant_blocks(blocks, item)
    assert len(sel) <= fs._REC_MAX_BLOCKS


def test_select_empty_blocks():
    assert fs._select_relevant_blocks([], _item()) == []


# ── _parse_recommendation ─────────────────────────────────────────────────────

def test_parse_valid_json_filters_hallucinated_refs():
    raw = '{"recommendation":"Fix it.","referenced_blocks":["Pricing","MADE UP"]}'
    rec, refs = fs._parse_recommendation(raw, ["Pricing", "Intro"])
    assert rec == "Fix it."
    assert refs == ["Pricing"]  # unknown label dropped


def test_parse_refs_case_insensitive_dedup():
    raw = '{"recommendation":"x","referenced_blocks":["pricing","PRICING","Intro"]}'
    _rec, refs = fs._parse_recommendation(raw, ["Pricing", "Intro"])
    assert refs == ["Pricing", "Intro"]  # canonical label, no duplicates


def test_parse_raw_text_fallback_when_not_json():
    rec, refs = fs._parse_recommendation("just prose, no json", ["A"])
    assert rec == "just prose, no json"
    assert refs == []


def test_parse_bare_string_json():
    rec, refs = fs._parse_recommendation('"a plain string"', ["A"])
    assert rec == "a plain string"
    assert refs == []


# ── model resolution / provenance ─────────────────────────────────────────────

def test_resolve_valid_override_wins():
    course = SimpleNamespace(config_model_choice=None)
    assert fs._resolve_requested_model("Claude Haiku 4.5 (Bedrock)", course) == "Claude Haiku 4.5 (Bedrock)"


def test_resolve_unknown_override_falls_back():
    course = SimpleNamespace(config_model_choice=None)
    assert fs._resolve_requested_model("bogus", course) == DEFAULT_MODEL_NAME


def test_resolve_none_override_uses_course_default():
    course = SimpleNamespace(config_model_choice="Claude Sonnet 4.5 (Bedrock)")
    assert fs._resolve_requested_model(None, course) == "Claude Sonnet 4.5 (Bedrock)"


def test_actual_model_on_success_is_requested():
    assert fs._actual_model_display("GPT-5.4", _fake_llm(status="success")) == "GPT-5.4"


def test_actual_model_on_fallback_maps_provider_id():
    # requested a Bedrock model, but the service fell back to OpenAI gpt-4o
    result = _fake_llm(status="fallback_success", model="gpt-4o")
    assert fs._actual_model_display("Claude Haiku 4.5 (Bedrock)", result) == "GPT-5.4"


def test_format_guidance():
    assert fs._format_guidance("") == ""
    assert fs._format_guidance("   ") == ""
    out = fs._format_guidance("focus on assessments")
    assert "focus on assessments" in out and "REVIEWER" in out


# ── _load_blocks_for_item (module scoping with safe fallback) ─────────────────

@patch.object(fs, "_load_course_blocks", return_value=[("Course", "course-wide")])
@patch.object(fs, "_load_module_blocks")
def test_load_blocks_no_module_uses_whole_course(mock_module, mock_course):
    # Item not mapped to a module → whole-course context; module loader untouched.
    out = fs._load_blocks_for_item(MagicMock(), 5, None)
    assert out == [("Course", "course-wide")]
    mock_course.assert_called_once()
    mock_module.assert_not_called()


@patch.object(fs, "_load_course_blocks", return_value=[("Course", "course-wide")])
@patch.object(fs, "_load_module_blocks", return_value=[("Lesson 1", "module-only")])
def test_load_blocks_mapped_module_scopes_to_module(mock_module, mock_course):
    # Module has blocks → context is scoped to that module; course loader not used.
    out = fs._load_blocks_for_item(MagicMock(), 5, 42)
    assert out == [("Lesson 1", "module-only")]
    mock_module.assert_called_once()
    mock_course.assert_not_called()


@patch.object(fs, "_load_course_blocks", return_value=[("Course", "course-wide")])
@patch.object(fs, "_load_module_blocks", return_value=[])
def test_load_blocks_empty_module_falls_back_to_course(mock_module, mock_course):
    # Mapped to a module that has no usable blocks yet → fall back to whole course,
    # so scoping never leaves the model with nothing to work from.
    out = fs._load_blocks_for_item(MagicMock(), 5, 42)
    assert out == [("Course", "course-wide")]
    mock_module.assert_called_once()
    mock_course.assert_called_once()


# ── recommend_for_items orchestration ─────────────────────────────────────────

@patch.object(fs, "_build_recommendation_prompt", return_value=("sys", "user"))
@patch.object(fs, "generate_with_metadata")
@patch.object(fs, "_load_blocks_for_item", return_value=[("Lesson 1", "content")])
def test_recommend_scopes_blocks_by_item_course_and_module(mock_load, mock_gen, _mock_prompt):
    # The orchestrator must load blocks scoped to each item's (course, module),
    # not the whole course, when the item carries a blueprint_id.
    mock_gen.return_value = _fake_llm('{"recommendation":"ok"}', status="success")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(
        config_model_choice=None, cluster_id=None, name="C",
    )
    item = _item(id=1, course_id=9, blueprint_id=77, feedback_text="fb")
    fs.recommend_for_items(db, items=[item], created_by="e")
    mock_load.assert_called_once_with(db, 9, 77)
    assert item.recommendation == "ok"


@patch.object(fs, "_build_recommendation_prompt", return_value=("sys", "user"))
@patch.object(fs, "generate_with_metadata")
@patch.object(fs, "_load_blocks_for_item", return_value=[])
def test_recommend_caches_blocks_per_course_module(mock_load, mock_gen, _mock_prompt):
    # Two items, same course but different modules, must NOT share a scoped block
    # set — the cache is keyed by (course_id, blueprint_id), so each module loads
    # once. A third item repeating the first module reuses the cache (no reload).
    mock_gen.return_value = _fake_llm('{"recommendation":"ok"}', status="success")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(
        config_model_choice=None, cluster_id=None, name="C",
    )
    items = [
        _item(id=1, course_id=9, blueprint_id=1),
        _item(id=2, course_id=9, blueprint_id=2),
        _item(id=3, course_id=9, blueprint_id=1),  # same (course, module) as id=1
    ]
    fs.recommend_for_items(db, items=items, created_by="f")
    calls = {c.args for c in mock_load.call_args_list}
    assert calls == {(db, 9, 1), (db, 9, 2)}  # module 1 loaded once, reused for id=3
    assert mock_load.call_count == 2




@patch.object(fs, "_build_recommendation_prompt", return_value=("sys", "user"))
@patch.object(fs, "generate_with_metadata")
def test_recommend_sets_fields_on_success(mock_gen, _mock_prompt):
    mock_gen.return_value = _fake_llm(
        '{"recommendation":"**Do X.**","referenced_blocks":[]}', status="success",
    )
    item = _item(course_id=None, feedback_text="fb")
    fs.recommend_for_items(MagicMock(), items=[item], created_by="alice")
    assert item.recommendation == "**Do X.**"
    assert item.recommendation_status == "ready"
    assert item.recommendation_model == DEFAULT_MODEL_NAME
    assert item.recommended_by == "alice"
    assert item.recommended_at is not None


@patch.object(fs, "_build_recommendation_prompt", return_value=("sys", "user"))
@patch.object(fs, "generate_with_metadata")
def test_recommend_isolates_per_item_error(mock_gen, _mock_prompt):
    # first item's LLM call errors, second succeeds — batch must not abort
    mock_gen.side_effect = [
        _fake_llm(is_error=True, status="error"),
        _fake_llm('{"recommendation":"ok"}', status="success"),
    ]
    a, b = _item(id=1, course_id=None), _item(id=2, course_id=None)
    fs.recommend_for_items(MagicMock(), items=[a, b], created_by="bob")
    assert a.recommendation_status == "error"
    assert a.recommendation is None
    assert b.recommendation_status == "ready"
    assert b.recommendation == "ok"


@patch.object(fs, "_build_recommendation_prompt", return_value=("sys", "user"))
@patch.object(fs, "generate_with_metadata")
def test_recommend_honours_valid_model_override(mock_gen, _mock_prompt):
    mock_gen.return_value = _fake_llm('{"recommendation":"x"}', status="success", model="gpt-4o")
    item = _item(course_id=None)
    fs.recommend_for_items(
        MagicMock(), items=[item], created_by="c",
        model_override="Claude Sonnet 4.5 (Bedrock)",
    )
    # requested Sonnet and the (mocked) call "succeeded" as that model
    assert item.recommendation_model == "Claude Sonnet 4.5 (Bedrock)"


@patch.object(fs, "_build_recommendation_prompt", return_value=("sys", "user"))
@patch.object(fs, "generate_with_metadata")
def test_recommend_empty_text_marks_error(mock_gen, _mock_prompt):
    mock_gen.return_value = _fake_llm('{"recommendation":""}', status="success")
    item = _item(course_id=None)
    fs.recommend_for_items(MagicMock(), items=[item], created_by="d")
    assert item.recommendation_status == "error"
    assert item.recommendation is None
