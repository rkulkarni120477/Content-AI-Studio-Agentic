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

    def __init__(self, scope: str, scope_id: str, limit_usd: float, current_spend: float, limit_type: str = "usd"):
        self.scope = scope
        self.scope_id = scope_id
        # Named *_usd for backward compat (app/main.py's handler and existing
        # tests read these attributes directly) — for a token-capped breach
        # these hold token counts, not dollars; check limit_type to know which.
        self.limit_usd = limit_usd
        self.current_spend = current_spend
        self.limit_type = limit_type
        if limit_type == "tokens":
            msg = (
                f"{scope.capitalize()} token limit exceeded: {int(current_spend):,} of "
                f"{int(limit_usd):,} tokens used this period."
            )
        else:
            msg = (
                f"{scope.capitalize()} budget exceeded: ${current_spend:.2f} of "
                f"${limit_usd:.2f} used this period."
            )
        super().__init__(msg)

# Matches llm_client.py's own DEFAULT_MAX_OUTPUT_TOKENS — the worst case for a
# call that doesn't override its output cap. Callers that DO pass a bigger
# max_tokens (e.g. block-wide reduce's up-to-32000 catalog ceiling) forward it
# into check_budget() below instead of relying on this flat floor, so the
# reservation stays a true worst case rather than under-reserving by up to 2x
# on the single largest spender — exactly where race-safety matters most.
WORST_CASE_OUTPUT_TOKENS = 16384

# ponytail: characters/4 is a rough token-count heuristic, not a real tokenizer
# (P6.1 owns fixing this properly across the codebase — out of scope for P2).
# Good enough for a worst-case reservation ceiling; being a little generous
# here only means reserving slightly more than strictly needed, never less.
_CHARS_PER_TOKEN_ESTIMATE = 4


def _estimate_input_tokens(system_prompt: str, user_prompt: str) -> int:
    return (len(system_prompt or "") + len(user_prompt or "")) // _CHARS_PER_TOKEN_ESTIMATE


def _worst_case_tokens(system_prompt: str, user_prompt: str, max_tokens: Optional[int] = None,
                       input_tokens: Optional[int] = None,
                       output_tokens: Optional[int] = None) -> int:
    """Worst-case total tokens for one call, from explicit estimates when the caller
    has them (see check_budget's estimated_* params) else from prompt lengths."""
    est_in = input_tokens if input_tokens is not None else _estimate_input_tokens(system_prompt, user_prompt)
    return est_in + (output_tokens or max_tokens or WORST_CASE_OUTPUT_TOKENS)


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
    """What check_budget hands reconcile_budget after the call completes.

    Both cost and token worst-case estimates are always carried, regardless
    of which one the scope's policy actually enforces — budget_period_spend
    tracks both columns unconditionally (see reconcile_budget), so the
    dashboard always has real numbers for either axis.
    """
    scope: str
    scope_id: str
    period_key: str
    reserved_usd: float
    reserved_tokens: int = 0


def _get_policy(db, scope: str, scope_id: str):
    from promptops_app.database import BudgetPolicy
    return db.query(BudgetPolicy).filter(
        BudgetPolicy.scope == scope, BudgetPolicy.scope_id == str(scope_id),
    ).first()


def _reserve(db, *, scope: str, scope_id: str, pkey: str, cost_usd: float, tokens: int, policy) -> bool:
    """Atomic check-and-reserve. Returns True if reserved, False if it would breach the limit.

    Two statements, same transaction: seed the bucket at 0 if it doesn't exist
    yet (ON CONFLICT DO NOTHING), THEN the guarded increment. Without the seed
    step, a fresh period's very first call would hit the INSERT branch of a
    single combined upsert unconditionally — no WHERE guard applies to a plain
    INSERT, so a first call whose own cost exceeds the limit would slip through.

    Both spent_usd and spent_tokens are incremented unconditionally in the same
    UPDATE — only the WHERE guard's column depends on the policy's limit_type,
    since that's the only one actually enforced. guard_col is one of two fixed
    internal strings, never user input, so the f-string is not an injection risk.
    """
    db.execute(
        text("""
            INSERT INTO budget_period_spend (scope, scope_id, period_key, spent_usd, spent_tokens, updated_at)
            VALUES (:scope, :scope_id, :period_key, 0, 0, :now)
            ON CONFLICT (scope, scope_id, period_key) DO NOTHING
        """),
        {"scope": scope, "scope_id": scope_id, "period_key": pkey, "now": datetime.now(timezone.utc)},
    )
    if policy.limit_type == "tokens":
        guard_col, limit_val = "spent_tokens", policy.limit_tokens
    else:
        guard_col, limit_val = "spent_usd", policy.limit_usd
    result = db.execute(
        text(f"""
            UPDATE budget_period_spend
            SET spent_usd = spent_usd + :cost, spent_tokens = spent_tokens + :tokens, updated_at = :now
            WHERE scope = :scope AND scope_id = :scope_id AND period_key = :period_key
              AND {guard_col} + :guard_amount <= :limit_val
            RETURNING spent_usd, spent_tokens
        """),
        {
            "scope": scope, "scope_id": scope_id, "period_key": pkey,
            "cost": cost_usd, "tokens": tokens,
            "guard_amount": tokens if policy.limit_type == "tokens" else cost_usd,
            "limit_val": limit_val, "now": datetime.now(timezone.utc),
        },
    )
    reserved = result.first() is not None
    db.commit()
    return reserved


def _reserve_unconditional(db, *, scope: str, scope_id: str, pkey: str, cost_usd: float, tokens: int) -> tuple[float, int]:
    """Always increments, never blocks — dry-run mode's accounting path.

    Dry-run means "don't block", not "don't track": the real call happens
    either way, so spend must be recorded for real even past a configured
    limit, or the running total silently under-counts once a limit is
    nominally hit. Returns (new_spent_usd, new_spent_tokens) so the caller can
    log/warn without a second query.
    """
    result = db.execute(
        text("""
            INSERT INTO budget_period_spend (scope, scope_id, period_key, spent_usd, spent_tokens, updated_at)
            VALUES (:scope, :scope_id, :period_key, :cost, :tokens, :now)
            ON CONFLICT (scope, scope_id, period_key) DO UPDATE
              SET spent_usd = budget_period_spend.spent_usd + :cost,
                  spent_tokens = budget_period_spend.spent_tokens + :tokens,
                  updated_at = :now
            RETURNING spent_usd, spent_tokens
        """),
        {"scope": scope, "scope_id": scope_id, "period_key": pkey, "cost": cost_usd, "tokens": tokens, "now": datetime.now(timezone.utc)},
    )
    row = result.first()
    db.commit()
    return (float(row[0]), int(row[1])) if row else (cost_usd, tokens)


def reconcile_budget(db, reservation: BudgetReservation, actual_cost_usd: float, actual_tokens: int = 0) -> None:
    """Adjust a reservation down (or up) to the real cost/tokens. Call in a
    `finally` — must run even when the LLM call fails, or a failed call
    permanently leaks its worst-case reservation until the period rolls over.
    """
    try:
        delta_cost = actual_cost_usd - reservation.reserved_usd
        delta_tokens = actual_tokens - reservation.reserved_tokens
        if delta_cost == 0 and delta_tokens == 0:
            return
        db.execute(
            text("""
                UPDATE budget_period_spend
                SET spent_usd = spent_usd + :delta_cost, spent_tokens = spent_tokens + :delta_tokens, updated_at = :now
                WHERE scope = :scope AND scope_id = :scope_id AND period_key = :period_key
            """),
            {
                "scope": reservation.scope, "scope_id": reservation.scope_id,
                "period_key": reservation.period_key,
                "delta_cost": delta_cost, "delta_tokens": delta_tokens,
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


def _current_total(db, scope: str, scope_id: str, pkey: str, limit_type: str = "usd") -> float:
    column = "spent_tokens" if limit_type == "tokens" else "spent_usd"
    row = db.execute(
        text(f"SELECT {column} FROM budget_period_spend WHERE scope=:s AND scope_id=:i AND period_key=:p"),
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


def check_budget(db, usage_ctx, *, system_prompt: str, user_prompt: str, model: str,
                  max_tokens: Optional[int] = None,
                  estimated_input_tokens: Optional[int] = None,
                  estimated_output_tokens: Optional[int] = None) -> BudgetCheckResult:
    """Pre-flight quota check — the P2.3 choke point, called from
    _call_openai_raw/_call_bedrock_raw before the provider request.

    ``estimated_input_tokens`` / ``estimated_output_tokens`` let a caller that knows
    the SCALE of the work but not the individual prompts reserve honestly. The
    block-wide digest MAP stage is the case that needs it: it runs N per-day calls
    inside DIS, on DIS's own Bedrock client, so no single prompt pair exists on this
    side to size the reservation from. Omitted (the default) ⇒ estimate from the
    prompt lengths exactly as before, so every existing caller is unchanged.

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

    # Declared before the try so the except block can always release whatever
    # got reserved before a later level threw — even if the exception happens
    # before this point (e.g. in _levels_for/estimate_cost), an empty list is
    # still a no-op release, not a NameError that would mask the real failure.
    reservations: list[BudgetReservation] = []

    try:
        levels = _levels_for(usage_ctx)
        if not levels:
            return BudgetCheckResult(reservations=[], warnings=[])

        worst_case_output = estimated_output_tokens or max_tokens or WORST_CASE_OUTPUT_TOKENS
        worst_case_input = (estimated_input_tokens
                            if estimated_input_tokens is not None
                            else _estimate_input_tokens(system_prompt, user_prompt))
        worst_case_cost = estimate_cost(model, worst_case_input, worst_case_output)
        worst_case_tokens = _worst_case_tokens(
            system_prompt, user_prompt, max_tokens,
            input_tokens=worst_case_input, output_tokens=worst_case_output)
        dry_run = enforcement_mode() != "enforce"

        warnings: list[dict] = []
        breaches: list[tuple[str, str, float, float, float, str]] = []  # scope, scope_id, limit, spend, margin, limit_type

        for scope, scope_id in levels:
            policy = _get_policy(db, scope, scope_id)
            if policy is None:
                continue  # no policy configured at this level — unrestricted
            pkey = period_key(policy.period)
            limit_type = policy.limit_type or "usd"
            limit_value = policy.limit_tokens if limit_type == "tokens" else policy.limit_usd

            if dry_run:
                # Real call happens regardless — track real spend, never block.
                new_usd, new_tokens = _reserve_unconditional(
                    db, scope=scope, scope_id=scope_id, pkey=pkey, cost_usd=worst_case_cost, tokens=worst_case_tokens,
                )
                new_total = new_tokens if limit_type == "tokens" else new_usd
                reservations.append(BudgetReservation(scope, scope_id, pkey, worst_case_cost, worst_case_tokens))
                if limit_value and new_total > limit_value:
                    _log.warning(
                        "BUDGET_DRY_RUN would_block scope=%s scope_id=%s limit_type=%s spend=%.4f limit=%.4f",
                        scope, scope_id, limit_type, new_total, limit_value,
                    )
                elif limit_value and (new_total / limit_value * 100) >= policy.warn_threshold_pct:
                    if policy.last_warned_period != pkey:
                        policy.last_warned_period = pkey
                        db.commit()
                        warnings.append({"scope": scope, "scope_id": scope_id, "current_spend": new_total, "limit_usd": limit_value, "limit_type": limit_type})
                continue

            reserved = _reserve(db, scope=scope, scope_id=scope_id, pkey=pkey, cost_usd=worst_case_cost, tokens=worst_case_tokens, policy=policy)
            if reserved:
                reservations.append(BudgetReservation(scope, scope_id, pkey, worst_case_cost, worst_case_tokens))
                current_total = _current_total(db, scope, scope_id, pkey, limit_type)
                if limit_value and (current_total / limit_value * 100) >= policy.warn_threshold_pct:
                    if policy.last_warned_period != pkey:
                        policy.last_warned_period = pkey
                        db.commit()
                        warnings.append({"scope": scope, "scope_id": scope_id, "current_spend": current_total, "limit_usd": limit_value, "limit_type": limit_type})
            else:
                current_total = _current_total(db, scope, scope_id, pkey, limit_type)
                margin = (limit_value or 0) - current_total
                breaches.append((scope, scope_id, limit_value or 0, current_total, margin, limit_type))

        if breaches:
            # The call as a whole is blocked — release any reservations already
            # made at OTHER levels this same attempt, since no real cost happens.
            for r in reservations:
                reconcile_budget(db, r, actual_cost_usd=0.0, actual_tokens=0)
            breaches.sort(key=lambda b: b[4])  # smallest margin = most restrictive
            scope, scope_id, limit_value, current_spend, _, limit_type = breaches[0]
            raise BudgetExceededError(scope, scope_id, limit_value, current_spend, limit_type)

        return BudgetCheckResult(reservations=reservations, warnings=warnings)

    except BudgetExceededError:
        raise
    except Exception as exc:
        _log.error("Budget check failed internally — failing OPEN, call proceeds: %s", exc)
        # A level earlier in the loop may have already reserved successfully
        # before a later level threw — release those now, or they stay
        # permanently charged in budget_period_spend with no real cost behind
        # them (the caller only gets the empty list returned below, so it has
        # nothing left to reconcile against).
        for r in reservations:
            try:
                reconcile_budget(db, r, actual_cost_usd=0.0, actual_tokens=0)
            except Exception:
                pass
        return BudgetCheckResult(reservations=[], warnings=[])


def check_budget_autocommit(usage_ctx, *, system_prompt: str, user_prompt: str, model: str,
                             max_tokens: Optional[int] = None) -> BudgetCheckResult:
    """check_budget() for callers with no open DB session (llm_client.py's raw
    functions) — mirrors usage_service.log_llm_usage_autocommit's pattern.
    """
    from promptops_app.database import SessionLocal
    db = SessionLocal()
    try:
        return check_budget(db, usage_ctx, system_prompt=system_prompt, user_prompt=user_prompt, model=model,
                             max_tokens=max_tokens)
    finally:
        db.close()


def reconcile_budget_autocommit(reservations: list, actual_cost_usd: float, actual_tokens: int = 0) -> None:
    """reconcile_budget() for all of a call's reservations at once, own session."""
    if not reservations:
        return
    from promptops_app.database import SessionLocal
    db = SessionLocal()
    try:
        for reservation in reservations:
            reconcile_budget(db, reservation, actual_cost_usd, actual_tokens)
    finally:
        db.close()


def build_usage_summary(db, usage_ctx, entity_type: str, entity_id: str) -> Optional[dict]:
    """Assemble a post-call summary for the frontend, or None if unavailable.

    Never raises. This is a REPORTING call that runs after the model has already
    answered and the money has already been spent, so a failure here must not be
    allowed to sink the result it is describing — the caller loses its summary,
    not its generation.

    That is not hypothetical. ``llm_usage_logs`` drifted from its ORM (the table
    had ``trace_id``; the model declared ``langfuse_trace_id``), and because
    SQLAlchemy names every mapped column in its SELECT, this query raised
    UndefinedColumn on a table it only wanted to read one row from. A successful,
    paid-for CDD regeneration returned 500 and the corrected content was
    discarded — the user saw "no change" with no indication anything had gone
    wrong. Enforcement is unaffected: check_budget() is what blocks a call, and
    it runs before the spend, not here.

    Returns None when there is no usage row (e.g. logging itself failed — see
    usage_service.log_llm_usage's own fail-safe note) so callers can omit the
    summary rather than show zeros.
    """
    try:
        return _build_usage_summary(db, usage_ctx, entity_type, entity_id)
    except Exception:  # noqa: BLE001 — a report must never sink what it reports on
        _log.warning("usage_summary_unavailable entity=%s:%s — returning None",
                     entity_type, entity_id, exc_info=True)
        try:
            # The session may be in a failed transaction after a database error;
            # leaving it that way would break the caller's next statement and
            # turn a cosmetic failure back into a fatal one.
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None


def _build_usage_summary(db, usage_ctx, entity_type: str, entity_id: str) -> Optional[dict]:
    """The real work. See build_usage_summary for why it is wrapped."""
    from promptops_app.database import LLMUsageLog

    query = db.query(LLMUsageLog).filter(
        LLMUsageLog.entity_type == entity_type, LLMUsageLog.entity_id == str(entity_id),
    )
    # Scope to the caller's own username when known — without it, two users
    # concurrently regenerating the SAME block (same entity_type/entity_id)
    # race on .first(): whichever row logged most recently wins, so one user
    # can be shown the other's cost/tokens.
    actor = getattr(usage_ctx, "user_name", None)
    if actor:
        query = query.filter(LLMUsageLog.user_id == actor)
    usage_row = query.order_by(LLMUsageLog.id.desc()).first()
    if usage_row is None:
        return None

    budgets = []
    for scope, scope_id in _levels_for(usage_ctx):
        policy = _get_policy(db, scope, scope_id)
        if policy is None:
            continue
        # Read from budget_period_spend (check_budget()'s own source of
        # truth), not a SUM() over LLMUsageLog — the two can disagree (e.g. a
        # reservation not yet reconciled), and showing the latter here would
        # let the displayed headroom mismatch what actually blocks the next call.
        pkey = period_key(policy.period)
        spent_usd = _current_total(db, scope, scope_id, pkey, "usd")
        spent_tokens = int(_current_total(db, scope, scope_id, pkey, "tokens"))
        limit_type = policy.limit_type or "usd"
        entry = {
            "scope": scope, "scope_id": scope_id, "limit_type": limit_type,
            "limit_usd": policy.limit_usd, "spent_usd": round(spent_usd, 4),
            "remaining_usd": round(policy.limit_usd - spent_usd, 4) if policy.limit_usd is not None else None,
            "limit_tokens": policy.limit_tokens, "spent_tokens": spent_tokens,
            "remaining_tokens": (policy.limit_tokens - spent_tokens) if policy.limit_tokens is not None else None,
        }
        budgets.append(entry)

    return {
        "cost_usd": round(usage_row.estimated_cost or 0.0, 4),
        "input_tokens": usage_row.input_tokens or 0,
        "output_tokens": usage_row.output_tokens or 0,
        "total_tokens": usage_row.total_tokens or 0,
        "budgets": budgets,
    }
