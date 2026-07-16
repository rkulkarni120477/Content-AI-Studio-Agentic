"""Studio context retrieval service.

This is not a public semantic search API. It is used by Content AI Studio to
retrieve relevant DIS-extracted content units for dynamic blueprint/prompt JSON.
"""
from __future__ import annotations
import math
import re
import uuid
from typing import Any, Dict, Iterable, List, Tuple

from config.settings import TenantConfig, get_settings
from services.artifacts import ArtifactWriter
from services.source_library import read_source_index, source_filter_options as compact_filter_options
from services.generated_documents import GeneratedDocumentService

_SKIP_KEYS = {"id", "created_at", "updated_at", "request_id", "prompt_id", "prompt_version"}


def flatten_text(obj: Any) -> List[str]:
    out: List[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in _SKIP_KEYS:
                continue
            out.extend(flatten_text(v))
    elif isinstance(obj, list):
        for item in obj:
            out.extend(flatten_text(item))
    elif isinstance(obj, str):
        s = obj.strip()
        if s:
            out.append(s)
    return out


def terms(text: str) -> List[str]:
    toks = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", text.lower())
    stop = {"the","and","for","with","this","that","from","into","using","course","lesson","module","generate","generation"}
    return [t for t in toks if t not in stop]


def estimate_tokens(text: str) -> int:
    return max(1, int(len(text.split()) * 1.35))


def clean_reading_text(text: Any) -> str:
    """Normalize extracted text for human preview and prompt context.

    The Source Library should show readable content only. Metadata remains
    available internally but is not mixed into the reading preview.
    """
    raw = str(text or '').replace('\r\n', '\n').replace('\r', '\n')
    lines = [re.sub(r'\s+', ' ', line).strip() for line in raw.split('\n')]
    paragraphs: List[str] = []
    buf: List[str] = []
    for line in lines:
        if not line:
            if buf:
                paragraphs.append(' '.join(buf).strip())
                buf = []
            continue
        # Keep obvious headings and bullets as separate readable lines.
        if line.startswith(('## ', '# ', '- ', '* ', '• ')) or re.match(r'^[A-Z][A-Za-z0-9 &/:-]{0,80}$', line):
            if buf:
                paragraphs.append(' '.join(buf).strip())
                buf = []
            paragraphs.append(line.lstrip('# ').strip())
        else:
            buf.append(line)
    if buf:
        paragraphs.append(' '.join(buf).strip())
    return '\n'.join(p for p in paragraphs if p).strip()


def payload_reading_content(payload: Dict[str, Any]) -> str:
    units = payload.get('content_units') or []
    texts = []
    for unit in units:
        text = clean_reading_text(unit.get('text') or '')
        if text:
            texts.append(text)
    return '\n\n'.join(texts).strip()


class ContextRetrievalService:
    def __init__(self, tenant_cfg: TenantConfig, role: str = "user"):
        self.tenant_cfg = tenant_cfg
        self.role = role
        self.writer = ArtifactWriter(tenant_cfg)
        self.settings = get_settings()

    def _base_prefix(self, client_id: str) -> str:
        namespace = self.tenant_cfg.get_namespace(client_id)
        return f"processed/{namespace}/{self.settings.environment}"

    def _source_ui_config(self) -> Dict[str, Any]:
        # Priority: retrieval.source_ui, then client_rules.source_ui, then safe generic defaults.
        ui = dict(getattr(self.tenant_cfg.retrieval, "source_ui", {}) or {})
        if not ui:
            ui = dict((self.tenant_cfg.client_rules or {}).get("source_ui", {}) or {})
        if not ui:
            ui = {
                "taxonomy_filters": [
                    {"key": "course_name", "label": "Course", "type": "select"},
                    {"key": "module_name", "label": "Module", "type": "select"},
                    {"key": "lesson_name", "label": "Lesson", "type": "select"},
                ],
                "common_filters": ["purpose", "document_type", "visibility", "status", "search"],
            }
        return ui

    def _purpose_mapping(self) -> Dict[str, List[str]]:
        mapping = dict(getattr(self.tenant_cfg.retrieval, "source_type_mapping", {}) or {})
        if not mapping:
            mapping = dict((self.tenant_cfg.client_rules or {}).get("source_type_mapping", {}) or {})
        if not mapping:
            mapping = {
                "style": ["style_guide", "authoring_guide", "authoring_guidelines", "sample_lesson", "approved_template"],
                "cdd": ["syllabus", "course_outline", "course_calendar", "program_overview", "learning_objectives"],
                "blueprint": ["course_calendar", "syllabus", "chapter_outline", "module_map", "block_schedule"],
                "course_generation": ["lesson_plan", "lesson_slide_deck", "slide_deck", "project_activity", "quiz_exam", "student_handout", "textbook_chapter", "study_questions", "ebook_reference"],
            }
        return mapping

    def ui_config(self, client_id: str) -> Dict[str, Any]:
        return {
            "tenant_id": self.tenant_cfg.tenant_id,
            "client_id": client_id,
            "source_library": self._source_ui_config(),
            "purpose_labels": getattr(self.tenant_cfg.retrieval, "purpose_labels", {}) or (self.tenant_cfg.client_rules or {}).get("purpose_labels", {}) or {
                "style": "Style Reference Documents",
                "cdd": "Course Design Sources",
                "blueprint": "Blueprint Sources",
                "course_generation": "Lesson Source Materials",
            },
            "source_type_mapping": self._purpose_mapping(),
        }

    def _normalize_doc_type(self, payload: Dict[str, Any]) -> str:
        meta = payload.get("metadata", {}) or {}
        return str(meta.get("document_type") or meta.get("doc_type") or meta.get("content_type") or payload.get("source_file", {}).get("type") or "other")

    def _metadata_value(self, payload: Dict[str, Any], key: str) -> Any:
        meta = payload.get("metadata", {}) or {}
        if key in meta:
            return meta.get(key)
        aliases = {
            "course": ["course_name", "course_id", "title", "product_title"],
            "course_name": ["course_name", "course_id", "title", "product_title"],
            "block": ["block", "block_name", "block_id", "block_number"],
            "day": ["day", "day_id", "day_number", "mapped_day"],
            "chapter": ["chapter", "chapter_number", "chapter_title"],
            "module": ["module", "module_name", "module_id", "section_title"],
            "learning_objective": ["learning_objective", "learning_objective_id", "learning_objective_text"],
        }
        for alias in aliases.get(key, []):
            if alias in meta and meta.get(alias):
                return meta.get(alias)
        return ""

    def _purposes_for_payload(self, payload: Dict[str, Any]) -> List[str]:
        meta = payload.get("metadata", {}) or {}
        explicit = []
        for purpose in ("style", "cdd", "blueprint", "course_generation", "general_reference"):
            if bool(meta.get(f"use_for_{purpose}")) or meta.get("purpose") == purpose:
                explicit.append(purpose)
        doc_type = self._normalize_doc_type(payload)
        mapping = self._purpose_mapping()
        inferred = [p for p, types in mapping.items() if doc_type in set(types or [])]
        return sorted(set(explicit + inferred)) or ["general_reference"]

    def _restricted_doc_types(self) -> set:
        """Doc types that must never enter generation context (answer keys, guides).

        Config-driven (client document_processing.restricted_document_types) plus
        built-in answer-key/guide families so protection never depends on a
        client's config list being complete.
        """
        types: set = set()
        dp = getattr(self.tenant_cfg, "document_processing", None)
        for t in (getattr(dp, "restricted_document_types", None) or []):
            types.add(str(t).strip().lower())
        types |= {
            "quiz_answer_key", "final_exam_answer_key", "exam_answer_key", "answer_key",
            "project_key", "project_instructor_guide", "instructor_guide",
        }
        return types

    def _restricted_name_markers(self) -> list:
        """Filename substrings that mark restricted content (answer keys, guides)."""
        markers = ["answer key", "instructor guide", "exam key", "quiz key"]
        for block in (self.tenant_cfg.client_rules or {}).values():
            if isinstance(block, dict):
                for m in (block.get("restricted_filename_contains") or []):
                    markers.append(str(m).strip().lower())
        return list(dict.fromkeys(m for m in markers if m))

    def _is_restricted_source(self, document_type: Any, source_file_name: Any) -> bool:
        """Detect answer keys / instructor guides by doc type or filename."""
        dt = str(document_type or "").strip().lower()
        if dt and dt in self._restricted_doc_types():
            return True
        name = str(source_file_name or "").strip().lower()
        if name and any(m in name for m in self._restricted_name_markers()):
            return True
        return False

    def _is_hard_restricted(self, meta_all: Dict[str, Any]) -> bool:
        """True only for genuinely restricted content (answer keys, instructor
        guides, admin/internal-only) that must never seed generated content.

        Instructor / content-team-authored SOURCE documents are NOT hard-restricted:
        they are legitimate generation material (Cengage manuscripts, AIM calendar).
        Answer keys are flagged restricted in _iter_payloads, so they are caught
        here by the flag; the type/name check is a defensive backup.
        """
        if bool(meta_all.get("restricted")):
            return True
        if str(meta_all.get("access_level") or "").strip().lower() == "admin_only":
            return True
        if str(meta_all.get("visibility") or "").strip().lower() in {"internal", "internal_only", "admin_only", "restricted_admin"}:
            return True
        if self._is_restricted_source(meta_all.get("document_type") or meta_all.get("doc_type"), meta_all.get("source_file_name")):
            return True
        return False

    def documents_library(self, client_id: str, purpose: str = "", filters: Dict[str, Any] | None = None, limit: int = 200, offset: int = 0) -> Dict[str, Any]:
        filters = filters or {}
        if purpose:
            filters["purpose"] = purpose
        listed = self.list_sources(client_id, filters=filters, limit=limit, offset=offset)
        return {
            "tenant_id": listed["tenant_id"],
            "client_id": listed["client_id"],
            "total": listed["total"],
            "limit": listed["limit"],
            "offset": listed["offset"],
            "documents": listed["sources"],
            "filter_options": listed["filter_options"],
            "ui_config": self.ui_config(client_id),
        }

    def list_sources(self, client_id: str, filters: Dict[str, Any] | None = None, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """Return compact Source Library records from the persisted client index.

        Product rule: this endpoint must not return internal metadata, chunks,
        embedding/RDS fields, or full payload JSON. It returns only the fields
        CAS needs for the Source Library table and simple filters.
        """
        filters = filters or {}
        index = read_source_index(self.tenant_cfg, client_id)
        records = list(index.get("sources") or [])
        sources: List[Dict[str, Any]] = []
        for src in records:
            if self._source_matches(src, filters):
                # Explicit whitelist. Do not leak internal metadata into CAS.
                sources.append({
                    "document_id": src.get("document_id") or src.get("job_id"),
                    "job_id": src.get("job_id"),
                    "tenant_id": src.get("tenant_id"),
                    "client_id": src.get("client_id"),
                    "title": src.get("title"),
                    "source_file_name": src.get("source_file_name"),
                    "source_file_type": src.get("source_file_type"),
                    # UI hint only: whether the original file can be downloaded via
                    # /context/sources/{job_id}/download-url. We expose the boolean,
                    # never the raw S3 key (that stays internal per the whitelist rule).
                    "has_original": bool(src.get("raw_key")),
                    "document_type": src.get("document_type"),
                    "purpose": src.get("purpose"),
                    "visibility": src.get("visibility"),
                    "status": src.get("status"),
                    "size_bytes": src.get("size_bytes"),
                    "page_count": src.get("page_count"),
                    "total_units": src.get("total_units", 0),
                    "created_at": src.get("created_at"),
                    "updated_at": src.get("updated_at"),
                    "course_name": src.get("course_name", ""),
                    "block": src.get("block", ""),
                    "day": src.get("day", ""),
                    "chapter": src.get("chapter", ""),
                    "module_name": src.get("module_name", ""),
                    "learning_objective": src.get("learning_objective", ""),
                })
        sources.sort(key=lambda x: str(x.get("updated_at") or x.get("created_at") or ""), reverse=True)
        total = len(sources)
        limit = max(1, min(int(limit or 50), 500))
        offset = max(0, int(offset or 0))
        return {
            "tenant_id": self.tenant_cfg.tenant_id,
            "client_id": client_id,
            "total": total,
            "limit": limit,
            "offset": offset,
            "sources": sources[offset:offset+limit],
            "filter_options": compact_filter_options(records),
        }

    def _source_matches(self, src: Dict[str, Any], filters: Dict[str, Any]) -> bool:
        def eq(field: str, value: Any) -> bool:
            if value in (None, ""):
                return True
            if str(value).strip().lower() in {"all", "*", "any"}:
                return True
            return str(src.get(field) or "").lower() == str(value).lower()
        for field in ("document_type", "source_file_type", "block", "course_name", "day", "chapter", "module_name", "lesson_name", "visibility", "status"):
            if not eq(field, filters.get(field)):
                return False
        purpose = str(filters.get("purpose") or "").strip().lower()
        if purpose and purpose not in {"all", "*", "any"}:
            purpose_values = [str(p).lower() for p in src.get("purposes", [])]
            if src.get("purpose"):
                purpose_values.append(str(src.get("purpose")).lower())
            if purpose not in purpose_values:
                return False
        metadata_filters = filters.get("metadata_filters") or {}
        for k, v in metadata_filters.items():
            if v in (None, "", [], {}):
                continue
            if str(src.get(k) or (src.get("metadata") or {}).get(k) or "").lower() != str(v).lower():
                return False
        q = str(filters.get("search") or "").strip().lower()
        if q:
            hay = " ".join(str(src.get(k) or "") for k in ("source_file_name", "title", "document_type", "course_name", "block", "day", "chapter", "module_name", "lesson_name", "learning_objective", "purpose")).lower()
            if q not in hay:
                return False
        return True

    def source_filter_options(self, client_id: str) -> Dict[str, List[str]]:
        index = read_source_index(self.tenant_cfg, client_id)
        return compact_filter_options(list(index.get("sources") or []))

    def get_structure(self, client_id: str, job_id: str) -> Dict[str, Any]:
        """Return clean readable content for one source document.

        This endpoint intentionally hides DIS metadata and payload internals.
        """
        index = read_source_index(self.tenant_cfg, client_id)
        record = next((r for r in index.get("sources", []) if str(r.get("job_id")) == str(job_id)), None)
        if not record:
            # Backward compatibility for old payload-only uploads.
            payload = self._load_payload_by_job(client_id, job_id)
            units = payload.get("content_units", [])
            meta = payload.get("metadata", {}) or {}
            source = payload.get("source_file", {}) or {}
            reading_content = payload_reading_content(payload)
            return {
                "job_id": job_id,
                "document_title": meta.get("title") or source.get("name"),
                "source_file_name": source.get("name"),
                "document_type": self._normalize_doc_type(payload),
                "purpose": meta.get("purpose") or ", ".join(self._purposes_for_payload(payload)),
                "visibility": meta.get("visibility") or "instructor",
                "status": meta.get("status") or "processed",
                "total_units": len(units),
                "reading_content": reading_content,
                "preview": reading_content[:12000],
                "content_units": [{
                    "content_unit_id": u.get("content_unit_id"),
                    "unit_type": u.get("unit_type"),
                    "unit_number": u.get("unit_number"),
                    "title": u.get("title"),
                    "text": clean_reading_text(u.get("text") or ""),
                } for u in units],
            }
        content_key = record.get("content_key")
        if not content_key:
            raise FileNotFoundError("Source content key missing in source index")
        doc = self.writer.read_json(content_key)
        return {
            "job_id": job_id,
            "document_title": doc.get("document_title") or record.get("title"),
            "source_file_name": doc.get("source_file_name") or record.get("source_file_name"),
            "document_type": doc.get("document_type") or record.get("document_type"),
            "purpose": doc.get("purpose") or record.get("purpose"),
            "visibility": doc.get("visibility") or record.get("visibility"),
            "status": doc.get("status") or record.get("status"),
            "total_units": doc.get("total_units") or len(doc.get("content_units") or []),
            "reading_content": doc.get("reading_content") or "",
            "preview": doc.get("preview") or str(doc.get("reading_content") or "")[:12000],
            "content_units": doc.get("content_units") or [],
        }

    def resolve_source_raw_ref(self, client_id: str, job_id: str) -> Dict[str, Any]:
        """Resolve the original-uploaded-file reference for one source.

        Returns the logical S3 key (``raw_key``) plus filename/type/url so the
        API layer can mint a short-lived presigned download URL without ever
        exposing the raw key. Enforces the same restricted/visibility rules as
        retrieval, so a normal user cannot pull an instructor-only original.
        ``raw_key`` is empty for sources ingested before deep-link capture
        (re-ingest to populate it).
        """
        index = read_source_index(self.tenant_cfg, client_id)
        record = next((r for r in index.get("sources", []) if str(r.get("job_id")) == str(job_id)), None)
        if not record:
            raise FileNotFoundError(f"No source found for job_id '{job_id}'")
        visibility = str(record.get("visibility") or "").strip().lower()
        restricted = bool(record.get("restricted")) or visibility in {"instructor", "instructor_only", "internal", "internal_only", "restricted_admin", "admin_only"}
        if self.tenant_cfg.retrieval.restricted_content_filter and self.role == "user" and restricted:
            raise PermissionError("This source is restricted; a normal user cannot download the original file.")
        return {
            "raw_key": record.get("raw_key") or "",
            "raw_storage_url": record.get("raw_storage_url") or "",
            "source_file_name": record.get("source_file_name"),
            "source_file_type": record.get("source_file_type"),
        }

    def _load_payload_by_job(self, client_id: str, job_id: str) -> Dict[str, Any]:
        prefix = f"{self._base_prefix(client_id)}/{job_id}/studio_payload/payload.json"
        return self.writer.read_json(prefix)

    def _calendar_day_hints(self, client_id: str, filters: Dict[str, Any]) -> Dict[str, Any]:
        """Return day-level text hints from ingested calendar files.

        AIM uses calendar/syllabus as source of truth for quiz/project/study mapping.
        Some quiz/study filenames do not contain BxDy, so retrieval can use the
        requested calendar day text as a safe secondary matcher.
        """
        requested_day_id = str(filters.get("day_id") or filters.get("mapped_day") or "").strip()
        requested_day_number = filters.get("day_number")
        if not requested_day_number and requested_day_id:
            m = re.search(r"D(\d+)$", requested_day_id, re.I)
            requested_day_number = int(m.group(1)) if m else None
        if not requested_day_number:
            return {}
        block_id = str(filters.get("block_id") or "").strip().lower()
        hints: List[str] = []
        for payload in self._iter_payloads(client_id, {"content_types": ["course_calendar", "syllabus"], "include_restricted": True}):
            meta = payload.get("metadata", {}) or {}
            if block_id and str(meta.get("block_id") or meta.get("block") or "").strip().lower() not in {"", block_id, block_id.replace("b", "block ")}:
                # Keep permissive because old calendar metadata may not have block_id.
                pass
            cal = payload.get("calendar_structure") or {}
            for day in cal.get("days", []) or []:
                try:
                    day_no = int(day.get("day_number") or 0)
                except Exception:
                    day_no = 0
                if day_no == int(requested_day_number):
                    hints.extend([
                        str(day.get("lesson_title") or ""),
                        str(day.get("topic") or ""),
                        str(day.get("source_text") or ""),
                        " ".join(day.get("activities") or []),
                        " ".join(day.get("assignments") or []),
                        " ".join(day.get("assessments") or []),
                    ])
        hint_text = " ".join(h for h in hints if h).strip()
        return {"day_number": int(requested_day_number), "day_id": requested_day_id, "text": hint_text, "terms": terms(hint_text)} if hint_text else {}

    def _iter_payloads(self, client_id: str, filters: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
        """Yield retrieval-ready source documents.

        Prefer clean Source Library content files over full pipeline payloads.
        This keeps style/CDD/blueprint sources coherent and prevents CAS prompts
        from receiving internal DIS metadata.
        """
        job_ids = filters.get("job_ids") or filters.get("job_id") or filters.get("document_ids") or []
        if isinstance(job_ids, str):
            job_ids = [job_ids]
        index = read_source_index(self.tenant_cfg, client_id)
        records = list(index.get("sources") or [])
        if job_ids:
            wanted = {str(j) for j in job_ids}
            records = [r for r in records if str(r.get("job_id")) in wanted]
        if records:
            for rec in records:
                try:
                    doc = self.writer.read_json(rec.get("content_key"))
                    payload = {
                        "job_id": rec.get("job_id"),
                        "tenant_id": rec.get("tenant_id"),
                        "client_id": rec.get("client_id"),
                        "source_file": {
                            "name": rec.get("source_file_name"),
                            "type": rec.get("source_file_type"),
                            # Original-file references for source citation/deep-link.
                            "raw_url": rec.get("raw_storage_url"),
                            "raw_key": rec.get("raw_key"),
                            "relative_path": rec.get("source_relative_path"),
                        },
                        "metadata": {
                            "title": rec.get("title"),
                            "document_type": rec.get("document_type"),
                            "doc_type": rec.get("document_type"),
                            "purpose": rec.get("purpose"),
                            "visibility": rec.get("visibility"),
                            # Security-critical: propagate restriction flags so the
                            # retrieval gate can hide restricted sources from students.
                            "restricted": rec.get("restricted"),
                            "access_level": rec.get("access_level"),
                            "status": rec.get("status"),
                            "course_name": rec.get("course_name"),
                            "block": rec.get("block"),
                            "day": rec.get("day"),
                            "chapter": rec.get("chapter"),
                            "module_name": rec.get("module_name"),
                            "learning_objective": rec.get("learning_objective"),
                        },
                        "content_units": doc.get("content_units") or [],
                        "reading_content": doc.get("reading_content") or "",
                    }
                    # Derive use_for_* eligibility flags from the document's purpose
                    # mapping (config-driven, per client). Source index records
                    # written at ingestion do not carry these flags, so the
                    # generation handlers' use_for_* filters would otherwise reject
                    # every doc. This is purely ADDITIVE: it only turns a flag ON
                    # when the doc's inferred purpose qualifies; it never relaxes the
                    # restricted/visibility security gates.
                    _purposes = set(self._purposes_for_payload(payload))
                    _meta = payload["metadata"]
                    for _p in ("style", "cdd", "blueprint", "course_generation"):
                        _key = f"use_for_{_p}"
                        if not _meta.get(_key):
                            _meta[_key] = _p in _purposes
                    # Security hardening: mark answer keys / instructor guides as
                    # restricted by doc type or filename, even when the source
                    # index record left the flag unset. This makes their protection
                    # robust (by type, not by the instructor-visibility gate), which
                    # is required now that instructor/content-team SOURCE docs are
                    # allowed into generation context.
                    if not _meta.get("restricted") and self._is_restricted_source(_meta.get("document_type"), (payload.get("source_file") or {}).get("name")):
                        _meta["restricted"] = True
                        if not _meta.get("access_level"):
                            _meta["access_level"] = "admin_only"
                    yield payload
                except Exception:
                    continue
            return


        # Include active/generated CAS documents from DIS when requested. This lets
        # generation use active Style/CDD/Blueprint content that is stored in S3.
        include_generated = filters.get("include_generated") or filters.get("generated_doc_ids") or (filters.get("purpose") in {"blueprint", "course_generation"})
        if include_generated:
            wanted_ids = filters.get("generated_doc_ids") or []
            if isinstance(wanted_ids, str):
                wanted_ids = [wanted_ids]
            wanted_types = filters.get("generated_types") or []
            if isinstance(wanted_types, str):
                wanted_types = [wanted_types]
            try:
                gsvc = GeneratedDocumentService(self.tenant_cfg)
                generated = gsvc.list(client_id, limit=500).get("items", [])
                for item in generated:
                    if wanted_ids and item.get("generated_doc_id") not in wanted_ids:
                        continue
                    if wanted_types and item.get("generated_type") not in wanted_types:
                        continue
                    if not wanted_ids and not item.get("active"):
                        continue
                    try:
                        doc = gsvc.get(client_id, item["generated_doc_id"])
                    except Exception:
                        continue
                    yield {
                        "job_id": doc.get("generated_doc_id"),
                        "tenant_id": doc.get("tenant_id"),
                        "client_id": client_id,
                        "source_file": {"name": doc.get("title"), "type": f"generated_{doc.get('generated_type')}"},
                        "metadata": {
                            "title": doc.get("title"),
                            "document_type": f"generated_{doc.get('generated_type')}",
                            "doc_type": f"generated_{doc.get('generated_type')}",
                            "purpose": doc.get("generated_type"),
                            **(doc.get("metadata") or {}),
                        },
                        "content_units": [{
                            "content_unit_id": doc.get("generated_doc_id"),
                            "unit_type": f"generated_{doc.get('generated_type')}",
                            "unit_number": 1,
                            "title": doc.get("title"),
                            "text": doc.get("content") or "",
                        }],
                        "reading_content": doc.get("content") or "",
                    }
            except Exception:
                pass

        # Backward compatibility: old payload-only data.
        prefix = self._base_prefix(client_id)
        for key in self.writer.list_keys(prefix, suffix="/studio_payload/payload.json"):
            try:
                yield self.writer.read_json(key)
            except Exception:
                continue

    def _passes_filters(self, unit: Dict[str, Any], payload: Dict[str, Any], filters: Dict[str, Any]) -> bool:
        def as_list(v):
            if v is None or v == "":
                return []
            return v if isinstance(v, list) else [v]

        def norm(v: Any) -> str:
            return str(v or "").strip().lower()

        def value_matches(actual: Any, expected: Any) -> bool:
            if expected in (None, "", [], {}):
                return True
            expected_values = as_list(expected)
            if not expected_values:
                return True
            if isinstance(actual, list):
                actual_values = [norm(x) for x in actual]
            else:
                actual_values = [norm(actual)]
            expected_norm = [norm(x) for x in expected_values]
            return bool(set(actual_values) & set(expected_norm))

        payload_meta = payload.get("metadata", {}) or {}
        unit_meta = unit.get("metadata", {}) or {}
        meta_all = {**payload_meta, **unit_meta}
        source = payload.get("source_file", {}) or {}

        content_types = as_list(filters.get("content_types") or filters.get("document_types"))
        unit_types = as_list(filters.get("unit_types"))
        course_name = filters.get("course_name") or filters.get("course_id")

        if unit_types and unit.get("unit_type") not in unit_types:
            return False

        if content_types:
            possible = [
                meta_all.get("content_type"),
                meta_all.get("document_type"),
                meta_all.get("doc_type"),
                source.get("type"),
            ]
            if not any(norm(v) in [norm(x) for x in content_types] for v in possible):
                return False

        # Generic exact filters used by blueprint/course generation APIs.
        exact_fields = [
            "client_id", "program_id", "course_id", "block_id",
            "block_number", "visibility", "status", "version",
            "topic", "quiz_number", "quiz_id", "project_number", "project_id",
            "administered_on_day", "covers_day",
        ]
        for field in exact_fields:
            if field in filters and not value_matches(meta_all.get(field) or payload.get(field), filters.get(field)):
                return False

        # Day filtering is strict for lesson/slide content, but calendar-mapped
        # content such as AIM quizzes/study questions may not have day_id in filename.
        requested_day = filters.get("day_id") or filters.get("mapped_day")
        requested_day_number = filters.get("day_number")
        if requested_day or requested_day_number:
            actual_day_values = [meta_all.get("day_id"), meta_all.get("mapped_day"), meta_all.get("filename_day_id"), meta_all.get("suggested_day_from_filename")]
            actual_day_number = meta_all.get("day_number")
            day_ok = any(value_matches(v, requested_day) for v in actual_day_values if v) if requested_day else False
            if requested_day_number and str(actual_day_number or "") == str(requested_day_number):
                day_ok = True
            if not day_ok and bool(meta_all.get("calendar_mapping_required")):
                hints = filters.get("_calendar_day_hints") or {}
                hint_terms = hints.get("terms") or []
                hay = " ".join([
                    str(unit.get("title", "")),
                    str(unit.get("text", ""))[:2000],
                    " ".join(str(v) for v in meta_all.values()),
                    str(source.get("name", "")),
                    str(source.get("relative_path", "")),
                ]).lower()
                # Require a meaningful overlap with calendar day text. This supports
                # quiz/study/project mapping from calendar without exposing answer keys.
                overlap = sum(1 for t in set(hint_terms) if t and t in hay)
                day_ok = overlap >= 2
            if not day_ok:
                return False

        bool_fields = [
            "use_for_style", "use_for_cdd", "use_for_blueprint", "use_for_course_generation", "is_generation_candidate",
            "restricted", "calendar_mapping_required", "is_archive_or_working_version",
        ]
        for field in bool_fields:
            if field in filters and filters.get(field) is not None:
                expected = bool(filters.get(field))
                if bool(meta_all.get(field)) != expected:
                    return False

        # Purpose gate.
        # When the caller hand-picks specific documents (job_ids/document_ids),
        # that explicit selection IS the authoritative answer to "what should I
        # use for this purpose". Re-filtering those docs by their auto-inferred
        # purpose would silently drop a user's deliberate choice (e.g. selecting a
        # lesson_pdf/slide_deck as a STYLE reference even though its inferred
        # purpose is general_reference). So we skip the purpose gate for explicitly
        # selected docs. The restricted/visibility gate below is intentionally NOT
        # skipped, so students still can never pull answer keys/instructor guides.
        explicit_selection = bool(
            filters.get("job_ids") or filters.get("job_id") or filters.get("document_ids")
        )
        purpose = str(filters.get("purpose") or "").strip().lower()
        if purpose and purpose not in {"all", "*", "any"} and not explicit_selection:
            if purpose not in [str(p).lower() for p in self._purposes_for_payload(payload)]:
                return False

        # Dynamic metadata filters from CAS Source Library config.
        for dyn_key, dyn_value in (filters.get("metadata_filters") or {}).items():
            if dyn_value in (None, "", [], {}):
                continue
            if not value_matches(meta_all.get(dyn_key) or self._metadata_value(payload, dyn_key), dyn_value):
                return False

        # Restricted-content gate. Only GENUINELY restricted material (answer keys,
        # instructor guides, admin/internal-only) is blocked. Instructor/content-
        # team-authored SOURCE documents (e.g. Cengage manuscripts, AIM calendar)
        # are legitimate generation material and are allowed through — this is what
        # lets both AIM and Cengage source docs feed the CAS pipeline. Answer keys
        # are flagged restricted in _iter_payloads, so _is_hard_restricted catches
        # them regardless of visibility.
        if self.tenant_cfg.retrieval.restricted_content_filter and self._is_hard_restricted(meta_all):
            include_restricted = bool(filters.get("include_restricted", False))
            # role=user can never pull restricted; admins only with an explicit opt-in.
            if self.role == "user" or not include_restricted:
                return False

        if course_name:
            text = " ".join(str(meta_all.get(k,"")) for k in ("course_name","course_id","title","module_name","subject"))
            if str(course_name).lower() not in text.lower():
                return False

        search = str(filters.get("search") or "").strip().lower()
        if search:
            hay = " ".join([
                str(unit.get("title", "")),
                str(unit.get("text", ""))[:2000],
                " ".join(str(v) for v in meta_all.values()),
                str(source.get("name", "")),
                str(source.get("relative_path", "")),
            ]).lower()
            if search not in hay:
                return False
        return True

    def retrieve(self, client_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        filters = dict(body.get("filters", {}) or {})
        generation_type = str((body.get("generation") or {}).get("type") or "").strip().lower().replace("-", "_")
        if generation_type and not filters.get("purpose"):
            filters["purpose"] = generation_type
        retrieval = body.get("retrieval", {}) or {}
        context_input = body.get("context_input") or body.get("blueprint_context") or {}
        calendar_hints = self._calendar_day_hints(client_id, filters)
        if calendar_hints:
            filters["_calendar_day_hints"] = calendar_hints
        query_text = " ".join([str(body.get("query") or ""), " ".join(flatten_text(context_input))]).strip()
        if calendar_hints.get("text"):
            query_text = (query_text + " " + calendar_hints["text"]).strip()
        query_terms = terms(query_text)
        top_k = min(int(retrieval.get("top_k", 8)), self.tenant_cfg.retrieval.max_results)
        token_budget = min(int(retrieval.get("token_budget", 6000)), 20000)
        min_score = float(retrieval.get("min_score", 0.0))

        scored: List[Tuple[float, Dict[str, Any]]] = []
        # Security/filter allow-set. Any doc with >=1 unit that passes the existing
        # S3 gate (_passes_filters: restricted, visibility, client, purpose, day,
        # etc.) is eligible. OpenSearch results are later restricted to these
        # job_ids, so semantic retrieval can never surface content the S3 gate
        # would have hidden. This keeps security identical to the S3 path.
        allowed_jobs: set = set()
        allowed_records: Dict[str, Dict[str, Any]] = {}
        for payload in self._iter_payloads(client_id, filters):
            for unit in payload.get("content_units", []):
                if not self._passes_filters(unit, payload, filters):
                    continue
                _jid = payload.get("job_id")
                allowed_jobs.add(_jid)
                allowed_records.setdefault(_jid, payload)
                searchable = " ".join([
                    str(unit.get("title", "")),
                    str(unit.get("text", "")),
                    str(unit.get("visual_summary", "")),
                    " ".join(unit.get("keywords", []) or []),
                    " ".join(unit.get("topics", []) or []),
                    " ".join(str(v) for v in unit.get("metadata", {}).values()),
                    " ".join(str(v) for v in payload.get("metadata", {}).values()),
                ]).lower()
                if not searchable.strip():
                    continue
                hits = sum(1 for t in query_terms if t in searchable)
                title_hits = sum(1 for t in query_terms if t in str(unit.get("title", "")).lower())
                keyword_hits = sum(1 for t in query_terms if t in " ".join(unit.get("keywords", []) or []).lower())
                visual_hits = sum(1 for t in query_terms if t in str(unit.get("visual_summary", "")).lower())
                denom = max(1, len(set(query_terms)))
                raw = (hits + title_hits*2 + keyword_hits*1.5 + visual_hits*1.2) / denom
                score = min(1.0, raw / 3.0)
                if score >= min_score or not query_terms:
                    src = payload.get("source_file", {}) or {}
                    enriched = {**unit}
                    enriched["job_id"] = payload.get("job_id")
                    enriched["source_file_name"] = src.get("name")
                    enriched["source_file_type"] = src.get("type")
                    # Original-file reference so callers can cite/deep-link the
                    # source document in S3, not just name it. Empty for older
                    # sources indexed before these fields were carried through.
                    enriched["source_file_url"] = src.get("raw_url") or ""
                    enriched["source_relative_path"] = src.get("relative_path") or ""
                    enriched["score"] = round(score, 4)
                    enriched["matched_on"] = []
                    if title_hits: enriched["matched_on"].append("title")
                    if keyword_hits: enriched["matched_on"].append("keywords")
                    if visual_hits: enriched["matched_on"].append("visual_summary")
                    if hits: enriched["matched_on"].append("text_or_metadata")
                    scored.append((score, enriched))

        scored.sort(key=lambda x: x[0], reverse=True)
        s3_units: List[Dict[str, Any]] = [u for _, u in scored]
        include_visual_summary = bool(filters.get("include_visual_summary", True))

        # Prefer semantic (OpenSearch hybrid) ranking, restricted to the allow-set.
        # Fall back to S3 keyword ranking when the user hand-picked documents, when
        # there is no query, or when OpenSearch is disabled/empty/unavailable.
        retrieval_method = "s3_keyword"
        chosen_units = s3_units
        explicit_selection = bool(filters.get("job_ids") or filters.get("job_id") or filters.get("document_ids"))
        if not explicit_selection:
            vector_units = self._vector_units(client_id, query_text, allowed_jobs, allowed_records, top_k)
            if vector_units:
                chosen_units = vector_units
                retrieval_method = "opensearch_hybrid"

        selected, used_tokens, combined = self._pack_units(chosen_units, top_k, token_budget, include_visual_summary)
        return {
            "context_pack_id": f"ctx_{uuid.uuid4().hex[:12]}",
            "tenant_id": self.tenant_cfg.tenant_id,
            "client_id": client_id,
            "request_id": body.get("request_id"),
            "generation": body.get("generation", {}),
            "retrieval_summary": {
                "query_text": query_text,
                "retrieval_method": retrieval_method,
                "total_matches": len(chosen_units),
                "returned_units": len(selected),
                "token_budget": token_budget,
                "estimated_tokens": used_tokens,
                "avg_score": round(sum(float(u.get("score", 0.0)) for u in selected) / len(selected), 4) if selected else 0.0,
                "quality_status": "good" if selected else "no_context",
            },
            "source_units": selected,
            "combined_context": combined,
        }

    def _vector_units(self, client_id: str, query_text: str, allowed_jobs: set, allowed_records: Dict[str, Any], top_k: int) -> List[Dict[str, Any]]:
        """Semantically-ranked units from OpenSearch, gated to allowed_jobs.

        Returns [] (so the caller uses S3 keyword ranking) when vector retrieval
        is disabled, the query is empty, no docs are allowed, or OpenSearch
        errors. Never raises.
        """
        vs = getattr(self.tenant_cfg, "vector_store", None)
        emb_cfg = getattr(self.tenant_cfg, "embedding", None)
        if not (vs and getattr(vs, "enabled", False) and emb_cfg and getattr(emb_cfg, "enabled", False)):
            return []
        if not (query_text or "").strip() or not allowed_jobs:
            return []
        try:
            from services.indexing import embed_query, vector_search
            query_embedding = embed_query(self.tenant_cfg, query_text)
            if not query_embedding:
                return []
            hits = vector_search(self.tenant_cfg, client_id, query_text, query_embedding, size=max(top_k * 4, 20))
        except Exception:
            # Any failure (missing deps, network, bad index) -> S3 keyword fallback.
            return []
        units: List[Dict[str, Any]] = []
        seen: set = set()
        for h in hits:
            job_id = h.get("job_id")
            # SECURITY: only surface chunks from docs the S3 gate already allowed.
            if job_id not in allowed_jobs:
                continue
            text = str(h.get("text") or "").strip()
            if not text:
                continue
            cuid = h.get("content_unit_id") or f"{job_id}:{len(units)}"
            if cuid in seen:
                continue
            seen.add(cuid)
            rec = allowed_records.get(job_id) or {}
            src = rec.get("source_file", {}) if isinstance(rec, dict) else {}
            units.append({
                "content_unit_id": cuid,
                "unit_type": h.get("unit_type") or "content_chunk",
                "unit_number": h.get("unit_number") or 1,
                "title": h.get("title") or (src.get("name") if src else "") or "",
                "text": text,
                "visual_summary": h.get("visual_summary") or "",
                "keywords": h.get("keywords") or [],
                "job_id": job_id,
                "source_file_name": h.get("source_file_name") or (src.get("name") if src else "") or "",
                "source_file_type": h.get("source_file_type") or (src.get("type") if src else "") or "",
                "source_file_url": (src.get("raw_url") if src else "") or "",
                "source_relative_path": (src.get("relative_path") if src else "") or "",
                "score": round(float(h.get("_score") or 0.0), 4),
                "matched_on": ["semantic"],
            })
            if len(units) >= max(top_k * 3, top_k):
                break
        return units

    def _pack_units(self, units: List[Dict[str, Any]], top_k: int, token_budget: int, include_visual_summary: bool) -> Tuple[List[Dict[str, Any]], int, str]:
        """Pack ranked units into the token budget. Returns (selected, tokens, combined)."""
        selected: List[Dict[str, Any]] = []
        used_tokens = 0
        for unit in units:
            unit_text = self._format_unit(unit, include_visual_summary)
            t = estimate_tokens(unit_text)
            if selected and used_tokens + t > token_budget:
                continue
            unit["estimated_tokens"] = t
            selected.append(unit)
            used_tokens += t
            if len(selected) >= top_k:
                break
        combined = "\n\n---\n\n".join(self._format_unit(u, include_visual_summary) for u in selected)
        return selected, used_tokens, combined

    def get_units(self, client_id: str, content_unit_ids: List[str], include_visual_summary: bool = True) -> Dict[str, Any]:
        wanted = set(content_unit_ids)
        out: List[Dict[str, Any]] = []
        for payload in self._iter_payloads(client_id, {}):
            for unit in payload.get("content_units", []):
                if unit.get("content_unit_id") in wanted:
                    src = payload.get("source_file", {}) or {}
                    enriched = {**unit, "job_id": payload.get("job_id"),
                                "source_file_name": src.get("name"),
                                "source_file_type": src.get("type"),
                                "source_file_url": src.get("raw_url") or "",
                                "source_relative_path": src.get("relative_path") or ""}
                    out.append(enriched)
        combined = "\n\n---\n\n".join(self._format_unit(u, include_visual_summary) for u in out)
        return {"source_units": out, "combined_context": combined, "returned_units": len(out)}

    def _format_unit(self, unit: Dict[str, Any], include_visual_summary: bool = True) -> str:
        # Prompt context should contain readable source content, not internal metadata.
        parts = [
            f"Source: {unit.get('source_file_name','')}",
            f"Title: {unit.get('title','')}",
            clean_reading_text(unit.get('text','')),
        ]
        if include_visual_summary and unit.get("visual_summary"):
            parts.append(f"Visual Summary: {clean_reading_text(unit.get('visual_summary'))}")
        return "\n".join(p for p in parts if str(p or '').strip())

# -----------------------------------------------------------------------------
# Large-document Source Library view helpers (attached without disturbing legacy methods)
# -----------------------------------------------------------------------------
from services.source_library import (
    build_view_manifest,
    content_pages_response,
    content_units_response,
    content_unit_detail_response,
    content_search_response,
)


def _load_source_content_doc_for_view(self, client_id: str, job_id: str) -> Dict[str, Any]:
    index = read_source_index(self.tenant_cfg, client_id)
    record = next((r for r in index.get("sources", []) if str(r.get("job_id")) == str(job_id)), None)
    if record and record.get("content_key"):
        return self.writer.read_json(record.get("content_key"))
    # Backward compatibility for old payload-only uploads.
    payload = self._load_payload_by_job(client_id, job_id)
    units = payload.get("content_units", [])
    meta = payload.get("metadata", {}) or {}
    source = payload.get("source_file", {}) or {}
    reading_content = payload_reading_content(payload)
    return {
        "schema_version": "source_content_v1",
        "job_id": job_id,
        "tenant_id": payload.get("tenant_id"),
        "client_id": payload.get("client_id"),
        "document_title": meta.get("title") or source.get("name"),
        "source_file_name": source.get("name"),
        "source_file_type": source.get("type"),
        "document_type": self._normalize_doc_type(payload),
        "purpose": meta.get("purpose") or ", ".join(self._purposes_for_payload(payload)),
        "visibility": meta.get("visibility") or "instructor",
        "status": meta.get("status") or "processed",
        "total_units": len(units),
        "reading_content": reading_content,
        "preview": reading_content[:12000],
        "content_units": [{
            "content_unit_id": u.get("content_unit_id"),
            "unit_type": u.get("unit_type"),
            "unit_number": u.get("unit_number"),
            "title": u.get("title"),
            "text": clean_reading_text(u.get("text") or ""),
        } for u in units],
    }


def get_source_overview(self, client_id: str, job_id: str) -> Dict[str, Any]:
    return build_view_manifest(_load_source_content_doc_for_view(self, client_id, job_id))


def get_source_pages(self, client_id: str, job_id: str, page: int = 1, page_size: int = 5) -> Dict[str, Any]:
    return content_pages_response(_load_source_content_doc_for_view(self, client_id, job_id), page=page, page_size=page_size)


def get_source_units(self, client_id: str, job_id: str) -> Dict[str, Any]:
    return content_units_response(_load_source_content_doc_for_view(self, client_id, job_id))


def get_source_unit_detail(self, client_id: str, job_id: str, unit_id: str) -> Dict[str, Any]:
    return content_unit_detail_response(_load_source_content_doc_for_view(self, client_id, job_id), unit_id=unit_id)


def search_source_content(self, client_id: str, job_id: str, q: str, limit: int = 20) -> Dict[str, Any]:
    return content_search_response(_load_source_content_doc_for_view(self, client_id, job_id), q=q, limit=limit)


ContextRetrievalService._load_source_content_doc_for_view = _load_source_content_doc_for_view
ContextRetrievalService.get_source_overview = get_source_overview
ContextRetrievalService.get_source_pages = get_source_pages
ContextRetrievalService.get_source_units = get_source_units
ContextRetrievalService.get_source_unit_detail = get_source_unit_detail
ContextRetrievalService.search_source_content = search_source_content
