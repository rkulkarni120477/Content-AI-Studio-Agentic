"""Agent for DIS pipeline step: finalize.

Updates client-level dedup_manifest.json after processing finishes.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any, Dict

from services.agents.base import BasePipelineAgent
from services.pipeline.common import PipelineState


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _normalize_text(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"\bpage\s+\d+\b", " ", text)
    text = re.sub(r"\b\d+\s*/\s*\d+\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _manifest_key(ctx, state: PipelineState) -> str:
    key = state.get("dedup_manifest_key") or ""
    if key:
        return key
    dedup_cfg = getattr(ctx.cfg, "deduplication", None)
    configured = getattr(dedup_cfg, "manifest_s3_key", "") if dedup_cfg else ""
    if configured:
        key = configured.replace("\\", "/")
        if key.startswith("s3://"):
            key = "/".join(key.split("/", 3)[3:])
        base_prefix = (getattr(ctx.cfg.storage, "base_prefix", "") or "").strip("/")
        if base_prefix and key.startswith(base_prefix + "/"):
            key = key[len(base_prefix) + 1:]
        return key.lstrip("/")
    env = ctx.writer.settings.environment or "development"
    namespace = state.get("namespace") or ctx.cfg.namespace
    return f"processed/{namespace}/{env}/_dedup/dedup_manifest.json"


def _empty_manifest(state: PipelineState, env: str) -> Dict[str, Any]:
    return {
        "client_id": state.get("client_id") or state.get("tenant_id"),
        "tenant_id": state.get("tenant_id"),
        "namespace": state.get("namespace"),
        "env": env,
        "hash_algorithm": "sha256",
        "created_at": _now(),
        "last_updated_at": _now(),
        "files": {},
        "content_hashes": {},
    }


class FinalizeAgent(BasePipelineAgent):
    step_name = "finalize"
    purpose = "Finalize job and update dedup manifest"

    def run(self, state: PipelineState) -> PipelineState:
        ctx = self.ctx

        if state.get("is_duplicate"):
            state["final_status"] = "duplicate"
            return self.done(state)

        if state.get("fatal_error"):
            state["final_status"] = "failed"
            self._update_manifest(state, status="failed")
            return self.done(state)

        state["final_status"] = "ready_for_studio_context_retrieval"
        self._update_manifest(state, status="completed")
        return self.done(state)

    def _update_manifest(self, state: PipelineState, status: str) -> None:
        ctx = self.ctx
        dedup_cfg = getattr(ctx.cfg, "deduplication", None)
        enabled = getattr(dedup_cfg, "enabled", None)
        if enabled is None:
            enabled = getattr(ctx.cfg.ingestion, "dedup_enabled", True)
        if not enabled:
            return

        file_sha256 = state.get("file_sha256") or ""
        if not file_sha256:
            return

        env = ctx.writer.settings.environment or "development"
        manifest_key = _manifest_key(ctx, state)
        try:
            manifest = ctx.writer.read_json(manifest_key)
            if not isinstance(manifest, dict):
                manifest = _empty_manifest(state, env)
        except Exception:
            manifest = _empty_manifest(state, env)
        manifest.setdefault("files", {})
        manifest.setdefault("content_hashes", {})

        normalized = _normalize_text(state.get("raw_text", ""))
        content_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else ""

        existing = manifest["files"].get(file_sha256, {})
        existing.update({
            "file_sha256": file_sha256,
            "content_hash": content_hash,
            "latest_job_id": state.get("job_id"),
            "first_job_id": existing.get("first_job_id") or state.get("job_id"),
            "tenant_id": state.get("tenant_id"),
            "client_id": state.get("client_id"),
            "namespace": state.get("namespace"),
            "source_file_name": state.get("filename"),
            "source_relative_path": state.get("source_relative_path"),
            "document_type": state.get("doc_type"),
            "classification": state.get("classification"),
            "raw_s3_key": state.get("s3_key"),
            "raw_storage_url": state.get("raw_storage_url"),
            "processed_s3_prefix": ctx.writer.job_prefix(state.get("namespace", "unknown"), state.get("job_id", "unknown")),
            "content_units_count": len(state.get("content_units", []) or []),
            "page_count": state.get("page_count", 0),
            "structure_store_upsert_status": (state.get("structure_store_upsert_result") or {}).get("status", "not_run"),
            "embedding_generation_status": (state.get("embedding_generation_result") or {}).get("status", "not_run"),
            "vector_store_upsert_status": (state.get("vector_store_upsert_result") or {}).get("status", "not_run"),
            "status": status,
            "error": state.get("fatal_error") or "",
            "updated_at": _now(),
        })
        existing.setdefault("created_at", _now())
        manifest["files"][file_sha256] = existing

        if content_hash:
            ch = manifest["content_hashes"].setdefault(content_hash, {
                "first_job_id": state.get("job_id"),
                "source_file_names": [],
                "document_type": state.get("doc_type"),
                "created_at": _now(),
            })
            if state.get("filename") and state.get("filename") not in ch["source_file_names"]:
                ch["source_file_names"].append(state.get("filename"))
            ch["latest_job_id"] = state.get("job_id")
            ch["updated_at"] = _now()
            ch["document_type"] = state.get("doc_type") or ch.get("document_type")

        manifest["env"] = env
        manifest["last_updated_at"] = _now()
        ctx.writer.write_json(manifest_key, manifest)
