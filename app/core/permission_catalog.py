"""
Human-readable, category-grouped view of the real permission catalog.

This wraps the actual enforcement table in ``app/core/permissions.py``
(``_PERMISSIONS``) with labels/descriptions for the tenant custom-Roles UI —
every key listed here is a real, currently-enforced permission, not a
separate/aspirational list. Categories mirror the section comments already
in ``_PERMISSIONS``.
"""

from __future__ import annotations

from app.core.permissions import _PERMISSIONS

PERMISSION_CATEGORIES: list[dict] = [
    {
        "category": "style",
        "label": "Style Management",
        "permissions": [
            {"key": "style.create",     "label": "Create style guides"},
            {"key": "style.edit",       "label": "Edit style guides"},
            {"key": "style.delete",     "label": "Delete style guides"},
            {"key": "style.activate",   "label": "Activate a style guide"},
            {"key": "style.deactivate", "label": "Deactivate a style guide"},
            {"key": "style.upload",     "label": "Upload style reference documents"},
            {"key": "style.understand", "label": "Run style understanding analysis"},
        ],
    },
    {
        "category": "cdd",
        "label": "CDD Pipeline",
        "permissions": [
            {"key": "cdd.generate", "label": "Generate a CDD"},
            {"key": "cdd.edit",     "label": "Edit a CDD"},
            {"key": "cdd.review",   "label": "Review/approve a CDD"},
            {"key": "cdd.pin",      "label": "Pin a CDD version"},
            {"key": "cdd.version",  "label": "Create a new CDD version"},
            {"key": "cdd.archive",  "label": "Archive / restore a CDD"},
            {"key": "cdd.purge",    "label": "Permanently delete an archived CDD"},
        ],
    },
    {
        "category": "blueprint",
        "label": "Blueprint Pipeline",
        "permissions": [
            {"key": "blueprint.generate", "label": "Generate a blueprint"},
            {"key": "blueprint.edit",     "label": "Edit a blueprint"},
            {"key": "blueprint.review",   "label": "Review/approve a blueprint"},
            {"key": "blueprint.pin",      "label": "Pin a blueprint version"},
            {"key": "blueprint.version",  "label": "Create a new blueprint version"},
            {"key": "blueprint.archive",  "label": "Archive / restore a blueprint"},
            {"key": "blueprint.purge",    "label": "Permanently delete an archived blueprint"},
        ],
    },
    {
        "category": "generate",
        "label": "Content Generation & Editing",
        "permissions": [
            {"key": "generate.run",      "label": "Run content generation"},
            {"key": "editor.edit",       "label": "Edit generated content"},
            {"key": "editor.review",     "label": "Review/approve generated content"},
            {"key": "editor.snapshot",   "label": "Create a content snapshot"},
        ],
    },
    {
        "category": "workflow",
        "label": "Workflow & Approvals",
        "permissions": [
            {"key": "workflow.submit",          "label": "Submit for review"},
            {"key": "workflow.approve",          "label": "Approve"},
            {"key": "workflow.request_changes",  "label": "Request changes"},
            {"key": "workflow.publish",          "label": "Publish"},
            {"key": "workflow.bulk_approve",     "label": "Bulk approve"},
            {"key": "workflow.bulk_submit",      "label": "Bulk submit for review"},
            {"key": "workflow.bulk_publish",     "label": "Bulk publish"},
            {"key": "workflow.reset_draft",      "label": "Reset to draft"},
            {"key": "workflow.archive",          "label": "Archive"},
        ],
    },
    {
        "category": "feedback",
        "label": "Reviewer Feedback",
        "permissions": [
            {"key": "feedback.view",      "label": "View reviewer feedback"},
            {"key": "feedback.upload",    "label": "Upload & analyse feedback documents"},
            {"key": "feedback.recommend", "label": "Generate AI recommendations for feedback"},
            {"key": "feedback.delete",    "label": "Delete feedback items"},
        ],
    },
    {
        "category": "ce_review",
        "label": "CE Agent Review",
        "permissions": [
            {"key": "review.configure", "label": "Upload & manage the CE checklist"},
        ],
    },
    {
        "category": "export",
        "label": "Export",
        "permissions": [
            {"key": "export.course",     "label": "Export a course"},
            {"key": "export.audit_log",  "label": "Export the audit log"},
        ],
    },
    {
        "category": "prompts",
        "label": "Prompt Registry",
        "permissions": [
            {"key": "prompts.view",           "label": "View pipeline prompts"},
            {"key": "prompts.create",         "label": "Create pipeline prompts"},
            {"key": "prompts.manage",         "label": "Manage pipeline prompts"},
            {"key": "prompt.pipeline.edit",   "label": "Edit pipeline-kind prompt bindings (admin-tier)"},
        ],
    },
    {
        "category": "analytics",
        "label": "Analytics",
        "permissions": [
            {"key": "analytics.view_own", "label": "View own analytics"},
            {"key": "analytics.view_all", "label": "View analytics across the tenant"},
            {"key": "analytics.export",   "label": "Export analytics"},
        ],
    },
    {
        "category": "llm_usage",
        "label": "LLM Cost Tracking",
        "permissions": [
            {"key": "llm_usage.view_own",     "label": "View own LLM usage/cost"},
            {"key": "llm_usage.view_project", "label": "View project LLM usage/cost"},
            {"key": "llm_usage.view_all",     "label": "View all LLM usage/cost"},
        ],
    },
    {
        "category": "users",
        "label": "User Management",
        "permissions": [
            {"key": "users.view",   "label": "View users"},
            {"key": "users.create", "label": "Create users"},
            {"key": "users.edit",   "label": "Edit users"},
            {"key": "users.toggle", "label": "Activate/deactivate users"},
            {"key": "users.assign", "label": "Assign users to projects/courses"},
        ],
    },
    {
        "category": "project",
        "label": "Projects, Clusters & Courses",
        "permissions": [
            {"key": "project.create",        "label": "Create projects"},
            {"key": "project.edit",          "label": "Edit projects"},
            {"key": "project.delete",        "label": "Delete projects"},
            {"key": "cluster.create",        "label": "Create clusters"},
            {"key": "cluster.edit",          "label": "Edit clusters"},
            {"key": "cluster.delete",        "label": "Delete clusters"},
            {"key": "cluster_prompt.create", "label": "Create cluster prompts"},
            {"key": "cluster_prompt.delete", "label": "Delete cluster prompts"},
            {"key": "course.create",         "label": "Create courses"},
            {"key": "course.edit",           "label": "Edit courses"},
            {"key": "course.delete",         "label": "Delete courses"},
        ],
    },
    {
        "category": "central",
        "label": "Central Repository",
        "permissions": [
            {"key": "central.view",   "label": "View the central repository"},
            {"key": "central.create", "label": "Add to the central repository"},
            {"key": "central.edit",   "label": "Edit the central repository"},
            {"key": "central.delete", "label": "Delete from the central repository"},
        ],
    },
    {
        "category": "prompt_library",
        "label": "Prompt Library",
        "permissions": [
            {"key": "prompt_library.view",            "label": "Browse the prompt library"},
            {"key": "prompt_library.manage",          "label": "Manage prompt library entries"},
            {"key": "prompt_library.request",         "label": "Request a prompt"},
            {"key": "prompt_library.request_manage",  "label": "Manage prompt requests"},
            {"key": "prompt_library.review",          "label": "Review prompts"},
            {"key": "prompt_library.review_read_all", "label": "View all prompt reviews"},
            {"key": "prompt_library.audit",           "label": "View the prompt library audit log"},
        ],
    },
    {
        "category": "system",
        "label": "System Administration",
        "permissions": [
            {"key": "system.clear_db",   "label": "Clear the database (destructive)"},
            {"key": "system.view_logs",  "label": "View system logs"},
            {"key": "system.analytics",  "label": "View system-wide analytics"},
        ],
    },
]


def _validate_catalog_matches_enforcement() -> None:
    """Fail fast at import time if this catalog drifts from the real _PERMISSIONS table."""
    catalog_keys = {p["key"] for cat in PERMISSION_CATEGORIES for p in cat["permissions"]}
    real_keys = set(_PERMISSIONS.keys())
    missing = real_keys - catalog_keys
    extra = catalog_keys - real_keys
    if missing or extra:
        raise RuntimeError(
            "permission_catalog.py is out of sync with app/core/permissions.py: "
            f"missing={sorted(missing)}  extra={sorted(extra)}"
        )


_validate_catalog_matches_enforcement()
