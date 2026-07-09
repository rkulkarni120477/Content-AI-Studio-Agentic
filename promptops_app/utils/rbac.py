"""RBAC helpers — convenience re-export from auth.permissions."""

from promptops_app.auth.permissions import (
    rbac_check, rbac_gate, role_label,
    can_modify_style, _is_lead_for_project, _is_lead_for_course,
    ROLE_DISPLAY, ROLE_DISPLAY_OPTIONS, ROLE_DISPLAY_TO_DB,
)
