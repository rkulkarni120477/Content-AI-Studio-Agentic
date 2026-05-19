"""Centralized RBAC entry point.

Import everything RBAC-related from this single module.

Usage:
    from promptops_app.auth.rbac import rbac_check, rbac_gate, role_label
    from promptops_app.auth.rbac import can_modify_style
"""

from promptops_app.auth.permissions import (
    ROLE_DISPLAY,
    ROLE_DISPLAY_OPTIONS,
    ROLE_DISPLAY_TO_DB,
    _PERMISSIONS,
    _LEAD_BLOCKED,
    role_label,
    rbac_check,
    rbac_gate,
    can_modify_style,
    _is_lead_for_project,
    _is_lead_for_course,
)
