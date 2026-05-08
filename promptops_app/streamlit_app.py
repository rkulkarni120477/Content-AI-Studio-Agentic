"""Main Streamlit shell for the production-split PromptOps app."""

import datetime
import logging
import sys
import subprocess
import time

import extra_streamlit_components as stx
import streamlit as st
import streamlit.components.v1 as _stcomp

_log = logging.getLogger(__name__)

from promptops_app.auth.session_manager import COOKIE_NAME, create_token, decode_token
from promptops_app.core.models import (
    MODEL_CATALOG, MODELS_BY_NAME, OPENAI_MODELS, BEDROCK_MODELS,
    DEFAULT_MODEL_NAME, resolve_model,
)
from promptops_app.database import SessionLocal, CourseDesignDocument, get_active_style, init_db_with_seed, User
from promptops_app.core.context import PageContext
from promptops_app.core.shared import (
    login_page, project_dashboard_page, cluster_selection_page,
    course_selection_page, _sidebar_brand,
)
from promptops_app.pages import style, cdd, blueprint, generate, editor, workflow, analytics
from promptops_app.repositories import cdd_repository
from promptops_app.ui.components import inject_premium_style
from promptops_app.ui.notifications import notify_check

_COOKIE_TTL_DAYS = 7

PAGE_RENDERERS = {
    "Style": style.render_page,
    "CDD": cdd.render_page,
    "Blueprint": blueprint.render_page,
    "Generate": generate.render_page,
    "Editor": editor.render_page,
    "Workflow": workflow.render_page,
    "Analytics": analytics.render_page,
}

def main():
    # ── Cookie-based session: must initialise before any other st calls ───────
    # CookieManager communicates with the browser via a hidden component.
    # get_all() reads the current cookie jar; result is None on first render
    # of a cold session (returns {} or the actual cookies on subsequent renders).
    _cm = stx.CookieManager(key="__contentai_cm")
    _all_cookies = _cm.get_all() or {}

    # Handle explicit logout from shared pages (they set a flag since they lack _cm access)
    if st.session_state.pop("_logout_pending", False):
        _cm.delete(COOKIE_NAME, key="__logout_del_shared")
        st.session_state.clear()
        st.rerun()

    # Restore auth from cookie when session_state has no user (fresh / refreshed session)
    if not st.session_state.get("user"):
        _token = _all_cookies.get(COOKIE_NAME)
        if _token:
            _sd = decode_token(_token)
            if _sd:
                _db_chk = SessionLocal()
                try:
                    _u = _db_chk.query(User).filter(
                        User.username == _sd["username"]
                    ).first()
                    if _u and (_u.is_active is None or _u.is_active):
                        st.session_state.user = {
                            "username": _sd["username"],
                            "role":     _sd["role"],
                        }
                        # Restore workspace (project / cluster / course / nav_page)
                        for _k, _v in (_sd.get("workspace") or {}).items():
                            if _v is not None:
                                st.session_state[_k] = _v
                        # Restore sidebar config (model / domain / audience)
                        for _k, _v in (_sd.get("cfg") or {}).items():
                            if _v:
                                st.session_state[_k] = _v
                        st.rerun()
                    else:
                        # Account deactivated or deleted — drop the stale cookie
                        _cm.delete(COOKIE_NAME, key="__del_stale")
                finally:
                    _db_chk.close()
            else:
                # Token expired or tampered — clear it
                _cm.delete(COOKIE_NAME, key="__del_invalid")

    # Display any deferred notifications from previous actions
    notify_check()

    if not st.session_state.get('user'):
        login_page(); return

    # ── Keep cookie fresh on every render with latest workspace / config ──────
    # This ensures that after a browser refresh the user returns to the exact
    # page and workspace they were on, without re-selecting project/cluster/course.
    _cur_ws = {
        "selected_project_id":   st.session_state.get("selected_project_id"),
        "selected_project_name": st.session_state.get("selected_project_name"),
        "selected_cluster_id":   st.session_state.get("selected_cluster_id"),
        "selected_cluster_name": st.session_state.get("selected_cluster_name"),
        "selected_course_id":    st.session_state.get("selected_course_id"),
        "selected_course_name":  st.session_state.get("selected_course_name"),
        "nav_page":              st.session_state.get("nav_page"),
    }
    _cur_cfg = {
        "model_choice":    st.session_state.get("model_choice"),
        "expert_domain":   st.session_state.get("expert_domain"),
        "target_audience": st.session_state.get("target_audience"),
        "sidebar_aud_cat": st.session_state.get("sidebar_aud_cat"),
    }
    # Only write the cookie when the tracked state has changed (avoids noisy
    # component re-renders; the key is stable so Streamlit updates in-place).
    _state_sig = (
        st.session_state.get("selected_course_id"),
        st.session_state.get("nav_page"),
        st.session_state.get("model_choice"),
        st.session_state.get("expert_domain"),
        st.session_state.get("target_audience"),
    )
    if st.session_state.get("_last_cookie_sig") != _state_sig:
        _cm.set(
            COOKIE_NAME,
            create_token(
                st.session_state.user["username"],
                st.session_state.user["role"],
                workspace=_cur_ws,
                cfg=_cur_cfg,
            ),
            expires_at=datetime.datetime.now() + datetime.timedelta(days=_COOKIE_TTL_DAYS),
            key="__session_refresh",
        )
        st.session_state._last_cookie_sig = _state_sig

    # ── Unsaved-changes guard: warn the user before browser refresh / close ───
    # The browser shows a generic "Leave site?" prompt; the custom message is
    # ignored by modern browsers but the prompt itself fires reliably.
    _stcomp.html(
        """<script>
        (function() {
            var _w = window.parent || window;
            if (!_w.__contentai_unload_guard) {
                _w.__contentai_unload_guard = true;
                _w.addEventListener('beforeunload', function(e) {
                    e.preventDefault();
                    e.returnValue = 'You have unsaved changes. Refresh anyway?';
                });
            }
        })();
        </script>""",
        height=0,
    )

    user_role = st.session_state.user['role']
    user_name = st.session_state.user['username']

    # ── Project / Cluster / Course selection gates ────────────────────────────
    if not st.session_state.get('selected_project_id'):
        project_dashboard_page(); return
    if not st.session_state.get('selected_cluster_id'):
        cluster_selection_page(); return
    if not st.session_state.get('selected_course_id'):
        course_selection_page(); return

    # ── Req 1 + 3: Load per-course config from DB when course changes ─────────
    # Runs once per course switch (not on every rerun).  The flag
    # _cfg_loaded_for tracks which course_id was last loaded so we skip
    # subsequent reruns for the same course and avoid overwriting config
    # that the user applied in this session.
    _curr_crs = st.session_state.get("selected_course_id")
    if _curr_crs and st.session_state.get("_cfg_loaded_for") != _curr_crs:
        _cfg_db = SessionLocal()
        try:
            from promptops_app.repositories.course_repository import get_course_config
            _saved_cfg = get_course_config(_cfg_db, _curr_crs)
            for _k, _v in _saved_cfg.items():
                if _v is not None:       # never overwrite with None
                    st.session_state[_k] = _v
        except Exception:
            pass
        finally:
            _cfg_db.close()
        st.session_state._cfg_loaded_for = _curr_crs

    # Convenience vars for context
    _proj_id    = st.session_state.selected_project_id
    _proj_name  = st.session_state.selected_project_name
    _clus_id    = st.session_state.selected_cluster_id
    _clus_name  = st.session_state.selected_cluster_name
    _crs_id     = st.session_state.selected_course_id
    _crs_name   = st.session_state.selected_course_name
    _is_admin   = (user_role == "admin")
    _is_lead    = (user_role == "reviewer")  # Lead role

    # ── Sidebar brand + user pill ─────────────────────────────────────────────
    _sidebar_brand(user_name, user_role)

    # ── Project / Cluster / Course context pill ───────────────────────────────
    st.sidebar.markdown(
        f"<div style='background:rgba(255,255,255,0.12);border:1px solid rgba(165,180,252,0.22);border-radius:8px;"
        f"padding:9px 12px;margin:4px 0;font-size:0.75rem;'>"
        f"<div style='color:#c7d2fe;font-weight:700;text-transform:uppercase;"
        f"letter-spacing:.07em;margin-bottom:6px;'>Workspace</div>"
        f"<div style='color:#e0e7ff;margin-bottom:3px;'>📁 <strong style='color:#ffffff;font-weight:700;'>{_proj_name}</strong></div>"
        f"<div style='color:#e0e7ff;margin-bottom:3px;'>🗂️ <strong style='color:#ffffff;font-weight:700;'>{_clus_name}</strong></div>"
        f"<div style='color:#e0e7ff;'>📖 <strong style='color:#ffffff;font-weight:700;'>{_crs_name}</strong></div>"
        f"</div>",
        unsafe_allow_html=True
    )
    _nb1, _nb2, _nb3 = st.sidebar.columns(3)
    if _nb1.button("← Projects", use_container_width=True, key="back_to_projects_btn"):
        st.session_state.pop("selected_project_id",   None)
        st.session_state.pop("selected_project_name", None)
        st.session_state.pop("selected_cluster_id",   None)
        st.session_state.pop("selected_cluster_name", None)
        st.session_state.pop("selected_course_id",    None)
        st.session_state.pop("selected_course_name",  None)
        st.rerun()
    if _nb2.button("← Clusters", use_container_width=True, key="back_to_clusters_btn"):
        st.session_state.pop("selected_cluster_id",   None)
        st.session_state.pop("selected_cluster_name", None)
        st.session_state.pop("selected_course_id",    None)
        st.session_state.pop("selected_course_name",  None)
        st.session_state.pop("active_cdd_id",         None)
        st.session_state.pop("active_blueprint_id",   None)
        st.rerun()
    if _nb3.button("← Courses", use_container_width=True, key="back_to_courses_btn"):
        st.session_state.pop("selected_course_id",   None)
        st.session_state.pop("selected_course_name", None)
        st.session_state.pop("active_cdd_id",        None)
        st.session_state.pop("active_blueprint_id",  None)
        st.rerun()

    st.sidebar.divider()

    # ── Active Style + CDD status pill (scoped to current project/course) ────
    _sb_db = SessionLocal()
    _active_style_sb = get_active_style(_sb_db, project_id=_proj_id, course_id=_crs_id)
    _active_cdd_id_sb = st.session_state.get("active_cdd_id")
    _active_cdd_sb = cdd_repository.get_cdd_by_id(_sb_db, _active_cdd_id_sb) if _active_cdd_id_sb else None
    _sb_db.close()

    _sty_label = _active_style_sb.name if _active_style_sb else "None"
    _sty_color = "#10b981" if _active_style_sb else "#9ca3af"
    _cdd_label = _active_cdd_sb.title if _active_cdd_sb else "None"
    _cdd_color = "#10b981" if _active_cdd_sb else "#9ca3af"

    st.sidebar.markdown(
        f"<div style='background:rgba(255,255,255,0.10);border:1px solid rgba(165,180,252,0.20);border-radius:8px;"
        f"padding:9px 12px;margin:4px 0;font-size:0.72rem;'>"
        f"<div style='color:#c7d2fe;font-weight:700;text-transform:uppercase;"
        f"letter-spacing:.07em;margin-bottom:7px;'>Global State</div>"
        f"<div style='display:flex;align-items:center;gap:6px;margin-bottom:5px;'>"
        f"<span style='width:8px;height:8px;border-radius:50%;background:{_sty_color};flex-shrink:0;display:inline-block;'></span>"
        f"<span style='color:#e0e7ff;'>🎨 Style: <strong style='color:#ffffff;'>{_sty_label}</strong></span></div>"
        f"<div style='display:flex;align-items:center;gap:6px;'>"
        f"<span style='width:8px;height:8px;border-radius:50%;background:{_cdd_color};flex-shrink:0;display:inline-block;'></span>"
        f"<span style='color:#e0e7ff;'>📘 CDD: <strong style='color:#ffffff;'>{_cdd_label}</strong></span></div>"
        f"</div>",
        unsafe_allow_html=True
    )

    # Getting Started Guide (admin and lead see full guide; ID sees abbreviated)
    if _is_admin or _is_lead:
        with st.sidebar.expander("📖 Getting Started", expanded=False):
            st.markdown("""
            **0. Create & Activate a Style (optional but recommended)**
            Go to the **Style** tab → 🎨 Style Management → create a style with tone rules + reference docs → it auto-activates on save.

            **1. Create a Course Design Document (CDD)**
            Go to the **CDD** tab → fill in course details → generate with AI → pin with 📌.
            Active Style is auto-applied.

            **2. Create a Module Blueprint**
            Go to the **Blueprint** tab → link your CDD → describe the module → generate → pin with 📌.
            Active Style + Active CDD are auto-injected.

            **3. Generate Lessons**
            Open **Generate** — the banner shows active Style, CDD + Blueprint. Select a component and launch.

            **4. Upload Extra Context (optional)**
            Use the **Style** tab → 📄 Document Registry for supplementary PDFs/docs.

            **5. Review & Edit**
            Refine blocks in **Editor**. Each generation shows its CDD + Blueprint traceability.

            **6. Manage Workflow**
            Move blocks Draft → Review → Approved → Published in the **Workflow** tab.
            """)

    # ── Ensure session defaults ───────────────────────────────────────────────
    # Resolve old/unknown model names via backward-compat aliases before
    # any widget renders so the saved value is always valid.
    if "model_choice" not in st.session_state:
        st.session_state.model_choice = DEFAULT_MODEL_NAME
    elif st.session_state.model_choice not in MODELS_BY_NAME:
        st.session_state.model_choice = resolve_model(
            st.session_state.model_choice
        ).display_name
    if "expert_domain"   not in st.session_state: st.session_state.expert_domain   = ""
    if "target_audience" not in st.session_state: st.session_state.target_audience = ""
    if "expert_exp"      not in st.session_state: st.session_state.expert_exp      = "20 years"
    if "sidebar_aud_cat" not in st.session_state: st.session_state.sidebar_aud_cat = "Professional/Corporate"

    # Target User Configuration
    with st.sidebar.expander("🎯 Target & Model", expanded=True):
        st.caption("Configure the AI model and audience profile. Applied to all generations.")

        # Build grouped model list: OpenAI first, Bedrock second
        _all_model_names = (
            [m.display_name for m in OPENAI_MODELS]
            + [m.display_name for m in BEDROCK_MODELS]
        )
        _saved_model = st.session_state.get("model_choice", DEFAULT_MODEL_NAME)
        _model_idx   = (
            _all_model_names.index(_saved_model)
            if _saved_model in _all_model_names
            else 0
        )

        with st.form(key="sidebar_target_form"):
            # Model selector — options driven by the catalog, no hardcoded strings
            model_choice = st.selectbox(
                "LLM Model",
                _all_model_names,
                index=_model_idx,
                key="temp_model_choice",
                help=(
                    "OpenAI GPT models route via the OpenAI API.  "
                    "Claude models route via AWS Bedrock."
                ),
            )
            st.divider()

            # Expert Domain
            expert_domain = st.text_input(
                "Expert Domain",
                value=st.session_state.get("expert_domain", ""),
                placeholder="e.g. Nursing, Software Dev, Finance",
                key="temp_expert_domain"
            )

            # Audience category
            aud_cat = st.selectbox(
                "Audience Category",
                ["Professional/Corporate", "Undergrad/Graduate", "K-12 Student", "General"],
                index=["Professional/Corporate", "Undergrad/Graduate", "K-12 Student", "General"].index(
                    st.session_state.get("sidebar_aud_cat", "Professional/Corporate")),
                key="temp_aud_cat"
            )
            target_audience = st.text_input(
                "Specific Audience Detail",
                value=st.session_state.get("target_audience", ""),
                placeholder="e.g. Grade 7 students, Junior nurses",
                key="temp_audience_detail"
            )

            submit_target = st.form_submit_button("✅ Apply Configuration", use_container_width=True)
            if submit_target:
                st.session_state.model_choice     = model_choice
                st.session_state.expert_domain    = expert_domain
                st.session_state.sidebar_aud_cat  = aud_cat
                st.session_state.target_audience  = target_audience
                # ── Req 1: Persist config to DB for the active course ─────────
                _save_crs = st.session_state.get("selected_course_id")
                if _save_crs:
                    try:
                        from promptops_app.repositories.course_repository import save_course_target_config
                        _save_db = SessionLocal()
                        save_course_target_config(
                            _save_db, _save_crs,
                            model_choice=model_choice,
                            expert_domain=expert_domain,
                            target_audience=target_audience,
                            audience_category=aud_cat,
                        )
                        _save_db.close()
                    except Exception:
                        pass   # config save failure must never block the UI
                st.toast("✅ Configuration Applied!")
                st.rerun()

        # ── Active model card (outside form so it always reflects applied state) ──
        _active_m = MODELS_BY_NAME.get(st.session_state.get("model_choice", DEFAULT_MODEL_NAME))
        if _active_m:
            _prov_icon  = "🤖" if _active_m.provider == "openai" else "☁️"
            _prov_label = "OpenAI GPT" if _active_m.provider == "openai" else "AWS Bedrock"
            _tag_chips  = "".join(
                f"<span style='background:#eef2ff;color:#4338ca;font-size:0.67rem;"
                f"font-weight:700;padding:1px 8px;border-radius:10px;"
                f"letter-spacing:.04em;margin-right:4px;'>{t}</span>"
                for t in _active_m.tags
            )
            st.sidebar.markdown(
                f"<div style='background:#f8faff;border:1.5px solid #c7d2fe;"
                f"border-radius:10px;padding:10px 13px;margin-top:6px;'>"
                f"<div style='font-size:0.68rem;font-weight:700;text-transform:uppercase;"
                f"letter-spacing:.09em;color:#6366f1;margin-bottom:5px;'>"
                f"{_prov_icon} {_prov_label}</div>"
                f"<div style='font-weight:700;color:#111827;font-size:0.88rem;"
                f"margin-bottom:3px;'>{_active_m.display_name}</div>"
                f"<div style='font-size:0.77rem;color:#6b7280;margin-bottom:6px;"
                f"line-height:1.45;'>{_active_m.description}</div>"
                f"<div>{_tag_chips}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

    st.sidebar.divider()
    inject_premium_style()

    # ── Sidebar navigation — role-based ───────────────────────────────────────
    # Admin / Lead / ID: same pipeline. Prompt management is embedded in each tab.
    if _is_admin:
        NAV_ITEMS = [
            ("🎨", "Style"),
            ("📘", "CDD"),
            ("🧩", "Blueprint"),
            ("⚙️", "Generate"),
            ("✏️", "Editor"),
            ("🚦", "Workflow"),
            ("📊", "Analytics"),
        ]
    elif _is_lead:
        NAV_ITEMS = [
            ("🎨", "Style"),
            ("📘", "CDD"),
            ("🧩", "Blueprint"),
            ("⚙️", "Generate"),
            ("✏️", "Editor"),
            ("🚦", "Workflow"),
            ("📊", "Analytics"),
        ]
    else:
        NAV_ITEMS = [
            ("🎨", "Style"),
            ("📘", "CDD"),
            ("🧩", "Blueprint"),
            ("⚙️", "Generate"),
            ("✏️", "Editor"),
            ("🚦", "Workflow"),
            ("📊", "Analytics"),
        ]

    # Ensure nav_page is valid for this role
    _default_nav = "CDD" if (not _is_admin and not _is_lead) else "Style"
    if "nav_page" not in st.session_state:
        st.session_state.nav_page = _default_nav
    valid_pages = {lbl for _, lbl in NAV_ITEMS}
    if st.session_state.nav_page not in valid_pages:
        st.session_state.nav_page = _default_nav

    st.sidebar.divider()
    st.sidebar.markdown(
        "<div style='font-size:0.68rem;font-weight:700;letter-spacing:0.12em;"
        "text-transform:uppercase;color:#a5b4fc;padding:0 4px 4px 4px;'>"
        "Navigation</div>",
        unsafe_allow_html=True
    )

    # Build all nav button CSS in one pass — single <style> injection instead of one per button
    _nav_css_parts = []
    for _icon, _label in NAV_ITEMS:
        _is_active = st.session_state.nav_page == _label
        _bg       = "rgba(99,102,241,0.25)" if _is_active else "rgba(255,255,255,0.04)"
        _fg       = "#ffffff" if _is_active else "#c7d2fe"
        _border_l = "3px solid #a5b4fc" if _is_active else "3px solid transparent"
        _fw       = "700" if _is_active else "500"
        _nav_css_parts.append(
            f"div[data-testid='stSidebar'] div[data-testid='stButton']"
            f":has(button[kind='secondary'] p:contains('{_label}')) > button {{"
            f"background:{_bg} !important;color:{_fg} !important;"
            f"border-left:{_border_l} !important;font-weight:{_fw} !important;"
            f"border-radius:0 6px 6px 0 !important;text-align:left !important;}}"
        )
    st.sidebar.markdown(f"<style>{''.join(_nav_css_parts)}</style>", unsafe_allow_html=True)

    for _icon, _label in NAV_ITEMS:
        if st.sidebar.button(
            f"{_icon}  {_label}",
            key=f"nav_{_label}",
            use_container_width=True,
        ):
            st.session_state.nav_page = _label
            st.rerun()

    _page = st.session_state.nav_page

    _page = st.session_state.nav_page
    db = SessionLocal()
    ctx = PageContext(
        user_role=user_role,
        user_name=user_name,
        project_id=_proj_id,
        project_name=_proj_name,
        cluster_id=_clus_id,
        cluster_name=_clus_name,
        course_id=_crs_id,
        course_name=_crs_name,
        is_admin=_is_admin,
        is_lead=_is_lead,
        model_choice=st.session_state.get("model_choice", "GPT-5.4"),
        expert_domain=st.session_state.get("expert_domain", ""),
        target_audience=st.session_state.get("target_audience", ""),
        audience_category=st.session_state.get("sidebar_aud_cat", "Professional/Corporate"),
    )

    _page_start = time.monotonic()
    try:
        PAGE_RENDERERS[_page](db, ctx)
    except Exception as _page_exc:
        _exc_name = type(_page_exc).__name__
        if "Rerun" not in _exc_name and "Stop" not in _exc_name:
            _log.error(
                "page_render FAILED  page=%r  user=%r  project_id=%r  cluster_id=%r  course_id=%r  duration=%.3fs",
                _page, user_name, _proj_id, _clus_id, _crs_id, time.monotonic() - _page_start,
                exc_info=True,
            )
        raise
    else:
        _log.info(
            "page_render  page=%r  user=%r  project_id=%r  cluster_id=%r  course_id=%r  duration=%.3fs",
            _page, user_name, _proj_id, _clus_id, _crs_id, time.monotonic() - _page_start,
        )
    finally:
        st.sidebar.divider()
        st.sidebar.markdown(
            "<p style='color:#818cf8;font-size:0.72rem;text-align:center;margin-bottom:6px;'>"
            "Content AI Studio · Enterprise AI Platform</p>",
            unsafe_allow_html=True,
        )
        _sf1, _sf2 = st.sidebar.columns(2)
        if _sf1.button("🔄 Refresh", use_container_width=True, key="sidebar_refresh"):
            st.rerun()
        if _sf2.button("🚪 Sign Out", use_container_width=True, key="sidebar_signout"):
            _cm.delete(COOKIE_NAME, key="__logout_del")
            st.session_state.clear()
            st.rerun()
        db.close()


@st.cache_resource(show_spinner=False)
def _initialize_database():
    """Run DB migrations and seed exactly once per server process, not on every rerun."""
    init_db_with_seed()


def run():
    if st.runtime.exists():
        _initialize_database()
        main()
    else:
        _log.info("Bootstrapping Content AI Studio — launching Streamlit on port 1060")
        try:
            subprocess.run([
                "streamlit", "run", "app.py",
                "--server.port", "1060",
                "--server.address", "0.0.0.0",
                "--server.enableCORS", "false",
                "--server.enableXsrfProtection", "false",
                "--browser.gatherUsageStats", "false",
            ])
        except Exception as exc:
            _log.error("Failed to launch Streamlit: %s", exc, exc_info=True)
            _log.info("Run manually: streamlit run app.py --server.port 1060")


if __name__ == "__main__":
    run()
