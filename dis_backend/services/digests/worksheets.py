"""Block-level worksheet aggregation — Block Overview / Source File Inventory /
ACS Code Registry, for the multi-worksheet CDD/Blueprint shape.

These consume the full in-process ``EnumerateResult`` (before it's projected down
to ``to_summary()``) so ``units_by_day``/``unattributed``/``acs_by_day`` are still
available. Pure, read-only, no LLM — the same "never invent a fact" discipline as
ENUMERATE/mapper: any field this repo can't source honestly renders as an explicit
``NOT AVAILABLE``/``NO AKTR DATA`` placeholder rather than a fabricated value (ACS1.pdf
task-description text and AKTR miss-rate analytics are not ingested anywhere in this
system — see CDD_CONTEXT_REDESIGN_PLAN.md).
"""
from __future__ import annotations

import bisect
import json
import logging
import re
from typing import Any, Dict, List

from services.aim_calendar import parse_handbook
from services.digests import attribution

log = logging.getLogger(__name__)

# Quick-check priority defaults when no AKTR miss-rate data exists for a code —
# mirrors the fallback AIM's own sample already uses for the ~78 of 88 codes it
# has no real AKTR number for (RECALL/APPLY/ANALYZE by ACS element type).
_DEFAULT_PRIORITY = {
    "K": "RECALL (default — no AKTR data for this code)",
    "R": "ANALYZE (default — Risk Management element)",
    "S": "APPLY (default — Skill element)",
}
_ACS_TYPE_RE = re.compile(r"\.([KRS])\d+$")
# Non-greedy up to a period that starts the NEXT sentence (". " + capital letter)
# or end-of-string — a plain `[^.]+` stops at the first period, which truncates
# real citations like "FAA-H-8083-30B Ch. 4 pgs 4-1 to 4-9" at "Ch." (caught by
# test_worksheets.py, not from theory — a live run had silently returned the
# truncated "FAA-H-8083-30B Ch" as if it were the complete citation).
_REFERENCE_READING_RE = re.compile(r"Reference reading:\s*(.+?)(?:\.\s+[A-Z]|\.\s*$|$)", re.I)
_HANDBOOK_RE = re.compile(r"FAA-H-\d{4}-\d+[A-Z]?")

# Generic "Title Case Heading:" detector — syllabi in this system are ingested as one
# flattened line (no blank-line section breaks survive extraction), so bounding a
# marker-anchored field capture needs to find wherever the source's OWN next labeled
# section actually starts, not a fixed character count or "run to end of text" (both
# were tried and both broke live: a fixed count truncated mid-word, and running to the
# end bled a labeled field into unrelated boilerplate that happens to immediately
# follow with no separator). A heading here is 1-6 words, each either capitalized or a
# short lowercase connector, immediately followed by ": " — the same shape every real
# syllabus heading in this system already has (including form-style labels like
# "Required Text(s):" that have no sentence-ending punctuation before them, so this
# intentionally does NOT require one). The leading (?<![(\w]) blocks a false match
# starting mid-parenthetical — confirmed live: "...Standards (ACS) Other Required
# Materials:" let "ACS)" itself (capital A, immediately after "(") satisfy the
# per-word pattern and match as if IT were the heading start, truncating the
# previous field's capture one word early. Real headings are never preceded by "("
# or a word character in this flattened text — always whitespace or start-of-text.
_HEADING_RE = re.compile(
    r"(?<![(\w])[A-Z][A-Za-z()/&'.\-]*(?:\s+(?:[A-Z][A-Za-z()/&'.\-]*|and|of|the|for|in|to))*:\s"
)
# A generic PDF-table-flattening artifact: a run of "Label | NN%" (or "Label - NN%")
# pairs. Confirmed live: a real syllabus's grading breakdown lands in exactly this
# shape, physically separated from the "Grading and Evaluation:" prose paragraph by
# several unrelated sections — reading only the prose right after that marker misses
# the actual percentages entirely.
_GRADING_ROW_RE = re.compile(r"([A-Za-z][A-Za-z &/]{2,40}?)\s*[|\-]\s*(\d{1,3}%)")
# Headings covering a syllabus's required/supplemental reading beyond the primary FAA
# handbook(s) — wording varies by syllabus, so this is a small set of common labels,
# not an exhaustive enum; matched case-insensitively.
_SUPPLEMENTAL_MARKERS = [
    "Required Text(s)", "Required Texts", "Required Text",
    "Other Required Materials", "Recommended Supplemental Material",
    "Supplemental References",
]
# Excludes the Unicode Private Use Area (U+E000-U+F8FF) from the URL body —
# confirmed live: real ingested text has PDF-glyph-fallback bullet characters
# (e.g. U+F0B7) landing directly against a URL with no whitespace before the
# next sentence (e.g. "...bptMQuestions<PUA bullet>The video..."); \S alone doesn't stop
# there, so the match ran straight through the bullet and into the next
# sentence's words too.
_PUA_RANGE = chr(0xE000) + "-" + chr(0xF8FF)  # Unicode Private Use Area
_URL_RE = re.compile(r"https?://[^\s" + _PUA_RANGE + "]+")


def _next_heading_start(text: str, after: int) -> int:
    """Position where the next generic heading begins at or after `after`, or
    len(text) if none — see _HEADING_RE."""
    m = _HEADING_RE.search(text, after)
    return m.start() if m else len(text)


def _grading_percentage_table(text: str) -> str:
    """Format a detected Label|NN% run (see _GRADING_ROW_RE) as a clean breakdown,
    or "" if fewer than 2 such rows are found (too weak a signal to be a real
    table rather than a coincidental match)."""
    rows = [(label.strip(), pct) for label, pct in _GRADING_ROW_RE.findall(text)
            if label.strip().lower() != "total"]
    if len(rows) < 2:
        return ""
    return "Breakdown: " + "; ".join(f"{label} {pct}" for label, pct in rows)


def _supplemental_references(text: str) -> str:
    """Books/ACs/etc. beyond the primary FAA handbook(s) — e.g. AC 43.13-1B or a
    supplemental textbook — named in the syllabus's own "Required Text(s)" /
    "Recommended Supplemental Material" style sections. Confirmed live: these were
    previously never surfaced anywhere despite being right there in the ingested
    syllabus text (zero mentions of "AC 43.13" in any generated output). The
    primary handbook code itself is stripped out of each value (see _HANDBOOK_RE)
    so it isn't listed twice — once here and once under primary_handbooks."""
    if not text:
        return "NOT AVAILABLE — syllabus not ingested"
    parts = []
    for marker in _SUPPLEMENTAL_MARKERS:
        m = re.search(re.escape(marker) + r"\s*:\s*", text, re.I)
        if not m:
            continue
        end = _next_heading_start(text, m.end())
        value = _HANDBOOK_RE.sub("", text[m.end():end]).strip()
        value = re.sub(r"\s{2,}", " ", value).strip(" ,")
        if value and value.lower() not in ("none", "n/a", "none."):
            parts.append(f"{marker}: {value}")
    return "; ".join(parts) if parts else "NOT AVAILABLE — not found in syllabus text"


_LEAKED_WORD_SUFFIX_RE = re.compile(r"(?:[A-Z][a-z]{2,})+$")


def build_web_resources(days: List[Dict[str, Any]], units_by_day: Dict[int, List[Dict[str, Any]]]) -> List[str]:
    """Unique URLs found anywhere in a block's already-ingested day text or unit
    content — purely mechanical (a regex over text we already have), so this
    works for any block/client without new ingestion or per-client wiring."""
    found: List[str] = []
    seen = set()
    texts = [d.get("source_text") or "" for d in days]
    for units in units_by_day.values():
        texts.extend(u.get("text_content") or "" for u in units)
    for text in texts:
        for url in _URL_RE.findall(text):
            url = url.rstrip(").,;:")
            # Confirmed live: a PDF-extraction artifact drops the space/line-break
            # between a URL and the sentence that follows it (e.g. a lost bullet
            # point), producing "...bptMQuestions" — a trailing run of Capitalized
            # English word(s) directly appended with no separator. Real URL paths
            # essentially never end that way, so this strips the leaked prose back
            # off rather than keeping a corrupted URL.
            url = _LEAKED_WORD_SUFFIX_RE.sub("", url)
            if url not in seen:
                seen.add(url)
                found.append(url)
    return found


def _collapse_day_ranges(day_nums) -> str:
    """Format a set of day numbers as contiguous ranges, e.g. {1,2,3,5} -> "1-3, 5"."""
    ranges: List[List[int]] = []
    for dn in sorted(set(day_nums)):
        if ranges and ranges[-1][-1] == dn - 1:
            ranges[-1].append(dn)
        else:
            ranges.append([dn])
    return ", ".join(f"{r[0]}-{r[-1]}" if len(r) > 1 else str(r[0]) for r in ranges)


def _acs_type(code: str) -> str:
    m = _ACS_TYPE_RE.search(code or "")
    return m.group(1) if m else "?"


def _acs_type_label(letter: str) -> str:
    return {"K": "K — Knowledge", "R": "R — Risk Management", "S": "S — Skill"}.get(letter, "Unknown")


# Matches an Airman Certification Standards code of the shape this system uses
# everywhere else (e.g. "AM.I.B.K1") — generic to the CODE SHAPE, not to any one
# client's filename, so any tenant that ingests an ACS-standards-shaped document
# gets the same extraction.
_ACS_CODE_RE = re.compile(r"\bAM\.I{1,3}\.[A-Z]{1,2}\.[KRS]\d+\b")
# A real FAA-S-ACS-1-shaped document's running page footer ("© 2026 Aviation
# Supplies & Academics, Inc. Provided for use by the Aviation Institute of
# Maintenance's enrolled students, active instructors, and program
# administrators. [Page 35]") is interleaved mid-list between codes at every
# page break — confirmed live: "...K23 Binary numbers. 28 © 2026 Aviation
# Supplies... [Page 35] AM.I.A.K24 Electrostatic discharge." Left in place, this
# would corrupt K23's captured description with page-footer text. Stripped
# before parsing, not per-client — the wording is the publisher's own
# boilerplate, not something any one tenant controls.
_ACS1_PAGE_FOOTER_RE = re.compile(
    r"\d{0,3}\s*©\s*\d{4}\s+Aviation Supplies\s*&\s*Academics,?\s*Inc\.[^.]*?"
    r"administrators\.\s*(?:\[Page\s*\d+\]\s*)?",
    re.IGNORECASE,
)
# The three section-preamble phrases that separate a subject's Knowledge/Risk
# Management/Skills code lists from each other — without this, the LAST code's
# description in a section would run on into the next section's preamble
# sentence (e.g. "...K27 AC and DC motors. Risk Management The applicant
# demonstrates the ability to identify..." landing inside K27's own text).
_ACS1_SECTION_BOUNDARY_RE = re.compile(
    r"Knowledge\s+The applicant demonstrates understanding of:|"
    r"Risk Management\s+The applicant demonstrates the ability to identify, "
    r"assess, and mitigate risks associated with:|"
    r"Skills\s+The applicant demonstrates the ability to:",
    re.IGNORECASE,
)
# A document must contain at least this many DISTINCT codes to be treated as
# "the ACS reference", not merely a syllabus/calendar that happens to cite a
# handful of codes in passing.
_ACS1_MIN_DISTINCT_CODES = 20
# No real FAA-S-ACS-1-shaped standards document is anywhere near this large —
# the real document extracts to ~200K characters. A client's OTHER
# ebook_reference uploads (a multi-hundred-page handbook, an advisory circular)
# can run several MEGABYTES — confirmed live: one aim client alone has two
# ebook_reference PDFs at 4MB/2MB of extracted text. Filtering candidates by a
# cheap SUM(length(...)) query BEFORE fetching any text_content means those
# never cross the wire or get regex-scanned at all, instead of every block-wide
# CDD/Blueprint generation call transferring and scanning several MB of
# text that structurally can never be the ACS reference.
_ACS1_MAX_CANDIDATE_CHARS = 1_000_000


def _parse_acs1_task_descriptions(text: str) -> Dict[str, str]:
    """Per-code task-description text from a raw FAA-S-ACS-1-shaped reference
    document: each code (e.g. "AM.I.B.K1") is immediately followed by its own
    description, up to whichever comes first — the next code, or the next
    section-boundary phrase. Never guesses: a code whose span comes up empty is
    simply absent from the result, and the caller keeps its own NOT AVAILABLE
    default for it."""
    clean = _ACS1_PAGE_FOOTER_RE.sub(" ", text)
    code_matches = list(_ACS_CODE_RE.finditer(clean))
    boundary_positions = sorted(
        {m.start() for m in code_matches} | {m.start() for m in _ACS1_SECTION_BOUNDARY_RE.finditer(clean)}
    )
    out: Dict[str, str] = {}
    for m in code_matches:
        # First boundary strictly after this match's own start — bisect_right
        # lands just past m.start() itself (which is always in the list, since
        # every code match is also a boundary), giving the next one in O(log n)
        # instead of an O(n) linear scan per code (this document has ~1,200+
        # codes, so that difference is the whole point at this scale).
        idx = bisect.bisect_right(boundary_positions, m.start())
        end = boundary_positions[idx] if idx < len(boundary_positions) else len(clean)
        desc = " ".join(clean[m.end():end].strip(" .").split())
        if desc and m.group(0) not in out:  # first occurrence wins
            out[m.group(0)] = desc
    return out


def _acs1_task_descriptions(en, cur, schema: str) -> Dict[str, str]:
    """Locate the ingested ACS-standards reference document (if any) for this
    client — identified by CONTENT (the document with the most distinct ACS
    codes matched), not by filename/client, so this works for any tenant that
    ingests an ACS-1-shaped document under any name — and parse its per-code
    task descriptions. Returns {} (never raises) when no cursor is available or
    no qualifying document is found, same best-effort contract as every other
    cur-based lookup in this module.

    Two queries, not one: the first is a cheap SUM(length(...)) per document
    (no text transferred) used only to drop oversized candidates (see
    _ACS1_MAX_CANDIDATE_CHARS); the second fetches full text_content for only
    the documents that survive that filter.

    Caught and logged here, not left to an outer try/except (e.g. build.py's
    context_bundle) — that outer handler wraps THREE independent worksheet
    builds in one try/except, so an unhandled exception here would discard an
    already-successful block_overview/source_file_inventory result too and
    force ALL three back into degraded mode. This lookup's own failure must
    only cost itself."""
    if cur is None:
        return {}
    try:
        cur.execute(
            f"""SELECT cu.document_id, SUM(length(cu.text_content)) AS total_chars
                 FROM {schema}.dis_content_units cu
                 JOIN {schema}.dis_documents doc ON doc.document_id = cu.document_id
                WHERE cu.client_id = %s AND doc.document_type = 'ebook_reference'
                GROUP BY cu.document_id""",
            (en.client_id,),
        )
        candidate_ids = [row["document_id"] for row in cur.fetchall()
                         if (row["total_chars"] or 0) <= _ACS1_MAX_CANDIDATE_CHARS]
        if not candidate_ids:
            return {}
        cur.execute(
            f"""SELECT cu.document_id, cu.text_content
                 FROM {schema}.dis_content_units cu
                WHERE cu.document_id = ANY(%s)
                ORDER BY cu.document_id, cu.unit_number""",
            (candidate_ids,),
        )
        by_doc: Dict[str, List[str]] = {}
        for row in cur.fetchall():
            by_doc.setdefault(row["document_id"], []).append(row["text_content"] or "")
    except Exception as exc:  # best-effort; a failed lookup must never sink the caller
        log.warning("acs1_task_descriptions lookup failed for client=%s: %s", en.client_id, exc)
        return {}

    best_doc_text, best_count = "", 0
    for chunks in by_doc.values():
        full_text = " ".join(chunks)
        distinct = len(set(_ACS_CODE_RE.findall(full_text)))
        if distinct > best_count:
            best_doc_text, best_count = full_text, distinct
    if best_count < _ACS1_MIN_DISTINCT_CODES:
        return {}
    return _parse_acs1_task_descriptions(best_doc_text)


def build_acs_registry(en, cur=None, schema: str = "dis") -> List[Dict[str, Any]]:
    """Per-code registry: days active, type, and an honest task-description /
    quick-check-priority / high-miss placeholder wherever we lack real source data."""
    by_code_days: Dict[str, set] = {}
    for dn, codes in en.acs_by_day.items():
        for c in codes:
            by_code_days.setdefault(c, set()).add(dn)

    task_descriptions = _acs1_task_descriptions(en, cur, schema)

    out = []
    for code in sorted(en.declared_acs, key=attribution.acs_sort_key):
        letter = _acs_type(code)
        out.append({
            "acs_code": code,
            "acs_type": _acs_type_label(letter),
            "task_description": task_descriptions.get(code, "NOT AVAILABLE — ACS1.pdf not ingested"),
            "days_active": sorted(by_code_days.get(code, [])),
            "high_miss": "NO AKTR DATA — not supplied",
            "quick_check_priority": _DEFAULT_PRIORITY.get(letter, "RECALL (default)"),
        })
    return out


_DEFAULT_RESTRICTED_DOC_TYPES = {"quiz_answer_key", "project_key"}


# Block-wide reference document types (§ enabled_document_types) that describe
# the block as a whole rather than any one day — a syllabus or course calendar
# is never itself a per-day content unit, so it can be entirely absent from
# ``en.units_by_day``/``en.unattributed`` even though the underlying files are
# genuinely ingested. Matches the taxonomy in config/settings.py; kept as its
# own small constant here (not the full enabled_document_types list) because
# lesson/project/quiz types ARE reachable as per-day units already and adding
# them here would risk double-counting.
_BLOCK_WIDE_REFERENCE_TYPES = ["syllabus", "course_calendar", "ebook_reference"]


def _block_wide_reference_files(en, cur, schema: str) -> Dict[str, set]:
    """Syllabus/calendar/handbook-reference documents queried directly from
    dis_documents, by the same filename-regex match ``_syllabus_text`` already
    uses — independent of the units_by_day/unattributed pipeline's own
    ``metadata_json->>'block'`` day-attribution filter, whose normalization
    sensitivity ('Block 02' vs 'Block 2', see that function's docstring) is
    exactly what can drop these documents from the inventory entirely. Returns
    {} (never raises) when no cursor is available, no block number is
    parseable, or the query itself fails — same best-effort contract as
    build_block_overview's own cur=None fallback, and (see
    _acs1_task_descriptions's docstring for why) caught HERE rather than left
    to an outer bundle-level try/except, so this lookup's own failure can
    never cost the rest of the worksheet build."""
    if cur is None:
        return {}
    m = re.search(r"\d+", en.block or "")
    block_num = m.group(0).lstrip("0") or "0" if m else None
    if block_num is None:
        return {}
    try:
        cur.execute(
            f"""SELECT DISTINCT document_type, source_file_name
                 FROM {schema}.dis_documents
                WHERE client_id = %s AND document_type = ANY(%s)
                  AND source_file_name ~ %s""",
            (en.client_id, _BLOCK_WIDE_REFERENCE_TYPES, rf"[Bb]lock\s*0*{block_num}\D"),
        )
        out: Dict[str, set] = {}
        for row in cur.fetchall():
            out.setdefault(row["document_type"], set()).add(row["source_file_name"] or "(untitled)")
        return out
    except Exception as exc:  # best-effort; a failed lookup must never sink the caller
        log.warning("block_wide_reference_files lookup failed for client=%s block=%s: %s",
                    en.client_id, en.block, exc)
        return {}


def build_source_file_inventory(en, tenant_cfg=None, cur=None, schema: str = "dis") -> List[Dict[str, Any]]:
    """Group every unit (attributed + unattributed) by DOCUMENT TYPE — whatever
    value the tenant's own client profile / doc-type classifier assigned at
    ingestion (confirmed present on every unit's own metadata_json, written by
    metadata_tagging_agent — no new ingestion, no DB join needed, and no
    hardcoded value list here: this groups by whatever key is actually present,
    generic across any client's taxonomy). This reproduces AIM's own curated
    ~14-row category rollup as an emergent property: types with exactly one file
    (e.g. course_calendar) stay one row; types with many files (e.g. slide decks,
    quizzes, instructor guides, study questions, projects) roll up into one row
    each — with zero AIM-specific grouping logic, instead of one row per literal
    filename (87+ rows for a real block).

    Day association comes from the ``units_by_day`` bucket key itself (unambiguous
    ground truth — that's literally which day this unit was placed on), not from
    re-reading each unit's own ``resolved_day`` attribute, so this can't silently
    disagree with the bucket it's actually stored in."""
    by_type: Dict[str, Dict[str, Any]] = {}

    def _touch(u: Dict[str, Any], day_number) -> None:
        if u.get("unit_type") == "calendar_day":
            return  # structural placeholder, not a real source file
        md = u.get("metadata_json") or {}
        doc_type = md.get("document_type") or "other"
        fname = md.get("source_file_name") or u.get("title") or "(untitled)"
        entry = by_type.setdefault(doc_type, {"files": set(), "days": set()})
        entry["files"].add(fname)
        if day_number is not None:
            entry["days"].add(day_number)

    for dn, units in en.units_by_day.items():
        for u in units:
            _touch(u, dn)
    for u in en.unattributed:
        _touch(u, None)

    # Block-wide reference docs (syllabus/calendar/handbook) that never became
    # per-day content units at all — see _block_wide_reference_files. Only
    # filled in when this tenant/type combination isn't already reachable via
    # the units pass above, so a client whose classifier DOES tag these as
    # per-day units is never double-counted.
    block_wide_types: set[str] = set()
    for doc_type, files in _block_wide_reference_files(en, cur, schema).items():
        if doc_type in by_type:
            continue
        by_type[doc_type] = {"files": files, "days": set()}
        block_wide_types.add(doc_type)

    restricted_types = (set(getattr(tenant_cfg.document_processing, "restricted_document_types", []) or [])
                        if tenant_cfg is not None else _DEFAULT_RESTRICTED_DOC_TYPES)

    out = []
    for doc_type, entry in sorted(by_type.items()):
        days = sorted(entry["days"])
        file_count = len(entry["files"])
        # Mechanical facts only (file count + day coverage) — no fabricated
        # BLOCKING/edition-conflict judgment calls; those are genuine SME
        # observations AIM's sample adds by hand, not something to synthesize.
        if doc_type in block_wide_types:
            status_notes = f"{file_count} file(s) — block-wide reference, not tied to a specific day"
        elif days:
            status_notes = f"{file_count} file(s) — Days {_collapse_day_ranges(days)}"
        else:
            status_notes = f"{file_count} file(s) — day not resolved"
        out.append({
            "document_type": doc_type,
            "file_count": file_count,
            # A type whose every occurrence is unattributed genuinely has no known
            # day — say so rather than defaulting to "All" (which would falsely
            # imply block-wide applicability, AIM's own meaning for that value).
            # The one genuine exception is a type we KNOW is block-wide by
            # construction (syllabus/calendar/handbook, queried directly above) —
            # there "All" is the honest answer, not a guess.
            "days_applicable": (["All"] if doc_type in block_wide_types
                               else days if days else ["Unattributed"]),
            "status": "EXISTS",
            "production_action": ("Instructor-only — exclude from student-facing use"
                                  if doc_type in restricted_types else "Include in input bundle"),
            "status_notes": status_notes,
        })
    return out


def _subject_letter(code: str) -> str:
    parts = code.split(".")
    return parts[2] if len(parts) > 2 else "?"


def _project_quiz_counts(days: List[Dict[str, Any]]) -> tuple[int, int]:
    proj_ref, quiz_ref = attribution.build_calendar_refs(days)
    return len(proj_ref), len(quiz_ref)


def _primary_handbooks(days: List[Dict[str, Any]]) -> List[str]:
    """One prose line per handbook (not per exact citation), contiguous day-ranges
    collapsed — e.g. "FAA-H-8083-30B — cited on Days 1-13, 17-19". Reuses
    parse_handbook to pull the handbook NAME out of each day's 'Reference reading:'
    citation text (already embedded in source_text at calendar-ingestion time),
    grouping by that identity rather than by exact page range — a handbook cited
    with a different page range on different days still collapses into one line,
    matching how AIM's own sample presents this rather than emitting one row per
    distinct citation string."""
    days_by_handbook: Dict[str, List[int]] = {}
    for d in days:
        dn = d.get("day_number")
        if dn is None:
            continue
        m = _REFERENCE_READING_RE.search(d.get("source_text") or "")
        if not m:
            continue
        for ref in parse_handbook(m.group(1)):
            hb = ref.get("handbook")
            if hb:
                days_by_handbook.setdefault(hb, []).append(dn)

    out = [f"{hb} — cited on Days {_collapse_day_ranges(day_nums)}"
           for hb, day_nums in days_by_handbook.items()]
    return sorted(out)


def _syllabus_text(en, cur, schema: str) -> str:
    """Best-effort verbatim syllabus lookup. dis_syllabus (the structured table) is
    dead schema — nothing writes to it — so this reads the raw syllabus_section
    content unit directly, matched by filename rather than metadata_json.block
    (that field is inconsistently normalized, e.g. 'Block 02' vs 'Block 2' —
    a known gap, see stale-code-schema-audit). Returns '' if none found.

    ``schema`` MUST come from the tenant's own structure_store config (e.g.
    ``tenant_cfg.structure_store.schema_name``) — every other query in this
    package parameterizes it the same way (see enumerate.py); a literal 'dis.'
    here would silently return nothing for any tenant on a different schema.

    The filename regex can match more than one document — a block can carry a
    stale/superseded syllabus upload alongside the current one (confirmed live:
    Block 2 has both "...ACS Syllabus Revised.docx" and the canonical
    "...ACS Syllabus (Rev. 01.14.26).docx", with different grading percentages).
    ORDER BY created_at DESC + LIMIT 1 makes the pick deterministic and favors
    whichever was ingested most recently, instead of leaving it to Postgres's
    unspecified row order for a bare LIMIT 1 with no ORDER BY."""
    m = re.search(r"\d+", en.block or "")
    block_num = m.group(0).lstrip("0") or "0" if m else None
    if block_num is None:
        return ""
    cur.execute(
        f"""SELECT cu.text_content
             FROM {schema}.dis_content_units cu
             JOIN {schema}.dis_documents doc ON doc.job_id = cu.job_id
            WHERE cu.client_id = %s AND doc.document_type = 'syllabus'
              AND cu.unit_type = 'syllabus_section'
              AND doc.source_file_name ~ %s
            ORDER BY doc.created_at DESC
            LIMIT 1""",
        (en.client_id, rf"[Bb]lock\s*0*{block_num}\D"),
    )
    row = cur.fetchone()
    return (row["text_content"] if row else "") or ""


_SYLLABUS_KEY_BY_MARKER = {"Course Description": "course_description",
                           "Course Objectives": "course_objectives",
                           "Grading and Evaluation": "grading_policy"}


def _extract_syllabus_fields(text: str) -> Dict[str, str]:
    """Verbatim marker-anchored slicing (mirrors cdd_parser.parse_cdd_flat's
    position-based approach) — no LLM, so these fields can never be paraphrased.
    Each marker's content runs until wherever the syllabus's OWN next labeled
    heading actually starts (_next_heading_start), not a fixed character count —
    see that function's docstring for why a hardcoded cutoff is wrong in both
    directions."""
    keys = tuple(_SYLLABUS_KEY_BY_MARKER.values())
    if not text:
        out = {k: "NOT AVAILABLE — syllabus not ingested" for k in keys}
        out["supplemental_references"] = "NOT AVAILABLE — syllabus not ingested"
        return out
    out: Dict[str, str] = {}
    for marker, key in _SYLLABUS_KEY_BY_MARKER.items():
        m = re.search(re.escape(marker) + r"\s*:\s*", text)
        if m:
            end = _next_heading_start(text, m.end())
            out[key] = text[m.end():end].strip()
    grading_table = _grading_percentage_table(text)
    if grading_table:
        out["grading_policy"] = f"{out.get('grading_policy', '')} {grading_table}".strip()
    for key in keys:
        out.setdefault(key, "NOT AVAILABLE — not found in syllabus text")
    out["supplemental_references"] = _supplemental_references(text)
    return out


def _json_list(value: Any) -> List[str]:
    """`_load_days` selects assignments_json/assessments_json as `::text` (the
    JSONB column's string representation, not a deserialized value) — parse it
    back into a list rather than iterating the raw string's characters.

    Real calendar cells sometimes embed raw newlines inside one item (caught
    live: Day 17's assessments_json entry was literally "Quiz 10 ... Days 5 &
    6\\n\\nBlock 2 Review Quiz\\n(50 Questions)") — every downstream markdown
    table row is single-line, so an unstripped newline here breaks that row's
    syntax and can silently truncate everything the renderer treats as "after"
    it. Collapsed once here so every caller (not just today's day-table) is
    protected, not just the one render call site that happened to expose it."""
    if isinstance(value, list):
        items = [v for v in value if v]
    elif isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            items = [v for v in parsed if v] if isinstance(parsed, list) else []
        except Exception:
            items = []
    else:
        items = []
    return [" ".join(str(item).split()) for item in items]


def build_day_fields(day: Dict[str, Any], units: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Per-day fields for the Day-by-Day Map that need the raw calendar row /
    day's units — not derivable from the projected day summary alone."""
    projects = _json_list(day.get("assignments_json"))
    assessments = _json_list(day.get("assessments_json"))
    m = _REFERENCE_READING_RE.search(day.get("source_text") or "")
    handbook_reference = m.group(1).strip() if m else ""
    hb_match = _HANDBOOK_RE.search(handbook_reference)

    def _fname(u: Dict[str, Any]):
        return (u.get("metadata_json") or {}).get("source_file_name") or u.get("title")

    files = sorted({
        _fname(u) for u in units if u.get("unit_type") != "calendar_day" and _fname(u)
    })
    # Hangar activities get their own AIM Day-by-Day Map column, split out of the
    # general file list — matched by "hangar" appearing in whatever document_type
    # string the tenant's own client profile assigned (confirmed live: AIM's is
    # literally "hangar_activity"), not a hardcoded per-client filename check.
    hangar_files = sorted({
        _fname(u) for u in units
        if _fname(u) and "hangar" in str((u.get("metadata_json") or {}).get("document_type") or "").lower()
    })
    return {
        "projects_today": projects,
        "assessment_today": assessments,
        "handbook_reference": handbook_reference,
        "handbook_edition": hb_match.group(0) if hb_match else "",
        "source_files_today": files,
        "hangar_activity_today": hangar_files,
    }


def build_block_overview(en, cur=None, schema: str = "dis") -> Dict[str, Any]:
    """Block-level summary: totals, ACS subjects, handbook/supplemental-reference
    citations, web resources, and verbatim syllabus fields where the source is
    available. ``schema`` must be the tenant's own structure_store.schema_name —
    passing the default only ever gives the right answer by coincidence for
    tenants that happen to also use "dis"."""
    total_projects, total_quizzes = _project_quiz_counts(en.days)
    subjects = sorted({_subject_letter(c) for c in en.declared_acs if _subject_letter(c) != "?"})
    primary_handbooks = _primary_handbooks(en.days)
    web_resources = build_web_resources(en.days, en.units_by_day)

    syllabus_fields = {k: "NOT AVAILABLE — syllabus not ingested" for k in
                       ("course_description", "course_objectives", "grading_policy",
                        "supplemental_references")}
    if cur is not None:
        try:
            text = _syllabus_text(en, cur, schema)
            syllabus_fields = _extract_syllabus_fields(text)
        except Exception:
            pass  # best-effort; overview must never fail the whole build over this

    return {
        "block": en.block,
        "total_days": en.total_days,
        "total_projects": total_projects,
        "total_quizzes": total_quizzes,
        "acs_subjects_covered": subjects,
        "primary_handbooks": primary_handbooks or ["NOT AVAILABLE — no handbook citations found"],
        "web_resources": web_resources or ["NOT AVAILABLE — none found in ingested text"],
        **syllabus_fields,
    }
