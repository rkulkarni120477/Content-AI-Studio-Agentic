"""P1.5 — negative-control cross-tenant isolation tests for the trace-detail endpoint.

Zero-tolerance per claude_plan_platform_hardening/PLAN.md: a user from one
project must never be able to fetch another project's generation trace via
GET /api/v1/generations/{id}/trace, under any circumstance — not even a
partial/truncated response, and not via ID enumeration. These tests are what
prove that holds, not just assume it from the get_scoped_or_404 reuse in P1.4.

Phoenix itself is mocked (app.core.phoenix_client.get_trace_observations) —
these tests verify CAS's own authorization boundary, which must reject a
cross-tenant request BEFORE Phoenix is ever called, not the Phoenix
integration itself (that's covered by a live smoke test run during rollout).
"""

from __future__ import annotations

import uuid

import pytest

from app.core.security import create_access_token, hash_password


def _make_user_and_headers(db, *, username: str, project_id: int, is_platform_admin: bool = False):
    from promptops_app.database import TenantMembership, User

    user = User(
        username=username,
        password_hash=hash_password("test_password"),
        role="admin" if is_platform_admin else "author",
        is_active=True,
        is_platform_admin=is_platform_admin,
        project_id=project_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    if not is_platform_admin:
        # get_current_user re-validates an active TenantMembership on every
        # request for non-platform-admin users, not just User.is_active.
        db.add(TenantMembership(user_id=user.id, project_id=project_id, role="author", active=True))
        db.commit()

    token = create_access_token(
        user.username, user.role or "author",
        project_id=project_id, is_platform_admin=is_platform_admin,
    )
    return {"Authorization": f"Bearer {token}"}


def _make_traced_generation(db, *, project_id: int, trace_id: str):
    """A Generation + its GenerationJob + the LLMUsageLog row P1.4 joins through."""
    from promptops_app.database import Generation, GenerationJob, LLMUsageLog

    gen = Generation(
        prompt_name="p", prompt_version="v1", block_type="lesson", topic="isolation test",
        output_text="output", project_id=project_id, created_by="tester",
    )
    db.add(gen)
    db.commit()
    db.refresh(gen)

    job_id = f"isolation-test-{uuid.uuid4().hex[:8]}"
    job = GenerationJob(id=job_id, job_type="generation", status="completed", progress=100, result_entity_id=gen.id)
    db.add(job)

    usage_row = LLMUsageLog(
        user_id="tester", project_id=project_id, entity_type="generation", entity_id=job_id,
        model_name="gpt-4o", status="success", trace_id=trace_id,
    )
    db.add(usage_row)
    db.commit()
    return gen


@pytest.fixture()
def two_tenant_setup(db):
    from promptops_app.database import Project

    project_a = Project(name="Isolation Project A", is_active=True)
    project_b = Project(name="Isolation Project B", is_active=True)
    db.add_all([project_a, project_b])
    db.commit()
    db.refresh(project_a)
    db.refresh(project_b)

    gen_a = _make_traced_generation(db, project_id=project_a.id, trace_id="trace-belongs-to-a")

    headers_a = _make_user_and_headers(db, username="tenant_a_user", project_id=project_a.id)
    headers_b = _make_user_and_headers(db, username="tenant_b_user", project_id=project_b.id)
    headers_admin = _make_user_and_headers(db, username="platform_admin_user", project_id=project_a.id, is_platform_admin=True)

    return {
        "project_a": project_a, "project_b": project_b, "gen_a": gen_a,
        "headers_a": headers_a, "headers_b": headers_b, "headers_admin": headers_admin,
    }


@pytest.fixture(autouse=True)
def _mock_phoenix(monkeypatch):
    """Never make a real network call to Phoenix in these tests."""
    from app.core import phoenix_client

    monkeypatch.setattr(
        phoenix_client, "get_trace_observations",
        lambda trace_id: [{
            "id": "span1",
            "context": {"trace_id": trace_id, "span_id": "span1"},
            "name": "llm_call:generation",
            "span_kind": "LLM",
            "status_code": "OK",
            "attributes": {"input": {"value": "secret prompt"}, "output": {"value": "secret response"}},
        }],
    )


class TestCrossTenantIsolation:
    def test_owner_can_fetch_their_own_trace(self, client, two_tenant_setup):
        gen_a = two_tenant_setup["gen_a"]
        resp = client.get(f"/api/v1/generations/{gen_a.id}/trace", headers=two_tenant_setup["headers_a"])
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["trace_id"] == "trace-belongs-to-a"
        assert data["observations"][0]["attributes"]["output"]["value"] == "secret response"

    def test_cross_tenant_user_gets_404_not_data(self, client, two_tenant_setup):
        """The core zero-tolerance case: Project B's user requests Project A's trace."""
        gen_a = two_tenant_setup["gen_a"]
        resp = client.get(f"/api/v1/generations/{gen_a.id}/trace", headers=two_tenant_setup["headers_b"])
        assert resp.status_code == 404
        body_text = resp.text
        # Must not leak ANY fragment of the trace content, not even truncated.
        assert "secret prompt" not in body_text
        assert "secret response" not in body_text
        assert "trace-belongs-to-a" not in body_text

    def test_platform_admin_can_access_any_project_trace(self, client, two_tenant_setup):
        """Sanity/positive control — isolation must not accidentally break legitimate admin access."""
        gen_a = two_tenant_setup["gen_a"]
        resp = client.get(f"/api/v1/generations/{gen_a.id}/trace", headers=two_tenant_setup["headers_admin"])
        assert resp.status_code == 200, resp.text
        assert resp.json()["trace_id"] == "trace-belongs-to-a"

    def test_enumeration_gives_identical_404_shape_for_owned_vs_nonexistent(self, client, two_tenant_setup):
        """No enumeration oracle: 'exists but not yours' must look identical to 'does not exist'."""
        gen_a = two_tenant_setup["gen_a"]
        headers_b = two_tenant_setup["headers_b"]

        resp_real_but_not_owned = client.get(f"/api/v1/generations/{gen_a.id}/trace", headers=headers_b)
        resp_never_existed = client.get("/api/v1/generations/99999999/trace", headers=headers_b)

        assert resp_real_but_not_owned.status_code == 404
        assert resp_never_existed.status_code == 404
        # Same shape — a caller (or an attacker probing IDs) cannot distinguish the two cases.
        assert resp_real_but_not_owned.json().keys() == resp_never_existed.json().keys()

    def test_sweep_of_ids_never_leaks_cross_tenant_data(self, client, two_tenant_setup):
        """Broader sweep: no ID in a small range leaks Project A's trace to a Project B caller."""
        headers_b = two_tenant_setup["headers_b"]
        gen_a_id = two_tenant_setup["gen_a"].id

        for candidate_id in range(max(1, gen_a_id - 2), gen_a_id + 3):
            resp = client.get(f"/api/v1/generations/{candidate_id}/trace", headers=headers_b)
            if candidate_id == gen_a_id:
                assert resp.status_code == 404
            assert "secret prompt" not in resp.text
            assert "secret response" not in resp.text


class TestGenerationByIdTenantIsolation:
    """The plain by-id Generation endpoints (get/export/completion-status) had
    NO tenant check at all before this fix — unlike /trace above, which was
    already scoped via get_scoped_or_404. Same fixtures/helpers as the trace
    tests above; reused rather than the generic `two_tenants` fixture so this
    file stays internally consistent."""

    def test_get_404s_for_another_tenant(self, client, two_tenant_setup):
        gen_a = two_tenant_setup["gen_a"]
        assert client.get(f"/api/v1/generations/{gen_a.id}", headers=two_tenant_setup["headers_b"]).status_code == 404
        assert client.get(f"/api/v1/generations/{gen_a.id}", headers=two_tenant_setup["headers_a"]).status_code == 200

    def test_platform_admin_still_reaches_every_tenants_generation(self, client, two_tenant_setup):
        gen_a = two_tenant_setup["gen_a"]
        resp = client.get(f"/api/v1/generations/{gen_a.id}", headers=two_tenant_setup["headers_admin"])
        assert resp.status_code == 200

    # get_module_completion (/completion-status) is not exercised here: it
    # unconditionally imports promptops_app.core.shared, whose module-level
    # `@st.cache_data` decorator NameErrors on import (Streamlit reference
    # with no `st` import) — a pre-existing, unrelated bug that 500s the
    # endpoint for every caller, tenant check or not. Not this fix's to carry.

    def test_export_404s_for_another_tenant(self, client, two_tenant_setup):
        gen_a = two_tenant_setup["gen_a"]
        resp = client.get(
            f"/api/v1/generations/{gen_a.id}/export", headers=two_tenant_setup["headers_b"],
        )
        assert resp.status_code == 404

    def test_null_project_generation_not_visible_to_a_tenant_but_platform_admin_reaches_it(
        self, client, db, two_tenant_setup,
    ):
        """get_scoped_or_404 (the pre-existing, already-tested /trace idiom
        this fix reuses) has no NULL-is-shared carve-out — unlike CDD/Style's
        visible_to_tenant, a NULL project_id row here is invisible to every
        tenant, reachable only by a platform admin. Pinning the ACTUAL,
        already-established semantics rather than assuming CDD/Style's
        convention carries over to a different helper."""
        from promptops_app.database import Generation

        gen = Generation(
            prompt_name="p", prompt_version="v1", block_type="lesson", topic="orphan row",
            output_text="output", project_id=None, created_by="tester",
        )
        db.add(gen)
        db.commit()
        db.refresh(gen)
        for hdrs in (two_tenant_setup["headers_a"], two_tenant_setup["headers_b"]):
            assert client.get(f"/api/v1/generations/{gen.id}", headers=hdrs).status_code == 404
        assert client.get(f"/api/v1/generations/{gen.id}", headers=two_tenant_setup["headers_admin"]).status_code == 200


class TestListGenerationsTenantIsolation:
    def test_tenant_b_cannot_see_tenant_a_generation(self, client, two_tenant_setup):
        gen_a = two_tenant_setup["gen_a"]
        resp = client.get("/api/v1/generations", headers=two_tenant_setup["headers_b"])
        assert resp.status_code == 200
        ids = [g["id"] for g in resp.json()["items"]]
        assert gen_a.id not in ids

    def test_tenant_a_sees_its_own_generation(self, client, two_tenant_setup):
        gen_a = two_tenant_setup["gen_a"]
        resp = client.get("/api/v1/generations", headers=two_tenant_setup["headers_a"])
        assert resp.status_code == 200
        ids = [g["id"] for g in resp.json()["items"]]
        assert gen_a.id in ids

    def test_tenant_cannot_use_project_id_param_to_see_another_tenant(self, client, two_tenant_setup):
        gen_a = two_tenant_setup["gen_a"]
        resp = client.get(
            "/api/v1/generations",
            params={"project_id": two_tenant_setup["project_a"].id},
            headers=two_tenant_setup["headers_b"],
        )
        assert resp.status_code == 200
        ids = [g["id"] for g in resp.json()["items"]]
        assert gen_a.id not in ids

    def test_platform_admin_with_no_project_id_sees_every_tenant(self, client, two_tenant_setup):
        gen_a = two_tenant_setup["gen_a"]
        resp = client.get("/api/v1/generations", headers=two_tenant_setup["headers_admin"])
        assert resp.status_code == 200
        ids = [g["id"] for g in resp.json()["items"]]
        assert gen_a.id in ids


class TestCourseCompletionTenantIsolation:
    """GET /generations/course/{course_id}/completion-status — same unfiltered
    by-id lookup bug, on Course rather than Generation."""

    def _course(self, db, *, project_id):
        from promptops_app.database import Course

        course = Course(project_id=project_id, name="Isolation course")
        db.add(course)
        db.commit()
        db.refresh(course)
        return course

    def test_404s_for_another_tenant(self, client, db, two_tenant_setup):
        course = self._course(db, project_id=two_tenant_setup["project_a"].id)
        resp = client.get(
            f"/api/v1/generations/course/{course.id}/completion-status", headers=two_tenant_setup["headers_b"],
        )
        assert resp.status_code == 404

    def test_works_for_own_tenant(self, client, db, two_tenant_setup):
        course = self._course(db, project_id=two_tenant_setup["project_a"].id)
        resp = client.get(
            f"/api/v1/generations/course/{course.id}/completion-status", headers=two_tenant_setup["headers_a"],
        )
        assert resp.status_code == 200, resp.text

    def test_platform_admin_still_reaches_every_tenants_course(self, client, db, two_tenant_setup):
        course = self._course(db, project_id=two_tenant_setup["project_a"].id)
        resp = client.get(
            f"/api/v1/generations/course/{course.id}/completion-status", headers=two_tenant_setup["headers_admin"],
        )
        assert resp.status_code == 200, resp.text
