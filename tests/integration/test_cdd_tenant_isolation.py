"""Cross-tenant isolation for the CDD router (app/api/v1/routers/cdd.py).

Found while investigating the prompt-tenant-isolation ticket: ``_get_cdd_or_404``
called ``cdd_repository.get_cdd_by_id`` — a completely unfiltered
``WHERE id = cdd_id`` — with no tenant check at all, at all ~14 of its call
sites (get/update/archive/restore/versions/pin/export/…). ``list_cdds`` had a
parallel hole: it gated on ``current_user.role == "admin"`` (a per-tenant
membership role, not platform-admin) instead of ``is_platform_admin``, so any
tenant's own admin could pass another tenant's ``project_id`` — or omit
project_id/course_id entirely and fall through to ``list_all_cdds``, every
tenant, unfiltered — and see it.

Same zero-tolerance spirit as test_generation_trace_isolation.py and
test_prompt_library_tenant_isolation.py.
"""

from __future__ import annotations


def _cdd(db, *, project_id, title="Source CDD", course_title="Source Course"):
    from promptops_app.database import CourseDesignDocument

    cdd = CourseDesignDocument(title=title, course_title=course_title, project_id=project_id)
    db.add(cdd)
    db.commit()
    db.refresh(cdd)
    return cdd


class TestCddByIdTenantIsolation:
    def test_get_404s_for_another_tenant(self, client, db, two_tenants):
        cdd = _cdd(db, project_id=two_tenants["a"].id)
        assert client.get(f"/api/v1/cdd/{cdd.id}", headers=two_tenants["headers_b"]).status_code == 404
        assert client.get(f"/api/v1/cdd/{cdd.id}", headers=two_tenants["headers_a"]).status_code == 200

    def test_platform_admin_still_reaches_every_tenants_cdd(self, client, db, two_tenants):
        cdd = _cdd(db, project_id=two_tenants["a"].id)
        resp = client.get(f"/api/v1/cdd/{cdd.id}", headers=two_tenants["headers_platform"])
        assert resp.status_code == 200

    def test_references_404s_for_another_tenant(self, client, db, two_tenants):
        cdd = _cdd(db, project_id=two_tenants["a"].id)
        resp = client.get(f"/api/v1/cdd/{cdd.id}/references", headers=two_tenants["headers_b"])
        assert resp.status_code == 404

    def test_list_versions_404s_for_another_tenant(self, client, db, two_tenants):
        cdd = _cdd(db, project_id=two_tenants["a"].id)
        resp = client.get(f"/api/v1/cdd/{cdd.id}/versions", headers=two_tenants["headers_b"])
        assert resp.status_code == 404

    def test_archive_is_blocked_for_another_tenant(self, client, db, two_tenants):
        from promptops_app.database import CourseDesignDocument

        cdd = _cdd(db, project_id=two_tenants["a"].id)
        resp = client.delete(f"/api/v1/cdd/{cdd.id}", headers=two_tenants["headers_b"])
        assert resp.status_code == 404
        assert db.query(CourseDesignDocument).filter_by(id=cdd.id).first().deleted_at is None

    def test_archive_and_restore_work_for_own_tenant(self, client, db, two_tenants):
        cdd = _cdd(db, project_id=two_tenants["a"].id)
        resp = client.delete(f"/api/v1/cdd/{cdd.id}", headers=two_tenants["headers_a"])
        assert resp.status_code == 200, resp.text
        resp = client.post(f"/api/v1/cdd/{cdd.id}/restore", headers=two_tenants["headers_a"])
        assert resp.status_code == 200, resp.text

    def test_restore_is_blocked_for_another_tenant(self, client, db, two_tenants):
        cdd = _cdd(db, project_id=two_tenants["a"].id)
        client.delete(f"/api/v1/cdd/{cdd.id}", headers=two_tenants["headers_a"])
        resp = client.post(f"/api/v1/cdd/{cdd.id}/restore", headers=two_tenants["headers_b"])
        assert resp.status_code == 404

    def test_shared_cdd_is_readable_by_every_tenant(self, client, db, two_tenants):
        """A NULL project_id CDD (legacy/shared) stays visible — the fix must
        not turn every un-migrated legacy row into a 404 for everyone."""
        cdd = _cdd(db, project_id=None, title="Legacy Shared CDD")
        for hdrs in (two_tenants["headers_a"], two_tenants["headers_b"]):
            assert client.get(f"/api/v1/cdd/{cdd.id}", headers=hdrs).status_code == 200


class TestListCddsTenantIsolation:
    def test_tenant_b_cannot_see_tenant_a_cdd(self, client, db, two_tenants):
        cdd = _cdd(db, project_id=two_tenants["a"].id, title="A-only CDD")
        resp = client.get("/api/v1/cdd", headers=two_tenants["headers_b"])
        assert resp.status_code == 200
        ids = [c["id"] for c in resp.json()["items"]]
        assert cdd.id not in ids

    def test_tenant_a_sees_its_own_cdd(self, client, db, two_tenants):
        cdd = _cdd(db, project_id=two_tenants["a"].id, title="A-own-visible CDD")
        resp = client.get("/api/v1/cdd", headers=two_tenants["headers_a"])
        assert resp.status_code == 200
        ids = [c["id"] for c in resp.json()["items"]]
        assert cdd.id in ids

    def test_tenant_admin_cannot_use_project_id_param_to_see_another_tenant(self, client, db, two_tenants):
        """The exact hole: a tenant's own admin (role == 'admin', NOT a
        platform admin) passing ?project_id=<another tenant> used to be
        honored verbatim instead of being scoped to their own tenant."""
        cdd = _cdd(db, project_id=two_tenants["a"].id, title="A CDD via spoofed param")
        resp = client.get(
            "/api/v1/cdd", params={"project_id": two_tenants["a"].id}, headers=two_tenants["headers_b"],
        )
        assert resp.status_code == 200
        ids = [c["id"] for c in resp.json()["items"]]
        assert cdd.id not in ids

    def test_tenant_admin_omitting_all_filters_does_not_see_every_tenant(self, client, db, two_tenants):
        """The other half of the same hole: omitting project_id AND course_id
        used to fall through to list_all_cdds — every tenant, unfiltered —
        for any tenant-level admin, not just a platform admin."""
        cdd_a = _cdd(db, project_id=two_tenants["a"].id, title="A CDD no filters")
        cdd_b = _cdd(db, project_id=two_tenants["b"].id, title="B CDD no filters")
        resp = client.get("/api/v1/cdd", headers=two_tenants["headers_b"])
        assert resp.status_code == 200
        ids = [c["id"] for c in resp.json()["items"]]
        assert cdd_b.id in ids
        assert cdd_a.id not in ids

    def test_platform_admin_with_no_project_id_sees_every_tenant(self, client, db, two_tenants):
        cdd_a = _cdd(db, project_id=two_tenants["a"].id, title="A CDD platform view")
        cdd_b = _cdd(db, project_id=two_tenants["b"].id, title="B CDD platform view")
        resp = client.get("/api/v1/cdd", headers=two_tenants["headers_platform"])
        assert resp.status_code == 200
        ids = [c["id"] for c in resp.json()["items"]]
        assert cdd_a.id in ids
        assert cdd_b.id in ids

    def test_platform_admin_viewing_as_tenant_a_sees_only_tenant_a(self, client, db, two_tenants):
        cdd_a = _cdd(db, project_id=two_tenants["a"].id, title="A CDD viewed-as")
        cdd_b = _cdd(db, project_id=two_tenants["b"].id, title="B CDD viewed-as")
        resp = client.get(
            "/api/v1/cdd", params={"project_id": two_tenants["a"].id}, headers=two_tenants["headers_platform"],
        )
        assert resp.status_code == 200
        ids = [c["id"] for c in resp.json()["items"]]
        assert cdd_a.id in ids
        assert cdd_b.id not in ids
