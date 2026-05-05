"""Prompts page renderer."""

import streamlit as st

from promptops_app.prompt_templates import PROMPT_TEMPLATES, REGISTRY_FALLBACK_SYSTEM, REGISTRY_FALLBACK_USER
from promptops_app.database import Prompt, PromptVersion
from promptops_app.auth.permissions import role_label
from promptops_app.repositories import prompt_repository
from promptops_app.services.evaluation_service import generate_prompt_template_with_llm
from promptops_app.ui.components import _section_badge


def render_page(db, ctx):
    user_role = ctx.user_role
    user_name = ctx.user_name
    _proj_id = ctx.project_id
    _proj_name = ctx.project_name
    _crs_id = ctx.course_id
    _crs_name = ctx.course_name
    _is_admin = ctx.is_admin
    _is_lead = ctx.is_lead
    model_choice = ctx.model_choice
    expert_domain = ctx.expert_domain
    target_audience = ctx.target_audience
    aud_cat = ctx.audience_category
    st.markdown(_section_badge("📚", "Prompt Registry",
        "Centralised library of prompt templates. Create, version, and manage the AI instructions that drive all content generation."),
        unsafe_allow_html=True)

    # Advanced Search Layer
    with st.expander("🔍 Filter & Search Asset Library", expanded=False):
        search_query = st.text_input("Search Registry (Name, Description, Tags)", "").lower()
        role_filter = st.multiselect("Owner Roles", ["admin", "author", "reviewer"],
            format_func=lambda r: role_label(r))

    adm_c1, adm_c2 = st.columns([0.4, 0.6], gap="large")
    with adm_c1:
        st.subheader("🆕 Commit New Asset")

        # One-click Template Library
        with st.expander("📚 Quick Start from Template", expanded=False):
            st.caption("Choose a pre-built prompt template to get started instantly.")
            tmpl_name = st.selectbox("Template", list(PROMPT_TEMPLATES.keys()))
            if st.button("⚡ Create from Template", use_container_width=True):
                tmpl = PROMPT_TEMPLATES[tmpl_name]
                asset_id = tmpl_name.lower().replace(" ", "_").replace("&", "and")
                if not prompt_repository.get_prompt_by_name(db, asset_id):
                    p_new = Prompt(name=asset_id, description=f"Auto-created from '{tmpl_name}' template.", owner=user_name, active_version="v1", tags=tmpl["tags"])
                    db.add(p_new); db.commit(); db.refresh(p_new)
                    db.add(PromptVersion(prompt_id=p_new.id, version="v1", system_prompt=tmpl["system"], user_prompt_template=tmpl["user"], change_reason="Created from template.", is_active=True))
                    db.commit(); st.success(f"Created '{asset_id}' with version v1 from template!")
                else:
                    st.warning(f"Asset '{asset_id}' already exists.")
        # AI-Powered Template Generator
        with st.expander("🤖 AI Prompt Generator (Describe What You Need)", expanded=False):
            st.caption("Describe what kind of prompt you need in plain language, and the AI will generate one for you.")
            ai_desc = st.text_area("Describe your ideal prompt", placeholder="e.g., I need a prompt that creates interactive coding exercises with hints and solution explanations for programming courses...", key="ai_tmpl_desc", height=100)
            if st.button("✨ Generate Prompt with AI", use_container_width=True) and ai_desc:
                with st.spinner("AI is engineering your prompt..."):
                    tmpl = generate_prompt_template_with_llm(ai_desc)
                asset_id = tmpl.get("name", "custom_prompt")
                if not prompt_repository.get_prompt_by_name(db, asset_id):
                    p_new = Prompt(name=asset_id, description=tmpl.get("description", ai_desc), owner=user_name, active_version="v1", tags=tmpl.get("tags", "custom"))
                    db.add(p_new); db.commit(); db.refresh(p_new)
                    db.add(PromptVersion(prompt_id=p_new.id, version="v1", system_prompt=tmpl["system_prompt"], user_prompt_template=tmpl["user_prompt_template"], change_reason="AI-generated from description.", is_active=True))
                    db.commit(); st.success(f"✅ Created '{asset_id}' with AI-generated prompts!")
                    st.caption("Preview Generated Prompt")
                    st.json(tmpl)
                else:
                    st.warning(f"Asset '{asset_id}' already exists. Try a different description.")

        with st.form("asset_commit"):
            p_name = st.text_input("Asset ID (Required)", placeholder="e.g., adaptive_tutor_v1")
            p_desc = st.text_area("Implementation Goal", placeholder="Describe output expectations...")
            p_tags = st.text_input("Metadata Tags", placeholder="elearning, concise, technical")
            if st.form_submit_button("Commit to Content AI Studio"):
                if not p_name: st.error("Asset ID is required.")
                elif prompt_repository.get_prompt_by_name(db, p_name): st.error("Asset ID collision. Use a unique name.")
                else:
                    db.add(Prompt(name=p_name, description=p_desc, owner=st.session_state.user['username'], tags=p_tags))
                    db.commit(); st.success(f"Asset '{p_name}' Registered")

    with adm_c2:
        st.subheader("🛠️ Version Control & Lifecycle")
        # Apply Search Filtering
        all_prompts = prompt_repository.list_all_prompts(db)
        filtered_prompts = [p for p in all_prompts if (search_query in p.name.lower() or search_query in (p.description or "").lower() or search_query in (p.tags or "").lower())]

        if filtered_prompts:
            plist = [p.name for p in filtered_prompts]
            sel_p = st.selectbox("Select Asset to Manage", plist)
            p_obj = prompt_repository.get_prompt_by_name(db, sel_p)

            with st.expander(f"Details: {p_obj.name}", expanded=True):
                st.caption(f"Owner: {p_obj.owner} | Tags: {p_obj.tags}")
                if p_obj.active_version: st.info(f"Current Active Production Version: **{p_obj.active_version}**")

                with st.form(f"version_form_{p_obj.id}"):
                    st.write("---")
                    v_str = st.text_input("New Version Tag", value=f"v{len(prompt_repository.list_versions_for_prompt(db, p_obj.id))+1}")
                    v_sys = st.text_area("System User", value=p_obj.versions[-1].system_prompt if p_obj.versions else REGISTRY_FALLBACK_SYSTEM)
                    v_tmpl = st.text_area("User Prompt Template", value=p_obj.versions[-1].user_prompt_template if p_obj.versions else REGISTRY_FALLBACK_USER)
                    v_reason = st.text_input("Change Log Entry", placeholder="Describe what changed in this version...")
                    if st.form_submit_button("🚀 Deploy New Version"):
                        db.query(PromptVersion).filter(PromptVersion.prompt_id == p_obj.id).update({PromptVersion.is_active: False})
                        db.add(PromptVersion(prompt_id=p_obj.id, version=v_str, system_prompt=v_sys, user_prompt_template=v_tmpl, change_reason=v_reason, is_active=True))
                        p_obj.active_version = v_str; db.commit(); st.success(f"Version {v_str} Deployed Successfully")
        else: st.info("No assets match your search or library is empty.")

