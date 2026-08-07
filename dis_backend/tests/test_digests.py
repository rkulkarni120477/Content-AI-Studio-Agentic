"""Offline eval for the DIS digest tier (attribution + MAP + lazy build/cache).

Deterministic, no live infra (Postgres/OpenSearch/Bedrock all stubbed), so it is
safe for CI. The heavy end-to-end validation against the real dis_db + Bedrock is
kept out of CI (run manually).
"""
import json
import types

import pytest

from services.digests import attribution, mapper, build
from services import indexing


# --------------------------------------------------------------------------- #
# Attribution — the three deterministic signals + unresolved.
# --------------------------------------------------------------------------- #
def _days():
    return [
        {"day_number": 1, "topic": "Aircraft drawings and lines", "lesson_title": "Intro",
         "source_text": "", "assignments_json": json.dumps(["Project 2-1"]), "assessments_json": ""},
        {"day_number": 2, "topic": "Metal cutting tools", "lesson_title": "Tools",
         "source_text": "", "assignments_json": "", "assessments_json": json.dumps(["Quiz 1"])},
        {"day_number": 3, "topic": "Corrosion control and inspection", "lesson_title": "Corrosion",
         "source_text": "", "assignments_json": "", "assessments_json": ""},
    ]


def _attr(unit):
    days = _days()
    proj_ref, quiz_ref = attribution.build_calendar_refs(days)
    dterms = attribution.day_terms_by_day(days)
    return attribution.attribute(unit, days, proj_ref, quiz_ref, dterms)


def test_attribution_s1_item_cross_reference():
    unit = {"unit_type": "project_task", "title": "Project 2-1 layout",
            "text_content": "", "metadata_json": {}}
    placed, signal, conf = _attr(unit)
    assert placed == [1] and signal.startswith("S1") and conf == 1.0


def test_attribution_s1_quiz_reference():
    unit = {"unit_type": "quiz_question", "title": "Quiz 1 question 3",
            "text_content": "", "metadata_json": {}}
    placed, signal, _ = _attr(unit)
    assert placed == [2] and signal.startswith("S1")


def test_attribution_s2_filename_day_token():
    # Day token must end on a word boundary (space/dot), as real filenames do; a
    # token glued to more word chars (e.g. "B2D3_guide") intentionally does NOT
    # match and falls through to the S3 term-overlap signal.
    unit = {"unit_type": "guide_section", "title": "Guide",
            "text_content": "", "metadata_json": {"source_file_name": "B2D3.pptx"}}
    placed, signal, _ = _attr(unit)
    assert placed == [3] and signal.startswith("S2")


def test_attribution_s3_term_overlap():
    unit = {"unit_type": "guide_section", "title": "Corrosion inspection notes",
            "text_content": "corrosion control inspection procedures", "metadata_json": {}}
    placed, signal, _ = _attr(unit)
    assert placed == [3] and signal.startswith("S3")


def test_attribution_unresolved():
    unit = {"unit_type": "guide_section", "title": "zzz",
            "text_content": "unrelated gibberish qqq", "metadata_json": {}}
    placed, signal, conf = _attr(unit)
    assert placed == [] and signal == "unresolved" and conf == 0.0


# --------------------------------------------------------------------------- #
# MAP — deliverable-aware text gate + content-addressed cache key.
# --------------------------------------------------------------------------- #
@pytest.fixture
def stub_llm(monkeypatch):
    import services.pipeline.common as common

    def fake_call_llm(model, prompt, max_tokens=300):
        assert "ANSWERKEY_SECRET" not in prompt, "answer-key text leaked into MAP prompt"
        return ('{"derived_objective":"obj","misconceptions":["m"],'
                '"salient_excerpts":["e"],"concept_type":"Conceptual"}'), 100, 30
    monkeypatch.setattr(common, "call_llm", fake_call_llm)


def _tenant():
    return types.SimpleNamespace(
        pipeline=types.SimpleNamespace(models=types.SimpleNamespace(digest_extraction="stub-model")))


def test_build_digest_gate_and_metadata(stub_llm):
    day = {"day_number": 1, "topic": "Intro", "lesson_title": "L1"}
    units = [
        {"content_unit_id": "s1", "unit_type": "slide", "title": "s1",
         "text_content": "slide body", "content_hash": "a",
         "metadata_json": {"acs_codes": ["1a"]}},
        {"content_unit_id": "g1", "unit_type": "guide_section", "title": "g1",
         "text_content": "instructor guidance", "content_hash": "b",
         "metadata_json": {"visibility": "instructor_only"}},
        {"content_unit_id": "k1", "unit_type": "answer_key_item", "title": "k1",
         "text_content": "ANSWERKEY_SECRET answers", "content_hash": "c",
         "metadata_json": {"acs_codes": ["1b"], "is_answer_key": True}},
    ]
    digest = mapper.build_digest(day, units, _tenant(), model="stub-model",
                                 client_id="aim", block="Block 2")
    assert digest["digest_status"] == "ok"
    # ACS from ALL units (incl. the answer key's metadata).
    assert digest["acs_codes"] == ["1a", "1b"]
    # Instructor-guide text is allowed; only the answer-key unit's text is withheld.
    assert digest["text_withheld_units"] == 1


def test_missing_source_flag_suppressed_when_other_substantive_content_exists(stub_llm):
    """Regression: a real Block 2 day had a real source PDF (unit_type='page')
    but no unit typed exactly 'slide'/'guide_section' — 14/20 days hit this,
    each raising a "does slide material exist?" reviewer question despite real
    content existing, reading as noise rather than a genuine gap."""
    day = {"day_number": 1, "topic": "Intro", "lesson_title": "L1"}
    units = [{"content_unit_id": "p1", "unit_type": "page", "title": "p1",
              "text_content": "page body", "content_hash": "a", "metadata_json": {}}]
    digest = mapper.build_digest(day, units, _tenant(), model="stub-model",
                                 client_id="aim", block="Block 2")
    assert digest["source_availability"]["slide"] == "missing"
    assert digest["source_availability"]["guide_section"] == "missing"
    assert not any(f.startswith("MISSING_SOURCE") for f in digest["review_flags"])


def test_missing_source_flag_suppressed_for_non_substantive_unit_types_too(stub_llm):
    """The first attempt at this suppression checked only SUBSTANTIVE_UNIT_TYPES
    and under-suppressed live: several real Block 2 review days had files
    ingested as 'chunk' (outside that set) and still got the reviewer question
    despite Source Files Today already showing them. The check must match that
    column's own criterion — ANY non-calendar_day unit — not the narrower set."""
    day = {"day_number": 17, "topic": "Review", "lesson_title": "Review Day 1"}
    units = [{"content_unit_id": "c1", "unit_type": "chunk", "title": "B2D17 - Review - Day 1.pdf",
              "text_content": "review body", "content_hash": "a", "metadata_json": {}}]
    digest = mapper.build_digest(day, units, _tenant(), model="stub-model",
                                 client_id="aim", block="Block 2")
    assert not any(f.startswith("MISSING_SOURCE") for f in digest["review_flags"])


def test_missing_source_flag_still_fires_when_day_has_no_content_at_all(stub_llm):
    """A genuinely thin day (no substantive units of any type) must still raise
    MISSING_SOURCE — the suppression above must not silence real gaps."""
    day = {"day_number": 20, "topic": "", "lesson_title": ""}
    digest = mapper.build_digest(day, [], _tenant(), model="stub-model",
                                 client_id="aim", block="Block 2")
    flags = digest["review_flags"]
    assert "MISSING_SOURCE — slide" in flags
    assert "MISSING_SOURCE — guide_section" in flags


def test_build_digest_storyline_asset_status_is_mechanical_not_llm_guessed(stub_llm):
    """storyline_source_asset_status must come from whether a visual-bearing unit
    (slide/page) actually exists for the day — not an LLM guess, since the LLM
    only ever sees TEXT content and can't verify whether art exists."""
    day = {"day_number": 1, "topic": "Intro", "lesson_title": "L1"}
    no_visual = [{"content_unit_id": "g1", "unit_type": "guide_section", "title": "g1",
                  "text_content": "text", "content_hash": "a", "metadata_json": {}}]
    digest = mapper.build_digest(day, no_visual, _tenant(), model="stub-model",
                                 client_id="aim", block="Block 2")
    assert digest["storyline_source_asset_status"] == "NEEDS NEW ART"

    with_visual = no_visual + [{"content_unit_id": "s1", "unit_type": "slide", "title": "s1",
                                "text_content": "text", "content_hash": "b", "metadata_json": {}}]
    digest = mapper.build_digest(day, with_visual, _tenant(), model="stub-model",
                                 client_id="aim", block="Block 2")
    assert digest["storyline_source_asset_status"] == "AVAILABLE"


def test_build_digest_wires_through_new_llm_fields(monkeypatch):
    """concept_type_explanation/interactive_scope/job_aid_source_reference are new
    LLM-returned fields — confirm they flow from the raw JSON into the digest."""
    import services.pipeline.common as common

    def fake_call_llm(model, prompt, max_tokens=300):
        return ('{"derived_objective":"obj","misconceptions":[],"salient_excerpts":[],'
                '"concept_type":"Conceptual + Skill (Mixed)",'
                '"concept_type_explanation":"Day blends new theory with practice.",'
                '"interactive_candidate":true,"interactive_scope":"labeling a title block",'
                '"job_aid_candidate":true,"job_aid_source_reference":"FAA-H-8083-30B Ch. 4"}'), 100, 30
    monkeypatch.setattr(common, "call_llm", fake_call_llm)

    day = {"day_number": 1, "topic": "Intro", "lesson_title": "L1"}
    digest = mapper.build_digest(day, [], _tenant(), model="stub-model", client_id="aim", block="Block 2")
    assert digest["concept_type"] == "Conceptual + Skill (Mixed)"
    assert digest["concept_type_explanation"] == "Day blends new theory with practice."
    assert digest["interactive_scope"] == "labeling a title block"
    assert digest["job_aid_source_reference"] == "FAA-H-8083-30B Ch. 4"


def test_build_digest_new_llm_fields_default_sanely_when_omitted(stub_llm):
    """The stub_llm fixture's response has none of the new keys at all — they
    must default sanely, not crash, same discipline as every other optional LLM
    field in this schema."""
    day = {"day_number": 1, "topic": "Intro", "lesson_title": "L1"}
    digest = mapper.build_digest(day, [], _tenant(), model="stub-model", client_id="aim", block="Block 2")
    assert digest["concept_type_explanation"] == ""
    assert digest["interactive_scope"] == ""
    assert digest["job_aid_source_reference"] == "N/A"


def test_concept_type_prompt_gives_a_discriminating_rubric():
    """Regression: a real Block 2 run returned "Procedural" for all 20 days
    (including a Test day) because the prompt just listed 4 bare words with no
    criteria — the model wasn't discriminating, it was defaulting. The prompt
    must give each type a concrete one-line criterion."""
    # _llm_extract builds the prompt inline (no separate template function) —
    # inspect the source text itself for the rubric rather than executing a call.
    import inspect
    source = inspect.getsource(mapper._llm_extract)
    assert "Conceptual:" in source and "Procedural:" in source and "Metacognitive:" in source
    assert "Mixed" in source  # combined-type guidance for genuinely blended days


def test_concept_type_prompt_distinguishes_being_taught_from_doing():
    """Regression: a real Block 2 run classified Day 1 (identifying drawing
    elements — no bench task) and Day 5 (tool identification/purpose — no bench
    task) as "Skill"/"Procedural + Skill (Mixed)" against the AIM reference's
    "Conceptual" for both — the rubric didn't distinguish being TAUGHT ABOUT a
    procedure/tool from the learner actually DOING it. The prompt must say so
    explicitly, not just list the four original bare labels."""
    import inspect
    source = inspect.getsource(mapper._llm_extract).lower()
    assert "how a tool/technique/process is used" in source
    assert "hands-on task" in source


def test_concept_type_prompt_adds_summative_assessment_distinct_from_metacognitive():
    """Regression: AIM's reference labels a final/cumulative exam day "Summative
    Assessment", distinct from "Metacognitive" (which it reserves for
    review/reflection days with no graded test) — the prompt previously offered
    no such value, so the model's only fallback was Metacognitive for every kind
    of test/review day."""
    import inspect
    source = inspect.getsource(mapper._llm_extract)
    assert "Summative Assessment:" in source


def test_misconceptions_prompt_requires_empty_array_not_placeholder_string():
    """Regression: a real assessment-day digest returned misconceptions=["no"] —
    a placeholder INSIDE the array — which renders as a literal, wrong-looking
    list item downstream instead of the clean "NONE DOCUMENTED" default an
    empty array produces."""
    import inspect
    source = inspect.getsource(mapper._llm_extract)
    assert '"no"' in source and "placeholder" in source


def test_cache_key_content_addressed():
    day_units = [{"content_hash": "h1"}, {"content_hash": "h2"}]
    k1 = mapper.cache_key(1, day_units, "m")
    assert k1 == mapper.cache_key(1, day_units, "m")           # stable
    assert k1 != mapper.cache_key(1, day_units, "other-model")  # model-sensitive
    changed = [{"content_hash": "h1"}, {"content_hash": "CHANGED"}]
    assert k1 != mapper.cache_key(1, changed, "m")              # content-sensitive


def test_cache_key_is_map_guidance_sensitive():
    """Editing the course's selected prompt (which resolve_prompt_guidance
    distills into map_guidance) must invalidate previously cached digests —
    otherwise an admin edits the prompt, regenerates, and silently gets the
    stale pre-edit output with no error or indication anything is wrong."""
    day_units = [{"content_hash": "h1"}]
    k_none = mapper.cache_key(1, day_units, "m")
    assert k_none == mapper.cache_key(1, day_units, "m", map_guidance="")  # default == explicit ""
    k_guided = mapper.cache_key(1, day_units, "m", map_guidance="Emphasize hands-on tasks.")
    assert k_none != k_guided
    k_guided_edited = mapper.cache_key(1, day_units, "m", map_guidance="Emphasize safety procedures.")
    assert k_guided != k_guided_edited


def test_guidance_block_renders_nothing_for_empty_guidance():
    """No guidance must produce byte-identical prompt text to before this
    feature existed — not an empty-but-present instructional section."""
    assert mapper._guidance_block("") == ""
    assert mapper._guidance_block("   ") == ""
    assert mapper._guidance_block(None) == ""


def test_guidance_block_frames_guidance_as_additive_never_authoritative():
    block = mapper._guidance_block("Always name the specific AC number.")
    assert "Always name the specific AC number." in block
    assert "never add a field" in block.lower()
    assert "contradict the required json schema" in block.lower()


def test_llm_extract_prompt_carries_map_guidance_and_omits_it_when_absent(monkeypatch):
    """The guidance block must actually reach the LLM prompt when supplied, and
    the prompt must be unaffected (no stray empty section) when it is not."""
    import services.pipeline.common as common

    captured = {}

    def fake_call_llm(model, prompt, max_tokens=300):
        captured["prompt"] = prompt
        return ('{"derived_objective":"obj","misconceptions":[],"salient_excerpts":[],'
                '"concept_type":"Conceptual"}'), 100, 30
    monkeypatch.setattr(common, "call_llm", fake_call_llm)

    day = {"day_number": 1, "topic": "Intro", "lesson_title": "L1"}
    mapper.build_digest(day, [], _tenant(), model="stub-model", client_id="aim", block="Block 2",
                        map_guidance="Emphasize hands-on tasks explicitly.")
    assert "ADDITIONAL GENERATION GUIDANCE" in captured["prompt"]
    assert "Emphasize hands-on tasks explicitly." in captured["prompt"]
    # The fixed schema/rubric text must still be intact alongside it.
    assert "concept_type is one of" in captured["prompt"]

    mapper.build_digest(day, [], _tenant(), model="stub-model", client_id="aim", block="Block 2")
    assert "ADDITIONAL GENERATION GUIDANCE" not in captured["prompt"]


def test_llm_extract_prompt_spacing_is_byte_identical_with_no_guidance(monkeypatch):
    """Regression: the fixed schema block and the DAY/SOURCES block are built as
    two SEPARATELY dedented f-strings (to avoid a textwrap.dedent hazard — see
    _llm_extract's own comment), joined by `_guidance_block(map_guidance) or
    "\\n"`. A bare `+= _guidance_block(...)` (no `or "\\n"` fallback) silently
    swallowed the blank line that used to separate those two sections in the
    single-f-string version whenever there was no guidance — caught by actually
    diffing the assembled prompt, not by reading the source. Locks in the exact
    boundary text so this can't silently regress again."""
    import services.pipeline.common as common

    captured = {}

    def fake_call_llm(model, prompt, max_tokens=300):
        captured["prompt"] = prompt
        return '{"derived_objective":"obj","concept_type":"Conceptual"}', 100, 30
    monkeypatch.setattr(common, "call_llm", fake_call_llm)

    day = {"day_number": 1, "topic": "Intro", "lesson_title": "L1"}
    mapper.build_digest(day, [], _tenant(), model="stub-model", client_id="aim", block="Block 2")
    prompt = captured["prompt"]
    idx = prompt.index('*_candidate is false.')
    boundary = prompt[idx:idx + len('*_candidate is false.') + len("\n\nDAY 1:")]
    assert boundary == '*_candidate is false.\n\nDAY 1:'


def test_build_digest_records_whether_map_guidance_was_applied(stub_llm):
    day = {"day_number": 1, "topic": "Intro", "lesson_title": "L1"}
    without = mapper.build_digest(day, [], _tenant(), model="stub-model", client_id="aim", block="Block 2")
    assert without["map_guidance_applied"] is False

    with_guidance = mapper.build_digest(day, [], _tenant(), model="stub-model", client_id="aim",
                                        block="Block 2", map_guidance="Some real guidance.")
    assert with_guidance["map_guidance_applied"] is True
    assert with_guidance["cache_key"] != without["cache_key"]


# --------------------------------------------------------------------------- #
# Build orchestration — lazy build + cache (cold → warm → edit → force).
# --------------------------------------------------------------------------- #
def test_build_digests_lazy_cache(monkeypatch, stub_llm):
    from services.digests.enumerate import EnumerateResult

    units_by_day = {n: [{"content_unit_id": f"s{n}", "unit_type": "slide", "title": f"s{n}",
                         "text_content": "b", "content_hash": f"h{n}",
                         "metadata_json": {"acs_codes": [f"{n}a"]}}] for n in (1, 2, 3)}
    days = [{"day_number": n, "topic": f"T{n}", "lesson_title": f"L{n}"} for n in (1, 2, 3)]

    def fake_enumerate(tenant_cfg, block, client_id=""):
        return EnumerateResult(block=block, client_id="aim", calendar_id="c",
                               total_days=3, enumerated_days=3, days=days,
                               units_by_day=units_by_day, declared_acs=["1a", "2a", "3a"])

    store = {}
    monkeypatch.setattr(build, "enumerate_block", fake_enumerate)
    monkeypatch.setattr(indexing, "upsert_digest",
                        lambda cfg, d: store.__setitem__(d["digest_id"], d) or {"status": "completed"})
    monkeypatch.setattr(indexing, "fetch_digests",
                        lambda cfg, block, cid: [d for d in store.values() if d.get("block") == block])
    monkeypatch.setattr(indexing, "delete_digests",
                        lambda cfg, block, cid: store.clear() or {"status": "completed"})

    tenant = _tenant()
    cold = build.build_digests(tenant, "Block X")
    assert (cold["built"], cold["cached"], cold["map_calls"]) == (3, 0, 3)

    warm = build.build_digests(tenant, "Block X")
    assert (warm["built"], warm["cached"], warm["map_calls"]) == (0, 3, 0)

    units_by_day[3][0]["content_hash"] = "h3_CHANGED"
    edit = build.build_digests(tenant, "Block X")
    assert (edit["built"], edit["cached"]) == (1, 2)

    forced = build.build_digests(tenant, "Block X", force=True)
    assert (forced["built"], forced["cached"]) == (3, 0)

    status = build.digest_status(tenant, "Block X")
    assert status["fresh"] == 3 and status["up_to_date"] is True


def test_build_digests_rebuilds_all_days_when_map_guidance_changes(monkeypatch, stub_llm):
    """A warm cache must go cold the moment the resolved prompt guidance
    changes — otherwise an admin edits the course prompt, regenerates, and
    silently gets the pre-edit output with no error."""
    from services.digests.enumerate import EnumerateResult

    units_by_day = {n: [{"content_unit_id": f"s{n}", "unit_type": "slide", "title": f"s{n}",
                         "text_content": "b", "content_hash": f"h{n}",
                         "metadata_json": {}}] for n in (1, 2)}
    days = [{"day_number": n, "topic": f"T{n}", "lesson_title": f"L{n}"} for n in (1, 2)]

    def fake_enumerate(tenant_cfg, block, client_id=""):
        return EnumerateResult(block=block, client_id="aim", calendar_id="c",
                               total_days=2, enumerated_days=2, days=days,
                               units_by_day=units_by_day, declared_acs=[])

    store = {}
    monkeypatch.setattr(build, "enumerate_block", fake_enumerate)
    monkeypatch.setattr(indexing, "upsert_digest",
                        lambda cfg, d: store.__setitem__(d["digest_id"], d) or {"status": "completed"})
    monkeypatch.setattr(indexing, "fetch_digests",
                        lambda cfg, block, cid: [d for d in store.values() if d.get("block") == block])
    monkeypatch.setattr(indexing, "delete_digests",
                        lambda cfg, block, cid: store.clear() or {"status": "completed"})

    tenant = _tenant()
    cold = build.build_digests(tenant, "Block Y", map_guidance="Emphasize safety.")
    assert (cold["built"], cold["cached"]) == (2, 0)

    warm = build.build_digests(tenant, "Block Y", map_guidance="Emphasize safety.")
    assert (warm["built"], warm["cached"]) == (0, 2)  # same guidance → still cached

    guidance_changed = build.build_digests(tenant, "Block Y", map_guidance="Emphasize teamwork instead.")
    assert (guidance_changed["built"], guidance_changed["cached"]) == (2, 0)  # edited prompt busts cache

    no_guidance = build.build_digests(tenant, "Block Y")
    assert (no_guidance["built"], no_guidance["cached"]) == (2, 0)  # guidance removed also busts cache


# --------------------------------------------------------------------------- #
# Day-scoped structured retrieval (§7) — complete fetch + gate + bounded kNN.
# --------------------------------------------------------------------------- #
def test_day_context_structured_fetch(monkeypatch):
    from services.digests import day_scoped
    from services.digests.enumerate import EnumerateResult

    days = [
        {"day_number": 1, "topic": "Aircraft drawings", "lesson_title": "Intro",
         "week_number": 1, "source_text": "calendar note"},
        {"day_number": 2, "topic": "Corrosion", "lesson_title": "C", "source_text": ""},
    ]
    units_by_day = {
        1: [
            {"content_unit_id": "s1", "unit_type": "slide", "title": "Slide 1",
             "text_content": "slide body", "attribution_signal": "raw:day_number",
             "metadata_json": {"acs_codes": ["1a"]}},
            {"content_unit_id": "k1", "unit_type": "answer_key_item", "title": "Key",
             "text_content": "SECRET answers", "attribution_signal": "raw:day_number",
             "metadata_json": {"acs_codes": ["1b"], "is_answer_key": True}},
            {"content_unit_id": "g1", "unit_type": "guide_section", "title": "Guide",
             "text_content": "instructor-only guidance", "attribution_signal": "S1",
             "metadata_json": {"visibility": "instructor_only"}},
        ],
    }

    def fake_enumerate(tenant_cfg, block, client_id=""):
        return EnumerateResult(block=block, client_id="aim", calendar_id="c",
                               total_days=2, enumerated_days=2, days=days,
                               units_by_day=units_by_day,
                               acs_by_day={1: ["1a", "1b"]}, declared_acs=["1a", "1b"])

    monkeypatch.setattr(day_scoped, "enumerate_block", fake_enumerate)
    monkeypatch.setattr(indexing, "fetch_digests",
                        lambda cfg, block, cid: [{"day_number": 1, "topic": "Aircraft drawings",
                                                  "derived_objective": "Read drawings"}])
    monkeypatch.setattr(indexing, "embed_query", lambda cfg, text: [0.1, 0.2])
    # kNN returns one of the day's own units (must be excluded), a digest doc
    # (excluded), and one genuinely new page (kept).
    monkeypatch.setattr(indexing, "vector_search",
                        lambda cfg, cid, q, emb, size=40, allowed_job_ids=None: [
                            {"content_unit_id": "s1", "unit_type": "slide", "title": "own", "_score": 9.0},
                            {"content_unit_id": "dg", "unit_type": indexing.DIGEST_UNIT_TYPE, "title": "digest", "_score": 8.0},
                            {"content_unit_id": "p9", "unit_type": "page", "title": "Handbook p9",
                             "text": "x" * 5000, "day_number": 7, "_score": 7.0},
                        ])

    # Instructor audience: guide text allowed, answer-key text withheld.
    out = day_scoped.day_context(_tenant(), "Block 2", 1, client_id="aim", audience="instructor")
    assert out["day_number"] == 1 and out["unit_count"] == 3
    assert out["acs_codes"] == ["1a", "1b"]
    assert out["digest"] and out["digest"]["derived_objective"] == "Read drawings"
    by_id = {u["content_unit_id"]: u for u in out["units"]}
    assert by_id["s1"]["text"] == "slide body" and by_id["s1"]["text_withheld"] is False
    assert by_id["k1"]["text"] is None and by_id["k1"]["text_withheld"] is True      # answer key
    assert by_id["g1"]["text"] == "instructor-only guidance"                          # instructor OK
    # Supplement: own unit + digest excluded, only the new page kept, text capped.
    assert [s["content_unit_id"] for s in out["supplement"]] == ["p9"]
    assert len(out["supplement"][0]["text"]) == day_scoped._SUPPLEMENT_TEXT_CAP

    # Student audience: instructor-only guide text now withheld too.
    stu = day_scoped.day_context(_tenant(), "Block 2", 1, client_id="aim", audience="student")
    stu_by_id = {u["content_unit_id"]: u for u in stu["units"]}
    assert stu_by_id["g1"]["text"] is None and stu_by_id["g1"]["text_withheld"] is True

    # A day with no calendar row / no units → flagged, not crashed.
    empty = day_scoped.day_context(_tenant(), "Block 2", 2, client_id="aim", supplement_k=0)
    assert empty["unit_count"] == 0
    assert any(f.startswith("THIN_DAY:2") for f in empty["flags"])


# --------------------------------------------------------------------------- #
# CurriculumProfile seam (§5.5 / D8) — AIM profile #1 + registry selection.
# --------------------------------------------------------------------------- #
class _FakeCursor:
    """Minimal cursor that replays canned result sets per query keyword."""

    def __init__(self, calendars, days, units):
        self._calendars, self._days, self._units = calendars, days, units
        self._last = None

    def execute(self, sql, params=None):
        if "dis_course_calendars" in sql:
            self._last = self._calendars
        elif "dis_calendar_days" in sql:
            self._last = self._days
        elif "dis_content_units" in sql:
            self._last = self._units
        else:
            self._last = []

    def fetchall(self):
        return self._last


def test_curriculum_profile_registry_selection():
    from services.digests import profiles

    tenant = _tenant()
    # No structure_store marker → falls back to client id.
    assert type(profiles.get_curriculum_profile("aim", tenant)).__name__ == "AIMCurriculumProfile"
    # Unknown tenant → base profile (honest failure on load_scope, not mis-enumeration).
    base = profiles.get_curriculum_profile("cengage", tenant)
    assert type(base).__name__ == "CurriculumProfile"
    with pytest.raises(RuntimeError, match="not configured"):
        base.load_scope(None, "dis", "cengage", "Unit 1")

    # Explicit config marker wins over client id.
    tenant2 = types.SimpleNamespace(structure_store=types.SimpleNamespace(curriculum_profile="aim"))
    assert type(profiles.get_curriculum_profile("whoever", tenant2)).__name__ == "AIMCurriculumProfile"


def test_aim_profile_load_scope_and_coverage():
    from services.digests.profiles.aim import AIMCurriculumProfile

    calendars = [
        {"calendar_id": "cal_rich", "total_days": 2, "created_at": "2026-01-02", "day_rows": 2},
        {"calendar_id": "cal_thin", "total_days": 2, "created_at": "2026-01-01", "day_rows": 1},
    ]
    days = [
        {"calendar_day_id": "d1", "day_number": 1, "topic": "Intro"},
        {"calendar_day_id": "d2", "day_number": 2, "topic": "Methods"},
    ]
    units = [{"content_unit_id": "u1", "unit_type": "slide",
              "metadata_json": {"acs_codes": ["1a", "", "1b"]}}]

    prof = AIMCurriculumProfile(_tenant())
    scope = prof.load_scope(_FakeCursor(calendars, days, units), "dis", "aim", "Block 2")
    # Canonical = richest calendar; the other is flagged as a duplicate.
    assert scope.calendar_id == "cal_rich"
    assert scope.duplicate_calendar_ids == ["cal_thin"]
    assert scope.total_days == 2 and len(scope.days) == 2 and len(scope.units) == 1
    # Coverage codes drop blanks; label is ACS for AIM.
    assert prof.coverage_codes(units[0]) == ["1a", "1b"]
    assert prof.coverage_label == "ACS"

    # No calendar rows → LookupError (block not found), not a silent empty.
    with pytest.raises(LookupError):
        prof.load_scope(_FakeCursor([], [], []), "dis", "aim", "Nope")


# --------------------------------------------------------------------------- #
# LangGraph Send fan-out (D4) — real graph, offline; parity with sequential.
# --------------------------------------------------------------------------- #
def test_build_digests_graph_fanout(monkeypatch, stub_llm):
    from services.digests import graph as digest_graph
    from services.digests.enumerate import EnumerateResult

    if not digest_graph.langgraph_available():
        pytest.skip("langgraph not installed")

    units_by_day = {n: [{"content_unit_id": f"s{n}", "unit_type": "slide", "title": f"s{n}",
                         "text_content": "b", "content_hash": f"h{n}",
                         "metadata_json": {"acs_codes": [f"{n}a"]}}] for n in (1, 2, 3, 4)}
    days = [{"day_number": n, "topic": f"T{n}", "lesson_title": f"L{n}"} for n in (1, 2, 3, 4)]

    def fake_enumerate(tenant_cfg, block, client_id=""):
        return EnumerateResult(block=block, client_id="aim", calendar_id="c",
                               total_days=4, enumerated_days=4, days=days,
                               units_by_day=units_by_day, declared_acs=["1a", "2a", "3a", "4a"])

    store = {}
    monkeypatch.setattr(build, "enumerate_block", fake_enumerate)
    monkeypatch.setattr(indexing, "upsert_digest",
                        lambda cfg, d: store.__setitem__(d["digest_id"], d) or {"status": "completed"})
    monkeypatch.setattr(indexing, "fetch_digests",
                        lambda cfg, block, cid: [d for d in store.values() if d.get("block") == block])
    monkeypatch.setattr(indexing, "delete_digests",
                        lambda cfg, block, cid: store.clear() or {"status": "completed"})

    tenant = _tenant()

    # Cold via the graph: all 4 days fan out, none cached.
    cold = build.build_digests(tenant, "Block G", use_graph=True, max_concurrency=3)
    assert cold["strategy"] == "langgraph_send"
    assert (cold["built"], cold["cached"], cold["failed"]) == (4, 0, 0)
    assert cold["map_calls"] == 4                      # one MAP call per day, no shared-budget race
    assert [p["day_number"] for p in cold["per_day"]] == [1, 2, 3, 4]  # reducer merge, sorted

    # Warm via the graph: shared cache decision ⇒ every day served from cache.
    warm = build.build_digests(tenant, "Block G", use_graph=True)
    assert (warm["built"], warm["cached"], warm["map_calls"]) == (0, 4, 0)

    # One edited day ⇒ exactly that day rebuilds; the graph and loop agree.
    units_by_day[2][0]["content_hash"] = "h2_CHANGED"
    edit = build.build_digests(tenant, "Block G", use_graph=True)
    assert (edit["built"], edit["cached"]) == (1, 3)
    assert next(p for p in edit["per_day"] if p["day_number"] == 2)["status"] == "built"


def test_make_checkpointer_safe_default():
    """D7 seam: a blank DSN must yield no checkpointer and open no connection —
    dev containers point at prod RDS, so the checkpointer must never connect there."""
    from services.digests import graph as digest_graph
    assert digest_graph.make_checkpointer("") is None
    assert digest_graph.make_checkpointer("   ") is None


# --------------------------------------------------------------------------- #
# Review regression fixes — §8.4 supplement gate (C1), phantom-day drop (M3),
# broader student gate (M6), topic-aware cache_key (M4).
# --------------------------------------------------------------------------- #
def test_supplement_gate_blocks_restricted_and_answer_key(monkeypatch):
    """C1: the kNN day-supplement must apply the §8.4 text gate — answer-key and
    instructor-only hits must never leak their text (esp. for student audience)."""
    from services.digests import day_scoped
    from services.digests.enumerate import EnumerateResult

    days = [{"day_number": 1, "topic": "T", "lesson_title": "L", "source_text": ""}]
    monkeypatch.setattr(day_scoped, "enumerate_block",
                        lambda cfg, block, client_id="": EnumerateResult(
                            block=block, client_id="aim", calendar_id="c", total_days=1,
                            enumerated_days=1, days=days, units_by_day={1: []},
                            acs_by_day={1: ["1a"]}, declared_acs=["1a"]))
    monkeypatch.setattr(day_scoped.indexing, "fetch_digests", lambda cfg, b, c: [])
    monkeypatch.setattr(day_scoped.indexing, "embed_query", lambda cfg, t: [0.1])
    monkeypatch.setattr(day_scoped.indexing, "vector_search",
                        lambda cfg, cid, q, emb, size=40, allowed_job_ids=None: [
                            {"content_unit_id": "ak", "unit_type": "answer_key_item",
                             "title": "Key", "text": "SECRET", "metadata_json": {}, "metadata": {"is_answer_key": True}},
                            {"content_unit_id": "io", "unit_type": "page", "title": "Internal",
                             "text": "internal notes", "metadata": {"visibility": "internal_only"}},
                            {"content_unit_id": "ok", "unit_type": "page", "title": "Public page",
                             "text": "public handbook", "metadata": {}},
                        ])
    out = day_scoped.day_context(_tenant(), "Block 2", 1, client_id="aim", audience="student")
    ids = {s["content_unit_id"] for s in out["supplement"]}
    assert ids == {"ok"}, f"restricted hits leaked into supplement: {ids}"
    assert all("SECRET" not in (s.get("text") or "") for s in out["supplement"])


def test_enumerate_phantom_day_not_dropped():
    """M3: a unit whose (raw or attributed) day isn't a real calendar day must be
    surfaced in `unattributed`, never silently lost from digests + coverage."""
    from services.digests.enumerate import _assemble
    from services.digests.profiles.aim import AIMCurriculumProfile
    from services.digests.profiles.base import ScopeData

    days = [{"day_number": 1, "topic": "T1", "assignments_json": "", "assessments_json": "", "source_text": ""}]
    units = [
        {"content_unit_id": "good", "unit_type": "slide", "title": "s",
         "metadata_json": {"day_number": 1, "acs_codes": ["1a"]}},
        {"content_unit_id": "phantom", "unit_type": "slide", "title": "p",
         "metadata_json": {"day_number": 9, "acs_codes": ["9a"]}},  # day 9 not in calendar
    ]
    scope = ScopeData(calendar_id="c", total_days=1, days=days, units=units)
    res = _assemble("Block 2", "aim", AIMCurriculumProfile(_tenant()), scope)
    assert "good" in {u["content_unit_id"] for u in res.units_by_day.get(1, [])}
    assert "phantom" in {u["content_unit_id"] for u in res.unattributed}
    assert 9 not in res.units_by_day
    assert "9a" not in res.acs_by_day.get(1, [])  # phantom ACS not miscounted onto day 1


def test_student_gate_covers_internal_only():
    """M6: the student text gate must withhold internal_only/admin_only, not only
    the literal instructor_only."""
    for vis in ("instructor_only", "internal_only", "internal", "admin_only", "restricted_admin"):
        unit = {"unit_type": "page", "metadata_json": {"visibility": vis}}
        assert mapper.text_allowed_for_digest(unit, "student") is False, vis
        assert mapper.text_allowed_for_digest(unit, "instructor") is True, vis
    # access_level admin_only likewise withheld for students.
    assert mapper.text_allowed_for_digest(
        {"unit_type": "page", "metadata_json": {"access_level": "admin_only"}}, "student") is False


def test_cache_key_invalidates_on_topic_edit():
    """M4: editing a day's topic/lesson_title must change cache_key even when no
    source unit changed."""
    units = [{"content_hash": "h1"}]
    day_a = {"day_number": 1, "topic": "Old topic", "lesson_title": "L"}
    day_b = {"day_number": 1, "topic": "New topic", "lesson_title": "L"}
    k_a = mapper.cache_key(1, units, "m", day_meta=mapper.day_signature(day_a))
    k_b = mapper.cache_key(1, units, "m", day_meta=mapper.day_signature(day_b))
    assert k_a != k_b
    # Same topic → stable.
    assert k_a == mapper.cache_key(1, units, "m", day_meta=mapper.day_signature(dict(day_a)))


def test_build_digest_never_truncates_long_prose_fields(stub_llm, monkeypatch):
    """Regression: the digest used to run every field through `_enforce_cap`,
    which truncated derived_objective/interactive_content/job_aid_description
    to as little as 60 characters on any content-rich day. Per explicit user
    request, digests must return their full, untruncated content — no cap."""
    import services.pipeline.common as common
    long_text = "This is a genuinely long, detailed narrative field. " * 40

    def fake_call_llm(model, prompt, max_tokens=300):
        return (
            '{"derived_objective": "%s", "misconceptions": [], "salient_excerpts": [], '
            '"concept_type": "Conceptual"}' % long_text
        ), 100, 30
    monkeypatch.setattr(common, "call_llm", fake_call_llm)

    day = {"day_number": 1, "topic": "Intro", "lesson_title": "L1"}
    digest = mapper.build_digest(day, [], _tenant(), model="stub-model",
                                 client_id="aim", block="Block 2")
    assert digest["derived_objective"] == long_text
    assert len(digest["derived_objective"]) > 500
