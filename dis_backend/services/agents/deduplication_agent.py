"""Agent for DIS pipeline step: deduplication.

Client-level dedup manifest mode:
- one JSON manifest per client/environment
- exact duplicate check happens by raw file SHA256 before heavy extraction
- manifest is updated again in FinalizeAgent with content_hash and final statuses
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Dict

from services.agents.base import BasePipelineAgent
from services.pipeline.common import PipelineState


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _manifest_key(ctx, state: PipelineState) -> str:
    """Return logical manifest key. ArtifactWriter adds storage.base_prefix automatically."""
    dedup_cfg = getattr(ctx.cfg, "deduplication", None)
    configured = getattr(dedup_cfg, "manifest_s3_key", "") if dedup_cfg else ""
    if configured:
        # If someone put s3://bucket/key, keep only key part.
        key = configured.replace("\\", "/")
        if key.startswith("s3://"):
            key = "/".join(key.split("/", 3)[3:])
        # ArtifactWriter.write_json always writes to processed bucket and joins base_prefix.
        base_prefix = (getattr(ctx.cfg.storage, "base_prefix", "") or "").strip("/")
        if base_prefix and key.startswith(base_prefix + "/"):
            key = key[len(base_prefix) + 1:]
        return key.lstrip("/")

    env = ctx.writer.settings.environment or "development"
    namespace = state.get("namespace") or ctx.cfg.namespace
    return f"processed/{namespace}/{env}/_dedup/dedup_manifest.json"


def _empty_manifest(state: PipelineState) -> Dict[str, Any]:
    return {
        "client_id": state.get("client_id") or state.get("tenant_id"),
        "tenant_id": state.get("tenant_id"),
        "namespace": state.get("namespace"),
        "env": "development",
        "hash_algorithm": "sha256",
        "created_at": _now(),
        "last_updated_at": _now(),
        "files": {},
        "content_hashes": {},
    }


class DeduplicationAgent(BasePipelineAgent):
    step_name = "deduplication"
    purpose = "Client-level duplicate detection using one dedup_manifest.json per client/env"

    def run(self, state: PipelineState) -> PipelineState:
        ctx = self.ctx
        dedup_cfg = getattr(ctx.cfg, "deduplication", None)
        enabled = getattr(dedup_cfg, "enabled", None)
        if enabled is None:
            enabled = getattr(ctx.cfg.ingestion, "dedup_enabled", True)

        if not enabled:
            state["is_duplicate"] = False
            state["dedup_result"] = {"status": "skipped", "reason": "deduplication.enabled=false"}
            return self.done(state)

        raw = state.get("raw_bytes", b"") or b""
        file_sha256 = state.get("file_sha256") or hashlib.sha256(raw).hexdigest()
        state["file_sha256"] = file_sha256

        manifest_key = _manifest_key(ctx, state)
        state["dedup_manifest_key"] = manifest_key

        try:
            manifest = ctx.writer.read_json(manifest_key)
            if not isinstance(manifest, dict):
                manifest = _empty_manifest(state)
        except Exception:
            manifest = _empty_manifest(state)

        manifest["env"] = ctx.writer.settings.environment or manifest.get("env") or "development"
        manifest.setdefault("files", {})
        manifest.setdefault("content_hashes", {})

        existing = manifest["files"].get(file_sha256)
        if existing:
            state["is_duplicate"] = True
            state["stop_pipeline"] = True
            state["final_status"] = "duplicate"
            state["duplicate_of_job_id"] = existing.get("first_job_id") or existing.get("job_id")
            state["duplicate_reason"] = "same_file_hash"
            state["dedup_result"] = {
                "status": "duplicate",
                "duplicate_reason": "same_file_hash",
                "file_sha256": file_sha256,
                "manifest_key": manifest_key,
                "existing_job_id": existing.get("first_job_id") or existing.get("job_id"),
                "existing_source_file_name": existing.get("source_file_name"),
                "existing_status": existing.get("status"),
            }
            return self.done(state)

        # Reserve the hash immediately so folder parallel uploads of the same file do not both process.
        entry = {
            "file_sha256": file_sha256,
            "first_job_id": state.get("job_id"),
            "latest_job_id": state.get("job_id"),
            "tenant_id": state.get("tenant_id"),
            "client_id": state.get("client_id"),
            "namespace": state.get("namespace"),
            "source_file_name": state.get("filename"),
            "source_relative_path": state.get("source_relative_path"),
            "raw_s3_key": state.get("s3_key"),
            "raw_storage_url": state.get("raw_storage_url"),
            "file_size_bytes": len(raw),
            "status": "processing",
            "created_at": _now(),
            "updated_at": _now(),
        }
        manifest["files"][file_sha256] = entry
        manifest["last_updated_at"] = _now()
        ctx.writer.write_json(manifest_key, manifest)

        state["is_duplicate"] = False
        state["dedup_result"] = {
            "status": "new",
            "file_sha256": file_sha256,
            "manifest_key": manifest_key,
        }
        return self.done(state)
