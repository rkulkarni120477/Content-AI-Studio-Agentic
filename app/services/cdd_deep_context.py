"""Source-backed context for regenerating what the digest pipeline missed.

The worksheet context in ``cdd_regen_context`` is enough to *revise* a cell —
it holds what the reduce concluded. It is definitionally not enough to *fill*
one, because a cell the pipeline never populated has nothing in the worksheet
to read. Neither does the digest, if MAP is what dropped it.

So filling a real gap means escalating past both, to what was actually
ingested. This module is that escalation, and it exists as a ladder rather than
a single fetch because "not captured" has several distinct causes:

===  ==========================================  ==============================
 #   Cause                                       Reached by
===  ==========================================  ==============================
 1   the reduce dropped it                       the worksheet (already had it)
 2   MAP thinned it                              the day's digest
 3   MAP never saw it                            the day's raw ingested units
 4   it was never attributed to any day          a QUERY over the library
 5   it was never ingested at all                nothing — refuse
===  ==========================================  ==============================

Level 4 is why a query step exists at all. CDD 169 carries ``UNATTRIBUTED:78``
— seventy-eight substantive units that could not be placed on any day. A
day-keyed fetch will never return one of them, however deep it goes, because
they have no day. Only a search does.

Level 5 is the one that must not be papered over. A model asked to fill a cell
with no source will produce something fluent every time, and an invented ACS
mapping or page range is indistinguishable from a real one to every reader
downstream. An empty cell is a true statement about the source library; the
caller raises SourceUnavailableError rather than trading it for a plausible
sentence.

Every DIS call here is best-effort: a failure degrades to the next level, and
exhausting the ladder degrades to the worksheet context that already works. A
regeneration must never fail because the deep path was unavailable — only
because there is genuinely nothing to say.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from promptops_app.core.config import count_tokens

_log = logging.getLogger(__name__)

#: Former per-level token caps on the day bundle, retired. They clipped the day's
#: digest JSON and its own source units to fit alongside the worksheet context —
#: but a day's units ARE the evidence the regenerated cell is supposed to rest on,
#: and a clipped prompt produces an equally confident answer from less of it. The
#: bundle is now passed whole; SEARCH_TOKENS documents where the real ceiling is.

#: Ceiling on what the search rung contributes to the prompt.
#:
#: Raised from 4,000 when the rung started assembling whole documents rather
#: than passing DIS's score-ordered chunk list through. Sized from the corpus,
#: not guessed: the ENTIRE cdd-purpose document set for a block is small,
#: because `retrieval.source_type_mapping` restricts that purpose to structural
#: documents (syllabus, course_outline, course_calendar, program_overview,
#: learning_objectives) and excludes the reference material. Measured for
#: Block 2 — 30 units across 4 documents, ~7,900 tokens in total:
#:
#: (An instruction can widen the request beyond that set — see `search_filters` —
#: so this is a bound over a variable corpus, not over a fixed one. Nothing is
#: trimmed to fit it: a document is passed whole or omitted whole and named, so a
#: wider request costs relevance, never fidelity.)
#:
#:     Block 2 Teacher Calendar.xlsx              20 units   ~1,780
#:     Block 02-Student Copy ACS Course Calendar    8 units     ~559
#:     Block 02 General Science II Syllabus         1 unit    ~2,401
#:     Block 02 …ACS Syllabus (Rev. 01.14.26)       1 unit    ~3,158
#:
#: So a whole syllabus plus a whole calendar costs ~4,200 tokens, and the
#: theoretical maximum for a block is under 8,000.
#:
#: Raised well above that measured ceiling because it is no longer a quality knob —
#: nothing is trimmed to fit it any more, so the only thing it can now do is EXCLUDE
#: a whole document. Every model this product targets has a 200,000-token input
#: window at minimum, and the regeneration prompt around this context is a few
#: thousand tokens, so 100,000 stays far inside the smallest real limit while making
#: exclusion essentially unreachable for the corpus as measured — including when an
#: instruction opens the gate to a large reference document, which is the case the
#: old 9,000 could not absorb. It is a model-capacity backstop, not a budget.
SEARCH_TOKENS = 100_000

#: Units requested from DIS. Its default is 8, which is the reason a calendar
#: arrived as three disconnected day rows: with one slot per document-chunk and
#: score ordering across documents, a 20-day calendar cannot fit.
#:
#: The effective value is ``min(this, tenant.retrieval.max_results)``, so this
#: number alone does not decide anything — aim.yaml raises max_results to 50 for
#: the same reason, and each half of the pair is inert without the other. A
#: tenant that leaves the cap at 20 still works: documents come back partial and
#: are LABELLED partial by assemble_documents, rather than silently passing as
#: whole. Degrading, not breaking, is the point.
#:
#: Sized from every block, not one. Measured across the 14 AIM blocks that carry
#: cdd-purpose material: unit counts run 1, 8, 12, 30, 32, 41 (x6) and 49, and
#: token totals peak at ~7,900 (Block 2) with the largest single document at
#: ~3,300. So the TOKEN budget was never the binding constraint — the unit count
#: was, and an earlier value of 40 silently truncated the seven blocks at 41+.
#: 50 covers the measured maximum (49, Block 5) and equals result_size_cap.
SEARCH_TOP_K = 50

#: Token budget asked of DIS, whose own default is 6,000. Must be >= SEARCH_TOKENS
#: or DIS would trim the documents before this module ever sees them, and the
#: assembly below would be carefully regrouping an already-truncated set — the
#: worst case, because the result LOOKS whole. Derived from SEARCH_TOKENS rather
#: than restated, so raising one can never silently leave the other behind, and
#: strictly larger so that DIS is never the component that decides what to drop:
#: it would drop by its own ordering, before the block filter and the whole-document
#: regrouping here have had any say, and without reporting what it removed.
SEARCH_TOKEN_BUDGET = SEARCH_TOKENS * 2

# MAX_DOCUMENT_TOKENS (5000) used to clip any document over that size, which made a
# partial syllabus indistinguishable from a whole one — the model then answered
# "not covered" about text sitting just past the cut. Documents are now passed
# whole, or omitted whole and named (see `assemble_documents`). The constant is
# gone rather than kept "in case": nothing reads it, and a number no caller uses
# is a claim about the system that the system does not make.

#: Days fetched per regeneration. Each is one DIS round-trip; an instruction
#: naming more days than this gets the first few deeply and the rest from the
#: worksheet context, which is still a strict improvement on today.
MAX_DEEP_DAYS = 3

#: Wall-clock budget for the ENTIRE ladder, shared across every rung.
#:
#: The DIS client defaults to 300s (dis_api_timeout_seconds), which is correct
#: for a generation and badly wrong here: this is a supplementary lookup inside
#: a button press. Measured before this existed — a regeneration sat in flight
#: for over four minutes, the browser waiting indefinitely (apiClient uses
#: timeout: 0), and the user reading it as "nothing happened".
#:
#: One shared deadline rather than a per-call timeout, so three day fetches plus
#: a search cannot multiply into six minutes. Whatever the ladder has found by
#: the deadline is what the prompt gets; a rung that never ran is flagged, never
#: silently reported as "nothing there".
#: Sized from measurement, not taste: a real /context/retrieve/cdd against the
#: AIM library took 44.3s to return 26,944 characters and 8 source units. A 45s
#: budget with a 20s per-call cap therefore timed out every search while the
#: service was working perfectly — turning a slow success into a reported
#: outage. Headroom over the measured figure, and far under the client default
#: of 300s that let a single request hang for four minutes.
DEEP_BUDGET_SECONDS = 90.0

#: No single rung may consume the whole budget and starve the next one.
PER_CALL_TIMEOUT_SECONDS = 60.0

#: How much of the user's instruction reaches the retrieval query. See the
#: comment at the query-assembly site for why this is capped rather than passed
#: whole.
INSTRUCTION_QUERY_CHARS = 200

#: Cell values that mean "nothing here" — the pipeline's own vocabulary for a
#: gap, plus the usual empty markers. Detecting these is what distinguishes a
#: request to FILL (escalate to source) from a request to REVISE (do not).
_PLACEHOLDERS = {
    "", "—", "-", "--", "n/a", "na", "tbd", "none", "none documented",
    "not documented", "unknown", "missing",
}
_PLACEHOLDER_PREFIXES = ("review needed", "missing_source", "no aktr data",
                         "no acs codes", "not available")


def is_placeholder(value: str) -> bool:
    """Whether a cell is empty in the sense that matters — no content, or one
    of the pipeline's explicit gap markers."""
    text = (value or "").strip().lower()
    if text in _PLACEHOLDERS:
        return True
    return any(text.startswith(p) for p in _PLACEHOLDER_PREFIXES)


#: Instructions that ask for content to be produced rather than improved. A
#: fill request escalates even when the target cell already has text, because
#: "add the handbook rationale" may need source the worksheet never carried.
_FILL_WORDS = re.compile(
    r"\b(fill|add|complete|populate|missing|supply|provide|expand|source|cite|"
    r"reference|find|recover|restore|"
    # Verbs that ask for material to be CONSULTED rather than produced. Added
    # because the original set missed the most natural way to ask for exactly
    # this: of fifteen realistic phrasings tried against it, eleven did not
    # trip it, "refer to the syllabus" among them. The live instruction on
    # CDD 169 escalated only by accident — it happened to contain "complete"
    # and "missing" further along the sentence. A shorter phrasing of the same
    # request would have been silently answered from the worksheet alone.
    r"refer|consult|check|verify|confirm|review|lookup|include|import|pull|"
    r"crosscheck|cross-check|reconcile|correct)\w*\b",
    re.IGNORECASE,
)

#: Documents the source library holds. Naming one is itself a request to consult
#: it, and several natural phrasings carry no verb from the list above at all —
#: "per the syllabus", "according to the calendar".
#:
#: Deliberately excludes "handbook" and "document". "Primary Handbooks" is a
#: Worksheet 1 FIELD LABEL, so matching it would escalate for instructions that
#: only reword the field ("shorten the Primary Handbooks line") and spend a
#: retrieval round-trip to do it. "document" is too generic to mean anything.
_SOURCE_NOUNS = re.compile(
    r"\b(syllabus|syllabi|calendar|library|teaching outline|instructor guide|"
    r"study questions?|transcript|answer key)\b",
    re.IGNORECASE,
)


def wants_source(instruction: str) -> bool:
    """Whether an instruction needs material from outside the document.

    Two signals, either sufficient: a verb that asks for content to be produced
    or consulted, or the name of a document the library holds. Erring toward
    escalation costs one bounded retrieval; erring against it silently answers a
    source question from the worksheet, which is the failure this module exists
    to prevent.
    """
    text = instruction or ""
    if _FILL_WORDS.search(text) or _SOURCE_NOUNS.search(text):
        return True
    # Naming a KIND of document the library holds is the same request as naming a
    # document. Without this, "add the quiz topics for each day" would match
    # nothing in the two patterns above, be answered from the worksheet alone,
    # and the document types it implies would never be requested — the gate that
    # `document_type_hints` opens would be unreachable from the gate that decides
    # whether to search at all.
    #
    # Strong matches only: `document_type_hints` also recognises words the
    # worksheet itself uses ("exam", "project", "lesson"), which are worth
    # widening a search for and not worth starting one for.
    return names_source_material(text)


# ---------------------------------------------------------------------------
# Which documents the search is allowed to see
# ---------------------------------------------------------------------------
#
# DIS scopes retrieval by PURPOSE, and a purpose is a fixed list of document
# types in tenant config (`retrieval.source_type_mapping`). For AIM's `cdd`
# purpose that list is calendars and syllabi, which — measured against the live
# index — makes 361 of the tenant's 3,723 indexed units eligible, or 10 of the
# 1,002 units carrying a Block 2 tag. The other 992 are not ranked low; they are
# invisible. So an instruction naming the block's Study Questions or its teaching
# outline could not be answered however it was phrased, and the model correctly
# declined every time.
#
# Widening the tenant's `cdd` list would fix that instruction and change what
# every future CDD generation is grounded in, which is not the same decision.
# Instead the gate is opened per request, to exactly what the instruction names:
# DIS treats `filters.purpose` of "all" as "skip the purpose gate" while leaving
# the restricted/visibility gate fully in force, and `filters.document_types`
# then bounds the request. Nothing here can reach answer keys or instructor
# guides — those are blocked by role, by `_is_hard_restricted`, and by
# RESTRICTED_DOC_TYPES below, independently.

#: Document types this module must never ask for, whatever an instruction says.
#: DIS blocks them for role=user regardless; naming them here means a future edit
#: to _DOC_TYPE_HINTS cannot quietly start requesting them either.
RESTRICTED_DOC_TYPES = frozenset({
    "answer_key", "quiz_answer_key", "final_exam_answer_key", "exam_answer_key",
    "instructor_guide", "project_instructor_guide", "project_key",
})

#: Instruction phrase -> document types to open the gate to, and whether naming it
#: is on its own a reason to go and look.
#:
#: Keyed on the document types the corpus actually carries (measured: slide_deck,
#: quiz, final_exam, project, hangar_activity, lesson_pdf, study_questions,
#: project_activity) rather than on type names that sound plausible — five of the
#: names already in the tenant's own `cdd` list (course_outline,
#: program_overview, learning_objectives, block_schedule, lesson_plan) match zero
#: units in this index.
#:
#: STRONG vs WEAK matters, and getting it wrong costs the user money. "Study
#: questions" and "powerpoint" can only mean library material. "Exam", "project",
#: "activity" and "lesson" are ALSO the worksheet's own vocabulary — "Summative
#: Exam Item Cluster" is a Worksheet 4 column heading — so treating them as a
#: reason to search would make "reformat the Summative Exam Item Cluster column"
#: pay for a retrieval round-trip it has no use for. A weak match still widens a
#: search that is happening for another reason; it never starts one.
#:
#: `ebook_reference` is deliberately absent. It is the largest family in the
#: corpus (1,164 units) and opening the gate to it would let it crowd out the
#: calendar it is meant to supplement, for a question the syllabus answers
#: directly. Adding it is a one-line change if a real instruction needs it.
_DOC_TYPE_HINTS: tuple[tuple[re.Pattern[str], tuple[str, ...], bool], ...] = (
    # Strong: unambiguously the library.
    (re.compile(r"\bstudy\s+questions?\b", re.IGNORECASE), ("study_questions",), True),
    (re.compile(r"\bquiz(?:zes|es)?\b", re.IGNORECASE), ("quiz",), True),
    (re.compile(r"\bfinal\s+exams?\b", re.IGNORECASE), ("final_exam",), True),
    (re.compile(r"\bhangar\b", re.IGNORECASE),
     ("hangar_activity", "project_activity"), True),
    (re.compile(r"\b(?:slides?|decks?|powerpoints?|ppt|teaching\s+outlines?)\b",
                re.IGNORECASE), ("slide_deck", "lesson_pdf"), True),
    # AKTR / knowledge-test performance data. Strong: no worksheet column is named
    # this, so the phrase can only be asking for the report itself. The `cdd` purpose
    # already admits the type, so this only matters for an instruction issued against
    # a tenant whose purpose list has not been widened.
    (re.compile(r"\baktr\b|\bmissed\s+codes?\b|\bmiss(?:ed)?\s+rates?\b"
                r"|\bknowledge[\s-]test\b", re.IGNORECASE),
     ("knowledge_test_report",), True),
    # Weak: also the worksheet's own words. Widen, never trigger.
    (re.compile(r"\bexams?\b", re.IGNORECASE), ("final_exam",), False),
    (re.compile(r"\bprojects?\b", re.IGNORECASE), ("project", "project_activity"), False),
    (re.compile(r"\bactivit(?:y|ies)\b", re.IGNORECASE),
     ("hangar_activity", "project_activity"), False),
    (re.compile(r"\blessons?\b", re.IGNORECASE), ("slide_deck", "lesson_pdf"), False),
)

#: Enough to answer a compound instruction ("the quiz and the study questions")
#: without turning one request into a scan of the whole library.
MAX_OPENED_DOC_TYPES = 4


def document_type_hints(instruction: str, *,
                        limit: int = MAX_OPENED_DOC_TYPES) -> tuple[str, ...]:
    """Document types an instruction asks for beyond the CDD purpose's own set.

    Empty for the ordinary case, which leaves retrieval scoped exactly as before.
    Includes weak matches: once a search is happening, widening it to material the
    instruction mentions is free, and the alternative is a search that cannot see
    what was named.
    """
    text = instruction or ""
    out: list[str] = []
    for pattern, types, _strong in _DOC_TYPE_HINTS:
        if not pattern.search(text):
            continue
        for doc_type in types:
            if doc_type in RESTRICTED_DOC_TYPES:
                continue  # unreachable by construction; kept so it stays true
            if doc_type not in out:
                out.append(doc_type)
    return tuple(out[:limit])


def names_source_material(instruction: str) -> bool:
    """Whether an instruction names library material outright.

    Only the strong patterns. This is the half of `_DOC_TYPE_HINTS` that may
    trigger a retrieval on its own; see the table's comment for why the rest may
    not.
    """
    text = instruction or ""
    return any(pattern.search(text) for pattern, _types, strong in _DOC_TYPE_HINTS if strong)


#: Tenant config, not per-request data — so it is fetched once and reused. Short
#: enough that a config change lands without a restart.
PURPOSE_TYPES_TTL_SECONDS = 300.0

#: A failed lookup is cached too, briefly. Without this, a DIS whose /ui-config is
#: down costs an extra round-trip on EVERY regeneration — the failure mode that
#: hurts is the slow one, and retrying it per request is how a degraded dependency
#: becomes a degraded product.
PURPOSE_TYPES_FAILURE_TTL_SECONDS = 30.0

#: This lookup is a detail of a button press, not the work itself. Bounded hard
#: and separately from the ladder: `retrieve_context_sync` carries a docstring
#: explaining that the client's 300s default let one regeneration hang for four
#: minutes, and passing no timeout here would have reproduced it exactly.
PURPOSE_TYPES_TIMEOUT_SECONDS = 10.0

_purpose_types_cache: dict[tuple[str, str], tuple[float, tuple[str, ...]]] = {}


def cdd_document_types(*, current_user, client_id: str,
                       deadline: float | None = None) -> tuple[str, ...]:
    """The document types the tenant's `cdd` purpose admits.

    Read from DIS rather than restated here: this module opening the gate must
    not also decide what closing it means, or the two would drift and a tenant
    config change would silently stop applying. Returns () when DIS cannot say,
    which callers treat as "do not open the gate" — never worse than today.

    Keyed on the resolved TENANT as well as the client, and the response is
    checked to be the tenant that was asked for. DIS derives the tenant from the
    caller's identity, not from `client_id`: a call made without a user resolves
    to the default tenant and answers confidently with that tenant's mapping.
    Observed while verifying this — `client_id="aim"` with no user returned
    Cengage's cdd set, which omits `course_calendar`. Using it as the base would
    have opened the gate to the requested type while silently dropping the
    calendars, i.e. worse than not opening it at all.
    """
    import time

    from app.core.dis_client import dis_client

    # Defensive on both counts. This module's contract is that no rung raises —
    # a regeneration must never fail because the deep path was unavailable — and
    # an AttributeError here escaped it: the resolver was called outside the try,
    # so a client object without the method took the whole request down instead of
    # degrading to an unopened gate.
    try:
        tenant_id = str(getattr(dis_client, "resolved_tenant_id", lambda *_a: "")(
            current_user, client_id) or "")
    except Exception:  # noqa: BLE001
        tenant_id = ""
    key = (tenant_id, client_id or "-")
    hit = _purpose_types_cache.get(key)
    now = time.monotonic()
    if hit:
        age, cached = now - hit[0], hit[1]
        ttl = PURPOSE_TYPES_TTL_SECONDS if cached else PURPOSE_TYPES_FAILURE_TTL_SECONDS
        if age < ttl:
            return cached

    # The ladder's remaining budget bounds this as well. A lookup that would
    # leave no time for the search it is preparing is not worth making: the
    # search matters, this only widens it.
    budget = min(PURPOSE_TYPES_TIMEOUT_SECONDS, _remaining(deadline))
    if budget <= 0:
        return ()
    try:
        cfg = dis_client.ui_config_sync(current_user=current_user, client_id=client_id,
                                        timeout=budget)
        answered = str((cfg or {}).get("tenant_id") or "")
        if tenant_id and answered and answered != tenant_id:
            _log.warning("cdd_purpose_types_tenant_mismatch asked=%s answered=%s",
                         tenant_id, answered)
            return ()
        mapping = (cfg or {}).get("source_type_mapping") or {}
        types = tuple(str(t) for t in (mapping.get("cdd") or []) if str(t).strip())
    except Exception as exc:  # noqa: BLE001
        _log.warning("cdd_purpose_types_unavailable client_id=%s error=%s", client_id, exc)
        _purpose_types_cache[key] = (now, ())
        return ()
    _purpose_types_cache[key] = (now, types)
    return types


# ---------------------------------------------------------------------------
# Block resolution
# ---------------------------------------------------------------------------

_BLOCK_IN_OVERVIEW = re.compile(r"\*\*Block:\*\*\s*([^\n|]+)", re.IGNORECASE)
_BLOCK_IN_TITLE = re.compile(r"\b(Block\s+\w+)", re.IGNORECASE)


def resolve_block(db, cdd, sections: dict | None = None) -> str:
    """The block a CDD belongs to, or "".

    Deliberately reads the document before the database. Worksheet 1 states the
    block on its first line — ``- **Block:** Block 2`` — which makes this exact
    and available today, with no migration and no backfill. ``generation_params``
    would be the tidier home for it and will be added, but 0 of 137 stored CDD
    versions carry it, so relying on it would mean shipping a feature that
    works for nothing currently in the database.
    """
    try:
        from app.services.cdd_regen_context import find_section, load_sections

        sections = sections if sections is not None else load_sections(db, cdd)
        overview = find_section(sections, "WORKSHEET 1: BLOCK OVERVIEW")
        if not overview:
            for key in sections:
                if "BLOCK OVERVIEW" in str(key).upper():
                    overview = str(sections[key] or "")
                    break
        match = _BLOCK_IN_OVERVIEW.search(overview or "")
        if match:
            return match.group(1).strip()

        match = _BLOCK_IN_TITLE.search(getattr(cdd, "title", "") or "")
        if match:
            return match.group(1).strip()
    except Exception:  # noqa: BLE001 — resolution is best-effort by design
        _log.warning("cdd_block_unresolved cdd_id=%s", getattr(cdd, "id", "?"), exc_info=True)
    return ""


# ---------------------------------------------------------------------------
# Query formulation
# ---------------------------------------------------------------------------
#
# The step between "the user asked for something" and "the library was
# searched". It earns its own section because getting it wrong is invisible:
# retrieval succeeds, returns eight plausible units, and the model correctly
# declines because none of them answer the question. That reads to the user as
# a broken feature, and no log line says otherwise.
#
# Measured on CDD 169. The instruction "Primary handbooks do not have all the
# handbook. refer the syllabus document and make it complete without missing any
# applicable for the block", prefixed with the block name, retrieved the
# syllabi of Blocks 12, 14 and 16 — and starved Block 2's own, which sits in the
# same library and states exactly the handbook list that was asked for. Every
# syllabus in the corpus shares several thousand characters of identical
# boilerplate, so a query whose content is mostly verbs and quantifiers ranks
# them interchangeably, and two tokens of block name cannot break the tie
# across 3,723 units. Leading with content nouns instead put Block 2's syllabus
# first and second.

_BLOCK_NUM_RE = re.compile(r"(\d{1,2})")

#: Prefixes the corpus writes a block designation with.
#: Evidenced rather than assumed: "Block 02" is how both copies of a block's
#: syllabus are tagged, and "BLK 12"/"BLK 16" is how the syllabi head their own
#: out-of-class activity tables. Kept as data in one place so a new form is a
#: one-line change rather than an edit inside the variant logic.
_BLOCK_PREFIX_FORMS = ("Block", "BLK")

#: Widths a block number is written at — bare and zero-padded. Both occur in the
#: same index for the same block.
_BLOCK_NUMBER_WIDTHS = (1, 2)


def block_variants(block: str) -> list[str]:
    """A block designation in every form the library files it under.

    The same block is tagged three ways in the AIM index: "Block 2" on 935
    units, "2" on 80, and "Block 02" on 10 — and those ten include *both*
    copies of the block's syllabus. Retrieval compares these as opaque
    lowercased strings, so a query carrying only the form Worksheet 1 happens to
    use ("Block 2") has no lexical path to a document filed under "Block 02".

    Emitting every form costs a handful of query tokens. It is the difference
    between finding a block's syllabus and reporting that the library has no
    syllabus for the block.
    """
    text = (block or "").strip()
    if not text:
        return []
    out = [text]
    seen = {text.lower()}
    match = _BLOCK_NUM_RE.search(text)
    if match:
        number = int(match.group(1))
        for prefix in _BLOCK_PREFIX_FORMS:
            for width in _BLOCK_NUMBER_WIDTHS:
                form = f"{prefix} {number:0{width}d}"
                if form.lower() not in seen:
                    seen.add(form.lower())
                    out.append(form)
    return out


#: Words that describe the EDIT rather than the CONTENT.
#:
#: A retrieval query is a description of the material wanted; an instruction is
#: a command about what to do to it. "make it complete without missing any
#: applicable for the block" is entirely the latter, and embedding it buys
#: nothing but noise. Dropping these keeps the nouns that discriminate —
#: "syllabus", "handbook", "corrosion" — and discards the scaffolding that every
#: instruction shares.
#:
#: Note that the fill verbs (add/complete/fill/missing) are deliberately in
#: here even though `wants_source` reads them as intent. They are the right
#: signal for deciding WHETHER to search and the wrong one for deciding WHAT to
#: search for.
_QUERY_FILLER = frozenset("""
a an the this that these those it its is are was were be been being am
and or but so then than if when while for with without within into onto from
at on in of to by as
do does did done doing please kindly make makes making made let lets
want wants need needs should must can cannot could would will shall may might
correct corrects correcting corrected properly proper property
complete completes completing completed completely incomplete
all any some every each both other others more most much many few
refer refers referring referred use uses using used update updates updating
updated fix fixes fixing fixed ensure ensures ensuring ensured
add adds adding added fill fills filling filled populate populates
missing miss misses supply supplies provide provides expand expands
applicable applicables relevant appropriate necessary required
have has had having not no nor only just also again now here there
i you we they me my our your their them he she him her his hers
please thanks thank rather instead still yet already
""".split())


#: Content words carried from the instruction. Past roughly this many the query
#: is describing the whole sentence again, which is what it is trying not to do.
MAX_INSTRUCTION_WORDS = 14


def _content_words(instruction: str, *, limit: int = MAX_INSTRUCTION_WORDS) -> list[str]:
    """The discriminating words of an instruction, in order, filler removed."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in re.findall(r"[A-Za-z0-9][A-Za-z0-9.\-/]*", instruction or ""):
        word = raw.strip(".-/")
        if not word:
            continue
        key = word.lower()
        if key in _QUERY_FILLER or key in seen:
            continue
        # Short tokens are usually filler, but not when they are an acronym or
        # carry a digit: "AC 43.13" and "B" (an ACS subject) are exactly the
        # anchors worth keeping.
        if len(word) < 3 and not (word.isupper() or any(c.isdigit() for c in word)):
            continue
        seen.add(key)
        out.append(word)
        if len(out) >= limit:
            break
    return out


#: Identifiers worth lifting out of the section being regenerated.
#:
#: A document code is the strongest anchor any of these corpora offer: it appears
#: in the calendars, the syllabi and the slides, and nowhere outside the subject
#: at hand. This matches them by SHAPE rather than by vocabulary, deliberately.
#: An earlier version enumerated the aviation codes this one client happens to
#: use (FAA-H-8083-30B, AC43.13-1B/2B, AM.I.B.K1) — which anchors retrieval for
#: AIM and silently does nothing for every other client that reaches the same
#: code path. Shape generalises; a vocabulary list does not.
#:
#: Matching by shape also sidesteps a corpus inconsistency that broke the
#: enumerated version: the same advisory circular is written "AC 43.13-2025" in
#: one revision of a syllabus and "AC43.13-1B/2B" in another, so a pattern built
#: around one spelling mined the anchor from one document and missed it in the
#: other.
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:[.\-/][A-Za-z0-9]+)*")

#: URLs are stripped before mining. They satisfy every structural test below —
#: mixed case, digits, separators — while carrying no subject signal, and
#: Worksheet 1 lists them under "Web Resources" directly after the handbooks,
#: where they would crowd the real codes out of the anchor budget.
_URL_RE = re.compile(r"\b(?:https?://|www\.)\S+", re.IGNORECASE)

_IDENTIFIER_SEPARATORS = frozenset(".-/")

#: Below this, a mixed token is far more often prose or a bare figure than a
#: document code.
MIN_IDENTIFIER_CHARS = 4

#: Enough to anchor the query without letting the section dominate it.
MAX_IDENTIFIER_ANCHORS = 4


def _is_identifier(token: str) -> bool:
    """Whether a token has the shape of a document code rather than of prose.

    Three conditions, and prose essentially never satisfies all three: it mixes
    letters with digits, it is long enough not to be a bare figure, and it either
    carries an internal separator or is fully capitalised. "FAA-H-8083-30B",
    "AC43.13-1B/2B", "AM.I.B.K1" and "B2D5" all pass; "Aug14", "2026", "1-13",
    "Days" and "70" all fail.
    """
    if len(token) < MIN_IDENTIFIER_CHARS:
        return False
    if not any(c.isalpha() for c in token) or not any(c.isdigit() for c in token):
        return False
    return bool(_IDENTIFIER_SEPARATORS & set(token)) or token.isupper()


def identifier_anchors(text: str, *, limit: int = MAX_IDENTIFIER_ANCHORS) -> list[str]:
    """Document codes named in a section, in order, deduplicated."""
    out: list[str] = []
    seen: set[str] = set()
    for token in _TOKEN_RE.findall(_URL_RE.sub(" ", text or "")):
        if not _is_identifier(token):
            continue
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(token)
        if len(out) >= limit:
            break
    return out


#: Ceiling on the assembled query. Long queries blur the embedding and cost
#: latency; this is roughly twice what the structured part ever needs.
MAX_QUERY_CHARS = 600


def build_search_query(*, block: str = "", instruction: str = "", scope: Any = None,
                       course_title: str = "", section_key: str = "",
                       section_content: str = "") -> str:
    """Assemble the library query for a regeneration.

    Structured anchors first — block, course, section, days, ACS codes, the
    identifiers already present in the section — then what is left of the
    instruction once the imperative is stripped. Order matters less than
    proportion: the point is that the anchors are no longer outnumbered by
    filler.
    """
    parts: list[str] = []
    seen: set[str] = set()

    def push(value: str) -> None:
        text = str(value or "").strip()
        if not text:
            return
        key = text.lower()
        if key in seen:
            return
        seen.add(key)
        parts.append(text)

    for variant in block_variants(block):
        push(variant)
    push(course_title)
    push(section_key)
    for day in getattr(scope, "day_numbers", ()) or ():
        push(f"Day {day}")
    for code in getattr(scope, "acs_codes", ()) or ():
        push(code)
    for column in getattr(scope, "columns", ()) or ():
        push(column)
    for identifier in identifier_anchors(section_content or ""):
        push(identifier)
    for word in _content_words(instruction):
        push(word)

    # Trimmed at a word boundary. A hard slice can leave a half-token like
    # "syllab", which is noise in the embedding rather than a weaker signal.
    query = " ".join(parts)
    if len(query) > MAX_QUERY_CHARS:
        query = query[:MAX_QUERY_CHARS].rsplit(" ", 1)[0]
    query = query.strip()
    # Never return nothing. An instruction made entirely of filler still has to
    # search for something, and the block's own name is a better query than the
    # empty string, which retrieval answers with unranked keyword results.
    if not query:
        query = (instruction or "")[:INSTRUCTION_QUERY_CHARS].strip() or (block or "").strip()
    return query


#: Format that `fetch_search_context` receives its sources in, so the caller can
#: tell the user which documents were actually consulted. Reported on a no-op
#: because "the library was read and did not cover this" is an actionable answer
#: — upload the missing document — while silence is not.
_SOURCE_LINE_RE = re.compile(r"^Source:\s*(.+?)\s*$", re.MULTILINE)


#: Retrieval returns a bounded number of units per call, so this only needs to be
#: large enough not to truncate a normal response.
MAX_REPORTED_SOURCES = 8


def source_names(text: str, *, limit: int = MAX_REPORTED_SOURCES) -> tuple[str, ...]:
    """Distinct source documents named in a retrieved context block."""
    out: list[str] = []
    seen: set[str] = set()
    for name in _SOURCE_LINE_RE.findall(text or ""):
        clean = name.strip()
        key = clean.lower()
        if not clean or key in seen:
            continue
        seen.add(key)
        out.append(clean)
        if len(out) >= limit:
            break
    return tuple(out)


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DeepContext:
    """What the ladder found, and how far it had to go to find it."""

    text: str = ""
    level: str = "none"
    units: int = 0
    levels_tried: tuple[str, ...] = ()
    flags: tuple[str, ...] = field(default_factory=tuple)
    #: Documents the ladder actually read. Carried so a no-op can name them:
    #: "these were consulted and none covered it" is something the user can act
    #: on, where "nothing happened" is not.
    sources: tuple[str, ...] = field(default_factory=tuple)
    #: How DIS answered: "opensearch_hybrid" (semantic ranking) or "s3_keyword"
    #: (the fallback, which ranks by term overlap and has been observed returning
    #: other blocks' syllabi). Previously discarded, which made it impossible to
    #: tell from a log which path had served a disappointing result.
    retrieval_method: str = ""
    #: Document types the search was allowed to see. Empty means the tenant's
    #: `cdd` purpose alone; a non-empty tuple means an instruction named material
    #: outside it and the gate was opened to exactly that.
    document_types: tuple[str, ...] = field(default_factory=tuple)
    #: Documents retrieved but NOT sent to the model, because the assembly budget
    #: was already spent. Never partially sent — a name here means none of it was
    #: read. The user's move is to name it in the instruction so it ranks higher.
    omitted_documents: tuple[str, ...] = field(default_factory=tuple)
    #: Documents DIS packed only PART of before answering (its own token budget).
    #: These reached the model, but not in full, so a "not covered" conclusion about
    #: one of them is unsafe. The user's move is to narrow the request.
    #:
    #: Kept separate from `omitted_documents` because the two demand opposite
    #: actions, and collapsing them into one "incomplete" list would leave the
    #: reader unable to tell which they are looking at.
    incomplete_documents: tuple[str, ...] = field(default_factory=tuple)

    @property
    def found(self) -> bool:
        return bool(self.text.strip())

    @property
    def unavailable(self) -> bool:
        """True when a rung could not be consulted, as opposed to consulted and
        empty.

        These are opposite answers and must never be conflated. "Nothing was
        found" is a fact about the source library — the gap is real and the
        caller should keep it. "We could not look" is a fact about the
        infrastructure — the gap may not be real at all, and reporting it as one
        would tell the user their library lacks material it might well contain.
        Observed live: DIS could not reach S3, so a syllabus lookup failed with
        EndpointConnectionError and the regeneration returned the section
        unchanged, indistinguishable from success.
        """
        return any(str(f).endswith("unavailable") for f in self.flags)

    def provenance(self) -> dict[str, Any]:
        return {"deep_level": self.level, "deep_units": self.units,
                "levels_tried": list(self.levels_tried), "flags": list(self.flags),
                "sources": list(self.sources),
                "retrieval_method": self.retrieval_method,
                "document_types": list(self.document_types),
                "omitted_documents": list(self.omitted_documents),
                "incomplete_documents": list(self.incomplete_documents)}


# ---------------------------------------------------------------------------
# Ladder rungs
# ---------------------------------------------------------------------------

def _render_day_bundle(bundle: dict) -> tuple[str, int]:
    """Format a /context/retrieve/day response into prompt text.

    The bundle carries the day's digest, every unit attributed to the day, and
    a bounded kNN supplement — levels 2 and 3 of the ladder in one round-trip.
    """
    day = bundle.get("day_number")
    parts = [f"### DAY {day} — SOURCE MATERIAL"]

    topic = bundle.get("topic") or bundle.get("lesson_title")
    if topic:
        parts.append(f"Topic: {topic}")

    digest = bundle.get("digest") or {}
    if digest:
        import json
        # Whole. The digest is a compact extraction to begin with, and clipping
        # JSON produces a fragment that no longer parses as the structure it is
        # labelled as — the model then reads a broken object as the day's facts.
        parts.append("#### Day digest (what MAP extracted)\n"
                     + json.dumps(digest, indent=1, default=str))

    units = bundle.get("units") or []
    supplement = bundle.get("supplement") or []
    unit_text = []
    for unit in list(units) + list(supplement):
        if not isinstance(unit, dict):
            continue
        body = str(unit.get("text") or unit.get("content") or "").strip()
        if not body:
            continue
        label = str(unit.get("source_file") or unit.get("document_title")
                    or unit.get("unit_id") or "unit")
        unit_text.append(f"[{label}] {body}")
    if unit_text:
        # Whole. These are the day's OWN units — the material the answer is
        # supposed to be based on — so cutting the tail removes evidence while
        # leaving the answer looking equally well-sourced.
        parts.append("#### Ingested source units for this day\n"
                     + "\n\n".join(unit_text))

    if len(parts) == 1:
        return "", 0
    return "\n\n".join(parts), len(units) + len(supplement)


def fetch_day_context(block: str, days, *, current_user, client_id: str,
                      deadline: float | None = None) -> DeepContext:
    """Levels 2-3: the day's digest and its raw ingested units."""
    from app.core.dis_client import dis_client

    chunks, total_units, flags = [], 0, []
    for day in list(days)[:MAX_DEEP_DAYS]:
        budget = _remaining(deadline)
        if budget <= 0:
            # Out of time. Flagged, not silently dropped: an unconsulted rung
            # must never read as "the source library has nothing".
            flags.append(f"day_{day}_unavailable")
            continue
        try:
            bundle = dis_client.get_day_context_sync(
                block, int(day), current_user=current_user, client_id=client_id,
                timeout=min(budget, PER_CALL_TIMEOUT_SECONDS),
            )
        except Exception as exc:  # noqa: BLE001 — fall through to the next rung
            _log.warning("deep_context_day_unavailable block=%s day=%s error=%s",
                         block, day, exc)
            flags.append(f"day_{day}_unavailable")
            continue
        text, count = _render_day_bundle(bundle or {})
        if text:
            chunks.append(text)
            total_units += count
        flags.extend(str(f) for f in (bundle or {}).get("flags", []) or [])

    if not chunks:
        return DeepContext(level="none", levels_tried=("day_context",), flags=tuple(flags))
    return DeepContext(text="\n\n".join(chunks), level="day_context", units=total_units,
                       levels_tried=("day_context",), flags=tuple(flags))


def _remaining(deadline: float | None) -> float:
    """Seconds left in the ladder's budget; a large number when unbounded."""
    if deadline is None:
        return PER_CALL_TIMEOUT_SECONDS
    import time
    return deadline - time.monotonic()


def _unit_number(unit: dict) -> int:
    try:
        return int(unit.get("unit_number") or 0)
    except (TypeError, ValueError):
        return 0


#: A block designation inside a document NAME. Retrieved units carry no block
#: metadata at all — `source_file_name`, `job_id`, `unit_number`, `title` and
#: `text` are the whole shape — so the name is the only block signal available
#: here, and for this corpus it is a reliable one ("Block 04-Instructor Copy-
#: ACS Course Calendar.pdf").
_NAME_BLOCK_RE = re.compile(r"\b(?:block|blk)[\s_\-]*0*(\d{1,2})\b", re.IGNORECASE)


def document_block_numbers(name: str) -> tuple[int, ...]:
    """Every block a document's name declares, in order of appearance.

    All of them, not the first: "Block 1 and 2 crosswalk.pdf" serves Block 2 as
    much as Block 1, and matching only the first occurrence would discard it from
    a Block 2 search. Empty is deliberately not "wrong block" — a document may
    span blocks ("ALL Block Calendars_NEW FORMAT.xlsx") or simply not say, and
    dropping those would lose material no other rung reaches.
    """
    out: list[int] = []
    for match in _NAME_BLOCK_RE.finditer(name or ""):
        try:
            number = int(match.group(1))
        except (TypeError, ValueError):
            continue
        if number not in out:
            out.append(number)
    return tuple(out)


def document_block_number(name: str) -> int | None:
    """The first block a document's name declares, or None."""
    numbers = document_block_numbers(name)
    return numbers[0] if numbers else None


def is_foreign_block(name: str, block: str) -> bool:
    """Whether a document's name declares blocks and the target is not among them."""
    target = document_block_number(block)
    if target is None:
        return False
    found = document_block_numbers(name)
    return bool(found) and target not in found


def assemble_documents(units: list, *, token_budget: int = SEARCH_TOKENS,
                       block: str = "",
                       omitted: list[str] | None = None) -> tuple[str, tuple[str, ...]]:
    """Rebuild retrieved units into whole documents, in document order.

    DIS returns units ranked by relevance across the whole corpus, which is the
    right answer for a corpus of reference material and the wrong one for this
    purpose. A syllabus or a calendar is a STRUCTURED document: its meaning is in
    the relationships between its parts — which handbook covers which days, what
    Day 3 assumes Day 2 established, how the required-texts list relates to the
    course description. Three day-rows plucked out of a twenty-day calendar by
    cosine similarity carry none of that, and a model reading them cannot tell
    whether it is seeing the whole picture or a keyhole view of it.

    Measured on the live corpus, taking these documents whole is also *cheaper*
    than the ranked-chunk alternative: a complete 20-day calendar is ~1,780
    tokens, where the previous eight-chunk mixed response ran to ~5,600. Coherent
    and smaller at the same time, because the chunks it was spending the budget
    on came from other blocks' documents.

    Returns (text, source_names). Documents are emitted in first-appearance
    order, which is DIS's relevance order, so the most relevant document is the
    one that survives if the budget runs out.

    ``omitted`` (optional) is appended to in place with the name of any document the
    budget excluded. Nothing is ever trimmed to fit — a document is included whole or
    left out whole — so this list is the complete record of what the model did not
    see, and the caller turns it into something the user reads. An omission the user
    is told about is a boundary they can act on; a silent one is a wrong answer.
    """
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    if omitted is None:
        omitted = []
    for unit in units or []:
        if not isinstance(unit, dict):
            continue
        name = str(unit.get("source_file_name") or unit.get("job_id") or "").strip()
        if not name or not str(unit.get("text") or "").strip():
            continue
        if name not in groups:
            groups[name] = []
            order.append(name)
        groups[name].append(unit)

    # Drop documents that name a different block.
    #
    # Measured on the live index, a plain CDD search for Block 2 returned 16
    # units of which 8 came from the Block 01, 03, 04, 08 and 14 calendars — on
    # the SEMANTIC path, not the keyword fallback. Every one of those competes
    # for the token budget and has to be ignored by the model, and mistaking one
    # for this block's calendar is the original failure this module was built to
    # stop.
    #
    # Only applied when something survives it. If every retrieved document names
    # another block, the honest outcome is to pass them and let the labels and
    # the leave-content-alone rule do their work — not to report an empty library.
    if block:
        kept = [n for n in order if not is_foreign_block(n, block)]
        if kept:
            order = kept

    chunks: list[str] = []
    used: list[str] = []
    spent = 0
    for name in order:
        members = sorted(groups[name], key=_unit_number)
        numbers = [_unit_number(u) for u in members]
        body = "\n\n".join(str(u.get("text") or "").strip() for u in members)

        # Documents are passed WHOLE. This used to clip a large one to
        # MAX_DOCUMENT_TOKENS "from the beginning, rather than dropped — a truncated
        # syllabus still answers questions its first pages cover". That reasoning is
        # wrong in the direction that matters: a clipped document is indistinguishable
        # from a complete one to the model, so a fact living past the cut is not
        # merely unavailable, it is actively contradicted — the model reports "not
        # covered" about material that is sitting in the library. A document that is
        # genuinely too large is omitted whole and NAMED below, which loses the same
        # content while telling the truth about having lost it.

        # Label what is present, and never claim completeness.
        #
        # An earlier version called a run of sections numbered from 1 the
        # "complete document". It is not: DIS returns at most `max_results`
        # units across ALL documents, so a 20-day calendar that only got ten
        # slots arrives as sections 1..10 — contiguous from 1, and half a
        # calendar. It was labelled complete, which is the exact
        # misunderstanding this field exists to prevent.
        #
        # The response carries no per-document unit total, so completeness is
        # not knowable here. Report the sections held and let the reader treat
        # anything else as absent.
        contiguous = numbers == list(range(1, len(numbers) + 1))
        scope = (f"sections 1-{len(numbers)}, in document order" if contiguous
                 else "sections " + ", ".join(str(n) for n in numbers)
                      + " only — non-consecutive extract")
        header = f"Source: {name}\n[{scope}]"
        rendered = f"{header}\n{body}".strip()

        cost = count_tokens(rendered)
        # `chunks and` keeps the first document unconditionally: one oversized
        # document is still the best answer available, and returning nothing would
        # report an empty library. Past that, a document that does not fit is
        # skipped WHOLE and recorded — never trimmed to fit.
        if chunks and spent + cost > token_budget:
            omitted.append(name)
            continue
        chunks.append(rendered)
        used.append(name)
        spent += cost

    if not chunks:
        return "", ()
    # Stated once, up front. Without it a reader can reasonably assume the
    # sections shown are the document, and conclude that something absent from
    # an extract is absent from the source — which is how a real gap and a
    # retrieval boundary become indistinguishable.
    preamble = ("Each document below is grouped and ordered by its own sections. "
                "Only the sections listed are included; treat anything else as "
                "not retrieved rather than as absent from the document.")
    return preamble + "\n\n" + "\n\n---\n\n".join(chunks), tuple(used)


def search_filters(document_types: Sequence[str], *, current_user, client_id: str,
                   deadline: float | None = None,
                   ) -> tuple[dict[str, Any], tuple[str, ...], tuple[str, ...]]:
    """The `filters` for a search, and the document types it will actually see.

    Returns (filters, effective_types, flags). With no requested types this is
    the purpose-scoped request CDD generation itself uses. With requested types
    it becomes an explicitly typed request covering the purpose's own set PLUS
    what was asked for — but only if the purpose's set could be read from the
    tenant, because opening the gate without it would silently drop the
    calendars and syllabi that are the point of the CDD corpus.
    """
    requested = tuple(t for t in document_types if t and t not in RESTRICTED_DOC_TYPES)
    # Resolved even when nothing was requested, so a caller explaining an
    # unhelpful result can say what the search was allowed to cover instead of
    # guessing at ingestion. Cached, so this is not a per-request round-trip.
    base = cdd_document_types(current_user=current_user, client_id=client_id,
                              deadline=deadline)
    if not requested:
        return {"purpose": "cdd"}, base, ()
    if not base:
        # Degrade to today's behaviour rather than guess. Flagged, not silent, and
        # deliberately not "*unavailable": the search itself is fine, it is just
        # narrower than the instruction asked for.
        return {"purpose": "cdd"}, (), ("doc_types_unopened",)
    effective = tuple(dict.fromkeys((*base, *requested)))
    return ({"purpose": "all", "document_types": list(effective)}, effective, ())


def fetch_search_context(query: str, *, current_user, client_id: str,
                         deadline: float | None = None,
                         document_types: Sequence[str] = (),
                         block: str = "") -> DeepContext:
    """Level 4: a query over the library, for content attributed to no day.

    The only rung that can reach an UNATTRIBUTED unit. Scoped to the same
    retrieval purpose as CDD generation, so it searches the same corpus with the
    same tenant scoping — widened only to document types the instruction named
    (see `search_filters`).
    """
    from app.core.dis_client import dis_client

    if not query.strip():
        return DeepContext(level="none", levels_tried=("search",))
    budget = _remaining(deadline)
    if budget <= 0:
        return DeepContext(level="none", levels_tried=("search",),
                           flags=("search_unavailable",))
    filters, effective_types, filter_flags = search_filters(
        document_types, current_user=current_user, client_id=client_id,
        deadline=deadline,
    )
    # Re-checked: resolving the purpose set may itself have consumed the budget,
    # and spending what is left on a request that cannot return in time turns a
    # slow dependency into a failed regeneration.
    budget = _remaining(deadline)
    if budget <= 0:
        return DeepContext(level="none", levels_tried=("search",),
                           flags=("search_unavailable", *filter_flags),
                           document_types=effective_types)
    try:
        result = dis_client.retrieve_context_sync(
            "cdd",
            {"purpose": "cdd", "query": query, "filters": filters,
             # Asking for enough units to hold a whole document. DIS's defaults
             # (8 units / 6,000 tokens) cannot express a 20-day calendar, so it
             # returned whichever three day-rows scored highest and the
             # relationships between them were lost. Both values are clamped
             # server-side, so this widens the request without assuming it is
             # granted.
             "retrieval": {"top_k": SEARCH_TOP_K, "token_budget": SEARCH_TOKEN_BUDGET}},
            current_user=current_user, client_id=client_id,
            timeout=min(budget, PER_CALL_TIMEOUT_SECONDS),
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning("deep_context_search_unavailable error=%s", exc)
        return DeepContext(level="none", levels_tried=("search",),
                           flags=("search_unavailable", *filter_flags),
                           document_types=effective_types)

    units = (result or {}).get("source_units") or (result or {}).get("sources") or []
    summary = (result or {}).get("retrieval_summary") or {}
    method = str(summary.get("retrieval_method") or "")
    flags: tuple[str, ...] = filter_flags

    # What DIS itself removed before answering. Its packer skips whole units that
    # do not fit the token budget, and until it reported them a caller could not
    # tell a document it received in full from one whose tail was packed away —
    # which is precisely the claim `assemble_documents` goes on to make.
    incomplete = tuple(str(n) for n in (summary.get("budget_dropped_sources") or []) if n)

    # Preferred path: rebuild whole documents from the units.
    left_out: list[str] = []
    assembled, names = assemble_documents(
        [u for u in units if isinstance(u, dict)], token_budget=SEARCH_TOKENS,
        block=block, omitted=left_out,
    )
    if assembled:
        text, sources = assembled, names
    else:
        # Fallback for a response carrying no per-unit detail — an older DIS, or
        # the keyword path returning a pre-joined blob. Relevance order and all,
        # this is still better than no context.
        raw = str((result or {}).get("combined_context") or "").strip()
        if not raw:
            return DeepContext(level="none", levels_tried=("search",),
                               flags=flags, retrieval_method=method,
                               document_types=effective_types)
        # Passed whole. DIS already bounded this blob by the token_budget we asked
        # for, so clipping it again here could only cut a second time — and this
        # path has no per-unit structure, so a cut lands mid-document with nothing
        # in the text to say so.
        text = raw
        sources = source_names(text)
        # Deliberately NOT suffixed "unavailable": that suffix is the convention
        # `DeepContext.unavailable` reads to mean "the library could not be
        # consulted", which would be a false report here — it was consulted and
        # answered, just without the per-unit detail needed to group it.
        flags = (*flags, "search_units_missing")

    return DeepContext(
        text="### SOURCE LIBRARY DOCUMENTS FOR THIS BLOCK\n" + text,
        level="search", units=len(units), levels_tried=("search",), flags=flags,
        # Named from what was actually kept, not from the whole response: a
        # document dropped by the budget did not inform the answer.
        sources=sources,
        retrieval_method=method, document_types=effective_types,
        # Both kinds of loss, kept distinct because the user's next move differs:
        # `omitted` documents were never opened (ask for them by name), while
        # `incomplete` ones were opened partially (narrow the request).
        omitted_documents=tuple(left_out), incomplete_documents=incomplete,
    )


# ---------------------------------------------------------------------------
# The ladder
# ---------------------------------------------------------------------------

def deepen(db, cdd, *, scope, instruction: str, current_user,
           sections: dict | None = None, course_title: str = "",
           section_key: str = "", section_content: str = "") -> DeepContext:
    """Climb the ladder until something is found, or report that nothing is.

    Day-keyed first because it is precise and cheap; search second because it is
    the only rung that reaches unattributed content. Stops at the first rung
    that answers, so the common case costs one call and the hopeless case costs
    two before it admits as much.
    """
    import time

    from app.core.dis_access import resolve_course_dis_client

    deadline = time.monotonic() + DEEP_BUDGET_SECONDS
    tried: list[str] = []
    # Accumulated across rungs, never replaced. A day fetch that failed and then
    # a search that legitimately found nothing must still report `unavailable` —
    # otherwise falling through to the next rung launders an infrastructure
    # failure into "your source library has a real gap", which is the one
    # conclusion this module exists to stop the caller drawing by accident.
    flags: list[str] = []
    try:
        client_id = resolve_course_dis_client(
            db, course_id=getattr(cdd, "course_id", None),
            project_id=getattr(cdd, "project_id", None),
        )
    except Exception:  # noqa: BLE001
        _log.warning("deep_context_client_unresolved cdd_id=%s", getattr(cdd, "id", "?"),
                     exc_info=True)
        return DeepContext(level="none", levels_tried=("client_unresolved",))

    block = resolve_block(db, cdd, sections)

    if block and getattr(scope, "day_numbers", ()):
        found = fetch_day_context(block, scope.day_numbers,
                                  current_user=current_user, client_id=client_id,
                                  deadline=deadline)
        tried.extend(found.levels_tried)
        flags.extend(found.flags)
        if found.found:
            return DeepContext(text=found.text, level=found.level, units=found.units,
                               levels_tried=tuple(tried), flags=tuple(flags),
                               sources=found.sources,
                               retrieval_method=found.retrieval_method,
                               document_types=found.document_types,
                               omitted_documents=found.omitted_documents,
                               incomplete_documents=found.incomplete_documents)

    # Level 4. See `build_search_query` for why the instruction is not simply
    # concatenated onto the block name: that is what it used to do, and it
    # retrieved three other blocks' syllabi instead of this block's own.
    query = build_search_query(
        block=block, instruction=instruction, scope=scope,
        course_title=course_title, section_key=section_key,
        section_content=section_content,
    )
    # What the instruction asks to consult decides what the search may see. Empty
    # for the ordinary case, which leaves the request scoped exactly as before.
    doc_types = document_type_hints(instruction)
    _log.info("deep_context_query cdd_id=%s block=%r doc_types=%s query=%r",
              getattr(cdd, "id", "?"), block, list(doc_types), query)
    found = fetch_search_context(query, current_user=current_user, client_id=client_id,
                                 deadline=deadline, document_types=doc_types,
                                 block=block)
    tried.extend(found.levels_tried)
    flags.extend(found.flags)
    return DeepContext(text=found.text, level=found.level, units=found.units,
                       levels_tried=tuple(tried), flags=tuple(flags),
                       sources=found.sources,
                       retrieval_method=found.retrieval_method,
                       document_types=found.document_types,
                       omitted_documents=found.omitted_documents,
                       incomplete_documents=found.incomplete_documents)
