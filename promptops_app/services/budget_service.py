"""Budget/quota enforcement — P2 of claude_plan_platform_hardening.

Two spend-tracking paths, deliberately separate:
  - `current_period_spend()` — a plain SUM() over LLMUsageLog. Historical/
    dashboard view (P3). Not what enforcement checks — see P2.2's note in the
    plan: only add a materialized rollup if this is ever measured slow.
  - `budget_period_spend` (the table) — the race-safe running total
    `check_budget()`/`reconcile_budget()` reserve against via one atomic
    UPDATE per call. This is enforcement's actual source of truth.

Reserve strategy (resolved 2026-08-05, flagged for sign-off per the plan's own
"OPEN DESIGN QUESTION" note — a real cost is unknowable before the provider
responds): reserve a WORST-CASE estimate before the call (input tokens counted
from the prompt + the request's own max_tokens ceiling as the output-token
assumption), then reconcile down to the actual cost afterward. This is what
makes the atomic reserve genuinely race-safe — a check-then-log approach
without a real pre-call reservation reopens the exact race P2.9 exists to close.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text

from promptops_app.core.config import settings as _cfg
from promptops_app.services.usage_service import estimate_cost

_log = logging.getLogger(__name__)


class BudgetExceededError(Exception):
    """A project/course/user has exceeded its configured LLM budget.

    Deliberately a plain Exception, not an app.core.exceptions.AppError subclass
    — promptops_app has no existing dependency on the app/ (FastAPI) layer
    anywhere, and this is the choke point (llm_client.py), one layer below it.
    app/main.py registers its own handler for this exact type instead, mapping
    it to HTTP 402 (429 stays reserved for P5's separate rate limiting).
    """

    def __init__(self, scope: str, scope_id: str, limit_usd: float, current_spend: float):
        self.scope = scope
        self.scope_id = scope_id
        self.limit_usd = limit_usd
        self.current_spend = current_spend
        super().__init__(
            f"{scope.capitalize()} budget exceeded: ${current_spend:.2f} of "
            f"${limit_usd:.2f} used this period."
        )

# Matches the max_tokens already hardcoded in llm_client.py's request bodies —
# the true worst case a single call can actually request from the provider.
WORST_CASE_OUTPUT_TOKENS = 16384

# ponytail: characters/4 is a rough token-count heuristic, not a real tokenizer
# (P6.1 owns fixing this properly across the codebase — out of scope for P2).
# Good enough for a worst-case reservation ceiling; being a little generous
# here only means reserving slightly more than strictly needed, never less.
_CHARS_PER_TOKEN_ESTIMATE = 4


def _estimate_input_tokens(system_prompt: str, user_prompt: str) -> int:
    return (len(system_prompt or "") + len(user_prompt or "")) // _CHARS_PER_TOKEN_ESTIMATE


def period_key(period: str, now: Optional[datetime] = None) -> str:
    """The bucket a call's spend is reserved against.

    "monthly": calendar month, e.g. "2026-08" — genuinely race-safe, one bucket
    per month, atomic reserve applies cleanly.

    ponytail: "rolling" also buckets by calendar month today, not a true
    30-day sliding window — a real sliding-window ledger needs a per-day
    bucket structure (sum the trailing 30 daily buckets) to stay atomic, which
    is real complexity for an option that may never get configured. Upgrade
    path: switch to daily buckets + a 30-row SUM if "rolling" sees real usage.
    current_period_spend() below still reports a true trailing-30-day figure
    for the dashboard; only the enforcement bucket is approximated.
    """
    now = now or datetime.now(timezone.utc)
    return f"{now.year:04d}-{now.month:02d}"


def current_period_usage(db, scope: str, scope_id: str, period: str, now: Optional[datetime] = None) -> tuple[float, int]:
    """Historical spend + tokens for dashboard/reporting — NOT the enforcement
    path (see module docstring). One query, since a caller wanting tokens
    almost always wants spend too."""
    now = now or datetime.now(timezone.utc)
    column = {"project": "project_id", "course": "course_id", "user": "user_id"}[scope]
    if period == "rolling":
        window_start = now - timedelta(days=30)
    else:
        window_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    row = db.execute(
        text(f"""
            SELECT COALESCE(SUM(estimated_cost), 0), COALESCE(SUM(total_tokens), 0)
            FROM llm_usage_logs
            WHERE {column} = :scope_id AND created_at >= :window_start
        """),
        {"scope_id": scope_id, "window_start": window_start},
    ).first()
    return float(row[0] or 0.0), int(row[1] or 0)


def current_period_spend(db, scope: str, scope_id: str, period: str, now: Optional[datetime] = None) -> float:
    """Spend-only convenience wrapper around current_period_usage()."""
    spend, _tokens = current_period_usage(db, scope, scope_id, period, now)
    return spend


@dataclass
class BudgetReservation:
    """What check_budget hands reconcile_budget after the call completes."""
    scope: str
    scope_id: str
    period_key: str
    reserved_usd: float


def _get_policy(db, scope: str, scope_id: str):
    from promptops_app.database import BudgetPolicy
    return db.query(BudgetPolicy).filter(
        BudgetPolicy.scope == scope, BudgetPolicy.scope_id == str(scope_id),
    ).first()


def _reserve(db, *, scope: str, scope_id: str, pkey: str, cost_usd: float, limit_usd: float) -> bool:
    """Atomic check-and-reserve. Returns True if reserved, False if it would breach the limit.

    Two statements, same transaction: seed the bucket at 0 if it doesn't exist
    yet (ON CONFLICT DO NOTHING), THEN the guarded increment. Without the seed
    step, a fresh period's very first call would hit the INSERT branch of a
    single combined upsert unconditionally — no WHERE guard applies to a plain
    INSERT, so a first call whose own cost exceeds the limit would slip through.
    """
    db.execute(
        text("""
            INSERT INTO budget_period_spend (scope, scope_id, period_key, spent_usd, updated_at)
            VALUES (:scope, :scope_id, :period_key, 0, :now)
            ON CONFLICT (scope, scope_id, period_key) DO NOTHING
        """),
        {"scope": scope, "scope_id": scope_id, "period_key": pkey, "now": datetime.now(timezone.utc)},
    )
    result = db.execute(
        text("""
            UPDATE budget_period_spend
            SET spent_usd = spent_usd + :cost, updated_at = :now
            WHERE scope = :scope AND scope_id = :scope_id AND period_key = :period_key
              AND spent_usd + :cost <= :limit_usd
            RETURNING spent_usd
        """),
        {
            "scope": scope, "scope_id": scope_id, "period_key": pkey,
            "cost": cost_usd, "limit_usd": limit_usd, "now": datetime.now(timezone.utc),
        },
    )
    reserved = result.first() is not None
    db.commit()
    return reserved


def _reserve_unconditional(db, *, scope: str, scope_id: str, pkey: str, cost_usd: float) -> float:
    """Always increments, never blocks — dry-run mode's accounting path.

    Dry-run means "don't block", not "don't track": the real call happens
    either way, so spend must be recorded for real even past a configured
    limit, or the running total silently under-counts once a limit is
    nominally hit. Returns the new total so the caller can log/warn without
    a second query.
    """
    result = db.execute(
        text("""
            INSERT INTO budget_period_spend (scope, scope_id, period_key, spent_usd, updated_at)
            VALUES (:scope, :scope_id, :period_key, :cost, :now)
            ON CONFLICT (scope, scope_id, period_key) DO UPDATE
              SET spent_usd = budget_period_spend.spent_usd + :cost, updated_at = :now
            RETURNING spent_usd
        """),
        {"scope": scope, "scope_id": scope_id, "period_key": pkey, "cost": cost_usd, "now": datetime.now(timezone.utc)},
    )
    row = result.first()
    db.commit()
    return float(row[0]) if row else cost_usd


def reconcile_budget(db, reservation: BudgetReservation, actual_cost_usd: float) -> None:
    """Adjust a reservation down (or up) to the real cost. Call in a `finally` —
    must run even when the LLM call fails, or a failed call permanently leaks
    its worst-case reservation until the period rolls over.
    """
    try:
        delta = actual_cost_usd - reservation.reserved_usd
        if delta == 0:
            return
        db.execute(
            text("""
                UPDATE budget_period_spend
                SET spent_usd = spent_usd + :delta, updated_at = :now
                WHERE scope = :scope AND scope_id = :scope_id AND period_key = :period_key
            """),
            {
                "scope": reservation.scope, "scope_id": reservation.scope_id,
                "period_key": reservation.period_key, "delta": delta,
                "now": datetime.now(timezone.utc),
            },
        )
        db.commit()
    except Exception as exc:
        _log.error("Budget reconciliation failed (reservation may over-count until period rolls over): %s", exc)
        try:
            db.rollback()
        except Exception:
            pass


def _live_flag(key: str, default: str) -> str:
    """Re-read a single var directly from .env, bypassing the cached pydantic
    settings singleton (loaded once at import time) — this is what lets P2.6's
    mode and P2.10's kill switch flip live with no deploy/restart, matching
    this repo's existing `.:/app` bind-mount deployment (same file, same repo
    root, in both local dev and the production EC2 host per backend-deploy.yml).
    """
    from dotenv import dotenv_values
    return dotenv_values(".env").get(key, default) or default


def enforcement_mode() -> str:
    """"dry_run" (default — safe) or "enforce". Flip via BUDGET_ENFORCEMENT_MODE in .env."""
    return _live_flag("BUDGET_ENFORCEMENT_MODE", "dry_run").strip().lower()


def enforcement_killswitch_active() -> bool:
    """A full bypass, independent of dry-run/enforce — flip via
    BUDGET_ENFORCEMENT_KILLSWITCH=true in .env with no deploy needed."""
    return _live_flag("BUDGET_ENFORCEMENT_KILLSWITCH", "false").strip().lower() in ("true", "1", "yes")


@dataclass
class BudgetCheckResult:
    """What check_budget hands back to the caller for later reconciliation."""
    reservations: list  # list[BudgetReservation] — only levels actually reserved against
    warnings: list       # list[dict] — levels that crossed warn_threshold_pct this call


def _current_total(db, scope: str, scope_id: str, pkey: str) -> float:
    row = db.execute(
        text("SELECT spent_usd FROM budget_period_spend WHERE scope=:s AND scope_id=:i AND period_key=:p"),
        {"s": scope, "i": scope_id, "p": pkey},
    ).first()
    return float(row[0]) if row else 0.0


def _levels_for(usage_ctx) -> list[tuple[str, str]]:
    levels = []
    if usage_ctx is None:
        return levels
    if getattr(usage_ctx, "project_id", None) is not None:
        levels.append(("project", str(usage_ctx.project_id)))
    if getattr(usage_ctx, "course_id", None) is not None:
        levels.append(("course", str(usage_ctx.course_id)))
    if getattr(usage_ctx, "user_name", None):
        levels.append(("user", usage_ctx.user_name))
    return levels


def check_budget(db, usage_ctx, *, system_prompt: str, user_prompt: str, model: str) -> BudgetCheckResult:
    """Pre-flight quota check — the P2.3 choke point, called from
    _call_openai_raw/_call_bedrock_raw before the provider request.

    Checks project/course/user independently (P2.8): the call is blocked if
    ANY configured level would breach; "most-restrictive-wins" governs only
    which level is reported in the raised error (smallest remaining margin),
    not whether a less-restrictive breach gets ignored.

    Fails OPEN on any internal error (DB unavailable, bug in this function) —
    logs loudly, lets the call through. Only a confirmed breach raises
    BudgetExceededError. This is a locked decision (PLAN.md §0): a bug in this
    code must never take down 100% of LLM functionality platform-wide.
    """
    if enforcement_killswitch_active():
        return BudgetCheckResult(reservations=[], warnings=[])

    try:
        levels = _levels_for(usage_ctx)
        if not levels:
            return BudgetCheckResult(reservations=[], warnings=[])

        worst_case_cost = estimate_cost(model, _estimate_input_tokens(system_prompt, user_prompt), WORST_CASE_OUTPUT_TOKENS)
        dry_run = enforcement_mode() != "enforce"

        reservations: list[BudgetReservation] = []
        warnings: list[dict] = []
        breaches: list[tuple[str, str, float, float, float]] = []  # scope, scope_id, limit, spend, margin

        for scope, scope_id in levels:
            policy = _get_policy(db, scope, scope_id)
            if policy is None:
                continue  # no policy configured at this level — unrestricted
            pkey = period_key(policy.period)

            if dry_run:
                # Real call happens regardless — track real spend, never block.
                new_total = _reserve_unconditional(db, scope=scope, scope_id=scope_id, pkey=pkey, cost_usd=worst_case_cost)
                reservations.append(BudgetReservation(scope, scope_id, pkey, worst_case_cost))
                if new_total > policy.limit_usd:
                    _log.warning(
                        "BUDGET_DRY_RUN would_block scope=%s scope_id=%s spend=%.4f limit=%.4f",
                        scope, scope_id, new_total, policy.limit_usd,
                    )
                elif policy.limit_usd > 0 and (new_total / policy.limit_usd * 100) >= policy.warn_threshold_pct:
                    if policy.last_warned_period != pkey:
                        policy.last_warned_period = pkey
                        db.commit()
                        warnings.append({"scope": scope, "scope_id": scope_id, "current_spend": new_total, "limit_usd": policy.limit_usd})
                continue

            reserved = _reserve(db, scope=scope, scope_id=scope_id, pkey=pkey, cost_usd=worst_case_cost, limit_usd=policy.limit_usd)
            if reserved:
                reservations.append(BudgetReservation(scope, scope_id, pkey, worst_case_cost))
                current_total = _current_total(db, scope, scope_id, pkey)
                if policy.limit_usd > 0 and (current_total / policy.limit_usd * 100) >= policy.warn_threshold_pct:
                    if policy.last_warned_period != pkey:
                        policy.last_warned_period = pkey
                        db.commit()
                        warnings.append({"scope": scope, "scope_id": scope_id, "current_spend": current_total, "limit_usd": policy.limit_usd})
            else:
                current_total = _current_total(db, scope, scope_id, pkey)
                margin = policy.limit_usd - current_total
                breaches.append((scope, scope_id, policy.limit_usd, current_total, margin))

        if breaches:
            # The call as a whole is blocked — release any reservations already
            # made at OTHER levels this same attempt, since no real cost happens.
            for r in reservations:
                reconcile_budget(db, r, actual_cost_usd=0.0)
            breaches.sort(key=lambda b: b[4])  # smallest margin = most restrictive
            scope, scope_id, limit_usd, current_spend, _ = breaches[0]
            raise BudgetExceededError(scope, scope_id, limit_usd, current_spend)

        return BudgetCheckResult(reservations=reservations, warnings=warnings)

    except BudgetExceededError:
        raise
    except Exception as exc:
        _log.error("Budget check failed internally — failing OPEN, call proceeds: %s", exc)
        return BudgetCheckResult(reservations=[], warnings=[])


def check_budget_autocommit(usage_ctx, *, system_prompt: str, user_prompt: str, model: str) -> BudgetCheckResult:
    """check_budget() for callers with no open DB session (llm_client.py's raw
    functions) — mirrors usage_service.log_llm_usage_autocommit's pattern.
    """
    from promptops_app.database import SessionLocal
    db = SessionLocal()
    try:
        return check_budget(db, usage_ctx, system_prompt=system_prompt, user_prompt=user_prompt, model=model)
    finally:
        db.close()


def reconcile_budget_autocommit(reservations: list, actual_cost_usd: float) -> None:
    """reconcile_budget() for all of a call's reservations at once, own session."""
    if not reservations:
        return
    from promptops_app.database import SessionLocal
    db = SessionLocal()
    try:
        for reservation in reservations:
            reconcile_budget(db, reservation, actual_cost_usd)
    finally:
        db.close()


def build_usage_summary(db, usage_ctx, entity_type: str, entity_id: str) -> Optional[dict]:
    """Assemble a post-call summary for the frontend: what this one call cost
    (re-read from the LLMUsageLog row that logging just wrote), plus remaining
    headroom for every scope (project/course/user) that has a BudgetPolicy —
    reuses the exact same levels check_budget() enforces against, so what the
    user sees here always matches what would actually block them.

    Returns None if no usage row is found (e.g. logging itself failed — see
    usage_service.log_llm_usage's own fail-safe note) so callers can simply
    omit the summary rather than show zeros.
    """
    from promptops_app.database import LLMUsageLog

    usage_row = (
        db.query(LLMUsageLog)
        .filter(LLMUsageLog.entity_type == entity_type, LLMUsageLog.entity_id == str(entity_id))
        .order_by(LLMUsageLog.id.desc())
        .first()
    )
    if usage_row is None:
        return None

    budgets = []
    for scope, scope_id in _levels_for(usage_ctx):
        policy = _get_policy(db, scope, scope_id)
        if policy is None:
            continue
        spent = current_period_spend(db, scope, scope_id, policy.period)
        budgets.append({
            "scope": scope, "scope_id": scope_id,
            "limit_usd": policy.limit_usd, "spent_usd": round(spent, 4),
            "remaining_usd": round(policy.limit_usd - spent, 4),
        })

    return {
        "cost_usd": round(usage_row.estimated_cost or 0.0, 4),
        "input_tokens": usage_row.input_tokens or 0,
        "output_tokens": usage_row.output_tokens or 0,
        "total_tokens": usage_row.total_tokens or 0,
        "budgets": budgets,
    }
