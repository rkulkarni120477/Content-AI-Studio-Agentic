"""Main Streamlit shell for the production-split PromptOps app."""

import logging
import sys
import subprocess
import time

import streamlit as st

st.set_page_config(
    page_title="Content AI Studio",
    page_icon="🌀",
    layout="wide",
    initial_sidebar_state="expanded",
)


#def hide_streamlit_style():
#    st.markdown("""
#        <style>
#        header[data-testid="stHeader"] {display: none !important;}
#        [data-testid="stToolbar"] {display: none !important;}
#        #MainMenu {visibility: hidden !important;}
#        footer {visibility: hidden !important;}
#        .stDeployButton {display: none !important;}
#        </style>
#    """, unsafe_allow_html=True)

def hide_streamlit_style():
    st.markdown("""
        <style>
        #MainMenu {visibility: hidden !important;}
        footer {visibility: hidden !important;}
        </style>
    """, unsafe_allow_html=True)

_log = logging.getLogger(__name__)

#from promptops_app.database import SessionLocal, CourseDesignDocument, get_active_style, init_db_with_seed
from promptops_app.database import (
    SessionLocal,
    CourseDesignDocument,
    get_active_style,
    init_db_with_seed,
)
from promptops_app.core.context import PageContext
from promptops_app.core.shared import (
    login_page, project_dashboard_page, course_selection_page, _sidebar_brand,
)
from promptops_app.pages import style, cdd, blueprint, prompts, generate, editor, workflow, analytics
from promptops_app.repositories import cdd_repository
from promptops_app.ui.components import inject_premium_style
from promptops_app.ui.notifications import notify_check

PAGE_RENDERERS = {
    "Style": style.render_page,
    "CDD": cdd.render_page,
    "Blueprint": blueprint.render_page,
    "Prompts": prompts.render_page,
    "Generate": generate.render_page,
    "Editor": editor.render_page,
    "Workflow": workflow.render_page,
    "Analytics": analytics.render_page,
}

def main():
   # hide_streamlit_style()
    login_page()	
    # Display any deferred notifications from previous actions
    notify_check()

    if not st.session_state.get('user'):
        login_page(); return

    user_role = st.session_state.user['role']
    user_name = st.session_state.user['username']

    # ── Project / Course selection gates ─────────────────────────────────────
    if not st.session_state.get('selected_project_id'):
        project_dashboard_page(); return
    if not st.session_state.get('selected_course_id'):
        course_selection_page(); return

    # Convenience vars for context
    _proj_id   = st.session_state.selected_project_id
    _proj_name = st.session_state.selected_project_name
    _crs_id    = st.session_state.selected_course_id
    _crs_name  = st.session_state.selected_course_name
    _is_admin  = (user_role == "admin")
    _is_lead   = (user_role == "reviewer")  # Lead role

    # ── Sidebar brand + user pill ─────────────────────────────────────────────
    _sidebar_brand(user_name, user_role)

    # ── Project / Course context pill ─────────────────────────────────────────
    st.sidebar.markdown(
        f"<div style='background:rgba(255,255,255,0.12);border:1px solid rgba(165,180,252,0.22);border-radius:8px;"
        f"padding:9px 12px;margin:4px 0;font-size:0.75rem;'>"
        f"<div style='color:#c7d2fe;font-weight:700;text-transform:uppercase;"
        f"letter-spacing:.07em;margin-bottom:6px;'>Workspace</div>"
        f"<div style='color:#e0e7ff;margin-bottom:4px;'>📁 <strong style='color:#ffffff;font-weight:700;'>{_proj_name}</strong></div>"
        f"<div style='color:#e0e7ff;'>📖 <strong style='color:#ffffff;font-weight:700;'>{_crs_name}</strong></div>"
        f"</div>",
        unsafe_allow_html=True
    )
    _nav_back1, _nav_back2 = st.sidebar.columns(2)
    if _nav_back1.button("← Projects", use_container_width=True, key="back_to_projects_btn"):
        st.session_state.pop("selected_project_id", None)
        st.session_state.pop("selected_project_name", None)
        st.session_state.pop("selected_course_id", None)
        st.session_state.pop("selected_course_name", None)
        st.rerun()
    if _nav_back2.button("← Courses", use_container_width=True, key="back_to_courses_btn"):
        st.session_state.pop("selected_course_id", None)
        st.session_state.pop("selected_course_name", None)
        # Clear course-scoped pinned context to avoid stale state
        st.session_state.pop("active_cdd_id", None)
        st.session_state.pop("active_blueprint_id", None)
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

    # Target User Configuration
    with st.sidebar.expander("🎯 Target & Model", expanded=True):
        st.caption("Configure audience profile and AI model. Applied globally to all generations.")

        with st.form(key="sidebar_target_form"):
            # Model Selection
            model_choice = st.selectbox(
                "LLM Model",
                ["GPT-5.4", "Sonnet 4.5 (Bedrock)"],
                index=0 if st.session_state.get("model_choice") == "GPT-5.4" else 1,
                key="temp_model_choice"
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
                st.toast("✅ Configuration Applied!")
                st.rerun()

        # Ensure session defaults
        if "expert_domain"   not in st.session_state: st.session_state.expert_domain   = ""
        if "target_audience" not in st.session_state: st.session_state.target_audience = ""
        if "model_choice"    not in st.session_state: st.session_state.model_choice    = "GPT-5.4"
        if "expert_exp"      not in st.session_state: st.session_state.expert_exp      = "20 years"
        if "sidebar_aud_cat" not in st.session_state: st.session_state.sidebar_aud_cat = "Professional/Corporate"

    st.sidebar.divider()
    inject_premium_style()

    # ── Sidebar navigation — role-based ───────────────────────────────────────
    # Admin: full pipeline.
    # Lead (reviewer): full pipeline except Prompts (same scope as admin within assigned project).
    # ID (author): full pipeline except Prompts admin.
    if _is_admin:
        NAV_ITEMS = [
            ("🎨", "Style"),
            ("📘", "CDD"),
            ("🧩", "Blueprint"),
            ("📚", "Prompts"),
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
                "page_render FAILED  page=%r  user=%r  project_id=%r  course_id=%r  duration=%.3fs",
                _page, user_name, _proj_id, _crs_id, time.monotonic() - _page_start,
                exc_info=True,
            )
        raise
    else:
        _log.info(
            "page_render  page=%r  user=%r  project_id=%r  course_id=%r  duration=%.3fs",
            _page, user_name, _proj_id, _crs_id, time.monotonic() - _page_start,
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
            st.session_state.user = None
            st.rerun()
        db.close()


def run():
    if st.runtime.exists():
        init_db_with_seed()
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
