"""Digest build orchestration — lazy build + content-addressed cache (D3).

``build_digests`` enumerates a block, and for each day either reuses a fresh
cached digest (cache_key match) or (re)builds it via MAP and upserts it to the
digest store. ``digest_status`` reports coverage/freshness without building.

Two execution strategies, same signature/report, chosen by ``use_graph``:

* **sequential** (default) — a plain per-day loop; a block is ~20 days and
  Bedrock retries are handled in ``call_llm``. Simple, dependency-free, verified.
* **LangGraph ``Send`` fan-out** (D4, ``use_graph=True``) — dispatches one MAP
  task per uncached day as a real graph node for per-day checkpoint/resume,
  native retry and bounded concurrency (D7 ``PostgresSaver`` slots in via an
  injected checkpointer). Falls back to sequential if langgraph is unavailable.

Both strategies share the SAME per-day primitives (``day_is_cached`` /
``build_one_day``), so their reports are identical and the cache decision can
never diverge between them.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from config.settings import TenantConfig
from services import indexing
from services.digests import mapper
from services.digests.enumerate import enumerate_block

log = logging.getLogger(__name__)


def day_is_cached(day: Dict[str, Any], units: List[Dict[str, Any]], model: str,
                  existing: Dict[Any, Dict[str, Any]], force: bool, map_guidance: str = "") -> bool:
    """Whether a fresh, ok cached digest already covers this day (cache_key match).
    The single source of truth for the lazy-cache decision, shared by both the
    sequential loop and the fan-out graph. Uses the SAME cache_key inputs as
    build_digest (incl. the day topic/title signature and map_guidance) so the
    two never diverge."""
    if force:
        return False
    prev = existing.get(day["day_number"])
    if not prev:
        return False
    ck = mapper.cache_key(day["day_number"], units, model, day_meta=mapper.day_signature(day),
                          map_guidance=map_guidance)
    if prev.get("cache_key") != ck or prev.get("digest_status") != "ok":
        return False
    if not mapper.has_extraction(prev):
        # digest_status=="ok" is only as trustworthy as the code that wrote it. Digests
        # built before MAP failures were surfaced (see mapper.MapExtractionError) were
        # stored "ok" with every LLM field at its default, and a status-only check
        # reuses them forever — that is what shipped a 20-day Blueprint whose every
        # extracted cell was blank. Re-derive instead: one wasted MAP call is strictly
        # cheaper than an empty deliverable, and it self-heals the stored digest.
        log.warning("digest cache: day %s matches cache_key but carries no extraction "
                    "(status=ok, empty fields) — rebuilding", day["day_number"])
        return False
    return True


def build_one_day(tenant_cfg: TenantConfig, day: Dict[str, Any], units: List[Dict[str, Any]],
                  model: str, client_id: str, block: str, map_guidance: str = "") -> Dict[str, Any]:
    """Build + upsert one day's digest. Pure per-day: returns its OWN token counts
    under ``budget`` so concurrent fan-out has no shared-state race. Never raises —
    a MAP/store failure is captured as ``status='failed'``."""
    dn = day["day_number"]
    budget: Dict[str, int] = {"calls": 0, "tok_in": 0, "tok_out": 0}
    digest = mapper.build_digest(day, units, tenant_cfg, model=model,
                                 client_id=client_id, block=block, budget=budget,
                                 map_guidance=map_guidance)
    store_res = indexing.upsert_digest(tenant_cfg, digest)
    ok = digest.get("digest_status") == "ok" and store_res.get("status") == "completed"
    return {
        "day_number": dn,
        "status": "built" if ok else "failed",
        "error": None if ok else (digest.get("error") or store_res.get("error")),
        "budget": budget,
    }


def _finalize_report(block: str, en, per_day: List[Dict[str, Any]],
                     budget: Dict[str, int], strategy: str) -> Dict[str, Any]:
    built = sum(1 for p in per_day if p["status"] == "built")
    cached = sum(1 for p in per_day if p["status"] == "cached")
    failed = sum(1 for p in per_day if p["status"] == "failed")
    return {
        "block": block,
        "client_id": en.client_id,
        "days": en.enumerated_days,
        "built": built,
        "cached": cached,
        "failed": failed,
        "map_calls": budget["calls"],
        "map_tokens_in": budget["tok_in"],
        "map_tokens_out": budget["tok_out"],
        "strategy": strategy,
        "attribution": en.attribution,
        "flags": en.flags,
        "per_day": sorted(per_day, key=lambda p: p["day_number"]),
    }


def build_digests(tenant_cfg: TenantConfig, block: str, client_id: str = "",
                  force: bool = False,
                  use_graph: bool = False, checkpointer: Any = None,
                  max_concurrency: int = 5, map_guidance: str = "") -> Dict[str, Any]:
    """Build (or reuse) every day's digest for a block. Returns a build report.

    Digests are always built for the instructor audience (see mapper.build_digest).
    ``use_graph=True`` runs the D4 LangGraph ``Send`` fan-out (with optional D7
    ``checkpointer`` and a ``max_concurrency`` cap); otherwise the sequential loop.

    ``map_guidance`` (optional) is judgment/emphasis instructions distilled from
    the course's selected CDD/Blueprint prompt (see
    promptops_app.services.prompt_guidance.resolve_prompt_guidance on the CAS
    side) — threaded into every day's MAP call and into the cache-key decision
    so an edited prompt correctly busts stale cached digests. "" (the default)
    reproduces this function's exact pre-existing behavior.
    """
    en = enumerate_block(tenant_cfg, block, client_id)
    model = tenant_cfg.pipeline.models.digest_extraction

    if force:
        indexing.delete_digests(tenant_cfg, block, en.client_id)
        existing: Dict[Any, Dict[str, Any]] = {}
    else:
        existing = {d.get("day_number"): d for d in indexing.fetch_digests(tenant_cfg, block, en.client_id)}

    # Create the OpenSearch index up front so a cold-block fan-out doesn't race N
    # concurrent create-index calls (TOCTOU) and spuriously fail the losers.
    _ensure_digest_index(tenant_cfg)

    if use_graph:
        from services.digests.graph import build_digests_via_graph, langgraph_available
        if langgraph_available():
            return build_digests_via_graph(
                tenant_cfg, block, en, model, existing,
                checkpointer=checkpointer, max_concurrency=max_concurrency,
                map_guidance=map_guidance,
            )
        log.warning("use_graph=True but langgraph is unavailable; falling back to sequential build")

    budget: Dict[str, int] = {"calls": 0, "tok_in": 0, "tok_out": 0}
    per_day: List[Dict[str, Any]] = []
    for day in en.days:
        dn = day["day_number"]
        units = en.units_by_day.get(dn, [])
        if day_is_cached(day, units, model, existing, force, map_guidance=map_guidance):
            per_day.append({"day_number": dn, "status": "cached"})
            continue
        res = build_one_day(tenant_cfg, day, units, model, en.client_id, block, map_guidance=map_guidance)
        for k in budget:
            budget[k] += res["budget"].get(k, 0)
        per_day.append({"day_number": dn, "status": res["status"], "error": res.get("error")})

    return _finalize_report(block, en, per_day, budget, strategy="sequential")


def _ensure_digest_index(tenant_cfg: TenantConfig) -> None:
    """Best-effort pre-creation of the digest index (idempotent) so concurrent
    fan-out writers don't race index creation. Never raises — upsert_digest still
    calls ensure_index defensively."""
    try:
        cfg = tenant_cfg.vector_store
        if not getattr(cfg, "enabled", False):
            return
        client = indexing._vector_store_read_client(cfg)
        indexing.ensure_index(client, cfg.index_name, tenant_cfg.embedding.dimension)
    except Exception as exc:  # noqa: BLE001 — pre-warm only; real create is retried per-day
        log.debug("digest index pre-create skipped: %s", exc)


def context_bundle(tenant_cfg: TenantConfig, block: str, client_id: str = "") -> Dict[str, Any]:
    """Everything the app-side REDUCE needs in one payload: the enumerate summary
    (days, declared ACS, flags), the persisted digest bodies, and the block-level
    worksheet aggregates (overview / source inventory / ACS registry). Read-only —
    does NOT build; call build_digests first to ensure freshness."""
    from services.digests import worksheets

    en = enumerate_block(tenant_cfg, block, client_id)
    digests = indexing.fetch_digests(tenant_cfg, block, en.client_id)
    digests = sorted(digests, key=lambda d: d.get("day_number") or 0)

    schema = tenant_cfg.structure_store.schema_name
    block_overview = None
    source_file_inventory = None
    acs_registry = None
    try:
        import psycopg
        from psycopg.rows import dict_row
        from services.digests.enumerate import _resolve_dsn
        with psycopg.connect(_resolve_dsn(tenant_cfg.structure_store), row_factory=dict_row) as conn:
            conn.read_only = True
            with conn.cursor() as cur:
                block_overview = worksheets.build_block_overview(en, cur, schema)
                source_file_inventory = worksheets.build_source_file_inventory(en, tenant_cfg, cur, schema)
                acs_registry = worksheets.build_acs_registry(en, cur, schema)
    except Exception as exc:  # best-effort; the overview must never sink the bundle
        log.warning("block_overview build failed for block=%s: %s", block, exc)
        block_overview = worksheets.build_block_overview(en, cur=None, schema=schema)
        source_file_inventory = worksheets.build_source_file_inventory(en, tenant_cfg)
        acs_registry = worksheets.build_acs_registry(en)

    return {
        "block": block,
        "client_id": en.client_id,
        "enumerate": en.to_summary(include_units=False),
        "digests": digests,
        "block_overview": block_overview,
        "source_file_inventory": source_file_inventory,
        "acs_registry": acs_registry,
    }


def digest_status(tenant_cfg: TenantConfig, block: str, client_id: str = "") -> Dict[str, Any]:
    """Coverage + freshness of the digest store for a block, without building.

    A day is ``stale`` when a digest exists but its cache_key no longer matches the
    current source units (content/model/prompt drift); ``missing`` when absent;
    ``failed`` when the stored digest recorded a MAP failure.

    KNOWN LIMITATION: this computes cache_key with map_guidance="" (no way to know
    which course prompt, if any, a FUTURE build_digests_sync call will resolve
    guidance from — that's request-time state, not a stored block property). A day
    actually built with non-empty guidance will therefore report "stale" here even
    though a rebuild with the SAME guidance would reproduce it exactly; conversely a
    day built with no guidance reports "fresh" even if a differently-configured
    future rebuild would change it. Freshness w.r.t. guidance is only ever decided
    correctly by day_is_cached() at actual build time, which does receive it. Not
    currently called by any CAS-side or frontend code (diagnostics-only today) —
    fix properly (thread a resolved map_guidance in) before wiring this up to
    anything guidance-sensitive.
    """
    en = enumerate_block(tenant_cfg, block, client_id)
    model = tenant_cfg.pipeline.models.digest_extraction
    existing = {d.get("day_number"): d for d in indexing.fetch_digests(tenant_cfg, block, en.client_id)}

    fresh = stale = missing = failed = 0
    per_day: list[Dict[str, Any]] = []
    for day in en.days:
        dn = day["day_number"]
        units = en.units_by_day.get(dn, [])
        ck = mapper.cache_key(dn, units, model, day_meta=mapper.day_signature(day))
        prev = existing.get(dn)
        if prev is None:
            state = "missing"; missing += 1
        elif prev.get("digest_status") != "ok":
            state = "failed"; failed += 1
        elif prev.get("cache_key") != ck:
            state = "stale"; stale += 1
        else:
            state = "fresh"; fresh += 1
        per_day.append({"day_number": dn, "state": state})

    return {
        "block": block,
        "client_id": en.client_id,
        "days": en.enumerated_days,
        "fresh": fresh,
        "stale": stale,
        "missing": missing,
        "failed": failed,
        "up_to_date": stale == 0 and missing == 0 and failed == 0,
        "flags": en.flags,
        "per_day": per_day,
    }
