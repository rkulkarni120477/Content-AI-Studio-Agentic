"""Which documents a CDD regeneration is allowed to look at.

DIS scopes retrieval by purpose, and a purpose is a fixed list of document types
in tenant config. For AIM's `cdd` purpose that list is calendars and syllabi,
which — measured against the live index — leaves 10 of the 1,002 units carrying a
Block 2 tag eligible and the other 992 invisible. Not ranked low: unreachable.
An instruction naming the block's Study Questions could not be answered however
it was phrased, and the honest refusal that came back looked like a bug.

These tests pin the two halves of the fix. The instruction decides what the
search may see (never answer keys, whatever it says), and a document naming a
different block is dropped before it can spend the token budget — measured, a
plain Block 2 search returned 8 of 16 units from five other blocks' calendars,
on the semantic path.
"""

from app.api.v1.routers.cdd import _noop_note, _scope_hint
from app.services import cdd_deep_context as D

# ---------------------------------------------------------------------------
# What the instruction asks for
# ---------------------------------------------------------------------------

def test_a_plain_rewording_asks_for_nothing_extra():
    assert D.document_type_hints("tighten the wording of the block description") == ()


def test_study_questions_are_asked_for_by_name():
    assert "study_questions" in D.document_type_hints(
        "fill the review row from the Block 2 study questions")


def test_the_families_the_corpus_actually_carries_are_reachable():
    cases = {
        "list the quiz coverage per day": "quiz",
        "what does the final exam assess": "final_exam",
        "add the project numbers": "project",
        "reference the hangar activities": "hangar_activity",
        "check the teaching outline slides": "slide_deck",
    }
    for instruction, expected in cases.items():
        assert expected in D.document_type_hints(instruction), instruction


def test_answer_keys_are_never_asked_for():
    """The gate DIS enforces by role is also never approached from this side."""
    for instruction in ("use the answer key", "pull from the instructor guide",
                        "check the exam answer key", "quiz answer key page 3"):
        hints = D.document_type_hints(instruction)
        assert not (set(hints) & D.RESTRICTED_DOC_TYPES), instruction


def test_no_hint_maps_to_restricted_material():
    for _pattern, types, _strong in D._DOC_TYPE_HINTS:
        assert not (set(types) & D.RESTRICTED_DOC_TYPES)


def test_the_worksheets_own_vocabulary_does_not_start_a_paid_lookup():
    """"Summative Exam Item Cluster" is a Worksheet 4 column heading.

    A weak match must widen a search that is already happening and never begin
    one — otherwise reformatting a column costs a retrieval round-trip, and the
    user pays for context the instruction has no use for.
    """
    for instruction in ("reformat the Summative Exam Item Cluster column",
                        "shorten the project titles",
                        "capitalise the lesson names",
                        "tidy up the activity column"):
        assert not D.names_source_material(instruction), instruction
        assert not D.wants_source(instruction), instruction


def test_a_weak_word_still_widens_a_search_that_is_happening():
    """The two gates must not disagree in the other direction either."""
    instruction = "fill the missing exam coverage for each day"
    assert D.wants_source(instruction)          # "fill"/"missing" — a real ask
    assert "final_exam" in D.document_type_hints(instruction)


def test_activity_no_longer_matches_mid_word():
    """`\\bhangar|activit(?:y|ies)\\b` bound the boundary to one branch only."""
    assert D.document_type_hints("reduce inactivity in the schedule") == ()


def test_a_compound_instruction_stays_bounded():
    hints = D.document_type_hints(
        "cross-check the quizzes, the study questions, the projects, the hangar "
        "activities, the exams and the slides")
    assert len(hints) <= D.MAX_OPENED_DOC_TYPES


def test_naming_a_document_kind_is_itself_a_request_to_look():
    """Otherwise the two gates disagree and the wider one is unreachable.

    `wants_source` decides whether to search at all. An instruction that trips
    `document_type_hints` but not the verb/noun patterns would be answered from
    the worksheet alone, and the document types it implies would never be asked
    for — the opening would exist and never be used.
    """
    instruction = "add the quiz topics for each day"
    assert D.document_type_hints(instruction)
    assert D.wants_source(instruction)


# ---------------------------------------------------------------------------
# What the request sends
# ---------------------------------------------------------------------------

BASE = ("syllabus", "course_calendar")


def _filters(monkeypatch, requested, *, base=BASE):
    monkeypatch.setattr(D, "cdd_document_types", lambda **_kw: tuple(base))
    return D.search_filters(requested, current_user=None, client_id="aim")


def test_the_ordinary_request_is_scoped_exactly_as_before(monkeypatch):
    filters, effective, flags = _filters(monkeypatch, ())
    assert filters == {"purpose": "cdd"}
    assert flags == ()
    # Still reported, so an unhelpful result can say what was covered.
    assert effective == BASE


def test_a_named_type_is_added_to_the_purpose_set_not_substituted(monkeypatch):
    filters, effective, flags = _filters(monkeypatch, ("study_questions",))
    assert filters["purpose"] == "all"
    assert set(filters["document_types"]) == {*BASE, "study_questions"}
    assert flags == ()
    # The calendars and syllabi are the point of the CDD corpus; opening the gate
    # must not drop them.
    for expected in BASE:
        assert expected in effective


def test_restricted_types_are_stripped_from_a_request(monkeypatch):
    filters, _effective, _flags = _filters(monkeypatch, ("quiz_answer_key",))
    assert filters == {"purpose": "cdd"}


def test_the_gate_stays_shut_when_the_tenant_cannot_be_read(monkeypatch):
    """Degrade to today's behaviour, flagged, rather than guess the purpose set."""
    filters, effective, flags = _filters(monkeypatch, ("study_questions",), base=())
    assert filters == {"purpose": "cdd"}
    assert effective == ()
    assert "doc_types_unopened" in flags
    # Not an availability failure: the search itself runs fine.
    assert not any(f.endswith("unavailable") for f in flags)


class _FakeClient:
    """Records what the purpose-set lookup asks DIS for."""

    def __init__(self, *, mapping=None, boom=False, tenant="aim_tenant"):
        self.mapping, self.boom, self.tenant = mapping, boom, tenant
        self.calls: list[dict] = []

    def resolved_tenant_id(self, _user=None, _client=""):
        return self.tenant

    def ui_config_sync(self, current_user=None, client_id="", timeout=None):
        self.calls.append({"client_id": client_id, "timeout": timeout})
        if self.boom:
            raise RuntimeError("ui-config down")
        return {"tenant_id": self.tenant,
                "source_type_mapping": {"cdd": list(self.mapping or [])}}


def _install(monkeypatch, client):
    import app.core.dis_client as dc
    monkeypatch.setattr(dc, "dis_client", client)
    D._purpose_types_cache.clear()
    return client


def test_the_purpose_lookup_cannot_hang_a_button_press(monkeypatch):
    """The client default is 300s and one regeneration already hung for four
    minutes on it. This lookup only widens a search; it must be bounded."""
    client = _install(monkeypatch, _FakeClient(mapping=["syllabus"]))
    assert D.cdd_document_types(current_user=None, client_id="aim") == ("syllabus",)
    sent = client.calls[0]["timeout"]
    assert sent is not None
    assert 0 < sent <= D.PURPOSE_TYPES_TIMEOUT_SECONDS


def test_the_purpose_lookup_respects_the_ladders_remaining_budget(monkeypatch):
    import time
    client = _install(monkeypatch, _FakeClient(mapping=["syllabus"]))
    D.cdd_document_types(current_user=None, client_id="aim",
                         deadline=time.monotonic() + 2.0)
    assert client.calls[0]["timeout"] <= 2.0


def test_an_exhausted_budget_skips_the_lookup_entirely(monkeypatch):
    import time
    client = _install(monkeypatch, _FakeClient(mapping=["syllabus"]))
    assert D.cdd_document_types(current_user=None, client_id="aim",
                                deadline=time.monotonic() - 1.0) == ()
    assert client.calls == []


def test_a_failing_lookup_is_not_retried_on_every_regeneration(monkeypatch):
    """A slow dependency retried per request is how it becomes a slow product."""
    client = _install(monkeypatch, _FakeClient(boom=True))
    assert D.cdd_document_types(current_user=None, client_id="aim") == ()
    assert D.cdd_document_types(current_user=None, client_id="aim") == ()
    assert len(client.calls) == 1
    # And the failure is held for far less time than a success.
    assert D.PURPOSE_TYPES_FAILURE_TTL_SECONDS < D.PURPOSE_TYPES_TTL_SECONDS


def test_a_client_without_the_resolver_degrades_instead_of_raising(monkeypatch):
    """No rung may raise. A regeneration must never fail because the deep path
    was unavailable — only because there is genuinely nothing to say.

    This escaped once: the tenant resolver was called outside the try, so a
    client object lacking the method took the whole request down.
    """
    class _Bare:
        def ui_config_sync(self, **_kw):
            return {"source_type_mapping": {"cdd": ["syllabus"]}}

    _install(monkeypatch, _Bare())
    assert D.cdd_document_types(current_user=None, client_id="aim") == ("syllabus",)


def test_a_resolver_that_throws_degrades_too(monkeypatch):
    class _Angry:
        def resolved_tenant_id(self, *_a):
            raise RuntimeError("no access context")

        def ui_config_sync(self, **_kw):
            return {"source_type_mapping": {"cdd": ["syllabus"]}}

    _install(monkeypatch, _Angry())
    assert D.cdd_document_types(current_user=None, client_id="aim") == ("syllabus",)


def test_another_tenants_answer_is_refused(monkeypatch):
    """DIS resolves the tenant from the caller, not from client_id.

    Observed live: client_id="aim" with no user returned Cengage's cdd set, which
    omits course_calendar. Using it would have opened the gate to the requested
    type while dropping the calendars — worse than not opening it.
    """
    client = _FakeClient(mapping=["syllabus", "cdd_layout_pdf"], tenant="cengage")
    client.resolved_tenant_id = lambda _u=None, _c="": "aim_tenant"
    _install(monkeypatch, client)
    assert D.cdd_document_types(current_user=None, client_id="aim") == ()


def test_two_tenants_do_not_share_one_cache_entry(monkeypatch):
    client = _install(monkeypatch, _FakeClient(mapping=["syllabus"]))
    assert D.cdd_document_types(current_user=None, client_id="aim") == ("syllabus",)
    client.tenant = "other_tenant"
    client.mapping = ["course_outline"]
    assert D.cdd_document_types(current_user=None, client_id="aim") == ("course_outline",)


def test_the_purpose_set_is_read_from_the_tenant_not_restated(monkeypatch):
    """A tenant that admits something else must be followed, not overridden."""
    filters, effective, _flags = _filters(
        monkeypatch, ("quiz",), base=("program_overview",))
    assert "program_overview" in filters["document_types"]
    assert "program_overview" in effective


# ---------------------------------------------------------------------------
# Other blocks' documents
# ---------------------------------------------------------------------------

def test_a_block_is_read_out_of_a_document_name():
    assert D.document_block_number("Block 04-Instructor Copy- ACS Calendar.pdf") == 4
    assert D.document_block_number("Block 2 Teacher Calendar.xlsx") == 2
    assert D.document_block_number("BLK 12 outline.docx") == 12


def test_a_name_declaring_no_block_is_not_treated_as_the_wrong_one():
    """A block-spanning document must survive: no other rung reaches it."""
    assert D.document_block_number("ALL Block Calendars_NEW FORMAT.xlsx") is None
    assert not D.is_foreign_block("ALL Block Calendars_NEW FORMAT.xlsx", "Block 2")


def test_zero_padding_and_prefix_forms_are_the_same_block():
    for name in ("Block 02-Student Copy- ACS Course Calendar.pdf",
                 "Block 2 Teacher Calendar.xlsx", "BLK 2 notes.docx"):
        assert not D.is_foreign_block(name, "Block 2")
        assert not D.is_foreign_block(name, "Block 02")


def test_another_blocks_document_is_foreign():
    assert D.is_foreign_block("Block 04-Instructor Copy- ACS Calendar.pdf", "Block 2")
    assert D.is_foreign_block("Block 14 Syllabus.docx", "Block 02")


def test_an_unparseable_target_filters_nothing():
    assert not D.is_foreign_block("Block 04 Calendar.pdf", "")
    assert not D.is_foreign_block("Block 04 Calendar.pdf", "General Science")


def test_a_document_serving_several_blocks_is_kept_for_each_of_them():
    """Matching only the FIRST block in a name discarded this from Block 2.

    "Block 1 and 2 crosswalk" serves Block 2 as much as Block 1, and no other
    rung reaches a document that spans blocks.
    """
    name = "Block 1 and Block 2 crosswalk.pdf"
    assert D.document_block_numbers(name) == (1, 2)
    assert not D.is_foreign_block(name, "Block 2")
    assert not D.is_foreign_block(name, "Block 1")
    assert D.is_foreign_block(name, "Block 7")


def test_a_plural_block_word_is_not_read_as_a_block_number():
    assert D.document_block_numbers("Blocks overview.pdf") == ()


def _unit(name, number, text):
    return {"source_file_name": name, "unit_number": number, "text": text}


def test_other_blocks_documents_are_dropped_before_they_spend_the_budget():
    units = [
        _unit("Block 02-Student Copy- ACS Course Calendar.pdf", 1, "Day 1 Drawings"),
        _unit("Block 04-Instructor Copy- ACS Calendar.pdf", 1, "Day 1 Powerplant"),
        _unit("Block 14-Instructor Copy- ACS Calendar .docx", 1, "Day 1 Systems"),
    ]
    text, names = D.assemble_documents(units, block="Block 2")
    assert names == ("Block 02-Student Copy- ACS Course Calendar.pdf",)
    assert "Powerplant" not in text
    assert "Systems" not in text


def test_nothing_is_dropped_when_no_block_is_known():
    units = [
        _unit("Block 02 Calendar.pdf", 1, "Day 1 Drawings"),
        _unit("Block 04 Calendar.pdf", 1, "Day 1 Powerplant"),
    ]
    _text, names = D.assemble_documents(units)
    assert len(names) == 2


def test_an_all_foreign_response_is_passed_through_rather_than_reported_empty():
    """Reporting an empty library would be a false statement about the source.

    The labels and the leave-content-alone rule already stop the model using
    material that does not answer the instruction; claiming nothing came back
    when something did is the failure this module exists to prevent.
    """
    units = [
        _unit("Block 04 Calendar.pdf", 1, "Day 1 Powerplant"),
        _unit("Block 14 Calendar.docx", 1, "Day 1 Systems"),
    ]
    text, names = D.assemble_documents(units, block="Block 2")
    assert len(names) == 2
    assert text


# ---------------------------------------------------------------------------
# What the user is told
# ---------------------------------------------------------------------------

def test_the_note_no_longer_blames_ingestion():
    """It was ingested. 992 of Block 2's 1,002 units were out of purpose scope.

    The old wording sent the user to the Source Library to look for a document
    that was already there.
    """
    deep = D.DeepContext(text="x", level="search", units=24, levels_tried=("search",),
                         sources=("Block 2 Teacher Calendar.xlsx",),
                         document_types=("syllabus", "course_calendar"))
    note = _noop_note(deep)
    assert "not be ingested" not in note
    assert "course_calendar" in note
    assert "Nothing was saved" in note


FILLED = ("- **Primary Handbooks:** FAA-H-8083-30B — cited on Days 1-13, 17-19; "
          "FAA-H-8083-31B — cited on Days 14-16;")


def _found(**kw):
    base = dict(text="x", level="search", units=25, levels_tried=("search",),
                sources=("Block 2 Teacher Calendar.xlsx",),
                document_types=("syllabus", "course_calendar"))
    base.update(kw)
    return D.DeepContext(**base)


def test_a_complete_cell_is_not_blamed_on_the_source_library():
    """The real run: a full Primary Handbooks line, 25 correct units, and an
    instruction that reduced to "part check once".

    Leading with "the material does not answer the instruction" sent the user to
    the Source Library when the instruction was what said nothing.
    """
    note = _noop_note(_found(), target_text=FILLED)
    assert "already has content" in note
    assert "does not answer the instruction" not in note
    assert "none of it covers what was asked" not in note
    # And it says what would actually help.
    assert "say what should change" in note


def test_an_empty_cell_still_points_at_the_source():
    """Where the cell IS empty, the library genuinely is the thing to look at."""
    for empty in ("", "—", "TBD", "Not documented"):
        note = _noop_note(_found(), target_text=empty)
        assert "does not answer the instruction" in note, empty
        assert "already has content" not in note, empty


def test_the_caller_may_not_know_the_target_and_gets_the_neutral_wording():
    note = _noop_note(_found())
    assert "does not answer the instruction" in note


def test_the_advice_survives_the_empty_lookup_branch():
    note = _noop_note(D.DeepContext(level="search", levels_tried=("search",)),
                      target_text=FILLED)
    assert "already has content" in note
    assert "say what should change" in note
    assert "Nothing was saved" in note


def test_the_note_names_what_was_searched():
    deep = D.DeepContext(text="x", level="search", units=12, levels_tried=("search",),
                         document_types=("syllabus", "course_calendar", "study_questions"))
    assert "study_questions" in _scope_hint(deep)


def test_an_unopened_gate_is_reported_as_such():
    deep = D.DeepContext(text="x", level="search", units=8, levels_tried=("search",),
                         flags=("doc_types_unopened",))
    hint = _scope_hint(deep)
    assert "could not be read" in hint


def test_provenance_carries_the_retrieval_path():
    """Which path served a disappointing result was previously unknowable.

    The keyword fallback ranks by term overlap and has been observed returning
    other blocks' syllabi; the semantic path is a different diagnosis.
    """
    deep = D.DeepContext(text="x", level="search", retrieval_method="s3_keyword",
                         document_types=("syllabus",))
    prov = deep.provenance()
    assert prov["retrieval_method"] == "s3_keyword"
    assert prov["document_types"] == ["syllabus"]
