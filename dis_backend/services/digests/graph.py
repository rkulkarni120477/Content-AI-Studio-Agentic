"""LangGraph ``Send`` fan-out for the MAP step (plan D4) + checkpointer seam (D7).

The block-wide digest build is a map-reduce: ENUMERATE (done upstream) → **MAP one
digest per day (fan-out)** → collect. This module expresses the MAP fan-out as a
LangGraph graph so it gets, over a plain loop:

* **per-day checkpoint/resume** — with a ``PostgresSaver`` (D7), a run that dies
  mid-block resumes only the unfinished days instead of redoing the block;
* **bounded concurrency** — ``max_concurrency`` caps simultaneous Bedrock calls so
  a 20-day block is a bounded worker pool, not a 20-way burst (§8.3);
* **failure isolation** — a day that errors is captured (``status='failed'``) and
  the block still completes; the reducer merges per-day results.

Design constraints honored here:
* **Same primitives as the sequential path** — the ``map_day`` node calls
  ``build.build_one_day``, and the cache decision uses ``build.day_is_cached``, so
  the graph and the loop can never diverge on what gets built or reported.
* **No shared-budget race** — each ``map_day`` returns its own token counts; the
  ``list`` reducer (``operator.add``) merges them in the collect step.
* **Checkpointer is injected, never constructed here.** Production passes a
  ``PostgresSaver``; tests/dev pass ``None`` (no checkpoint). This module opens no
  DB connection — important because dev containers point at prod RDS.
"""
from __future__ import annotations

import logging
import operator
from typing import Any, Dict, List, Optional
from typing_extensions import Annotated, TypedDict

from config.settings import TenantConfig
from services.digests import build as _build
from services.digests import progress

log = logging.getLogger(__name__)


def langgraph_available() -> bool:
    try:
        import langgraph  # noqa: F401
        from langgraph.graph import StateGraph  # noqa: F401
        from langgraph.constants import Send  # noqa: F401
        return True
    except Exception:
        return False


def make_checkpointer(dsn: str = ""):
    """D7 seam. Return a ``PostgresSaver`` for cross-process per-day resume when a
    DSN is configured AND ``langgraph-checkpoint-postgres`` is installed; otherwise
    ``None`` (in-process fan-out, no persisted resume).

    Deliberately opens NO connection when ``dsn`` is blank — dev containers point
    at prod RDS, so an accidental checkpointer must never connect there. Any import
    or setup error degrades to ``None`` with a warning rather than failing the build.
    """
    if not (dsn or "").strip():
        return None
    try:
        from langgraph.checkpoint.postgres import PostgresSaver
    except Exception:
        log.warning("digest_fanout_checkpoint_dsn set but langgraph-checkpoint-postgres "
                    "is not installed; running fan-out without a checkpointer")
        return None
    try:
        saver = PostgresSaver.from_conn_string(dsn)
        saver.setup()
        return saver
    except Exception as exc:  # noqa: BLE001 — never let checkpointer setup break the build
        log.warning("PostgresSaver setup failed (%s); running fan-out without a checkpointer", exc)
        return None


class _MapState(TypedDict, total=False):
    # Inputs (set once, read by fan-out).
    days_to_build: List[Dict[str, Any]]         # [{day, units}] — uncached days only
    cached_days: List[int]
    # Accumulated per-day outputs (merged across concurrent map_day nodes).
    results: Annotated[List[Dict[str, Any]], operator.add]


def build_digests_via_graph(
    tenant_cfg: TenantConfig,
    block: str,
    en: Any,                      # EnumerateResult
    model: str,
    existing: Dict[Any, Dict[str, Any]],
    checkpointer: Any = None,
    max_concurrency: int = 5,
    map_guidance: str = "",
) -> Dict[str, Any]:
    """Run the MAP fan-out as a LangGraph graph and return the same report shape as
    the sequential ``build_digests``. Assumes ``langgraph_available()`` (the caller
    checks and falls back otherwise)."""
    from langgraph.graph import END, START, StateGraph
    from langgraph.constants import Send

    # Partition days up front using the shared cache decision, so map_day only ever
    # runs for days that actually need (re)building.
    days_to_build: List[Dict[str, Any]] = []
    cached_days: List[int] = []
    for day in en.days:
        dn = day["day_number"]
        units = en.units_by_day.get(dn, [])
        refs = en.references_by_day.get(dn)
        if _build.day_is_cached(day, units, model, existing, force=False,
                                map_guidance=map_guidance, references=refs):
            cached_days.append(dn)
        else:
            days_to_build.append({"day": day, "units": units, "references": refs})

    client_id = en.client_id

    # Progress is reported here as well as in the sequential loop, because this is the
    # path AIM actually runs (digest_fanout_enabled). The cached days are known up
    # front, so they are recorded immediately — a retry that reuses 15 of 20 days
    # should jump to 15/20 at once rather than crawling, which is both honest and what
    # makes the remaining work legible.
    progress.start(client_id, block, total=len(en.days))
    for _dn in cached_days:
        progress.record(client_id, block, "cached")

    def map_day(state: Dict[str, Any]) -> Dict[str, Any]:
        """One MAP node = one day's digest (build + upsert). Reuses the shared
        per-day primitive; returns a single-item ``results`` list to be merged."""
        res = _build.build_one_day(
            tenant_cfg, state["day"], state["units"], model, client_id, block,
            map_guidance=map_guidance, references=state.get("references"),
        )
        # Runs on N worker threads concurrently; the registry takes a lock per call.
        progress.record(client_id, block, res["status"])
        return {"results": [res]}

    def fan_out(state: _MapState):
        """Dispatch one ``map_day`` per uncached day via ``Send`` (the fan-out).
        When there is nothing to build (all cached), skip straight to END so the
        graph still terminates cleanly."""
        sends = [Send("map_day", item) for item in state.get("days_to_build", [])]
        return sends or [END]

    graph = StateGraph(_MapState)
    graph.add_node("map_day", map_day)
    # Fan out from START directly to N map_day nodes; each merges its one result
    # into the ``results`` channel (operator.add) and ends. No collect node — a
    # pass-through that re-emits ``results`` would double-count via the reducer,
    # and one that emits nothing is rejected by langgraph. We read the accumulated
    # channel after invoke() instead.
    graph.add_conditional_edges(START, fan_out, ["map_day", END])
    graph.add_edge("map_day", END)

    compiled = graph.compile(checkpointer=checkpointer) if checkpointer else graph.compile()

    config: Dict[str, Any] = {"max_concurrency": max(1, int(max_concurrency))}
    if checkpointer is not None:
        # A checkpointer requires a thread id to key the run's state.
        config.setdefault("configurable", {})["thread_id"] = f"digest:{client_id}:{block}"

    init: _MapState = {"days_to_build": days_to_build, "cached_days": cached_days, "results": []}
    try:
        final = compiled.invoke(init, config=config)
    finally:
        # In a finally so a fan-out that raises still stops reporting itself in flight,
        # rather than leaving the UI on a bar that never completes.
        progress.finish(client_id, block)

    # Merge per-day results into the standard report (cached days re-added here).
    per_day: List[Dict[str, Any]] = [{"day_number": dn, "status": "cached"} for dn in cached_days]
    budget: Dict[str, int] = {"calls": 0, "tok_in": 0, "tok_out": 0}
    for res in final.get("results", []):
        for k in budget:
            budget[k] += (res.get("budget") or {}).get(k, 0)
        per_day.append({"day_number": res["day_number"], "status": res["status"], "error": res.get("error")})

    return _build._finalize_report(block, en, per_day, budget, strategy="langgraph_send",
                                  model=model)
