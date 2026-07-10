"""Agent for DIS pipeline step: embedding_generation.

This agent calls the configured embedding provider and prepares chunks for the
vector-store step. Important: after successful embedding generation it must clear
skip_embedding and add vector_store to storage_targets; otherwise OpenSearch will
be skipped even though embeddings were generated.
"""
from __future__ import annotations

from services.agents.base import BasePipelineAgent
from services.pipeline.common import PipelineState
from services.indexing import generate_embeddings


class EmbeddingGenerationAgent(BasePipelineAgent):
    """Generate embeddings for content units."""

    step_name = "embedding_generation"
    purpose = "Generate vector embeddings for extracted content units"

    def run(self, state: PipelineState) -> PipelineState:
        result = generate_embeddings(self.ctx.cfg, state)
        state["embedding_generation_result"] = result

        status = result.get("status")

        if status == "completed":
            embedded_chunks = state.get("embedding_ready_chunks") or []

            # If embeddings were actually created, make the vector store step eligible.
            # This fixes the old contradictory state:
            # embedding_ready_chunks=[...embeddings...] but skip_embedding=true.
            has_embeddings = any(bool(chunk.get("embedding")) for chunk in embedded_chunks)
            if has_embeddings:
                state["skip_embedding"] = False
                targets = state.setdefault("storage_targets", [])
                if "vector_store" not in targets:
                    targets.append("vector_store")
            else:
                # Completed with zero chunks is valid, but vector upsert should skip cleanly.
                state["skip_embedding"] = True

        elif status == "skipped":
            state["skip_embedding"] = True
            state.setdefault("storage_targets", []).append("embedding_skipped")

        elif status == "failed":
            error = result.get("error") or "unknown error"
            state["skip_embedding"] = True
            state.setdefault("storage_targets", []).append("embedding_failed")
            state.setdefault("errors", []).append(f"embedding_generation failed: {error}")
            state["fatal_error"] = f"embedding_generation failed: {error}"
            state["stop_pipeline"] = True
            state["failed_step"] = self.step_name

        return self.done(state)
