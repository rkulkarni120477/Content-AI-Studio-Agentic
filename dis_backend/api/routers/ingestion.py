"""
DIS – Ingestion Router  /v1/ingest
"""
from __future__ import annotations
import logging, uuid, asyncio, re, hashlib
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field
from pathlib import Path

from api.middleware.auth import get_current_tenant, require_role
from config.settings import TenantConfig, get_settings, get_tenant_config
from models.schemas import JobStatus, JobStatusResponse, UploadResponse
from services.pipeline.graph import run_pipeline
from services.validation import ValidationService
from storage.provider import get_storage_provider
from services.artifacts import ArtifactWriter
from services.source_library import write_source_content_and_index, normalize_purpose, normalize_visibility
from services.indexing import rds_upsert as do_rds_upsert, generate_embeddings, opensearch_upsert as do_opensearch_upsert

router = APIRouter(prefix="/ingest", tags=["Ingestion"])
log = logging.getLogger(__name__)
_jobs: Dict[str, Dict[str, Any]] = {}   # production: move to RDS/DynamoDB
_scans: Dict[str, Dict[str, Any]] = {}  # scan-level tracking for folder-scan


class FolderScanRequest(BaseModel):
    folder_path: str = Field(..., description="Server-side folder path. For local testing, this is a Windows path on the machine running FastAPI, for example C:\\...\\DIS\\data")
    client_id: str = ""
    course_id: str = ""
    course_name: str = ""
    recursive: bool = True
    dry_run: bool = False
    skip_duplicates: bool = True
    include_extensions: Optional[List[str]] = None
    # v7: local parallel processing. Leave blank to use tenant config processing.max_workers.
    max_workers: Optional[int] = Field(None, ge=1, le=50, description="Parallel workers for actual ingestion. Blank = config value.")
    return_results_limit: Optional[int] = Field(None, ge=1, le=1000, description="Limit number of file results returned in API response.")


def _safe_relative_path(value: str, fallback_filename: str) -> str:
    """Return a safe POSIX relative path and prevent path traversal.

    Folder ingestion passes paths like `a/b/c/file.pdf`. We preserve that
    structure under the job raw folder, but never allow absolute paths or `..`.
    """
    from pathlib import PurePosixPath
    raw = (value or fallback_filename or "unnamed").replace("\\", "/").strip().lstrip("/")
    parts = []
    for part in PurePosixPath(raw).parts:
        if part in ("", ".", ".."):
            continue
        # keep names storage-safe without destroying readable folder structure
        clean = "".join(ch if ch.isalnum() or ch in "._- ()" else "_" for ch in part).strip()
        if clean:
            parts.append(clean)
    return "/".join(parts) or (fallback_filename or "unnamed")


def _allowed_file(path: Path, include_extensions: Optional[List[str]] = None) -> bool:
    if not path.is_file():
        return False
    if include_extensions:
        allowed = {e.lower().lstrip(".") for e in include_extensions}
    else:
        allowed = {"pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx", "csv", "txt", "json", "jpg", "jpeg", "png"}
    return path.suffix.lower().lstrip(".") in allowed


def _mime_for_filename(filename: str) -> str:
    import mimetypes
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


def _clean_reading_text(text: str) -> str:
    """Return readable extraction text for CAS Source Library previews/prompts."""
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    # Drop NUL and normalize tabs/spaces while preserving paragraph boundaries.
    raw = raw.replace("\x00", " ").replace("\t", " ")
    lines = [re.sub(r"[ \u00a0]+", " ", line).strip() for line in raw.split("\n")]
    paragraphs = []
    buf = []
    for line in lines:
        if not line:
            if buf:
                paragraphs.append(" ".join(buf).strip())
                buf = []
            continue
        # Keep likely headings and bullets separate. This is important for style guides.
        is_bullet = line.startswith(("- ", "* ", "• ", "– ")) or re.match(r"^\d+[\.)]\s+", line)
        is_heading = len(line) <= 90 and not line.endswith((".", ",", ";", ":")) and re.match(r"^[A-Z0-9][A-Za-z0-9 &/()_\-–:]+$", line)
        if is_bullet or is_heading:
            if buf:
                paragraphs.append(" ".join(buf).strip())
                buf = []
            paragraphs.append(line)
        else:
            buf.append(line)
    if buf:
        paragraphs.append(" ".join(buf).strip())
    return "\n".join(p for p in paragraphs if p).strip()


def _extract_text_for_source_library(filename: str, content: bytes) -> str:
    """Best-effort local extraction used immediately after upload.

    The full pipeline may later create richer structured artifacts, but Source Library
    should show the document immediately after upload. This extractor is deliberately
    dependency-light and never calls an LLM.
    """
    suffix = Path(filename or "").suffix.lower().lstrip(".")
    try:
        if suffix == "pdf":
            from io import BytesIO
            from pypdf import PdfReader
            reader = PdfReader(BytesIO(content))
            parts = []
            for page_num, page in enumerate(reader.pages, start=1):
                try:
                    parts.append(f"[Page {page_num}]\n" + (page.extract_text() or ""))
                except Exception:
                    continue
            return _clean_reading_text("\n\n".join(parts))
        if suffix in {"docx", "doc"}:
            # Delegates to the pipeline's own docx/doc extractor so legacy
            # binary .doc (pre-2007, OLE2) files get the antiword fallback
            # too, instead of duplicating docx-only logic here.
            from services.pipeline.extractors import extract_docx
            return _clean_reading_text(extract_docx(content).text)
        if suffix in {"pptx", "ppt"}:
            from io import BytesIO
            from pptx import Presentation
            prs = Presentation(BytesIO(content))
            parts = []
            for i, slide in enumerate(prs.slides, start=1):
                slide_parts = []
                for shape in slide.shapes:
                    text = getattr(shape, "text", "")
                    if text and text.strip():
                        slide_parts.append(text.strip())
                if slide_parts:
                    parts.append(f"Slide {i}\n" + "\n".join(slide_parts))
            return _clean_reading_text("\n\n".join(parts))
        if suffix in {"xlsx", "xls"}:
            from io import BytesIO
            import openpyxl
            wb = openpyxl.load_workbook(BytesIO(content), read_only=True, data_only=True)
            parts = []
            for ws in wb.worksheets:
                parts.append(f"Sheet: {ws.title}")
                for r_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
                    vals = [str(v).strip() for v in row if v is not None and str(v).strip()]
                    if vals:
                        parts.append(f"Row {r_idx}: " + " | ".join(vals))
            return _clean_reading_text("\n".join(parts))
        # txt/csv/json and fallback decode
        return _clean_reading_text(content.decode("utf-8", errors="ignore"))
    except Exception as exc:
        log.warning("Source Library extraction failed for %s: %s", filename, exc)
        return _clean_reading_text(content[:200000].decode("utf-8", errors="ignore"))


def _guess_document_type(filename: str, supplied: str = "", purpose: str = "") -> str:
    supplied = (supplied or "").strip()
    if supplied:
        return supplied
    name = (filename or "").lower()
    if any(k in name for k in ["style guide", "style_guide", "copyediting", "copy editing", "authoring guide", "guidelines"]):
        return "style_guide"
    if "syllabus" in name:
        return "syllabus"
    if any(k in name for k in ["calendar", "schedule"]):
        return "course_calendar"
    if any(k in name for k in ["rubric", "grr"]):
        return "rubric"
    if "chapter" in name:
        return "textbook_chapter"
    if any(k in name for k in ["quiz", "exam", "assessment"]):
        return "assessment"
    if purpose == "style":
        return "style_guide"
    return Path(filename or "").suffix.lower().lstrip(".") or "document"


def _purpose_flags(purpose: str, document_type: str) -> dict:
    purpose = (purpose or "").strip() or "general_reference"
    doc = (document_type or "").strip()
    flags = {
        "use_for_style": False,
        "use_for_cdd": False,
        "use_for_blueprint": False,
        "use_for_course_generation": False,
    }
    if purpose == "style" or doc in {"style_guide", "authoring_guide", "authoring_guidelines", "copyediting_guidelines", "sample_chapter"}:
        flags["use_for_style"] = True
    elif purpose == "cdd" or doc in {"syllabus", "course_outline", "learning_objectives"}:
        flags["use_for_cdd"] = True
    elif purpose == "blueprint" or doc in {"course_calendar", "chapter_outline", "module_map"}:
        flags["use_for_blueprint"] = True
    elif purpose == "course_generation":
        flags["use_for_course_generation"] = True
    return flags


def _build_source_library_payload(
    *, tenant_cfg: TenantConfig, client_id: str, namespace: str, job_id: str, user_id: str,
    filename: str, content: bytes, content_type: str, raw_storage_url: str, s3_key: str,
    source_relative_path: str = "", source_root: str = "", metadata_hints: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    hints = metadata_hints or {}
    raw_purpose = (hints.get("purpose") or "general_reference").strip() or "general_reference"
    document_type = _guess_document_type(filename, hints.get("document_type") or "", raw_purpose)
    purpose = normalize_purpose(raw_purpose, document_type)
    visibility = normalize_visibility(hints.get("visibility") or "instructor")
    extracted = _extract_text_for_source_library(filename, content)
    if not extracted:
        extracted = f"No readable text could be extracted from {filename}."
    file_type = Path(filename or "").suffix.lower().lstrip(".") or (content_type or "application/octet-stream")
    sha = hashlib.sha256(content).hexdigest()
    now_iso = datetime.utcnow().isoformat()
    flags = _purpose_flags(purpose, document_type)
    title = hints.get("title") or Path(filename or "Source Document").stem
    metadata = {
        "title": title,
        "source_file_name": filename,
        "document_type": document_type,
        "doc_type": document_type,
        "purpose": purpose,
        "visibility": visibility,
        "access_level": visibility,
        "status": "processed",
        "file_sha256": sha,
        "content_hash": hashlib.sha256(extracted.encode("utf-8", errors="ignore")).hexdigest(),
        "course_name": hints.get("course_name") or "",
        "course_id": str(hints.get("course_id") or ""),
        "block": hints.get("block") or "",
        "day": hints.get("day") or "",
        "chapter": hints.get("chapter") or "",
        "module_name": hints.get("module_name") or "",
        "learning_objective": hints.get("learning_objective") or "",
        **flags,
    }
    # Keep style/reference rule documents as one full document unit.
    unit_type = "style_full_document" if flags.get("use_for_style") else "document_content"
    return {
        "schema_version": "source_library_v1",
        "job_id": job_id,
        "tenant_id": tenant_cfg.tenant_id,
        "client_id": client_id,
        "namespace": namespace,
        "created_at": now_iso,
        "updated_at": now_iso,
        "source_file": {
            "name": filename,
            "type": file_type,
            "mime_type": content_type or "application/octet-stream",
            "relative_path": source_relative_path or filename,
            "source_root": source_root or "",
            "raw_key": s3_key,
            "raw_url": raw_storage_url,
            "size_bytes": len(content),
        },
        "metadata": metadata,
        "content_units": [
            {
                "content_unit_id": f"{job_id}:0",
                "unit_type": unit_type,
                "unit_number": 1,
                "title": str(title),
                "text": extracted,
                "metadata": {"document_type": document_type, "purpose": purpose, "visibility": visibility},
            }
        ],
        "reading_content": extracted,
        "preview": extracted[:12000],
        "calendar_structure": {},
        "syllabus_structure": {},
        "quiz_structure": {},
        "project_structure": {},
    }


def _write_source_library_payload(
    *, tenant_cfg: TenantConfig, namespace: str, job_id: str, payload: Dict[str, Any]
) -> str:
    """Write full studio payload, clean source-content file, and compact source index.

    Source Library list endpoints read the compact index, not the full payload.
    View endpoints read the clean source-content file. This keeps CAS product UI
    simple while preserving metadata internally for DIS/debugging.
    """
    key = f"processed/{namespace}/{get_settings().environment}/{job_id}/studio_payload/payload.json"
    payload_url = ArtifactWriter(tenant_cfg).write_json(key, payload)
    client_id = str(payload.get("client_id") or "")
    if client_id:
        write_source_content_and_index(tenant_cfg, client_id, payload, payload_key=key)
    return payload_url


def _resolve_tenant_and_client_for_ingestion(request: Request, current_tenant_cfg: TenantConfig, supplied_client_id: str = "") -> tuple[TenantConfig, str]:
    """Resolve target tenant/client for write/index actions.

    v14 rule:
    - super_admin MUST pass client_id on ingestion/index actions.
      This prevents accidental uploads into the first/default client from the token.
    - client_admin can only use the client embedded in their token.
    """
    role = getattr(request.state, "role", "user")
    token_client_id = getattr(request.state, "client_id", "")
    supplied = (supplied_client_id or "").strip()

    if role == "super_admin":
        # CAS sends X-CAS-Client-Id and may also send multipart client_id.
        # Accept either one; do not fail just because multipart parsing omitted client_id.
        target_client = supplied or token_client_id
        if not target_client:
            raise HTTPException(400, "client_id is required for super_admin on ingestion/index actions")
        try:
            target_tenant_cfg = get_tenant_config(target_client)
        except (KeyError, PermissionError) as exc:
            raise HTTPException(404, f"Client '{target_client}' is not configured or active: {exc}")
        return target_tenant_cfg, target_tenant_cfg.effective_client_id(target_client)

    actual = current_tenant_cfg.effective_client_id(token_client_id)
    if supplied and current_tenant_cfg.effective_client_id(supplied) != actual:
        raise HTTPException(403, "client_admin can access only own client config/data")
    return current_tenant_cfg, actual


async def _create_ingestion_job(
    *, request: Request, background_tasks: BackgroundTasks, tenant_cfg: TenantConfig,
    actual_client_id: str, user_id: str, namespace: str, filename: str, content: bytes,
    content_type: str, source_relative_path: str = "", source_root: str = "", skip_duplicate: bool = False
) -> UploadResponse:
    client_cfg = tenant_cfg.get_client(actual_client_id)
    if not client_cfg:
        raise HTTPException(404, f"Client '{actual_client_id}' is not configured for tenant '{tenant_cfg.tenant_id}'")
    if not content:
        raise HTTPException(400, "Empty file")

    validator = ValidationService(tenant_cfg, client_cfg)
    result = await validator.validate(filename or "unnamed", content, content_type or "")
    if not result.passed:
        raise HTTPException(422, {"errors": result.errors, "warnings": result.warnings})
    # Duplicate detection is handled by DeduplicationAgent using client dedup_manifest.json.

    # Do not block file upload using LLM token quota.
    # Token quota is checked only immediately before actual LLM calls inside the pipeline.
    # Large PDFs/DOCX files can be several MB; estimating tokens from raw bytes causes false 429 errors.

    job_id = str(uuid.uuid4())
    env_name = get_settings().environment or "development"
    safe_rel_path = _safe_relative_path(source_relative_path, filename or "unnamed")
    s3_key = f"raw/{namespace}/{env_name}/{job_id}/{safe_rel_path}"
    provider = get_storage_provider(tenant_cfg)
    try:
        storage_url = await provider.upload(s3_key, content, content_type or "application/octet-stream")
    except Exception as exc:
        log.error("[%s] Storage upload failed: %s", job_id, exc)
        raise HTTPException(500, f"Storage upload failed: {exc}")

    presigned = await provider.presigned_url(s3_key, expires=3600)
    now = datetime.utcnow()
    _jobs[job_id] = {
        "job_id": job_id, "tenant_id": tenant_cfg.tenant_id, "client_id": actual_client_id,
        "user_id": user_id, "namespace": namespace, "filename": filename,
        "source_relative_path": safe_rel_path, "source_root": source_root,
        "s3_key": s3_key, "storage_url": storage_url, "status": JobStatus.PENDING,
        "progress_pct": 0, "current_step": None, "errors": [],
        "created_at": now, "updated_at": now, "completed_at": None, "metadata": {},
        "artifact_urls": {}, "payload_storage_url": "", "studio_job_id": "",
        "validation_report_url": "",
    }

    background_tasks.add_task(
        _process, job_id=job_id, tenant_cfg=tenant_cfg,
        client_id=actual_client_id, user_id=user_id, namespace=namespace,
        filename=filename or "unnamed", s3_key=s3_key, content=content, raw_storage_url=storage_url,
        source_relative_path=safe_rel_path, source_root=source_root,
    )
    return UploadResponse(
        job_id=job_id, tenant_id=tenant_cfg.tenant_id, client_id=actual_client_id,
        namespace=namespace, s3_key=s3_key, upload_url=presigned,
        status=JobStatus.PENDING, created_at=now,
    )


@router.post("/upload", response_model=UploadResponse, status_code=202)
async def upload_file(
    request: Request,
    background_tasks: BackgroundTasks,
    _=Depends(require_role("client_admin")),
    file: UploadFile = File(...),
    client_id: str = Form(""),
    source_relative_path: str = Form(""),
    source_root: str = Form(""),
    purpose: str = Form(""),
    document_type: str = Form(""),
    # Empty by default, NOT "instructor": this same dict feeds metadata_hints,
    # whose override loop treats any non-empty value as an explicit correction
    # that clobbers aim.py's deterministic content_type-based visibility. A
    # non-empty default here silently forced every single-file upload to
    # visibility="instructor" regardless of true classification (e.g. student-
    # visible projects/exams), hiding them from student-facing retrieval.
    # _build_source_library_payload still falls back to "instructor" on its
    # own for the immediate pre-classification Source Library preview.
    visibility: str = Form(""),
    course_name: str = Form(""),
    course_id: str = Form(""),
    block: str = Form(""),
    day: str = Form(""),
    chapter: str = Form(""),
    module_name: str = Form(""),
    learning_objective: str = Form(""),
):
    tenant_cfg: TenantConfig = get_current_tenant(request)
    user_id: str = getattr(request.state, "user_id", "unknown")
    tenant_cfg, actual_client_id = _resolve_tenant_and_client_for_ingestion(request, tenant_cfg, client_id)
    client_cfg = tenant_cfg.get_client(actual_client_id)
    if not client_cfg:
        raise HTTPException(404, f"Client '{actual_client_id}' is not configured for tenant '{tenant_cfg.tenant_id}'")
    namespace = tenant_cfg.get_namespace(actual_client_id)

    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty file")

    # Step 2.5 – Admission gate (before storage)
    validator = ValidationService(tenant_cfg, client_cfg)
    result = await validator.validate(file.filename or "unnamed", content, file.content_type or "")
    if not result.passed:
        raise HTTPException(422, {"errors": result.errors, "warnings": result.warnings})
    # Duplicate detection is handled by DeduplicationAgent using client dedup_manifest.json.

    # Do not block file upload using LLM token quota.
    # Token quota is checked only immediately before actual LLM calls inside the pipeline.
    # Large PDFs/DOCX files can be several MB; estimating tokens from raw bytes causes false 429 errors.

    # Upload to storage (S3 / Azure / GCP / Local).
    # Folder ingestion passes source_relative_path so arbitrary nested folders are preserved:
    #   main/a/b/c/file.pdf -> <namespace>/<env>/raw/<job_id>/main/a/b/c/file.pdf
    job_id = str(uuid.uuid4())
    env_name = get_settings().environment or "development"
    safe_rel_path = _safe_relative_path(source_relative_path, file.filename or "unnamed")
    s3_key = f"raw/{namespace}/{env_name}/{job_id}/{safe_rel_path}"
    provider = get_storage_provider(tenant_cfg)
    try:
        storage_url = await provider.upload(s3_key, content, file.content_type or "application/octet-stream")
    except Exception as exc:
        log.error("[%s] Storage upload failed: %s", job_id, exc)
        raise HTTPException(500, f"Storage upload failed: {exc}")

    presigned = await provider.presigned_url(s3_key, expires=3600)

    now = datetime.utcnow()
    _jobs[job_id] = {
        "job_id": job_id, "tenant_id": tenant_cfg.tenant_id, "client_id": actual_client_id,
        "user_id": user_id, "namespace": namespace, "filename": file.filename,
        "source_relative_path": safe_rel_path, "source_root": source_root,
        "s3_key": s3_key, "storage_url": storage_url, "status": JobStatus.PENDING,
        "progress_pct": 0, "current_step": None, "errors": [],
        "created_at": now, "updated_at": now, "completed_at": None, "metadata": {},
        "artifact_urls": {}, "payload_storage_url": "", "studio_job_id": "",
        "validation_report_url": "",
    }
    log.info("[%s] Job created. tenant=%s client=%s file=%s", job_id, tenant_cfg.tenant_id, actual_client_id, file.filename)

    metadata_hints = {
        "purpose": purpose, "document_type": document_type, "visibility": visibility,
        "course_name": course_name, "course_id": course_id, "block": block, "day": day,
        "chapter": chapter, "module_name": module_name, "learning_objective": learning_objective,
    }

    # Product behavior: Source Library must show the file immediately after upload.
    # The deeper agent pipeline can still run in the background, but CAS list/view
    # should not wait for embeddings/RDS/OpenSearch or LLM extraction.
    try:
        immediate_payload = _build_source_library_payload(
            tenant_cfg=tenant_cfg, client_id=actual_client_id, namespace=namespace,
            job_id=job_id, user_id=user_id, filename=file.filename or "unnamed",
            content=content, content_type=file.content_type or "application/octet-stream",
            raw_storage_url=storage_url, s3_key=s3_key, source_relative_path=safe_rel_path,
            source_root=source_root, metadata_hints=metadata_hints,
        )
        payload_url = _write_source_library_payload(
            tenant_cfg=tenant_cfg, namespace=namespace, job_id=job_id, payload=immediate_payload
        )
        _jobs[job_id]["payload_storage_url"] = payload_url
        _jobs[job_id].setdefault("artifact_urls", {})["studio_payload"] = payload_url
        _jobs[job_id]["metadata"] = {"doc_type": immediate_payload["metadata"].get("document_type"), "source_library_ready": True}
    except Exception as exc:
        log.exception("[%s] Immediate Source Library payload failed: %s", job_id, exc)
        raise HTTPException(500, f"Source Library payload creation failed: {exc}")

    background_tasks.add_task(
        _process, job_id=job_id, tenant_cfg=tenant_cfg,
        client_id=actual_client_id, user_id=user_id, namespace=namespace,
        filename=file.filename or "unnamed", s3_key=s3_key, content=content, raw_storage_url=storage_url,
        source_relative_path=safe_rel_path, source_root=source_root, metadata_hints=metadata_hints,
    )

    return UploadResponse(
        job_id=job_id, tenant_id=tenant_cfg.tenant_id, client_id=actual_client_id,
        namespace=namespace, s3_key=s3_key, upload_url=presigned,
        status=JobStatus.PENDING, created_at=now,
    )


@router.post("/batch", status_code=202)
async def batch_upload(
    request: Request, background_tasks: BackgroundTasks,
    _=Depends(require_role("client_admin")),
    files: List[UploadFile] = File(...), client_id: str = Form(""),
    course_id: str = Form(""), course_name: str = Form(""),
):
    tenant_cfg: TenantConfig = get_current_tenant(request)
    metadata_hints = {"course_id": course_id, "course_name": course_name}
    tenant_cfg, actual_client_id = _resolve_tenant_and_client_for_ingestion(request, tenant_cfg, client_id)
    client_cfg = tenant_cfg.get_client(actual_client_id)
    if not client_cfg:
        raise HTTPException(404, f"Client '{actual_client_id}' is not configured for tenant '{tenant_cfg.tenant_id}'")
    max_files = tenant_cfg.ingestion.max_concurrent_files
    if len(files) > max_files:
        raise HTTPException(429, f"Batch size {len(files)} exceeds max {max_files}")

    results = []
    for f in files:
        content = await f.read()
        validator = ValidationService(tenant_cfg, client_cfg)
        v = await validator.validate(f.filename or "unnamed", content, f.content_type or "")
        if v.passed:
            # Duplicate detection is handled by DeduplicationAgent using client dedup_manifest.json.
            job_id = str(uuid.uuid4())
            namespace = tenant_cfg.get_namespace(actual_client_id)
            env_name = get_settings().environment or "development"
            s3_key = f"raw/{namespace}/{env_name}/{job_id}/{f.filename}"
            provider = get_storage_provider(tenant_cfg)
            storage_url = await provider.upload(s3_key, content, f.content_type or "application/octet-stream")
            now = datetime.utcnow()
            _jobs[job_id] = {
                "job_id": job_id, "tenant_id": tenant_cfg.tenant_id, "client_id": actual_client_id,
                "user_id": getattr(request.state, "user_id", "batch"), "namespace": namespace,
                "filename": f.filename, "s3_key": s3_key, "status": JobStatus.PENDING,
                "progress_pct": 0, "current_step": None, "errors": [],
                "created_at": now, "updated_at": now, "completed_at": None, "metadata": {},
                "artifact_urls": {}, "payload_storage_url": "", "studio_job_id": "",
                "validation_report_url": "",
            }
            # Make batch-uploaded files visible in Source Library immediately.
            try:
                immediate_payload = _build_source_library_payload(
                    tenant_cfg=tenant_cfg, client_id=actual_client_id, namespace=namespace,
                    job_id=job_id, user_id=getattr(request.state, "user_id", "batch"),
                    filename=f.filename or "unnamed", content=content,
                    content_type=f.content_type or "application/octet-stream", raw_storage_url=storage_url,
                    s3_key=s3_key, metadata_hints=metadata_hints,
                )
                payload_url = _write_source_library_payload(
                    tenant_cfg=tenant_cfg, namespace=namespace, job_id=job_id, payload=immediate_payload
                )
                _jobs[job_id]["payload_storage_url"] = payload_url
                _jobs[job_id].setdefault("artifact_urls", {})["studio_payload"] = payload_url
            except Exception as exc:
                log.exception("[%s] Immediate Source Library payload failed for batch upload: %s", job_id, exc)
                results.append({"filename": f.filename, "status": "rejected", "errors": [str(exc)]})
                continue
            background_tasks.add_task(
                _process, job_id=job_id, tenant_cfg=tenant_cfg, client_id=actual_client_id,
                user_id=getattr(request.state, "user_id", "batch"), namespace=namespace,
                filename=f.filename or "unnamed", s3_key=s3_key, content=content, raw_storage_url=storage_url,
                metadata_hints=metadata_hints,
            )
            results.append({"filename": f.filename, "job_id": job_id, "status": "accepted"})
        else:
            results.append({"filename": f.filename, "status": "rejected", "errors": v.errors})

    accepted = sum(1 for r in results if r["status"] == "accepted")
    return {"total": len(files), "accepted": accepted, "results": results}


@router.post("/folder-scan", status_code=202)
async def folder_scan(request: Request, body: FolderScanRequest, background_tasks: BackgroundTasks, _=Depends(require_role("client_admin"))):
    """Scan a server-side folder recursively and ingest all supported files.

    v7 behavior:
    - dry_run=true returns discovered files only.
    - dry_run=false creates a scan_id immediately, writes an initial scan report,
      and processes files in the background.
    - processing.mode=local_parallel uses asyncio concurrency inside the API process.
      This is good for local/EC2 testing. For production at higher scale, use SQS worker mode.
    """
    tenant_cfg: TenantConfig = get_current_tenant(request)
    user_id: str = getattr(request.state, "user_id", "folder_scan")
    tenant_cfg, actual_client_id = _resolve_tenant_and_client_for_ingestion(request, tenant_cfg, body.client_id)
    client_cfg = tenant_cfg.get_client(actual_client_id)
    if not client_cfg:
        raise HTTPException(404, f"Client '{actual_client_id}' is not configured for tenant '{tenant_cfg.tenant_id}'")
    namespace = tenant_cfg.get_namespace(actual_client_id)

    root = Path(body.folder_path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise HTTPException(400, f"Folder path does not exist or is not a directory: {root}")

    iterator = root.rglob("*") if body.recursive else root.glob("*")
    files = [p for p in iterator if _allowed_file(p, body.include_extensions)]
    files.sort(key=lambda x: x.as_posix().lower())

    configured_workers = getattr(getattr(tenant_cfg, "processing", None), "max_workers", 5) or 5
    max_workers = body.max_workers or configured_workers
    max_workers = max(1, min(int(max_workers), 50))
    result_limit = body.return_results_limit or getattr(getattr(tenant_cfg, "processing", None), "folder_scan_return_results_limit", 200) or 200

    if body.dry_run:
        return {
            "status": "dry_run",
            "folder_path": str(root),
            "recursive": body.recursive,
            "skip_duplicates": body.skip_duplicates,
            "processing_mode": getattr(getattr(tenant_cfg, "processing", None), "mode", "local_parallel"),
            "max_workers": max_workers,
            "files_discovered": len(files),
            "files": [p.relative_to(root).as_posix() for p in files[: min(result_limit, 500)]],
            "truncated": len(files) > min(result_limit, 500),
        }

    scan_id = str(uuid.uuid4())
    env_name = get_settings().environment or "development"
    report_key = f"processed/{namespace}/{env_name}/folder_scans/{scan_id}/validation/report.json"
    initial_report = {
        "scan_id": scan_id,
        "tenant_id": tenant_cfg.tenant_id,
        "client_id": actual_client_id,
        "folder_path": str(root),
        "recursive": body.recursive,
        "skip_duplicates": body.skip_duplicates,
        "processing_mode": getattr(getattr(tenant_cfg, "processing", None), "mode", "local_parallel"),
        "max_workers": max_workers,
        "files_discovered": len(files),
        "queued": len(files),
        "accepted": 0,
        "completed": 0,
        "processing": 0,
        "skipped_duplicates": 0,
        "rejected": 0,
        "failed": 0,
        "status": "queued",
        "valid": None,
        "warnings": [],
        "results": [],
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }
    report_url = ArtifactWriter(tenant_cfg).write_json(report_key, initial_report)
    _scans[scan_id] = {**initial_report, "validation_report_url": report_url}

    # CAS-supplied course scope, so folder-scanned documents get isolated to the
    # course they were scanned for, same as single-file /ingest/upload.
    metadata_hints = {"course_id": body.course_id, "course_name": body.course_name}

    background_tasks.add_task(
        _process_folder_scan_parallel,
        scan_id=scan_id,
        tenant_cfg=tenant_cfg,
        client_id=actual_client_id,
        user_id=user_id,
        namespace=namespace,
        root=root,
        files=files,
        skip_duplicates=body.skip_duplicates,
        max_workers=max_workers,
        report_key=report_key,
        report_url=report_url,
        metadata_hints=metadata_hints,
    )

    return {
        "status": "queued",
        "scan_id": scan_id,
        "folder_path": str(root),
        "recursive": body.recursive,
        "files_discovered": len(files),
        "queued": len(files),
        "max_workers": max_workers,
        "message": "Folder scan accepted. Processing continues in background. Use GET /v1/ingest/folder-scans/{scan_id} to monitor.",
        "validation_report_url": report_url,
    }


async def _process_folder_scan_parallel(
    *, scan_id: str, tenant_cfg: TenantConfig, client_id: str, user_id: str, namespace: str,
    root: Path, files: List[Path], skip_duplicates: bool, max_workers: int, report_key: str, report_url: str,
    metadata_hints: Optional[Dict[str, Any]] = None,
):
    """Process a folder scan with bounded local concurrency.

    This is v7 local_parallel mode. It is intentionally storage/provider-agnostic:
    raw and processed artifacts still go to S3/local/Azure/GCP based on tenant config.
    """
    writer = ArtifactWriter(tenant_cfg)
    sem = asyncio.Semaphore(max_workers)
    lock = asyncio.Lock()
    started_at = datetime.utcnow()
    _scans[scan_id].update({"status": "processing", "started_at": started_at.isoformat(), "updated_at": started_at.isoformat()})

    async def persist():
        # Persist a compact live report without making response huge.
        report = dict(_scans[scan_id])
        report.pop("validation_report_url", None)
        writer.write_json(report_key, report)

    async def process_one(path: Path) -> Dict[str, Any]:
        async with sem:
            rel = path.relative_to(root).as_posix()
            rel_with_root = f"{root.name}/{rel}"
            async with lock:
                _scans[scan_id]["processing"] += 1
                _scans[scan_id]["updated_at"] = datetime.utcnow().isoformat()

            result: Dict[str, Any]
            try:
                content = await asyncio.to_thread(path.read_bytes)
                job_id = await _create_job_record_and_upload(
                    tenant_cfg=tenant_cfg,
                    client_id=client_id,
                    user_id=user_id,
                    namespace=namespace,
                    filename=path.name,
                    content=content,
                    content_type=_mime_for_filename(path.name),
                    source_relative_path=rel_with_root,
                    source_root=str(root),
                    skip_duplicate=skip_duplicates,
                    metadata_hints=metadata_hints,
                )
                await _process(
                    job_id=job_id, tenant_cfg=tenant_cfg, client_id=client_id, user_id=user_id,
                    namespace=namespace, filename=path.name, s3_key=_jobs[job_id]["s3_key"],
                    content=content, raw_storage_url=_jobs[job_id].get("storage_url", ""),
                    source_relative_path=rel_with_root, source_root=str(root),
                    metadata_hints=metadata_hints,
                )
                status = _jobs.get(job_id, {}).get("status")
                if status == JobStatus.COMPLETED:
                    result_status = "completed"
                elif status == JobStatus.DUPLICATE:
                    result_status = "skipped_duplicate"
                elif status == JobStatus.FAILED:
                    result_status = "failed"
                else:
                    result_status = str(status)
                result = {"file": rel_with_root, "job_id": job_id, "status": result_status}
            except HTTPException as exc:
                detail = exc.detail
                if exc.status_code == 409 and isinstance(detail, dict) and detail.get("status") == "skipped_duplicate":
                    result = {
                        "file": rel_with_root,
                        "status": "skipped_duplicate",
                        "reason": detail.get("reason", "Duplicate file detected"),
                        "sha256": detail.get("sha256", ""),
                    }
                else:
                    result = {"file": rel_with_root, "status": "rejected", "error": detail}
            except Exception as exc:
                log.exception("[%s] Folder scan file failed: %s", scan_id, path)
                result = {"file": rel_with_root, "status": "failed", "error": str(exc)}

            async with lock:
                _scans[scan_id]["processing"] = max(0, _scans[scan_id]["processing"] - 1)
                _scans[scan_id]["results"].append(result)
                st = result.get("status")
                if st == "completed":
                    _scans[scan_id]["accepted"] += 1
                    _scans[scan_id]["completed"] += 1
                elif st == "skipped_duplicate":
                    _scans[scan_id]["skipped_duplicates"] += 1
                elif st == "rejected":
                    _scans[scan_id]["rejected"] += 1
                else:
                    _scans[scan_id]["failed"] += 1
                _scans[scan_id]["updated_at"] = datetime.utcnow().isoformat()
            return result

    # Kick off all tasks with semaphore-limited concurrency.
    await asyncio.gather(*(process_one(p) for p in files))

    finished_at = datetime.utcnow()
    _scans[scan_id].update({
        "status": "completed",
        "completed_at": finished_at.isoformat(),
        "updated_at": finished_at.isoformat(),
        "valid": _scans[scan_id]["rejected"] == 0 and _scans[scan_id]["failed"] == 0,
        "warnings": ([f"{_scans[scan_id]['skipped_duplicates']} duplicate file(s) skipped"] if _scans[scan_id]["skipped_duplicates"] else []),
        "validation_report_url": report_url,
    })
    await persist()


async def _create_job_record_and_upload(
    *, tenant_cfg: TenantConfig, client_id: str, user_id: str, namespace: str, filename: str,
    content: bytes, content_type: str, source_relative_path: str, source_root: str, skip_duplicate: bool,
    metadata_hints: Optional[Dict[str, Any]] = None,
) -> str:
    client_cfg = tenant_cfg.get_client(client_id)
    if not client_cfg:
        raise HTTPException(404, f"Client '{client_id}' is not configured for tenant '{tenant_cfg.tenant_id}'")
    if not content:
        raise HTTPException(400, "Empty file")

    validator = ValidationService(tenant_cfg, client_cfg)
    result = await validator.validate(filename or "unnamed", content, content_type or "")
    if not result.passed:
        raise HTTPException(422, {"errors": result.errors, "warnings": result.warnings})
    # Duplicate detection is handled by DeduplicationAgent using client dedup_manifest.json.

    # Do not block file upload using LLM token quota.
    # Token quota is checked only immediately before actual LLM calls inside the pipeline.
    # Large PDFs/DOCX files can be several MB; estimating tokens from raw bytes causes false 429 errors.

    job_id = str(uuid.uuid4())
    env_name = get_settings().environment or "development"
    safe_rel_path = _safe_relative_path(source_relative_path, filename or "unnamed")
    s3_key = f"raw/{namespace}/{env_name}/{job_id}/{safe_rel_path}"
    provider = get_storage_provider(tenant_cfg)
    try:
        storage_url = await provider.upload(s3_key, content, content_type or "application/octet-stream")
    except Exception as exc:
        log.error("[%s] Storage upload failed: %s", job_id, exc)
        raise HTTPException(500, f"Storage upload failed: {exc}")

    now = datetime.utcnow()
    _jobs[job_id] = {
        "job_id": job_id, "tenant_id": tenant_cfg.tenant_id, "client_id": client_id,
        "user_id": user_id, "namespace": namespace, "filename": filename,
        "source_relative_path": safe_rel_path, "source_root": source_root,
        "s3_key": s3_key, "storage_url": storage_url, "status": JobStatus.PENDING,
        "progress_pct": 0, "current_step": None, "errors": [],
        "created_at": now, "updated_at": now, "completed_at": None, "metadata": {},
        "artifact_urls": {}, "payload_storage_url": "", "studio_job_id": "",
        "validation_report_url": "",
    }
    try:
        immediate_payload = _build_source_library_payload(
            tenant_cfg=tenant_cfg, client_id=client_id, namespace=namespace,
            job_id=job_id, user_id=user_id, filename=filename or "unnamed",
            content=content, content_type=content_type or "application/octet-stream",
            raw_storage_url=storage_url, s3_key=s3_key, source_relative_path=safe_rel_path,
            source_root=source_root, metadata_hints=metadata_hints or {},
        )
        payload_url = _write_source_library_payload(
            tenant_cfg=tenant_cfg, namespace=namespace, job_id=job_id, payload=immediate_payload
        )
        _jobs[job_id]["payload_storage_url"] = payload_url
        _jobs[job_id].setdefault("artifact_urls", {})["studio_payload"] = payload_url
        _jobs[job_id]["metadata"] = {"doc_type": immediate_payload["metadata"].get("document_type"), "source_library_ready": True}
    except Exception as exc:
        log.exception("[%s] Immediate Source Library payload failed: %s", job_id, exc)
        raise HTTPException(500, f"Source Library payload creation failed: {exc}")
    return job_id


@router.get("/folder-scans/{scan_id}")
async def get_folder_scan(scan_id: str, request: Request, _=Depends(require_role("client_admin"))):
    tenant_cfg: TenantConfig = get_current_tenant(request)
    scan = _scans.get(scan_id)
    if not scan or scan.get("tenant_id") != tenant_cfg.tenant_id:
        raise HTTPException(404, "Folder scan not found")
    return scan


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job(job_id: str, request: Request):
    tenant_cfg: TenantConfig = get_current_tenant(request)
    job = _jobs.get(job_id)
    if not job or job["tenant_id"] != tenant_cfg.tenant_id:
        raise HTTPException(404, "Job not found")
    return JobStatusResponse(**job)


@router.get("/jobs")
async def list_jobs(request: Request, limit: int = 20, job_status: str = ""):
    tenant_cfg: TenantConfig = get_current_tenant(request)
    user_id = getattr(request.state, "user_id", "")
    role = getattr(request.state, "role", "user")
    jobs = [j for j in _jobs.values() if j["tenant_id"] == tenant_cfg.tenant_id
            and (not job_status or j["status"] == job_status)
            and (role in ("super_admin", "client_admin") or j.get("user_id") == user_id)]
    return {"jobs": jobs[:limit], "total": len(jobs)}



@router.get("/jobs/{job_id}/payload")
async def get_job_payload(job_id: str, request: Request, _=Depends(require_role("client_admin"))):
    tenant_cfg: TenantConfig = get_current_tenant(request)
    job = _jobs.get(job_id)
    if not job or job["tenant_id"] != tenant_cfg.tenant_id:
        raise HTTPException(404, "Job not found")
    key = f"processed/{job['namespace']}/{get_settings().environment}/{job_id}/studio_payload/payload.json"
    try:
        payload = ArtifactWriter(tenant_cfg).read_json(key)
    except Exception as exc:
        raise HTTPException(404, f"Payload not found yet: {exc}")
    return {"job_id": job_id, "payload_storage_url": job.get("payload_storage_url", ""), "studio_payload": payload}


@router.get("/jobs/{job_id}/validation-report")
async def get_validation_report(job_id: str, request: Request, _=Depends(require_role("client_admin"))):
    tenant_cfg: TenantConfig = get_current_tenant(request)
    job = _jobs.get(job_id)
    if not job or job["tenant_id"] != tenant_cfg.tenant_id:
        raise HTTPException(404, "Job not found")
    key = f"processed/{job['namespace']}/{get_settings().environment}/{job_id}/validation/report.json"
    try:
        report = ArtifactWriter(tenant_cfg).read_json(key)
    except Exception as exc:
        raise HTTPException(404, f"Validation report not found yet: {exc}")
    return {"job_id": job_id, "validation_report_url": job.get("validation_report_url", ""), "validation_report": report}


@router.post("/jobs/{job_id}/index", status_code=202)
async def index_job(job_id: str, request: Request, _=Depends(require_role("client_admin"))):
    """Manual RDS/OpenSearch indexing for an already processed job.

    This reads S3/local artifacts and runs only the indexing steps. Useful when
    database/opensearch were disabled during initial extraction or when indexing
    needs retry without re-uploading/re-extracting the file.
    """
    tenant_cfg: TenantConfig = get_current_tenant(request)
    job = _jobs.get(job_id)
    if not job or job["tenant_id"] != tenant_cfg.tenant_id:
        raise HTTPException(404, "Job not found")
    writer = ArtifactWriter(tenant_cfg)
    env = get_settings().environment
    base = f"processed/{job['namespace']}/{env}/{job_id}"
    try:
        payload = writer.read_json(f"{base}/studio_payload/payload.json")
        content_units = writer.read_json(f"{base}/extracted/content_units.json")
        metadata = writer.read_json(f"{base}/extracted/metadata.json")
    except Exception as exc:
        raise HTTPException(404, f"Required artifacts not found: {exc}")

    def try_read(name, default):
        try:
            return writer.read_json(f"{base}/extracted/{name}")
        except Exception:
            return default

    state = {
        "job_id": job_id,
        "tenant_id": job["tenant_id"],
        "client_id": job["client_id"],
        "namespace": job["namespace"],
        "filename": job.get("filename", ""),
        "file_type": payload.get("source_file", {}).get("type", ""),
        "source_relative_path": payload.get("source_file", {}).get("relative_path", ""),
        "raw_storage_url": payload.get("source_file", {}).get("raw_url", ""),
        "doc_type": metadata.get("doc_type", payload.get("metadata", {}).get("doc_type", "")),
        "doc_metadata": metadata,
        "content_units": content_units if isinstance(content_units, list) else [],
        "calendar_structure": try_read("calendar_structure.json", {}),
        "syllabus_structure": try_read("syllabus_structure.json", {}),
        "quiz_structure": try_read("quiz_structure.json", {}),
        "project_structure": try_read("project_structure.json", {}),
        "artifact_urls": job.get("artifact_urls", {}),
    }
    rds_result = do_rds_upsert(tenant_cfg, state)
    embed_result = generate_embeddings(tenant_cfg, state)
    os_result = do_opensearch_upsert(tenant_cfg, state)
    index_result = {
        "job_id": job_id,
        "rds_upsert": rds_result,
        "embedding_generation": embed_result,
        "opensearch_upsert": os_result,
        "created_at": datetime.utcnow().isoformat(),
    }
    index_url = writer.write_json(f"{base}/indexing/index_result.json", index_result)
    job.setdefault("metadata", {})["indexing"] = index_result
    job.setdefault("artifact_urls", {})["index_result"] = index_url
    return {"job_id": job_id, "index_result_url": index_url, "indexing": index_result}


@router.get("/jobs/{job_id}/index-status")
async def get_index_status(job_id: str, request: Request, _=Depends(require_role("client_admin"))):
    tenant_cfg: TenantConfig = get_current_tenant(request)
    job = _jobs.get(job_id)
    if not job or job["tenant_id"] != tenant_cfg.tenant_id:
        raise HTTPException(404, "Job not found")
    return {
        "job_id": job_id,
        "indexing": job.get("metadata", {}).get("indexing", {}),
        "index_result_url": job.get("artifact_urls", {}).get("index_result", ""),
    }


async def _process(job_id, tenant_cfg, client_id, user_id, namespace, filename, s3_key, content, raw_storage_url="", source_relative_path="", source_root="", metadata_hints=None):
    _jobs[job_id]["status"] = JobStatus.PROCESSING
    _jobs[job_id]["updated_at"] = datetime.utcnow()
    try:
        if not content:
            provider = get_storage_provider(tenant_cfg)
            content = await provider.download(s3_key)
        result = await run_pipeline(
            tenant_cfg=tenant_cfg, job_id=job_id,
            tenant_id=tenant_cfg.tenant_id, client_id=client_id,
            user_id=user_id, namespace=namespace,
            filename=filename, s3_key=s3_key, raw_bytes=content, raw_storage_url=raw_storage_url or _jobs[job_id].get("storage_url", ""),
            source_relative_path=source_relative_path or _jobs[job_id].get("source_relative_path", filename), source_root=source_root or _jobs[job_id].get("source_root", ""),
            metadata_hints=metadata_hints or {},
        )
        fatal_error = result.get("fatal_error") or ""
        if result.get("is_duplicate") or result.get("final_status") == "duplicate":
            final_status = JobStatus.DUPLICATE
        else:
            final_status = JobStatus.FAILED if fatal_error else JobStatus.COMPLETED
        _jobs[job_id].update({
            "status": final_status, "progress_pct": 100,
            "completed_at": datetime.utcnow(),
            "error_message": fatal_error or None,
            "metadata": {
                "steps_completed": len(result.get("completed_steps", [])),
                "storage_targets": result.get("storage_targets", []),
                "doc_type": result.get("doc_type", ""),
                "classification": result.get("classification", ""),
                "chunk_count": len(result.get("chunks", [])),
                "errors": result.get("errors", []),
                "failed_step": result.get("failed_step", ""),
                "is_duplicate": result.get("is_duplicate", False),
                "duplicate_reason": result.get("duplicate_reason", ""),
                "duplicate_of_job_id": result.get("duplicate_of_job_id", ""),
                "dedup_result": result.get("dedup_result", {}),
                "artifact_urls": result.get("artifact_urls", {}),
                "payload_storage_url": result.get("artifact_urls", {}).get("studio_payload", ""),
                "validation_report_url": result.get("artifact_urls", {}).get("validation_report", ""),
                "content_units": len(result.get("content_units", [])),
                "structure_store_upsert": result.get("structure_store_upsert_result", {}),
                "embedding_generation": result.get("embedding_generation_result", {}),
                "vector_store_upsert": result.get("vector_store_upsert_result", {}),
            },
            "artifact_urls": result.get("artifact_urls", {}),
            "payload_storage_url": result.get("artifact_urls", {}).get("studio_payload", ""),
            "validation_report_url": result.get("artifact_urls", {}).get("validation_report", ""),
        })
        # v11: no automatic push-to-studio. Content AI Studio retrieves context
        # from DIS using /context/sources and /context/retrieve after ingestion completes.
    except Exception as exc:
        log.exception("[%s] Pipeline failed: %s", job_id, exc)
        _jobs[job_id]["status"] = JobStatus.FAILED
        _jobs[job_id]["error_message"] = str(exc)
        _jobs[job_id]["updated_at"] = datetime.utcnow()
