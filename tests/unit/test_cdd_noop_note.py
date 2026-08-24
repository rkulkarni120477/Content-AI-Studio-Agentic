"""What the user is told when a regeneration changes nothing.

An unchanged section is a legitimate outcome — the grounded prompt tells the
model to leave content alone rather than guess — but the three ways of arriving
there need different answers, and conflating them sends the user to debug the
wrong thing.

Observed live on CDD 169: the search rung returned eight source units and the
toast read "search lookup found nothing". The user went looking for a broken
retrieval path. Retrieval was working; the documents it returned belonged to
other blocks, which is a completely different fix.
"""

from app.api.v1.routers.cdd import _noop_note
from app.services.cdd_deep_context import DeepContext


def test_retrieved_but_unhelpful_does_not_claim_nothing_was_found():
    """The regression. Eight units came back; saying "found nothing" is false and
    points the user at infrastructure instead of at the corpus."""
    note = _noop_note(DeepContext(
        text="### SEARCH RESULTS\nSource: Block 12 Syllabus.docx\n",
        level="search", units=8, levels_tried=("search",),
        sources=("Block 12 Syllabus.docx",),
    ))
    assert "found nothing" not in note
    assert "8" in note
    assert "Block 12 Syllabus.docx" in note
    assert "Nothing was saved" in note


def test_it_points_at_the_source_library_when_the_answer_was_not_among_the_hits():
    note = _noop_note(DeepContext(
        text="x", level="search", units=3, levels_tried=("search",),
        sources=("a.docx", "b.xlsx"),
    ))
    assert "Source Library" in note


def test_many_sources_are_summarised_rather_than_listed_in_full():
    note = _noop_note(DeepContext(
        text="x", level="search", units=9, levels_tried=("search",),
        sources=tuple(f"doc{i}.pdf" for i in range(9)),
    ))
    assert "and others" in note
    assert "doc8.pdf" not in note


def test_a_lookup_that_really_came_back_empty_says_so():
    note = _noop_note(DeepContext(level="none", levels_tried=("day_context", "search")))
    assert "came back empty" in note
    assert "Nothing was saved" in note


def test_no_escalation_is_reported_as_no_escalation():
    """A revise-only instruction never touches the library, so neither verdict
    about the library applies."""
    note = _noop_note(DeepContext())
    assert "No source lookup was needed" in note


def test_units_without_parsed_source_names_still_produce_a_usable_note():
    """`sources` is parsed from the retrieved text, so a response in an
    unexpected shape leaves it empty. The count is still worth reporting."""
    note = _noop_note(DeepContext(text="x", level="search", units=5,
                                  levels_tried=("search",)))
    assert "5 unit(s)" in note
    assert "Nothing was saved" in note
    # No empty parenthetical where the document list would have gone.
    assert "()" not in note


def test_source_names_without_a_unit_count_are_still_counted():
    """The two fields come from different places — the count from the retrieval
    response, the names parsed out of its text — so either can arrive alone."""
    note = _noop_note(DeepContext(text="x", level="search", units=0,
                                  levels_tried=("search",),
                                  sources=("a.docx", "b.docx")))
    assert "2 unit(s)" in note
    assert "a.docx" in note


# ---------------------------------------------------------------------------
# Completeness — naming what the model did not see in full.
# ---------------------------------------------------------------------------

def _deep(**kw):
    from app.services.cdd_deep_context import DeepContext
    base = {"text": "### SOURCE LIBRARY DOCUMENTS\nbody", "level": "search", "units": 12,
            "levels_tried": ("search",), "sources": ("Block 2 Teacher Calendar.xlsx",)}
    base.update(kw)
    return DeepContext(**base)


def test_a_fully_read_result_carries_no_completeness_caveat():
    """The normal case. A note that always warns about incompleteness teaches the
    reader to ignore the warning."""
    from app.api.v1.routers.cdd import _completeness_hint
    assert _completeness_hint(_deep()) == ""


def test_an_unread_document_is_named_with_the_action_that_reaches_it():
    from app.api.v1.routers.cdd import _completeness_hint
    hint = _completeness_hint(_deep(omitted_documents=("Block 2 Study Questions.docx",)))
    assert "Block 2 Study Questions.docx" in hint
    assert "not read" in hint
    assert "Naming one in the instruction" in hint


def test_a_partially_read_document_says_it_cannot_rule_the_answer_out():
    """The more dangerous of the two: it DID inform the answer, so a "not covered"
    conclusion drawn from it looks as well-sourced as any other."""
    from app.api.v1.routers.cdd import _completeness_hint
    hint = _completeness_hint(_deep(incomplete_documents=("Block 2 Handbook.pdf",)))
    assert "Block 2 Handbook.pdf" in hint
    assert "read only in part" in hint
    assert "rules out" in hint


def test_the_two_kinds_of_loss_stay_distinguishable():
    """They demand opposite actions — name it vs narrow the request — so collapsing
    them into one sentence would leave the reader unable to tell which they have."""
    from app.api.v1.routers.cdd import _completeness_hint
    hint = _completeness_hint(_deep(omitted_documents=("Never.docx",),
                                    incomplete_documents=("Partial.pdf",)))
    assert hint.index("Never.docx") < hint.index("Partial.pdf")
    assert "not read" in hint and "read only in part" in hint


def test_a_long_omission_list_is_bounded_but_the_note_says_so():
    from app.api.v1.routers.cdd import _NOTE_SOURCE_LIMIT, _completeness_hint
    names = tuple(f"Doc {i}.docx" for i in range(_NOTE_SOURCE_LIMIT + 3))
    hint = _completeness_hint(_deep(omitted_documents=names))
    assert hint.count(".docx") == _NOTE_SOURCE_LIMIT
    assert ", and others" in hint


def test_the_hint_survives_a_deep_context_without_the_fields():
    """Defensive: the note layer must never raise on an older DeepContext shape —
    a regeneration failing because its EXPLANATION failed is the worst trade."""
    from app.api.v1.routers.cdd import _completeness_hint

    class _Bare:
        pass

    assert _completeness_hint(_Bare()) == ""
