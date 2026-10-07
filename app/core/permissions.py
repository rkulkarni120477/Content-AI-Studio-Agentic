"""
Role-Based Access Control (RBAC) — permission registry and enforcement.

Ported from ``promptops_app/auth/permissions.py``.
The Streamlit-specific ``rbac_gate()`` function (which rendered st.error banners)
has been replaced by ``require_permission()``, which raises ``PermissionDeniedError``
and is used as a FastAPI ``Depends()`` in route handlers.

Roles
-----
  admin    → Full platform access
  reviewer → Lead / project-scoped approvals
  author   → ID / content creation only

Permission keys follow  <domain>.<action>  dot-notation.
The full catalog is defined in ``PERMISSION_CATALOG`` in this module
and is exposed via ``GET /api/v1/admin/permissions``.

Usage
-----
    from app.core.permissions import rbac_check, get_permissions_for_role

    # Pure boolean check (used in service layer):
    if not rbac_check(user.role, "workflow.bulk_approve"):
        raise PermissionDeniedError("workflow.bulk_approve", user.role)

    # FastAPI dependency (used in route layer):
    @router.post("/blocks/{id}/approve")
    def approve(user = Depends(require_permission("workflow.approve"))): ...
"""

from __future__ import annotations

# ── Role constants ─────────────────────────────────────────────────────────────
# Using short aliases internally for readability in the permission table.
_ADMIN    = "admin"
_REVIEWER = "reviewer"   # displayed as "Lead"
_AUTHOR   = "author"     # displayed as "ID"

ROLE_DISPLAY_NAMES: dict[str, str] = {
    _ADMIN:    "Admin",
    _REVIEWER: "Lead",
    _AUTHOR:   "ID",
}

ROLE_DISPLAY_TO_DB: dict[str, str] = {
    "Admin": _ADMIN,
    "Lead":  _REVIEWER,
    "ID":    _AUTHOR,
}


def role_label(db_role: str) -> str:
    """Return the human-readable display label for a database role value."""
    return ROLE_DISPLAY_NAMES.get(db_role, db_role)


# ---------------------------------------------------------------------------
# Permission registry
# Each key maps to the list of DB role values that may perform the action.
# ---------------------------------------------------------------------------

_PERMISSIONS: dict[str, list[str]] = {

    # ── Style management ──────────────────────────────────────────────────────
    "style.create":          [_ADMIN, _REVIEWER],
    "style.edit":            [_ADMIN, _REVIEWER],
    "style.delete":          [_ADMIN],
    "style.activate":        [_ADMIN, _REVIEWER],
    "style.deactivate":      [_ADMIN, _REVIEWER],
    "style.upload":          [_ADMIN, _REVIEWER],
    "style.understand":      [_ADMIN, _REVIEWER],

    # ── CDD pipeline ──────────────────────────────────────────────────────────
    "cdd.generate":          [_ADMIN, _REVIEWER, _AUTHOR],
    "cdd.edit":              [_ADMIN, _REVIEWER, _AUTHOR],
    "cdd.review":            [_ADMIN, _REVIEWER],
    "cdd.pin":               [_ADMIN, _REVIEWER, _AUTHOR],
    "cdd.version":           [_ADMIN, _REVIEWER, _AUTHOR],
    # Archiving is reversible, so authors clean up after themselves — they are
    # the ones who create the duplicates. Purging is not reversible: admin only.
    "cdd.archive":           [_ADMIN, _REVIEWER, _AUTHOR],
    "cdd.purge":             [_ADMIN],

    # ── Blueprint pipeline ────────────────────────────────────────────────────
    "blueprint.generate":    [_ADMIN, _REVIEWER, _AUTHOR],
    "blueprint.edit":        [_ADMIN, _REVIEWER, _AUTHOR],
    "blueprint.review":      [_ADMIN, _REVIEWER],
    "blueprint.pin":         [_ADMIN, _REVIEWER, _AUTHOR],
    "blueprint.version":     [_ADMIN, _REVIEWER, _AUTHOR],
    "blueprint.archive":     [_ADMIN, _REVIEWER, _AUTHOR],
    "blueprint.purge":       [_ADMIN],

    # ── Content generation and editing ────────────────────────────────────────
    "generate.run":          [_ADMIN, _REVIEWER, _AUTHOR],
    "editor.edit":           [_ADMIN, _REVIEWER, _AUTHOR],
    "editor.review":         [_ADMIN, _REVIEWER],
    "editor.snapshot":       [_ADMIN, _REVIEWER, _AUTHOR],

    # ── Workflow state transitions ────────────────────────────────────────────
    "workflow.submit":          [_ADMIN, _REVIEWER, _AUTHOR],
    "workflow.approve":         [_ADMIN, _REVIEWER],
    "workflow.request_changes": [_ADMIN, _REVIEWER],
    "workflow.publish":         [_ADMIN, _REVIEWER],
    "workflow.bulk_approve":    [_ADMIN],
    "workflow.bulk_submit":     [_ADMIN],
    "workflow.bulk_publish":    [_ADMIN],
    "workflow.reset_draft":     [_ADMIN, _REVIEWER, _AUTHOR],
    "workflow.archive":         [_ADMIN],

    # ── Reviewer feedback ─────────────────────────────────────────────────────
    "feedback.view":         [_ADMIN, _REVIEWER, _AUTHOR],
    "feedback.upload":       [_ADMIN, _REVIEWER, _AUTHOR],
    "feedback.recommend":    [_ADMIN, _REVIEWER, _AUTHOR],
    "feedback.delete":       [_ADMIN, _REVIEWER, _AUTHOR],

    # ── CE Agent Review ───────────────────────────────────────────────────────
    # Managing the client-wide CE checklist. Running reviews and applying fixes
    # (added in later steps) reuse editor.edit; this key is only for curating the
    # checklist the whole client is judged against.
    "review.configure":      [_ADMIN, _REVIEWER, _AUTHOR],

    # ── Export ────────────────────────────────────────────────────────────────
    "export.course":         [_ADMIN, _REVIEWER, _AUTHOR],
    "export.audit_log":      [_ADMIN, _REVIEWER],

    # ── Prompt registry ───────────────────────────────────────────────────────
    "prompts.view":          [_ADMIN, _REVIEWER, _AUTHOR],
    "prompts.create":        [_ADMIN, _REVIEWER],
    "prompts.manage":        [_ADMIN, _REVIEWER],
    # Writes to pipeline-kind prompt rows (set-default, workflow-state
    # transitions, unrestricted scope locks) — admin/prompt-engineer only.
    "prompt.pipeline.edit":  [_ADMIN],

    # ── Analytics ─────────────────────────────────────────────────────────────
    "analytics.view_own":    [_ADMIN, _REVIEWER, _AUTHOR],
    "analytics.view_all":    [_ADMIN],
    "analytics.export":      [_ADMIN, _REVIEWER],

    # ── LLM cost tracking ─────────────────────────────────────────────────────
    "llm_usage.view_own":     [_ADMIN, _REVIEWER, _AUTHOR],
    "llm_usage.view_project": [_ADMIN, _REVIEWER],
    "llm_usage.view_all":     [_ADMIN],

    # ── User management ───────────────────────────────────────────────────────
    "users.view":            [_ADMIN, _REVIEWER],
    "users.create":          [_ADMIN],
    "users.edit":            [_ADMIN],
    "users.toggle":          [_ADMIN],
    "users.assign":          [_ADMIN, _REVIEWER],

    # ── Project, cluster, and course management ───────────────────────────────
    "project.create":        [_ADMIN],
    "project.edit":          [_ADMIN],
    "project.delete":        [_ADMIN],
    "cluster.create":        [_ADMIN],
    "cluster.edit":          [_ADMIN],
    "cluster.delete":        [_ADMIN],
    "cluster_prompt.create": [_ADMIN],
    "cluster_prompt.delete": [_ADMIN],
    "course.create":         [_ADMIN, _REVIEWER],
    "course.edit":           [_ADMIN, _REVIEWER],
    "course.delete":         [_ADMIN],

    # ── Central repository (admin-curated assets) ─────────────────────────────
    "central.view":          [_ADMIN],
    "central.create":        [_ADMIN],
    "central.edit":          [_ADMIN],
    "central.delete":        [_ADMIN],

    # ── Prompt Library (ported standalone app; replaces Central Repository) ────
    "prompt_library.view":            [_ADMIN, _REVIEWER, _AUTHOR],
    "prompt_library.manage":          [_ADMIN, _REVIEWER],
    "prompt_library.request":         [_ADMIN, _REVIEWER, _AUTHOR],
    "prompt_library.request_manage":  [_ADMIN, _REVIEWER],
    "prompt_library.review":          [_ADMIN, _REVIEWER, _AUTHOR],
    "prompt_library.review_read_all": [_ADMIN, _REVIEWER],
    "prompt_library.audit":           [_ADMIN],

    # ── Agent Builder ─────────────────────────────────────────────────────────
    # Agent discovery: authors can see available agents they can run
    "agents.view":                [_ADMIN, _REVIEWER, _AUTHOR],
    # Creation and configuration: admin only (tenant admin)
    "agents.create":              [_ADMIN],
    "agents.configure":           [_ADMIN],
    "agents.templates.manage":    [_ADMIN],  # Platform admin only (tested separately)
    # Testing and activation: admin only
    "agents.test":                [_ADMIN, _AUTHOR],  # Authors can test before activation
    "agents.activate":            [_ADMIN],
    "agents.pause":               [_ADMIN],
    "agents.archive":             [_ADMIN],
    # Execution: authors run agents, reviewers run review agents
    "agents.run":                 [_ADMIN, _REVIEWER, _AUTHOR],
    # Change application: authors apply their own agent results
    "agents.apply":               [_ADMIN, _REVIEWER, _AUTHOR],
    # History and inspection
    "agents.runs.view_own":       [_ADMIN, _REVIEWER, _AUTHOR],  # See own runs
    "agents.runs.view_tenant":    [_ADMIN, _REVIEWER],            # Admin/reviewer: all runs
    "agents.costs.view_own":      [_ADMIN, _REVIEWER, _AUTHOR],
    "agents.costs.view_tenant":   [_ADMIN],

    # ── System administration ─────────────────────────────────────────────────
    "system.clear_db":       [_ADMIN],
    "system.view_logs":      [_ADMIN, _REVIEWER],
    "system.analytics":      [_ADMIN, _REVIEWER, _AUTHOR],
}

# Permissions the Reviewer (Lead) role can never perform, regardless of
# what the table above says.  Belt-and-suspenders guard against accidental
# permission grants during future table updates.
_REVIEWER_BLOCKLIST: frozenset[str] = frozenset({
    "prompt.pipeline.edit",
    "users.create",
    "users.edit",
    "users.toggle",
    "project.create",
    "project.delete",
    "cluster.create",
    "cluster.edit",
    "cluster.delete",
    "cluster_prompt.create",
    "cluster_prompt.delete",
    "course.delete",
    "cdd.purge",
    "blueprint.purge",
    "workflow.bulk_approve",
    "workflow.bulk_submit",
    "workflow.bulk_publish",
    "workflow.archive",
    "system.clear_db",
    "analytics.view_all",
    "llm_usage.view_all",
    "central.view",
    "central.create",
    "central.edit",
    "central.delete",
    # Agent builder: reviewers cannot configure or activate agents
    "agents.create",
    "agents.configure",
    "agents.templates.manage",
    "agents.activate",
    "agents.pause",
    "agents.archive",
    "agents.costs.view_tenant",
})


# ---------------------------------------------------------------------------
# Core RBAC functions
# ---------------------------------------------------------------------------

def rbac_check(role: str, permission: str) -> bool:
    """
    Return True if the given role holds the given permission.

    The reviewer blocklist is enforced here so accidental future table changes
    cannot escalate reviewer privileges.
    """
    if role == _REVIEWER and permission in _REVIEWER_BLOCKLIST:
        return False
    return role in _PERMISSIONS.get(permission, [])


def get_permissions_for_role(role: str) -> list[str]:
    """Return a sorted list of all permission keys held by the given role."""
    return sorted(
        permission
        for permission in _PERMISSIONS
        if rbac_check(role, permission)
    )


def effective_rbac_check(user, permission: str) -> bool:
    """Like rbac_check, but honors a custom tenant role's permission set.

    If `user` carries `_custom_permissions` (set by get_current_user for
    members on a custom tenant role — see app/services/tenant_service.py),
    that explicit set is authoritative. Otherwise falls back to the static
    system-role catalog exactly as before.
    """
    custom = getattr(user, "_custom_permissions", None)
    if custom is not None:
        return permission in custom
    return rbac_check(user.role, permission)


def effective_permissions(user) -> list[str]:
    """Sorted list of permission keys the user effectively holds."""
    custom = getattr(user, "_custom_permissions", None)
    if custom is not None:
        return sorted(custom)
    return get_permissions_for_role(user.role)
