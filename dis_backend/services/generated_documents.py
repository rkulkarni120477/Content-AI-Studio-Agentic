"""Generated document registry for CAS outputs stored in DIS/S3.

S3 is the content source of truth for generated Style/CDD/Blueprint documents.
CAS may keep small DB pointers for workflow/active state, but generated document
bodies are saved here and listed from a generated index.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List
import uuid

from config.settings import TenantConfig, get_settings
from services.artifacts import ArtifactWriter

GENERATED_TYPES = {"style", "cdd", "blueprint", "course_generation", "block"}


def generated_base_prefix(client_id: str) -> str:
    return f"generated/{client_id}/{get_settings().environment}"


def generated_index_key(client_id: str) -> str:
    return f"{generated_base_prefix(client_id)}/generated_index/generated_list.json"


def generated_doc_key(client_id: str, generated_type: str, generated_doc_id: str) -> str:
    return f"{generated_base_prefix(client_id)}/{generated_type}/{generated_doc_id}.json"


def _now() -> str:
    return datetime.utcnow().isoformat()


def _norm_type(value: str) -> str:
    t = (value or "").strip().lower().replace("-", "_")
    return t if t in GENERATED_TYPES else "style"


class GeneratedDocumentService:
    def __init__(self, tenant_cfg: TenantConfig):
        self.tenant_cfg = tenant_cfg
        self.writer = ArtifactWriter(tenant_cfg)

    def read_index(self, client_id: str) -> Dict[str, Any]:
        try:
            data = self.writer.read_json(generated_index_key(client_id))
            if isinstance(data, dict):
                data.setdefault("items", [])
                return data
        except Exception:
            pass
        return {
            "schema_version": "generated_index_v1",
            "tenant_id": self.tenant_cfg.tenant_id,
            "client_id": client_id,
            "updated_at": _now(),
            "items": [],
        }

    def write_index(self, client_id: str, index: Dict[str, Any]) -> str:
        index["schema_version"] = "generated_index_v1"
        index["tenant_id"] = self.tenant_cfg.tenant_id
        index["client_id"] = client_id
        index["updated_at"] = _now()
        return self.writer.write_json(generated_index_key(client_id), index)

    def upsert(self, client_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        generated_type = _norm_type(str(payload.get("generated_type") or payload.get("type") or "style"))
        generated_doc_id = str(payload.get("generated_doc_id") or payload.get("id") or f"gen_{generated_type}_{uuid.uuid4().hex[:12]}")
        title = str(payload.get("title") or f"Generated {generated_type.title()}").strip()
        now = _now()
        doc = {
            "schema_version": "generated_document_v1",
            "generated_doc_id": generated_doc_id,
            "generated_type": generated_type,
            "client_id": client_id,
            "tenant_id": self.tenant_cfg.tenant_id,
            "title": title,
            "content": payload.get("content") or payload.get("body") or "",
            "summary": payload.get("summary") or str(payload.get("content") or "")[:500],
            "metadata": payload.get("metadata") or {},
            "source_documents_used": payload.get("source_documents_used") or payload.get("source_units") or [],
            "active": bool(payload.get("active", False)),
            "archived": bool(payload.get("archived", False)),
            "cas_ref": payload.get("cas_ref") or {},
            "created_by": payload.get("created_by") or "",
            "created_at": payload.get("created_at") or now,
            "updated_at": now,
        }
        key = generated_doc_key(client_id, generated_type, generated_doc_id)
        url = self.writer.write_json(key, doc)

        index = self.read_index(client_id)
        items = [i for i in index.get("items", []) if i.get("generated_doc_id") != generated_doc_id]
        if doc["active"]:
            for i in items:
                if i.get("generated_type") == generated_type:
                    i["active"] = False
        item = {
            "generated_doc_id": generated_doc_id,
            "generated_type": generated_type,
            "client_id": client_id,
            "tenant_id": self.tenant_cfg.tenant_id,
            "title": title,
            "summary": doc["summary"],
            "metadata": doc["metadata"],
            "active": doc["active"],
            "archived": doc["archived"],
            "s3_key": key,
            "s3_url": url,
            "cas_ref": doc["cas_ref"],
            "created_by": doc["created_by"],
            "created_at": doc["created_at"],
            "updated_at": doc["updated_at"],
        }
        items.append(item)
        items.sort(key=lambda x: str(x.get("updated_at") or x.get("created_at") or ""), reverse=True)
        index["items"] = items
        self.write_index(client_id, index)
        return {"ok": True, "generated_doc_id": generated_doc_id, "generated_type": generated_type, "s3_key": key, "s3_url": url, "item": item}

    def list(self, client_id: str, generated_type: str = "", search: str = "", active: str = "", limit: int = 100, offset: int = 0) -> Dict[str, Any]:
        index = self.read_index(client_id)
        items: List[Dict[str, Any]] = []
        gtype = (generated_type or "").strip().lower().replace("-", "_")
        q = (search or "").strip().lower()
        for item in index.get("items", []):
            if item.get("archived"):
                continue
            if gtype and gtype not in {"all", "*"} and item.get("generated_type") != gtype:
                continue
            if active in {"true", "false"} and bool(item.get("active")) != (active == "true"):
                continue
            if q:
                hay = " ".join(str(item.get(k) or "") for k in ("title", "summary", "generated_doc_id", "generated_type")).lower()
                hay += " " + " ".join(str(v) for v in (item.get("metadata") or {}).values()).lower()
                if q not in hay:
                    continue
            items.append(item)
        items.sort(key=lambda x: str(x.get("updated_at") or x.get("created_at") or ""), reverse=True)
        total = len(items)
        limit = max(1, min(int(limit or 100), 500))
        offset = max(0, int(offset or 0))
        return {"tenant_id": self.tenant_cfg.tenant_id, "client_id": client_id, "total": total, "limit": limit, "offset": offset, "items": items[offset:offset + limit]}

    def get(self, client_id: str, generated_doc_id: str) -> Dict[str, Any]:
        index = self.read_index(client_id)
        item = next((i for i in index.get("items", []) if str(i.get("generated_doc_id")) == str(generated_doc_id)), None)
        if not item:
            raise FileNotFoundError(f"Generated document not found: {generated_doc_id}")
        return self.writer.read_json(item["s3_key"])

    def activate(self, client_id: str, generated_doc_id: str) -> Dict[str, Any]:
        doc = self.get(client_id, generated_doc_id)
        generated_type = doc.get("generated_type") or "style"
        doc["active"] = True
        return self.upsert(client_id, doc)

    def archive(self, client_id: str, generated_doc_id: str) -> Dict[str, Any]:
        doc = self.get(client_id, generated_doc_id)
        doc["archived"] = True
        doc["active"] = False
        return self.upsert(client_id, doc)
