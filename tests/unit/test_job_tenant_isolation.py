"""CAS-146: content generated in one tenant showed up under "Generate Another" /
Latest Results in another tenant.

Root cause: GET /jobs/{id} (status poll), DELETE /jobs/{id} (cancel), and
GET /jobs/{id}/progress fetched a GenerationJob by id alone -- no ownership
check at all. Any authenticated user, in any tenant, could poll or cancel any
other tenant's job by id (job ids are opaque hex strings, not secrets) and
read back its full result, including generated blocks. list_jobs and
get_active_job were already correctly scoped by username; only the three
per-job endpoints were missing the check.

Same-tenant users must still be able to see each other's jobs (e.g. a lead
checking an ID's build), so this is scoped by project/tenant, not by the
exact user who launched it.
"""
from __future__ import annotations

from promptops_app.repositories import job_repository


def _make_job(db, *, project_id, course_id=100, user_name="someone"):
    job_id = job_repository.create_job(
        db, user_name=user_name, request_params={}, project_id=project_id, course_id=course_id,
    )
    return job_id


class TestStatusPollIsTenantScoped:
    def test_a_different_tenant_gets_404_not_someone_elses_job(self, client, two_tenants, db):
        job_id = _make_job(db, project_id=two_tenants["a"].id)

        resp = client.get(f"/api/v1/jobs/{job_id}", headers=two_tenants["headers_b"])

        assert resp.status_code == 404

    def test_the_owning_tenant_can_still_poll_its_own_job(self, client, two_tenants, db):
        job_id = _make_job(db, project_id=two_tenants["a"].id)

        resp = client.get(f"/api/v1/jobs/{job_id}", headers=two_tenants["headers_a"])

        assert resp.status_code == 200
        assert resp.json()["job_id"] == job_id

    def test_a_platform_admin_can_still_poll_any_tenants_job(self, client, two_tenants, db):
        job_id = _make_job(db, project_id=two_tenants["a"].id)

        resp = client.get(f"/api/v1/jobs/{job_id}", headers=two_tenants["headers_platform"])

        assert resp.status_code == 200

    def test_a_job_with_no_project_id_is_not_visible_to_a_tenant_user(self, client, two_tenants, db):
        """A job somehow created without a project_id (e.g. a legacy row)
        must fail closed -- never treated as belonging to every tenant."""
        job_id = _make_job(db, project_id=None)

        resp = client.get(f"/api/v1/jobs/{job_id}", headers=two_tenants["headers_a"])

        assert resp.status_code == 404


class TestCancelIsTenantScoped:
    def test_a_different_tenant_cannot_cancel_someone_elses_job(self, client, two_tenants, db):
        job_id = _make_job(db, project_id=two_tenants["a"].id)

        resp = client.delete(f"/api/v1/jobs/{job_id}", headers=two_tenants["headers_b"])

        assert resp.status_code == 404
        from promptops_app.repositories import job_repository as jr
        assert jr.get_job(db, job_id).status == "queued"  # untouched

    def test_the_owning_tenant_can_still_cancel_its_own_job(self, client, two_tenants, db):
        job_id = _make_job(db, project_id=two_tenants["a"].id)

        resp = client.delete(f"/api/v1/jobs/{job_id}", headers=two_tenants["headers_a"])

        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"


class TestBlockProgressIsTenantScoped:
    def test_a_different_tenant_gets_404_on_day_progress_too(self, client, two_tenants, db):
        job_id = _make_job(db, project_id=two_tenants["a"].id)

        resp = client.get(f"/api/v1/jobs/{job_id}/progress", headers=two_tenants["headers_b"])

        assert resp.status_code == 404
