"""
Integration tests for the workflow approval pipeline.

Tests the full submit → approve → publish flow via HTTP, verifying that:
  - State transitions work correctly end-to-end.
  - RBAC prevents authors from approving.
  - Invalid transitions return HTTP 409 with a meaningful error.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def test_block(db):
    """Create a test block in draft state for workflow tests."""
    from promptops_app.database import Block, Generation

    # Generation is required as the block's parent.
    gen = Generation(
        topic="Test Lesson",
        block_type="lesson",                      # NOT NULL on the model
        output_text="Generated lesson content.",  # NOT NULL on the model
        prompt_name="test_prompt",
        prompt_version="v1",
        created_by="test_admin",
    )
    db.add(gen)
    db.commit()
    db.refresh(gen)

    block = Block(
        generation_id=gen.id,
        block_label="Introduction",
        content="This is test content for the introduction block.",
        workflow_state="draft",
        position=0,
    )
    db.add(block)
    db.commit()
    db.refresh(block)
    return block


class TestSubmitForReview:
    def test_author_can_submit_draft_block(self, client, author_headers, test_block):
        """Authors can submit their own draft blocks for review."""
        response = client.post(
            f"/api/v1/workflow/blocks/{test_block.id}/submit",
            json={"reviewer_username": "test_admin"},
            headers=author_headers,
        )

        assert response.status_code == 200
        assert response.json()["workflow_state"] == "in_review"

    def test_submit_returns_409_for_approved_block(self, client, auth_headers, db, test_block):
        """A block already in 'approved' state cannot be submitted again."""
        # Force the block into approved state directly.
        test_block.workflow_state = "approved"
        db.commit()

        response = client.post(
            f"/api/v1/workflow/blocks/{test_block.id}/submit",
            json={"reviewer_username": "test_admin"},
            headers=auth_headers,
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "WORKFLOW_VIOLATION"


class TestApproveBlock:
    def test_admin_can_approve_in_review_block(self, client, auth_headers, db, test_block):
        """Admin can approve a block that is currently in_review."""
        test_block.workflow_state = "in_review"
        db.commit()

        response = client.post(
            f"/api/v1/workflow/blocks/{test_block.id}/approve",
            json={"comment": "Approved — well structured."},
            headers=auth_headers,
        )

        assert response.status_code == 200
        assert response.json()["workflow_state"] == "approved"

    def test_author_cannot_approve(self, client, author_headers, db, test_block):
        """Authors do not have the workflow.approve permission."""
        test_block.workflow_state = "in_review"
        db.commit()

        response = client.post(
            f"/api/v1/workflow/blocks/{test_block.id}/approve",
            json={"comment": ""},
            headers=author_headers,
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "PERMISSION_DENIED"

    def test_cannot_approve_draft_block(self, client, auth_headers, test_block):
        """Approving a draft block (not in_review) must return 409."""
        # test_block starts as 'draft'
        response = client.post(
            f"/api/v1/workflow/blocks/{test_block.id}/approve",
            json={"comment": ""},
            headers=auth_headers,
        )

        assert response.status_code == 409


class TestFullWorkflowCycle:
    def test_draft_to_published_full_cycle(self, client, auth_headers, author_headers, db, test_block):
        """
        Verify the complete happy path:
        draft → in_review → approved → published
        """
        # Step 1: Author submits for review.
        r1 = client.post(
            f"/api/v1/workflow/blocks/{test_block.id}/submit",
            json={"reviewer_username": "test_admin"},
            headers=author_headers,
        )
        assert r1.status_code == 200
        assert r1.json()["workflow_state"] == "in_review"

        # Step 2: Admin approves.
        r2 = client.post(
            f"/api/v1/workflow/blocks/{test_block.id}/approve",
            json={"comment": "LGTM"},
            headers=auth_headers,
        )
        assert r2.status_code == 200
        assert r2.json()["workflow_state"] == "approved"

        # Step 3: Admin publishes.
        r3 = client.post(
            f"/api/v1/workflow/blocks/{test_block.id}/publish",
            headers=auth_headers,
        )
        assert r3.status_code == 200
        assert r3.json()["workflow_state"] == "published"

    def test_request_changes_and_resubmit(self, client, auth_headers, author_headers, db, test_block):
        """
        Verify the changes_requested → resubmit path:
        draft → in_review → changes_requested → in_review
        """
        # Submit.
        client.post(
            f"/api/v1/workflow/blocks/{test_block.id}/submit",
            json={"reviewer_username": "test_admin"},
            headers=author_headers,
        )

        # Reviewer requests changes.
        r = client.post(
            f"/api/v1/workflow/blocks/{test_block.id}/request-changes",
            json={"reason": "Please add more clinical examples."},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["workflow_state"] == "changes_requested"

        # Author resubmits.
        r2 = client.post(
            f"/api/v1/workflow/blocks/{test_block.id}/submit",
            json={"reviewer_username": "test_admin"},
            headers=author_headers,
        )
        assert r2.status_code == 200
        assert r2.json()["workflow_state"] == "in_review"


class TestWorkflowSummary:
    def test_summary_returns_counts_per_state(self, client, auth_headers, db, test_block):
        """GET /workflow/summary returns integer counts for each workflow state."""
        response = client.get("/api/v1/workflow/summary", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        # All states must be present and be non-negative integers.
        for state in ("draft", "in_review", "approved", "published", "archived", "rejected"):
            assert state in data
            assert isinstance(data[state], int)
            assert data[state] >= 0
