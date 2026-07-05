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

BASE = "/api/v1/prompts"


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
