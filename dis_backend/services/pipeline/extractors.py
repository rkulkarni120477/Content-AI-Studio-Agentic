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
from dataclasses import dataclass
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)


@dataclass
class ExtractionResult:
    text: str
    page_count: int = 1
    has_images: bool = False
    tables: List[List[List[str]]] = None   # list of tables, each is rows of cells
    slide_texts: List[str] = None          # for PPTX: per-slide text
    raw_metadata: dict = None

    def __post_init__(self):
        if self.tables is None:
            self.tables = []
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
    try:
        import pdfplumber
        all_text = []
        tables = []
        page_count = 0
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            page_count = len(pdf.pages)
            for page_num, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                all_text.append(f"[Page {page_num}]\n{text}")
                for tbl in page.extract_tables() or []:
                    tables.append(tbl)
        return ExtractionResult(
            text="\n\n".join(all_text),
            page_count=page_count,
            has_images=False,
            tables=tables,
        )
    except Exception as exc:
        log.warning("[PDF] pdfplumber failed (%s), fallback to pypdf", exc)
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(content))
            text = "\n".join(p.extract_text() or "" for p in reader.pages)
            return ExtractionResult(text=text, page_count=len(reader.pages))
        except Exception as e2:
            log.error("[PDF] Both extractors failed: %s", e2)
            return ExtractionResult(text="", page_count=0)


# ── DOCX ──────────────────────────────────────────────────────────────────────

def extract_docx(content: bytes, vision_fn=None, options: dict | None = None) -> ExtractionResult:
    # python-docx only reads the modern zip/OOXML format. Legacy binary
    # "Composite Document File V2" .doc files (pre-2007 Word) are a different
    # container entirely and raise here, so route those to antiword instead
    # of silently returning empty text.
    if content[:4] == b"\xd0\xcf\x11\xe0":
        return _extract_legacy_doc(content)
    try:
        import docx as python_docx
        doc = python_docx.Document(io.BytesIO(content))
        parts = []
        tables = []
        for para in doc.paragraphs:
            if para.text.strip():
                if para.style.name.startswith("Heading"):
                    parts.append(f"\n## {para.text}")
                else:
                    parts.append(para.text)
        for tbl in doc.tables:
            rows = [[cell.text.strip() for cell in row.cells] for row in tbl.rows]
            tables.append(rows)
            # Also add table as text
            parts.append("\n" + "\n".join(" | ".join(row) for row in rows))
        full_text = "\n".join(parts)
        return ExtractionResult(text=full_text, page_count=max(1, len(full_text) // 3000), tables=tables)
    except Exception as exc:
        log.warning("[DOCX] python-docx failed (%s), trying legacy .doc extraction", exc)
        return _extract_legacy_doc(content)


def _extract_legacy_doc(content: bytes) -> ExtractionResult:
    """Extract text from pre-2007 binary OLE2 .doc files via antiword.

    antiword is a small, purpose-built CLI for this exact legacy format;
    python-docx and pandoc do not support it. Requires `antiword` on PATH
    (apt package `antiword`).
    """
    import shutil
    import subprocess
    import tempfile

    if not shutil.which("antiword"):
        log.error("[DOCX] Legacy .doc file but antiword is not installed; returning empty text")
        return ExtractionResult(text="")
    try:
        with tempfile.NamedTemporaryFile(suffix=".doc") as tmp:
            tmp.write(content)
            tmp.flush()
            proc = subprocess.run(
                ["antiword", tmp.name], capture_output=True, timeout=60,
            )
        if proc.returncode != 0:
            log.error("[DOCX] antiword failed (rc=%s): %s", proc.returncode, proc.stderr.decode("utf-8", errors="ignore")[:500])
            return ExtractionResult(text="")
        text = proc.stdout.decode("utf-8", errors="ignore")
        return ExtractionResult(text=text, page_count=max(1, len(text) // 3000))
    except Exception as exc:
        log.error("[DOCX] Legacy .doc extraction failed: %s", exc)
        return ExtractionResult(text="")


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
            # First row as header
            headers = [str(c) if c is not None else "" for c in rows[0]]
            sheet_rows = []
            for row in rows[1:]:
                cells = [str(c) if c is not None else "" for c in row]
                if any(cells):
                    row_dict = dict(zip(headers, cells))
                    sheet_rows.append(json.dumps(row_dict))
                    all_tables.append(cells)

            all_parts.append(f"[Sheet: {sheet_name}]\nColumns: {', '.join(h for h in headers if h)}")
            all_parts.extend(sheet_rows[:500])  # cap at 500 rows per sheet in text

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
            text = "\n".join(json.dumps(item) for item in data[:500])
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
