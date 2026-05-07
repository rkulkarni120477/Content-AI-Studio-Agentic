"""Component-Level User Prompt History Panel.

Full-featured save / load / edit / version / AI-improve / download panel for
the "Additional Instructions" free-text field in Style, CDD, Blueprint, and
Generate pages.

Usage
-----
Place the panel call OUTSIDE the generation form::

    extra = render_user_prompt_panel(
        db, component="cdd",
        project_id=_proj_id, cluster_id=None, course_id=_crs_id,
        user_name=user_name, model_choice=model_choice,
    )

``extra`` is the current instruction text (same as
``st.session_state.get("_uph_text_cdd", "")``).  Read either after the
form submit — Streamlit persists non-form widget state across reruns.

After successful generation call::

    auto_save_instructions(db, "cdd", extra, name=course_title[:80], ...)
"""

from __future__ import annotations

from typing import Optional

import streamlit as st

from promptops_app.repositories.user_prompt_history_repository import (
    get_versions_for_name,
    list_latest_for_component,
    save_prompt,
    soft_delete,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _text_key(component: str, suffix: str = "") -> str:
    return f"_uph_text_{component}{suffix}"


def _ai_key(component: str, suffix: str = "") -> str:
    return f"_uph_ai_{component}{suffix}"


def _sel_key(component: str, suffix: str = "") -> str:
    return f"_uph_sel_{component}{suffix}"


def _improve_with_llm(
    current: str, instructions: str, component: str, model_choice: str
) -> str:
    """Call the active LLM to improve user instructions. Returns improved text."""
    try:
        from promptops_app.services.llm_service import generate_text as _call

        _sys = (
            "You are an expert instructional designer. Improve the user's additional "
            "generation instructions based on their feedback. "
            "Return ONLY the improved instructions text — no preamble, no explanation."
        )
        _usr = (
            f"Component: {component.upper()}\n\n"
            f"Current instructions:\n{current}\n\n"
            f"Improvement request:\n{instructions}\n\n"
            "Return only the improved instructions."
        )
        result = _call(model_choice, _sys, _usr)
        if isinstance(result, str) and result.startswith("ERROR"):
            return current
        return result.strip()
    except Exception:
        return current


# ---------------------------------------------------------------------------
# Main panel
# ---------------------------------------------------------------------------

def render_user_prompt_panel(
    db,
    component: str,
    project_id: Optional[int],
    cluster_id: Optional[int],
    course_id: Optional[int],
    user_name: str,
    model_choice: str,
    placeholder: str = "e.g. Focus on clinical simulations. Add DEI examples. Emphasise Bloom's levels 4–6.",
    height: int = 110,
    key_suffix: str = "",
) -> str:
    """Render the full Additional Instructions panel and return current text.

    Parameters
    ----------
    component:
        One of ``"style"``, ``"cdd"``, ``"blueprint"``, ``"generate"``.
    key_suffix:
        Optional suffix added to all widget keys — use when the same component
        panel is rendered multiple times on one page (e.g. per-style rows).
    """
    _TK  = _text_key(component, key_suffix)
    _AIK = _ai_key(component, key_suffix)
    _SK  = _sel_key(component, key_suffix)

    if _TK not in st.session_state:
        st.session_state[_TK] = ""

    saved = list_latest_for_component(
        db, component=component, project_id=project_id, course_id=course_id
    )

    with st.expander("💬 Additional Instructions", expanded=False):

        if saved:
            _t_write, _t_saved, _t_history, _t_ai = st.tabs(
                ["✏️ Write", "📂 Saved", "📋 History", "🤖 Improve with AI"]
            )
        else:
            _t_write, _t_ai = st.tabs(["✏️ Write", "🤖 Improve with AI"])
            _t_saved = _t_history = None  # type: ignore[assignment]

        # ── Write tab ────────────────────────────────────────────────────
        with _t_write:
            _current: str = st.text_area(
                "Instructions",
                height=height,
                placeholder=placeholder,
                label_visibility="collapsed",
                key=_TK,
            )

            if _current.strip():
                st.caption("**Save for reuse:**")
                _sc1, _sc2, _sc3 = st.columns([0.44, 0.30, 0.26])
                _save_name: str = _sc1.text_input(
                    "Name",
                    placeholder="e.g. Clinical simulation focus",
                    label_visibility="collapsed",
                    key=f"_uph_sname_{component}{key_suffix}",
                )
                _as_new: bool = _sc2.checkbox(
                    "New version",
                    key=f"_uph_newver_{component}{key_suffix}",
                    help="Tick to keep the previous save and create a new version.",
                )
                if _sc3.button(
                    "💾 Save",
                    key=f"_uph_save_{component}{key_suffix}",
                    use_container_width=True,
                ):
                    if _save_name.strip():
                        save_prompt(
                            db,
                            name=_save_name.strip(),
                            component=component,
                            content=_current.strip(),
                            project_id=project_id,
                            cluster_id=cluster_id,
                            course_id=course_id,
                            created_by=user_name,
                            as_new_version=_as_new,
                        )
                        st.success(f"✅ Saved as **'{_save_name.strip()}'**")
                        st.rerun()
                    else:
                        st.warning("Enter a name to save.")

                st.download_button(
                    "⬇️ Download Instructions (.txt)",
                    data=_current,
                    file_name=f"{component}_instructions.txt",
                    mime="text/plain",
                    use_container_width=True,
                    key=f"_uph_dl_{component}{key_suffix}",
                )

        # ── Saved tab ────────────────────────────────────────────────────
        if _t_saved is not None:
            with _t_saved:
                if not saved:
                    st.info("No saved instructions yet.")
                else:
                    opt_labels = [r.name for r in saved]
                    label_to_rec = {r.name: r for r in saved}

                    sel_label: str = st.selectbox(
                        "Saved Instructions",
                        opt_labels,
                        key=_SK,
                        label_visibility="collapsed",
                    )
                    sel_rec = label_to_rec.get(sel_label)

                    if sel_rec:
                        _meta: list[str] = [f"v{sel_rec.version_number}"]
                        if sel_rec.updated_at:
                            try:
                                _meta.append(sel_rec.updated_at.strftime("%Y-%m-%d"))
                            except Exception:
                                pass
                        if sel_rec.created_by:
                            _meta.append(f"by {sel_rec.created_by}")
                        st.caption("  ·  ".join(_meta))

                        st.text_area(
                            "Content preview",
                            value=sel_rec.content,
                            height=80,
                            disabled=True,
                            key=f"_uph_preview_{component}{key_suffix}",
                            label_visibility="collapsed",
                        )

                        _lc1, _lc2, _lc3 = st.columns(3)
                        if _lc1.button(
                            "📥 Load",
                            key=f"_uph_load_{component}{key_suffix}",
                            use_container_width=True,
                            help="Load into the Write tab.",
                        ):
                            st.session_state[_TK] = sel_rec.content
                            st.rerun()

                        if _lc2.button(
                            "✏️ Edit",
                            key=f"_uph_edit_{component}{key_suffix}",
                            use_container_width=True,
                            help="Load into Write tab for editing.",
                        ):
                            st.session_state[_TK] = sel_rec.content
                            st.rerun()

                        if _lc3.button(
                            "🗑️ Delete",
                            key=f"_uph_del_{component}{key_suffix}",
                            use_container_width=True,
                        ):
                            soft_delete(
                                db,
                                name=sel_rec.name,
                                component=component,
                                project_id=project_id,
                                course_id=course_id,
                            )
                            st.toast(f"🗑️ '{sel_rec.name}' deleted.")
                            st.rerun()

        # ── History tab ───────────────────────────────────────────────────
        if _t_history is not None:
            with _t_history:
                _sel_name = st.session_state.get(_SK)
                if not _sel_name:
                    st.info("Select a saved prompt in the Saved tab to view its history.")
                else:
                    versions = get_versions_for_name(
                        db,
                        _sel_name,
                        component,
                        project_id=project_id,
                        course_id=course_id,
                    )
                    if not versions:
                        st.info("No version history for this prompt.")
                    else:
                        for v in versions:
                            _vat = "—"
                            try:
                                if v.updated_at:
                                    _vat = v.updated_at.strftime("%Y-%m-%d %H:%M")
                            except Exception:
                                pass
                            with st.expander(
                                f"v{v.version_number}  ·  {_vat}  ·  {v.created_by or '—'}",
                                expanded=(v == versions[0]),
                            ):
                                st.text_area(
                                    "Content",
                                    value=v.content,
                                    height=80,
                                    disabled=True,
                                    key=f"_uph_hist_{component}{key_suffix}_{v.id}",
                                    label_visibility="collapsed",
                                )
                                if st.button(
                                    "📥 Load this version",
                                    key=f"_uph_hload_{component}{key_suffix}_{v.id}",
                                    use_container_width=True,
                                ):
                                    st.session_state[_TK] = v.content
                                    st.rerun()

        # ── Improve with AI tab ───────────────────────────────────────────
        with _t_ai:
            _cur_text = st.session_state.get(_TK, "")
            if not _cur_text.strip():
                st.info(
                    "Write some instructions in the **✏️ Write** tab first, "
                    "then improve them here."
                )
            else:
                st.caption(
                    "Describe what to change — the AI will rewrite your instructions."
                )
                _ai_instr: str = st.text_area(
                    "Improvement request",
                    placeholder=(
                        "e.g. Make it more concise.  "
                        "Add a DEI focus.  Remove academic jargon."
                    ),
                    height=70,
                    label_visibility="collapsed",
                    key=f"_uph_ai_instr_{component}{key_suffix}",
                )

                _ai_c1, _ai_c2 = st.columns(2)
                if _ai_c1.button(
                    "✨ Improve with AI",
                    key=f"_uph_ai_btn_{component}{key_suffix}",
                    use_container_width=True,
                ):
                    if _ai_instr.strip():
                        with st.spinner("Improving with AI…"):
                            _improved = _improve_with_llm(
                                _cur_text, _ai_instr, component, model_choice
                            )
                        st.session_state[_AIK] = _improved
                        st.rerun()
                    else:
                        st.warning("Describe what you want to improve first.")

                if _ai_c2.button(
                    "🗑️ Clear",
                    key=f"_uph_ai_clear_{component}{key_suffix}",
                    use_container_width=True,
                ):
                    st.session_state.pop(_AIK, None)
                    st.rerun()

                _sugg = st.session_state.get(_AIK, "")
                if _sugg:
                    st.caption("**AI suggestion — review before applying:**")
                    st.text_area(
                        "Suggestion",
                        value=_sugg,
                        height=90,
                        disabled=True,
                        key=f"_uph_ai_sugg_{component}{key_suffix}",
                        label_visibility="collapsed",
                    )
                    _ap1, _ap2 = st.columns(2)
                    if _ap1.button(
                        "✅ Apply",
                        key=f"_uph_ai_apply_{component}{key_suffix}",
                        use_container_width=True,
                        type="primary",
                    ):
                        st.session_state[_TK] = _sugg
                        st.session_state.pop(_AIK, None)
                        st.rerun()
                    if _ap2.button(
                        "🗑️ Discard",
                        key=f"_uph_ai_discard_{component}{key_suffix}",
                        use_container_width=True,
                    ):
                        st.session_state.pop(_AIK, None)
                        st.rerun()

    return st.session_state.get(_TK, "")


# ---------------------------------------------------------------------------
# Auto-save helper (called after successful generation)
# ---------------------------------------------------------------------------

def auto_save_instructions(
    db,
    component: str,
    content: str,
    name: str,
    project_id: Optional[int],
    cluster_id: Optional[int],
    course_id: Optional[int],
    user_name: str,
) -> None:
    """Silently save non-empty instructions after generation (upsert by name).

    Uses ``as_new_version=False`` so the same course/module name always updates
    the single record rather than accumulating duplicate rows.
    Never raises — never blocks generation.
    """
    if not content or not content.strip():
        return
    _name = (name or "").strip()[:255] or "Saved Instructions"
    try:
        save_prompt(
            db,
            name=_name,
            component=component,
            content=content.strip(),
            project_id=project_id,
            cluster_id=cluster_id,
            course_id=course_id,
            created_by=user_name,
            as_new_version=False,
        )
    except Exception:
        pass
