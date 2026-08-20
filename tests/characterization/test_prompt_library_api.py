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

    def test_meta_lists_categories_and_tags(self, client, auth_headers, db):
        # Phase 12b: categories stay library-derived (they feed the create
        # form), but the tag list is pipeline-scoped for pipeline managers —
        # the console list only surfaces CAS pipeline prompts now.
        from promptops_app.database import Prompt, PromptTag

        _create(client, auth_headers)
        pipe = Prompt(prompt_kind="pipeline", name="meta_pipe", owner="admin",
                      component_type="cdd")
        db.add(pipe)
        db.flush()
        db.add(PromptTag(prompt_id=pipe.id, tag="cdd"))
        db.commit()
        body = client.get("/api/v1/prompt-library/meta", headers=auth_headers).json()
        assert "Course Development" in body["categories"]
        assert "cdd" in body["tags"]
        assert "kickoff" not in body["tags"]  # library tags left the admin console

    def test_meta_tags_stay_library_scoped_for_non_admins(self, client, auth_headers,
                                                          author_headers, db):
        from promptops_app.database import Prompt, PromptTag

        _create(client, auth_headers, visibility="global")
        pipe = Prompt(prompt_kind="pipeline", name="meta_pipe2", owner="admin",
                      component_type="style")
        db.add(pipe)
        db.flush()
        db.add(PromptTag(prompt_id=pipe.id, tag="style"))
        db.commit()
        body = client.get("/api/v1/prompt-library/meta", headers=author_headers).json()
        assert "kickoff" in body["tags"]
        assert "style" not in body["tags"]


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
        # Additive contract change (prompt-delete ticket): the body still
        # carries `deleted`, and now also reports that the delete was an
        # ARCHIVE and which generation bindings it had to release to get
        # there — None when the prompt held none, as here.
        assert resp.json() == {
            "deleted": created["id"], "archived": True, "released": None,
        }
        # Hidden from list and 404 on read (deleted_at soft-delete filter).
        listing = client.get(
            "/api/v1/prompt-library/prompts", headers=auth_headers
        ).json()
        assert all(p["id"] != created["id"] for p in listing)
        # Phase 12e contract: pipeline managers can still OPEN an archived row
        # by id (to inspect/restore — doc §9 "unless explicitly enabled");
        # everyone else keeps the pre-existing 404.
        read = client.get(
            f"/api/v1/prompt-library/prompts/{created['id']}", headers=auth_headers
        )
        assert read.status_code == 200 and read.json()["archived"] is True

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


class TestDuplicateCheckEndpoint:
    """POST /prompts/duplicate-check — Phase 11 advisory dedup probe."""

    def test_probe_finds_normalized_match(self, client, auth_headers):
        created = _create(client, auth_headers).json()
        r = client.post(
            "/api/v1/prompt-library/prompts/duplicate-check",
            json={"content": "  draft a KICKOFF for {{course_name}}   aimed at {{audience}}. "},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        dup = r.json()["duplicate_of"]
        assert dup and dup["id"] == created["id"] and dup["kind"] == "library"

        r = client.post(
            "/api/v1/prompt-library/prompts/duplicate-check",
            json={"content": "entirely different body"},
            headers=auth_headers,
        )
        assert r.json()["duplicate_of"] is None

    def test_probe_requires_manage_permission(self, client, author_headers):
        r = client.post(
            "/api/v1/prompt-library/prompts/duplicate-check",
            json={"content": "x"},
            headers=author_headers,
        )
        assert r.status_code == 403


class TestCategoryMandatory:
    """Phase 11 (doc §10) — category required on library create/update."""

    def test_create_without_category_400s(self, client, auth_headers):
        for cat in (None, "", "   "):
            r = _create(client, auth_headers, category=cat)
            assert r.status_code == 400, r.text
            assert "category" in r.json()["detail"]

    def test_update_to_blank_category_400s(self, client, auth_headers):
        created = _create(client, auth_headers).json()
        r = client.put(
            f"/api/v1/prompt-library/prompts/{created['id']}",
            json={"category": "  "},
            headers=auth_headers,
        )
        assert r.status_code == 400
        # Omitting category entirely stays fine (partial update).
        r = client.put(
            f"/api/v1/prompt-library/prompts/{created['id']}",
            json={"description": "still fine"},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text

    def test_probe_pipeline_kind_admin_and_reviewer_coercion(self, client, auth_headers, db):
        # Admin probing kind=pipeline matches pipeline rows' active content.
        from promptops_app.database import Prompt, PromptVersion

        pipe = Prompt(prompt_kind="pipeline", name="dup_pipe", owner="admin",
                      component_type="cdd")
        db.add(pipe)
        db.flush()
        db.add(PromptVersion(prompt_id=pipe.id, version="v1", version_number=1,
                             system_prompt="S", user_prompt_template="PIPE BODY",
                             is_active=True, created_by="admin"))
        db.commit()
        r = client.post(
            "/api/v1/prompt-library/prompts/duplicate-check",
            json={"content": "pipe  body", "kind": "pipeline"},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        dup = r.json()["duplicate_of"]
        assert dup and dup["id"] == pipe.id and dup["kind"] == "pipeline"
        # Unknown kinds coerce to library — never a 500, never a pipeline probe.
        r = client.post(
            "/api/v1/prompt-library/prompts/duplicate-check",
            json={"content": "pipe body", "kind": "weird"},
            headers=auth_headers,
        )
        assert r.status_code == 200 and r.json()["duplicate_of"] is None


class TestUnifiedListAndCasCategory:
    """Phase 12 — one list across kinds + CAS-category filter param."""

    def _seed_pipeline(self, db, name="uni_cdd", component="cdd", variant=None):
        from promptops_app.database import Prompt

        p = Prompt(prompt_kind="pipeline", name=name, owner="admin",
                   component_type=component, variant=variant)
        db.add(p)
        db.commit()
        return p

    def test_kind_all_returns_both_kinds_for_admins(self, client, auth_headers, db):
        created = _create(client, auth_headers).json()
        pipe = self._seed_pipeline(db)
        r = client.get("/api/v1/prompt-library/prompts?kind=all", headers=auth_headers)
        assert r.status_code == 200, r.text
        kinds = {row["id"]: row["prompt_kind"] for row in r.json()}
        assert kinds.get(created["id"]) == "library"
        assert kinds.get(pipe.id) == "pipeline"

    def test_cas_category_filters_to_the_slot(self, client, auth_headers, db):
        _create(client, auth_headers, category="CDD")  # freeform decoy
        pipe = self._seed_pipeline(db, name="uni_cas_cdd")
        self._seed_pipeline(db, name="uni_cas_style", component="style")
        r = client.get(
            "/api/v1/prompt-library/prompts?kind=all&cas_category=CDD",
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        assert [row["id"] for row in r.json()] == [pipe.id]

    def test_cas_category_is_empty_for_non_admins(self, client, author_headers, db):
        self._seed_pipeline(db, name="uni_hidden")
        r = client.get(
            "/api/v1/prompt-library/prompts?kind=all&cas_category=CDD",
            headers=author_headers,
        )
        assert r.status_code == 200
        assert r.json() == []


class TestPromoteEndpoint:
    """Phase 12 — POST /prompts/{pid}/promote (library → pipeline, in place)."""

    @staticmethod
    def _reviewer_headers(client, db):
        from app.core.security import hash_password
        from promptops_app.database import User

        db.add(User(username="test_reviewer", password_hash=hash_password("test_password"),
                    role="reviewer", is_active=True, is_platform_admin=True))
        db.commit()
        r = client.post("/api/v1/auth/login",
                        json={"username": "test_reviewer", "password": "test_password",
                              "platform_admin": True})
        assert r.status_code == 200
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    def _promote(self, client, headers, pid, **body):
        payload = {"component_type": "blueprint"}
        payload.update(body)
        return client.post(f"/api/v1/prompt-library/prompts/{pid}/promote",
                           json=payload, headers=headers)

    def test_promote_flips_kind_in_place_and_stays_inert(self, client, auth_headers):
        created = _create(client, auth_headers,
                          title="Management Blueprint Prompt", category="Blueprint").json()
        r = self._promote(client, auth_headers, created["id"])
        assert r.status_code == 200, r.text
        body = r.json()
        # Same master record: id and version history survive the kind flip.
        assert body["id"] == created["id"]
        assert body["prompt_kind"] == "pipeline"
        assert len(body["versions"]) == len(created["versions"])
        pipe = body["pipeline"]
        assert pipe["component_type"] == "blueprint" and pipe["variant"] is None
        assert pipe["name"] == "management_blueprint_prompt"
        # Inert for generation until an admin defaults/locks it.
        assert pipe["is_default"] is False
        # Freeform category no longer shadows the CAS classification.
        assert body["category"] == ""

    def test_promoted_row_leaves_the_library_write_surface(self, client, auth_headers):
        created = _create(client, auth_headers).json()
        assert self._promote(client, auth_headers, created["id"]).status_code == 200
        r = client.put(f"/api/v1/prompt-library/prompts/{created['id']}",
                       json={"description": "nope"}, headers=auth_headers)
        assert r.status_code == 404
        r = self._promote(client, auth_headers, created["id"])  # double-promote
        assert r.status_code == 404

    def test_registry_name_collisions_get_suffixed(self, client, auth_headers):
        first = _create(client, auth_headers, title="Same Title").json()
        second = _create(client, auth_headers, title="Same Title").json()
        assert self._promote(client, auth_headers, first["id"]).json()["pipeline"]["name"] == "same_title"
        assert self._promote(client, auth_headers, second["id"]).json()["pipeline"]["name"] == "same_title_2"

    def test_promote_into_the_component_slot_keeps_variant(self, client, auth_headers):
        created = _create(client, auth_headers, title="Widget Builder").json()
        r = self._promote(client, auth_headers, created["id"],
                          component_type="generate", variant="Interactive")
        assert r.status_code == 200, r.text
        pipe = r.json()["pipeline"]
        assert (pipe["component_type"], pipe["variant"]) == ("generate", "interactive")

    def test_invalid_component_and_hierarchy_400(self, client, auth_headers):
        created = _create(client, auth_headers).json()
        r = self._promote(client, auth_headers, created["id"], component_type="nonsense")
        assert r.status_code == 400
        parent = _create(client, auth_headers, title="Parent").json()
        child = _create(client, auth_headers, title="Child", parent_id=parent["id"]).json()
        assert self._promote(client, auth_headers, child["id"]).status_code == 400
        assert self._promote(client, auth_headers, parent["id"]).status_code == 400

    def test_promote_requires_pipeline_manager_role(self, client, auth_headers, db):
        created = _create(client, auth_headers).json()
        reviewer = self._reviewer_headers(client, db)
        # Reviewers hold prompt_library.manage but are not pipeline managers.
        r = self._promote(client, reviewer, created["id"])
        assert r.status_code == 403
        assert "Pipeline manager" in r.json()["detail"]
