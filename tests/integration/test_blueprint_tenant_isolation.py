"""Cross-tenant isolation for the Blueprint router (app/api/v1/routers/blueprints.py).

Same bug shape as test_cdd_tenant_isolation.py, found in the same audit:
``_get_blueprint_or_404`` called ``blueprint_repository.get_blueprint_by_id`` —
a completely unfiltered ``WHERE id = blueprint_id`` — with no tenant check at
all, at all ~16 of its call sites (get/update/archive/restore/versions/pin/
export/…). ``list_blueprints`` was worse than CDD's equivalent: omitting
course_id fell straight to ``list_all_blueprints`` (every tenant, unfiltered)
for ANY authenticated user, with no role check at all, and an explicit
``project_id`` query param was honored verbatim for anyone.
"""

from __future__ import annotations


def _blueprint(db, *, project_id, title="Source Blueprint", module_title="Module 1"):
    from promptops_app.database import ModuleBlueprint

    bp = ModuleBlueprint(title=title, module_title=module_title, project_id=project_id)
    db.add(bp)
    db.commit()
    db.refresh(bp)
    return bp


class TestBlueprintByIdTenantIsolation:
    def test_get_404s_for_another_tenant(self, client, db, two_tenants):
        bp = _blueprint(db, project_id=two_tenants["a"].id)
        assert client.get(f"/api/v1/blueprints/{bp.id}", headers=two_tenants["headers_b"]).status_code == 404
        assert client.get(f"/api/v1/blueprints/{bp.id}", headers=two_tenants["headers_a"]).status_code == 200

    def test_platform_admin_still_reaches_every_tenants_blueprint(self, client, db, two_tenants):
        bp = _blueprint(db, project_id=two_tenants["a"].id)
        resp = client.get(f"/api/v1/blueprints/{bp.id}", headers=two_tenants["headers_platform"])
        assert resp.status_code == 200

    def test_references_404s_for_another_tenant(self, client, db, two_tenants):
        bp = _blueprint(db, project_id=two_tenants["a"].id)
        resp = client.get(f"/api/v1/blueprints/{bp.id}/references", headers=two_tenants["headers_b"])
        assert resp.status_code == 404

    def test_list_versions_404s_for_another_tenant(self, client, db, two_tenants):
        bp = _blueprint(db, project_id=two_tenants["a"].id)
        resp = client.get(f"/api/v1/blueprints/{bp.id}/versions", headers=two_tenants["headers_b"])
        assert resp.status_code == 404

    def test_archive_is_blocked_for_another_tenant(self, client, db, two_tenants):
        from promptops_app.database import ModuleBlueprint

        bp = _blueprint(db, project_id=two_tenants["a"].id)
        resp = client.delete(f"/api/v1/blueprints/{bp.id}", headers=two_tenants["headers_b"])
        assert resp.status_code == 404
        assert db.query(ModuleBlueprint).filter_by(id=bp.id).first().deleted_at is None

    def test_archive_and_restore_work_for_own_tenant(self, client, db, two_tenants):
        bp = _blueprint(db, project_id=two_tenants["a"].id)
        resp = client.delete(f"/api/v1/blueprints/{bp.id}", headers=two_tenants["headers_a"])
        assert resp.status_code == 200, resp.text
        resp = client.post(f"/api/v1/blueprints/{bp.id}/restore", headers=two_tenants["headers_a"])
        assert resp.status_code == 200, resp.text

    def test_restore_is_blocked_for_another_tenant(self, client, db, two_tenants):
        bp = _blueprint(db, project_id=two_tenants["a"].id)
        client.delete(f"/api/v1/blueprints/{bp.id}", headers=two_tenants["headers_a"])
        resp = client.post(f"/api/v1/blueprints/{bp.id}/restore", headers=two_tenants["headers_b"])
        assert resp.status_code == 404

    def test_shared_blueprint_is_readable_by_every_tenant(self, client, db, two_tenants):
        """A NULL project_id blueprint (legacy/shared) stays visible — the fix
        must not turn every un-migrated legacy row into a 404 for everyone."""
        bp = _blueprint(db, project_id=None, title="Legacy Shared Blueprint")
        for hdrs in (two_tenants["headers_a"], two_tenants["headers_b"]):
            assert client.get(f"/api/v1/blueprints/{bp.id}", headers=hdrs).status_code == 200


class TestListBlueprintsTenantIsolation:
    def test_tenant_b_cannot_see_tenant_a_blueprint(self, client, db, two_tenants):
        bp = _blueprint(db, project_id=two_tenants["a"].id, title="A-only Blueprint")
        resp = client.get("/api/v1/blueprints", headers=two_tenants["headers_b"])
        assert resp.status_code == 200
        ids = [b["id"] for b in resp.json()["items"]]
        assert bp.id not in ids

    def test_tenant_a_sees_its_own_blueprint(self, client, db, two_tenants):
        bp = _blueprint(db, project_id=two_tenants["a"].id, title="A-own-visible Blueprint")
        resp = client.get("/api/v1/blueprints", headers=two_tenants["headers_a"])
        assert resp.status_code == 200
        ids = [b["id"] for b in resp.json()["items"]]
        assert bp.id in ids

    def test_tenant_caller_cannot_use_project_id_param_to_see_another_tenant(self, client, db, two_tenants):
        """The exact hole: any authenticated tenant caller passing
        ?project_id=<another tenant> used to be honored verbatim instead of
        being scoped to their own tenant."""
        bp = _blueprint(db, project_id=two_tenants["a"].id, title="A Blueprint via spoofed param")
        resp = client.get(
            "/api/v1/blueprints", params={"project_id": two_tenants["a"].id}, headers=two_tenants["headers_b"],
        )
        assert resp.status_code == 200
        ids = [b["id"] for b in resp.json()["items"]]
        assert bp.id not in ids

    def test_tenant_caller_omitting_all_filters_does_not_see_every_tenant(self, client, db, two_tenants):
        """The other half of the same hole: omitting project_id AND course_id
        used to fall through to list_all_blueprints — every tenant,
        unfiltered — for ANY authenticated user, no role check at all."""
        bp_a = _blueprint(db, project_id=two_tenants["a"].id, title="A Blueprint no filters")
        bp_b = _blueprint(db, project_id=two_tenants["b"].id, title="B Blueprint no filters")
        resp = client.get("/api/v1/blueprints", headers=two_tenants["headers_b"])
        assert resp.status_code == 200
        ids = [b["id"] for b in resp.json()["items"]]
        assert bp_b.id in ids
        assert bp_a.id not in ids

    def test_platform_admin_with_no_project_id_sees_every_tenant(self, client, db, two_tenants):
        bp_a = _blueprint(db, project_id=two_tenants["a"].id, title="A Blueprint platform view")
        bp_b = _blueprint(db, project_id=two_tenants["b"].id, title="B Blueprint platform view")
        resp = client.get("/api/v1/blueprints", headers=two_tenants["headers_platform"])
        assert resp.status_code == 200
        ids = [b["id"] for b in resp.json()["items"]]
        assert bp_a.id in ids
        assert bp_b.id in ids

    def test_platform_admin_viewing_as_tenant_a_sees_only_tenant_a(self, client, db, two_tenants):
        bp_a = _blueprint(db, project_id=two_tenants["a"].id, title="A Blueprint viewed-as")
        bp_b = _blueprint(db, project_id=two_tenants["b"].id, title="B Blueprint viewed-as")
        resp = client.get(
            "/api/v1/blueprints", params={"project_id": two_tenants["a"].id}, headers=two_tenants["headers_platform"],
        )
        assert resp.status_code == 200
        ids = [b["id"] for b in resp.json()["items"]]
        assert bp_a.id in ids
        assert bp_b.id not in ids
