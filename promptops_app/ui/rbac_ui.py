"""RBAC UI helpers — role pills, access-denied banners, and the permission matrix viewer.

Import and call from any page or the Analytics tab.

Public API
----------
render_role_pill(role)
    Inline coloured pill showing the user's role.

render_access_denied(action_label, user_role)
    Consistent error card for blocked actions; no raw permission keys shown.

render_permission_matrix()
    Interactive, filterable full-matrix table for admin / lead review.

render_my_permissions(role)
    Compact card listing what the current user can do.
"""

from __future__ import annotations

import streamlit as st

from promptops_app.auth.permission_matrix import PERMISSION_CATALOG, get_matrix_rows
from promptops_app.auth.permissions import (
    ROLE_DISPLAY,
    get_permissions_for_role,
    rbac_check,
    role_label,
)

# ── Role palette ──────────────────────────────────────────────────────────────
_ROLE_STYLE: dict[str, tuple[str, str]] = {
    # (background, text)
    "admin":    ("#ef4444", "#ffffff"),
    "reviewer": ("#6366f1", "#ffffff"),
    "author":   ("#10b981", "#ffffff"),
}


# ── Role pill ─────────────────────────────────────────────────────────────────

def render_role_pill(role: str) -> None:
    """Render a small inline badge showing the user's display role."""
    bg, fg = _ROLE_STYLE.get(role, ("#6b7280", "#ffffff"))
    label  = role_label(role)
    st.markdown(
        f"<span style='background:{bg};color:{fg};font-size:0.7rem;font-weight:700;"
        f"padding:2px 10px;border-radius:12px;letter-spacing:.05em;'>{label}</span>",
        unsafe_allow_html=True,
    )


# ── Access-denied banner ──────────────────────────────────────────────────────

def render_access_denied(action_label: str, user_role: str) -> None:
    """Display a consistent, user-friendly access-denied card.

    Does NOT expose the internal permission key or any technical detail.
    """
    disp = role_label(user_role)
    st.markdown(
        f"<div style='background:#fef2f2;border:1.5px solid #fca5a5;"
        f"border-radius:10px;padding:14px 18px;display:flex;gap:12px;align-items:flex-start;'>"
        f"<span style='font-size:1.4rem;'>🔒</span>"
        f"<div>"
        f"<div style='font-weight:700;color:#991b1b;margin-bottom:4px;'>Access Denied</div>"
        f"<div style='font-size:0.84rem;color:#7f1d1d;'>"
        f"<strong>{action_label}</strong> is not available for the "
        f"<strong>{disp}</strong> role. "
        f"Contact an Admin if you believe this is incorrect.</div>"
        f"</div></div>",
        unsafe_allow_html=True,
    )


# ── My permissions card ───────────────────────────────────────────────────────

def render_my_permissions(role: str) -> None:
    """Show a compact card listing the categories accessible to this role."""
    disp = role_label(role)
    bg, fg = _ROLE_STYLE.get(role, ("#6b7280", "#ffffff"))

    # Header
    st.markdown(
        f"<div style='background:{bg}18;border:1.5px solid {bg}55;"
        f"border-radius:10px;padding:12px 16px;margin-bottom:8px;'>"
        f"<span style='font-size:0.7rem;font-weight:700;text-transform:uppercase;"
        f"letter-spacing:.08em;color:{bg};'>Your role: {disp}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

    # Category access grid
    _cols = st.columns(2)
    for i, section in enumerate(PERMISSION_CATALOG):
        _any = any(rbac_check(role, p["key"]) for p in section["permissions"])
        _allowed_labels = [
            p["label"] for p in section["permissions"] if rbac_check(role, p["key"])
        ]
        _col = _cols[i % 2]
        _col.markdown(
            f"<div style='margin-bottom:8px;'>"
            f"<div style='font-size:0.8rem;font-weight:700;color:{'#111827' if _any else '#9ca3af'};'>"
            f"{section['icon']} {section['category']}"
            + (" <span style='color:#10b981;font-size:0.65rem;'>✓</span>" if _any else
               " <span style='color:#d1d5db;font-size:0.65rem;'>—</span>")
            + "</div>"
            + (
                "".join(
                    f"<div style='font-size:0.72rem;color:#6b7280;padding-left:12px;'>• {lbl}</div>"
                    for lbl in _allowed_labels
                )
                if _any else
                "<div style='font-size:0.72rem;color:#d1d5db;padding-left:12px;font-style:italic;'>No access</div>"
            )
            + "</div>",
            unsafe_allow_html=True,
        )


# ── Full permission matrix ────────────────────────────────────────────────────

def render_permission_matrix(filter_role: str = None) -> None:
    """Render the full permission matrix as an interactive Streamlit dataframe.

    Parameters
    ----------
    filter_role:
        Optional DB role value ("admin" | "reviewer" | "author").
        When supplied, only rows where that role has access are shown.
    """
    st.markdown(
        "<div style='font-size:0.75rem;color:#6b7280;margin-bottom:8px;'>"
        "✅ = allowed &nbsp;|&nbsp; — = not allowed &nbsp;|&nbsp; "
        "Scope: <em>global</em> = across all projects, "
        "<em>project</em> = within assigned project, "
        "<em>course</em> = within assigned course</div>",
        unsafe_allow_html=True,
    )

    _filt_c1, _filt_c2 = st.columns([1, 3])
    _cat_options = ["All categories"] + sorted({s["category"] for s in PERMISSION_CATALOG})
    _cat_sel     = _filt_c1.selectbox("Category", _cat_options, key="rbac_matrix_cat")
    _role_opts   = {"All roles": None, "Admin": "admin", "Lead": "reviewer", "ID": "author"}
    _role_sel    = _filt_c2.selectbox(
        "Filter by role",
        list(_role_opts.keys()),
        index=0 if not filter_role else ["All roles", "Admin", "Lead", "ID"][
            ["admin", "reviewer", "author"].index(filter_role) + 1
        ],
        key="rbac_matrix_role",
    )
    _sel_role_db = _role_opts[_role_sel]

    rows = get_matrix_rows()

    if _cat_sel != "All categories":
        rows = [r for r in rows if r["Category"] == _cat_sel]
    if _sel_role_db:
        rows = [r for r in rows if r[role_label(_sel_role_db)] == "✅"]

    if not rows:
        st.info("No permissions match the selected filters.")
        return

    st.dataframe(
        rows,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Category":   st.column_config.TextColumn("Category",   width="medium"),
            "Permission": st.column_config.TextColumn("Permission", width="medium"),
            "Description":st.column_config.TextColumn("Description",width="large"),
            "Scope":      st.column_config.TextColumn("Scope",      width="small"),
            "Admin":      st.column_config.TextColumn("Admin",      width="small"),
            "Lead":       st.column_config.TextColumn("Lead",       width="small"),
            "ID":         st.column_config.TextColumn("ID",         width="small"),
        },
    )
    st.caption(f"{len(rows)} permission(s) shown.")
