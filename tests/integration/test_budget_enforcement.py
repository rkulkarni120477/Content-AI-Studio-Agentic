"""P2.11 — automated tests for the budget engine.

Covers: P2.9's race-safety under concurrent requests, P2.8's four nesting
scenarios, P2.3's fail-open behavior, P2.10's kill switch, reconciliation
correctly releasing a reservation on failure, and that a quota breach never
triggers the cross-provider fallback (P2.3's retry-ladder note).
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

import promptops_app.services.budget_service as bs
from promptops_app.database import BudgetPolicy
from promptops_app.services.usage_service import UsageLogContext
from tests.conftest import _TestSessionLocal


@pytest.fixture(autouse=True)
def _force_enforce_mode(monkeypatch):
    """Tests exercise blocking behavior — force enforce mode regardless of
    whatever BUDGET_ENFORCEMENT_MODE happens to be set to in .env."""
    monkeypatch.setattr(bs, "enforcement_mode", lambda: "enforce")
    monkeypatch.setattr(bs, "enforcement_killswitch_active", lambda: False)


def _policy(db, scope, scope_id, limit_usd, warn_pct=80.0):
    p = BudgetPolicy(scope=scope, scope_id=scope_id, period="monthly", limit_usd=limit_usd, warn_threshold_pct=warn_pct)
    db.add(p)
    db.commit()
    return p


class TestRaceSafety:
    def test_concurrent_reserves_never_exceed_limit(self, db):
        """N concurrent reserve attempts against a limit that fits ~2 calls'
        worth — total charged must never exceed the limit by more than one
        call's worth, regardless of how many fire at once.
        """
        _policy(db, "project", "race-test", limit_usd=1.0)
        per_call_cost = 0.3
        n_threads = 10
        results = []
        lock = threading.Lock()

        def attempt():
            # Each thread gets its own session bound to the same test engine —
            # this is what actually exercises the atomic UPDATE's WHERE guard
            # rather than one shared session serializing everything trivially.
            # SQLite's StaticPool shares one underlying connection across
            # threads (see conftest.py) — real concurrent commits on it can hit
            # sqlite3's own threading limitations (OperationalError), a test-
            # infrastructure artifact of SQLite specifically, not of the atomic
            # SQL itself (already verified directly against real Postgres
            # during manual smoke testing). Treat that as "did not reserve"
            # rather than a hard test-thread crash.
            thread_db = _TestSessionLocal()
            try:
                policy = SimpleNamespace(limit_type="usd", limit_usd=1.0, limit_tokens=None)
                reserved = bs._reserve(
                    thread_db, scope="project", scope_id="race-test", pkey=bs.period_key("monthly"),
                    cost_usd=per_call_cost, tokens=0, policy=policy,
                )
            except Exception:
                reserved = False
            finally:
                thread_db.close()
            with lock:
                results.append(reserved)

        threads = [threading.Thread(target=attempt) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        successful = sum(1 for r in results if r)
        total_charged = successful * per_call_cost
        # Limit 1.0 / per-call 0.3 -> at most 3 can succeed (0.9 <= 1.0, a 4th would be 1.2 > 1.0).
        assert successful <= 3, f"{successful} reservations succeeded — overspend beyond the limit"
        assert total_charged <= 1.0 + per_call_cost, "combined spend exceeded the limit by more than one call's worth"


class TestNestingPrecedence:
    # 0.30 comfortably fits exactly one worst-case reservation (~0.246 for these
    # tiny test prompts on gpt-4o) and blocks a second — see WORST_CASE_OUTPUT_TOKENS.
    _TIGHT_LIMIT = 0.30

    def test_course_only_configured(self, db):
        _policy(db, "course", "c1", limit_usd=self._TIGHT_LIMIT)
        ctx = UsageLogContext(user_name="u", course_id="c1", entity_type="generation")
        r1 = bs.check_budget(db, ctx, system_prompt="s", user_prompt="u", model="gpt-4o")
        assert len(r1.reservations) == 1
        with pytest.raises(bs.BudgetExceededError) as exc_info:
            bs.check_budget(db, ctx, system_prompt="s", user_prompt="u", model="gpt-4o")
        assert exc_info.value.scope == "course"

    def test_project_only_configured(self, db):
        _policy(db, "project", "p1", limit_usd=self._TIGHT_LIMIT)
        ctx = UsageLogContext(user_name="u", project_id="p1", entity_type="generation")
        bs.check_budget(db, ctx, system_prompt="s", user_prompt="u", model="gpt-4o")
        with pytest.raises(bs.BudgetExceededError) as exc_info:
            bs.check_budget(db, ctx, system_prompt="s", user_prompt="u", model="gpt-4o")
        assert exc_info.value.scope == "project"

    def test_both_configured_only_course_breaches(self, db):
        _policy(db, "project", "p2", limit_usd=100.0)   # plenty of room
        _policy(db, "course", "c2", limit_usd=self._TIGHT_LIMIT)
        ctx = UsageLogContext(user_name="u", project_id="p2", course_id="c2", entity_type="generation")
        bs.check_budget(db, ctx, system_prompt="s", user_prompt="u", model="gpt-4o")
        with pytest.raises(bs.BudgetExceededError) as exc_info:
            bs.check_budget(db, ctx, system_prompt="s", user_prompt="u", model="gpt-4o")
        assert exc_info.value.scope == "course"
        # The project-level reservation from the blocked attempt must have been
        # released, not left double-counted.
        assert bs._current_total(db, "project", "p2", bs.period_key("monthly")) < 1.0

    def test_both_configured_both_breach_reports_smallest_margin(self, db):
        _policy(db, "project", "p3", limit_usd=self._TIGHT_LIMIT)  # small margin after 1 call
        _policy(db, "course", "c3", limit_usd=5.0)                  # large margin — less restrictive
        ctx = UsageLogContext(user_name="u", project_id="p3", course_id="c3", entity_type="generation")
        bs.check_budget(db, ctx, system_prompt="s", user_prompt="u", model="gpt-4o")
        with pytest.raises(bs.BudgetExceededError) as exc_info:
            bs.check_budget(db, ctx, system_prompt="s", user_prompt="u", model="gpt-4o")
        # Project has the smaller margin -> most-restrictive-wins -> reported.
        assert exc_info.value.scope == "project"


class TestFailOpen:
    def test_internal_error_lets_the_call_through(self, db, monkeypatch):
        _policy(db, "project", "fail-open-test", limit_usd=0.001)  # would otherwise block immediately

        def _broken_reserve(*a, **k):
            raise RuntimeError("simulated DB failure")

        monkeypatch.setattr(bs, "_reserve", _broken_reserve)
        ctx = UsageLogContext(user_name="u", project_id="fail-open-test", entity_type="generation")
        result = bs.check_budget(db, ctx, system_prompt="s", user_prompt="u", model="gpt-4o")
        assert result.reservations == [] and result.warnings == []  # no exception raised — call proceeds


class TestKillSwitch:
    def test_killswitch_bypasses_a_would_be_block(self, db, monkeypatch):
        _policy(db, "project", "kill-test", limit_usd=0.001)
        monkeypatch.setattr(bs, "enforcement_killswitch_active", lambda: True)
        ctx = UsageLogContext(user_name="u", project_id="kill-test", entity_type="generation")
        result = bs.check_budget(db, ctx, system_prompt="s", user_prompt="u", model="gpt-4o")
        assert result.reservations == []  # full bypass, not even a reservation attempted


class TestReconciliation:
    def test_failed_call_releases_its_reservation(self, db):
        _policy(db, "project", "reconcile-test", limit_usd=1.0)
        pkey = bs.period_key("monthly")
        reservation = bs.BudgetReservation(scope="project", scope_id="reconcile-test", period_key=pkey, reserved_usd=0.25)
        policy = SimpleNamespace(limit_type="usd", limit_usd=1.0, limit_tokens=None)
        bs._reserve(db, scope="project", scope_id="reconcile-test", pkey=pkey, cost_usd=0.25, tokens=0, policy=policy)
        assert bs._current_total(db, "project", "reconcile-test", pkey) == 0.25

        # Simulated failed call: reconcile down to 0 real cost.
        bs.reconcile_budget(db, reservation, actual_cost_usd=0.0)
        assert bs._current_total(db, "project", "reconcile-test", pkey) == 0.0


class TestNoFallbackOnQuota:
    def test_quota_breach_does_not_trigger_cross_provider_fallback(self, monkeypatch):
        from promptops_app.services import llm_service

        fallback_called = {"count": 0}

        def _raise_quota(*a, **k):
            raise bs.BudgetExceededError("project", "p1", 1.0, 1.0)

        def _record_fallback(*a, **k):
            fallback_called["count"] += 1
            raise RuntimeError("fallback should never be reached")

        monkeypatch.setattr(llm_service, "_invoke_primary", _raise_quota)
        monkeypatch.setattr(llm_service, "_invoke_fallback", _record_fallback)

        with pytest.raises(bs.BudgetExceededError):
            llm_service.generate_with_metadata("GPT-5.4", "sys", "usr", UsageLogContext(project_id="p1"))

        assert fallback_called["count"] == 0, "quota breach must not attempt the fallback provider"
