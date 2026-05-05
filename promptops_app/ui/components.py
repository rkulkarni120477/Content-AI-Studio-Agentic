"""Reusable Streamlit UI components.

Extracted from core/shared.py (Phase 1 refactoring).

Usage:
    from promptops_app.ui.components import status_badge, _section_badge, inject_premium_style
"""

import os
import base64
from pathlib import Path

import streamlit as st

# Two levels up: promptops_app/ui/ → promptops_app/ → project root
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


@st.cache_resource(show_spinner=False)
def _load_css() -> str:
    """Read style.css once and cache it for the lifetime of the server."""
    css_path = str(_PROJECT_ROOT / ".streamlit" / "style.css")
    try:
        with open(css_path, "r", encoding="utf-8") as _f:
            return _f.read()
    except FileNotFoundError:
        return ""
    except Exception:
        return ""


@st.cache_resource(show_spinner=False)
def _logo_b64() -> str:
    """Read and base64-encode the logo once; cached for the lifetime of the server."""
    logo_path = str(_PROJECT_ROOT / "ContentStudio.png")

    if not os.path.exists(logo_path):
        return ""

    with open(logo_path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def inject_premium_style(theme: str = "light") -> None:
    """Inject the PromptOps design-system CSS (CSS string is cached in memory)."""
    css = _load_css()
    if css:
        st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def status_badge(state: str) -> str:
    """Return a styled HTML badge for the current workflow state."""
    _palette = {
        "draft":              ("#6b7280", "#f9fafb"),
        "in_review":          ("#b45309", "#fffbeb"),
        "changes_requested":  ("#9a3412", "#fff7ed"),
        "approved":           ("#059669", "#ecfdf5"),
        "published":          ("#4f46e5", "#eef2ff"),
        "rejected":           ("#dc2626", "#fef2f2"),
        "archived":           ("#4b5563", "#f3f4f6"),
    }
    fg, bg = _palette.get(state.lower(), ("#6b7280", "#f9fafb"))
    style = (
        f"background:{bg};color:{fg};border:1.5px solid {fg}44;"
        f"padding:2px 9px;border-radius:5px;font-size:0.72rem;"
        f"font-weight:700;text-transform:uppercase;letter-spacing:0.06em;"
    )
    return f"<span style='{style}'>{state}</span>"


def fill_template(template: str, payload: dict) -> str:
    """Replace {key} placeholders in a template string."""
    out = template
    for key, value in payload.items():
        out = out.replace("{" + key + "}", str(value or ""))
    return out


def _req(label: str) -> str:
    """Return a label with a red asterisk for required fields."""
    return f'{label} <span style="color:#ef4444;font-weight:700;">*</span>'


@st.cache_resource(show_spinner=False)
def _logo_b64() -> str:
    """Read and base64-encode the logo once; cached for the lifetime of the server."""
    logo_path = str(_PROJECT_ROOT / "ContentStudio.png")
    with open(logo_path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def _section_badge(icon: str, title: str, subtitle: str = "") -> str:
    """Return an HTML section header card."""
    sub = (
        f"<div style='font-size:0.78rem;color:#6b7280;margin-top:2px;font-weight:400;'>{subtitle}</div>"
        if subtitle
        else ""
    )
    return (
        f"<div style='background:linear-gradient(135deg,#eef2ff 0%,#f5f3ff 100%);"
        f"border:1px solid #c7d2fe;border-left:4px solid #6366f1;"
        f"border-radius:0 10px 10px 0;padding:0.6rem 1rem;margin-bottom:1rem;'>"
        f"<span style='font-size:1rem;font-weight:700;color:#3730a3;'>{icon} {title}</span>{sub}</div>"
    )


def _info_card(content: str, color: str = "#6366f1") -> str:
    """Return a styled info card."""
    bg = color + "0f"  # ~6% opacity tint
    return (
        f"<div style='background:{bg};border:1px solid {color}33;"
        f"border-radius:8px;padding:0.7rem 1rem;font-size:0.84rem;"
        f"color:{color};line-height:1.5;'>{content}</div>"
    )
