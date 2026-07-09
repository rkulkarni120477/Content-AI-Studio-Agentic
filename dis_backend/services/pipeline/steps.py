"""Deprecated compatibility module.

The final DIS pipeline is agent-based. Use services/agents/*_agent.py for step logic
and services/pipeline/graph.py for LangGraph orchestration.
"""
from services.agents.registry import STEP_ORDER, AGENT_REGISTRY, get_agent

def get_step(name: str):
    raise RuntimeError("Function-based steps are removed. Use services.agents.<step>_agent instead.")
