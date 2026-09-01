"""Bulk Workflow Status Update ticket — POST /workflow/bulk-submit (Draft →
In Review) and POST /workflow/bulk-publish (Approved → Published).

Also covers the tenant-isolation gap found while building these: every
bulk_* service function (including the pre-existing bulk_approve) fetched
blocks by a raw, unfiltered id — the ids in a bulk request come straight
from the client, not from a same-tenant-filtered list the server already
scoped, so nothing upstream can be trusted to have kept them within one
tenant. Fixed via workflow_service._tenant_scoped_block, mirroring
workflow._get_block_or_404's own tenant check.
"""

from __future__ import annotations

import pytest


def _block(db, *, project_id, state="draft", created_by="someone"):
    from promptops_app.database import Block, Generation

    gen = Generation(
        topic="Test Lesson", block_type="lesson", output_text="Generated lesson content.",
        prompt_name="test_prompt", prompt_version="v1", created_by=created_by,
        project_id=project_id,
    )
    db.add(gen)
    db.commit()
    db.refresh(gen)

    block = Block(
        generation_id=gen.id, block_label="Introduction",
        content="This is test content.", workflow_state=state, position=0,
    )
    db.add(block)
    db.commit()
    db.refresh(block)
    return block


class TestBulkSubmit:
    def test_moves_all_eligible_drafts_to_in_review(self, client, auth_headers, db):
        blocks = [_block(db, project_id=None, state="draft") for _ in range(3)]
        resp = client.post(
            "/api/v1/workflow/bulk-submit",
            json={"block_ids": [b.id for b in blocks], "reviewer_username": "test_admin"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["succeeded"] == 3
        assert body["failed"] == 0
        for b in blocks:
            db.refresh(b)
            assert b.workflow_state == "in_review"
            assert b.assigned_reviewer == "test_admin"

    def test_reports_per_item_failure_reason_without_blocking_the_rest(self, client, auth_headers, db):
        ok_block = _block(db, project_id=None, state="draft")
        bad_block = _block(db, project_id=None, state="published")
        resp = client.post(
            "/api/v1/workflow/bulk-submit",
            json={"block_ids": [ok_block.id, bad_block.id], "reviewer_username": "test_admin"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["succeeded"] == 1
        assert body["failed"] == 1
        results = {r["block_id"]: r for r in body["results"]}
        assert results[ok_block.id]["ok"] is True
        assert results[ok_block.id]["reason"] is None
        assert results[bad_block.id]["ok"] is False
        assert "Draft" in results[bad_block.id]["reason"] or "draft" in results[bad_block.id]["reason"].lower()

    def test_reports_missing_block_as_not_found(self, client, auth_headers, db):
        ok_block = _block(db, project_id=None, state="draft")
        resp = client.post(
            "/api/v1/workflow/bulk-submit",
            json={"block_ids": [ok_block.id, 9999999], "reviewer_username": "test_admin"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["succeeded"] == 1
        assert body["failed"] == 1
        results = {r["block_id"]: r for r in body["results"]}
        assert results[9999999]["ok"] is False
        assert "not found" in results[9999999]["reason"].lower()

    def test_author_lacks_bulk_submit_permission(self, client, author_headers, db):
        block = _block(db, project_id=None, state="draft")
        resp = client.post(
            "/api/v1/workflow/bulk-submit",
            json={"block_ids": [block.id], "reviewer_username": "test_admin"},
            headers=author_headers,
        )
        assert resp.status_code == 403

    def test_reviewer_username_is_required(self, client, auth_headers, db):
        block = _block(db, project_id=None, state="draft")
        resp = client.post(
            "/api/v1/workflow/bulk-submit",
            json={"block_ids": [block.id]},
            headers=auth_headers,
        )
        assert resp.status_code == 422


class TestBulkPublish:
    def test_moves_all_eligible_approved_to_published(self, client, auth_headers, db):
        blocks = [_block(db, project_id=None, state="approved") for _ in range(2)]
        resp = client.post(
            "/api/v1/workflow/bulk-publish",
            json={"block_ids": [b.id for b in blocks]},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["succeeded"] == 2
        assert body["failed"] == 0
        for b in blocks:
            db.refresh(b)
            assert b.workflow_state == "published"

    def test_reports_failure_for_non_approved_block(self, client, auth_headers, db):
        ok_block = _block(db, project_id=None, state="approved")
        bad_block = _block(db, project_id=None, state="draft")
        resp = client.post(
            "/api/v1/workflow/bulk-publish",
            json={"block_ids": [ok_block.id, bad_block.id]},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["succeeded"] == 1
        assert body["failed"] == 1
        results = {r["block_id"]: r for r in body["results"]}
        assert results[bad_block.id]["ok"] is False
        db.refresh(bad_block)
        assert bad_block.workflow_state == "draft"

    def test_author_lacks_bulk_publish_permission(self, client, author_headers, db):
        block = _block(db, project_id=None, state="approved")
        resp = client.post(
            "/api/v1/workflow/bulk-publish",
            json={"block_ids": [block.id]},
            headers=author_headers,
        )
        assert resp.status_code == 403


class TestBulkTransitionsTenantIsolation:
    """Bulk request ids come from the client, not a pre-scoped list — every
    bulk_* function must refuse to act on another tenant's block."""

    def test_bulk_submit_cannot_touch_another_tenants_draft(self, client, db, two_tenants):
        block_a = _block(db, project_id=two_tenants["a"].id, state="draft")
        resp = client.post(
            "/api/v1/workflow/bulk-submit",
            json={"block_ids": [block_a.id], "reviewer_username": "someone"},
            headers=two_tenants["headers_b"],
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["succeeded"] == 0
        assert body["failed"] == 1
        assert "not found" in body["results"][0]["reason"].lower()
        db.refresh(block_a)
        assert block_a.workflow_state == "draft"

    def test_bulk_submit_works_for_own_tenant(self, client, db, two_tenants):
        block_a = _block(db, project_id=two_tenants["a"].id, state="draft")
        resp = client.post(
            "/api/v1/workflow/bulk-submit",
            json={"block_ids": [block_a.id], "reviewer_username": "someone"},
            headers=two_tenants["headers_a"],
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["succeeded"] == 1

    def test_bulk_publish_cannot_touch_another_tenants_approved_block(self, client, db, two_tenants):
        block_a = _block(db, project_id=two_tenants["a"].id, state="approved")
        resp = client.post(
            "/api/v1/workflow/bulk-publish",
            json={"block_ids": [block_a.id]},
            headers=two_tenants["headers_b"],
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["succeeded"] == 0
        db.refresh(block_a)
        assert block_a.workflow_state == "approved"

    def test_platform_admin_bulk_publish_reaches_every_tenant(self, client, db, two_tenants):
        block_a = _block(db, project_id=two_tenants["a"].id, state="approved")
        resp = client.post(
            "/api/v1/workflow/bulk-publish",
            json={"block_ids": [block_a.id]},
            headers=two_tenants["headers_platform"],
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["succeeded"] == 1

    def test_bulk_approve_cannot_touch_another_tenants_block(self, client, db, two_tenants):
        """Same fix applied to the pre-existing bulk-approve endpoint."""
        block_a = _block(db, project_id=two_tenants["a"].id, state="in_review")
        resp = client.post(
            "/api/v1/workflow/bulk-approve",
            json={"block_ids": [block_a.id]},
            headers=two_tenants["headers_platform"],
        )
        # Platform admin still reaches it (sanity: the fix didn't overshoot).
        assert resp.status_code == 200, resp.text
        assert resp.json()["total_approved"] == 1

        block_b = _block(db, project_id=two_tenants["b"].id, state="in_review")
        resp2 = client.post(
            "/api/v1/workflow/bulk-approve",
            json={"block_ids": [block_b.id]},
            headers=two_tenants["headers_a"],
        )
        assert resp2.status_code == 200, resp2.text
        assert resp2.json()["total_approved"] == 0
        assert block_b.id in resp2.json()["errors"]
        db.refresh(block_b)
        assert block_b.workflow_state == "in_review"
