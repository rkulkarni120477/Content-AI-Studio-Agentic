"""Machine-readable permission matrix for the PromptOps RBAC system.

Provides:
  PERMISSION_MATRIX  — dict[role_db → set[permission_key]]
  PERMISSION_CATALOG — structured catalog with categories, descriptions, icons
  get_matrix_rows()  — flat list of dicts for rendering in a Streamlit table
"""

from __future__ import annotations

from promptops_app.auth.permissions import (
    _PERMISSIONS,
    get_permissions_for_role,
    rbac_check,
)

# ── Role order for column display ─────────────────────────────────────────────
ROLES_ORDERED = [("admin", "Admin"), ("reviewer", "Lead"), ("author", "ID")]

# ── Full permission matrix ────────────────────────────────────────────────────
PERMISSION_MATRIX: dict[str, set[str]] = {
    role_db: set(get_permissions_for_role(role_db))
    for role_db, _ in ROLES_ORDERED
}

# ── Structured catalog — category → list of permission defs ──────────────────
PERMISSION_CATALOG: list[dict] = [
    # ── User Management ──────────────────────────────────────────────────────
    {
        "category": "User Management",
        "icon": "👥",
        "permissions": [
            {"key": "users.view",   "label": "View user list",           "scope": "global"},
            {"key": "users.create", "label": "Create new users",         "scope": "global"},
            {"key": "users.edit",   "label": "Edit user roles",          "scope": "global"},
            {"key": "users.toggle", "label": "Activate / deactivate accounts", "scope": "global"},
            {"key": "users.assign", "label": "Assign users to projects", "scope": "project"},
        ],
    },
    # ── Project & Course Management ──────────────────────────────────────────
    {
        "category": "Project & Course",
        "icon": "📁",
        "permissions": [
            {"key": "project.create", "label": "Create projects",       "scope": "global"},
            {"key": "project.edit",   "label": "Edit project settings", "scope": "global"},
            {"key": "project.delete", "label": "Delete projects",       "scope": "global"},
            {"key": "course.create",  "label": "Create courses",        "scope": "project"},
            {"key": "course.edit",    "label": "Edit course settings",  "scope": "project"},
            {"key": "course.delete",  "label": "Delete courses",        "scope": "project"},
        ],
    },
    # ── Style Management ─────────────────────────────────────────────────────
    {
        "category": "Style Management",
        "icon": "🎨",
        "permissions": [
            {"key": "style.create",     "label": "Create styles",                   "scope": "project"},
            {"key": "style.edit",       "label": "Edit style settings",             "scope": "project"},
            {"key": "style.upload",     "label": "Upload style reference files",    "scope": "project"},
            {"key": "style.activate",   "label": "Activate / deactivate styles",   "scope": "course"},
            {"key": "style.understand", "label": "Generate style intelligence",    "scope": "project"},
            {"key": "style.delete",     "label": "Delete styles permanently",       "scope": "global"},
        ],
    },
    # ── CDD Pipeline ────────────────────────────────────────────────────────
    {
        "category": "Course Design Document",
        "icon": "📘",
        "permissions": [
            {"key": "cdd.generate", "label": "Generate CDDs with AI",   "scope": "course"},
            {"key": "cdd.edit",     "label": "Edit / version CDDs",     "scope": "course"},
            {"key": "cdd.pin",      "label": "Pin CDD for generation",  "scope": "course"},
            {"key": "cdd.review",   "label": "Review & approve CDDs",   "scope": "course"},
            {"key": "cdd.version",  "label": "Create new CDD versions", "scope": "course"},
            {"key": "cdd.archive",  "label": "Archive / restore CDDs",  "scope": "course"},
            {"key": "cdd.purge",    "label": "Permanently delete archived CDDs", "scope": "course"},
        ],
    },
    # ── Blueprint Pipeline ───────────────────────────────────────────────────
    {
        "category": "Module Blueprint",
        "icon": "🧩",
        "permissions": [
            {"key": "blueprint.generate", "label": "Generate blueprints with AI",  "scope": "course"},
            {"key": "blueprint.edit",     "label": "Edit / version blueprints",    "scope": "course"},
            {"key": "blueprint.pin",      "label": "Pin blueprint for generation", "scope": "course"},
            {"key": "blueprint.review",   "label": "Review & approve blueprints",  "scope": "course"},
            {"key": "blueprint.version",  "label": "Create new blueprint versions","scope": "course"},
            {"key": "blueprint.archive",  "label": "Archive / restore blueprints", "scope": "course"},
            {"key": "blueprint.purge",    "label": "Permanently delete archived blueprints", "scope": "course"},
        ],
    },
    # ── Content Generation ───────────────────────────────────────────────────
    {
        "category": "Content Generation",
        "icon": "⚙️",
        "permissions": [
            {"key": "generate.run",    "label": "Run generation pipeline",  "scope": "course"},
            {"key": "editor.edit",     "label": "Edit & regenerate blocks", "scope": "course"},
            {"key": "editor.snapshot", "label": "Save block snapshots",     "scope": "course"},
            {"key": "editor.review",   "label": "Submit AI quality reviews","scope": "course"},
        ],
    },
    # ── Workflow & Approvals ─────────────────────────────────────────────────
    {
        "category": "Workflow & Approvals",
        "icon": "🚦",
        "permissions": [
            {"key": "workflow.submit",       "label": "Submit blocks for review",  "scope": "course"},
            {"key": "workflow.approve",      "label": "Approve / reject blocks",  "scope": "course"},
            {"key": "workflow.publish",      "label": "Publish approved blocks",   "scope": "course"},
            {"key": "workflow.bulk_approve", "label": "Bulk-approve all blocks",   "scope": "global"},
            {"key": "workflow.reset_draft",  "label": "Reset blocks to Draft",    "scope": "course"},
        ],
    },
    # ── Prompt Registry ──────────────────────────────────────────────────────
    {
        "category": "Prompt Registry",
        "icon": "📚",
        "permissions": [
            {"key": "prompts.view",   "label": "Browse prompt registry",    "scope": "global"},
            {"key": "prompts.create", "label": "Create prompt assets",      "scope": "global"},
            {"key": "prompts.manage", "label": "Version & deploy prompts",  "scope": "global"},
        ],
    },
    # ── Export ───────────────────────────────────────────────────────────────
    {
        "category": "Export",
        "icon": "📥",
        "permissions": [
            {"key": "export.course",    "label": "Export course (MD / HTML / DOCX)", "scope": "course"},
            {"key": "export.audit_log", "label": "Export audit trail (CSV)",          "scope": "global"},
        ],
    },
    # ── Analytics ────────────────────────────────────────────────────────────
    {
        "category": "Analytics & Observability",
        "icon": "📊",
        "permissions": [
            {"key": "analytics.view_own",  "label": "View own project metrics",      "scope": "project"},
            {"key": "analytics.view_all",  "label": "View cross-project analytics",  "scope": "global"},
            {"key": "analytics.export",    "label": "Export analytics data",         "scope": "project"},
            {"key": "system.view_logs",    "label": "View system event log",         "scope": "global"},
        ],
    },
    # ── System ───────────────────────────────────────────────────────────────
    {
        "category": "System Administration",
        "icon": "⚙️",
        "permissions": [
            {"key": "system.clear_db",  "label": "Clear database tables",          "scope": "global"},
            {"key": "system.analytics", "label": "Access the Analytics tab",       "scope": "global"},
        ],
    },
]


# ── Flat row builder for Streamlit st.dataframe ───────────────────────────────

def get_matrix_rows() -> list[dict]:
    """Return a flat list of dicts suitable for st.dataframe / st.table.

    Each row: {Category, Permission, Description, Scope, Admin, Lead, ID}
    """
    rows = []
    for section in PERMISSION_CATALOG:
        for p in section["permissions"]:
            row = {
                "Category":   section["category"],
                "Permission": p["key"],
                "Description":p["label"],
                "Scope":      p["scope"],
            }
            for role_db, role_label in ROLES_ORDERED:
                row[role_label] = "✅" if rbac_check(role_db, p["key"]) else "—"
            rows.append(row)
    return rows
