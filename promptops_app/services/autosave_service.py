"""Autosave service — draft persistence for the Editor page.

Draft lifecycle
---------------
1.  User edits content in the text_area.
2.  Autosave fires when content has been stable for ≥ AUTOSAVE_INTERVAL_SECONDS
    and the block is not in an approved / published state.
3.  Draft is stored in block.draft_content — never touches block.content.
4.  On manual "Save Edit": draft is cleared, block.content is updated normally.
5.  On page/session reload: if draft_content differs from content, recovery is offered.

Constraints
-----------
* Autosave is skipped for approved and published blocks.
* Autosave does NOT trigger quality checks (plagiarism, eval, AI review).
* Manual save always writes an audit event; autosave never does.
* No Streamlit imports — UI state is managed entirely in the calling page.
"""

from __future__ import annotations

from datetime import datetime, timezone

from promptops_app.database import Block

AUTOSAVE_INTERVAL_SECONDS: int = 10

# Workflow states where autosave is suppressed (content considered locked).
_LOCKED_STATES: frozenset[str] = frozenset({"approved", "published"})


# ── Draft persistence ─────────────────────────────────────────────────────────

def save_draft(db, block: Block, content: str, saved_by: str) -> None:
    """Persist the working draft without touching block.content."""
    block.draft_content  = content
    block.draft_saved_at = datetime.now(timezone.utc)
    block.draft_saved_by = saved_by
    db.commit()


def clear_draft(db, block: Block) -> None:
    """Remove draft after a successful manual save or deliberate dismissal."""
    block.draft_content  = None
    block.draft_saved_at = None
    block.draft_saved_by = None
    db.commit()


# ── Draft queries ─────────────────────────────────────────────────────────────

def has_recoverable_draft(block: Block) -> bool:
    """Return True if block carries a draft that differs from its saved content."""
    if not block.draft_content:
        return False
    return block.draft_content.strip() != (block.content or "").strip()


def is_locked(block: Block) -> bool:
    """Return True if the block state prevents autosave."""
    return block.workflow_state.lower() in _LOCKED_STATES


# ── UI helpers (return plain strings — no st.* calls) ────────────────────────

def draft_age_label(block: Block) -> str:
    """Human-readable age of the stored draft (e.g. '3m ago')."""
    saved = block.draft_saved_at
    if not saved:
        return "previously"
    if saved.tzinfo is None:
        saved = saved.replace(tzinfo=timezone.utc)
    elapsed = (datetime.now(timezone.utc) - saved).total_seconds()
    if elapsed < 60:
        return f"{int(elapsed)}s ago"
    if elapsed < 3600:
        return f"{int(elapsed // 60)}m ago"
    if elapsed < 86400:
        return f"{int(elapsed // 3600)}h ago"
    return f"{int(elapsed // 86400)}d ago"


def status_html(
    *,
    dirty: bool,
    saved_at: datetime | None,
    locked: bool,
) -> str:
    """Return the save-status indicator as an HTML string for st.markdown().

    Returns an empty string when there is nothing to show.
    """
    if locked:
        return (
            "<div style='font-size:0.72rem;color:#b45309;padding:3px 0;margin-top:4px;'>"
            "⚠️ Autosave paused — content is Approved/Published. "
            "Reset to Draft before editing."
            "</div>"
        )
    if dirty:
        return (
            "<div style='font-size:0.72rem;color:#d97706;padding:3px 0;margin-top:4px;'>"
            "⏳ Unsaved changes…"
            "</div>"
        )
    if saved_at is not None:
        if saved_at.tzinfo is None:
            saved_at = saved_at.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - saved_at).total_seconds()
        if elapsed < 15:
            lbl = "Autosaved just now"
        elif elapsed < 60:
            lbl = f"Autosaved {int(elapsed)}s ago"
        elif elapsed < 3600:
            lbl = f"Autosaved {int(elapsed // 60)}m ago"
        else:
            lbl = f"Autosaved {int(elapsed // 3600)}h ago"
        return (
            f"<div style='font-size:0.72rem;color:#10b981;padding:3px 0;margin-top:4px;'>"
            f"✅ {lbl}"
            f"</div>"
        )
    return ""
