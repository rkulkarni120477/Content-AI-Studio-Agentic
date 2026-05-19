"""
Unit tests for the workflow state machine.

These tests verify every valid and invalid transition without touching
the database.  The Block ORM object is constructed in memory and the
``db`` argument is a MagicMock.

Why unit test services directly?
---------------------------------
The service layer contains the real business rules (state guards,
required fields, audit writes).  Testing through the HTTP layer would
mix HTTP concerns into these assertions and make failures harder to trace.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from promptops_app.services.workflow_service import (
    approve_block,
    archive_block,
    bulk_approve,
    publish_block,
    reject_block,
    request_changes,
    submit_for_review,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_block(state: str, **kwargs) -> MagicMock:
    """
    Build a minimal Block mock with the given workflow state.

    The mock's generation attribute is set to None so SLA helpers
    don't fail when they try to read project_id / course_id.
    """
    block = MagicMock()
    block.id = 1
    block.block_label = "Test Block"
    block.workflow_state = state
    block.review_requested_at = None
    block.generation = None

    for key, value in kwargs.items():
        setattr(block, key, value)

    return block


def _make_db() -> MagicMock:
    """
    Return a database session mock.

    The workflow service calls db.add(), db.commit(), and sometimes
    db.query().  We mock those to keep tests in-process.
    """
    db = MagicMock()
    db.add = MagicMock()
    db.commit = MagicMock()
    return db


# ---------------------------------------------------------------------------
# submit_for_review
# ---------------------------------------------------------------------------

class TestSubmitForReview:
    def test_draft_transitions_to_in_review(self):
        """Happy path: draft → in_review."""
        block = _make_block("draft")
        db = _make_db()

        ok, reason = submit_for_review(db, block, reviewer_username="jane", actor="shubham")

        assert ok is True
        assert reason is None
        assert block.workflow_state == "in_review"
        assert block.assigned_reviewer == "jane"
        assert block.submitted_by == "shubham"

    def test_changes_requested_can_be_resubmitted(self):
        """changes_requested → in_review (resubmission after feedback)."""
        block = _make_block("changes_requested")
        db = _make_db()

        ok, _ = submit_for_review(db, block, reviewer_username="jane", actor="shubham")

        assert ok is True
        assert block.workflow_state == "in_review"

    def test_rejected_can_be_resubmitted(self):
        """rejected → in_review after author makes edits."""
        block = _make_block("rejected")
        db = _make_db()

        ok, _ = submit_for_review(db, block, reviewer_username="jane", actor="shubham")

        assert ok is True
        assert block.workflow_state == "in_review"

    def test_approved_cannot_be_submitted(self):
        """Approved blocks must not be re-submitted — they are locked."""
        block = _make_block("approved")
        db = _make_db()

        ok, reason = submit_for_review(db, block, reviewer_username="jane", actor="shubham")

        assert ok is False
        assert reason is not None
        assert block.workflow_state == "approved"  # state unchanged

    def test_published_cannot_be_submitted(self):
        block = _make_block("published")
        db = _make_db()
        ok, _ = submit_for_review(db, block, reviewer_username="jane", actor="shubham")
        assert ok is False


# ---------------------------------------------------------------------------
# approve_block
# ---------------------------------------------------------------------------

class TestApproveBlock:
    def test_in_review_transitions_to_approved(self):
        """Happy path: in_review → approved."""
        block = _make_block("in_review")
        db = _make_db()

        ok, reason = approve_block(db, block, actor="jane", comment="Looks great.")

        assert ok is True
        assert reason is None
        assert block.workflow_state == "approved"
        assert block.approved_by == "jane"
        assert block.review_comments == "Looks great."

    def test_draft_cannot_be_approved(self):
        """Draft blocks must go through in_review first."""
        block = _make_block("draft")
        db = _make_db()

        ok, reason = approve_block(db, block, actor="jane")

        assert ok is False
        assert "In Review" in reason
        assert block.workflow_state == "draft"


# ---------------------------------------------------------------------------
# request_changes
# ---------------------------------------------------------------------------

class TestRequestChanges:
    def test_in_review_transitions_to_changes_requested(self):
        block = _make_block("in_review")
        db = _make_db()

        ok, _ = request_changes(db, block, actor="jane", reason="Add more examples.")

        assert ok is True
        assert block.workflow_state == "changes_requested"
        assert block.review_comments == "Add more examples."

    def test_reason_is_required(self):
        """request_changes must fail if no reason is given."""
        block = _make_block("in_review")
        db = _make_db()

        ok, reason = request_changes(db, block, actor="jane", reason="")

        assert ok is False
        assert "reason" in reason.lower()
        assert block.workflow_state == "in_review"

    def test_draft_cannot_request_changes(self):
        block = _make_block("draft")
        db = _make_db()
        ok, _ = request_changes(db, block, actor="jane", reason="Fix it.")
        assert ok is False


# ---------------------------------------------------------------------------
# reject_block
# ---------------------------------------------------------------------------

class TestRejectBlock:
    def test_in_review_transitions_to_rejected(self):
        block = _make_block("in_review")
        db = _make_db()

        ok, _ = reject_block(db, block, actor="jane", reason="Does not meet standards.")

        assert ok is True
        assert block.workflow_state == "rejected"
        assert block.rejected_reason == "Does not meet standards."

    def test_reason_is_required(self):
        block = _make_block("in_review")
        db = _make_db()

        ok, reason = reject_block(db, block, actor="jane", reason="")

        assert ok is False
        assert "reason" in reason.lower()


# ---------------------------------------------------------------------------
# publish_block
# ---------------------------------------------------------------------------

class TestPublishBlock:
    def test_approved_transitions_to_published(self):
        block = _make_block("approved")
        db = _make_db()

        ok, _ = publish_block(db, block, actor="shubham")

        assert ok is True
        assert block.workflow_state == "published"

    def test_draft_cannot_be_published(self):
        block = _make_block("draft")
        db = _make_db()
        ok, _ = publish_block(db, block, actor="shubham")
        assert ok is False


# ---------------------------------------------------------------------------
# archive_block
# ---------------------------------------------------------------------------

class TestArchiveBlock:
    def test_approved_can_be_archived(self):
        block = _make_block("approved")
        db = _make_db()

        ok, _ = archive_block(db, block, actor="admin")

        assert ok is True
        assert block.workflow_state == "archived"

    def test_published_can_be_archived(self):
        block = _make_block("published")
        db = _make_db()

        ok, _ = archive_block(db, block, actor="admin")

        assert ok is True
        assert block.workflow_state == "archived"

    def test_draft_cannot_be_archived(self):
        block = _make_block("draft")
        db = _make_db()
        ok, _ = archive_block(db, block, actor="admin")
        assert ok is False

    def test_in_review_cannot_be_archived(self):
        block = _make_block("in_review")
        db = _make_db()
        ok, _ = archive_block(db, block, actor="admin")
        assert ok is False


# ---------------------------------------------------------------------------
# bulk_approve
# ---------------------------------------------------------------------------

class TestBulkApprove:
    def test_approves_only_in_review_blocks(self):
        """
        bulk_approve should approve in_review blocks and skip all others.
        """
        db = _make_db()

        # Mock db.query().filter().first() to return a block based on ID.
        in_review_block = _make_block("in_review")
        in_review_block.id = 1
        in_review_block.generation = None

        draft_block = _make_block("draft")
        draft_block.id = 2
        draft_block.generation = None

        block_map = {1: in_review_block, 2: draft_block}

        def _query_side_effect(model):
            mock_query = MagicMock()
            mock_query.filter.return_value.first.side_effect = (
                lambda: block_map.get(mock_query.filter.call_args[0][0].right.value)
            )
            return mock_query

        # Simpler: patch the query to return blocks by position in block_ids list.
        call_count = [0]
        block_list = [in_review_block, draft_block]

        def _mock_query(model):
            q = MagicMock()
            idx = call_count[0]
            call_count[0] += 1
            q.filter.return_value.first.return_value = block_list[idx] if idx < len(block_list) else None
            return q

        db.query.side_effect = _mock_query

        results = bulk_approve(db, block_ids=[1, 2], actor="admin")

        # Block 1 (in_review) should be approved; block 2 (draft) should be skipped.
        assert 1 in results["approved"]
        assert 2 in results["skipped"]
        assert results["errors"] == []
