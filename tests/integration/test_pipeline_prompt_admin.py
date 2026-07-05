"""
Integration tests — Phase 8 pipeline-management endpoints on /api/v1/prompts:

  PUT  /{id}/default                    set/clear the component default flag
  POST /{id}/versions/{version}/state   workflow_state transitions
  PUT/DELETE /fixings, GET /fixings/resolve   scope locks (reuse-by-reference)

All writes are gated by prompt.pipeline.edit (admin only), except binding an
already-approved prompt, which authors may do (role split per the plan's
"Reuse / inject endpoint" decision).
"""

from __future__ import annotations

import pytest

BASE = "/api/v1/prompts"


@pytest.fixture()
def reviewer_headers(client, db) -> dict:
    """Authorization headers for a reviewer (Lead) user."""
    from app.core.security import hash_password
    from promptops_app.database import User

    db.add(User(username="test_reviewer",
                password_hash=hash_password("test_password"),
                role="reviewer", is_active=True))
    db.commit()
    resp = client.post("/api/v1/auth/login",
                       json={"username": "test_reviewer", "password": "test_password"})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _create_pipeline_prompt(client, headers, name, component="cdd",
                            system="SYS {{x}}", user="USR {{x}}"):
    resp = client.post(
        BASE,
        json={
            "name": name,
            "description": f"test asset {name}",
            "component_type": component,
            "system_prompt": system,
            "user_prompt_template": user,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _set_state(db, prompt_id, version, state):
    """Set a version's workflow_state directly (the draft-insertion write path
    lands with the approval-gate item; tests stage states explicitly)."""
    from promptops_app.database import PromptVersion

    ver = (
        db.query(PromptVersion)
        .filter(PromptVersion.prompt_id == prompt_id, PromptVersion.version == version)
        .one()
    )
    ver.workflow_state = state
    db.commit()


class TestDefaultFlag:
    def test_set_default_demotes_previous_default(self, client, auth_headers, db):
        from promptops_app.database import Prompt

        a = _create_pipeline_prompt(client, auth_headers, "cdd_a")
        b = _create_pipeline_prompt(client, auth_headers, "cdd_b")

        r1 = client.put(f"{BASE}/{a['id']}/default", json={"is_default": True},
                        headers=auth_headers)
        assert r1.status_code == 200 and r1.json()["is_default"] is True

        r2 = client.put(f"{BASE}/{b['id']}/default", json={"is_default": True},
                        headers=auth_headers)
        assert r2.status_code == 200 and r2.json()["is_default"] is True

        # Exactly one default per (component_type, variant).
        defaults = (
            db.query(Prompt)
            .filter(Prompt.component_type == "cdd", Prompt.is_default == True)  # noqa: E712
            .all()
        )
        assert [p.id for p in defaults] == [b["id"]]

    def test_clear_default(self, client, auth_headers):
        a = _create_pipeline_prompt(client, auth_headers, "cdd_clear")
        client.put(f"{BASE}/{a['id']}/default", json={"is_default": True},
                   headers=auth_headers)
        r = client.put(f"{BASE}/{a['id']}/default", json={"is_default": False},
                       headers=auth_headers)
        assert r.status_code == 200 and r.json()["is_default"] is False

    def test_requires_component_type(self, client, auth_headers):
        a = _create_pipeline_prompt(client, auth_headers, "no_component", component="")
        r = client.put(f"{BASE}/{a['id']}/default", json={"is_default": True},
                       headers=auth_headers)
        assert r.status_code == 422

    def test_author_forbidden(self, client, auth_headers, author_headers):
        a = _create_pipeline_prompt(client, auth_headers, "cdd_authwall")
        r = client.put(f"{BASE}/{a['id']}/default", json={"is_default": True},
                       headers=author_headers)
        assert r.status_code == 403

    def test_library_row_rejected(self, client, auth_headers, db):
        from promptops_app.database import Prompt

        lib = Prompt(name=None, title="lib row", prompt_kind="library",
                     component_type="cdd", owner="test_admin")
        db.add(lib)
        db.commit()
        r = client.put(f"{BASE}/{lib.id}/default", json={"is_default": True},
                       headers=auth_headers)
        assert r.status_code == 422


class TestWorkflowState:
    def test_full_walk_draft_to_active(self, client, auth_headers, db):
        p = _create_pipeline_prompt(client, auth_headers, "wf_walk")
        # v1 is live (workflow_state 'active'); commit v2 and stage it as draft.
        client.post(f"{BASE}/{p['id']}/versions",
                    json={"version": "v2", "system_prompt": "S2",
                          "user_prompt_template": "U2"},
                    headers=auth_headers)
        _set_state(db, p["id"], "v2", "draft")
        # deploy_new_version made v2 active; restore v1 as the deployed one so
        # activation side effects are observable.
        r = client.post(f"{BASE}/{p['id']}/versions/v1/deploy", headers=auth_headers)
        assert r.status_code == 200

        def move(state):
            return client.post(f"{BASE}/{p['id']}/versions/v2/state",
                               json={"state": state}, headers=auth_headers)

        # draft cannot jump straight to active
        assert move("active").status_code == 409
        assert move("in_review").json()["workflow_state"] == "in_review"
        assert move("approved").json()["workflow_state"] == "approved"
        body = move("active").json()
        assert body["workflow_state"] == "active" and body["is_active"] is True

        # Activation deployed v2 and demoted v1.
        detail = client.get(f"{BASE}/{p['id']}", headers=auth_headers).json()
        assert detail["active_version"] == "v2"
        versions = {v["version"]: v for v in
                    client.get(f"{BASE}/{p['id']}/versions", headers=auth_headers).json()}
        assert versions["v1"]["is_active"] is False
        assert versions["v1"]["workflow_state"] == "approved"

    def test_rejection_back_to_draft(self, client, auth_headers, db):
        p = _create_pipeline_prompt(client, auth_headers, "wf_reject")
        _set_state(db, p["id"], "v1", "in_review")
        r = client.post(f"{BASE}/{p['id']}/versions/v1/state",
                        json={"state": "draft"}, headers=auth_headers)
        assert r.status_code == 200 and r.json()["workflow_state"] == "draft"

    def test_unknown_state_rejected(self, client, auth_headers):
        p = _create_pipeline_prompt(client, auth_headers, "wf_badstate")
        r = client.post(f"{BASE}/{p['id']}/versions/v1/state",
                        json={"state": "published"}, headers=auth_headers)
        assert r.status_code == 422

    def test_author_forbidden(self, client, auth_headers, author_headers):
        p = _create_pipeline_prompt(client, auth_headers, "wf_authwall")
        r = client.post(f"{BASE}/{p['id']}/versions/v1/state",
                        json={"state": "in_review"}, headers=author_headers)
        assert r.status_code == 403


class TestApprovalGate:
    """POST /versions role split: admins instant-deploy, reviewers commit drafts;
    version activation (deploy) is admin-only."""

    def test_admin_commit_still_instant_deploys(self, client, auth_headers):
        p = _create_pipeline_prompt(client, auth_headers, "gate_admin")
        r = client.post(f"{BASE}/{p['id']}/versions",
                        json={"version": "v2", "system_prompt": "S2",
                              "user_prompt_template": "U2"},
                        headers=auth_headers)
        assert r.status_code == 201
        body = r.json()
        assert body["is_active"] is True and body["workflow_state"] == "active"
        detail = client.get(f"{BASE}/{p['id']}", headers=auth_headers).json()
        assert detail["active_version"] == "v2"

    def test_reviewer_commit_lands_as_inactive_draft(self, client, auth_headers,
                                                     reviewer_headers):
        p = _create_pipeline_prompt(client, auth_headers, "gate_reviewer")
        r = client.post(f"{BASE}/{p['id']}/versions",
                        json={"version": "v2", "system_prompt": "S2",
                              "user_prompt_template": "U2"},
                        headers=reviewer_headers)
        assert r.status_code == 201
        body = r.json()
        assert body["is_active"] is False and body["workflow_state"] == "draft"
        # The deployed version is untouched.
        detail = client.get(f"{BASE}/{p['id']}", headers=auth_headers).json()
        assert detail["active_version"] == "v1"
        assert detail["user_prompt_template"] == "USR {{x}}"

    def test_duplicate_version_tag_conflicts(self, client, auth_headers):
        p = _create_pipeline_prompt(client, auth_headers, "gate_dup")
        r = client.post(f"{BASE}/{p['id']}/versions",
                        json={"version": "v1", "system_prompt": "S",
                              "user_prompt_template": "U"},
                        headers=auth_headers)
        assert r.status_code == 409

    def test_reviewer_cannot_deploy(self, client, auth_headers, reviewer_headers):
        p = _create_pipeline_prompt(client, auth_headers, "gate_deploy")
        r = client.post(f"{BASE}/{p['id']}/versions/v1/deploy",
                        headers=reviewer_headers)
        assert r.status_code == 403

    def test_reviewer_cannot_transition_state(self, client, auth_headers,
                                              reviewer_headers, db):
        p = _create_pipeline_prompt(client, auth_headers, "gate_state")
        _set_state(db, p["id"], "v1", "in_review")
        r = client.post(f"{BASE}/{p['id']}/versions/v1/state",
                        json={"state": "approved"}, headers=reviewer_headers)
        assert r.status_code == 403

    def test_version_numbers_are_sequential(self, client, auth_headers, db):
        from promptops_app.database import PromptVersion

        p = _create_pipeline_prompt(client, auth_headers, "gate_numbers")
        client.post(f"{BASE}/{p['id']}/versions",
                    json={"version": "v2", "system_prompt": "S2",
                          "user_prompt_template": "U2"},
                    headers=auth_headers)
        numbers = [
            v.version_number
            for v in db.query(PromptVersion)
            .filter(PromptVersion.prompt_id == p["id"])
            .order_by(PromptVersion.id)
            .all()
        ]
        assert numbers == [1, 2]


class TestScopeLocks:
    def _course(self, db):
        from promptops_app.database import Course, Project

        proj = Project(name="Fix Proj", created_by="test_admin")
        db.add(proj)
        db.commit()
        course = Course(name="Fix Course", project_id=proj.id, created_by="test_admin")
        db.add(course)
        db.commit()
        return course

    def test_admin_bind_resolve_unbind(self, client, auth_headers, db):
        course = self._course(db)
        p = _create_pipeline_prompt(client, auth_headers, "fix_admin")

        r = client.put(f"{BASE}/fixings",
                       json={"component": "cdd", "scope_level": "course",
                             "course_id": course.id, "prompt_id": p["id"]},
                       headers=auth_headers)
        assert r.status_code == 200, r.text
        assert r.json()["prompt_id"] == p["id"]

        r = client.get(f"{BASE}/fixings/resolve",
                       params={"component": "cdd", "course_id": course.id},
                       headers=auth_headers)
        assert r.json()["prompt_id"] == p["id"]

        r = client.delete(f"{BASE}/fixings",
                          params={"component": "cdd", "scope_level": "course",
                                  "course_id": course.id},
                          headers=auth_headers)
        assert r.status_code == 204
        r = client.get(f"{BASE}/fixings/resolve",
                       params={"component": "cdd", "course_id": course.id},
                       headers=auth_headers)
        assert r.json() is None

    def test_author_can_bind_approved_prompt(self, client, auth_headers,
                                             author_headers, db):
        course = self._course(db)
        p = _create_pipeline_prompt(client, auth_headers, "fix_approved")
        _set_state(db, p["id"], "v1", "approved")

        r = client.put(f"{BASE}/fixings",
                       json={"component": "cdd", "scope_level": "course",
                             "course_id": course.id, "prompt_id": p["id"]},
                       headers=author_headers)
        assert r.status_code == 200, r.text
        assert r.json()["fixed_by_role"] == "author"

    def test_author_cannot_bind_draft_prompt(self, client, auth_headers,
                                             author_headers, db):
        course = self._course(db)
        p = _create_pipeline_prompt(client, auth_headers, "fix_draft")
        _set_state(db, p["id"], "v1", "draft")

        r = client.put(f"{BASE}/fixings",
                       json={"component": "cdd", "scope_level": "course",
                             "course_id": course.id, "prompt_id": p["id"]},
                       headers=author_headers)
        assert r.status_code == 403

    def test_component_mismatch_rejected_for_everyone(self, client, auth_headers):
        p = _create_pipeline_prompt(client, auth_headers, "fix_mismatch",
                                    component="blueprint")
        r = client.put(f"{BASE}/fixings",
                       json={"component": "cdd", "scope_level": "global",
                             "prompt_id": p["id"]},
                       headers=auth_headers)
        assert r.status_code == 422

    def test_scope_id_required_for_scoped_levels(self, client, auth_headers):
        p = _create_pipeline_prompt(client, auth_headers, "fix_noid")
        r = client.put(f"{BASE}/fixings",
                       json={"component": "cdd", "scope_level": "course",
                             "prompt_id": p["id"]},
                       headers=auth_headers)
        assert r.status_code == 422

    def test_unset_missing_fixing_404s(self, client, auth_headers):
        r = client.delete(f"{BASE}/fixings",
                          params={"component": "cdd", "scope_level": "global"},
                          headers=auth_headers)
        assert r.status_code == 404

    def test_library_prompt_cannot_be_bound(self, client, auth_headers, db):
        from promptops_app.database import Prompt

        lib = Prompt(name=None, title="lib", prompt_kind="library",
                     component_type="cdd", owner="test_admin")
        db.add(lib)
        db.commit()
        r = client.put(f"{BASE}/fixings",
                       json={"component": "cdd", "scope_level": "global",
                             "prompt_id": lib.id},
                       headers=auth_headers)
        assert r.status_code == 422


class TestVariantAuthoring:
    """Phase 7b console support: create rows with a variant; re-key
    component_type/variant via PUT /{id} (admin-only; defaults must be
    demoted first)."""

    def test_create_with_variant(self, client, auth_headers):
        r = client.post(
            BASE,
            json={"name": "bp_teacher", "component_type": "blueprint",
                  "variant": "teacher",
                  "system_prompt": "S", "user_prompt_template": "U"},
            headers=auth_headers,
        )
        assert r.status_code == 201, r.text
        assert r.json()["variant"] == "teacher"

    def test_admin_can_rekey_component_and_variant(self, client, auth_headers):
        p = _create_pipeline_prompt(client, auth_headers, "rekey_me")
        r = client.put(f"{BASE}/{p['id']}",
                       json={"component_type": "blueprint", "variant": "student"},
                       headers=auth_headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["component_type"] == "blueprint"
        assert body["variant"] == "student"
        # Empty string clears to NULL.
        r = client.put(f"{BASE}/{p['id']}", json={"variant": ""},
                       headers=auth_headers)
        assert r.status_code == 200 and r.json()["variant"] is None

    def test_reviewer_cannot_rekey(self, client, auth_headers, reviewer_headers):
        p = _create_pipeline_prompt(client, auth_headers, "rekey_denied")
        r = client.put(f"{BASE}/{p['id']}", json={"variant": "teacher"},
                       headers=reviewer_headers)
        assert r.status_code == 403
        # Plain metadata updates stay reviewer-accessible.
        r = client.put(f"{BASE}/{p['id']}", json={"description": "new desc"},
                       headers=reviewer_headers)
        assert r.status_code == 200

    def test_default_row_cannot_be_rekeyed(self, client, auth_headers):
        p = _create_pipeline_prompt(client, auth_headers, "default_locked")
        client.put(f"{BASE}/{p['id']}/default", json={"is_default": True},
                   headers=auth_headers)
        r = client.put(f"{BASE}/{p['id']}", json={"variant": "teacher"},
                       headers=auth_headers)
        assert r.status_code == 422


class TestTagsConvergence:
    """Phase 8 tags hygiene: prompt_tags rows are canonical, the legacy
    comma-string stays mirrored, and tag search matches either store."""

    def _tag_rows(self, db, prompt_id):
        from promptops_app.database import PromptTag

        return sorted(
            t.tag for t in
            db.query(PromptTag).filter(PromptTag.prompt_id == prompt_id).all()
        )

    def test_create_writes_rows_and_mirrors_legacy_string(self, client,
                                                          auth_headers, db):
        from promptops_app.database import Prompt

        p = _create_pipeline_prompt(client, auth_headers, "tags_created")
        # No explicit tags -> the component fallback lands in both stores.
        assert self._tag_rows(db, p["id"]) == ["cdd"]
        assert db.get(Prompt, p["id"]).tags == "cdd"

    def test_create_with_explicit_tags_dedupes_and_normalizes(self, client,
                                                              auth_headers, db):
        from promptops_app.database import Prompt

        r = client.post(BASE, json={
            "name": "tags_explicit",
            "description": "d",
            "component_type": "cdd",
            "tags": " alpha , beta ,alpha,",
            "system_prompt": "SYS",
            "user_prompt_template": "USR",
        }, headers=auth_headers)
        assert r.status_code == 201, r.text
        pid = r.json()["id"]
        assert self._tag_rows(db, pid) == ["alpha", "beta"]
        assert db.get(Prompt, pid).tags == "alpha,beta"

    def test_update_replaces_both_stores(self, client, auth_headers, db):
        from promptops_app.database import Prompt

        p = _create_pipeline_prompt(client, auth_headers, "tags_updated")
        r = client.put(f"{BASE}/{p['id']}", json={"tags": "x,y"},
                       headers=auth_headers)
        assert r.status_code == 200
        db.expire_all()
        assert self._tag_rows(db, p["id"]) == ["x", "y"]
        assert db.get(Prompt, p["id"]).tags == "x,y"
        # Overlapping update — the kept tag must be reused, not delete+insert
        # (same composite PK in one flush would collide).
        r = client.put(f"{BASE}/{p['id']}", json={"tags": "y,z"},
                       headers=auth_headers)
        assert r.status_code == 200
        db.expire_all()
        assert self._tag_rows(db, p["id"]) == ["y", "z"]
        assert db.get(Prompt, p["id"]).tags == "y,z"
        # Clearing with an empty string empties both stores.
        r = client.put(f"{BASE}/{p['id']}", json={"tags": ""},
                       headers=auth_headers)
        assert r.status_code == 200
        db.expire_all()
        assert self._tag_rows(db, p["id"]) == []
        assert db.get(Prompt, p["id"]).tags == ""

    def test_list_prompts_tagged_matches_rows_or_legacy_string(self, client,
                                                               auth_headers, db):
        from promptops_app.database import Prompt, PromptTag
        from promptops_app.repositories import prompt_repository

        # Canonical rows only (post-convergence writer output).
        rows_only = Prompt(name="tagged_rows_only", owner="t", tags="")
        rows_only.tag_rows = [PromptTag(tag="special")]
        db.add(rows_only)
        # Legacy string only (a database that predates the backfill).
        legacy_only = Prompt(name="tagged_legacy_only", owner="t",
                             tags="special,old")
        db.add(legacy_only)
        db.commit()

        names = {p.name for p in
                 prompt_repository.list_prompts_tagged(db, "special")}
        assert {"tagged_rows_only", "tagged_legacy_only"} <= names

    def test_search_param_matches_tag_rows(self, client, auth_headers, db):
        from promptops_app.database import Prompt

        r = client.post(BASE, json={
            "name": "tags_searchable",
            "description": "plain description",
            "component_type": "cdd",
            "tags": "findme",
            "system_prompt": "SYS",
            "user_prompt_template": "USR",
        }, headers=auth_headers)
        assert r.status_code == 201, r.text
        # Blank the mirrored legacy string so only the canonical rows can match.
        db.get(Prompt, r.json()["id"]).tags = ""
        db.commit()
        r = client.get(BASE, params={"search": "findme"}, headers=auth_headers)
        assert r.status_code == 200
        assert "tags_searchable" in {i["name"] for i in r.json()["items"]}
