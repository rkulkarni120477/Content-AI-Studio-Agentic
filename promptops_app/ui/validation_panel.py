"""Validation panel — Streamlit UI component.

Renders the structured dict produced by validation_service.validate_blocks().
Exported functions:
  render(result, *, compact, show_confirm_checkbox, key_prefix)
  export_allowed(result, *, key_prefix) -> bool
"""

from __future__ import annotations

import streamlit as st


# ── Helpers ───────────────────────────────────────────────────────────────────

def _badge(text: str, bg: str, fg: str, px: int = 8) -> str:
    return (
        f"<span style='background:{bg};color:{fg};font-size:0.72rem;"
        f"padding:2px {px}px;border-radius:10px;font-weight:700;"
        f"white-space:nowrap;'>{text}</span>"
    )


def _card(col, label: str, value: int, accent: str, icon: str) -> None:
    col.markdown(
        f"<div style='border:1px solid {accent}33;border-radius:10px;"
        f"padding:12px 10px;text-align:center;'>"
        f"<div style='font-size:1.5rem;font-weight:800;color:{accent};'>"
        f"{icon} {value}</div>"
        f"<div style='font-size:0.7rem;color:#94a3b8;margin-top:2px;'>{label}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )


def _issue_row(issue: dict) -> None:
    is_err     = issue["severity"] == "error"
    border_clr = "#ef4444" if is_err else "#f59e0b"
    bg_clr     = "#fef2f2" if is_err else "#fffbeb"
    text_clr   = "#991b1b" if is_err else "#92400e"
    icon       = "❌" if is_err else "⚠️"
    st.markdown(
        f"<div style='background:{bg_clr};border-left:3px solid {border_clr};"
        f"border-radius:0 6px 6px 0;padding:8px 12px;margin:4px 0;'>"
        f"<div style='font-size:0.8rem;font-weight:600;color:{text_clr};'>"
        f"{icon} {issue['message']}</div>"
        f"<div style='font-size:0.7rem;color:#6b7280;margin-top:2px;'>"
        f"📍 <em>{issue['location']}</em></div>"
        f"</div>",
        unsafe_allow_html=True,
    )


# ── Public API ────────────────────────────────────────────────────────────────

def render(
    result: dict,
    *,
    compact: bool = False,
) -> None:
    """Render validation result produced by validate_blocks().

    Parameters
    ----------
    result:
        Dict from validation_service.validate_blocks().
    compact:
        True → single-line inline badge (for per-generation export row).
        False → full panel with metric cards, banners, and issue lists.
    """
    summary  = result["summary"]
    errors   = result["errors"]
    warnings = result["warnings"]
    n_err    = summary["errors"]
    n_warn   = summary["warnings"]
    n_pass   = summary["passed"]
    total    = summary["total_checks"]

    # ── Compact (inline badge) ────────────────────────────────────────────────
    if compact:
        if n_err:
            st.markdown(
                _badge(f"❌ {n_err} error(s)", "#fef2f2", "#991b1b"),
                unsafe_allow_html=True,
            )
        elif n_warn:
            st.markdown(
                _badge(f"⚠️ {n_warn} warning(s)", "#fffbeb", "#92400e"),
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                _badge("✅ Passed", "#f0fdf4", "#166534"),
                unsafe_allow_html=True,
            )
        return

    # ── Full panel ────────────────────────────────────────────────────────────
    st.markdown("##### 🔍 Validation Results")

    c1, c2, c3, c4 = st.columns(4)
    _card(c1, "Total Checks", total,  "#6366f1", "🔢")
    _card(c2, "Passed",       n_pass, "#10b981", "✅")
    _card(c3, "Warnings",     n_warn, "#f59e0b", "⚠️")
    _card(c4, "Errors",       n_err,  "#ef4444", "❌")

    st.markdown("<div style='margin-top:10px;'></div>", unsafe_allow_html=True)

    # Overall banner
    if n_err == 0 and n_warn == 0:
        st.markdown(
            "<div style='background:#f0fdf4;border:2px solid #86efac;"
            "border-radius:10px;padding:12px 16px;font-weight:700;color:#15803d;'>"
            "✅ All checks passed — content is ready for export!</div>",
            unsafe_allow_html=True,
        )
    elif n_err == 0:
        st.markdown(
            f"<div style='background:#fffbeb;border:2px solid #fde68a;"
            f"border-radius:10px;padding:12px 16px;font-weight:700;color:#92400e;'>"
            f"⚠️ {n_warn} warning(s) found — review before exporting.</div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f"<div style='background:#fef2f2;border:2px solid #fca5a5;"
            f"border-radius:10px;padding:12px 16px;font-weight:700;color:#991b1b;'>"
            f"❌ {n_err} critical error(s) must be fixed before exporting.</div>",
            unsafe_allow_html=True,
        )

    # Error list
    if errors:
        with st.expander(f"❌ Errors ({n_err}) — must fix before export", expanded=True):
            for issue in errors:
                _issue_row(issue)

    # Warning list
    if warnings:
        with st.expander(
            f"⚠️ Warnings ({n_warn}) — review recommended",
            expanded=(n_err == 0),
        ):
            for issue in warnings:
                _issue_row(issue)


def export_allowed(result: dict, *, key_prefix: str = "val") -> bool:
    """Render the export gate and return True when the user may proceed.

    Call this AFTER render().  Returns True when:
    - No errors AND no warnings, OR
    - No errors AND user ticked the warnings confirmation checkbox.

    Returns False (and renders a blocking message) when errors exist.
    """
    n_err  = result["summary"]["errors"]
    n_warn = result["summary"]["warnings"]

    if n_err:
        st.error(
            f"❌ **Export blocked** — fix {n_err} critical error(s) first. "
            "Edit the blocks above, then re-run validation."
        )
        return False

    if n_warn:
        confirmed = st.checkbox(
            f"⚠️ I've reviewed the {n_warn} warning(s) and want to export anyway.",
            key=f"{key_prefix}_warn_confirm",
        )
        return confirmed

    return True  # clean pass
