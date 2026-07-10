"""Agent for DIS pipeline step: vector_store_upsert.

This agent uses embedding_ready_chunks as the source of truth. It should not skip
only because skip_embedding is true, because older states may still have
skip_embedding=true even after embeddings were generated.
"""
from __future__ import annotations

from services.agents.base import BasePipelineAgent
from services.pipeline.common import PipelineState
from services.adapters.vector_store import get_vector_store_adapter


class VectorStoreUpsertAgent(BasePipelineAgent):
    """Upsert embedded content units into the configured vector store."""

    step_name = "vector_store_upsert"
    purpose = "Upsert embedded content units into tenant configured vector store"

    def run(self, state: PipelineState) -> PipelineState:
        cfg = self.ctx.cfg.vector_store

        if not cfg.enabled:
            state["vector_store_upsert_result"] = {
                "status": "skipped",
                "reason": "vector_store.enabled=false",
            }
            targets = state.setdefault("storage_targets", [])
            if "vector_store_skipped" not in targets:
                targets.append("vector_store_skipped")
            return self.done(state)

        embedded_chunks = state.get("embedding_ready_chunks") or []
        chunks_with_embeddings = [chunk for chunk in embedded_chunks if chunk.get("embedding")]

        # Do not rely only on skip_embedding. The previous bug left skip_embedding=true
        # even when embedding_ready_chunks contained valid vectors.
        if not chunks_with_embeddings:
            state["vector_store_upsert_result"] = {
                "status": "skipped",
                "reason": "no_embedding_ready_chunks",
                "embedding_ready_chunks": len(embedded_chunks),
                "skip_embedding": bool(state.get("skip_embedding")),
            }
            targets = state.setdefault("storage_targets", [])
            if "vector_store_skipped" not in targets:
                targets.append("vector_store_skipped")
            return self.done(state)

        # Ensure downstream adapter receives only chunks that have vectors.
        state["embedding_ready_chunks"] = chunks_with_embeddings
        state["skip_embedding"] = False

        adapter = get_vector_store_adapter(cfg.provider)
        result = adapter.upsert(self.ctx.cfg, state)
        state["vector_store_upsert_result"] = result

        if result.get("status") == "completed":
            targets = state.setdefault("storage_targets", [])
            if "vector_store" not in targets:
                targets.append("vector_store")
        elif result.get("status") == "skipped":
            targets = state.setdefault("storage_targets", [])
            if "vector_store_skipped" not in targets:
                targets.append("vector_store_skipped")
        else:
            error = result.get("error") or result.get("reason") or "unknown error"
            targets = state.setdefault("storage_targets", [])
            if "vector_store_failed" not in targets:
                targets.append("vector_store_failed")
            state.setdefault("errors", []).append(f"vector_store_upsert failed: {error}")
            state["fatal_error"] = f"vector_store_upsert failed: {error}"
            state["stop_pipeline"] = True
            state["failed_step"] = self.step_name

        return self.done(state)
