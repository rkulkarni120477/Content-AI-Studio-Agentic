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
