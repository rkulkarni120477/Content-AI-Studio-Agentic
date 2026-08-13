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


def _rendered_map_prompt(guidance: str = "", sources: str = "(no units)") -> str:
    """The MAP prompt as actually assembled and sent.

    The rubric used to live in an f-string inside ``_llm_extract``, so these
    regression tests read it with ``inspect.getsource``. It now resolves from
    ``templates/digest_map.md`` (with the JSON contract injected as ``{{schema}}``),
    so rendering the real thing is both the only way to see it AND a stronger
    assertion than source-matching ever was: it exercises template resolution, the
    contract check, and substitution, catching a template that fails to load or a
    placeholder that never gets filled — none of which source-matching could see.
    """
    template, _hash = mapper.map_prompt()
    return mapper.prompt_template_render(template, {
        "schema": mapper.MAP_SCHEMA,
        "guidance_block": mapper._guidance_block(guidance) or "\n",
        "day_number": 3, "topic": "Topic", "lesson_title": "Lesson",
        "sources": sources,
    })


def test_map_prompt_resolves_from_template_not_builtin_fallback():
    """The shipped template must actually load and satisfy the reply contract.

    Guards the silent-degradation path: if templates/digest_map.md were deleted,
    malformed, or edited to drop the {{schema}} placeholder, resolution falls back
    to the compact built-in and digest quality quietly drops. That fallback is
    deliberate for robustness, but it must never be what a normal deploy uses.
    """
    template, content_hash = mapper.map_prompt()
    assert template is not mapper._BUILTIN_MAP_PROMPT, (
        "digest_map.md failed to resolve — check it exists and keeps {{schema}}"
    )
    assert "{{schema}}" in template
    prompt = _rendered_map_prompt()
    assert all(key in prompt for key in mapper.MAP_REPLY_KEYS)
    # No placeholder may survive rendering — an unfilled one would reach the model.
    assert "{{" not in prompt
    # The version marker carries the template hash, so an edit invalidates digests.
    assert mapper.current_prompt_version() == f"{mapper.PROMPT_VERSION_BASE}+{content_hash}"


def test_map_prompt_template_edit_changes_cache_key():
    """A template edit must invalidate the digests it would change.

    The rubric is now editable at runtime, so its content is part of what produced
    a digest. If the cache key ignored it, an admin's edit would appear to do
    nothing until every day happened to change for some other reason.
    """
    units = [{"content_hash": "h1"}]
    before = mapper.cache_key(1, units, "m")
    edited = mapper.cache_key(1, units, "m", prompt_version="map-v6+deadbeefcafe")
    assert before != edited


def test_concept_type_prompt_gives_a_discriminating_rubric():
    """Regression: a real Block 2 run returned "Procedural" for all 20 days
    (including a Test day) because the prompt just listed 4 bare words with no
    criteria — the model wasn't discriminating, it was defaulting. The prompt
    must give each type a concrete one-line criterion."""
    source = _rendered_map_prompt()
    assert "Conceptual:" in source and "Procedural:" in source and "Metacognitive:" in source
    assert "Mixed" in source  # combined-type guidance for genuinely blended days


def test_concept_type_prompt_distinguishes_being_taught_from_doing():
    """Regression: a real Block 2 run classified Day 1 (identifying drawing
    elements — no bench task) and Day 5 (tool identification/purpose — no bench
    task) as "Skill"/"Procedural + Skill (Mixed)" against the AIM reference's
    "Conceptual" for both — the rubric didn't distinguish being TAUGHT ABOUT a
    procedure/tool from the learner actually DOING it. The prompt must say so
    explicitly, not just list the four original bare labels."""
    source = _rendered_map_prompt().lower()
    assert "how a tool/technique/process is used" in source
    assert "hands-on task" in source


def test_concept_type_prompt_adds_summative_assessment_distinct_from_metacognitive():
    """Regression: AIM's reference labels a final/cumulative exam day "Summative
    Assessment", distinct from "Metacognitive" (which it reserves for
    review/reflection days with no graded test) — the prompt previously offered
    no such value, so the model's only fallback was Metacognitive for every kind
    of test/review day."""
    source = _rendered_map_prompt()
    assert "Summative Assessment:" in source


def test_misconceptions_prompt_requires_empty_array_not_placeholder_string():
    """Regression: a real assessment-day digest returned misconceptions=["no"] —
    a placeholder INSIDE the array — which renders as a literal, wrong-looking
    list item downstream instead of the clean "NONE DOCUMENTED" default an
    empty array produces."""
    source = _rendered_map_prompt()
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


# --------------------------------------------------------------------------- #
# Attribution precision — regressions from the Block 2 cdd-146 incident, where
# S3 pulled 22 pages of an unrelated 90-page ACS standards PDF onto Day 1
# (~90k chars of turbine/fire-detection/lavatory text on an Aircraft Drawings
# day), overflowed the extractor's context window, and left every LLM-derived
# cell of the shipped Blueprint blank.
# --------------------------------------------------------------------------- #
def _wide_days(n=20):
    """A block whose calendar rows repeat the same boilerplate on every day —
    the real shape of AIM calendar hint text."""
    boiler = "ACS codes FAA 8083 30B pgs reading reference topics covered aircraft"
    return [{"day_number": i, "topic": f"{boiler} widget{i} gadget{i}",
             "lesson_title": f"Lesson {i}", "source_text": "",
             "assignments_json": "", "assessments_json": ""} for i in range(1, n + 1)]


def test_ubiquitous_hint_terms_are_dropped_but_distinctive_ones_survive():
    dterms = attribution.day_terms_by_day(_wide_days(), drop_ubiquitous=False)
    common = attribution.ubiquitous_terms(dterms)
    # Repeated on every calendar row -> zero discriminative power.
    for t in ("acs", "faa", "8083", "reference", "aircraft", "topics"):
        assert t in common, t
    # Unique to one day -> must survive, or S3 loses all signal.
    assert "widget7" not in common and "gadget7" not in common
    filtered = attribution.day_terms_by_day(_wide_days())
    assert filtered[7] == {"widget7", "gadget7", "lesson"} - common


def test_ubiquity_filter_is_inert_on_a_tiny_block():
    """A 2-day block must not have its hint terms stripped — with so few days,
    'shared by most days' carries no information."""
    days = _days()[:2]
    assert attribution.ubiquitous_terms(attribution.day_terms_by_day(days, drop_ubiquitous=False)) == set()


def test_s3_refuses_a_tie_instead_of_silently_picking_the_lowest_day():
    """The original bug: `ov > best` over dict-insertion order meant a tie
    resolved to whichever day came first, making Day 1 a sink for every
    unresolvable unit. Half the wrongly-attributed units had margin == 0."""
    days = [
        {"day_number": 1, "topic": "alpha bravo charlie delta", "lesson_title": "",
         "source_text": "", "assignments_json": "", "assessments_json": ""},
        {"day_number": 2, "topic": "alpha bravo charlie echo", "lesson_title": "",
         "source_text": "", "assignments_json": "", "assessments_json": ""},
    ]
    proj_ref, quiz_ref = attribution.build_calendar_refs(days)
    dterms = attribution.day_terms_by_day(days)
    unit = {"unit_type": "page", "title": "alpha bravo charlie",
            "text_content": "alpha bravo charlie", "metadata_json": {}}
    placed, signal, conf = attribution.attribute(unit, days, proj_ref, quiz_ref, dterms)
    assert placed == [] and conf == 0.0
    assert "S3-weak" in signal and "/3)" in signal   # best 3 == runner-up 3


def test_s3_rejects_a_long_unit_that_overlaps_only_on_boilerplate():
    """A big document trivially clears an absolute overlap floor by volume. After
    ubiquity filtering its 'evidence' is empty, so it must stay unresolved."""
    days = _wide_days()
    proj_ref, quiz_ref = attribution.build_calendar_refs(days)
    dterms = attribution.day_terms_by_day(days)
    unit = {"unit_type": "page", "title": "Aviation Mechanic Certification Standards",
            "text_content": ("ACS codes FAA 8083 30B reference reading aircraft topics "
                             "covered inspect maintenance system " * 40),
            "metadata_json": {}}
    placed, signal, _ = attribution.attribute(unit, days, proj_ref, quiz_ref, dterms)
    assert placed == [], f"boilerplate-only overlap was attributed: {signal}"


def test_s3_still_recovers_a_genuinely_day_specific_unit():
    """Precision must not cost the real recoveries S3 exists for — the AIM study
    questions / hangar activities that carry no day token."""
    days = _wide_days()
    proj_ref, quiz_ref = attribution.build_calendar_refs(days)
    dterms = attribution.day_terms_by_day(days)
    unit = {"unit_type": "study_question", "title": "widget7 gadget7 study questions",
            "text_content": "widget7 gadget7 lesson practice", "metadata_json": {}}
    placed, signal, conf = attribution.attribute(unit, days, proj_ref, quiz_ref, dterms)
    assert placed == [7] and signal.startswith("S3") and conf > 0


@pytest.mark.parametrize("md", [
    {"doc_type": "ebook_reference"},
    {"document_type": "ebook_reference"},
    {"content_type": "syllabus"},
    {"tags": ["tenant:aim", "type:ebook_reference", "visibility:student"]},
    {"tags": ["content_type:course_calendar"]},
])
def test_block_wide_reference_pages_are_never_term_attributed(md):
    """One page of a 90-page standards PDF has no day. Sharing trade vocabulary
    with a day's topic is not evidence of belonging to it."""
    days = _wide_days()
    proj_ref, quiz_ref = attribution.build_calendar_refs(days)
    dterms = attribution.day_terms_by_day(days)
    unit = {"unit_type": "page", "title": "widget7 gadget7",
            "text_content": "widget7 gadget7 lesson", "metadata_json": md}
    placed, signal, _ = attribution.attribute(unit, days, proj_ref, quiz_ref, dterms)
    assert placed == [] and signal == "unresolved:block-wide-reference"


def test_block_wide_reference_still_honours_an_explicit_day_token():
    """Only the statistical signal is withheld. An explicit 'Day 3' or project
    cross-reference in such a document IS real evidence and must still land."""
    days = _days()
    proj_ref, quiz_ref = attribution.build_calendar_refs(days)
    dterms = attribution.day_terms_by_day(days)
    unit = {"unit_type": "page", "title": "Handbook excerpt", "text_content": "",
            "metadata_json": {"doc_type": "ebook_reference", "source_file_name": "B2D3.pdf"}}
    placed, signal, _ = attribution.attribute(unit, days, proj_ref, quiz_ref, dterms)
    assert placed == [3] and signal.startswith("S2")

    unit2 = {"unit_type": "page", "title": "Project 2-1 reference pages",
             "text_content": "", "metadata_json": {"doc_type": "ebook_reference"}}
    placed2, signal2, _ = attribution.attribute(unit2, days, proj_ref, quiz_ref, dterms)
    assert placed2 == [1] and signal2.startswith("S1")


def test_worksheets_shares_the_one_block_wide_reference_definition():
    """The two modules must never disagree about what a block-wide reference is."""
    from services.digests import worksheets
    assert set(worksheets._BLOCK_WIDE_REFERENCE_TYPES) == \
        set(attribution.BLOCK_WIDE_REFERENCE_DOC_TYPES)


# --------------------------------------------------------------------------- #
# MAP input budget — a day is unbounded upstream; exceeding the extractor's
# context window is a HARD Bedrock error that fails the whole day.
# --------------------------------------------------------------------------- #
def _unit(sig, chars, uid):
    return {"content_unit_id": uid, "unit_type": "page", "title": uid,
            "text_content": "x" * chars, "attribution_signal": sig,
            "metadata_json": {}}


def test_source_body_passes_everything_through_when_within_budget():
    units = [_unit("raw:day_number", 100, "a"), _unit("S3:overlap(9/2)", 100, "b")]
    body, dropped = mapper._source_body(units)
    assert dropped == {}
    assert "a" in body and "b" in body


def test_source_body_caps_total_and_drops_lowest_confidence_first():
    # The budget is now an explicit argument (derived per-model by the caller), so
    # the test states it directly instead of mutating a module global. Three units of
    # n chars cannot fit a two-unit budget, and the S3 unit is the least certainly
    # this day's material.
    n = 4_000
    units = [_unit("S3:overlap(3/1)", n, "weak"),
             _unit("raw:day_number", n, "strong1"),
             _unit("S1:project 2-1", n, "mid")]
    budget = 2 * (n + len("[page] strong1\n")) + 4
    body, dropped = mapper._source_body(units, limit=budget)
    assert dropped["units"] == 1 and dropped["chars"] > 0
    assert "strong1" in body and "mid" in body
    assert "weak" not in body
    # Surviving units keep source order, not confidence order.
    assert body.index("strong1") < body.index("mid")


def test_source_body_keeps_at_least_one_unit_even_if_it_alone_exceeds_budget():
    """A day whose single unit is larger than the whole budget must still send
    something (per-unit truncation applies) rather than an empty SOURCES block."""
    orig = mapper.MAP_MAX_SOURCE_CHARS
    try:
        mapper.MAP_MAX_SOURCE_CHARS = 10
        body, dropped = mapper._source_body([_unit("raw:day_number", 5000, "solo")])
    finally:
        mapper.MAP_MAX_SOURCE_CHARS = orig
    assert "solo" in body and dropped == {}


def test_oversized_day_is_flagged_for_review_not_silently_truncated(stub_llm):
    orig = mapper.MAP_MAX_SOURCE_CHARS
    try:
        mapper.MAP_MAX_SOURCE_CHARS = 200
        digest = mapper.build_digest(
            {"day_number": 1, "topic": "Intro", "lesson_title": "L1"},
            [_unit("raw:day_number", 150, "a"), _unit("S3:overlap(3/1)", 150, "b")],
            _tenant(), model="stub-model", client_id="aim", block="Block 2")
    finally:
        mapper.MAP_MAX_SOURCE_CHARS = orig
    assert digest["digest_status"] == "ok"
    assert any(f.startswith("SOURCES_TRUNCATED") for f in digest["review_flags"]), \
        digest["review_flags"]


# --------------------------------------------------------------------------- #
# Poisoned-digest self-healing. digest_status=="ok" is only as trustworthy as the
# code that wrote it; digests stored before MAP failures were surfaced are "ok"
# with every extracted field at its default.
# --------------------------------------------------------------------------- #
def test_has_extraction_rejects_the_poisoned_shape_and_accepts_sparse_real_days():
    poisoned = {"concept_type": "Unknown", "derived_objective": "",
                "misconceptions": [], "interactive_candidate": False}
    assert not mapper.has_extraction(poisoned)
    assert not mapper.has_extraction({})
    # A real but sparse day: no interactive, no misconceptions, still extracted.
    assert mapper.has_extraction({"concept_type": "Summative Assessment",
                                  "derived_objective": ""})
    assert mapper.has_extraction({"concept_type": "Unknown",
                                  "derived_objective": "Identify line types."})


def test_cache_reuses_a_real_digest_but_rebuilds_a_poisoned_one():
    day = {"day_number": 1, "topic": "Intro", "lesson_title": "L1"}
    units = [{"content_unit_id": "s1", "unit_type": "slide", "title": "s",
              "text_content": "body", "content_hash": "a", "metadata_json": {}}]
    ck = mapper.cache_key(1, units, "stub-model", day_meta=mapper.day_signature(day))
    real = {"cache_key": ck, "digest_status": "ok", "concept_type": "Conceptual",
            "derived_objective": "Explain drawings."}
    assert build.day_is_cached(day, units, "stub-model", {1: real}, force=False)

    poisoned = {"cache_key": ck, "digest_status": "ok", "concept_type": "Unknown",
                "derived_objective": ""}
    assert not build.day_is_cached(day, units, "stub-model", {1: poisoned}, force=False)


def test_stored_prompt_version_is_the_effective_one_not_the_bare_base(stub_llm):
    """Provenance: a v6-era digest and one from a since-edited template both
    claiming 'map-v6' is what made the stale-digest incident hard to diagnose."""
    digest = mapper.build_digest({"day_number": 1, "topic": "Intro", "lesson_title": "L1"},
                                 [], _tenant(), model="stub-model",
                                 client_id="aim", block="Block 2")
    assert digest["prompt_version"] == mapper.current_prompt_version()
    assert digest["prompt_version"].startswith(mapper.PROMPT_VERSION_BASE + "+")


def test_final_exam_resolves_to_the_calendar_exam_day_not_by_overlap():
    """A final exam covers every day's vocabulary, so term overlap scatters it
    across the block. On real Block 2 the calendar schedules it on day 20 while
    S3 had placed its questions on days 8 and 10."""
    days = _wide_days()
    days[-1]["assessments_json"] = json.dumps(["Block 2: Final Exam"])
    proj_ref, quiz_ref = attribution.build_calendar_refs(days)
    dterms = attribution.day_terms_by_day(days)
    assert attribution.final_exam_day(days) == 20

    # Deliberately loaded with day-7 vocabulary AND a project token, both of which
    # would otherwise win: the exam's own text cites items from across the block.
    unit = {"unit_type": "quiz_question", "title": "Block 2 Final Exam",
            "text_content": "widget7 gadget7 Project 2-1 review of all topics",
            "metadata_json": {"content_type": "final_exam",
                              "tags": ["type:final_exam"]}}
    placed, signal, conf = attribution.attribute(unit, days, proj_ref, quiz_ref, dterms)
    assert placed == [20] and signal == "S1:final-exam" and conf == 1.0


def test_final_exam_is_unresolved_when_no_calendar_day_claims_it():
    """Better surfaced as unattributed than scattered onto a plausible-looking day."""
    days = _wide_days()
    proj_ref, quiz_ref = attribution.build_calendar_refs(days)
    dterms = attribution.day_terms_by_day(days)
    unit = {"unit_type": "quiz_question", "title": "Final Exam",
            "text_content": "widget7 gadget7", "metadata_json": {"doc_type": "final_exam"}}
    placed, signal, _ = attribution.attribute(unit, days, proj_ref, quiz_ref, dterms)
    assert placed == [] and signal == "unresolved:final-exam-day-unknown"


def test_numbered_per_day_quiz_is_not_treated_as_the_final_exam():
    """'Quiz 1' must still resolve via its own calendar cross-reference."""
    days = _days()
    days[0]["assessments_json"] = json.dumps(["Block 2: Final Exam"])
    proj_ref, quiz_ref = attribution.build_calendar_refs(days)
    dterms = attribution.day_terms_by_day(days)
    unit = {"unit_type": "quiz_question", "title": "Quiz 1 question 3",
            "text_content": "", "metadata_json": {"content_type": "quiz"}}
    placed, signal, _ = attribution.attribute(unit, days, proj_ref, quiz_ref, dterms)
    assert placed == [2] and signal.startswith("S1:quiz")


# --------------------------------------------------------------------------- #
# Extractor preflight
#
# call_llm swallows every exception and returns a valid-JSON stub with 0 tokens, so
# an unavailable model does not raise — it quietly yields N digests whose fields are
# all defaults. 2026-08-12: a deployed role could not invoke the configured model,
# 8 of 20 days came back concept_type "Unknown", every AM.I.B ACS code was orphaned,
# and the job reported success. One cheap probe turns that into one clear error.
# --------------------------------------------------------------------------- #
def test_preflight_rejects_the_stub_signature(monkeypatch):
    """0 input tokens is uniquely call_llm's exception path."""
    import services.pipeline.common as common
    from services.digests import build as build_mod

    monkeypatch.setattr(common, "call_llm",
                        lambda *a, **k: ('{"doc_type":"other","classification":"internal"}', 0, 0))
    with pytest.raises(build_mod.ExtractorUnavailable) as exc:
        build_mod.preflight_extractor("global.anthropic.some-model-v1:0")
    msg = str(exc.value)
    assert "some-model-v1:0" in msg, "the error must name the model"
    assert "per-role" in msg, "must point at model access, not just region"
    assert "DIS_MODEL_TEXT_ALL" in msg, "must tell the reader how to override it"


def test_preflight_passes_a_real_reply(monkeypatch):
    import services.pipeline.common as common
    from services.digests import build as build_mod
    monkeypatch.setattr(common, "call_llm", lambda *a, **k: ('{"ok":true}', 12, 4))
    build_mod.preflight_extractor("global.anthropic.claude-sonnet-4-5-20250929-v1:0")


def test_preflight_tolerates_a_terse_or_unexpected_reply(monkeypatch):
    """It verifies reachability, not obedience: a model that answers something else
    is still usable, and rejecting it would block builds for no reason."""
    import services.pipeline.common as common
    from services.digests import build as build_mod
    monkeypatch.setattr(common, "call_llm", lambda *a, **k: ("sure!", 9, 2))
    build_mod.preflight_extractor("m")


def test_preflight_converts_a_raised_error_too(monkeypatch):
    """call_llm swallows today, but a future version may raise; either way the build
    must fail with the actionable message rather than a bare boto traceback."""
    import services.pipeline.common as common
    from services.digests import build as build_mod

    def boom(*a, **k):
        raise RuntimeError("AccessDeniedException: not authorized to invoke")

    monkeypatch.setattr(common, "call_llm", boom)
    with pytest.raises(build_mod.ExtractorUnavailable) as exc:
        build_mod.preflight_extractor("m")
    assert "AccessDenied" in str(exc.value)


def test_build_digests_fails_fast_instead_of_writing_empty_digests(monkeypatch):
    """The point of the probe: one error, not a block-shaped pile of defaults."""
    import services.pipeline.common as common
    from services.digests import build as build_mod

    calls = {"n": 0}

    def counting(*a, **k):
        calls["n"] += 1
        return ('{"doc_type":"other","classification":"internal"}', 0, 0)

    monkeypatch.setattr(common, "call_llm", counting)
    # enumerate/index are never reached, so no stores are touched.
    with pytest.raises(build_mod.ExtractorUnavailable):
        build_mod.build_digests(_tenant(), "Block 2")
    assert calls["n"] == 1, "must stop after the probe, not run a call per day"


# --------------------------------------------------------------------------- #
# Limits are configurable, and 0 means no limit
#
# Truncating MAP input is a silent quality tax: the model reasons over less than
# the day's material and nothing in the output says so. The caps exist only because
# exceeding the context window is a hard Bedrock error that fails the whole day, so
# they must be raisable — and removable — per environment.
# --------------------------------------------------------------------------- #
def test_env_int_parses_and_rejects_junk(monkeypatch):
    from services.digests import mapper as m
    monkeypatch.setenv("X_LIMIT", "5000")
    assert m._env_int("X_LIMIT", 1) == 5000
    monkeypatch.setenv("X_LIMIT", "0")
    assert m._env_int("X_LIMIT", 1) == 0, "0 must survive as an explicit 'no limit'"
    for junk in ("abc", "-5", ""):
        monkeypatch.setenv("X_LIMIT", junk)
        assert m._env_int("X_LIMIT", 77) == 77, f"{junk!r} should fall back, not crash"
    monkeypatch.delenv("X_LIMIT")
    assert m._env_int("X_LIMIT", 42) == 42


def test_zero_unit_cap_sends_each_unit_whole(monkeypatch):
    from services.digests import mapper as m
    monkeypatch.setattr(m, "MAP_MAX_UNIT_CHARS", 0)
    monkeypatch.setattr(m, "MAP_MAX_SOURCE_CHARS", 0)
    long_text = "x" * 50_000
    body, dropped = m._source_body([
        {"unit_type": "page", "title": "t", "text_content": long_text,
         "attribution_signal": "raw"}])
    assert long_text in body, "unit was trimmed despite MAP_MAX_UNIT_CHARS=0"
    assert dropped == {}


def test_zero_source_cap_keeps_every_unit(monkeypatch):
    from services.digests import mapper as m
    monkeypatch.setattr(m, "MAP_MAX_UNIT_CHARS", 0)
    monkeypatch.setattr(m, "MAP_MAX_SOURCE_CHARS", 0)
    units = [{"unit_type": "page", "title": f"t{i}", "text_content": "y" * 20_000,
              "attribution_signal": "raw"} for i in range(20)]
    body, dropped = m._source_body(units)
    assert dropped == {}, "units were dropped despite MAP_MAX_SOURCE_CHARS=0"
    for i in range(20):
        assert f"t{i}" in body


def test_a_nonzero_cap_still_trims_and_reports(monkeypatch):
    """The flag matters: a trimmed day must be visible, not inferred from thin output."""
    from services.digests import mapper as m
    monkeypatch.setattr(m, "MAP_MAX_UNIT_CHARS", 0)
    units = [{"unit_type": "page", "title": f"t{i}", "text_content": "z" * 4_000,
              "attribution_signal": "raw"} for i in range(5)]
    body, dropped = m._source_body(units, limit=5_000)
    assert dropped.get("units", 0) > 0
    assert len(body) <= 5_000 + 200


def test_map_output_ceiling_is_not_the_old_claude3_limit():
    """4096 was Claude 3 Sonnet's real maximum; against Sonnet 4.5 it became an
    arbitrary cap that can truncate a digest mid-JSON."""
    from services.digests import mapper as m
    assert m.MAP_MAX_TOKENS >= 16_000


# --------------------------------------------------------------------------- #
# Model-aware budget + escalation
#
# A single global char budget is wrong by construction: the same number is too small
# for a 1M-window model and too large for a 200k one. And when a day does not fit,
# moving UP to a larger-window model keeps all of its evidence, where trimming
# silently discards the material the extraction is supposed to rest on.
# --------------------------------------------------------------------------- #
def test_budget_scales_with_the_models_context_window():
    from services.digests import mapper as m
    small = m.context_budget_chars("global.anthropic.claude-sonnet-4-5-20250929-v1:0")  # 200k tok
    large = m.context_budget_chars("global.anthropic.claude-sonnet-5")                  # 1M tok
    assert large > small * 4, "a 1M-window model must get a far larger budget"
    assert small > 100_000


def test_unknown_model_gets_the_most_pessimistic_budget():
    """Being wrong low costs a flagged trim; being wrong high costs the whole day to
    a hard context-window rejection."""
    from services.digests import mapper as m
    unknown = m.context_budget_chars("some.model.nobody.registered")
    assert unknown <= min(m.context_budget_chars(k) for k in m._CONTEXT_TOKENS)


def test_a_day_that_fits_keeps_the_configured_model():
    from services.digests import mapper as m
    model, note = m.select_model_for("global.anthropic.claude-sonnet-5", 10_000)
    assert model == "global.anthropic.claude-sonnet-5" and note is None


def test_a_day_that_overflows_escalates_to_a_larger_window_model(monkeypatch):
    from services.digests import mapper as m
    monkeypatch.setattr(m, "MAP_ESCALATION_MODELS", ["global.anthropic.claude-opus-5"])
    small = "global.anthropic.claude-sonnet-4-5-20250929-v1:0"
    need = m.context_budget_chars(small) + 1
    model, note = m.select_model_for(small, need)
    assert model == "global.anthropic.claude-opus-5", "did not escalate"
    assert note and "escalated" in note


def test_escalation_is_skipped_when_no_candidate_is_large_enough(monkeypatch):
    """Falls back to trimming — with a flag — rather than sending a request that the
    provider will reject outright."""
    from services.digests import mapper as m
    monkeypatch.setattr(m, "MAP_ESCALATION_MODELS",
                        ["global.anthropic.claude-haiku-4-5-20251001-v1:0"])  # 200k, smaller
    big = "global.anthropic.claude-sonnet-5"
    model, note = m.select_model_for(big, m.context_budget_chars(big) + 1)
    assert model == big and note is None


def test_escalation_can_be_disabled(monkeypatch):
    from services.digests import mapper as m
    monkeypatch.setattr(m, "MAP_ESCALATION_MODELS", [])
    small = "global.anthropic.claude-sonnet-4-5-20250929-v1:0"
    model, note = m.select_model_for(small, m.context_budget_chars(small) + 1)
    assert model == small and note is None


def test_an_explicit_override_wins_over_the_derived_budget(monkeypatch):
    from services.digests import mapper as m
    monkeypatch.setattr(m, "MAP_MAX_SOURCE_CHARS", 1_234)
    assert m.context_budget_chars("global.anthropic.claude-sonnet-5") == 1_234
    monkeypatch.setattr(m, "MAP_MAX_SOURCE_CHARS", 0)   # 0 = unlimited
    assert m.context_budget_chars("global.anthropic.claude-sonnet-5") == 0


# --------------------------------------------------------------------------- #
# Newer models reject an explicit temperature
# --------------------------------------------------------------------------- #
def test_call_llm_retries_without_temperature_when_the_model_rejects_it(monkeypatch):
    """Sonnet 5 / Opus 5 answer `ValidationException: temperature is deprecated for
    this model`. Without this retry that exception is swallowed into call_llm's
    valid-JSON stub, so every day's digest comes back empty and the block reports
    success — the exact failure this pipeline already shipped once. CAS has had the
    same retry in core/llm_client.py; DIS did not."""
    import json as _json
    import services.pipeline.common as common

    calls = []

    class _Body:
        @staticmethod
        def read():
            return _json.dumps({"content": [{"text": '{"ok":true}'}],
                                "usage": {"input_tokens": 11, "output_tokens": 3}})

    class _Client:
        def invoke_model(self, modelId=None, body=None):
            calls.append(_json.loads(body))
            if len(calls) == 1:
                raise Exception("An error occurred (ValidationException) when calling the "
                                "InvokeModel operation: `temperature` is deprecated for this model.")
            return {"body": _Body()}

    monkeypatch.setattr(common, "get_settings", lambda: types.SimpleNamespace(
        environment="production", anthropic_api_key=None, aws_access_key_id="k",
        use_bedrock=True, bedrock_client_kwargs=lambda: {"region_name": "ap-south-1"}))
    monkeypatch.setitem(__import__("sys").modules, "boto3",
                        types.SimpleNamespace(client=lambda *a, **k: _Client()))
    # call_llm reuses one client per credential set, so a client cached by an earlier
    # test would be used instead of the fake above.
    import services.pipeline.common as _c
    _c.reset_bedrock_clients()

    text, ti, to = common.call_llm("global.anthropic.claude-sonnet-5", "hi", max_tokens=8)

    assert (text, ti, to) == ('{"ok":true}', 11, 3), "the retry's result was not returned"
    assert len(calls) == 2, "did not retry"
    assert "temperature" in calls[0], "the first attempt should still try temperature 0"
    assert "temperature" not in calls[1], "the retry must drop temperature"


def test_call_llm_does_not_mask_an_unrelated_validation_error(monkeypatch):
    """Only the temperature case is retried — a different ValidationException must not
    be retried into a second identical failure that hides the real cause."""
    import services.pipeline.common as common

    calls = []

    class _Client:
        def invoke_model(self, modelId=None, body=None):
            calls.append(body)
            raise Exception("An error occurred (ValidationException) when calling the "
                            "InvokeModel operation: model id is not supported.")

    monkeypatch.setattr(common, "get_settings", lambda: types.SimpleNamespace(
        environment="production", anthropic_api_key=None, aws_access_key_id="k",
        use_bedrock=True, bedrock_client_kwargs=lambda: {"region_name": "ap-south-1"}))
    monkeypatch.setitem(__import__("sys").modules, "boto3",
                        types.SimpleNamespace(client=lambda *a, **k: _Client()))
    # call_llm reuses one client per credential set, so a client cached by an earlier
    # test would be used instead of the fake above.
    import services.pipeline.common as _c
    _c.reset_bedrock_clients()

    # call_llm now RAISES on a provider error instead of returning a valid-JSON stub
    # (see common.LLMCallFailed). The point of this test is unchanged: exactly ONE
    # invoke_model call, because only the temperature ValidationException is retried.
    with pytest.raises(common.LLMCallFailed) as excinfo:
        common.call_llm("some-model", "hi", max_tokens=8)
    assert len(calls) == 1, "an unrelated ValidationException was retried"
    assert "model id is not supported" in str(excinfo.value), "the real cause must survive"


def test_a_malformed_source_budget_falls_back_instead_of_truncating(monkeypatch):
    """Found in review. DIS_MAP_MAX_SOURCE_CHARS resolved junk to the sentinel -1,
    which is TRUTHY in the trim comparison — so a single typo in that variable kept
    only the FIRST source unit of every day and said so in one log line. A malformed
    limit must never be quieter, or more destructive, than an unset one."""
    from services.digests import mapper as m

    for bad in ("notanumber", "-1", "12.5", ""):
        monkeypatch.setenv("DIS_MAP_MAX_SOURCE_CHARS", bad)
        assert m._env_int_or_none("DIS_MAP_MAX_SOURCE_CHARS") is None, bad
    monkeypatch.setenv("DIS_MAP_MAX_SOURCE_CHARS", "5000")
    assert m._env_int_or_none("DIS_MAP_MAX_SOURCE_CHARS") == 5000
    monkeypatch.setenv("DIS_MAP_MAX_SOURCE_CHARS", "0")
    assert m._env_int_or_none("DIS_MAP_MAX_SOURCE_CHARS") == 0, "0 means 'no limit'"


def test_a_derived_budget_is_never_negative():
    """The floor matters for the same reason: a negative budget is truthy."""
    from services.digests import mapper as m
    for model in list(m._CONTEXT_TOKENS) + ["unregistered.model"]:
        assert m.context_budget_chars(model) > 0


def test_every_unit_survives_when_the_budget_is_malformed(monkeypatch):
    """End-to-end version of the bug: 5 units in, 5 units out."""
    from services.digests import mapper as m
    monkeypatch.setattr(m, "MAP_MAX_SOURCE_CHARS", None)   # as a junk value now resolves
    monkeypatch.setattr(m, "MAP_MAX_UNIT_CHARS", 0)
    units = [{"unit_type": "page", "title": f"t{i}", "text_content": "x" * 100,
              "attribution_signal": "raw"} for i in range(5)]
    budget = m.context_budget_chars("global.anthropic.claude-sonnet-4-5-20250929-v1:0")
    body, dropped = m._source_body(units, limit=budget)
    assert dropped == {}
    assert all(f"t{i}" in body for i in range(5))


def test_preflight_success_is_memoised_so_a_cached_rebuild_stays_free(monkeypatch):
    """Found in review: the probe ran on EVERY build, so a fully cached rebuild that
    should cost 0 LLM calls quietly cost 1 — and the build report's map_calls=0 was
    then untrue of actual spend."""
    import services.pipeline.common as common
    from services.digests import build as build_mod

    monkeypatch.setattr(build_mod, "_PREFLIGHT_OK", set())
    calls = {"n": 0}

    def probe(*a, **k):
        calls["n"] += 1
        return ('{"ok":true}', 9, 2)

    monkeypatch.setattr(common, "call_llm", probe)
    for _ in range(4):
        build_mod.preflight_extractor("global.anthropic.claude-sonnet-4-5-20250929-v1:0")
    assert calls["n"] == 1, f"probed {calls['n']} times; success should be memoised"


def test_preflight_failure_is_never_memoised(monkeypatch):
    """Caching a failure would let a build sail past a broken extractor on the second
    attempt — the opposite of what the probe is for."""
    import services.pipeline.common as common
    from services.digests import build as build_mod

    monkeypatch.setattr(build_mod, "_PREFLIGHT_OK", set())
    calls = {"n": 0}

    def stub(*a, **k):
        calls["n"] += 1
        return ('{"doc_type":"other"}', 0, 0)

    monkeypatch.setattr(common, "call_llm", stub)
    for _ in range(3):
        with pytest.raises(build_mod.ExtractorUnavailable):
            build_mod.preflight_extractor("m")
    assert calls["n"] == 3, "a failing model must be re-probed every build"


def test_preflight_is_per_model(monkeypatch):
    import services.pipeline.common as common
    from services.digests import build as build_mod

    monkeypatch.setattr(build_mod, "_PREFLIGHT_OK", set())
    seen = []
    monkeypatch.setattr(common, "call_llm",
                        lambda model, *a, **k: (seen.append(model), ('{"ok":1}', 5, 1))[1])
    build_mod.preflight_extractor("model-a")
    build_mod.preflight_extractor("model-b")
    build_mod.preflight_extractor("model-a")
    assert seen == ["model-a", "model-b"], f"memo is not keyed per model: {seen}"
