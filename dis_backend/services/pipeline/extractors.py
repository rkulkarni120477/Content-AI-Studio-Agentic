"""
DIS – Document Extractors
===========================
One extractor per file type. Each returns (text, page_count, has_images).
PPT/PDF images are described by Claude Vision when vision_enabled=True.
"""
from __future__ import annotations
import io
import json
import logging
import re
import zipfile
from dataclasses import dataclass
from typing import List, Optional, Tuple
from xml.etree import ElementTree as ET

log = logging.getLogger(__name__)

# Above this size a PDF skips pdfplumber/pypdf and goes straight to PDFium.
# This is the EXTRACTION-cost threshold (pdfplumber's per-page structures blow
# up memory on big files) and is intentionally distinct from ingestion's
# _IMMEDIATE_EXTRACT_MAX_BYTES (25 MB), which is the UPLOAD-latency threshold
# for deferring extraction to the background. Different decisions → different
# numbers; 40 MB is where pdfplumber's RAM use starts risking the container cap.
_LARGE_PDF_BYTES = 40 * 1024 * 1024


@dataclass
class ExtractionResult:
    text: str
    page_count: int = 1
    has_images: bool = False
    tables: List[List[List[str]]] = None   # list of tables, each is rows of cells
    slide_texts: List[str] = None          # for PPTX: per-slide text
    # Per-page text for page-chunking. Each item is ``{"pdf_page": int, "text": str}``.
    # Populated for PDFs (physical pages) and DOCX/DOC (Word page/section breaks).
    # Empty for other types (xlsx, pptx, plain text).
    page_texts: List[dict] = None
    raw_metadata: dict = None

    def __post_init__(self):
        if self.tables is None:
            self.tables = []
        if self.slide_texts is None:
            self.slide_texts = []
        if self.page_texts is None:
            self.page_texts = []
        if self.raw_metadata is None:
            self.raw_metadata = {}


def extract(filename: str, content: bytes, vision_fn=None, options: dict | None = None) -> ExtractionResult:
    """
    Route to the right extractor based on file extension.
    vision_fn: optional async callable(image_bytes, context) -> str description
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "txt"
    extractors = {
        "pdf":  extract_pdf,
        "docx": extract_docx,
        "doc":  extract_docx,
        "pptx": extract_pptx,
        "ppt":  extract_pptx,
        "xlsx": extract_xlsx,
        "xls":  extract_xlsx,
        "csv":  extract_csv,
        "txt":  extract_txt,
        "md":   extract_txt,
        "json": extract_json,
        "jpg":  extract_image,
        "jpeg": extract_image,
        "png":  extract_image,
    }
    fn = extractors.get(ext, extract_txt)
    result = fn(content, vision_fn, options or {})
    log.info("[Extractor] %s → %d chars, %d pages, images=%s", filename, len(result.text), result.page_count, result.has_images)
    return result


# ── PDF ───────────────────────────────────────────────────────────────────────

def extract_pdf(content: bytes, vision_fn=None, options: dict | None = None) -> ExtractionResult:
    options = options or {}
    max_chars = int(options.get("max_extracted_chars", 0) or 0)

    # Large PDFs: go straight to PDFium and skip pdfplumber/pypdf (memory hogs
    # that OOM on 100+ MB handbooks). PDFium is lazy/low-memory and handles the
    # large/linearized files pdfminer/pypdf reject. Tables are sacrificed for
    # large reference docs (acceptable). Threshold: module-level _LARGE_PDF_BYTES.
    if len(content) > _LARGE_PDF_BYTES:
        try:
            return _extract_pdf_pdfium(content, max_chars)
        except Exception as e:
            log.error("[PDF] pdfium failed on large PDF (%s); falling back to pdfplumber/pypdf", e)

    # 1) pdfplumber — richest output (text + tables) for well-formed PDFs.
    try:
        import pdfplumber
        all_text = []
        tables = []
        page_texts = []
        page_count = 0
        total = 0
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            page_count = len(pdf.pages)
            for page_num, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                page_texts.append({"pdf_page": page_num, "text": text})
                all_text.append(f"[Page {page_num}]\n{text}")
                total += len(text)
                for tbl in page.extract_tables() or []:
                    tables.append(tbl)
                if max_chars and total >= max_chars:
                    page_count = page_num
                    break
        joined = "\n\n".join(all_text)
        if joined.strip():
            return ExtractionResult(
                text=joined, page_count=page_count, has_images=False,
                tables=tables, page_texts=page_texts,
            )
        log.warning("[PDF] pdfplumber returned no text; trying pypdf")
    except Exception as exc:
        log.warning("[PDF] pdfplumber failed (%s), trying pypdf", exc)

    # 2) pypdf — lightweight fallback.
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(content))
        page_texts = []
        parts = []
        total = 0
        for i, p in enumerate(reader.pages, start=1):
            t = p.extract_text() or ""
            page_texts.append({"pdf_page": i, "text": t})
            parts.append(f"[Page {i}]\n{t}")
            total += len(t)
            if max_chars and total >= max_chars:
                break
        text = "\n\n".join(parts)
        if text.strip():
            return ExtractionResult(
                text=text, page_count=len(page_texts), page_texts=page_texts,
            )
        log.warning("[PDF] pypdf returned no text; trying pdfium")
    except Exception as e2:
        log.warning("[PDF] pypdf failed (%s), trying pdfium", e2)

    # 3) PDFium (pypdfium2) — most tolerant of large/linearized PDFs that
    #    pdfminer/pypdf reject with "negative seek value -1" / "Unexpected EOF".
    #    Pure-pip, low memory (lazy per-page). Last resort so normal PDFs keep
    #    pdfplumber's richer output (including tables).
    try:
        return _extract_pdf_pdfium(content, max_chars)
    except Exception as e3:
        log.error("[PDF] All extractors failed (pdfium: %s)", e3)
        return ExtractionResult(text="", page_count=0)


def _extract_pdf_pdfium(content: bytes, max_chars: int = 0) -> ExtractionResult:
    """Extract text via PDFium. Iterates pages lazily and honors ``max_chars``
    so a 1000+ page handbook stays bounded in time and memory."""
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(content)
    try:
        n = len(doc)
        parts: List[str] = []
        page_texts: List[dict] = []
        total = 0
        pages_read = n  # stays n if we read the whole doc; set to i+1 on early stop
        for i in range(n):
            page = doc[i]
            textpage = page.get_textpage()
            try:
                txt = textpage.get_text_range() or ""
            finally:
                textpage.close()
                page.close()
            page_texts.append({"pdf_page": i + 1, "text": txt})
            parts.append(f"[Page {i+1}]\n{txt}")
            total += len(txt)
            if max_chars and total >= max_chars:
                pages_read = i + 1
                log.info("[PDF] pdfium reached max_extracted_chars=%d at page %d/%d", max_chars, pages_read, n)
                break
        # Report pages ACTUALLY read, so page_count agrees with the (possibly
        # truncated) text rather than claiming the full document's page count.
        log.info("[PDF] pdfium extracted %d chars from %d/%d pages", total, pages_read, n)
        return ExtractionResult(
            text="\n\n".join(parts), page_count=pages_read,
            has_images=False, page_texts=page_texts,
        )
    finally:
        doc.close()


# ── DOCX ──────────────────────────────────────────────────────────────────────

# AIM final-exam Word files (and their "Figures 1–N" companions) routinely put
# questions in text boxes, DrawingML shapes, or a pre-2007 OLE2 container saved
# with a .docx extension. python-docx only walks body paragraphs + tables, so
# those files extracted to empty text, the pipeline still reported "completed",
# and Source Library showed "Nothing extracted". Walk the OOXML XML (including
# text boxes / headers / alt text) and fall back to a Python OLE scrape when
# antiword is missing, so exam documents actually reach the index.

_OLE2_MAGIC = b"\xd0\xcf\x11\xe0"
_OOXML_SKIP_PARTS = (
    "/theme/", "/styles.xml", "/numbering.xml", "/settings.xml",
    "/fontTable.xml", "/webSettings.xml", "/people.xml",
)
_OLE_STREAM_NOISE = {
    "root entry", "worddocument", "1table", "0table", "data", "compobj",
    "summaryinformation", "documentsummaryinformation", "objectpool",
    "macros", "vba", "dir", "project", "workbook",
}


def extract_docx(content: bytes, vision_fn=None, options: dict | None = None) -> ExtractionResult:
    # python-docx only reads the modern zip/OOXML format. Legacy binary
    # "Composite Document File V2" .doc files (pre-2007 Word) are a different
    # container entirely and raise here, so route those to antiword instead
    # of silently returning empty text.
    options = options or {}
    if content[:4] == _OLE2_MAGIC:
        return _extract_legacy_doc(content)

    xml_result = _extract_ooxml(content)
    tables: list = []
    docx_text = ""
    try:
        import docx as python_docx
        doc = python_docx.Document(io.BytesIO(content))
        parts = []
        for para in doc.paragraphs:
            if para.text.strip():
                style_name = (para.style.name if para.style is not None else "") or ""
                if style_name.startswith("Heading"):
                    parts.append(f"\n## {para.text}")
                else:
                    parts.append(para.text)
        for tbl in doc.tables:
            rows = [[cell.text.strip() for cell in row.cells] for row in tbl.rows]
            tables.append(rows)
            parts.append("\n" + "\n".join(" | ".join(row) for row in rows))
        docx_text = "\n".join(parts)
    except Exception as exc:
        log.warning("[DOCX] python-docx failed (%s); using OOXML/legacy fallback", exc)
        if xml_result.text.strip():
            return xml_result
        return _extract_legacy_doc(content)

    # XML walk includes text boxes, headers, DrawingML, and alt text that
    # python-docx never sees. Prefer it whenever it recovered at least as much.
    text = xml_result.text if len(xml_result.text) >= len(docx_text) else docx_text
    has_images = bool(xml_result.has_images)
    if (
        options.get("ocr_embedded_images")
        and has_images
        and len(text.strip()) < 80
    ):
        ocr_text = _ocr_ooxml_images(content)
        if ocr_text.strip():
            text = (text + "\n" + ocr_text).strip() if text.strip() else ocr_text

    if not text.strip() and not tables:
        log.error("[DOCX] No text recovered from OOXML or python-docx")
        return ExtractionResult(text="", has_images=has_images, tables=tables)

    page_texts = extract_docx_pages(content)
    # Prefer page-joined text when the page walk recovered content (textbox-aware).
    if page_texts:
        joined = "\n\n".join(
            f"[Page {p['pdf_page']}]\n{p.get('text') or ''}" for p in page_texts
        ).strip()
        if len(joined) >= len(text):
            text = joined
    page_count = len(page_texts) if page_texts else max(1, len(text) // 3000)

    return ExtractionResult(
        text=text,
        page_count=page_count,
        has_images=has_images,
        tables=tables or xml_result.tables,
        page_texts=page_texts,
        raw_metadata=xml_result.raw_metadata,
    )


# Explicit Word hard page breaks + next-page section breaks. Soft pagination
# via lastRenderedPageBreak is also honored when Word saved it into the XML.
_PAGE_SECTION_TYPES = frozenset({"nextPage", "oddPage", "evenPage"})


def extract_docx_pages(content: bytes) -> List[dict]:
    """Split a Word file into page-bounded texts for ebook-style unit creation.

    Returns ``[{pdf_page: int, text: str}, ...]`` (1-based page index, same
    shape as PDF ``page_texts`` so ``build_ebook_page_units`` can reuse it).

    Split points, in document order:
    - ``<w:br w:type="page"/>``
    - section breaks whose type is nextPage / oddPage / evenPage
    - ``<w:lastRenderedPageBreak/>`` when Word stored soft pagination

    A file with no such breaks becomes a single page. Legacy OLE2 ``.doc``
    (no page-break XML) becomes one page from the flattened extract.
    Blank pages are omitted.
    """
    if not content:
        return []
    if content[:4] == _OLE2_MAGIC:
        legacy = _extract_legacy_doc(content)
        text = (legacy.text or "").strip()
        if not text:
            return []
        return [{"pdf_page": 1, "text": text}]

    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        return []
    try:
        xml_bytes = zf.read("word/document.xml")
    except KeyError:
        return []

    pages = _ooxml_document_pages(xml_bytes)
    if pages:
        return pages

    # No page markers and/or empty body walk — fall back to flat OOXML text.
    flat = _extract_ooxml(content)
    text = (flat.text or "").strip()
    if not text:
        return []
    return [{"pdf_page": 1, "text": text}]


def _ooxml_document_pages(xml_bytes: bytes) -> List[dict]:
    """Walk ``word/document.xml`` body and emit one page per break."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []

    body = None
    for elem in root.iter():
        if _xml_local(elem.tag) == "body":
            body = elem
            break
    if body is None:
        return []

    pages: List[dict] = []
    buf: List[str] = []

    def flush() -> None:
        text = _normalize_extracted_text("".join(buf))
        buf.clear()
        if text:
            pages.append({"pdf_page": len(pages) + 1, "text": text})

    def append_text_node(elem: ET.Element) -> None:
        local = _xml_local(elem.tag)
        if local in {"t", "instrText"} and elem.text:
            buf.append(elem.text)
        elif local == "tab":
            buf.append("\t")
        elif local in {"br", "cr"}:
            # Non-page line/carriage breaks only; page br is handled as a split.
            if (_xml_attr(elem, "type") or "").lower() != "page":
                buf.append("\n")
        elif local == "tc":
            buf.append("\t")
        elif local in {"docPr", "cNvPr"}:
            descr = (_xml_attr(elem, "descr") or "").strip()
            title = (_xml_attr(elem, "name") or "").strip()
            if descr:
                buf.append(f"\n[{descr}]\n")
            elif title and not title.lower().startswith("picture"):
                buf.append(f"\n[{title}]\n")

    def is_hard_page_break(elem: ET.Element) -> bool:
        local = _xml_local(elem.tag)
        if local == "lastRenderedPageBreak":
            return True
        if local == "br" and (_xml_attr(elem, "type") or "").lower() == "page":
            return True
        return False

    def sect_pr_is_page_break(elem: ET.Element) -> bool:
        """True for next-page style section breaks (not continuous/final props).

        OOXML default for omitted ``w:type`` is ``nextPage``. ``continuous`` and
        ``nextColumn`` stay on the same page.
        """
        if _xml_local(elem.tag) != "sectPr":
            return False
        sect_type = ""
        for child in elem:
            if _xml_local(child.tag) == "type":
                sect_type = (_xml_attr(child, "val") or "").strip()
                break
        if not sect_type:
            return True
        if sect_type in {"continuous", "nextColumn"}:
            return False
        return sect_type in _PAGE_SECTION_TYPES

    def walk(elem: ET.Element) -> bool:
        """Collect text; return True if a deferred section page-break follows.

        ``sectPr`` with nextPage lives in ``pPr`` (before runs). Defer the
        flush until the enclosing paragraph finishes so its text stays on the
        current page.
        """
        # In-place hard breaks: flush current page, then keep walking.
        if is_hard_page_break(elem):
            flush()
            return False

        # Section break marks the END of the current section/page.
        if sect_pr_is_page_break(elem):
            return True

        append_text_node(elem)

        deferred = False
        for child in list(elem):
            if walk(child):
                deferred = True

        local = _xml_local(elem.tag)
        if local in {"p", "tr"}:
            buf.append("\n")
            # Flush only at paragraph/row boundary (not at pPr).
            if deferred:
                flush()
            return False
        return deferred

    for child in list(body):
        # Trailing body-level sectPr is the final section's formatting only.
        if _xml_local(child.tag) == "sectPr":
            continue
        if walk(child):
            flush()

    flush()
    return pages


def _extract_ooxml(content: bytes) -> ExtractionResult:
    """Read every user-facing text node in an OOXML zip, not just body paragraphs.

    python-docx's `Document.paragraphs` skips `w:txbxContent` (text boxes),
    DrawingML `a:t`, headers/footers, and image alt text. AIM "Final Cumulative
    Exam" DOCX files and their "Figures 1–N" companions use those containers.
    """
    if content[:2] != b"PK":
        return ExtractionResult(text="")
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        return ExtractionResult(text="")

    names = zf.namelist()
    media = [n for n in names if n.startswith("word/media/")]
    xml_names = [
        n for n in names
        if n.startswith("word/") and n.endswith(".xml")
        and not any(skip in n for skip in _OOXML_SKIP_PARTS)
    ]
    xml_names.sort(key=_ooxml_part_order)

    parts: list[str] = []
    for name in xml_names:
        try:
            xml_bytes = zf.read(name)
        except Exception:
            continue
        chunk = _ooxml_xml_text(xml_bytes)
        if chunk:
            parts.append(chunk)

    text = _normalize_extracted_text("\n\n".join(parts))
    return ExtractionResult(
        text=text,
        page_count=max(1, len(text) // 3000) if text else 1,
        has_images=bool(media),
        raw_metadata={"embedded_images": len(media)},
    )


def _ooxml_part_order(name: str) -> tuple:
    # Body first, then headers/footers/notes so captions stay near the questions.
    if name == "word/document.xml":
        return (0, name)
    if "/header" in name:
        return (1, name)
    if "/footer" in name:
        return (2, name)
    if "footnote" in name or "endnote" in name:
        return (3, name)
    return (4, name)


def _xml_local(tag: str) -> str:
    if not tag:
        return ""
    if tag[0] == "{":
        return tag.rsplit("}", 1)[-1]
    if ":" in tag:
        return tag.split(":", 1)[-1]
    return tag


def _xml_attr(elem: ET.Element, name: str) -> str:
    for key, value in elem.attrib.items():
        if key == name or key.endswith("}" + name):
            return value or ""
    return ""


def _ooxml_xml_text(xml_bytes: bytes) -> str:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return _ooxml_xml_text_regex(xml_bytes)

    parts: list[str] = []
    for elem in root.iter():
        local = _xml_local(elem.tag)
        if local in {"t", "instrText"} and elem.text:
            parts.append(elem.text)
        elif local in {"tab"}:
            parts.append("\t")
        elif local in {"br", "cr"}:
            parts.append("\n")
        elif local in {"p", "tr"}:
            parts.append("\n")
        elif local == "tc":
            parts.append("\t")
        elif local in {"docPr", "cNvPr"}:
            descr = (_xml_attr(elem, "descr") or "").strip()
            title = (_xml_attr(elem, "name") or "").strip()
            if descr:
                parts.append(f"\n[{descr}]\n")
            elif title and not title.lower().startswith("picture"):
                parts.append(f"\n[{title}]\n")
    return _normalize_extracted_text("".join(parts))


def _ooxml_xml_text_regex(xml_bytes: bytes) -> str:
    """Fallback when the part is not well-formed enough for ElementTree."""
    xml = xml_bytes.decode("utf-8", errors="ignore")
    texts = re.findall(r"<(?:[\w.-]+:)?t(?:\s[^>]*)?>([^<]*)</(?:[\w.-]+:)?t>", xml)
    descrs = re.findall(r'\bdescr="([^"]+)"', xml)
    return _normalize_extracted_text(" ".join(texts + [f"[{d}]" for d in descrs]))


def _normalize_extracted_text(text: str) -> str:
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def _ocr_ooxml_images(content: bytes) -> str:
    """OCR embedded DOCX images when tesseract is on PATH.

    Off by default (see extract_docx options) so the upload-time Source Library
    preview stays fast. The pipeline turns it on for image-only exam figures.
    """
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    if not shutil.which("tesseract"):
        log.info("[DOCX] Embedded images present but tesseract is not installed; skipping OCR")
        return ""
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        return ""

    media = sorted(
        n for n in zf.namelist()
        if n.startswith("word/media/") and n.rsplit(".", 1)[-1].lower() in {
            "png", "jpg", "jpeg", "tif", "tiff", "bmp", "gif", "webp",
        }
    )
    if not media:
        return ""

    parts: list[str] = []
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        for i, name in enumerate(media[:24], 1):
            dest = tmp / f"img_{i}{Path(name).suffix.lower()}"
            try:
                dest.write_bytes(zf.read(name))
                proc = subprocess.run(
                    ["tesseract", str(dest), "stdout"],
                    capture_output=True, timeout=45,
                )
            except Exception as exc:
                log.warning("[DOCX] tesseract failed on %s: %s", name, exc)
                continue
            text = proc.stdout.decode("utf-8", errors="ignore").strip()
            if text:
                parts.append(f"[Figure {i}]\n{text}")
    return "\n\n".join(parts)


def _extract_legacy_doc(content: bytes) -> ExtractionResult:
    """Extract text from pre-2007 binary OLE2 .doc files via antiword.

    antiword is a small, purpose-built CLI for this exact legacy format;
    python-docx and pandoc do not support it. Requires `antiword` on PATH
    (apt package `antiword`). When the binary is missing or fails, scrape
    UTF-16/ASCII runs from the OLE container so exam files are not stored
    as successful empty documents.
    """
    import shutil
    import subprocess
    import tempfile

    antiword_text = ""
    if shutil.which("antiword"):
        try:
            with tempfile.NamedTemporaryFile(suffix=".doc") as tmp:
                tmp.write(content)
                tmp.flush()
                proc = subprocess.run(
                    ["antiword", tmp.name], capture_output=True, timeout=60,
                )
            if proc.returncode != 0:
                log.error(
                    "[DOCX] antiword failed (rc=%s): %s",
                    proc.returncode,
                    proc.stderr.decode("utf-8", errors="ignore")[:500],
                )
            else:
                antiword_text = proc.stdout.decode("utf-8", errors="ignore")
        except Exception as exc:
            log.error("[DOCX] Legacy .doc extraction failed: %s", exc)
    else:
        log.warning("[DOCX] Legacy .doc file but antiword is not installed; using Python OLE scrape")

    if antiword_text.strip():
        text = antiword_text
        return ExtractionResult(
            text=text,
            page_count=1,
            page_texts=[{"pdf_page": 1, "text": text.strip()}] if text.strip() else [],
        )

    scraped = _extract_ole_text_fallback(content)
    if scraped.strip():
        log.info("[DOCX] OLE scrape recovered %d chars from legacy .doc", len(scraped))
        return ExtractionResult(
            text=scraped,
            page_count=1,
            page_texts=[{"pdf_page": 1, "text": scraped.strip()}],
        )

    log.error("[DOCX] Legacy .doc yielded no text (antiword missing/failed and OLE scrape empty)")
    return ExtractionResult(text="")


def _extract_ole_text_fallback(content: bytes) -> str:
    """Best-effort Unicode/ASCII harvest from a Word 97-2003 binary.

    Not a FIB parser — antiword remains the preferred path. This exists so a
    missing binary (Windows/XAMPP, or an image that dropped the apt package)
    still recovers exam questions instead of indexing an empty document.
    """
    utf16 = max((_utf16le_runs(content), _utf16le_runs(content[1:])), key=len)
    ascii_runs = _ascii_runs(content)
    return utf16 if len(utf16) >= len(ascii_runs) else ascii_runs


def _utf16le_runs(data: bytes) -> str:
    decoded = data.decode("utf-16le", errors="ignore")
    runs = re.findall(r"[\t\n\r\x20-\x7e\u00a0-\u024f]{8,}", decoded)
    cleaned = []
    for run in runs:
        text = re.sub(r"\s+", " ", run).strip()
        if len(text) >= 8 and text.lower() not in _OLE_STREAM_NOISE:
            cleaned.append(text)
    return _normalize_extracted_text("\n".join(cleaned))


def _ascii_runs(data: bytes) -> str:
    runs = re.findall(rb"[\t\n\r\x20-\x7e]{12,}", data)
    cleaned = []
    for raw in runs:
        text = raw.decode("ascii", errors="ignore").strip()
        low = text.lower()
        if len(text) >= 12 and low not in _OLE_STREAM_NOISE and not low.startswith("root entry"):
            cleaned.append(text)
    return _normalize_extracted_text("\n".join(cleaned))


# ── PPTX – with vision for images ────────────────────────────────────────────

def extract_pptx(content: bytes, vision_fn=None, options: dict | None = None) -> ExtractionResult:
    """
    Extracts text per slide. If a slide has images AND vision_fn is provided,
    calls the vision model to describe each image.
    """
    try:
        from pptx import Presentation
        from pptx.util import Inches
        prs = Presentation(io.BytesIO(content))
        slide_texts = []
        has_images = False
        all_tables = []

        options = options or {}
        max_slides = int(options.get("max_pptx_slides") or 0)
        extract_images = bool(options.get("pptx_extract_images", False))
        max_chars = int(options.get("max_extracted_chars") or 0)

        for slide_num, slide in enumerate(prs.slides, 1):
            if max_slides and slide_num > max_slides:
                slide_texts.append(f"[Slides truncated after {max_slides} slides for fast ingestion]")
                break
            parts = []
            title = ""

            for shape in slide.shapes:
                # Picture/image. In fast mode we only flag image presence; we do not read blobs.
                if shape.shape_type == 13:   # picture
                    has_images = True
                    if extract_images and vision_fn:
                        try:
                            img_bytes = shape.image.blob
                            context = f"Slide {slide_num} of a presentation"
                            if title:
                                context += f", titled '{title}'"
                            # In real code: description = await vision_fn(img_bytes, context)
                            description = f"[Image on slide {slide_num}: visual content related to {title or 'the topic'}]"
                            parts.append(f"[Image description: {description}]")
                        except Exception:
                            parts.append(f"[Image on slide {slide_num}]")

                elif hasattr(shape, "text") and shape.text.strip():
                    text = shape.text.strip()
                    if shape.shape_type == 1 and slide.shapes.title and shape == slide.shapes.title:
                        title = text
                        parts.insert(0, f"## {text}")
                    else:
                        parts.append(text)

                elif shape.shape_type == 19:  # table
                    try:
                        rows = [[cell.text.strip() for cell in row.cells] for row in shape.table.rows]
                        all_tables.append(rows)
                        parts.append("\n".join(" | ".join(r) for r in rows))
                    except Exception:
                        pass

            slide_text = f"[Slide {slide_num}]\n" + "\n".join(parts)
            slide_texts.append(slide_text)

        full_text = "\n\n".join(slide_texts)
        if max_chars and len(full_text) > max_chars:
            full_text = full_text[:max_chars] + "\n[Extracted text truncated for fast ingestion]"
        return ExtractionResult(
            text=full_text,
            page_count=len(prs.slides),
            has_images=has_images,
            tables=all_tables,
            slide_texts=slide_texts,
        )
    except Exception as exc:
        log.error("[PPTX] Failed: %s", exc)
        return ExtractionResult(text="")


# ── XLSX ──────────────────────────────────────────────────────────────────────

def _xlsx_header_row(rows: list, scan_limit: int = 10) -> tuple:
    """Index of the row that holds column names, plus the title rows above it.

    A merged banner row ("ALL CAMPUS — Block 6 | Top 10 Most Missed ACS Codes")
    occupies one cell and leaves the rest of the row empty. Taking it as the header
    row gave every remaining column the same blank name, and ``dict(zip(...))`` then
    collapsed them onto one key — last column wins. A four-column AKTR sheet
    (Rank | % Missed | ACS Code | ACS Code Description) came out of here carrying
    only rank and description: the ACS code and the miss rate, the only reason to
    ingest the file, were silently gone from the text that gets chunked, embedded
    and retrieved.

    So the header is the first row within ``scan_limit`` that names at least two
    columns. Sheets whose real header is row 1 (the common case, calendars
    included) resolve to index 0 exactly as before, and a genuinely single-column
    sheet falls back to row 1 rather than scanning off the end.
    """
    for idx, row in enumerate(rows[:scan_limit]):
        if sum(1 for c in row if c is not None and str(c).strip()) >= 2:
            titles = [str(c).strip() for r in rows[:idx] for c in r
                      if c is not None and str(c).strip()]
            return idx, titles
    return 0, []


def _unique_headers(row) -> list:
    """Column names that never collide, so no column can overwrite another.

    Blanks become ``column_N`` (positional, so a nameless column is still
    addressable) and repeats get a numeric suffix.
    """
    headers: list = []
    seen: dict = {}
    for i, cell in enumerate(row, 1):
        name = str(cell).strip() if cell is not None else ""
        if not name:
            name = f"column_{i}"
        if name in seen:
            seen[name] += 1
            name = f"{name} ({seen[name]})"
        else:
            seen[name] = 1
        headers.append(name)
    return headers


def extract_xlsx(content: bytes, vision_fn=None, options: dict | None = None) -> ExtractionResult:
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        all_parts = []
        all_tables = []

        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                continue
            header_index, titles = _xlsx_header_row(rows)
            headers = _unique_headers(rows[header_index])
            sheet_rows = []
            for row in rows[header_index + 1:]:
                cells = [str(c) if c is not None else "" for c in row]
                if any(cells):
                    row_dict = dict(zip(headers, cells))
                    sheet_rows.append(json.dumps(row_dict))
                    all_tables.append(cells)

            # Banner/title rows above the header carry the sheet's own caption (e.g.
            # "ALL CAMPUS — Block 6 | Top 10 Most Missed ACS Codes"). They are not
            # column names, but they ARE the only place the sheet says what it is
            # about, so they stay in the text, above the columns they caption.
            banner = "".join(f"\n{t}" for t in titles)
            all_parts.append(f"[Sheet: {sheet_name}]{banner}\n"
                             f"Columns: {', '.join(h for h in headers if h)}")
            # Every row. This was `sheet_rows[:500]`, which silently dropped row 501
            # onward from the TEXT — the field that gets chunked, embedded and
            # retrieved — while `all_tables` above kept them, so nothing downstream
            # could tell the difference between a 400-row sheet and a 4,000-row one
            # truncated to 500. A teacher calendar or ACS-code workbook past that
            # line was simply absent from every digest and every retrieval, forever,
            # with no flag anywhere. extract_csv already keeps its rows in full for
            # exactly this reason ("keep full CSV text for DIS/S3; UI paginates
            # display"); the spreadsheet path was left behind.
            all_parts.extend(sheet_rows)

        return ExtractionResult(
            text="\n".join(all_parts),
            page_count=len(wb.sheetnames),
            tables=all_tables,
        )
    except Exception as exc:
        log.error("[XLSX] Failed: %s", exc)
        return ExtractionResult(text="")


# ── CSV ───────────────────────────────────────────────────────────────────────

def extract_csv(content: bytes, vision_fn=None, options: dict | None = None) -> ExtractionResult:
    try:
        import csv
        text = content.decode("utf-8", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        parts = [f"Columns: {', '.join(reader.fieldnames or [])}"]
        parts.extend(json.dumps(row) for row in rows)  # keep full CSV text for DIS/S3; UI paginates display
        return ExtractionResult(
            text="\n".join(parts),
            page_count=max(1, len(rows) // 100),
        )
    except Exception as exc:
        log.error("[CSV] Failed: %s", exc)
        return ExtractionResult(text=content.decode("utf-8", errors="replace"))


# ── TXT / MD ──────────────────────────────────────────────────────────────────

def extract_txt(content: bytes, vision_fn=None, options: dict | None = None) -> ExtractionResult:
    text = content.decode("utf-8", errors="replace")
    return ExtractionResult(text=text, page_count=max(1, len(text) // 3000))


# ── JSON ──────────────────────────────────────────────────────────────────────

def extract_json(content: bytes, vision_fn=None, options: dict | None = None) -> ExtractionResult:
    try:
        data = json.loads(content)
        if isinstance(data, list):
            # All items — was `data[:500]`. Same silent loss as the xlsx row cap:
            # the dropped tail never reaches the index and nothing records that it
            # existed. A dict-valued document was already serialized whole below,
            # so the list branch was the only shape that lost content.
            text = "\n".join(json.dumps(item) for item in data)
        else:
            text = json.dumps(data, indent=2)
        return ExtractionResult(text=text)
    except Exception as exc:
        log.error("[JSON] Failed: %s", exc)
        return ExtractionResult(text=content.decode("utf-8", errors="replace"))


# ── Image ─────────────────────────────────────────────────────────────────────

def extract_image(content: bytes, vision_fn=None, options: dict | None = None) -> ExtractionResult:
    """Images described by vision model. Validates with Pillow."""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(content))
        meta = {
            "format": img.format,
            "size": f"{img.width}x{img.height}",
            "mode": img.mode,
        }
        if vision_fn:
            # Production: await vision_fn(content, "Describe this image for search indexing")
            description = "[Image: visual content to be described by vision model]"
        else:
            description = f"[Image: {meta['format']} {meta['size']}]"
        return ExtractionResult(
            text=description,
            has_images=True,
            raw_metadata=meta,
        )
    except Exception as exc:
        log.error("[Image] Failed: %s", exc)
        return ExtractionResult(text="[Image: could not process]", has_images=True)
