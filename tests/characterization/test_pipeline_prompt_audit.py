"""Audit emission for the pipeline prompt registry (/api/v1/prompts).

Pipeline-prompt writes land in the unified ``audit_logs`` table under the
``prompt.`` action family, so the console's Audit Log (which filters on the
PL prefixes) surfaces them alongside the library events. Before this suite
existed the pipeline router wrote no audit rows at all — a compliance hole
the Audit Log page silently hid.
"""

from __future__ import annotations


def _events(client, headers):
    resp = client.get("/api/v1/prompt-library/audit", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]


def _create_pipeline_prompt(client, headers, name="audit_probe", **overrides):
    payload = {
        "name": name,
        "description": "audit characterization",
        "component_type": "cdd",
        "system_prompt": "You are a course designer.",
        "user_prompt_template": "Design {{topic}}.",
    }
    payload.update(overrides)
    resp = client.post("/api/v1/prompts", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestCreatePaths:
    def test_create_emits_pipeline_create(self, client, auth_headers):
        created = _create_pipeline_prompt(client, auth_headers)
        events = _events(client, auth_headers)
        match = [e for e in events
                 if e["event_type"] == "prompt.pipeline_create"
                 and e["entity_id"] == created["id"]]
        assert match, events
        e = match[0]
        assert e["action"] == "pipeline_create"
        assert e["actor_username"] == "test_admin"
        assert e["actor_role"] == "admin"
        assert "audit_probe" in e["summary"]

    def test_create_from_template_emits_pipeline_create(self, client, auth_headers):
        from promptops_app.prompt_templates import PROMPT_TEMPLATES

        template_name = next(iter(PROMPT_TEMPLATES))
        resp = client.post("/api/v1/prompts/from-template",
                           json={"template_name": template_name},
                           headers=auth_headers)
        assert resp.status_code == 201, resp.text
        events = _events(client, auth_headers)
        assert any(e["event_type"] == "prompt.pipeline_create"
                   and template_name in e["summary"] for e in events)


class TestVersionLifecycle:
    def test_commit_and_deploy_emit_events(self, client, auth_headers):
        created = _create_pipeline_prompt(client, auth_headers, name="audit_versions")
        pid = created["id"]

        resp = client.post(f"/api/v1/prompts/{pid}/versions", json={
            "version": "v2",
            "system_prompt": "Updated system.",
            "user_prompt_template": "Updated {{topic}}.",
            "change_reason": "audit test",
        }, headers=auth_headers)
        assert resp.status_code == 201, resp.text

        resp = client.post(f"/api/v1/prompts/{pid}/versions/v1/deploy",
                           headers=auth_headers)
        assert resp.status_code == 200, resp.text

        types = {e["event_type"] for e in _events(client, auth_headers)
                 if e["entity_id"] == pid}
        assert {"prompt.pipeline_create", "prompt.version_commit",
                "prompt.version_deploy"} <= types

    def test_workflow_transition_emits_event(self, client, auth_headers, db):
        from promptops_app.database import PromptVersion

        created = _create_pipeline_prompt(client, auth_headers, name="audit_state")
        pid = created["id"]
        db.add(PromptVersion(
            prompt_id=pid, version="v9", version_number=9,
            system_prompt="s", user_prompt_template="u",
            change_reason="draft for state test", is_active=False,
            workflow_state="draft", created_by="test_admin",
        ))
        db.commit()

        resp = client.post(f"/api/v1/prompts/{pid}/versions/v9/state",
                           json={"state": "in_review"}, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        assert any(e["event_type"] == "prompt.workflow_state"
                   and e["entity_id"] == pid
                   and "draft → in_review" in e["summary"]
                   for e in _events(client, auth_headers))


class TestManagementEndpoints:
    def test_default_flag_set_and_cleared(self, client, auth_headers):
        pid = _create_pipeline_prompt(client, auth_headers, name="audit_default")["id"]
        assert client.put(f"/api/v1/prompts/{pid}/default", json={"is_default": True},
                          headers=auth_headers).status_code == 200
        assert client.put(f"/api/v1/prompts/{pid}/default", json={"is_default": False},
                          headers=auth_headers).status_code == 200
        types = {e["event_type"] for e in _events(client, auth_headers)
                 if e["entity_id"] == pid}
        assert {"prompt.default_set", "prompt.default_cleared"} <= types

    def test_variables_set_emits_event(self, client, auth_headers):
        pid = _create_pipeline_prompt(client, auth_headers, name="audit_vars")["id"]
        resp = client.put(f"/api/v1/prompts/{pid}/variables",
                          json={"variables": [{"name": "topic", "label": "Topic"}]},
                          headers=auth_headers)
        assert resp.status_code == 200, resp.text
        assert any(e["event_type"] == "prompt.variables_set"
                   and e["entity_id"] == pid and "topic" in e["summary"]
                   for e in _events(client, auth_headers))

    def test_scope_lock_set_and_removed(self, client, auth_headers):
        pid = _create_pipeline_prompt(client, auth_headers, name="audit_lock")["id"]
        resp = client.put("/api/v1/prompts/fixings", json={
            "component": "cdd", "scope_level": "global", "prompt_id": pid,
        }, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        resp = client.delete("/api/v1/prompts/fixings?component=cdd&scope_level=global",
                             headers=auth_headers)
        assert resp.status_code == 204, resp.text

        events = _events(client, auth_headers)
        lock = [e for e in events if e["event_type"] == "prompt.scope_lock_set"]
        assert lock and lock[0]["entity_id"] == pid
        assert lock[0]["changes"] == {"scope_level": "global"}
        assert any(e["event_type"] == "prompt.scope_lock_removed" for e in events)

    def test_fragment_set_emits_event(self, client, auth_headers):
        resp = client.put("/api/v1/prompts/fragments/guardrails",
                          json={"content": "Never invent citations."},
                          headers=auth_headers)
        assert resp.status_code == 200, resp.text
        match = [e for e in _events(client, auth_headers)
                 if e["event_type"] == "prompt.fragment_set"]
        assert match
        assert match[0]["entity_type"] == "fragment"
        assert match[0]["entity_id"] == "guardrails"
        assert "v1" in match[0]["summary"]

    def test_delete_emits_event(self, client, auth_headers):
        pid = _create_pipeline_prompt(client, auth_headers, name="audit_delete")["id"]
        assert client.delete(f"/api/v1/prompts/{pid}",
                             headers=auth_headers).status_code == 204
        assert any(e["event_type"] == "prompt.pipeline_delete"
                   and e["entity_id"] == pid and "audit_delete" in e["summary"]
                   for e in _events(client, auth_headers))
