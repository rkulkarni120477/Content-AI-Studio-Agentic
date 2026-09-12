"""Page-aligned chunking for ``ebook_reference`` PDFs.

Splits on physical PDF pages (not the 512-word slider), then stamps each unit
with:

* ``pdf_page`` — 1-based file page index
* ``chapter`` — detected chapter number (sticky across interior pages)
* ``printed_page`` — integer from the handbook footer/header (e.g. ``1`` in ``13-1``)
* ``page_number`` — citation form ``"{chapter}-{printed_page}"`` (e.g. ``13-1``)
* ``chunking_strategy`` — always ``"page"`` so backfill can skip already-done files

Chapter / printed-page detection order:

1. PDF outline/bookmarks (pypdf) mapped onto destination pages
2. Header/footer tokens in the first/last few lines (body ignored)
3. Carry forward the last seen chapter
"""
from __future__ import annotations

import io
import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from services.pipeline.common import keywords

log = logging.getLogger(__name__)

_CHAPTER_HEADING_RE = re.compile(
    r"(?im)^(?:\s*)(?:chapter|ch\.?)\s+(\d{1,2})\b"
)
_PRINTED_PAGE_RE = re.compile(r"\b(\d{1,2})-(\d{1,3})[a-z]?\b")
_OUTLINE_CHAPTER_RE = re.compile(
    r"(?i)\b(?:chapter|ch\.?)\s+(\d{1,2})\b|^(\d{1,2})\s*[\.\:\-–—]\s+\S"
)

#: How many lines at the top/bottom of a page count as header/footer for
#: location tokens. Body tokens are ignored so TOC / index cross-refs do not win.
_EDGE_LINES = 4


def parse_page_texts_from_raw(raw_text: str) -> List[Dict[str, Any]]:
    """Rebuild ``[{pdf_page, text}, ...]`` from extractor output that used ``[Page N]`` markers."""
    if not raw_text:
        return []
    matches = list(re.finditer(r"(?m)^\[Page\s+(\d+)\]\s*$", raw_text))
    if not matches:
        return []
    pages: List[Dict[str, Any]] = []
    for idx, m in enumerate(matches):
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw_text)
        page_no = int(m.group(1))
        text = raw_text[start:end].strip()
        pages.append({"pdf_page": page_no, "text": text})
    return pages


def extract_pdf_pages(content: bytes, max_chars: int = 0) -> List[Dict[str, Any]]:
    """Extract every PDF page as ``{pdf_page, text}``.

    Prefer PDFium for large files (same cascade intent as extractors.extract_pdf),
    but always return a structured page list — never a truncated joined string alone.
    ``max_chars=0`` means no cap (required for full-handbook rechunk).
    """
    if not content:
        return []

    # Large PDFs: go straight to PDFium (memory).
    if len(content) > 40 * 1024 * 1024:
        pages = _pages_via_pdfium(content, max_chars)
        if pages:
            return pages

    try:
        import pdfplumber
        pages: List[Dict[str, Any]] = []
        total = 0
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                text = (page.extract_text() or "").strip()
                pages.append({"pdf_page": page_num, "text": text})
                total += len(text)
                if max_chars and total >= max_chars:
                    break
        if any(p["text"] for p in pages):
            return pages
    except Exception as exc:
        log.warning("[ebook_chunker] pdfplumber failed (%s); trying pypdf", exc)

    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(content))
        pages = []
        total = 0
        for i, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            pages.append({"pdf_page": i, "text": text})
            total += len(text)
            if max_chars and total >= max_chars:
                break
        if any(p["text"] for p in pages):
            return pages
    except Exception as exc:
        log.warning("[ebook_chunker] pypdf failed (%s); trying pdfium", exc)

    return _pages_via_pdfium(content, max_chars)


def _pages_via_pdfium(content: bytes, max_chars: int = 0) -> List[Dict[str, Any]]:
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(content)
    try:
        pages: List[Dict[str, Any]] = []
        total = 0
        for i in range(len(doc)):
            page = doc[i]
            textpage = page.get_textpage()
            try:
                txt = (textpage.get_text_range() or "").strip()
            finally:
                textpage.close()
                page.close()
            pages.append({"pdf_page": i + 1, "text": txt})
            total += len(txt)
            if max_chars and total >= max_chars:
                break
        return pages
    finally:
        doc.close()


def outline_chapter_map(content: bytes) -> Dict[int, int]:
    """Map pdf_page -> chapter number from PDF outline/bookmarks when available."""
    if not content:
        return {}
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(content))
    except Exception as exc:
        log.debug("[ebook_chunker] outline unread (%s)", exc)
        return {}

    outlines = getattr(reader, "outline", None) or getattr(reader, "outlines", None)
    if not outlines:
        return {}

    mapping: Dict[int, int] = {}

    def walk(items, depth: int = 0) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if isinstance(item, list):
                walk(item, depth + 1)
                continue
            title = str(getattr(item, "title", None) or item.get("/Title") if isinstance(item, dict) else "")
            m = _OUTLINE_CHAPTER_RE.search(title or "")
            if not m:
                continue
            chapter = int(m.group(1) or m.group(2))
            try:
                page_idx = reader.get_destination_page_number(item)
            except Exception:
                try:
                    page_idx = reader.get_page_number(item)
                except Exception:
                    continue
            if page_idx is None:
                continue
            # Destination pages are 0-based; stamp that page and leave sticky
            # inheritance to tag_pages().
            mapping[int(page_idx) + 1] = chapter

    try:
        walk(outlines)
    except Exception as exc:
        log.debug("[ebook_chunker] outline walk failed (%s)", exc)
        return {}
    return mapping


def _edge_text(text: str) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return ""
    if len(lines) <= _EDGE_LINES * 2:
        return "\n".join(lines)
    return "\n".join(lines[:_EDGE_LINES] + lines[-_EDGE_LINES:])


def _detect_chapter_in_edges(edge: str) -> Optional[int]:
    m = _CHAPTER_HEADING_RE.search(edge or "")
    return int(m.group(1)) if m else None


def _detect_printed_page(edge: str, chapter: Optional[int]) -> Optional[int]:
    """Prefer a printed page whose chapter prefix matches the known chapter."""
    hits = [(int(a), int(b)) for a, b in _PRINTED_PAGE_RE.findall(edge or "")]
    if not hits:
        return None
    if chapter is not None:
        same = [b for a, b in hits if a == chapter]
        if same:
            return same[-1]  # footer usually last
    # No chapter yet: take the last edge hit (footer bias).
    return hits[-1][1]


def tag_pages(
    pages: Sequence[Dict[str, Any]],
    outline_map: Optional[Dict[int, int]] = None,
) -> List[Dict[str, Any]]:
    """Attach chapter / printed_page / page_number to each page dict (in place copy)."""
    outline_map = outline_map or {}
    tagged: List[Dict[str, Any]] = []
    current_chapter: Optional[int] = None

    for page in pages:
        pdf_page = int(page.get("pdf_page") or 0)
        text = str(page.get("text") or "")
        edge = _edge_text(text)

        if pdf_page in outline_map:
            current_chapter = outline_map[pdf_page]
        else:
            found = _detect_chapter_in_edges(edge)
            if found is not None:
                current_chapter = found

        printed = _detect_printed_page(edge, current_chapter)
        # If edges carry ``13-1`` before any "Chapter 13" heading, adopt 13.
        if current_chapter is None and printed is not None:
            hits = [(int(a), int(b)) for a, b in _PRINTED_PAGE_RE.findall(edge)]
            if hits:
                current_chapter = hits[-1][0]
                printed = hits[-1][1]

        page_number = None
        if current_chapter is not None and printed is not None:
            page_number = f"{current_chapter}-{printed}"

        tagged.append({
            "pdf_page": pdf_page,
            "text": text,
            "chapter": current_chapter,
            "printed_page": printed,
            "page_number": page_number,
        })
    return tagged


def build_ebook_page_units(
    *,
    job_id: str,
    pages: Sequence[Dict[str, Any]],
    doc_metadata: Optional[Dict[str, Any]] = None,
    unit_type: str = "page",
    title: str = "Source",
    outline_map: Optional[Dict[int, int]] = None,
) -> List[Dict[str, Any]]:
    """Build pipeline content_units — one per non-blank physical page."""
    doc_metadata = dict(doc_metadata or {})
    tagged = tag_pages(pages, outline_map=outline_map)
    units: List[Dict[str, Any]] = []
    unit_number = 0

    for page in tagged:
        text = (page.get("text") or "").strip()
        if not text:
            continue
        unit_number += 1
        pdf_page = int(page["pdf_page"])
        chapter = page.get("chapter")
        printed = page.get("printed_page")
        page_number = page.get("page_number")

        if page_number:
            unit_title = f"{title} — p. {page_number}"
        else:
            unit_title = f"{title} — PDF p. {pdf_page}"

        meta = {
            **doc_metadata,
            "chunk_index": unit_number - 1,
            "pdf_page": pdf_page,
            "chapter": chapter,
            "printed_page": printed,
            "page_number": page_number,
            "chunking_strategy": "page",
            "tagging_status": "pending",
        }
        # Drop null location fields so JSON stays clean / filterable.
        meta = {k: v for k, v in meta.items() if v not in (None, "", [])}

        units.append({
            "content_unit_id": f"{job_id}:page_{pdf_page}",
            "unit_type": unit_type,
            "unit_number": unit_number,
            "title": unit_title[:160],
            "text": text,
            "visual_summary": "",
            "keywords": keywords(text),
            "topics": keywords(text, limit=10),
            "metadata": meta,
            "assets": [],
        })
    return units


def pages_look_truncated(pages: Sequence[Dict[str, Any]], page_count: int) -> bool:
    """True when page_texts is empty or shorter than the document's reported page_count."""
    if not pages:
        return True
    if page_count and len(pages) < int(page_count):
        return True
    return False
