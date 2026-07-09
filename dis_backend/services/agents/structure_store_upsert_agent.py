"""Agent for DIS pipeline step: structure_store_upsert."""
from __future__ import annotations
from services.agents.base import BasePipelineAgent
from services.pipeline.common import PipelineState
from services.adapters.structure_store import get_structure_store_adapter

class StructureStoreUpsertAgent(BasePipelineAgent):
    step_name = "structure_store_upsert"
    purpose = "Upsert structured ingestion records into tenant configured structure store"

    def run(self, state: PipelineState) -> PipelineState:
        cfg = self.ctx.cfg.structure_store
        if not cfg.enabled:
            state["structure_store_upsert_result"] = {"status": "skipped", "reason": "structure_store.enabled=false"}
            state.setdefault("storage_targets", []).append("structure_store_skipped")
            return self.done(state)
        adapter = get_structure_store_adapter(cfg.provider)
        result = adapter.upsert(self.ctx.cfg, state)
        state["structure_store_upsert_result"] = result
        if result.get("status") == "completed":
            state.setdefault("storage_targets", []).append("structure_store")
        else:
            state.setdefault("storage_targets", []).append("structure_store_failed")
            state.setdefault("errors", []).append(f"structure_store_upsert failed: {result.get('error') or result.get('reason') or 'unknown error'}")
            state["fatal_error"] = f"structure_store_upsert failed: {result.get('error') or result.get('reason') or 'unknown error'}"
            state["stop_pipeline"] = True
            state["failed_step"] = self.step_name
        return self.done(state)
