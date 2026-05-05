"""Reusable version history panel for CDD, Blueprint, and Style entities.

Public API
----------
render_version_panel(versions, ...)
    Shows the full version history table with Restore and Compare controls.

render_version_compare(content_a, label_a, content_b, label_b)
    Side-by-side diff view.
"""

from __future__ import annotations

import difflib

import streamlit as st


# ── Normalised version dataclass ──────────────────────────────────────────────

class VersionInfo:
    """Thin wrapper that normalises the different version models into one shape."""

    def __init__(
        self,
        *,
        id,
        label: str,           # "v1", "v2" or just the version string
        number: int,          # integer ordering
        is_active: bool,
        content: str,         # the main displayable text (full_content / understanding_content)
        change_summary: str,
        author: str,
        created_at,
    ):
        self.id            = id
        self.label         = label
        self.number        = number
        self.is_active     = is_active
        self.content       = content or ""
        self.change_summary = change_summary or ""
        self.author        = author or "—"
        self.created_at    = created_at

    @property
    def date_str(self) -> str:
        if self.created_at:
            return self.created_at.strftime("%Y-%m-%d %H:%M")
        return "—"


# ── Adapters ──────────────────────────────────────────────────────────────────

def from_blueprint_version(v) -> VersionInfo:
    return VersionInfo(
        id            = v.id,
        label         = v.version,
        number        = v.version_number or 0,
        is_active     = bool(v.is_active),
        content       = v.full_content or "",
        change_summary = v.change_reason or "",
        author        = v.created_by or "—",
        created_at    = v.created_at,
    )


def from_cdd_version(v) -> VersionInfo:
    return VersionInfo(
        id            = v.id,
        label         = v.version,
        number        = v.version_number or 0,
        is_active     = bool(v.is_active),
        content       = v.full_content or "",
        change_summary = v.change_reason or "",
        author        = v.created_by or "—",
        created_at    = v.created_at,
    )


def from_style_version(v) -> VersionInfo:
    return VersionInfo(
        id            = v.id,
        label         = f"v{v.version_number}",
        number        = v.version_number or 0,
        is_active     = bool(v.is_active),
        content       = v.understanding_content or "",
        change_summary = v.change_summary or "",
        author        = v.created_by or "—",
        created_at    = v.created_at,
    )


# ── Diff helper ───────────────────────────────────────────────────────────────

def _unified_diff(text_a: str, text_b: str, label_a: str, label_b: str) -> str:
    lines_a = text_a.splitlines(keepends=True)
    lines_b = text_b.splitlines(keepends=True)
    diff = list(difflib.unified_diff(lines_a, lines_b, fromfile=label_a, tofile=label_b))
    return "".join(diff) if diff else "No differences found."


# ── Compare view ──────────────────────────────────────────────────────────────

def render_version_compare(
    content_a: str,
    label_a: str,
    content_b: str,
    label_b: str,
) -> None:
    """Side-by-side view with unified diff toggle."""
    _tab_side, _tab_diff = st.tabs(["Side-by-Side", "Unified Diff"])

    with _tab_side:
        _c1, _c2 = st.columns(2)
        _c1.markdown(f"**{label_a}**")
        _c1.text_area("", value=content_a[:3000], height=350, disabled=True,
                      key=f"cmp_a_{hash(label_a)}")
        _c2.markdown(f"**{label_b}**")
        _c2.text_area("", value=content_b[:3000], height=350, disabled=True,
                      key=f"cmp_b_{hash(label_b)}")

    with _tab_diff:
        diff_text = _unified_diff(content_a, content_b, label_a, label_b)
        st.code(diff_text[:5000], language="diff")


# ── Main version panel ────────────────────────────────────────────────────────

def render_version_panel(
    versions_raw: list,
    *,
    adapter,                  # one of from_blueprint_version / from_cdd_version / from_style_version
    entity_label: str,        # "CDD", "Blueprint", "Style Understanding"
    on_restore,               # callable(version_id) → (success, msg) | None
    can_restore: bool = True,
    page_key: str,
    active_content: str = "", # content of the currently active version (for compare baseline)
) -> None:
    """Render a version history panel with preview, restore, and compare controls."""

    if not versions_raw:
        st.info(f"No version history yet for this {entity_label}.")
        return

    versions: list[VersionInfo] = [adapter(v) for v in versions_raw]
    active_ver = next((v for v in versions if v.is_active), None)

    # ── Metrics bar ─────────────────────────────────────────────────────────
    _mc1, _mc2, _mc3 = st.columns(3)
    _mc1.metric("Total Versions", len(versions))
    _mc2.metric("Active", active_ver.label if active_ver else "—")
    _mc3.metric("Last Updated", versions[0].date_str if versions else "—")

    # ── Version history table ────────────────────────────────────────────────
    _rows = [
        {
            "Version":  v.label,
            "Active":   "✅ Active" if v.is_active else "—",
            "By":       v.author,
            "Date":     v.date_str,
            "Change Summary": (v.change_summary or "—")[:60],
        }
        for v in versions
    ]
    st.dataframe(_rows, hide_index=True, use_container_width=True)

    # ── Per-version actions ──────────────────────────────────────────────────
    _non_active = [v for v in versions if not v.is_active]
    if not _non_active:
        st.caption("This is the only version — nothing to restore or compare.")
        return

    _sel_label  = st.selectbox(
        "Select version to inspect",
        options=[v.label for v in _non_active],
        key=f"{page_key}_sel_ver",
    )
    _sel_ver = next((v for v in _non_active if v.label == _sel_label), None)

    if not _sel_ver:
        return

    _act1, _act2 = st.columns(2)

    # Preview selected version
    if _act1.button("👁️ Preview", key=f"{page_key}_preview", use_container_width=True):
        st.session_state[f"{page_key}_show_preview"] = not st.session_state.get(f"{page_key}_show_preview", False)

    # Compare with active
    if _act2.button("⚖️ Compare with Active", key=f"{page_key}_compare", use_container_width=True):
        st.session_state[f"{page_key}_show_compare"] = not st.session_state.get(f"{page_key}_show_compare", False)

    # Restore
    if can_restore and on_restore:
        if st.button(
            f"⏪ Restore {_sel_label}",
            key=f"{page_key}_restore",
            type="primary",
            use_container_width=True,
        ):
            ok, msg = on_restore(_sel_ver.id)
            if ok:
                st.success(f"✅ {entity_label} restored from {_sel_label}.")
                st.rerun()
            else:
                st.error(msg or "Restore failed.")

    # Preview pane
    if st.session_state.get(f"{page_key}_show_preview"):
        with st.expander(f"📖 Preview — {_sel_label}", expanded=True):
            st.text_area("Content (first 3 000 chars)",
                         value=_sel_ver.content[:3000],
                         height=300, disabled=True,
                         key=f"{page_key}_preview_text")

    # Compare pane
    if st.session_state.get(f"{page_key}_show_compare"):
        with st.expander(f"⚖️ Compare: Active vs {_sel_label}", expanded=True):
            _active_content = active_content or (active_ver.content if active_ver else "")
            _active_label   = f"Active ({active_ver.label})" if active_ver else "Active"
            render_version_compare(
                _active_content, _active_label,
                _sel_ver.content, _sel_label,
            )
