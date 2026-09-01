"""Offline unit tests for services.digests.references — ASSIGNED READING.

The AIM calendars state, per day, exactly which handbook chapter that day teaches
from ("Reading: FAA-H-8083-31B / Ch. 13 pgs. 13-1 to 13-14"). Nothing read it, so
18 of Block 9's 20 days were summarised from the calendar row alone. This module
parses that citation, finds the named handbook among the ingested files, locates
the cited chapter inside its chunk sequence, and bounds what it attaches.

Every function under test here is pure — dicts in, dicts out, no DB, no LLM — so
these fixtures are hand-built to the shape ``dis_content_units`` rows arrive in
(``text_content`` + ``metadata_json.chunk_index`` + ``.source_file_name``).
"""
import pytest

from services.digests import references


# --------------------------------------------------------------------------- #
# 1. Citation parsing
# --------------------------------------------------------------------------- #
def test_parses_short_reading_spelling():
    """Blocks 5/7/8/9/10/14/15 write the marker as a bare "Reading:"."""
    cits = references.parse_citations(
        "Day 4. Reading: FAA-H-8083-31B Ch. 13 pgs. 13-1 to 13-14")
    assert len(cits) == 1, f"the short 'Reading:' spelling did not parse: {cits}"
    assert (cits[0].handbook, cits[0].chapter) == ("8083-31B", 13)


def test_parses_long_reference_reading_spelling():
    """Block 2 writes the same thing as "Reference reading:"; both must parse."""
    cits = references.parse_citations(
        "Reference reading: FAA-H-8083-30B Ch. 4 pgs 4-1 to 4-9.")
    assert len(cits) == 1, f"the 'Reference reading:' spelling did not parse: {cits}"
    assert (cits[0].handbook, cits[0].chapter) == ("8083-30B", 4)


def test_two_physical_line_citation_keeps_chapter_and_pages():
    """REGRESSION — the citation is authored across two physical lines, and a
    line-bounded capture returned the handbook alone. That is precisely what the
    delivered workbook showed: a bare "FAA-H-8083-31B" with the chapter and page
    range silently dropped, so the day got the whole book or nothing.

    The body must therefore run to the next BLANK line, not to end-of-line.
    """
    day_text = ("Day 1 | LANDING GEAR SYSTEMS | Review syllabus.\n"
                "\n"
                "Reading: FAA-H-8083-31B\n"
                "Ch. 13 pgs. 13-1 to 13-14\n"
                "\n"
                "Project A27: start (due day 4)")
    cits = references.parse_citations(day_text)
    assert len(cits) == 1
    c = cits[0]
    assert c.handbook == "8083-31B"
    assert c.chapter == 13, (
        "the chapter on the citation's SECOND physical line was dropped — the "
        f"capture stopped at end-of-line again (parsed: {c})")
    assert (c.page_from, c.page_to) == (1, 14), f"page range lost: {c}"
    assert c.describe() == "8083-31B Ch. 13 pgs. 13-1 to 13-14"


def test_reading_none_yields_no_citation():
    """Days with no assigned reading say "Reading: None". Inventing a citation
    there would send an unlocatable handbook code to _match_file and raise a
    READING_NOT_INGESTED flag on a day that assigns nothing."""
    assert references.parse_citations("Reading: None") == []
    assert references.parse_citations("Reading: None\n\nCumulative Exam") == []


def test_day_citing_two_handbooks_yields_two_citations():
    """Some Block 14/15 days pair a handbook chapter with an AC paragraph. The
    caller must not assume one citation per day."""
    day_text = ("Reading: FAA-H-8083-31B\n"
                "Ch. 13 pgs. 13-1 to 13-14\n"
                "\n"
                "Reading: AC 43.13-1B\n"
                "Pg. 9-1 to 9-3 paragraph 9-4")
    cits = references.parse_citations(day_text)
    assert [c.handbook for c in cits] == ["8083-31B", "AC43.13-1B"], (
        f"a day citing two handbooks resolved to {len(cits)} citation(s): {cits}")
    assert [c.chapter for c in cits] == [13, 9]


@pytest.mark.xfail(strict=True, reason=(
    "DEFECT: parse_citations extracts only the FIRST handbook per citation block. "
    "_HANDBOOK_RE is applied with .search() inside normalize_handbook, so when a "
    "day pairs two works inside one 'Reading:' paragraph — no blank line between "
    "them — the second is silently dropped and that day never receives it. Only "
    "the blank-line-separated form (see the test above) yields two citations."))
def test_two_handbooks_inside_one_reading_block_yields_two():
    cits = references.parse_citations(
        "Reading: FAA-H-8083-31B Ch. 13 pgs. 13-1 to 13-4; AC 43.13-1B Pg. 9-1 to 9-3")
    assert [c.handbook for c in cits] == ["8083-31B", "AC43.13-1B"]


def test_chapter_inferred_from_page_tokens_when_no_ch_given():
    """The AC 43.13 days are written "Pg. 9-1 to 9-3 paragraph 9-4" with no "Ch."
    anywhere — but FAA page numbers are chapter-prefixed, so this is chapter 9
    unambiguously. Without the inference these days fall through to the whole-file
    branch of units_for_citation."""
    cits = references.parse_citations("Reading: AC43.13-1B Pg. 9-1 to 9-3 paragraph 9-4")
    assert len(cits) == 1
    assert cits[0].chapter == 9, (
        f"chapter not inferred from chapter-prefixed page tokens: {cits[0]}")
    assert (cits[0].page_from, cits[0].page_to) == (1, 4)


def test_handbook_code_digits_are_not_read_as_a_page_range():
    """"8083-31B" is chapter-page shaped. If the handbook code is not removed
    before the page scan, a citation with no "Ch." infers chapter 8083."""
    cits = references.parse_citations("Reading: FAA-H-8083-31B")
    assert len(cits) == 1
    assert cits[0].chapter is None, (
        f"the handbook code's own digits leaked into the page scan: {cits[0]}")


def test_proofreading_does_not_match():
    """The marker is a whole word. "Proofreading:" ends in "reading:" and would
    otherwise capture whatever followed it as an assigned handbook."""
    assert references.parse_citations("Proofreading: FAA-H-8083-31B Ch. 2") == []
    assert references.parse_citations("Spreading: 8083-31B Ch. 2") == []


# --------------------------------------------------------------------------- #
# 2. Handbook -> ingested file matching
# --------------------------------------------------------------------------- #
def _files(*names):
    """A units_by_file map — _match_file only reads the keys."""
    return {n: [] for n in names}


def test_normalize_handbook_canonicalises_both_authoring_styles():
    assert references.normalize_handbook("FAA-H-8083-31B") == "8083-31B"
    assert references.normalize_handbook("faa-h-8083-31b") == "8083-31B"
    assert references.normalize_handbook("8083-31B") == "8083-31B"
    assert references.normalize_handbook("AC 43.13-1B") == "AC43.13-1B"
    assert references.normalize_handbook("AC43.13-1B") == "AC43.13-1B"
    assert references.normalize_handbook("no handbook here") == ""


def test_faa_handbook_matches_file_without_the_faa_h_prefix():
    """The calendars cite "FAA-H-8083-31B"; the ingested file is "8083-31B.pdf".
    An equality test finds nothing."""
    files = _files("8083-31B.pdf", "OEG-AMT5.pdf")
    assert references._match_file("8083-31B", files) == "8083-31B.pdf"


def test_shortest_matching_filename_wins():
    """A file that merely MENTIONS the handbook must not outrank the handbook."""
    files = _files("Block 9 notes on 8083-31B chapter 13.pdf", "8083-31B.pdf")
    assert references._match_file("8083-31B", files) == "8083-31B.pdf"


@pytest.mark.parametrize("cited", ["AC 43.13-1B", "AC43.13-1B"])
def test_ac_series_fallback_matches_a_later_edition(cited):
    """REGRESSION — AIM ingested AC43.13-2025; the calendars cite the -1B edition,
    so the exact code never appears in the filename and the series fallback has to
    fire. It could not: the fallback matched its ``AC\\d+\\.\\d+`` pattern against
    the SQUASHED code, from which the dot had already been stripped, making the
    regex unmatchable. 26 Block 9-15 day rows reported the handbook as "not
    ingested" while it sat in the index under a later revision.

    Both spellings must work — the space in "AC 43.13-1B" is authored, not
    canonical."""
    files = _files("AC43.13-2025.pdf", "8083-31B.pdf")
    handbook = references.normalize_handbook(cited)
    assert references._match_file(handbook, files) == "AC43.13-2025.pdf", (
        f"the AC series fallback did not fire for {cited!r} "
        f"(normalised {handbook!r}) against {sorted(files)}")


def test_unmatched_handbook_resolves_to_zero_units_and_a_reason():
    """48 AIM day rows cite 8083-32B, which is not ingested at all. That is a real
    coverage gap: it must reach a reason string (and thence a READING_NOT_INGESTED
    flag), never a silently thinner day."""
    citation = references.Citation(raw="", handbook="8083-32B", chapter=3)
    units, reason = references.units_for_citation(citation, _files("8083-31B.pdf"))
    assert units == []
    assert "8083-32B" in reason and "not ingested" in reason, (
        f"an un-ingested handbook produced no usable reason: {reason!r}")


# --------------------------------------------------------------------------- #
# 3. Chapter location
# --------------------------------------------------------------------------- #
def _chunk(index, text, filename="8083-31B.pdf"):
    return {"content_unit_id": f"u{index}", "unit_type": "chunk", "text_content": text,
            "metadata_json": {"chunk_index": index, "source_file_name": filename}}


def _synthetic_handbook():
    """A handbook whose chunks carry ``[Figure N-M]`` markers, as the real ones do.

    Layout (positions into the returned list):
        0-2    table of contents, dominated by chapter-13 page tokens
        3-10   chapter 10        11-18  chapter 11        19-26  chapter 12
        27-36  chapter 13
        37-44  chapter 14
        45-47  index, also dominated by chapter-13 page tokens
    """
    units, i = [], 0
    for _ in range(3):
        units.append(_chunk(i, "Table of contents: Chapter 13 Landing Gear 13-1 13-2 13-3"))
        i += 1
    for chapter in (10, 11, 12):
        for k in range(8):
            units.append(_chunk(i, f"prose [Figure {chapter}-{k + 1}] see "
                                   f"[Figure {chapter}-{k + 2}] more"))
            i += 1
    for k in range(10):
        units.append(_chunk(i, f"landing gear [Figure 13-{k + 1}] hydraulic "
                               f"[Figure 13-{k + 2}]"))
        i += 1
    for k in range(8):
        units.append(_chunk(i, f"prose [Figure 14-{k + 1}] see [Figure 14-{k + 2}]"))
        i += 1
    for _ in range(3):
        units.append(_chunk(i, "Index: landing gear 13-4, retraction 13-5, shock strut 13-6"))
        i += 1
    return units


def test_chapter_runs_finds_contiguous_chapter_spans():
    runs = references.chapter_runs(_synthetic_handbook())
    assert runs.get(13) == (27, 36), (
        f"chapter 13's run should be exactly its own chunks 27..36, got {runs.get(13)}")
    # Chapters must partition the book, not overlap.
    assert runs.get(12) == (19, 26) and runs.get(14) == (37, 44)
    ordered = sorted(runs.items())
    for (_c1, (_lo1, hi1)), (_c2, (lo2, _hi2)) in zip(ordered, ordered[1:]):
        assert hi1 < lo2, f"chapter runs overlap: {ordered}"


def test_table_of_contents_and_index_do_not_stretch_a_chapter_run():
    """Chapter 13's number also saturates the TOC (positions 0-2) and the index
    (45-47), hundreds of chunks from the chapter in the real book. Taking
    min..max over every position naming chapter 13 would swallow the whole
    volume, so only the longest near-contiguous run counts."""
    runs = references.chapter_runs(_synthetic_handbook())
    lo, hi = runs[13]
    assert lo > 2, f"the table of contents was absorbed into chapter 13's run: {(lo, hi)}"
    assert hi < 45, f"the index was absorbed into chapter 13's run: {(lo, hi)}"
    assert hi - lo + 1 <= 12, (
        f"chapter 13's run stretched to {hi - lo + 1} chunks; the chapter is 10")


def test_short_cross_reference_cluster_is_not_mistaken_for_a_chapter():
    """A couple of chunks that merely cite chapter 7 are cross-references, not
    chapter 7. Below _MIN_CHAPTER_CHUNKS they must not register as a run at all,
    or a citation for chapter 7 would resolve to two unrelated chunks."""
    units = _synthetic_handbook()
    units.insert(20, _chunk(999, "see also [Figure 7-1] and [Figure 7-2] for detail"))
    runs = references.chapter_runs(units)
    assert 7 not in runs, f"a lone cross-reference chunk registered as chapter 7: {runs.get(7)}"


def test_located_chapter_attaches_exactly_that_chapter():
    units = _synthetic_handbook()
    by_file = references.index_by_file(units)
    citation = references.Citation(raw="", handbook="8083-31B", chapter=13,
                                   page_from=1, page_to=14)
    picked, reason = references.units_for_citation(citation, by_file)
    assert reason == "", f"an exactly located chapter should carry no caveat: {reason!r}"
    assert [u["content_unit_id"] for u in picked] == [f"u{i}" for i in range(27, 37)]


def test_index_by_file_groups_and_orders_by_chunk_index():
    """Reading order is imposed here, not in SQL (a cast on chunk_index would
    raise on one malformed row and fail the whole block's enumeration)."""
    units = [_chunk(5, "e"), _chunk(1, "a"), _chunk(3, "c", "AC43.13-2025.pdf"),
             {"content_unit_id": "orphan", "text_content": "x", "metadata_json": {}}]
    by_file = references.index_by_file(units)
    assert sorted(by_file) == ["8083-31B.pdf", "AC43.13-2025.pdf"]
    assert [u["content_unit_id"] for u in by_file["8083-31B.pdf"]] == ["u1", "u5"]
    # A unit with no source filename is dropped rather than grouped under "".
    assert all("orphan" not in [u["content_unit_id"] for u in v] for v in by_file.values())


def test_unordered_chunks_still_slice_correctly():
    """chapter_runs indexes into the sequence it is given, so the caller's ordering
    is load-bearing; index_by_file must be what supplies it."""
    units = _synthetic_handbook()
    shuffled = units[::-1]
    by_file = references.index_by_file(shuffled)
    citation = references.Citation(raw="", handbook="8083-31B", chapter=13)
    picked, reason = references.units_for_citation(citation, by_file)
    assert reason == ""
    assert [u["content_unit_id"] for u in picked] == [f"u{i}" for i in range(27, 37)]


# --------------------------------------------------------------------------- #
# 4. Bounding — what happens when the chapter cannot be located
# --------------------------------------------------------------------------- #
def _sized_file(n_units, chars_each, body="hydraulic brake landing gear servicing "):
    text = (body * (chars_each // len(body) + 1))[:chars_each]
    return [_chunk(i, f"[Figure 5-{i + 1}] {text}") for i in range(n_units)]


def test_small_cited_file_with_unlocatable_chapter_attaches_whole():
    """The calendar's statement that a day reads a given handbook is authored
    fact. A chapter that cannot be located (8083-30B's ingestion restarts its
    figure numbering, so none of its chapters can be) must degrade to the whole
    file, never to nothing — and must say so."""
    units = _sized_file(6, 2_000)
    by_file = references.index_by_file(units)
    citation = references.Citation(raw="", handbook="8083-31B", chapter=13)
    picked, reason = references.units_for_citation(citation, by_file)
    assert len(picked) == 6, "an under-budget file with no locatable chapter was not attached whole"
    assert reason, "the whole-file fallback is an imprecise attachment and must carry a reason"
    assert "chapter 13 could not be located" in reason and "whole file attached" in reason


def test_oversized_cited_file_falls_back_to_topical_selection_within_budget():
    """8083-31B is ~4M characters. Attaching it whole is not an option, so the
    passages closest to the day's own topic are used — and the result must stay
    inside the budget, in reading order, with the substitution reported."""
    budget = references._WHOLE_FILE_CHAR_BUDGET
    units = _sized_file(120, 5_000)                      # 600k chars > 400k budget
    total = sum(len(u["text_content"]) for u in units)
    assert total > budget, "fixture is not actually over budget"
    by_file = references.index_by_file(units)
    citation = references.Citation(raw="", handbook="8083-31B", chapter=13)
    day = {"topic": "Hydraulic brake systems", "lesson_title": "Landing gear servicing"}

    picked, reason = references.units_for_citation(citation, by_file, day)

    assert picked, "an oversized cited file returned nothing at all"
    picked_chars = sum(len(u["text_content"]) for u in picked)
    assert picked_chars <= budget, (
        f"topical fallback attached {picked_chars:,} chars, over the "
        f"{budget:,}-char budget")
    assert picked_chars < total, "nothing was actually bounded"
    assert reason and "too large to attach whole" in reason, (
        f"a topical substitution must never look like the cited pages: {reason!r}")
    indices = [u["metadata_json"]["chunk_index"] for u in picked]
    assert indices == sorted(indices), (
        "relevance-ranked fragments were handed to the model out of reading order")


def test_oversized_file_with_no_topic_terms_reports_that_nothing_was_attached():
    """The one case that legitimately yields no units still must not be silent:
    an empty result with an empty reason is indistinguishable downstream from a
    day that assigned no reading."""
    by_file = references.index_by_file(_sized_file(120, 5_000))
    citation = references.Citation(raw="", handbook="8083-31B", chapter=13)
    picked, reason = references.units_for_citation(citation, by_file, {"topic": "", "lesson_title": ""})
    assert picked == []
    assert "NOTHING attached" in reason, f"a silently empty attachment: {reason!r}"


def test_citation_naming_no_chapter_says_so():
    units = _sized_file(4, 1_000)
    by_file = references.index_by_file(units)
    citation = references.Citation(raw="", handbook="8083-31B", chapter=None)
    picked, reason = references.units_for_citation(citation, by_file)
    assert len(picked) == 4
    assert "names no chapter" in reason, reason


def test_no_resolution_path_returns_units_without_a_reason_only_when_exact():
    """Contract check across all four branches: units and reason are never BOTH
    empty, and reason is empty only on an exact chapter hit."""
    by_file = references.index_by_file(_synthetic_handbook())
    cases = [
        references.Citation(raw="", handbook="8083-31B", chapter=13),   # exact
        references.Citation(raw="", handbook="8083-31B", chapter=99),   # unlocatable
        references.Citation(raw="", handbook="8083-31B", chapter=None),  # no chapter
        references.Citation(raw="", handbook="8083-32B", chapter=1),    # not ingested
    ]
    for citation in cases:
        units, reason = references.units_for_citation(citation, by_file, {"topic": "gear"})
        assert units or reason, f"{citation} resolved to nothing, silently"
        if not reason:
            assert citation.chapter == 13, f"{citation} claimed an exact resolution"


# --------------------------------------------------------------------------- #
# resolve_day — the per-day entry point
# --------------------------------------------------------------------------- #
def test_resolve_day_dedupes_units_and_keeps_unresolved_citations():
    by_file = references.index_by_file(_synthetic_handbook())
    day = {"day_number": 4, "topic": "Landing gear",
           "source_text": ("Reading: FAA-H-8083-31B\nCh. 13 pgs. 13-1 to 13-14\n"
                           "\n"
                           "Reading: FAA-H-8083-32B\nCh. 2\n"
                           "\n"
                           "Reading: FAA-H-8083-31B\nCh. 13")}
    resolved = references.resolve_day(day, by_file)
    assert len(resolved.citations) == 3
    ids = [u["content_unit_id"] for u in resolved.units]
    assert ids == sorted(set(ids), key=ids.index) and len(ids) == 10, (
        f"the same chapter cited twice was attached twice: {len(ids)} units")
    assert [c.handbook for c, _ in resolved.unresolved] == ["8083-32B"]
    assert "8083-31B Ch. 13 pgs. 13-1 to 13-14" in resolved.label()


def test_resolve_day_on_a_day_with_no_reading():
    resolved = references.resolve_day({"day_number": 20, "source_text": "Reading: None"}, {})
    assert resolved.citations == [] and resolved.units == [] and resolved.unresolved == []
