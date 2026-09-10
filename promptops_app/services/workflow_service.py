"""Approval workflow service.

All state transitions go through these functions — never call
apply_transition_local() or mutate block.workflow_state directly from pages.

Responsibility split
--------------------
* Service layer  — business rules, field mutations, audit events
* RBAC layer     — enforced by the caller (workflow.py) before calling here
* UI layer       — never touched here; no Streamlit imports

State machine
-------------
draft  →  in_review  →  approved      →  published  →  archived
                     →  changes_requested → in_review (resubmit)
                                       → draft (reset)
             ↘  rejected  →  draft
"""

from datetime import datetime, timedelta, timezone

from promptops_app.database import Block, WorkflowEvent, log_event
from promptops_app.services.audit_service import log_audit_event


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _record_event(db, block, from_state, to_state, action, actor, comment="") -> None:
    db.add(WorkflowEvent(
        block_id   = block.id,
        from_state = from_state,
        to_state   = to_state,
        action     = action,
        actor      = actor,
        comment    = comment or "",
    ))


def _audit(db, actor, action, block, extra: dict = None) -> None:
    """Write to audit_logs — fail-safe, never crashes the caller."""
    log_audit_event(
        db, actor, f"workflow.{action}",
        entity_type = "block",
        entity_id   = block.id,
        project_id  = getattr(block.generation, "project_id", None)
                      if block.generation else None,
        course_id   = getattr(block.generation, "course_id", None)
                      if block.generation else None,
        metadata    = {"block_label": block.block_label, **(extra or {})},
    )


# ── Core transitions ──────────────────────────────────────────────────────────

def submit_for_review(
    db,
    block: Block,
    reviewer_username: str,
    actor: str,
) -> tuple[bool, str | None]:
    """Draft | Changes Requested → in_review.  Assigns reviewer and starts SLA clock."""
    allowed_from = {"draft", "rejected", "changes_requested"}
    cur = block.workflow_state.lower()
    if cur not in allowed_from:
        return False, (
            f"Cannot submit from '{block.workflow_state}'. "
            f"Block must be in Draft, Rejected, or Changes Requested."
        )

    prev = block.workflow_state
    block.workflow_state       = "in_review"
    block.assigned_reviewer    = reviewer_username
    block.submitted_by         = actor
    block.review_requested_at  = _now()
    block.updated_at           = _now()

    _record_event(db, block, prev, "in_review", "submit_for_review", actor)
    db.commit()

    log_event(db, "submit_for_review", actor,
              f"Block #{block.id} submitted → '{reviewer_username}'",
              {"block_id": block.id, "reviewer": reviewer_username})
    _audit(db, actor, "submitted", block, {"reviewer": reviewer_username})
    return True, None


def approve_block(
    db,
    block: Block,
    actor: str,
    comment: str = "",
) -> tuple[bool, str | None]:
    """in_review → approved.  Records approver, timestamp, and optional comment."""
    if block.workflow_state.lower() != "in_review":
        return False, f"Block must be 'In Review' to approve. Current: '{block.workflow_state}'"

    prev = block.workflow_state
    now  = _now()
    block.workflow_state  = "approved"
    block.approved_by     = actor
    block.approved_at     = now
    block.reviewed_by     = actor
    block.reviewed_at     = now
    block.review_comments = comment or ""
    if comment:
        block.reviewer_comment = comment
    block.updated_at = now

    _record_event(db, block, prev, "approved", "approve", actor, comment)
    db.commit()

    log_event(db, "block_approved", actor,
              f"Block #{block.id} approved",
              {"block_id": block.id, "comment": (comment or "")[:200]})
    _audit(db, actor, "approved", block, {"comment": (comment or "")[:200]})
    return True, None


def request_changes(
    db,
    block: Block,
    actor: str,
    reason: str,
) -> tuple[bool, str | None]:
    """in_review → changes_requested.

    Softer than reject — the author can resubmit after making edits.
    A reason is mandatory so the author knows what to fix.
    """
    if block.workflow_state.lower() != "in_review":
        return False, f"Block must be 'In Review' to request changes. Current: '{block.workflow_state}'"
    if not reason or not reason.strip():
        return False, "A reason is required when requesting changes."

    prev = block.workflow_state
    now  = _now()
    block.workflow_state  = "changes_requested"
    block.reviewed_by     = actor
    block.reviewed_at     = now
    block.review_comments = reason
    block.rejected_reason = reason  # backward compat — show in any UI reading rejected_reason
    block.updated_at      = now

    _record_event(db, block, prev, "changes_requested", "request_changes", actor, reason)
    db.commit()

    log_event(db, "block_changes_requested", actor,
              f"Block #{block.id} — changes requested: {reason[:100]}",
              {"block_id": block.id, "reason": reason})
    _audit(db, actor, "request_changes", block, {"reason": reason[:300]})
    return True, None


def reject_block(
    db,
    block: Block,
    actor: str,
    reason: str,
) -> tuple[bool, str | None]:
    """in_review → rejected (hard decline).  A reason is mandatory."""
    if block.workflow_state.lower() not in ("in_review",):
        return False, f"Cannot reject from '{block.workflow_state}'."
    if not reason or not reason.strip():
        return False, "A rejection reason is required."

    prev = block.workflow_state
    now  = _now()
    block.workflow_state  = "rejected"
    block.rejected_reason = reason
    block.reviewed_by     = actor
    block.reviewed_at     = now
    block.review_comments = reason
    block.updated_at      = now

    _record_event(db, block, prev, "rejected", "reject", actor, reason)
    db.commit()

    log_event(db, "block_rejected", actor,
              f"Block #{block.id} rejected: {reason[:100]}",
              {"block_id": block.id, "reason": reason})
    _audit(db, actor, "rejected", block, {"reason": reason[:300]})
    return True, None


def publish_block(
    db,
    block: Block,
    actor: str,
) -> tuple[bool, str | None]:
    """approved → published."""
    if block.workflow_state.lower() != "approved":
        return False, f"Block must be 'Approved' to publish. Current: '{block.workflow_state}'"

    prev = block.workflow_state
    block.workflow_state = "published"
    block.updated_at     = _now()

    _record_event(db, block, prev, "published", "publish", actor)
    db.commit()

    log_event(db, "block_published", actor,
              f"Block #{block.id} published", {"block_id": block.id})
    _audit(db, actor, "published", block)

    # Best-effort: generate the Canvas-ready HTML rendition used by the IMSCC
    # exporter. A failure here must never undo a successful publish.
    _generate_canvas_html(db, block, actor)
    return True, None


def _generate_canvas_html(db, block: Block, actor: str) -> None:
    """Generate and persist ``block.content_html`` from the block's markdown.

    Runs after publish and swallows all errors — publishing must succeed even if
    the LLM rendition cannot be produced.
    """
    try:
        from promptops_app.services.canvas_html_service import generate_canvas_html
        from promptops_app.services.usage_service import UsageLogContext

        gen = block.generation
        usage_ctx = UsageLogContext(
            user_name=actor,
            project_id=getattr(gen, "project_id", None) if gen else None,
            course_id=getattr(gen, "course_id", None) if gen else None,
            entity_type="canvas_html",
            entity_id=str(block.id),
        )
        html = generate_canvas_html(
            label=block.block_label or "",
            content=block.content or "",
            block_type=block.block_type or "",
            usage_ctx=usage_ctx,
        )
        if html:
            block.content_html = html
            block.content_html_at = _now()
            db.commit()
    except Exception:  # pragma: no cover - defensive
        db.rollback()


def archive_block(
    db,
    block: Block,
    actor: str,
) -> tuple[bool, str | None]:
    """approved | published → archived.  Admin-only soft deletion."""
    allowed_from = {"approved", "published"}
    if block.workflow_state.lower() not in allowed_from:
        return False, (
            f"Only Approved or Published blocks can be archived. "
            f"Current: '{block.workflow_state}'"
        )

    prev = block.workflow_state
    now  = _now()
    block.workflow_state = "archived"
    block.archived_by    = actor
    block.archived_at    = now
    block.updated_at     = now

    _record_event(db, block, prev, "archived", "archive", actor)
    db.commit()

    log_event(db, "block_archived", actor,
              f"Block #{block.id} archived", {"block_id": block.id})
    _audit(db, actor, "archive", block)
    return True, None


# ── Bulk operations ───────────────────────────────────────────────────────────

def _tenant_scoped_block(db, block_id: int, project_id, is_platform_admin: bool):
    """Fetch a block by id, or None if missing OR out of the caller's tenant.

    A bare ``db.query(Block).filter(Block.id == bid).first()`` (what every
    bulk_* function did before this) has no tenant boundary at all — the ids
    in a bulk request come straight from the request body, not from a
    same-tenant-filtered list the caller was shown, so nothing upstream can
    be trusted to have already scoped them. Mirrors
    app.api.v1.routers.workflow._get_block_or_404's tenant check exactly, so
    a cross-tenant id fails the same way here as it would through the
    single-block endpoint.
    """
    from app.core.tenant_context import visible_to_tenant

    blk = db.query(Block).filter(Block.id == block_id).first()
    if blk is None:
        return None
    row_project_id = getattr(blk.generation, "project_id", None) if blk.generation else None
    if not visible_to_tenant(row_project_id, project_id, is_platform_admin):
        return None
    return blk


def bulk_approve(db, block_ids: list, actor: str, *, project_id=None, is_platform_admin: bool = False) -> dict:
    """Approve multiple in_review blocks. Admin only."""
    results = {"approved": [], "skipped": [], "errors": []}
    for bid in block_ids:
        blk = _tenant_scoped_block(db, bid, project_id, is_platform_admin)
        if not blk:
            results["errors"].append(bid); continue
        ok, _ = approve_block(db, blk, actor, comment="Bulk approved")
        (results["approved"] if ok else results["skipped"]).append(bid)

    log_event(db, "bulk_approve", actor,
              f"Bulk-approved {len(results['approved'])} of {len(block_ids)} blocks",
              {"approved": results["approved"], "skipped": results["skipped"]})
    log_audit_event(
        db, actor, "workflow.bulk_approved",
        entity_type="block", entity_id=None,
        metadata={"approved": results["approved"], "skipped": results["skipped"], "errors": results["errors"]},
    )
    return results


def _bulk_transition(db, block_ids: list, actor: str, transition_fn, *,
                      project_id=None, is_platform_admin: bool = False) -> list[dict]:
    """Shared runner for bulk_submit_for_review/bulk_publish.

    Applies transition_fn(db, block, actor) to each id and collects a per-id
    (ok, reason) result — a partial failure has to be legible, not rounded to
    a bare count, per the bulk-workflow ticket's error-handling requirement.
    A missing OR cross-tenant id is reported as "not found" (no enumeration
    oracle), same contract as _get_block_or_404.
    """
    results = []
    for bid in block_ids:
        blk = _tenant_scoped_block(db, bid, project_id, is_platform_admin)
        if blk is None:
            results.append({"block_id": bid, "ok": False, "reason": "Block not found."})
            continue
        ok, reason = transition_fn(db, blk, actor)
        results.append({"block_id": bid, "ok": ok, "reason": None if ok else reason})
    return results


def bulk_submit_for_review(db, block_ids: list, reviewer_username: str, actor: str, *,
                           project_id=None, is_platform_admin: bool = False) -> list[dict]:
    """Draft | Rejected | Changes Requested → in_review, for many blocks at once.

    One reviewer is assigned to every block submitted — mirrors the
    single-block submit endpoint's own contract (see submit_for_review).
    """
    results = _bulk_transition(
        db, block_ids, actor,
        lambda db, blk, actor: submit_for_review(db, blk, reviewer_username, actor),
        project_id=project_id, is_platform_admin=is_platform_admin,
    )
    succeeded = sum(1 for r in results if r["ok"])

    log_event(db, "bulk_submit_for_review", actor,
              f"Bulk-submitted {succeeded} of {len(block_ids)} blocks → '{reviewer_username}'",
              {"results": results})
    log_audit_event(
        db, actor, "workflow.bulk_submitted",
        entity_type="block", entity_id=None,
        metadata={"reviewer": reviewer_username, "results": results},
    )
    return results


def bulk_publish(db, block_ids: list, actor: str, *,
                  project_id=None, is_platform_admin: bool = False) -> list[dict]:
    """approved → published, for many blocks at once."""
    results = _bulk_transition(
        db, block_ids, actor, publish_block,
        project_id=project_id, is_platform_admin=is_platform_admin,
    )
    succeeded = sum(1 for r in results if r["ok"])

    log_event(db, "bulk_publish", actor,
              f"Bulk-published {succeeded} of {len(block_ids)} blocks",
              {"results": results})
    log_audit_event(
        db, actor, "workflow.bulk_published",
        entity_type="block", entity_id=None,
        metadata={"results": results},
    )
    return results


# ── Queries ───────────────────────────────────────────────────────────────────

def get_pending_for_reviewer(db, reviewer_username: str) -> list:
    """Return blocks assigned to this reviewer still awaiting action."""
    return (
        db.query(Block)
        .filter(
            Block.assigned_reviewer == reviewer_username,
            Block.workflow_state.in_(["in_review", "changes_requested"]),
        )
        .order_by(Block.review_requested_at)
        .all()
    )


def get_sla_status(block: Block, sla_hours: int = 24) -> dict:
    """Return SLA metadata for a block currently in_review."""
    if not block.review_requested_at or block.workflow_state.lower() != "in_review":
        return {"overdue": False, "hours_remaining": None, "label": ""}

    requested = block.review_requested_at
    if requested.tzinfo is None:
        requested = requested.replace(tzinfo=timezone.utc)

    deadline   = requested + timedelta(hours=sla_hours)
    hours_left = (deadline - _now()).total_seconds() / 3600
    overdue    = hours_left < 0

    if overdue:
        label = f"⚠️ Overdue {abs(int(hours_left))}h"
    elif hours_left < 4:
        label = f"🔴 {int(hours_left)}h left"
    elif hours_left < 12:
        label = f"🟡 {int(hours_left)}h left"
    else:
        label = f"🟢 {int(hours_left)}h left"

    return {"overdue": overdue, "hours_remaining": hours_left, "label": label}
