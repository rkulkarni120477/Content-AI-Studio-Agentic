# Budget / Quota Governance

How LLM spend limits work — project, title (course), and user level. Part of
the [AI Cost Governance & Platform Hardening plan](claude_plan_platform_hardening/PLAN.md)
(P2 enforcement engine, P3 admin UI).

## What a "budget" is

A `BudgetPolicy` row: `scope` (`project` | `course` | `user`), `scope_id`
(the project/course id, or a username for `user` scope), `period`
(`monthly` or `rolling`), `limit_usd`, and `warn_threshold_pct`.

A scope with no policy is **unrestricted** — budgets are opt-in per level, not
a default cap.

## Period semantics

- **`monthly`** — a real calendar-month bucket (`2026-08`). This is what
  every deployment actually uses today.
- **`rolling`** — intended as a 30-day sliding window. The dashboard's spend
  figure (`current_period_spend`) *is* a true trailing-30-day sum, but the
  atomic enforcement bucket it's checked against still keys by calendar month
  under the hood (a real sliding window needs day-granularity buckets summed
  across the trailing 30 days to stay race-safe — not built yet, since
  nothing uses `rolling` in practice). If you configure `rolling` and rely on
  it for enforcement precision, know that today it behaves like `monthly`.

## Nesting — project + title + user at once

All three levels that apply to a call (its project, its title, and the
calling user) are checked **independently in the same request**. The call is
blocked if **any** configured level would breach its limit — a generous
project budget does not override a tight title budget, and vice versa. When
more than one level would breach at once, the error reported to the caller
names whichever level has the **smallest remaining margin** (most
restrictive) — that's purely which message you see, not a decision about
whether to block.

## Warn vs. hard cutoff

- **Warn** (`warn_threshold_pct`, default 80%): once spend crosses this
  percentage of the limit, the next check logs a one-time warning for that
  period (`last_warned_period` prevents re-warning every call). No calls are
  blocked. The frontend budget meter (Analytics → LLM Cost tab) shows this as
  an amber "⚠️ Warning" state.
- **Hard cutoff** (`limit_usd`): a call that would push spend at or past the
  limit is rejected before the LLM provider is called — the caller gets an
  HTTP `402 QUOTA_EXCEEDED`. Shown in the UI as a red "⛔ Blocked" state.

## Dry-run mode and the kill switch

Two independent live flags in `.env`, both re-read on every check with no
deploy or restart needed:

- `BUDGET_ENFORCEMENT_MODE` — `dry_run` (default) tracks real spend and logs
  what *would* have blocked, but never actually blocks a call. `enforce`
  turns on real hard cutoffs. Flip this only after dry-run has run long
  enough against real traffic to trust the configured limits.
- `BUDGET_ENFORCEMENT_KILLSWITCH` — `true` bypasses budget checking
  entirely (no tracking, no blocking), independent of the mode above. For
  "the budget engine itself is misbehaving, turn it off right now."

Both checks fail **open**: any internal error in the budget check (DB down,
a bug) lets the call through rather than blocking all LLM traffic
platform-wide.

## Reserve-then-reconcile (why spend shows up before a call finishes)

Each call reserves a **worst-case** cost estimate (prompt size +
`max_tokens=16384`) atomically before calling the provider, then reconciles
the reservation down to the real cost afterward (in a `finally`, so a failed
call always releases its reservation rather than leaking it for the rest of
the period). This is what makes concurrent calls against the same budget
race-safe — a plain "read spend, compare, then log" approach can't prevent
two simultaneous calls from both slipping past a limit.

## Who can set a budget

**Platform admin only, for now** (`P3.3`, a deliberate v1 scope decision —
widening later is easy, narrowing after tenants already have the power is
not). Managing budgets (create/edit/delete) requires `is_platform_admin`.

**Viewing your own spend is not platform-admin-only.** A tenant admin or
title-scoped user can see the meter for their own project or their own title
via the same `GET /api/v1/platform/tenants/budgets?scope=...&scope_id=...`
endpoint, as long as `scope_id` actually resolves to something they belong
to — the server checks this server-side (own project id, own title's
project, or own username), it is not just a client-side filter. Listing
*every* policy across the platform (no scope filter) still requires platform
admin.

### To request a limit increase or a new budget

There's no self-service flow yet. Ask a platform admin to update it via
Analytics → LLM Cost → Budget Policies (or directly through the
`PUT /api/v1/platform/tenants/budgets/policy` endpoint).

## Where this lives in code

- `promptops_app/services/budget_service.py` — the enforcement engine
  (`check_budget`, `reconcile_budget`, the atomic reserve).
- `app/api/v1/routers/platform_tenants.py` — the admin `GET/PUT/DELETE
  /platform/tenants/budgets...` endpoints and the view-scope guard.
- `frontend/src/features/analytics/components/BudgetMeter/BudgetMeter.jsx` —
  the meter component, reused for both the platform-wide panel and the
  per-title panel (same `AnalyticsPage.jsx`, mounted at both `/analytics` and
  `/workspace/:courseId/analytics`).
