"""Escalation to source, for gaps the worksheet cannot explain.

The worksheet holds what the reduce concluded, so it can support revising a
cell but never filling an empty one — a cell the pipeline did not populate has
nothing there to read. These tests pin the ladder that goes past it: the day's
digest and ingested units first, then a query over the library for material
attributed to no day at all, then an honest refusal.

The refusal is the point. A model asked to fill a cell with no source produces
something fluent every time, and an invented ACS mapping or page range cannot
be told from a real one downstream. An empty cell is a true statement about the
source library and must survive contact with a regeneration that cannot improve
on it.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.services import cdd_deep_context as D
from app.services import cdd_scoped_regen as S
from app.services.cdd_regen_context import parse_scope
from promptops_app.database import Base, CDDVersion, CourseDesignDocument

TABLE = (
    "| Day | Topic | ACS | Learning Objective | Summative Exam Item Cluster |\n"
    "|---|---|---|---|---|\n"
    "| Day 1 | Drawings | AM.I.B.K1 | Read a title block | Items 1-5 |\n"
    "| Day 4 | Sketching | — | Draw a repair sketch | REVIEW NEEDED — no ACS codes mapped |\n"
)
COLUMNS = ["Day", "Topic", "ACS", "Learning Objective", "Summative Exam Item Cluster"]


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _cdd(db, sections=None, title="Block 2 — General Science II"):
    import json

    doc = CourseDesignDocument(title=title, course_title=title, active_version="v1",
                               course_id=48, project_id=1)
    db.add(doc)
    db.commit()
    db.add(CDDVersion(cdd_id=doc.id, version="v1", full_content="",
                      sections=json.dumps(sections or {}), is_active=True))
    db.commit()
    return doc


# ---------------------------------------------------------------------------
# Gap detection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["", "—", "N/A", "TBD", "NONE DOCUMENTED",
                                   "REVIEW NEEDED — no ACS codes mapped",
                                   "NO AKTR DATA for AM.I.B.K1", "MISSING_SOURCE"])
def test_pipeline_gap_markers_count_as_empty(value):
    """The pipeline states its gaps in its own vocabulary rather than leaving
    cells blank; treating only "" as empty would miss every real one."""
    assert D.is_placeholder(value)


@pytest.mark.parametrize("value", ["Items 1-5", "Read a title block", "Yes", "0"])
def test_real_content_is_not_a_gap(value):
    assert not D.is_placeholder(value)


def test_gaps_are_found_in_writable_cells_only():
    plan = S.plan_rows(TABLE, parse_scope("Rewrite Day 4", known_columns=COLUMNS))
    gaps = dict(S.unfilled_cells(plan))
    assert gaps[4] or gaps  # Day 4 has an empty writable cell
    # The ACS cell is also empty, but it is protected — it is a source problem,
    # not something a regeneration is allowed to fill.
    assert "ACS" not in {c for _, c in S.unfilled_cells(plan)}


def test_a_fully_populated_row_reports_no_gaps():
    plan = S.plan_rows(TABLE, parse_scope("Rewrite Day 1", known_columns=COLUMNS))
    assert S.unfilled_cells(plan) == ()


# ---------------------------------------------------------------------------
# Instruction intent
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("instruction", [
    "Fill the cluster column", "add the handbook rationale", "complete this row",
    "what is missing here", "cite the source",
])
def test_fill_instructions_ask_for_source(instruction):
    assert D.wants_source(instruction)


@pytest.mark.parametrize("instruction", [
    "refer the syllabus document", "refer to the syllabus", "check the syllabus",
    "consult the syllabus", "per the syllabus, correct this",
    "according to the syllabus", "update from the syllabus",
    "verify against the syllabus", "pull the handbooks from the syllabus",
])
def test_consulting_a_named_document_asks_for_source(instruction):
    """The regression. Of fifteen realistic phrasings tried against the original
    verb list, eleven did not trip it — "refer to the syllabus" among them. The
    live CDD 169 instruction escalated only because it happened to contain
    "complete" and "missing" later in the sentence; the short form of the same
    request would have been answered from the worksheet alone, silently."""
    assert D.wants_source(instruction)


@pytest.mark.parametrize("instruction", [
    "tighten the wording", "make it more concise", "use a Bloom verb",
    "shorten the Primary Handbooks line", "rephrase this", "fix the grammar",
])
def test_revision_instructions_do_not(instruction):
    """Revising works from the worksheet; escalating would spend DIS calls to
    reach material the request never needed."""
    assert not D.wants_source(instruction)


# ---------------------------------------------------------------------------
# Block resolution — the thing believed to need a migration
# ---------------------------------------------------------------------------

def test_the_block_is_read_from_worksheet_one(db):
    doc = _cdd(db, {"WORKSHEET 1: BLOCK OVERVIEW": "- **Block:** Block 2\n- **Total Days:** 20"})
    assert D.resolve_block(db, doc) == "Block 2"


def test_the_title_is_the_fallback(db):
    """Enough for a CDD whose worksheets are shaped differently — no stored
    provenance and no backfill required to reach DIS."""
    doc = _cdd(db, {}, title="Block 7 — Powerplant")
    assert D.resolve_block(db, doc) == "Block 7"


def test_an_unresolvable_block_is_empty_not_an_error(db):
    doc = _cdd(db, {}, title="Spring Boot Course Design Document")
    assert D.resolve_block(db, doc) == ""


# ---------------------------------------------------------------------------
# The ladder
# ---------------------------------------------------------------------------

class _FakeDis:
    def __init__(self, day=None, search=None, day_raises=False, search_raises=False):
        self._day, self._search = day, search
        self._day_raises, self._search_raises = day_raises, search_raises
        self.calls = []

    def get_day_context_sync(self, block, day, **kw):
        self.calls.append(("day", block, day))
        if self._day_raises:
            raise RuntimeError("DIS down")
        return self._day or {}

    def retrieve_context_sync(self, purpose, payload, **kw):
        self.calls.append(("search", payload.get("query", "")))
        if self._search_raises:
            raise RuntimeError("DIS down")
        return self._search or {}


@pytest.fixture()
def patch_dis(monkeypatch):
    def _apply(fake):
        import app.core.dis_client as mod
        monkeypatch.setattr(mod, "dis_client", fake)
        monkeypatch.setattr("app.core.dis_access.resolve_course_dis_client",
                            lambda *a, **k: "aim")
        return fake
    return _apply


DAY_BUNDLE = {
    "day_number": 4, "topic": "Sketching",
    "digest": {"derived_objective": "Draw a repair sketch", "acs_codes": []},
    "units": [{"text": "Project 2-3 requires a title block and bill of materials.",
               "source_file": "B2D4.pdf"}],
    "supplement": [], "flags": [],
}


def test_the_day_rung_returns_digest_and_units(db, patch_dis):
    fake = patch_dis(_FakeDis(day=DAY_BUNDLE))
    doc = _cdd(db, {"WORKSHEET 1: BLOCK OVERVIEW": "- **Block:** Block 2"})
    ctx = D.deepen(db, doc, scope=parse_scope("fill Day 4"), instruction="fill Day 4",
                   current_user=None)
    assert ctx.level == "day_context"
    assert "Draw a repair sketch" in ctx.text          # the digest
    assert "bill of materials" in ctx.text             # the raw unit
    assert ctx.units == 1
    assert [c[0] for c in fake.calls] == ["day"]       # stopped; no search needed


def test_it_falls_through_to_search_when_the_day_has_nothing(db, patch_dis):
    """Case (c): the material exists but was never attributed to a day, so a
    day-keyed fetch cannot reach it however deep it goes."""
    fake = patch_dis(_FakeDis(day={}, search={"combined_context": "Unattributed ACS excerpt",
                                              "source_units": [1, 2]}))
    doc = _cdd(db, {"WORKSHEET 1: BLOCK OVERVIEW": "- **Block:** Block 2"})
    ctx = D.deepen(db, doc, scope=parse_scope("fill Day 4"), instruction="fill Day 4",
                   current_user=None)
    assert ctx.level == "search"
    assert "Unattributed ACS excerpt" in ctx.text
    assert [c[0] for c in fake.calls] == ["day", "search"]


def test_a_dis_failure_degrades_to_the_next_rung_rather_than_raising(db, patch_dis):
    """A regeneration must never fail because the deep path was unavailable —
    only because there is genuinely nothing to say."""
    patch_dis(_FakeDis(day_raises=True, search={"combined_context": "found anyway"}))
    doc = _cdd(db, {"WORKSHEET 1: BLOCK OVERVIEW": "- **Block:** Block 2"})
    ctx = D.deepen(db, doc, scope=parse_scope("fill Day 4"), instruction="fill Day 4",
                   current_user=None)
    assert ctx.level == "search"


def test_exhausting_the_ladder_reports_nothing_found(db, patch_dis):
    """What the caller turns into SourceUnavailableError — the empty cell is
    kept rather than traded for an invention."""
    patch_dis(_FakeDis(day={}, search={}))
    doc = _cdd(db, {"WORKSHEET 1: BLOCK OVERVIEW": "- **Block:** Block 2"})
    ctx = D.deepen(db, doc, scope=parse_scope("fill Day 4"), instruction="fill Day 4",
                   current_user=None)
    assert not ctx.found
    assert ctx.level == "none"
    assert "day_context" in ctx.levels_tried and "search" in ctx.levels_tried


def test_both_rungs_failing_still_does_not_raise(db, patch_dis):
    patch_dis(_FakeDis(day_raises=True, search_raises=True))
    doc = _cdd(db, {"WORKSHEET 1: BLOCK OVERVIEW": "- **Block:** Block 2"})
    assert not D.deepen(db, doc, scope=parse_scope("fill Day 4"),
                        instruction="x", current_user=None).found


def test_could_not_look_is_distinguished_from_looked_and_found_nothing(db, patch_dis):
    """These are opposite answers about the source library and must never be
    conflated. "Nothing found" says the gap is real. "Could not look" says the
    gap may not exist at all — reporting it as real would have a designer act on
    a hole their library does not have.

    Observed live: DIS could not reach S3, the syllabus lookup failed with
    EndpointConnectionError, and the regeneration returned the section unchanged
    — indistinguishable from success.
    """
    patch_dis(_FakeDis(day={}, search_raises=True))
    doc = _cdd(db, {"WORKSHEET 1: BLOCK OVERVIEW": "- **Block:** Block 2"})
    broken = D.deepen(db, doc, scope=parse_scope("fill Day 4"), instruction="fill",
                      current_user=None)
    assert not broken.found
    assert broken.unavailable          # infrastructure, retryable

    patch_dis(_FakeDis(day={}, search={}))
    empty = D.deepen(db, doc, scope=parse_scope("fill Day 4"), instruction="fill",
                     current_user=None)
    assert not empty.found
    assert not empty.unavailable       # a real gap in the library


def test_a_partial_day_failure_is_still_flagged_unavailable(db, patch_dis):
    patch_dis(_FakeDis(day_raises=True, search={}))
    doc = _cdd(db, {"WORKSHEET 1: BLOCK OVERVIEW": "- **Block:** Block 2"})
    ctx = D.deepen(db, doc, scope=parse_scope("fill Day 4"), instruction="fill",
                   current_user=None)
    assert ctx.unavailable


def test_a_successful_lookup_is_never_unavailable(db, patch_dis):
    patch_dis(_FakeDis(day=DAY_BUNDLE))
    doc = _cdd(db, {"WORKSHEET 1: BLOCK OVERVIEW": "- **Block:** Block 2"})
    ctx = D.deepen(db, doc, scope=parse_scope("fill Day 4"), instruction="fill",
                   current_user=None)
    assert ctx.found and not ctx.unavailable


def test_a_cdd_with_no_block_goes_straight_to_search(db, patch_dis):
    """A legacy CDD has no block to key on, but the library is still searchable
    — which is the whole legacy path."""
    fake = patch_dis(_FakeDis(search={"combined_context": "legacy hit"}))
    doc = _cdd(db, {}, title="Spring Boot CDD")
    ctx = D.deepen(db, doc, scope=parse_scope("fill something"),
                   instruction="fill something", current_user=None)
    assert ctx.level == "search"
    assert [c[0] for c in fake.calls] == ["search"]


def test_the_search_query_carries_the_scope_not_just_the_instruction(db, patch_dis):
    """Anchoring on block and day is what stops the query matching any other
    day's material — the failure that put a 90-page PDF on Day 1."""
    fake = patch_dis(_FakeDis(day={}, search={"combined_context": "x"}))
    doc = _cdd(db, {"WORKSHEET 1: BLOCK OVERVIEW": "- **Block:** Block 2"})
    D.deepen(db, doc, scope=parse_scope("fill Day 4"), instruction="fill Day 4",
             current_user=None)
    query = [c[1] for c in fake.calls if c[0] == "search"][0]
    assert "Block 2" in query and "Day 4" in query


def test_more_days_than_the_cap_are_not_all_fetched(db, patch_dis):
    """Each day is a round-trip; an instruction naming many days gets the first
    few deeply rather than issuing an unbounded burst."""
    fake = patch_dis(_FakeDis(day=DAY_BUNDLE))
    doc = _cdd(db, {"WORKSHEET 1: BLOCK OVERVIEW": "- **Block:** Block 2"})
    D.deepen(db, doc, scope=parse_scope("fill Days 1-8"), instruction="fill",
             current_user=None)
    assert len([c for c in fake.calls if c[0] == "day"]) == D.MAX_DEEP_DAYS


def test_an_empty_bundle_renders_nothing_rather_than_a_header(db, patch_dis):
    patch_dis(_FakeDis(day={"day_number": 4}, search={}))
    doc = _cdd(db, {"WORKSHEET 1: BLOCK OVERVIEW": "- **Block:** Block 2"})
    assert not D.deepen(db, doc, scope=parse_scope("fill Day 4"),
                        instruction="fill", current_user=None).found


# ---------------------------------------------------------------------------
# Query formulation
# ---------------------------------------------------------------------------
#
# Retrieval that returns eight confident, irrelevant units is indistinguishable
# from a broken feature: the model correctly declines, the section comes back
# unchanged, and nothing in the response says the query was the problem. These
# pin the query, because it is the only part of the ladder whose failure is
# silent.


def test_block_variants_cover_how_the_library_actually_files_a_block():
    """The AIM index tags one block three ways — "Block 2" on 935 units, "2" on
    80, and "Block 02" on 10, which is where both copies of the block's syllabus
    live. A query in only Worksheet 1's form cannot reach them."""
    variants = D.block_variants("Block 2")
    assert "Block 2" in variants
    assert "Block 02" in variants          # the syllabus's form
    assert "BLK 2" in variants and "BLK 02" in variants


def test_block_variants_do_not_duplicate_an_already_padded_block():
    variants = D.block_variants("Block 02")
    assert variants.count("Block 02") == 1
    assert "Block 2" in variants


def test_block_variants_of_nothing_is_nothing():
    assert D.block_variants("") == []
    assert D.block_variants("   ") == []


def test_block_variants_survive_a_block_with_no_number():
    assert D.block_variants("Airframe") == ["Airframe"]


def test_the_imperative_is_stripped_from_the_query():
    """The live instruction that failed. Its content is "handbook" and
    "syllabus"; everything else describes the edit, and embedding it ranked
    three other blocks' syllabi above this block's own."""
    query = D.build_search_query(
        block="Block 2",
        instruction=("Primary handbooks do not have all the handbook. refer the "
                     "syllabus document and make it complete without missing any "
                     "applicable for the block."),
    )
    assert "syllabus" in query.lower()
    assert "handbook" in query.lower()
    for filler in ("without", "make", "applicable", "missing", "refer"):
        assert filler not in query.lower().split()


def test_identifiers_in_the_section_become_query_anchors():
    """A handbook code is the strongest anchor this corpus has: it appears in
    the calendars, the syllabi and the slides, and nowhere outside the subject."""
    query = D.build_search_query(
        block="Block 2", instruction="complete the handbook list",
        section_content="- **Primary Handbooks:** FAA-H-8083-30B; FAA-H-8083-31B",
    )
    assert "FAA-H-8083-30B" in query


def test_identifiers_are_matched_by_shape_not_by_vocabulary():
    """The miner must not encode one client's code vocabulary. Anything that
    mixes letters and digits and carries a separator or is fully capitalised is a
    document code in any corpus."""
    assert D.identifier_anchors("see FAA-H-8083-30B") == ["FAA-H-8083-30B"]
    assert D.identifier_anchors("see AC43.13-1B/2B") == ["AC43.13-1B/2B"]
    assert D.identifier_anchors("codes AM.I.B.K1 apply") == ["AM.I.B.K1"]
    assert D.identifier_anchors("slide B2D5 covers it") == ["B2D5"]
    # A different client's scheme, which an enumerated pattern would have missed.
    assert D.identifier_anchors("per ISO-9001-2015") == ["ISO-9001-2015"]


def test_prose_and_bare_figures_are_not_mistaken_for_identifiers():
    for prose in ("cited on Days 1-13, 17-19", "Document Date 07.30.2025 was Aug 14",
                  "passed with a 70% or higher", "Total Days: 20", "Block 02"):
        assert D.identifier_anchors(prose) == [], prose


def test_urls_are_not_anchors():
    """Worksheet 1 lists Web Resources immediately after the handbooks, so a URL
    that qualified would crowd the real codes out of the anchor budget."""
    section = ("- **Primary Handbooks:** FAA-H-8083-30B; FAA-H-8083-31B\n"
               "- **Web Resources:** https://youtu.be/Z3ZO6oRbptM; https://youtu.be/kKSDTSsmiJk\n")
    assert D.identifier_anchors(section) == ["FAA-H-8083-30B", "FAA-H-8083-31B"]


def test_anchors_are_capped_and_deduplicated():
    text = " ".join(["FAA-H-8083-30B"] * 5 + [f"AC4{i}.1-1B" for i in range(9)])
    anchors = D.identifier_anchors(text)
    assert anchors[0] == "FAA-H-8083-30B"
    assert len(anchors) == D.MAX_IDENTIFIER_ANCHORS
    assert len(set(a.lower() for a in anchors)) == len(anchors)


def test_the_query_is_never_empty_even_for_a_pure_imperative():
    """An instruction of nothing but filler still has to search for something;
    an empty query makes retrieval fall back to unranked keyword results."""
    query = D.build_search_query(block="Block 7", instruction="please make it complete")
    assert query.strip()
    assert "Block 7" in query


def test_the_query_is_bounded():
    query = D.build_search_query(
        block="Block 2", instruction="add " + " ".join(f"topic{i}" for i in range(400)),
        course_title="x" * 500, section_content="FAA-H-8083-30B " * 50,
    )
    assert len(query) <= D.MAX_QUERY_CHARS


def test_scope_days_and_acs_codes_still_reach_the_query():
    scope = parse_scope("fill Day 4 AM.I.B.K1", known_columns=COLUMNS)
    query = D.build_search_query(block="Block 2", instruction="fill", scope=scope)
    assert "Day 4" in query
    assert "AM.I.B.K1" in query


def test_the_search_rung_reports_which_documents_it_read(db, patch_dis):
    """A no-op has to name the documents consulted. "These were read and none of
    them covers it" tells the user which document to upload; "nothing happened"
    tells them the feature is broken."""
    patch_dis(_FakeDis(day={}, search={
        "combined_context": ("Source: Block 02 General Science II ACS Syllabus.docx\n"
                             "Title: BLK 02\nRequired Text(s): FAA-H-8083-30B\n\n---\n\n"
                             "Source: Block 2 Teacher Calendar.xlsx\nTitle: Day 2\nx\n"),
        "source_units": [1, 2]}))
    doc = _cdd(db, {"WORKSHEET 1: BLOCK OVERVIEW": "- **Block:** Block 2"})
    ctx = D.deepen(db, doc, scope=parse_scope("fill the handbooks"),
                   instruction="fill the handbooks", current_user=None)
    assert ctx.sources == ("Block 02 General Science II ACS Syllabus.docx",
                           "Block 2 Teacher Calendar.xlsx")
    assert "sources" in ctx.provenance()


def test_sources_are_deduplicated_and_capped():
    text = "\n".join(f"Source: doc{i % 3}.pdf" for i in range(40))
    assert D.source_names(text) == ("doc0.pdf", "doc1.pdf", "doc2.pdf")
    assert len(D.source_names("\n".join(f"Source: d{i}.pdf" for i in range(40)))) == D.MAX_REPORTED_SOURCES


def test_a_day_scoped_query_still_names_the_block_and_the_day(db, patch_dis):
    """The pre-existing contract, kept: the old assembly put block and days in
    the query and that must not regress while the filler handling changes."""
    fake = patch_dis(_FakeDis(day={}, search={"combined_context": "x"}))
    doc = _cdd(db, {"WORKSHEET 1: BLOCK OVERVIEW": "- **Block:** Block 2"})
    D.deepen(db, doc, scope=parse_scope("fill Day 4"), instruction="fill Day 4",
             current_user=None)
    query = [c[1] for c in fake.calls if c[0] == "search"][0]
    assert "Block 2" in query and "Day 4" in query


# ---------------------------------------------------------------------------
# Whole-document assembly
# ---------------------------------------------------------------------------
#
# A syllabus or a calendar is a structured document: its meaning is in the
# relationships between its parts, not in any one part. Relevance-ranked chunks
# destroy exactly that — three day-rows plucked from a twenty-day calendar by
# cosine similarity, with no way for the reader to tell a keyhole view from the
# whole picture. Measured on the live corpus, taking these whole is also cheaper:
# a complete 20-day calendar is ~1,780 tokens against ~5,600 for the eight mixed
# chunks it replaced, because those chunks were spending the budget on other
# blocks' documents.


def _unit(name, number, text, title=""):
    return {"source_file_name": name, "unit_number": number, "text": text,
            "title": title, "job_id": f"job-{name}"}


def test_units_are_regrouped_into_documents_in_document_order():
    """DIS returns relevance order; a calendar has to be read in day order."""
    units = [
        _unit("Calendar.xlsx", 18, "Day 18 — Review"),
        _unit("Syllabus.docx", 1, "Required Text(s): FAA-H-8083-30B"),
        _unit("Calendar.xlsx", 2, "Day 2 — Aircraft Drawings"),
        _unit("Calendar.xlsx", 1, "Day 1 — Intro"),
    ]
    text, sources = D.assemble_documents(units)
    # Grouped: one Source header per document, not one per chunk.
    assert text.count("Source: Calendar.xlsx") == 1
    assert text.count("Source: Syllabus.docx") == 1
    # Ordered by unit number within the document, not by arrival.
    cal = text.split("Source: Calendar.xlsx")[1]
    assert cal.index("Day 1") < cal.index("Day 2") < cal.index("Day 18")
    # Most relevant document first — DIS's ordering is preserved across groups.
    assert sources[0] == "Calendar.xlsx"


def test_consecutive_sections_are_labelled_by_range_not_as_complete():
    """The regression. DIS returns at most max_results units across ALL
    documents, so a 20-day calendar granted ten slots arrives as sections 1..10 —
    contiguous from 1 and half a calendar. Calling that "the complete document"
    is the exact misreading this label exists to prevent, and the response
    carries no per-document total, so completeness is not knowable here."""
    units = [_unit("S.docx", 1, "a"), _unit("S.docx", 2, "b"), _unit("S.docx", 3, "c")]
    text, _ = D.assemble_documents(units)
    assert "sections 1-3, in document order" in text
    assert "complete" not in text.lower()


def test_a_partial_document_is_labelled_as_partial():
    """The failure this exists to prevent: a model treating three of twenty
    calendar days as the whole calendar."""
    units = [_unit("Cal.xlsx", 2, "day 2"), _unit("Cal.xlsx", 18, "day 18")]
    text, _ = D.assemble_documents(units)
    assert "sections 2, 18 only — non-consecutive extract" in text
    assert "complete" not in text.lower()


def test_a_single_unit_document_is_labelled_as_its_one_section():
    """Both AIM syllabi are one unit each, so this is the common case."""
    text, _ = D.assemble_documents([_unit("Syllabus.docx", 1, "Required Text(s): x")])
    assert "sections 1-1, in document order" in text


def test_the_preamble_warns_that_an_extract_is_not_the_document():
    """Otherwise a reader concludes that what is missing from an extract is
    missing from the source — making a real gap and a retrieval boundary
    indistinguishable, which is the confusion this whole module exists to end."""
    text, _ = D.assemble_documents([_unit("S.docx", 1, "x")])
    assert "not retrieved rather than as absent" in text


def test_an_oversized_document_arrives_whole_and_is_never_trimmed():
    """Inverted deliberately. This used to assert the document was clipped to
    MAX_DOCUMENT_TOKENS on the reasoning that "a truncated syllabus still answers
    what its opening covers" — but the model cannot tell a clipped document from a
    complete one, so it answers "not covered" about text sitting just past the cut,
    with the same confidence it would have had reading the whole thing. A partial
    document presented as whole is worse than a slower prompt."""
    huge = [_unit("Big.pdf", i, f"chunk {i} " + "word " * 400) for i in range(1, 40)]
    text, sources = D.assemble_documents(huge)
    assert sources == ("Big.pdf",)
    assert "chunk 1" in text
    assert "chunk 39" in text, "the tail of the document must survive"
    from promptops_app.core.config import count_tokens
    # Comfortably past the 5,000-token cap that used to apply here.
    assert count_tokens(text) > 5000


def test_a_document_the_budget_excludes_is_named_not_silently_dropped():
    """Omission is still a loss of context — but a NAMED one, which the caller can
    put in front of the user. The failure being prevented is the silent kind."""
    units = ([_unit("First.docx", 1, "alpha " * 600)]
             + [_unit("Second.docx", 1, "beta " * 600)])
    left_out: list = []
    text, sources = D.assemble_documents(units, token_budget=700, omitted=left_out)
    assert sources == ("First.docx",)
    assert left_out == ["Second.docx"]
    assert "beta" not in text, "excluded whole, not partially included"


def test_the_omitted_list_is_optional_and_absent_callers_still_work():
    units = [_unit("Only.docx", 1, "alpha " * 10)]
    text, sources = D.assemble_documents(units)
    assert sources == ("Only.docx",)


def test_the_budget_drops_whole_documents_not_halves_of_each():
    """Coherence is the point, so the budget is spent a document at a time. The
    first (most relevant) document always survives."""
    units = ([_unit("First.docx", 1, "alpha " * 600)]
             + [_unit("Second.docx", 1, "beta " * 600)])
    text, sources = D.assemble_documents(units, token_budget=700)
    assert sources == ("First.docx",)
    assert "beta" not in text


def test_the_most_relevant_document_survives_an_exhausted_budget():
    units = [_unit("Wanted.docx", 1, "gamma " * 300), _unit("Other.docx", 1, "delta " * 300)]
    _, sources = D.assemble_documents(units, token_budget=1)
    assert sources == ("Wanted.docx",)


def test_units_without_text_or_name_are_ignored():
    units = [_unit("A.docx", 1, "   "), {"unit_number": 1, "text": "orphan"},
             "not a dict", _unit("B.docx", 1, "real")]
    text, sources = D.assemble_documents(units)
    assert sources == ("B.docx",)
    assert "orphan" not in text


def test_no_units_assembles_to_nothing():
    assert D.assemble_documents([]) == ("", ())


def test_the_search_rung_asks_dis_for_a_whole_document_worth_of_units(db, patch_dis):
    """DIS defaults to 8 units / 6,000 tokens, which cannot express a 20-day
    calendar however it is ranked."""
    captured = {}

    class _Cap(_FakeDis):
        def retrieve_context_sync(self, purpose, payload, **kw):
            captured.update(payload)
            return {"source_units": [_unit("S.docx", 1, "Required Text(s): x")]}

    patch_dis(_Cap(day={}))
    doc = _cdd(db, {"WORKSHEET 1: BLOCK OVERVIEW": "- **Block:** Block 2"})
    ctx = D.deepen(db, doc, scope=parse_scope("fill the handbooks"),
                   instruction="fill the handbooks from the syllabus", current_user=None)
    assert captured["retrieval"]["top_k"] == D.SEARCH_TOP_K
    assert captured["retrieval"]["token_budget"] == D.SEARCH_TOKEN_BUDGET
    # DIS trimming before this module can group would defeat the assembly.
    assert D.SEARCH_TOKEN_BUDGET > D.SEARCH_TOKENS
    assert "sections 1-1, in document order" in ctx.text
    assert ctx.sources == ("S.docx",)


def test_a_response_without_per_unit_detail_still_produces_context(db, patch_dis):
    """Older DIS, or the keyword path returning a pre-joined blob: degrade to the
    flat text rather than to nothing, and flag it without claiming the library
    was unreachable."""
    patch_dis(_FakeDis(day={}, search={"combined_context": "Source: X.docx\nsome text"}))
    doc = _cdd(db, {"WORKSHEET 1: BLOCK OVERVIEW": "- **Block:** Block 2"})
    ctx = D.deepen(db, doc, scope=parse_scope("fill it"),
                   instruction="fill it from the syllabus", current_user=None)
    assert ctx.found
    assert "some text" in ctx.text
    assert "search_units_missing" in ctx.flags
    # Must NOT read as an infrastructure failure — that would raise a 503.
    assert not ctx.unavailable


def test_the_unit_request_exceeds_a_single_structural_document():
    """A 20-day calendar is 20 units by itself, so a request that only just fits
    one document leaves nothing for the syllabus beside it. Both halves of the
    pair matter: DIS clamps to the tenant's max_results, which aim.yaml raises to
    match this."""
    assert D.SEARCH_TOP_K >= 49   # Block 5, the measured maximum
    # And the token budget asked of DIS must still exceed what we keep, or DIS
    # would trim documents before this module could group them.
    assert D.SEARCH_TOKEN_BUDGET > D.SEARCH_TOKENS


def test_a_clamped_tenant_degrades_to_labelled_partials_not_to_silence():
    """If a tenant leaves max_results low, DIS returns fewer units. That must
    read as a partial extract, never as the whole document."""
    ten_of_twenty = [_unit("Cal.xlsx", i, f"Day {i}") for i in range(1, 11)]
    text, sources = D.assemble_documents(ten_of_twenty)
    assert sources == ("Cal.xlsx",)
    assert "sections 1-10, in document order" in text
    assert "complete" not in text.lower()
    assert "not retrieved rather than as absent" in text
