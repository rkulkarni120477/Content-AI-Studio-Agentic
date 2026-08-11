"""Enterprise audit logging service.

Two logging targets
-------------------
1. audit_logs (AuditLog model) — structured, queryable enterprise audit trail.
   Every governance-relevant action is recorded here with full context:
   user, action, entity, project, course, timestamp, IP.

2. system_logs (SystemLog / log_event) — kept for backward compatibility.
   Existing call sites that use log_event() continue to work unchanged.

Public API
----------
log_audit_event(db, user_id, action, ...)
    The ONLY write path pages should use.  Wraps the repository in a
    fail-safe try/except so a broken DB connection never crashes the UI.

emit(db, event_type, actor, details, metadata)
    Legacy helper — delegates to log_event(); kept for backward compat.

get_audit_trail(db, ...)
    Queries AuditLog (NOT SystemLog) for the admin Audit Trail UI.

AUDIT_EVENTS
    Event taxonomy dict (action_key → metadata).  Import this for UI
    dropdowns / icons.
"""

from __future__ import annotations

import logging

from promptops_app.database import SystemLog, log_event
from promptops_app.repositories.audit_repository import (
    create_audit_log,
    list_audit_logs,
    count_audit_logs,
    list_distinct_actors,
    list_distinct_actions,
    list_distinct_entity_types,
)

_log = logging.getLogger(__name__)

# ── Event taxonomy ────────────────────────────────────────────────────────────
# action_key → {level, entity, icon, description}
# level: "info" | "warning" | "success" | "critical"

AUDIT_EVENTS: dict[str, dict] = {

    # ── Auth ─────────────────────────────────────────────────────────────────
    "user.login":              {"level": "info",    "entity": "auth",       "icon": "🔑", "label": "User logged in"},
    "user.login_failed":       {"level": "warning", "entity": "auth",       "icon": "🚫", "label": "Login failed"},
    "user.logout":             {"level": "info",    "entity": "auth",       "icon": "🚪", "label": "User logged out"},

    # ── User Management ───────────────────────────────────────────────────────
    "user.created":            {"level": "info",    "entity": "user",       "icon": "👤", "label": "User account created"},
    "user.role_changed":       {"level": "warning", "entity": "user",       "icon": "🔄", "label": "User role changed"},
    "user.deactivated":        {"level": "warning", "entity": "user",       "icon": "🔒", "label": "User account deactivated"},
    "user.reactivated":        {"level": "info",    "entity": "user",       "icon": "✅", "label": "User account reactivated"},
    "project.user_assigned":   {"level": "info",    "entity": "project",    "icon": "➕", "label": "User added to project"},
    "project.user_removed":    {"level": "info",    "entity": "project",    "icon": "➖", "label": "User removed from project"},

    # ── Style ────────────────────────────────────────────────────────────────
    "style.uploaded":          {"level": "info",    "entity": "style",      "icon": "🎨", "label": "Style uploaded"},
    "style.upgraded":          {"level": "info",    "entity": "style",      "icon": "🧠", "label": "Style intelligence generated"},
    "style.activated":         {"level": "info",    "entity": "style",      "icon": "✅", "label": "Style activated"},
    "style.deactivated":       {"level": "info",    "entity": "style",      "icon": "⏹",  "label": "Style deactivated"},
    "style.deleted":           {"level": "warning", "entity": "style",      "icon": "🗑️", "label": "Style deleted"},
    "style.file_added":        {"level": "info",    "entity": "style",      "icon": "📎", "label": "File added to style"},

    # ── CDD ──────────────────────────────────────────────────────────────────
    "cdd.created":             {"level": "info",    "entity": "cdd",        "icon": "📘", "label": "CDD created"},
    "cdd.version_committed":   {"level": "info",    "entity": "cdd",        "icon": "💾", "label": "CDD version committed"},
    "cdd.version_activated":   {"level": "info",    "entity": "cdd",        "icon": "✅", "label": "CDD active version changed"},
    "cdd.pinned":              {"level": "info",    "entity": "cdd",        "icon": "📌", "label": "CDD pinned for generation"},

    # ── Blueprint ─────────────────────────────────────────────────────────────
    "blueprint.generated":     {"level": "info",    "entity": "blueprint",  "icon": "🗂️", "label": "Blueprint generated"},
    "blueprint.created":       {"level": "info",    "entity": "blueprint",  "icon": "🗂️", "label": "Blueprint generated"},
    "blueprint.version_committed": {"level": "info","entity": "blueprint",  "icon": "💾", "label": "Blueprint version committed"},
    "blueprint.version_activated": {"level": "info","entity": "blueprint",  "icon": "✅", "label": "Blueprint active version changed"},
    "blueprint.pinned":        {"level": "info",    "entity": "blueprint",  "icon": "📌", "label": "Blueprint pinned for generation"},

    # ── Block-wide generation (digest pipeline) ───────────────────────────────
    # The legacy paths are synchronous, so "requested" and "created" coincide and one
    # `*.created` event covers both. Block-wide generation is always async, which
    # splits them: without a request event, a job that fails leaves NO audit trace of
    # an expensive, user-attributed operation ever having been asked for. These four
    # close that gap — request and failure are audited separately from `cdd.created` /
    # `blueprint.created`, which still record the successful outcome.
    "cdd.block_requested":       {"level": "info",    "entity": "cdd",       "icon": "🧩", "label": "Block-wide CDD generation requested"},
    "cdd.block_failed":          {"level": "warning", "entity": "cdd",       "icon": "⚠️", "label": "Block-wide CDD generation failed"},
    "blueprint.block_requested": {"level": "info",    "entity": "blueprint", "icon": "🧩", "label": "Block-wide Blueprint generation requested"},
    "blueprint.block_failed":    {"level": "warning", "entity": "blueprint", "icon": "⚠️", "label": "Block-wide Blueprint generation failed"},

    # ── Content Generation ────────────────────────────────────────────────────
    "generation.launched":     {"level": "info",    "entity": "generation", "icon": "🚀", "label": "Generation launched"},
    "content.generated":       {"level": "info",    "entity": "generation", "icon": "⚡", "label": "Content generated"},
    "content.edited":          {"level": "info",    "entity": "block",      "icon": "✏️", "label": "Content block edited"},
    "content.regenerated":     {"level": "info",    "entity": "block",      "icon": "🔄", "label": "Content block regenerated"},
    "content.snapshot":        {"level": "info",    "entity": "block",      "icon": "💾", "label": "Block snapshot saved"},
    "content.snapshot_restored": {"level": "warning","entity": "block",     "icon": "⏪", "label": "Block snapshot restored"},

    # ── Workflow & Approvals ──────────────────────────────────────────────────
    "workflow.submitted":      {"level": "info",    "entity": "workflow",   "icon": "📤", "label": "Content submitted for review"},
    "workflow.approved":       {"level": "success", "entity": "workflow",   "icon": "✅", "label": "Content approved"},
    "workflow.rejected":       {"level": "warning", "entity": "workflow",   "icon": "❌", "label": "Content rejected"},
    "workflow.published":      {"level": "success", "entity": "workflow",   "icon": "🚀", "label": "Content published"},
    "workflow.bulk_approved":  {"level": "success", "entity": "workflow",   "icon": "✅", "label": "Blocks bulk-approved"},
    "workflow.reset_draft":    {"level": "info",    "entity": "workflow",   "icon": "↩️", "label": "Block reset to draft"},

    # ── Prompts ───────────────────────────────────────────────────────────────
    "prompt.version_deployed": {"level": "info",    "entity": "prompt",     "icon": "📜", "label": "Prompt version deployed"},
    "prompt.created":          {"level": "info",    "entity": "prompt",     "icon": "📚", "label": "Prompt asset created"},

    # ── Export ────────────────────────────────────────────────────────────────
    "export.course":           {"level": "info",    "entity": "export",     "icon": "⬇️", "label": "Course exported"},
    "export.audit_log":        {"level": "info",    "entity": "export",     "icon": "📋", "label": "Audit log exported"},

    # ── System ────────────────────────────────────────────────────────────────
    "system.db_cleared":       {"level": "critical","entity": "system",     "icon": "💥", "label": "Database table cleared"},
    "system.document_uploaded":{"level": "info",    "entity": "document",   "icon": "📄", "label": "Document uploaded"},
    "system.document_archived":{"level": "info",    "entity": "document",   "icon": "🗄️", "label": "Document archived"},
}

# Reverse index: entity → list of action keys
_ENTITY_EVENTS: dict[str, list[str]] = {}
for _k, _v in AUDIT_EVENTS.items():
    _ENTITY_EVENTS.setdefault(_v["entity"], []).append(_k)


# ── Write ─────────────────────────────────────────────────────────────────────

def log_audit_event(
    db,
    user_id: str,
    action: str,
    *,
    entity_type: str = None,
    entity_id=None,
    project_id: int = None,
    course_id: int = None,
    metadata: dict = None,
    ip_address: str = None,
) -> None:
    """Record one governance audit event.

    This is the ONLY function pages should call for audit logging.

    Failure contract
    ----------------
    If writing to audit_logs fails (DB error, connection issue, etc.) the
    exception is caught, logged at ERROR level, and the caller's workflow
    continues uninterrupted.  Audit logging must NEVER crash the UI.
    """
    try:
        create_audit_log(
            db,
            user_id     = user_id,
            action      = action,
            entity_type = entity_type or AUDIT_EVENTS.get(action, {}).get("entity"),
            entity_id   = str(entity_id) if entity_id is not None else None,
            project_id  = project_id,
            course_id   = course_id,
            metadata    = metadata,
            ip_address  = ip_address,
        )
    except Exception as exc:
        _log.error(
            "Audit log write failed (action=%s user=%s): %s",
            action, user_id, exc,
            exc_info=True,
        )


# ── Legacy write helper (backward compat) ─────────────────────────────────────

def emit(db, event_type: str, actor: str, details: str, metadata: dict = None) -> None:
    """Legacy helper — writes to system_logs via log_event().

    Existing call sites continue to work.  New code should use
    log_audit_event() which writes to the structured audit_logs table.
    """
    log_event(db, event_type, actor, details, metadata or {})


# ── Read ──────────────────────────────────────────────────────────────────────

def get_audit_trail(
    db,
    event_type: str = None,
    actor: str = None,
    entity: str = None,
    limit: int = 250,
    offset: int = 0,
    project_id: int = None,
    course_id: int = None,
    date_from=None,
    date_to=None,
) -> list:
    """Query AuditLog with optional filters. Returns newest-first."""
    return list_audit_logs(
        db,
        user_id     = actor,
        action      = event_type,
        entity_type = entity,
        project_id  = project_id,
        course_id   = course_id,
        date_from   = date_from,
        date_to     = date_to,
        limit       = limit,
        offset      = offset,
    )


def count_trail(db, **kwargs) -> int:
    """Count audit records matching the same filter set as get_audit_trail."""
    return count_audit_logs(db, **kwargs)


def get_event_meta(action: str) -> dict:
    """Return taxonomy metadata (level, entity, icon, label) for an action key."""
    return AUDIT_EVENTS.get(action, {"level": "info", "entity": "unknown", "icon": "📌", "label": action})


def get_actors(db) -> list[str]:
    return list_distinct_actors(db)


def get_all_actions(db) -> list[str]:
    return list_distinct_actions(db)


def get_entity_types(db) -> list[str]:
    return list_distinct_entity_types(db)
