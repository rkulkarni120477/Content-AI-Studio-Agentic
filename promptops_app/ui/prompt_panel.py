"""Reusable Prompt Management Panel.

Embeds directly into the Style, CDD, Blueprint, and Generate tabs so users
can select, view, edit, version, create, improve, and download prompt assets
without leaving their current workflow.

Design
------
* Each component (style, cdd, blueprint, generate) has exactly one seeded
  **default** asset (is_default=True).  The panel auto-selects it on first
  open so users see the actual live prompt, not a "use built-in" placeholder.
* Edits always create a **new version** — the original v1 content is never
  overwritten, guaranteeing a complete audit trail.
* The panel returns ``(system_prompt, user_prompt_template)`` always — the
  caller uses these for generation.  Falls back to the caller-supplied
  *default_system* / *default_user* strings only when the DB has no assets at
  all (should not happen after seeding).

Usage
-----
    from promptops_app.ui.prompt_panel import render_prompt_panel, safe_format

    _sys, _usr = render_prompt_panel(
        db,
        component="cdd",
        user_name=user_name,
        model_choice=model_choice,
        default_system=CDD_SYSTEM_PROMPT,   # inline fallback (rarely used)
        default_user=CDD_USER_PROMPT_TEMPLATE,
    )
    # _sys / _usr are always populated strings — use directly in call_llm().
"""

from __future__ import annotations

import json
from datetime import datetime

import streamlit as st

from promptops_app.database import Prompt, PromptVersion
from promptops_app.repositories import prompt_repository
from promptops_app.services.evaluation_service import generate_prompt_template_with_llm


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _SafeDict(dict):
    """dict subclass that returns '' for any missing key."""
    def __missing__(self, key: str) -> str:
        return ""


def safe_format(template: str, **kwargs: str) -> str:
    """Format *template* with **kwargs**, silently skipping unknown placeholders."""
    try:
        return template.format_map(_SafeDict(**kwargs))
    except Exception:
        return template


# ---------------------------------------------------------------------------
# Dropdown label helpers
# ---------------------------------------------------------------------------

_COMPONENT_LABELS = {
    "style":     "Style",
    "cdd":       "CDD",
    "blueprint": "Blueprint",
    "generate":  "Generate",
}


def _display_label(p: Prompt) -> str:
    """Return the dropdown display string for *p*."""
    if p.is_default:
        comp = _COMPONENT_LABELS.get(p.component_type or "", p.component_type or "")
        return f"🏷️ Default {comp} Prompt"
    return p.name


# ---------------------------------------------------------------------------
# Main panel
# ---------------------------------------------------------------------------

def render_prompt_panel(
    db,
    component: str,
    user_name: str,
    model_choice: str,
    default_system: str = "",
    default_user: str = "",
) -> tuple[str, str]:
    """Render a collapsible **🔧 Prompt Settings** expander and return prompts.

    Parameters
    ----------
    component:
        One of ``"style"``, ``"cdd"``, ``"blueprint"``, ``"generate"``.
    default_system / default_user:
        Hard-coded fallback strings used only if the DB has zero assets for
        this component.  After seeding this path is never taken.

    Returns
    -------
    ``(system_prompt, user_prompt_template)`` — always populated.
    """
    _SK     = f"_pp_sel_{component}"    # session key: selected prompt name
    _AI_SK  = f"_pp_ai_sugg_{component}"

    # ── Build ordered prompt list ─────────────────────────────────────────
    # 1. Component-typed prompts (default first, then alphabetical)
    comp_prompts = prompt_repository.list_prompts_by_component(db, component)
    # 2. Legacy tag-matched prompts without component_type set
    tagged_other = [
        p for p in prompt_repository.list_prompts_tagged(db, component)
        if p.component_type != component
    ]
    # 3. Everything else
    all_ids = {p.id for p in comp_prompts} | {p.id for p in tagged_other}
    rest    = [p for p in prompt_repository.list_all_prompts(db)
               if p.id not in all_ids]

    all_ordered: list[Prompt] = comp_prompts + tagged_other + rest

    # Build label → Prompt map (labels must be unique)
    opt_labels: list[str]          = []
    label_to_prompt: dict[str, Prompt] = {}

    for p in all_ordered:
        lbl = _display_label(p)
        # Handle accidental label collision (shouldn't happen in practice)
        if lbl in label_to_prompt:
            lbl = f"{lbl} ({p.id})"
        opt_labels.append(lbl)
        label_to_prompt[lbl] = p

    # ── Determine initial selection ───────────────────────────────────────
    saved_label = st.session_state.get(_SK)
    if saved_label not in label_to_prompt:
        # Auto-select the default asset for this component on first open
        default_asset = prompt_repository.get_default_prompt(db, component)
        if default_asset:
            saved_label = _display_label(default_asset)
        elif opt_labels:
            saved_label = opt_labels[0]
        else:
            saved_label = None

    with st.expander("🔧 Prompt Settings", expanded=False):

        if not opt_labels:
            st.warning(
                "No prompt assets found. The default assets are created on startup — "
                "try refreshing the page."
            )
            return default_system, default_user

        # ── Selector ──────────────────────────────────────────────────────
        sel_label: str = st.selectbox(
            "Active Prompt",
            opt_labels,
            index=opt_labels.index(saved_label) if saved_label in opt_labels else 0,
            help=(
                "**🏷️ Default** = system-seeded prompt (content matches the original "
                "hardcoded prompt).  Editing creates a new version — v1 is preserved.  "
                "User-created prompts appear below."
            ),
            key=f"_pp_sel_widget_{component}",
        )
        st.session_state[_SK] = sel_label

        sel_p: Prompt = label_to_prompt[sel_label]

        # Resolve active version
        sel_ver: PromptVersion | None = prompt_repository.get_active_version(db, sel_p.id)

        cur_sys: str = (sel_ver.system_prompt          or default_system) if sel_ver else default_system
        cur_usr: str = (sel_ver.user_prompt_template   or default_user)   if sel_ver else default_user

        # ── Metadata badge ────────────────────────────────────────────────
        _meta_parts = [
            f"📦 **{sel_p.name}**",
            f"version: `{sel_p.active_version or '—'}`",
            f"component: `{sel_p.component_type or '—'}`",
        ]
        if sel_p.is_default:
            _meta_parts.append("🏷️ `system default`")
        if sel_p.owner:
            _meta_parts.append(f"owner: `{sel_p.owner}`")
        if sel_p.updated_at:
            _meta_parts.append(
                f"updated: `{sel_p.updated_at.strftime('%Y-%m-%d %H:%M') if hasattr(sel_p.updated_at, 'strftime') else sel_p.updated_at}`"
            )
        st.caption("  ·  ".join(_meta_parts))

        # ── Inline tabs ───────────────────────────────────────────────────
        _t_view, _t_edit, _t_history, _t_ai = st.tabs(
            ["📄 View", "✏️ Edit & Save", "📋 Version History", "🤖 Improve with AI"]
        )

        # ── View tab ──────────────────────────────────────────────────────
        with _t_view:
            _v_c1, _v_c2 = st.columns(2)
            with _v_c1:
                st.markdown(
                    "<span style='font-size:0.78rem;font-weight:600;color:#374151;'>"
                    "System Prompt</span>",
                    unsafe_allow_html=True,
                )
                st.text_area(
                    "_v_sys",
                    value=cur_sys,
                    height=260,
                    disabled=True,
                    label_visibility="collapsed",
                    key=f"_pp_vs_{component}",
                )
            with _v_c2:
                st.markdown(
                    "<span style='font-size:0.78rem;font-weight:600;color:#374151;'>"
                    "User Prompt Template</span>",
                    unsafe_allow_html=True,
                )
                st.text_area(
                    "_v_usr",
                    value=cur_usr,
                    height=260,
                    disabled=True,
                    label_visibility="collapsed",
                    key=f"_pp_vu_{component}",
                )

            # Version info row
            if sel_ver:
                _created_by_str = sel_ver.created_by or sel_p.owner or "—"
                _created_at_str = (
                    sel_ver.created_at.strftime("%Y-%m-%d %H:%M")
                    if sel_ver.created_at and hasattr(sel_ver.created_at, "strftime")
                    else str(sel_ver.created_at or "—")
                )
                st.caption(
                    f"Version **{sel_ver.version}** · "
                    f"committed by `{_created_by_str}` · "
                    f"at `{_created_at_str}`"
                    + (f" · _{sel_ver.change_reason}_" if sel_ver.change_reason else "")
                )

            _dl = json.dumps(
                {
                    "component":            component,
                    "prompt_name":          sel_p.name,
                    "is_default":           sel_p.is_default,
                    "active_version":       sel_p.active_version or "—",
                    "system_prompt":        cur_sys,
                    "user_prompt_template": cur_usr,
                    "owner":                sel_p.owner or "—",
                    "updated_at":           str(sel_p.updated_at or "—"),
                },
                indent=2,
                ensure_ascii=False,
            )
            st.download_button(
                "⬇️ Download Active Version (JSON)",
                data=_dl,
                file_name=f"{component}_prompt_{sel_p.active_version or 'v1'}.json",
                mime="application/json",
                use_container_width=True,
                key=f"_pp_dl_{component}",
            )

        # ── Edit & Save tab ───────────────────────────────────────────────
        with _t_edit:
            _version_count = len(prompt_repository.list_versions_for_prompt(db, sel_p.id))
            _next_ver      = f"v{_version_count + 1}"

            if sel_p.is_default:
                st.info(
                    f"**{sel_p.name}** is the system default. "
                    "Your edits will be saved as a new version — v1 is preserved as the original.",
                    icon="🏷️",
                )

            with st.form(f"_pp_edit_{component}_{sel_p.id}"):
                e_sys = st.text_area(
                    "System Prompt",
                    value=cur_sys,
                    height=200,
                    help="The persona / role instructions given to the AI.",
                )
                e_usr = st.text_area(
                    "User Prompt Template",
                    value=cur_usr,
                    height=200,
                    help="The generation instruction. Use {placeholders} for runtime variables.",
                )
                e_ver = st.text_input("Version Tag", value=_next_ver)
                e_log = st.text_input(
                    "Change Log",
                    placeholder="Describe what changed in this version",
                )
                if st.form_submit_button("💾 Save as New Version", use_container_width=True):
                    if not e_ver.strip():
                        st.error("Version tag is required.")
                    else:
                        prompt_repository.deploy_new_version(
                            db,
                            prompt=sel_p,
                            system_prompt=e_sys,
                            user_prompt_template=e_usr,
                            version_tag=e_ver.strip(),
                            change_reason=e_log.strip() or "Manual edit via Prompt Settings panel.",
                            created_by=user_name,
                        )
                        st.success(
                            f"✅ Version **{e_ver.strip()}** saved for "
                            f"**{sel_p.name}**."
                        )
                        st.rerun()

            st.divider()
            st.caption("➕ Create a New Prompt Asset for this Component")
            with st.form(f"_pp_create_{component}"):
                n_name = st.text_input(
                    "Asset Name",
                    placeholder=f"e.g. custom_{component}_prompt",
                )
                n_desc = st.text_input(
                    "Description",
                    placeholder="What is this prompt for?",
                )
                n_tags = st.text_input("Tags", value=component)
                n_sys  = st.text_area(
                    "System Prompt",
                    value=cur_sys,
                    height=130,
                    help="Start from the current prompt or write from scratch.",
                )
                n_usr  = st.text_area(
                    "User Prompt Template",
                    value=cur_usr,
                    height=130,
                )
                if st.form_submit_button("🚀 Create & Activate", use_container_width=True):
                    if not n_name.strip():
                        st.error("Asset name is required.")
                    elif prompt_repository.get_prompt_by_name(db, n_name.strip()):
                        st.error(
                            f"**'{n_name.strip()}'** already exists. "
                            "Choose a unique name."
                        )
                    else:
                        p_new = Prompt(
                            name=n_name.strip(),
                            description=n_desc.strip() or None,
                            owner=user_name,
                            active_version="v1",
                            tags=n_tags.strip() or component,
                            component_type=component,
                            is_default=False,
                            created_at=datetime.utcnow(),
                            updated_at=datetime.utcnow(),
                        )
                        db.add(p_new)
                        db.commit()
                        db.refresh(p_new)
                        db.add(PromptVersion(
                            prompt_id=p_new.id,
                            version="v1",
                            system_prompt=n_sys,
                            user_prompt_template=n_usr,
                            change_reason="Created from Prompt Settings panel.",
                            is_active=True,
                            created_by=user_name,
                        ))
                        db.commit()
                        st.success(f"✅ **'{p_new.name}'** created and activated!")
                        st.session_state[_SK] = _display_label(p_new)
                        st.rerun()

        # ── Version History tab ───────────────────────────────────────────
        with _t_history:
            all_versions = prompt_repository.list_versions_for_prompt(db, sel_p.id)
            if not all_versions:
                st.info("No versions recorded yet.")
            else:
                for _v in reversed(all_versions):
                    _active_badge = " ✅ **active**" if _v.is_active else ""
                    _by_str = _v.created_by or sel_p.owner or "—"
                    _at_str = (
                        _v.created_at.strftime("%Y-%m-%d %H:%M")
                        if _v.created_at and hasattr(_v.created_at, "strftime")
                        else str(_v.created_at or "—")
                    )
                    with st.expander(
                        f"`{_v.version}`{_active_badge}  ·  {_by_str}  ·  {_at_str}",
                        expanded=_v.is_active,
                    ):
                        if _v.change_reason:
                            st.caption(f"📝 {_v.change_reason}")
                        vh1, vh2 = st.columns(2)
                        vh1.text_area(
                            "System Prompt",
                            value=_v.system_prompt or "",
                            height=160,
                            disabled=True,
                            key=f"_pp_vh_sys_{component}_{_v.id}",
                        )
                        vh2.text_area(
                            "User Prompt Template",
                            value=_v.user_prompt_template or "",
                            height=160,
                            disabled=True,
                            key=f"_pp_vh_usr_{component}_{_v.id}",
                        )
                        if not _v.is_active:
                            if st.button(
                                f"↩️ Restore {_v.version}",
                                key=f"_pp_restore_{component}_{_v.id}",
                                use_container_width=True,
                            ):
                                prompt_repository.deploy_new_version(
                                    db,
                                    prompt=sel_p,
                                    system_prompt=_v.system_prompt or "",
                                    user_prompt_template=_v.user_prompt_template or "",
                                    version_tag=f"{_v.version}-restore",
                                    change_reason=f"Restored from {_v.version}.",
                                    created_by=user_name,
                                )
                                st.success(f"↩️ Restored {_v.version} as new active version.")
                                st.rerun()

        # ── Improve with AI tab ───────────────────────────────────────────
        with _t_ai:
            st.caption(
                "Describe how to improve this prompt and the AI will generate a "
                "revised version.  Review it before applying."
            )
            ai_instr: str = st.text_area(
                "Improvement Instructions",
                placeholder=(
                    "e.g. Make the system prompt more concise.  "
                    "Emphasise real-world examples.  Remove jargon."
                ),
                height=90,
                key=f"_pp_ai_instr_{component}",
            )

            _ai_col1, _ai_col2 = st.columns(2)
            if _ai_col1.button(
                "✨ Improve with AI",
                key=f"_pp_ai_btn_{component}",
                use_container_width=True,
            ):
                if ai_instr.strip():
                    _desc = (
                        f"Improve the following {component.upper()} prompt.\n\n"
                        f"CURRENT SYSTEM PROMPT:\n{cur_sys[:800]}\n\n"
                        f"CURRENT USER PROMPT TEMPLATE:\n{cur_usr[:800]}\n\n"
                        f"IMPROVEMENT INSTRUCTIONS:\n{ai_instr}"
                    )
                    with st.spinner("AI is improving your prompt…"):
                        suggestion = generate_prompt_template_with_llm(_desc)
                    st.session_state[_AI_SK] = suggestion
                    st.success("Suggestion ready — review below.")
                else:
                    st.warning("Describe what you want to improve first.")

            if _ai_col2.button(
                "🗑️ Clear Suggestion",
                key=f"_pp_ai_clear_{component}",
                use_container_width=True,
            ):
                st.session_state.pop(_AI_SK, None)
                st.rerun()

            _sugg = st.session_state.get(_AI_SK)
            if _sugg:
                _s_sys = _sugg.get("system_prompt", "")
                _s_usr = _sugg.get("user_prompt_template", "")

                _ai_p1, _ai_p2 = st.columns(2)
                if _s_sys:
                    _ai_p1.caption("Suggested System Prompt")
                    _ai_p1.code(_s_sys[:1200], language=None)
                if _s_usr:
                    _ai_p2.caption("Suggested User Prompt Template")
                    _ai_p2.code(_s_usr[:1200], language=None)

                if _s_sys or _s_usr:
                    if st.button(
                        "✅ Apply Suggestion as New Version",
                        key=f"_pp_ai_apply_{component}",
                        use_container_width=True,
                        type="primary",
                    ):
                        _new_vn = (
                            len(prompt_repository.list_versions_for_prompt(db, sel_p.id)) + 1
                        )
                        prompt_repository.deploy_new_version(
                            db,
                            prompt=sel_p,
                            system_prompt=_s_sys or cur_sys,
                            user_prompt_template=_s_usr or cur_usr,
                            version_tag=f"v{_new_vn}-ai",
                            change_reason="Applied AI-generated improvement.",
                            created_by=user_name,
                        )
                        st.session_state.pop(_AI_SK, None)
                        st.success(
                            f"✅ AI suggestion applied as **v{_new_vn}-ai** for "
                            f"**{sel_p.name}**!"
                        )
                        st.rerun()

    # ── Return resolved prompt texts ──────────────────────────────────────
    # Always return the active version's content so the caller never needs to
    # touch the hardcoded constants.
    if sel_ver is not None:
        return (
            sel_ver.system_prompt          or default_system,
            sel_ver.user_prompt_template   or default_user,
        )
    # Last resort: no prompts in DB at all
    return default_system, default_user
