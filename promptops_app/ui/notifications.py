"""Notification system — toast + deferred banner helpers.

Extracted from core/shared.py (Phase 3 refactoring).

Usage:
    from promptops_app.ui.notifications import notify, notify_deferred, notify_check
"""

import streamlit as st

_ICONS = {"success": "✅", "error": "❌", "warning": "⚠️", "info": "ℹ️"}


def notify(message: str, level: str = "success") -> None:
    """Show a Streamlit toast notification that auto-disappears after ~2 seconds."""
    icon = _ICONS.get(level, "ℹ️")
    st.toast(f"{icon} {message}")


def notify_deferred(key: str, message: str, level: str = "success") -> None:
    """Store a notification to be displayed on the NEXT render cycle.

    Use this before st.rerun() so the toast survives the rerun.
    """
    st.session_state[f"_notify_{key}"] = {"message": message, "level": level}


def notify_check() -> None:
    """Flush all deferred notifications. Call once at the top of each page render."""
    keys = [k for k in st.session_state if k.startswith("_notify_")]
    for k in keys:
        payload = st.session_state.pop(k)
        notify(payload["message"], payload["level"])
