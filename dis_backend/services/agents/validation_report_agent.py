"""Agent for DIS pipeline step: validation_report.

Creates the final validation report. This version is defensive about state
handoff between agents. It derives structure/vector status from explicit result
objects first, then from completed_steps as a fallback. This prevents reports
from showing not_run when the previous step artifact already completed.
"""
from __future__ import annotations

from typing import Any

from services.agents.base import BasePipelineAgent
from services.pipeline.common import PipelineState


class ValidationReportAgent(BasePipelineAgent):
    """Build and persist a validation report for one ingestion job."""

    step_name = "validation_report"
    purpose = "Validation Report"

    def run(self, state: PipelineState) -> PipelineState:
        ctx = self.ctx

        required_payload_fields = [
            "tenant_id",
            "client_id",
            "job_id",
            "source_file.name",
            "source_file.type",
            "source_file.raw_url",
            "metadata",
            "content_units",
        ]
        payload = state.get("studio_payload", {}) or {}

        def get_nested(obj: Any, path: str) -> Any:
            cur = obj
            for part in path.split("."):
                if isinstance(cur, dict) and part in cur:
                    cur = cur[part]
                else:
                    return None
            return cur

        missing: list[str] = []
        for field in required_payload_fields:
            value = get_nested(payload, field) if "." in field else payload.get(field)
            if value in (None, "", [], {}):
                missing.append(field)

        warnings: list[str] = []
        if not state.get("raw_text"):
            warnings.append("No extracted text found. For scanned PDFs/images, enable OCR/vision extraction.")
        if state.get("has_images") and not state.get("visual_units"):
            warnings.append("Images detected but detailed visual understanding is not enabled.")
        if not state.get("content_units"):
            warnings.append("No content units were created.")

        completed_steps = state.get("completed_steps", []) or []
        artifact_urls = state.get("artifact_urls", {}) or {}

        embedded_chunks = state.get("embedding_ready_chunks") or []
        embeddings_created = sum(1 for chunk in embedded_chunks if isinstance(chunk, dict) and chunk.get("embedding"))

        embedding_result = state.get("embedding_generation_result") or {}
        structure_result = state.get("structure_store_upsert_result") or {}
        vector_result = state.get("vector_store_upsert_result") or {}

        # Structure store status: prefer explicit result, fallback to completed_steps.
        structure_status = structure_result.get("status")
        if not structure_status:
            structure_status = "completed" if "structure_store_upsert" in completed_steps else "not_run"

        # Embedding status: prefer explicit result, fallback to actual vectors, then completed_steps.
        embedding_status = embedding_result.get("status")
        if not embedding_status:
            if embeddings_created > 0:
                embedding_status = "completed"
            elif "embedding_generation" in completed_steps:
                embedding_status = "completed"
            else:
                embedding_status = "not_run"

        # Vector status: prefer explicit result, fallback to completed_steps + embeddings.
        vector_status = vector_result.get("status")
        if not vector_status:
            if "vector_store_upsert" in completed_steps:
                vector_status = "completed"
            else:
                vector_status = "not_run"

        # Indexed count: prefer explicit result. If vector step completed but result was not
        # carried into validation state, use embedded chunk count as the best internal count.
        vector_documents_indexed = int(vector_result.get("documents_indexed") or 0)
        if vector_documents_indexed == 0 and vector_status == "completed":
            vector_documents_indexed = embeddings_created

        report = {
            "job_id": state.get("job_id"),
            "tenant_id": state.get("tenant_id"),
            "client_id": state.get("client_id"),
            "valid": len(missing) == 0 and not state.get("fatal_error"),
            "missing_required_fields": missing,
            "warnings": warnings,
            "artifact_checks": {
                "raw_file_uploaded": bool(state.get("raw_storage_url")),
                "extracted_text_created": bool(artifact_urls.get("extracted_text")),
                "metadata_created": bool(artifact_urls.get("metadata")),
                "content_units_created": bool(state.get("content_units")),
                "studio_payload_created": bool(artifact_urls.get("studio_payload")),
                "structure_store_upsert_status": structure_status,
                "embedding_generation_status": embedding_status,
                "vector_store_upsert_status": vector_status,
                "fatal_error": state.get("fatal_error", ""),
            },
            "counts": {
                "completed_steps": len(completed_steps),
                "content_units": len(state.get("content_units", []) or []),
                "text_length": len(state.get("raw_text", "") or ""),
                "page_count": state.get("page_count", 0),
                "embedding_ready_chunks": len(embedded_chunks),
                "embeddings_created": embeddings_created,
                "vector_documents_indexed": vector_documents_indexed,
            },
            "step_artifacts": artifact_urls,
        }

        state["validation_report"] = report
        prefix = ctx.writer.job_prefix(state.get("namespace", "unknown"), state["job_id"])
        state.setdefault("artifact_urls", {})["validation_report"] = ctx.writer.write_json(
            f"{prefix}/validation/report.json",
            report,
        )
        return self.done(state)
