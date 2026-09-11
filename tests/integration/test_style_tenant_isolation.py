"""Cross-tenant isolation for the Style router (app/api/v1/routers/styles.py).

Found while investigating the prompt-tenant-isolation ticket, same class of
bug as CDD/Blueprint: ``_get_style_or_404`` called
``style_repository.get_style_by_id`` — a completely unfiltered
``WHERE id = style_id`` — with no tenant check at all, at all ~9 of its call
sites (get/update/delete/activate/deactivate/understand/append-documents).
``list_styles`` had no tenant enforcement whatsoever: omitting project_id and
course_id returned ``get_styles(db)``, every tenant's styles, "by design" per
its own docstring; a tenant-supplied project_id was also honored verbatim,
letting one tenant view another's styles just by passing its project_id.

Same zero-tolerance spirit as test_cdd_tenant_isolation.py.
"""

from __future__ import annotations


def _style(db, *, project_id, name="Source Style"):
    from promptops_app.database import Style

    style = Style(style_id=f"style-{name.lower().replace(' ', '-')}-{project_id}",
                  name=name, project_id=project_id)
    db.add(style)
    db.commit()
    db.refresh(style)
    return style


class TestStyleByIdTenantIsolation:
    def test_get_404s_for_another_tenant(self, client, db, two_tenants):
        style = _style(db, project_id=two_tenants["a"].id)
        assert client.get(f"/api/v1/styles/{style.id}", headers=two_tenants["headers_b"]).status_code == 404
        assert client.get(f"/api/v1/styles/{style.id}", headers=two_tenants["headers_a"]).status_code == 200

    def test_platform_admin_still_reaches_every_tenants_style(self, client, db, two_tenants):
        style = _style(db, project_id=two_tenants["a"].id)
        resp = client.get(f"/api/v1/styles/{style.id}", headers=two_tenants["headers_platform"])
        assert resp.status_code == 200

    def test_update_is_blocked_for_another_tenant(self, client, db, two_tenants):
        style = _style(db, project_id=two_tenants["a"].id)
        resp = client.put(
            f"/api/v1/styles/{style.id}", json={"name": "Hijacked"}, headers=two_tenants["headers_b"],
        )
        assert resp.status_code == 404

    def test_update_works_for_own_tenant(self, client, db, two_tenants):
        style = _style(db, project_id=two_tenants["a"].id)
        resp = client.put(
            f"/api/v1/styles/{style.id}", json={"name": "Renamed"}, headers=two_tenants["headers_a"],
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["name"] == "Renamed"

    def test_delete_is_blocked_for_another_tenant(self, client, db, two_tenants):
        from promptops_app.database import Style

        style = _style(db, project_id=two_tenants["a"].id)
        resp = client.delete(f"/api/v1/styles/{style.id}", headers=two_tenants["headers_b"])
        assert resp.status_code == 404
        assert db.query(Style).filter_by(id=style.id).first() is not None

    def test_activate_is_blocked_for_another_tenant(self, client, db, two_tenants):
        style = _style(db, project_id=two_tenants["a"].id)
        resp = client.post(
            f"/api/v1/styles/{style.id}/activate",
            json={"project_id": two_tenants["a"].id},
            headers=two_tenants["headers_b"],
        )
        assert resp.status_code == 404

    def test_deactivate_is_blocked_for_another_tenant(self, client, db, two_tenants):
        style = _style(db, project_id=two_tenants["a"].id)
        resp = client.post(f"/api/v1/styles/{style.id}/deactivate", headers=two_tenants["headers_b"])
        assert resp.status_code == 404

    def test_shared_style_is_readable_by_every_tenant(self, client, db, two_tenants):
        """A NULL project_id style (legacy/unowned) stays visible by direct id
        — the fix must not turn every un-migrated legacy row into a 404."""
        style = _style(db, project_id=None, name="Legacy Shared Style")
        for hdrs in (two_tenants["headers_a"], two_tenants["headers_b"]):
            assert client.get(f"/api/v1/styles/{style.id}", headers=hdrs).status_code == 200


class TestListStylesTenantIsolation:
    def test_tenant_b_cannot_see_tenant_a_style(self, client, db, two_tenants):
        style = _style(db, project_id=two_tenants["a"].id, name="A-only Style")
        resp = client.get(
            "/api/v1/styles", params={"project_id": two_tenants["a"].id}, headers=two_tenants["headers_b"],
        )
        assert resp.status_code == 200
        ids = [s["id"] for s in resp.json()["items"]]
        assert style.id not in ids

    def test_tenant_a_sees_its_own_style(self, client, db, two_tenants):
        style = _style(db, project_id=two_tenants["a"].id, name="A-own-visible Style")
        resp = client.get(
            "/api/v1/styles", params={"project_id": two_tenants["a"].id}, headers=two_tenants["headers_a"],
        )
        assert resp.status_code == 200
        ids = [s["id"] for s in resp.json()["items"]]
        assert style.id in ids

    def test_tenant_cannot_use_project_id_param_to_see_another_tenant(self, client, db, two_tenants):
        """The exact hole: passing ?project_id=<another tenant> used to be
        honored verbatim instead of being scoped to the caller's own tenant."""
        style = _style(db, project_id=two_tenants["a"].id, name="A Style via spoofed param")
        resp = client.get(
            "/api/v1/styles", params={"project_id": two_tenants["a"].id}, headers=two_tenants["headers_b"],
        )
        assert resp.status_code == 200
        ids = [s["id"] for s in resp.json()["items"]]
        assert style.id not in ids

    def test_tenant_omitting_all_filters_does_not_see_every_tenant(self, client, db, two_tenants):
        """The other half of the same hole: omitting project_id AND course_id
        used to fall through to get_styles(db) — every tenant, unfiltered."""
        style_a = _style(db, project_id=two_tenants["a"].id, name="A Style no filters")
        style_b = _style(db, project_id=two_tenants["b"].id, name="B Style no filters")
        resp = client.get("/api/v1/styles", headers=two_tenants["headers_b"])
        assert resp.status_code == 200
        ids = [s["id"] for s in resp.json()["items"]]
        assert style_b.id in ids
        assert style_a.id not in ids

    def test_platform_admin_with_no_project_id_sees_every_tenant(self, client, db, two_tenants):
        style_a = _style(db, project_id=two_tenants["a"].id, name="A Style platform view")
        style_b = _style(db, project_id=two_tenants["b"].id, name="B Style platform view")
        resp = client.get("/api/v1/styles", headers=two_tenants["headers_platform"])
        assert resp.status_code == 200
        ids = [s["id"] for s in resp.json()["items"]]
        assert style_a.id in ids
        assert style_b.id in ids

    def test_platform_admin_viewing_as_tenant_a_sees_only_tenant_a(self, client, db, two_tenants):
        style_a = _style(db, project_id=two_tenants["a"].id, name="A Style viewed-as")
        style_b = _style(db, project_id=two_tenants["b"].id, name="B Style viewed-as")
        resp = client.get(
            "/api/v1/styles", params={"project_id": two_tenants["a"].id}, headers=two_tenants["headers_platform"],
        )
        assert resp.status_code == 200
        ids = [s["id"] for s in resp.json()["items"]]
        assert style_a.id in ids
        assert style_b.id not in ids

    def test_tenant_supplied_course_id_from_another_tenant_is_ignored(self, client, db, two_tenants):
        """A tenant passing a course_id belonging to a DIFFERENT tenant must
        not get that course's styles just because get_styles' own course_id
        branch does not itself verify course->project ownership."""
        from promptops_app.database import Course

        other_course = Course(project_id=two_tenants["b"].id, name="B's course")
        db.add(other_course)
        db.commit()
        db.refresh(other_course)
        style_b_course = _style(db, project_id=two_tenants["b"].id, name="B course-owned style")
        style_b_course.course_id = other_course.id
        db.commit()

        resp = client.get(
            "/api/v1/styles", params={"course_id": other_course.id}, headers=two_tenants["headers_a"],
        )
        assert resp.status_code == 200
        ids = [s["id"] for s in resp.json()["items"]]
        assert style_b_course.id not in ids


class TestStyleUnderstandReadsItsOwnTenantsLibrary:
    """S1 (AIM_PIPELINE_SHORTCOMINGS.txt): _retrieve_dis_style_context was the
    only Source Library retrieval in the codebase that never passed client_id,
    so it read the CALLING user's default tenant rather than the STYLE's own —
    a platform admin (default client 'cengage') generating Style Intelligence
    for an AIM style queried cengage's library, not AIM's.
    """

    def test_retrieval_is_scoped_to_the_styles_own_project_client(
        self, client, db, two_tenants, monkeypatch
    ):
        from promptops_app.database import Project

        # two_tenants' projects carry no client_name; give tenant A one so the
        # resolved client_id is observable and distinct from the platform
        # admin's own default ('cengage', per auth.py's seeded test user).
        proj = db.get(Project, two_tenants["a"].id)
        proj.client_name = "AIM"
        db.commit()

        style = _style(db, project_id=two_tenants["a"].id)

        calls = []

        def fake_retrieve(purpose, payload, current_user=None, client_id=""):
            calls.append(client_id)
            return {"combined_context": ""}

        monkeypatch.setattr(
            "app.api.v1.routers.styles.dis_client.retrieve_context_sync", fake_retrieve)
        # generate_style_understanding does the actual LLM call; stub it so this
        # test is purely about what retrieval was asked for, not generation.
        monkeypatch.setattr(
            "promptops_app.services.style_service.generate_style_understanding",
            lambda *a, **k: "Stubbed style understanding.",
        )

        resp = client.post(
            f"/api/v1/styles/{style.id}/understand",
            json={"document_ids": ["doc-1"]},
            headers=two_tenants["headers_platform"],
        )

        assert resp.status_code == 200, resp.text
        assert calls, "retrieve_context_sync was never called"
        assert calls[0] == "aim", (
            f"expected retrieval scoped to the style's own tenant ('aim'), got {calls[0]!r} "
            "-- this is the platform admin's personal default leaking through instead"
        )
