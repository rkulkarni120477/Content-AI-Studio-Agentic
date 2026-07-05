"""
Characterization: /api/v1/prompt-library (currently backed by pl_* tables).

Captures the API contract the Phase 4/5 cutover to the native tables must
preserve: endpoint paths, payload conventions, response shapes, version-bump
semantics, soft delete, {{var}} rendering, RBAC gating, and audit emission.

IDs are asserted as OPAQUE round-trip values only — the plan (Phase 5)
switches them from UUID strings to integers, and nothing here should break
when that lands.
"""

from __future__ import annotations

import pytest


def _create(client, headers, **overrides):
    payload = {
        "title": "Course Kickoff Playbook",
        "content": "Draft a kickoff for {{course_name}} aimed at {{audience}}.",
        "description": "Characterization prompt",
        "category": "Course Development",
        "visibility": "draft",
        "tags": ["kickoff", "cte"],
        "variables": [
            {"name": "course_name", "label": "Course", "hint": "Full title"},
            {"name": "audience", "label": "Audience"},
        ],
    }
    payload.update(overrides)
    resp = client.post("/api/v1/prompt-library/prompts", json=payload, headers=headers)
    return resp


class TestCreate:
    def test_create_shape(self, client, auth_headers):
        resp = _create(client, auth_headers)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        # Contract keys the frontend relies on.
        assert {
            "id", "parent_id", "title", "content", "description", "category",
            "visibility", "teams", "created_by", "created_at", "updated_at",
            "last_used_at", "tags", "variables", "parent", "children",
            "versions", "can_have_children",
        } <= set(body.keys())
        assert body["title"] == "Course Kickoff Playbook"
        # Tags come back sorted, not in submission order.
        assert body["tags"] == ["cte", "kickoff"]
        assert [v["name"] for v in body["variables"]] == ["course_name", "audience"]
        # Every create writes version 1 immediately (instant-publish model).
        assert len(body["versions"]) == 1
        assert body["versions"][0]["version"] == 1
        assert body["created_by"] == "test_admin"

    def test_title_and_content_required(self, client, auth_headers):
        resp = client.post(
            "/api/v1/prompt-library/prompts",
            json={"title": "no content"},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_team_visibility_requires_team(self, client, auth_headers):
        resp = _create(client, auth_headers, visibility="team", teams=[])
        assert resp.status_code == 400


class TestReadAndRender:
    def test_id_round_trip(self, client, auth_headers):
        created = _create(client, auth_headers).json()
        resp = client.get(
            f"/api/v1/prompt-library/prompts/{created['id']}", headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == created["id"]

    def test_list_is_bare_array_without_page_params(self, client, auth_headers):
        # Pagination is OPT-IN: no page/limit params → a plain JSON array.
        _create(client, auth_headers)
        resp = client.get("/api/v1/prompt-library/prompts", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert any(p["title"] == "Course Kickoff Playbook" for p in body)

    def test_list_paginates_with_page_params(self, client, auth_headers):
        _create(client, auth_headers)
        resp = client.get(
            "/api/v1/prompt-library/prompts?page=1&limit=10", headers=auth_headers
        )
        body = resp.json()
        assert {"items", "total", "page", "limit"} <= set(body.keys())
        assert body["page"] == 1

    def test_render_double_brace_semantics(self, client, auth_headers):
        created = _create(client, auth_headers).json()
        resp = client.post(
            f"/api/v1/prompt-library/prompts/{created['id']}/render",
            json={"variables": {"course_name": "Welding 101", "unknown": "x"}},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        rendered = resp.json()["content"]
        assert "Welding 101" in rendered
        # Unsupplied variables stay as literal {{placeholders}}.
        assert "{{audience}}" in rendered

    def test_meta_lists_categories_and_tags(self, client, auth_headers):
        _create(client, auth_headers)
        body = client.get("/api/v1/prompt-library/meta", headers=auth_headers).json()
        assert "Course Development" in body["categories"]
        assert "kickoff" in body["tags"]


class TestUpdateVersioning:
    def test_content_change_with_create_version_bumps(self, client, auth_headers):
        created = _create(client, auth_headers).json()
        resp = client.put(
            f"/api/v1/prompt-library/prompts/{created['id']}",
            json={"content": "New body {{x}}", "create_version": True,
                  "version_note": "second pass"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["content"] == "New body {{x}}"
        assert [v["version"] for v in body["versions"]] == [1, 2]
        assert body["versions"][-1]["note"] == "second pass"

    def test_content_change_without_flag_does_not_version(self, client, auth_headers):
        created = _create(client, auth_headers).json()
        body = client.put(
            f"/api/v1/prompt-library/prompts/{created['id']}",
            json={"content": "Silent edit"},
            headers=auth_headers,
        ).json()
        assert body["content"] == "Silent edit"
        assert [v["version"] for v in body["versions"]] == [1]


class TestDeleteAndDuplicate:
    def test_delete_is_soft_and_hides_from_list(self, client, auth_headers):
        created = _create(client, auth_headers).json()
        resp = client.delete(
            f"/api/v1/prompt-library/prompts/{created['id']}", headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.json() == {"deleted": created["id"]}
        # Hidden from list and 404 on read (deleted_at soft-delete filter).
        listing = client.get(
            "/api/v1/prompt-library/prompts", headers=auth_headers
        ).json()
        assert all(p["id"] != created["id"] for p in listing)
        assert client.get(
            f"/api/v1/prompt-library/prompts/{created['id']}", headers=auth_headers
        ).status_code == 404

    def test_duplicate_creates_new_id(self, client, auth_headers):
        created = _create(client, auth_headers).json()
        resp = client.post(
            f"/api/v1/prompt-library/prompts/{created['id']}/duplicate",
            headers=auth_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["id"] != created["id"]


class TestTeams:
    def test_team_create_shape(self, client, auth_headers):
        # Phase 4/5 cutover: team ids are autoincrement integers; a
        # caller-supplied slug id is ignored and the slug lives in `name`
        # (the Phase 2 migration remapped prod's P1–P6 the same way).
        resp = client.post(
            "/api/v1/prompt-library/teams",
            json={"id": "P1", "name": "Pathway 1"},
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["id"] is not None and body["id"] != "P1"
        assert body["name"] == "Pathway 1"
        assert body["_user_count"] == 0 and body["_prompt_count"] == 0
        # Round-trip: the returned id is what the list shows.
        teams = client.get("/api/v1/prompt-library/teams", headers=auth_headers).json()
        assert any(t["id"] == body["id"] for t in teams)

    def test_duplicate_team_conflict(self, client, auth_headers):
        # Uniqueness (and the 409) moved from the old slug id to the name.
        client.post("/api/v1/prompt-library/teams",
                    json={"name": "Pathway 1"}, headers=auth_headers)
        resp = client.post("/api/v1/prompt-library/teams",
                           json={"name": "pathway 1"}, headers=auth_headers)
        assert resp.status_code == 409


class TestRBAC:
    def test_author_can_view_but_not_manage(self, client, auth_headers, author_headers):
        created = _create(client, auth_headers, visibility="global").json()
        # view: allowed
        assert client.get(
            "/api/v1/prompt-library/prompts", headers=author_headers
        ).status_code == 200
        # manage (create/update/delete): admin+reviewer only
        assert _create(client, author_headers).status_code == 403
        assert client.delete(
            f"/api/v1/prompt-library/prompts/{created['id']}",
            headers=author_headers,
        ).status_code == 403

    def test_draft_visibility_hides_from_other_roles(self, client, auth_headers, author_headers):
        _create(client, auth_headers, visibility="draft",
                title="Admin Draft Only")
        items = client.get(
            "/api/v1/prompt-library/prompts", headers=author_headers
        ).json()
        assert all(p["title"] != "Admin Draft Only" for p in items)


class TestReviews:
    def test_review_round_trip(self, client, auth_headers, author_headers):
        created = _create(client, auth_headers, visibility="global").json()
        resp = client.post(
            f"/api/v1/prompt-library/prompts/{created['id']}/reviews",
            json={"rating": 4, "feedback": "solid"},
            headers=author_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["rating"] == 4 and body["username"] == "test_author"
        # managers see reviews; the author role gets an empty list back
        assert client.get(
            f"/api/v1/prompt-library/prompts/{created['id']}/reviews",
            headers=author_headers,
        ).json() == []
        reviews = client.get(
            f"/api/v1/prompt-library/prompts/{created['id']}/reviews",
            headers=auth_headers,
        ).json()
        assert [r["rating"] for r in reviews] == [4]
        # resubmission upserts (unique per prompt+user), never duplicates
        client.post(
            f"/api/v1/prompt-library/prompts/{created['id']}/reviews",
            json={"rating": 2}, headers=author_headers,
        )
        reviews = client.get(
            f"/api/v1/prompt-library/prompts/{created['id']}/reviews",
            headers=auth_headers,
        ).json()
        assert [r["rating"] for r in reviews] == [2]


class TestRequests:
    def test_request_round_trip(self, client, auth_headers, author_headers):
        resp = client.post(
            "/api/v1/prompt-library/requests",
            json={"title": "Need a rubric prompt", "type": "new"},
            headers=author_headers,
        )
        assert resp.status_code == 201, resp.text
        rid = resp.json()["id"]
        assert resp.json()["status"] == "open"
        # authors list only their own requests
        mine = client.get("/api/v1/prompt-library/requests", headers=author_headers).json()
        assert [r["id"] for r in mine] == [rid]
        # manager updates status + notes
        upd = client.put(
            f"/api/v1/prompt-library/requests/{rid}",
            json={"status": "done", "admin_notes": "added"},
            headers=auth_headers,
        )
        assert upd.status_code == 200
        assert upd.json()["status"] == "done"


class TestAttachments:
    def test_attachment_upload_download_delete(self, client, auth_headers, tmp_path, monkeypatch):
        import app.api.v1.routers.prompt_library as plr
        monkeypatch.setattr(plr, "_ATTACH_DIR", str(tmp_path))
        created = _create(client, auth_headers).json()
        resp = client.post(
            f"/api/v1/prompt-library/prompts/{created['id']}/attachments",
            files={"file": ("notes.txt", b"attachment body", "text/plain")},
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        att = resp.json()
        assert att["original_name"] == "notes.txt" and att["size"] == 15
        dl = client.get(
            f"/api/v1/prompt-library/prompts/{created['id']}/attachments/{att['id']}/download",
            headers=auth_headers,
        )
        assert dl.status_code == 200 and dl.content == b"attachment body"
        assert client.delete(
            f"/api/v1/prompt-library/prompts/{created['id']}/attachments/{att['id']}",
            headers=auth_headers,
        ).json() == {"deleted": att["id"]}

    def test_disallowed_extension_rejected(self, client, auth_headers, tmp_path, monkeypatch):
        import app.api.v1.routers.prompt_library as plr
        monkeypatch.setattr(plr, "_ATTACH_DIR", str(tmp_path))
        created = _create(client, auth_headers).json()
        resp = client.post(
            f"/api/v1/prompt-library/prompts/{created['id']}/attachments",
            files={"file": ("evil.exe", b"x", "application/octet-stream")},
            headers=auth_headers,
        )
        assert resp.status_code == 400


class TestAudit:
    def test_writes_emit_audit_events(self, client, auth_headers):
        created = _create(client, auth_headers).json()
        resp = client.get("/api/v1/prompt-library/audit", headers=auth_headers)
        assert resp.status_code == 200
        events = resp.json()["items"]
        assert any(
            e["event_type"] == "prompt.create" and e["entity_id"] == created["id"]
            for e in events
        )
        # Shape the audit UI depends on.
        assert {
            "id", "event_type", "action", "actor_username", "actor_role",
            "entity_type", "entity_id", "summary", "changes", "created_at",
        } <= set(events[0].keys())
