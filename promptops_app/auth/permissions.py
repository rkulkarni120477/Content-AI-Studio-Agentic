"""Centralized RBAC — role definitions, permission registry, and enforcement helpers.

Roles (DB value → display label)
---------------------------------
  admin    → Admin
  reviewer → Lead
  author   → ID

Permission keys follow  <domain>.<action>  dot-notation.
``rbac_check`` is the pure boolean test; ``rbac_gate`` additionally renders
the Streamlit error banner and is suitable for button / form handlers.
"""

import logging

# Streamlit is imported lazily — only when rbac_gate() is called from the
# Streamlit app. FastAPI code uses require_permission() from app/core/dependencies.py
# and never calls rbac_gate(), so the import never triggers in the API process.
try:
    import streamlit as _st
    _STREAMLIT_AVAILABLE = True
except ImportError:
    _st = None
    _STREAMLIT_AVAILABLE = False

from promptops_app.database import can_modify_style, _is_lead_for_project, _is_lead_for_course

_log = logging.getLogger(__name__)

# ── Role display ──────────────────────────────────────────────────────────────
ROLE_DB_VALUES = ("admin", "reviewer", "author")

ROLE_DISPLAY: dict[str, str] = {
    "admin":    "Admin",
    "reviewer": "Lead",
    "author":   "ID",
}

ROLE_DISPLAY_OPTIONS = ["ID", "Lead", "Admin"]
ROLE_DISPLAY_TO_DB   = {"ID": "author", "Lead": "reviewer", "Admin": "admin"}


def role_label(role: str) -> str:
    """Return the UI display label for a DB role value."""
    return ROLE_DISPLAY.get(role, role)


# ── Permission registry ───────────────────────────────────────────────────────
# Maps  permission_key  →  list[db_role]  that may perform the action.
#
# Scope note: "reviewer" (Lead) permissions are project-scoped in the business
# logic.  This table grants the *capability*; page code enforces the scope.
_A  = "admin"
_L  = "reviewer"   # Lead
_ID = "author"     # ID / Instructional Designer

_PERMISSIONS: dict[str, list[str]] = {

    # ── Style ────────────────────────────────────────────────────────────────
    "style.create":          [_A, _L],
    "style.edit":            [_A, _L],
    "style.delete":          [_A],
    "style.activate":        [_A, _L],
    "style.deactivate":      [_A, _L],
    "style.upload":          [_A, _L],
    "style.understand":      [_A, _L],   # generate / refine style intelligence

    # ── Prompt fixing (scope-level default locking) ───────────────────────────
    "prompt.fix":            [_A, _L],   # admin: any scope; lead: project/cluster/course only
    "prompt.unfix":          [_A, _L],

    # ── CDD ──────────────────────────────────────────────────────────────────
    "cdd.generate":          [_A, _L, _ID],
    "cdd.edit":              [_A, _L, _ID],
    "cdd.review":            [_A, _L],
    "cdd.pin":               [_A, _L, _ID],
    "cdd.version":           [_A, _L, _ID],

    # ── Blueprint ────────────────────────────────────────────────────────────
    "blueprint.generate":    [_A, _L, _ID],
    "blueprint.edit":        [_A, _L, _ID],
    "blueprint.review":      [_A, _L],
    "blueprint.pin":         [_A, _L, _ID],
    "blueprint.version":     [_A, _L, _ID],

    # ── Generate / Editor ────────────────────────────────────────────────────
    "generate.run":          [_A, _L, _ID],
    "editor.edit":           [_A, _L, _ID],
    "editor.review":         [_A, _L],        # approve / reject / publish blocks
    "editor.snapshot":       [_A, _L, _ID],   # manual version snapshot

    # ── Workflow ─────────────────────────────────────────────────────────────
    "workflow.submit":            [_A, _L, _ID],  # submit own block for review
    "workflow.approve":           [_A, _L],        # approve in review queue
    "workflow.request_changes":   [_A, _L],        # send back for changes (not full reject)
    "workflow.publish":           [_A, _L],        # publish approved block
    "workflow.bulk_approve":      [_A],            # admin bulk-approve
    "workflow.reset_draft":       [_A, _L, _ID],  # reset back to draft
    "workflow.archive":           [_A],            # archive approved/published blocks

    # ── Export ───────────────────────────────────────────────────────────────
    "export.course":          [_A, _L, _ID],  # all roles may export their content
    "export.audit_log":       [_A, _L],        # CSV audit-trail export

    # ── Prompts ──────────────────────────────────────────────────────────────
    "prompts.view":           [_A, _L, _ID],
    "prompts.create":         [_A, _L],
    "prompts.manage":         [_A, _L],        # version, deploy, edit

    # ── Analytics ────────────────────────────────────────────────────────────
    "analytics.view_own":     [_A, _L, _ID],  # own project metrics
    "analytics.view_all":     [_A],            # cross-project admin view
    "analytics.export":       [_A, _L],

    # ── LLM Cost Tracking ────────────────────────────────────────────────────
    "llm_usage.view_own":     [_A, _L, _ID],  # personal usage summary (ID sees own only)
    "llm_usage.view_project": [_A, _L],        # project-scoped cost breakdown
    "llm_usage.view_all":     [_A],            # cross-project admin dashboard

    # ── Users ────────────────────────────────────────────────────────────────
    "users.view":             [_A, _L],
    "users.create":           [_A],
    "users.edit":             [_A],
    "users.toggle":           [_A],            # activate / deactivate account
    "users.assign":           [_A, _L],        # assign users to project/course

    # ── Projects & Courses ───────────────────────────────────────────────────
    "project.create":         [_A],
    "project.edit":           [_A],
    "project.delete":         [_A],
    "course.create":          [_A, _L],
    "course.edit":            [_A, _L],
    "course.delete":          [_A],

    # ── Central Repository ───────────────────────────────────────────────────
    "central.view":           [_A],
    "central.create":         [_A],
    "central.edit":           [_A],
    "central.delete":         [_A],
    "central.reuse":          [_A],

    # ── System ───────────────────────────────────────────────────────────────
    "system.clear_db":        [_A],
    "system.view_logs":       [_A, _L],
    "system.analytics":       [_A, _L, _ID],  # access the Analytics tab at all
}

# Permissions Lead is explicitly BLOCKED from (belt-and-suspenders)
_LEAD_BLOCKED: frozenset[str] = frozenset({
    "users.create",
    "users.edit",
    "users.toggle",
    "project.create",
    "project.delete",
    "course.delete",
    "workflow.bulk_approve",
    "system.clear_db",
    "analytics.view_all",
    "llm_usage.view_all",
    "central.view",
    "central.create",
    "central.edit",
    "central.delete",
    "central.reuse",
})


# ── Core checkers ─────────────────────────────────────────────────────────────

def rbac_check(role: str, permission: str) -> bool:
    """Return True iff *role* may perform *permission*.

    Lead (reviewer) is additionally checked against _LEAD_BLOCKED so that
    any future accidental permission grant is still enforced.
    """
    if role == _L and permission in _LEAD_BLOCKED:
        return False
    return role in _PERMISSIONS.get(permission, [])


def rbac_gate(role: str, permission: str, action_label: str = "This action") -> bool:
    """Check permission and render a blocking error banner if denied.

    Returns True if allowed, False (+ st.error) if denied.
    Only call this from Streamlit pages. FastAPI code uses
    require_permission() from app/core/dependencies.py instead.
    """
    if rbac_check(role, permission):
        return True
    _log.warning(
        "permission_denied  role=%r  permission=%r  action=%r",
        role, permission, action_label,
    )
    disp = role_label(role)
    if _STREAMLIT_AVAILABLE and _st is not None:
        _st.error(
            f"🔒 **Access Denied** — {action_label} requires a higher permission level.\n\n"
            f"Your role: **{disp}**.  "
            f"Contact an Admin to request access."
        )
    return False


# ── Convenience helpers ───────────────────────────────────────────────────────

def get_permissions_for_role(role: str) -> list[str]:
    """Return every permission key the given role holds."""
    return sorted(
        perm
        for perm, roles in _PERMISSIONS.items()
        if rbac_check(role, perm)
    )


def roles_for_permission(permission: str) -> list[str]:
    """Return the display labels of roles that hold a given permission."""
    raw = _PERMISSIONS.get(permission, [])
    return [role_label(r) for r in raw if rbac_check(r, permission)]
