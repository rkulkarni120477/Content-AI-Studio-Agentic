"""ASSIGNED READING — the calendar's own per-day citation, resolved to real units.

AIM's calendars carry, on most days, an explicit reading assignment naming a
handbook, a chapter and a page range::

    Day 1 | LANDING GEAR SYSTEMS ... | Review syllabus, complete introductory
    materials.

    Reading: FAA-H-8083-31B
    Ch. 13 pgs. 13-1 to 13-14

    Project  A27: start (due day 4)

Measured on 2026-08-27 across the shared structure store: **350 of 505** AIM
calendar day rows carry such a citation (Block 9: 20 of 20 on its richest
calendar). That is an authored, per-day map from a course day to the exact
reference material the day teaches from — the single most precise statement of
"what belongs to this day" anywhere in the corpus.

Nothing read it. Two reasons, both fixed here:

  * ``worksheets._REFERENCE_READING_RE`` matched the literal ``Reference
    reading:``; these calendars say ``Reading:``. It matched 84 of the 350.
  * The handbooks the citations name (``8083-31B.pdf`` and friends) carry no
    block tag at all, so ``AIMCurriculumProfile._load_units`` — which filters on
    ``metadata_json->>'block'`` — never loaded them. Block 9's build saw 84 units
    from 9 files, every one of them a calendar, syllabus or exam, and 18 of its
    20 days raised ``THIN_DAY`` because the only unit on the day was the calendar
    row being summarised.

Resolution is by CHAPTER, never by page. Page slicing was tried first and is
what this module deliberately does not do: the handbooks are chunked as ~3.3k
character text runs with no page metadata (``page_number`` and ``chapter`` are
NULL on all 1,233 units of 8083-31B), so a page range can only be inferred from
page-shaped tokens in the running text — and those tokens are dominated by
forward cross-references. A slice for "Ch. 13 pgs. 13-1 to 13-14" opened on
air-compressor servicing from chapter 12, and the chapter-15 slice opened on
fuel-spill procedure. Chapter BOUNDARIES, by contrast, are exact: the figure
references (``[Figure 13-1]``) that saturate the text carry the chapter as their
prefix, so the dominant prefix per chunk rises monotonically through the book and
the run boundaries land on the real chapter openings (chapter 13's detected start,
chunk 861, is the chunk containing "Aircraft Landing Gear Systems Chapter 13").

So a day gets its whole cited chapter, and the page range travels alongside as an
emphasis hint for MAP rather than a filter. Nothing the calendar assigned is
withheld, which is the requirement this module exists to meet.
"""
from __future__ import annotations

import logging
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)

#: The citation marker, as the calendars actually write it. ``Reference`` is
#: optional because both spellings are in live data (Block 2 uses the longer
#: form, Blocks 5/7/8/9/10/14/15 the shorter). The negative lookbehind stops
#: ``Proofreading:`` and similar from matching.
#:
#: The body runs to the next blank line rather than to end-of-line: the citation
#: is authored across two physical lines (handbook, then chapter and pages), and
#: a line-bounded capture returns the handbook alone — which is exactly what the
#: delivered workbook showed, a bare "FAA-H-8083-31B" with the chapter and pages
#: silently dropped.
_READING_RE = re.compile(
    r"(?<![A-Za-z])(?:Reference\s+)?Reading:\s*(.+?)(?=\n\s*\n|\Z)",
    re.IGNORECASE | re.DOTALL,
)

#: ``FAA-H-8083-31B`` / ``8083-31B`` / ``AC 43.13-1B`` / ``AC43.13-1B``.
_HANDBOOK_RE = re.compile(
    r"(?:FAA-H-)?(\d{4}-\d+[A-Z]?)|AC\s?(43\.13-\d+[A-Z]?)", re.IGNORECASE)

#: ``Ch. 13`` / ``Chapter 13`` / ``Ch 13``.
_CHAPTER_RE = re.compile(r"\bCh(?:apter|\.)?\s*(\d{1,2})\b", re.IGNORECASE)

#: A page token, ``13-14`` — chapter-prefixed, as every FAA handbook numbers them.
#: Also how AC 43.13 writes paragraph numbers, which is why a citation with no
#: explicit "Ch." can still yield a chapter (see :func:`parse_citations`).
_PAGE_RE = re.compile(r"\b(\d{1,2})-(\d{1,3})[a-z]?\b")

#: How many chunks a detected chapter must span to be trusted as a chapter rather
#: than a cluster of cross-references. Chapters in these handbooks run 26-135
#: chunks; a genuine run is never near this floor.
_MIN_CHAPTER_CHUNKS = 3

#: Gap, in chunk positions, tolerated inside one chapter run. A handful of chunks
#: carry no figure reference at all (tables, photo plates); without this each one
#: would split its chapter in two and the longest-run rule would keep only a half.
_MAX_RUN_GAP = 3

#: Window for the median smoothing of the per-chunk chapter signal, in chunks
#: either side. Wide enough to absorb one cross-reference-heavy chunk, narrow
#: enough not to blur a real boundary.
_SMOOTH_RADIUS = 2


@dataclass(frozen=True)
class Citation:
    """One day's assigned reading, as the calendar states it."""

    raw: str
    handbook: str
    chapter: Optional[int] = None
    page_from: Optional[int] = None
    page_to: Optional[int] = None

    def describe(self) -> str:
        """Human-readable form for the Day-by-Day Map's Handbook Reference cell.

        Rebuilt from the parsed parts rather than echoing ``raw`` so the cell is
        single-line and consistent across the two authoring styles."""
        out = self.handbook
        if self.chapter is not None:
            out += f" Ch. {self.chapter}"
        if self.page_from is not None:
            pages = f"{self.chapter}-{self.page_from}" if self.chapter else str(self.page_from)
            if self.page_to is not None and self.page_to != self.page_from:
                pages += f" to {self.chapter}-{self.page_to}" if self.chapter else f" to {self.page_to}"
            out += f" pgs. {pages}"
        return out


@dataclass
class DayReferences:
    """What a day's citations resolved to, and what they didn't.

    ``unresolved`` is never dropped: a citation naming a handbook this corpus has
    not ingested is a real coverage gap, and the whole point of this module is
    that such a gap reaches a flag instead of a silently thinner digest."""

    citations: List[Citation] = field(default_factory=list)
    units: List[Dict[str, Any]] = field(default_factory=list)
    unresolved: List[Tuple[Citation, str]] = field(default_factory=list)

    def label(self) -> str:
        return "; ".join(c.describe() for c in self.citations)


def normalize_handbook(text: str) -> str:
    """Canonical handbook code, or "" when *text* names none.

    ``FAA-H-8083-31B``, ``8083-31B`` and ``faa-h-8083-31b`` all return
    ``8083-31B``; ``AC 43.13-1B`` and ``AC43.13-1B`` both return ``AC43.13-1B``.
    Normalising to the bare number is what lets a citation match a stored file
    named ``8083-31B.pdf`` — the ingested filenames drop the ``FAA-H-`` prefix
    the calendars use."""
    m = _HANDBOOK_RE.search(text or "")
    if not m:
        return ""
    if m.group(1):
        return m.group(1).upper()
    return f"AC{m.group(2).upper()}"


def edition_designator(handbook: str) -> str:
    """The handbook's official designator, from its normalised code.

    ``8083-31B`` -> ``FAA-H-8083-31B``; ``AC43.13-1B`` -> ``AC 43.13-1B``.
    The Day-by-Day Map's Handbook Edition column wants what the document calls
    itself, not the abbreviated form the citation matcher works in.
    """
    code = (handbook or "").strip().upper()
    if not code:
        return ""
    if code.startswith("AC"):
        return f"AC {code[2:]}"
    return f"FAA-H-{code}"


def edition_series(designator: str) -> str:
    """The revision-independent identity of a handbook designator.

    ``FAA-H-8083-31B`` and ``FAA-H-8083-31A`` are the SAME handbook at two
    revisions and citing both in one block is a real conflict; ``FAA-H-8083-31B``
    and ``AC 43.13-1B`` are two different documents and citing both is ordinary.
    Returns the designator with any trailing revision letter removed.
    """
    return re.sub(r"[A-Z]$", "", (designator or "").strip().upper())


def parse_citations(source_text: str) -> List[Citation]:
    """Every assigned reading on one calendar day row, in order.

    A day may cite more than one handbook (some Block 14/15 days pair a handbook
    chapter with an AC paragraph), so this returns a list and callers must not
    assume one.

    The chapter is taken from an explicit ``Ch. N`` when present and otherwise
    from the chapter prefix shared by the cited page tokens — which is how the
    AC 43.13 days are written (``Pg. 9-1 to 9-3 paragraph 9-4``, no "Ch."
    anywhere, but unambiguously chapter 9).
    """
    out: List[Citation] = []
    for block in _READING_RE.findall(source_text or ""):
        text = " ".join(str(block).split())
        if not text:
            continue
        handbook = normalize_handbook(text)
        if not handbook:
            continue
        chapter: Optional[int] = None
        m = _CHAPTER_RE.search(text)
        if m:
            chapter = int(m.group(1))
        # Page tokens, minus the one inside the handbook code itself (8083-31B's
        # "8083-31" would otherwise read as chapter 8083 / page 31).
        body = _HANDBOOK_RE.sub(" ", text)
        pages = [(int(a), int(b)) for a, b in _PAGE_RE.findall(body)]
        if chapter is None and pages:
            chapter = Counter(a for a, _ in pages).most_common(1)[0][0]
        nums = [b for a, b in pages if chapter is not None and a == chapter]
        out.append(Citation(
            raw=text,
            handbook=handbook,
            chapter=chapter,
            page_from=min(nums) if nums else None,
            page_to=max(nums) if nums else None,
        ))
    return out


def _unit_meta(unit: Dict[str, Any]) -> Dict[str, Any]:
    """Unit metadata — structure-store rows use ``metadata_json``; pipeline units use ``metadata``."""
    return unit.get("metadata_json") or unit.get("metadata") or {}


def _explicit_chapter(unit: Dict[str, Any]) -> Optional[int]:
    md = _unit_meta(unit)
    try:
        if md.get("chapter") is None or md.get("chapter") == "":
            return None
        return int(md.get("chapter"))
    except (TypeError, ValueError):
        return None


def _printed_page(unit: Dict[str, Any]) -> Optional[int]:
    md = _unit_meta(unit)
    try:
        if md.get("printed_page") is not None and md.get("printed_page") != "":
            return int(md.get("printed_page"))
    except (TypeError, ValueError):
        pass
    # Fall back to parsing page_number "13-1".
    pn = str(md.get("page_number") or "")
    m = _PAGE_RE.search(pn)
    return int(m.group(2)) if m else None


def _units_have_explicit_chapters(units: Sequence[Dict[str, Any]]) -> bool:
    """True when a majority of units carry an explicit chapter tag (page-chunked ebooks)."""
    if not units:
        return False
    tagged = sum(1 for u in units if _explicit_chapter(u) is not None)
    return tagged >= max(1, len(units) // 2)


def _chunk_index(unit: Dict[str, Any]) -> int:
    """Ordering position of a unit within its source file.

    Falls back to 0 when absent so a file with no chunk indices degrades to
    "one undifferentiated document" rather than raising."""
    md = _unit_meta(unit)
    try:
        return int(md.get("chunk_index") or 0)
    except (TypeError, ValueError):
        return 0


def _dominant_chapter(text: str) -> Optional[int]:
    """The chapter this chunk's page/figure tokens most often point at."""
    counts = Counter(int(a) for a, _ in _PAGE_RE.findall(text or ""))
    return counts.most_common(1)[0][0] if counts else None


def chapter_runs(units: Sequence[Dict[str, Any]]) -> Dict[int, Tuple[int, int]]:
    """Map chapter number -> (first, last) position in *units*.

    *units* must already be ordered by ``chunk_index``. Positions are indices
    into that sequence, not chunk_index values, so a file with gaps in its
    indices still slices correctly.

    When units carry explicit ``metadata.chapter`` (page-chunked ebook_reference),
    those tags win. Otherwise falls back to figure/page-token inference used for
    legacy 512-word chunks.

    Verified against 8083-31B (1,233 chunks): returns 17 contiguous,
    non-overlapping chapters covering chunks 22-1151, and chapter 13's start
    (861) is the chunk that contains the string "Aircraft Landing Gear Systems
    Chapter 13".
    """
    if _units_have_explicit_chapters(units):
        raw = [_explicit_chapter(u) for u in units]
    else:
        raw = [_dominant_chapter(u.get("text_content") or u.get("text") or "") for u in units]
    smoothed: List[Optional[int]] = []
    for i in range(len(raw)):
        window = [c for c in raw[max(0, i - _SMOOTH_RADIUS):i + _SMOOTH_RADIUS + 1]
                  if c is not None]
        smoothed.append(Counter(window).most_common(1)[0][0] if window else None)

    runs: Dict[int, Tuple[int, int]] = {}
    for chapter in set(c for c in smoothed if c is not None):
        positions = [i for i, c in enumerate(smoothed) if c == chapter]
        # Longest near-contiguous run only. A chapter number also appears in the
        # table of contents and the index, hundreds of chunks from the chapter
        # itself; taking min..max there would swallow most of the book.
        current: List[int] = [positions[0]]
        best: List[int] = current
        for prev, nxt in zip(positions, positions[1:]):
            if nxt - prev <= _MAX_RUN_GAP:
                current.append(nxt)
            else:
                if len(current) > len(best):
                    best = current
                current = [nxt]
        if len(current) > len(best):
            best = current
        # Explicit chapter tags are already trusted; figure-token inference needs
        # a longer run so TOC/index noise does not invent a chapter.
        min_chunks = 1 if _units_have_explicit_chapters(units) else _MIN_CHAPTER_CHUNKS
        if len(best) >= min_chunks:
            runs[chapter] = (best[0], best[-1])
    return _monotonic_only(runs)


def _monotonic_only(runs: Dict[int, Tuple[int, int]]) -> Dict[int, Tuple[int, int]]:
    """Keep only the chapters whose located positions agree with book order.

    "Longest run wins" knows nothing about WHERE a run sits, and a later region
    that reuses an early chapter's figure numbering — an appendix, a
    worked-examples section, renumbered errata — can be longer than the real
    chapter and take its place. The wrong span is then returned as an exact
    resolution, with no reason string and no coverage flag, which is the worst
    possible failure: silently confident and wrong.

    A book's chapters appear in order, so the largest subset of runs that is
    strictly increasing in start position is the one consistent with being a
    book. Chapters outside it are dropped rather than guessed at, which routes
    them through ``units_for_citation``'s documented fallback WITH a reason —
    turning a silent wrong answer into a visible approximate one.
    """
    chapters = sorted(runs)
    if len(chapters) < 2:
        return runs
    # Longest strictly-increasing-by-start subsequence over chapters in order.
    # n is the chapter count (<= ~30 for these handbooks), so O(n^2) is free.
    length = [1] * len(chapters)
    prev = [-1] * len(chapters)
    for i in range(len(chapters)):
        for j in range(i):
            if runs[chapters[j]][0] < runs[chapters[i]][0] and length[j] + 1 > length[i]:
                length[i] = length[j] + 1
                prev[i] = j
    end = max(range(len(chapters)), key=lambda i: length[i])
    keep = []
    while end != -1:
        keep.append(chapters[end])
        end = prev[end]
    kept = set(keep)
    for chapter in chapters:
        if chapter not in kept:
            log.warning("chapter %s located at %s contradicts book order; "
                        "not trusted as an exact span", chapter, runs[chapter])
    return {c: runs[c] for c in chapters if c in kept}


#: Characters of reference text one day may carry when the cited chapter cannot be
#: pinned down and the whole file is attached instead. ~100k tokens, which every
#: model this pipeline runs on holds comfortably alongside the day's own units.
#:
#: Sized from the corpus rather than guessed: 8083-30B is 279,829 characters in
#: total (its ingestion is heavily truncated — 67 chunks whose markers run 1..14
#: and then restart, so its chapters cannot be located at all), and attaching it
#: whole is both affordable and the honest reading of "the day reads this book".
#: 8083-31B is ~4M characters and is not, which is the case this bound exists for.
_WHOLE_FILE_CHAR_BUDGET = 400_000

#: Words too common in aviation-maintenance prose to discriminate between days.
#: Only consulted on the topical fallback below, never on a located chapter.
_TOPIC_STOP = set(
    "the a an and or of to for in on with by from is are be as at this that it its "
    "aircraft system systems component components maintenance inspection check "
    "procedure procedures type types used use using may can will shall".split())
_WORD_RE = re.compile(r"[a-z][a-z0-9]{2,}")


def _topic_terms(day: Dict[str, Any]) -> set:
    text = " ".join(str(day.get(k) or "") for k in ("topic", "lesson_title")).lower()
    return {w for w in _WORD_RE.findall(text) if w not in _TOPIC_STOP}


def _topical_units(units: Sequence[Dict[str, Any]], terms: set,
                   budget: int) -> List[Dict[str, Any]]:
    """The units of an oversized file that best match a day's own topic terms.

    A deliberate last resort, used only when a citation names a file too large to
    attach whole and gives no chapter to narrow it. Scored on distinct-term hits
    rather than raw frequency so one word repeated forty times cannot outrank a
    chunk that covers the day's actual subject.
    """
    scored = []
    for u in units:
        text = (u.get("text_content") or "").lower()
        if not text:
            continue
        hits = sum(1 for t in terms if t in text)
        if hits:
            scored.append((hits, len(text), u))
    scored.sort(key=lambda s: (-s[0], s[1]))
    out, used = [], 0
    for _hits, size, u in scored:
        if used + size > budget:
            continue
        out.append(u)
        used += size
    # Restored to reading order: the model is being handed book prose, and
    # relevance-ranked fragments read as non-sequiturs where consecutive ones read
    # as a passage.
    out.sort(key=_chunk_index)
    return out


def units_for_citation(citation: Citation,
                       units_by_file: Dict[str, List[Dict[str, Any]]],
                       day: Optional[Dict[str, Any]] = None
                       ) -> Tuple[List[Dict[str, Any]], str]:
    """Resolve one citation to units. Returns ``(units, reason)``.

    ``reason`` is "" on an exact resolution and otherwise names what happened, so
    an imprecise attachment reaches a coverage flag instead of quietly looking
    like a cited chapter.

    The calendar's statement that a day reads a given handbook is authored fact,
    so a chapter that cannot be located never causes the citation to be dropped —
    it degrades to the whole file, or, when that exceeds
    ``_WHOLE_FILE_CHAR_BUDGET``, to the topically closest part of it.
    """
    filename = _match_file(citation.handbook, units_by_file)
    if not filename:
        return [], f"handbook {citation.handbook} is not ingested for this client"

    units = units_by_file[filename]

    if citation.chapter is not None:
        # Prefer explicit page tags when present (page-chunked ebook_reference).
        # A citation "Ch. 13 pgs. 13-1 to 13-14" can then slice by printed_page
        # instead of attaching the whole chapter.
        if citation.page_from is not None and _units_have_explicit_chapters(units):
            sliced = [
                u for u in units
                if _explicit_chapter(u) == citation.chapter
                and (pp := _printed_page(u)) is not None
                and citation.page_from <= pp <= (citation.page_to or citation.page_from)
            ]
            if sliced:
                return sliced, ""
            # Tagged file but the printed range missed — fall through to whole chapter.

        span = chapter_runs(units).get(citation.chapter)
        if span:
            lo, hi = span
            return list(units[lo:hi + 1]), ""
        why = f"chapter {citation.chapter} could not be located in {filename}"
    else:
        why = f"citation names no chapter in {filename}"

    total = sum(len(u.get("text_content") or "") for u in units)
    if total <= _WHOLE_FILE_CHAR_BUDGET:
        return list(units), f"{why} — whole file attached ({total:,} chars)"

    picked = _topical_units(units, _topic_terms(day or {}), _WHOLE_FILE_CHAR_BUDGET)
    if not picked:
        return [], (f"{why}, and it is too large to attach whole ({total:,} chars) "
                    f"with no topic terms to narrow it — NOTHING attached")
    return picked, (f"{why} — too large to attach whole ({total:,} chars), so the "
                    f"{len(picked)} passages closest to this day's topic were used "
                    f"instead of the cited pages")


def _match_file(handbook: str, units_by_file: Dict[str, List[Dict[str, Any]]]) -> str:
    """The ingested filename a handbook code refers to, or "".

    Matched on the normalised code appearing in the filename, because the two
    are authored independently: the calendar says ``AC 43.13-1B`` and the file is
    ``AC43.13-2025.pdf``, so an equality test finds nothing. Whitespace and dots
    are stripped from both sides before comparing, and the longest matching
    filename wins so ``8083-31B`` prefers ``8083-31B.pdf`` over a file that
    merely mentions it.
    """
    def squash(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9]", "", str(value)).upper()

    want = squash(handbook)
    if not want:
        return ""
    hits = [f for f in units_by_file if want in squash(f)]
    if hits:
        return sorted(hits, key=len)[0]
    # AC 43.13-1B vs AC43.13-2025.pdf: the edition suffix differs because AIM
    # ingested a later revision than the calendar cites. Retry on the series
    # alone rather than reporting the handbook as missing — a superseded edition
    # of the same AC is the document the day means, and 26 Block 9-15 day rows
    # cite the -1B that this corpus holds only as the 2025 revision.
    #
    # The series is matched against the ORIGINAL code, not the squashed one: the
    # dot in "AC43.13" is load-bearing for this pattern, and squashing first made
    # the regex unmatchable, which is how those 26 days reported the handbook as
    # "not ingested" while it sat in the index under a later edition.
    series = re.match(r"^(AC\s?\d+\.\d+)", handbook, re.IGNORECASE)
    if series:
        stem = squash(series.group(1))
        hits = [f for f in units_by_file if stem in squash(f)]
        if hits:
            return sorted(hits, key=len)[0]
    return ""


def index_by_file(units: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Group reference units by source filename, each list ordered by chunk_index."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    for u in units:
        name = str(_unit_meta(u).get("source_file_name") or "")
        if name:
            out.setdefault(name, []).append(u)
    for name in out:
        out[name].sort(key=_chunk_index)
    return out


def resolve_day(day: Dict[str, Any],
                units_by_file: Dict[str, List[Dict[str, Any]]]) -> DayReferences:
    """Everything one calendar day assigns as reading, resolved to units."""
    refs = DayReferences(citations=parse_citations(day.get("source_text") or ""))
    seen: set = set()
    for citation in refs.citations:
        units, reason = units_for_citation(citation, units_by_file, day)
        if reason:
            refs.unresolved.append((citation, reason))
        for u in units:
            uid = u.get("content_unit_id")
            if uid not in seen:
                seen.add(uid)
                refs.units.append(u)
    return refs
