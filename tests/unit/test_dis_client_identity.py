"""A block-wide build must run against the COURSE'S DIS client — or not at all.

Production, Block 13, an AIM course run by an AIM-only user, failed with an opaque
DIS 502: "Curriculum enumeration is not configured for this tenant
(profile=CurriculumProfile)". Nothing about that message says what actually
happened: the build ran against **cengage**.

The digest build carries no client_id on the wire (dis_client._start_digest_build
sends only block/force/map_guidance/wait). DIS derives the tenant from the caller's
identity headers, and get_dis_access_for_user drops a requested client the caller
cannot prove access to, falling back to ``default_client_id``. So identity loss does
not refuse the request — it silently redirects it to another client's Source Library.

Two ways the identity was lost, both pinned here:

  * the async worker rebuilt the caller as ``id=username`` (a string), and the
    membership lookup did ``int(user_id)`` inside a blanket ``except: return []``.
    Every membership-derived AIM access therefore evaporated on the job path — which
    is the path the UI always takes.
  * nothing compared the client the build ran as against the client the course
    belongs to, so the mismatch only surfaced later, as someone else's error.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core import dis_access
from promptops_app.jobs import block_wide_jobs as jobs
from promptops_app.services import block_wide_service as svc

_ROOT = Path(__file__).resolve().parents[2]


# ── the identity that survives the job row ──────────────────────────────────
def test_the_worker_rebuilds_the_caller_with_the_real_user_id():
    user = jobs._reconstruct_user({"user_name": "jdoe", "user_id": 42, "role": "author"})
    assert user.id == 42, "a username in the id slot fails the membership lookup"
    assert user.username == "jdoe" and user.role == "author"


def test_a_job_enqueued_before_user_id_existed_still_rebuilds():
    """Rows already in the queue have no user_id. They must not crash the worker —
    the membership lookup handles a name in the id slot on its own."""
    user = jobs._reconstruct_user({"user_name": "jdoe"})
    assert user.id == "jdoe" and user.username == "jdoe"


@pytest.mark.parametrize("router", ["cdd.py", "blueprints.py"])
def test_both_generate_block_routers_persist_the_user_id(router):
    """Structural, because the two routers are edited independently: one that keeps
    only the username reintroduces the same silent downgrade for its deliverable."""
    src = (_ROOT / "app/api/v1/routers" / router).read_text()
    assert 'params["user_id"]' in src, f"{router} must persist the caller's user id"


# ── the membership lookup ───────────────────────────────────────────────────
class _Query:
    def __init__(self, rec):
        self.rec = rec

    def join(self, entity, *a, **k):
        self.rec["joins"].append(entity)
        return self

    def filter(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def all(self):
        return self.rec["rows"]


class _Session:
    def __init__(self, rec):
        self.rec = rec

    def query(self, *a, **k):
        return _Query(self.rec)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _membership(monkeypatch, user, rows=(("AIM", "admin"),)):
    import promptops_app.database as database

    rec = {"joins": [], "rows": list(rows)}
    monkeypatch.setattr(database, "SessionLocal", lambda: _Session(rec))
    return dis_access._membership_clients(user), rec


def test_membership_survives_a_username_sitting_in_the_id_slot(monkeypatch):
    """The exact production shape: the worker's rebuilt user. int("jdoe") raised into
    the blanket except, which reported "no memberships" — indistinguishable from a
    user who genuinely has none, and one step from another client's content."""
    import types

    from promptops_app.database import User

    user = types.SimpleNamespace(username="jdoe", id="jdoe", role="author")
    clients, rec = _membership(monkeypatch, user)
    assert clients == [("aim", "admin")]
    assert User in rec["joins"], "a non-numeric id must resolve by username instead"


def test_membership_by_real_id_does_not_need_the_username_join(monkeypatch):
    import types

    from promptops_app.database import User

    user = types.SimpleNamespace(username="jdoe", id=42, role="author")
    clients, rec = _membership(monkeypatch, user)
    assert clients == [("aim", "admin")]
    assert User not in rec["joins"]


def test_a_broken_membership_lookup_is_logged_not_swallowed(monkeypatch, caplog):
    """Every failure here downgrades the caller to the default client. A missing
    tenant_memberships table must not look like "this user has no access"."""
    import promptops_app.database as database
    import types

    def _boom():
        raise RuntimeError("no such table: tenant_memberships")

    monkeypatch.setattr(database, "SessionLocal", _boom)
    with caplog.at_level("WARNING"):
        assert dis_access._membership_clients(
            types.SimpleNamespace(username="jdoe", id=42)) == []
    assert "tenant_memberships" in caplog.text


# ── the guard in front of the build ─────────────────────────────────────────
class _Req:
    block = "13"
    project_id = None
    course_id = None
    prompt_id = None
    quality_tier = None


class _User:
    username = "aim_user"


def _run(monkeypatch, *, resolves_to, course_client="aim"):
    """Run _build_and_reduce with a DIS whose identity resolves to *resolves_to*
    (None ⇒ the client object has no resolver at all). Returns (result, calls)."""
    calls: list[str] = []

    class _Stub:
        enabled = True

        def build_digests_sync(self, block, **kw):
            calls.append(block)
            return {}

        def get_digests_bundle_sync(self, block, **kw):
            # No days: these tests are about whether the BUILD is reached at all, so
            # the service's own empty-enumerate exit keeps REDUCE out of the picture.
            return {"enumerate": {"days": []}, "digests": []}

    stub = _Stub()
    if resolves_to is not None:
        stub.resolved_tenant_id = lambda *a, **k: resolves_to

    monkeypatch.setattr(svc, "dis_client", stub)
    monkeypatch.setattr(svc, "_map_usage_ctx", lambda *a, **k: None)
    monkeypatch.setattr(svc, "_reserve_map_budget", lambda *a, **k: [])
    monkeypatch.setattr(svc, "_settle_map_usage", lambda *a, **k: None)
    out = svc._build_and_reduce("cdd", "13", None, _User(), course_client,
                                db=None, request_body=_Req())
    return out, calls


def test_a_client_mismatch_refuses_the_build_and_names_both_clients(monkeypatch):
    out, calls = _run(monkeypatch, resolves_to="cengage", course_client="aim")
    assert out[:2] == (None, None)
    assert calls == [], "the wrong client's block must never be enumerated"
    reason = svc.last_failure_reason()
    assert "aim" in reason and "cengage" in reason
    assert "not configured" not in reason, "must not read as DIS's tenant-config error"


def test_a_matching_client_proceeds_to_the_build(monkeypatch):
    _, calls = _run(monkeypatch, resolves_to="aim", course_client="aim")
    assert calls == ["13"]


def test_an_unresolvable_tenant_does_not_block_the_build(monkeypatch):
    """"Unknown" is not "mismatch". A check that exists to protect a generation must
    never be the thing that fails one — including against a client object that
    predates the resolver."""
    _, calls = _run(monkeypatch, resolves_to=None, course_client="aim")
    assert calls == ["13"]
    _, calls_empty = _run(monkeypatch, resolves_to="", course_client="aim")
    assert calls_empty == ["13"]
