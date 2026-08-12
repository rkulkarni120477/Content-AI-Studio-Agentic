"""LangGraph based DIS pipeline orchestrator.

Each pipeline step is a separate agent in services/agents/*_agent.py.
This file only defines graph order, routing, and state initialization.
"""
from __future__ import annotations
import logging

try:
    from langgraph.graph import END, StateGraph
except Exception:
    END = "__end__"
    StateGraph = None

from config.settings import TenantConfig
from services.pipeline.common import PipelineContext, PipelineState
from services.agents.registry import STEP_ORDER, get_agent
from services.token_guard import TokenGuard

log = logging.getLogger(__name__)

class AgentGraphNodes:
    """Instantiates and executes one agent object per graph node."""
    def __init__(self, tenant_cfg: TenantConfig, token_guard: TokenGuard):
        self.ctx = PipelineContext(tenant_cfg, token_guard)
        self.agents = {name: get_agent(name)(self.ctx) for name in STEP_ORDER}

    def run_agent(self, name: str, state: PipelineState) -> PipelineState:
        state["current_agent"] = name
        # Deduplication or another agent may stop heavy downstream processing.
        # We still run report/checkpoint/finalize so the job has a clear result.
        if state.get("stop_pipeline") and name not in {"validation_report", "checkpoint_save", "finalize"}:
            state.setdefault("skipped_agents", []).append(name)
            state["skip_reason"] = state.get("skip_reason") or state.get("dedup_result", {}).get("status") or "stopped"
            return self.nodes_step_done(name, state)
        return self.agents[name].run(state)

    def nodes_step_done(self, name: str, state: PipelineState) -> PipelineState:
        # Reuse any agent context to write a skipped step artifact.
        return self.ctx.step_done(state, name)

class _SequentialCompiled:
    """Fallback runner when langgraph package is not installed."""
    def __init__(self, nodes: AgentGraphNodes):
        self.nodes = nodes

    async def ainvoke(self, state: PipelineState) -> PipelineState:
        for name in STEP_ORDER:
            state = self.nodes.run_agent(name, state)
        return state

def build_pipeline(tenant_cfg: TenantConfig, token_guard: TokenGuard):
    nodes = AgentGraphNodes(tenant_cfg, token_guard)
    if StateGraph is None:
        log.warning("langgraph is not installed; using sequential agent fallback")
        return _SequentialCompiled(nodes)

    graph = StateGraph(PipelineState)
    for name in STEP_ORDER:
        graph.add_node(name, lambda state, step_name=name: nodes.run_agent(step_name, state))

    graph.set_entry_point(STEP_ORDER[0])
    for i in range(len(STEP_ORDER) - 1):
        graph.add_edge(STEP_ORDER[i], STEP_ORDER[i + 1])
    graph.add_edge(STEP_ORDER[-1], END)
    return graph.compile()

async def run_pipeline(
    tenant_cfg: TenantConfig,
    job_id: str,
    tenant_id: str,
    client_id: str,
    user_id: str,
    namespace: str,
    filename: str,
    s3_key: str,
    raw_bytes: bytes = b"",
    raw_storage_url: str = "",
    source_relative_path: str = "",
    source_root: str = "",
    metadata_hints: dict | None = None,
) -> PipelineState:
    guard = TokenGuard(tenant_cfg, user_id)
    compiled = build_pipeline(tenant_cfg, guard)
    initial: PipelineState = {
        "job_id": job_id,
        "tenant_id": tenant_id,
        "client_id": client_id,
        "user_id": user_id,
        "namespace": namespace,
        "filename": filename,
        "source_relative_path": source_relative_path or filename,
        "source_root": source_root,
        "s3_key": s3_key,
        "raw_storage_url": raw_storage_url,
        "raw_bytes": raw_bytes,
        "completed_steps": [],
        "errors": [],
        "current_step": "",
        "current_agent": "",
        "storage_targets": [],
        "skip_embedding": True,
        "artifact_urls": {},
        "metadata_hints": metadata_hints or {},
    }
    state = await compiled.ainvoke(initial)
    # Surface the run's LLM spend on the returned state. The guard is local to this
    # function, so without this the ingestion agents' token usage died here — and
    # since DIS calls Bedrock on its own client, that spend never reached CAS's
    # usage/budget accounting at all (see token_guard's module docstring: Studio owns
    # budget controls). try/except because reporting must never fail an ingestion that
    # already succeeded.
    try:
        state["llm_usage"] = guard.usage_summary()
    except Exception:                                  # pragma: no cover - defensive
        log.warning("could not summarise ingestion LLM usage", exc_info=True)
    return state
