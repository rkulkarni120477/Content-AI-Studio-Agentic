"""Cdd page renderer.

Extracted from the legacy Streamlit monolith so each page can be maintained independently.
"""

import json
import streamlit as st

from promptops_app.prompt_templates import CDD_SYSTEM_PROMPT, CDD_USER_PROMPT_TEMPLATE
from promptops_app.prompts.prompt_builder import build_prompt as _lib_build_prompt
from promptops_app.ui.prompt_panel import safe_format
from promptops_app.ui.generation_controls import render_inline_prompt_controls, render_prompt_download_button
from promptops_app.ui.user_prompt_widget import auto_save_instructions


def _build_cdd_prompts(db, *, course_name, target_audience, expert_domain,
                       audience_level, estimated_duration, extra_instructions,
                       style_guidelines="") -> tuple[str, str, str, str]:
    """Return (system, user, tpl_name, tpl_version).

    Tries the prompt library first (file → DB); falls back to the
    inline constants from promptops_app.prompt_templates.py if anything fails.
    """
    try:
        variables = {
            "course_name":       course_name,
            "target_audience":   target_audience,
            "expert_domain":     expert_domain,
            "audience_level":    audience_level,
            "estimated_duration": str(estimated_duration),
            "extra_instructions": extra_instructions or "",
            "style_guidelines":  style_guidelines or "",
            "grade_level":       target_audience,
        }
        return _lib_build_prompt("cdd_generation", variables, db=db)
    except Exception:
        user_p = CDD_USER_PROMPT_TEMPLATE.format(
            course_title=course_name,
            target_audience=target_audience,
            expert_domain=expert_domain,
            audience_level=audience_level,
            estimated_duration=str(estimated_duration),
            extra_instructions_block=extra_instructions or "",
        )
        return CDD_SYSTEM_PROMPT, user_p, "cdd_generation", "v1-inline"
from promptops_app.database import (
    Style, CourseDesignDocument, CDDVersion,
    get_styles, get_active_style, build_style_context, log_event,
)
from promptops_app.auth.permissions import rbac_gate
from promptops_app.core.llm_client import safe_json_loads
from promptops_app.services.llm_service import generate_text as call_llm
from promptops_app.services.usage_service import UsageLogContext
from promptops_app.core.shared import render_cdd_content
from promptops_app.repositories import cdd_repository, style_repository
from promptops_app.repositories.course_repository import set_active_cdd
from promptops_app.parsers.cdd_parser import (
    parse_cdd_flat, parse_sections_from_text, _strip_ui_hidden_text,
)
from dataclasses import replace as _dc_replace
from promptops_app.services.export_service import export_content, export_docx, ExportRequest, TEMPLATE_LABELS
from promptops_app.ui.components import _section_badge
from promptops_app.services.audit_service import log_audit_event
from promptops_app.ui.notifications import notify_deferred


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
    # =====================================================================
    # CDD TAB — Course Design Document Pipeline
    # =====================================================================
    st.markdown(_section_badge("📘", "Course Design Document (CDD)",
        "Define learning objectives, tone, module structure, and quality standards. "
        "All downstream Blueprints and lessons inherit from this document automatically."),
        unsafe_allow_html=True)

    cdd_left, cdd_right = st.columns([0.4, 0.6], gap="large")

    with cdd_left:
        st.markdown("#### ➕ Create New CDD")
        st.markdown(
            "<p style='font-size:0.82rem;color:#6b7280;margin-top:-0.5rem;margin-bottom:1rem;'>"
            "Fields marked <span style='color:#ef4444;font-weight:700;'>*</span> are required.</p>",
            unsafe_allow_html=True
        )

        # ── Style selector (outside form so it can drive display logic) ──
        _all_styles_cdd = get_styles(db)
        _active_style_cdd_obj = get_active_style(db, project_id=_proj_id, course_id=_crs_id)

        # Determine the auto-pinned style: most recently updated/created
        _most_recent_style = _all_styles_cdd[0] if _all_styles_cdd else None  # get_styles() already orders by updated_at desc

        # Build options map: name → id; inject "(Active)" tag where applicable
        _sty_opts_cdd = {}
        if _all_styles_cdd:
            for _s in _all_styles_cdd:
                _tag = ""
                if _s.is_active:
                    _tag = " ✅ Active"
                elif _most_recent_style and _s.id == _most_recent_style.id and not _s.is_active:
                    _tag = " 🕐 Latest"
                _sty_opts_cdd[f"{_s.name}{_tag}"] = _s.id

        _sty_opts_cdd_none = {"— No style —": None}
        _sty_opts_cdd_all = {**_sty_opts_cdd_none, **_sty_opts_cdd}

        # Determine default index: prefer active style, else most recent
        _default_sty_key = "— No style —"
        if _active_style_cdd_obj:
            for k, v in _sty_opts_cdd_all.items():
                if v == _active_style_cdd_obj.id:
                    _default_sty_key = k; break
        elif _most_recent_style:
            for k, v in _sty_opts_cdd_all.items():
                if v == _most_recent_style.id:
                    _default_sty_key = k; break

        if _all_styles_cdd:
            _cdd_sty_sel = st.selectbox(
                "🎨 Style for this CDD",
                list(_sty_opts_cdd_all.keys()),
                index=list(_sty_opts_cdd_all.keys()).index(_default_sty_key),
                help="Select the style to apply during CDD generation. Defaults to the most recently created/updated style.",
                key="cdd_style_selector"
            )
            _cdd_selected_style_id = _sty_opts_cdd_all.get(_cdd_sty_sel)
            _cdd_selected_style = style_repository.get_style_by_id(db, _cdd_selected_style_id) if _cdd_selected_style_id else None

            if _cdd_selected_style:
                _sty_is_pinned = _cdd_sty_sel == _default_sty_key
                _sty_pin_label = "auto-pinned (most recent)" if not (_cdd_selected_style.is_active) and _sty_is_pinned else (
                    "active style" if _cdd_selected_style.is_active else "manually selected"
                )
                st.markdown(
                    f"<div style='background:#f0fdf4;border:1px solid #86efac;border-radius:8px;"
                    f"padding:7px 12px;font-size:0.8rem;color:#166534;margin-bottom:8px;'>"
                    f"🎨 <strong>{_cdd_selected_style.name}</strong> will be applied "
                    f"<span style='color:#6b7280;'>({_sty_pin_label})</span></div>",
                    unsafe_allow_html=True
                )
            else:
                st.markdown(
                    "<div style='background:#fef9c3;border:1px solid #fde047;border-radius:8px;"
                    "padding:7px 12px;font-size:0.8rem;color:#854d0e;margin-bottom:8px;'>"
                    "⚠️ No style selected — CDD will be generated without style constraints.</div>",
                    unsafe_allow_html=True
                )
        else:
            _cdd_selected_style = None
            st.markdown(
                "<div style='background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;"
                "padding:7px 12px;font-size:0.8rem;color:#6b7280;margin-bottom:8px;'>"
                "ℹ️ No styles created yet. Go to the <strong>Style</strong> tab to create one.</div>",
                unsafe_allow_html=True
            )

        # ── Course fields ──────────────────────────────────────────────────
        st.markdown(
            "<label style='font-weight:600;font-size:0.875rem;'>Course Title "
            "<span style='color:#ef4444;'>*</span></label>",
            unsafe_allow_html=True
        )
        cdd_course_title = st.text_input(
            "Course Title *", label_visibility="collapsed",
            placeholder="e.g. Foundations of Clinical Nursing",
            key="_cdd_course_title",
        )
        cdd_doc_title = st.text_input(
            "Document Title",
            placeholder="e.g. Nursing Foundations CDD v1",
            help="Optional label for this CDD document.",
            key="_cdd_doc_title",
        )
        st.markdown(
            "<label style='font-weight:600;font-size:0.875rem;'>Estimated Duration "
            "<span style='color:#ef4444;'>*</span>"
            "<span style='font-weight:400;color:#9ca3af;'> (hours)</span></label>",
            unsafe_allow_html=True
        )
        cdd_duration_hours = st.number_input(
            "Estimated Duration (hours) *", label_visibility="collapsed",
            min_value=1, max_value=500, value=8, step=1,
            help="Total course duration in hours (e.g. enter 8 for an 8-hour course).",
            key="_cdd_duration_hours",
        )

    # ── prompt section + generate button moved below — see end of function ──

    with cdd_right:
        st.markdown("#### 📂 Your Course Design Documents")
        # Always scope to active project; ordered desc so index 0 = most recent
        all_cdds = (
            cdd_repository.list_cdds_for_project(db, _proj_id)
            if _proj_id
            else cdd_repository.list_all_cdds(db)
        )

        if not all_cdds:
            st.info("No CDDs yet. Create your first CDD using the form on the left.")
        else:
            cdd_options = {f"{c.title} (ID: {c.id})": c.id for c in all_cdds}

            sel_cdd_label = st.selectbox(
                "Select CDD to View/Edit",
                list(cdd_options.keys()),
                index=0
            )
            sel_cdd_id = cdd_options[sel_cdd_label]
            sel_cdd = cdd_repository.get_cdd_by_id(db, sel_cdd_id)

            # CDD metadata row
            meta_c1, meta_c2, meta_c3 = st.columns(3)
            meta_c1.metric("Active Version", sel_cdd.active_version or "—")
            meta_c2.metric("State", sel_cdd.workflow_state.title())
            all_cdd_vers = cdd_repository.list_cdd_versions(db, sel_cdd_id)
            meta_c3.metric("Total Versions", len(all_cdd_vers))

            # Version selector
            ver_labels = [v.version for v in all_cdd_vers]
            if ver_labels:
                sel_ver_label = st.selectbox("View Version", ver_labels,
                                              index=ver_labels.index(sel_cdd.active_version) if sel_cdd.active_version in ver_labels else 0,
                                              key="cdd_ver_sel")
                sel_cdd_ver = cdd_repository.get_cdd_version(db, sel_cdd_id, sel_ver_label)

                if sel_cdd_ver != sel_cdd.active_version:
                    if st.button(f"🟢 Set {sel_ver_label} as Active Version", key="set_cdd_active"):
                        db.query(CDDVersion).filter(CDDVersion.cdd_id == sel_cdd_id).update({CDDVersion.is_active: False})
                        sel_cdd.active_version = sel_ver_label
                        if sel_cdd_ver:
                            sel_cdd_ver.is_active = True
                        db.commit()
                        st.toast(f"✅ CDD active version set to {sel_ver_label}")
                        st.rerun()

                if sel_cdd_ver:
                    # ── Schema-driven CDD UI rendering ─────────────────────
                    render_cdd_content(
                        full_content=sel_cdd_ver.full_content or "",
                        cdd_id=sel_cdd_id,
                        ver_label=sel_ver_label,
                        sel_cdd_ver=sel_cdd_ver,
                        sel_cdd=sel_cdd,
                        db=db,
                        user_name=user_name,
                        model_choice=st.session_state.get("model_choice", "GPT-5.4")
                    )

                    # New version from edits
                    st.divider()
                    with st.expander("🚀 Save as New Version", expanded=False):
                        new_v_tag = st.text_input("New Version Tag", value=f"v{len(all_cdd_vers)+1}", key="cdd_new_ver_tag")
                        new_v_reason = st.text_input("Change Reason", placeholder="What changed?", key="cdd_new_ver_reason")
                        if st.button("💾 Commit New CDD Version", key="cdd_commit_ver"):
                            cur_sections = safe_json_loads(sel_cdd_ver.sections) if sel_cdd_ver.sections else {}
                            db.query(CDDVersion).filter(CDDVersion.cdd_id == sel_cdd_id).update({CDDVersion.is_active: False})
                            new_ver = CDDVersion(
                                cdd_id=sel_cdd_id, version=new_v_tag,
                                full_content=sel_cdd_ver.full_content,
                                sections=json.dumps(cur_sections),
                                generation_params=sel_cdd_ver.generation_params,
                                change_reason=new_v_reason, is_active=True,
                                created_by=user_name
                            )
                            db.add(new_ver)
                            sel_cdd.active_version = new_v_tag
                            db.commit()
                            log_event(db, "cdd_version_committed", user_name, f"CDD {sel_cdd.title} → {new_v_tag}", {"cdd_id": sel_cdd_id})
                            log_audit_event(db, user_name, "cdd.version_committed", entity_type="cdd", entity_id=sel_cdd_id,
                                            project_id=_proj_id, course_id=_crs_id,
                                            metadata={"version": new_v_tag, "reason": new_v_reason})
                            st.toast(f"✅ CDD version {new_v_tag} committed.")
                            st.rerun()

            # Set as active CDD for generation
            st.divider()
            if st.button("📌 Set as Active CDD for Generation", key="pin_cdd", use_container_width=True, type="primary"):
                st.session_state["active_cdd_id"] = sel_cdd_id
                set_active_cdd(db, _crs_id, sel_cdd_id)   # Req 3: persist pin to DB
                st.toast(f"✅ '{sel_cdd.title}' set as active CDD. It will be injected into all new lesson generations.")

            # ── Download CDD ──────────────────────────────────────────────
            st.markdown("**📥 Download CDD**")
            _dl_ver = cdd_repository.get_cdd_version(db, sel_cdd_id, sel_cdd.active_version)
            if _dl_ver:
                _dl_c1, _dl_c2 = st.columns(2)
                # Build download content from the same UI-visible blocks as the renderer
                # This ensures download = exact mirror of what user sees on screen
                _cdd_dl_parsed = parse_cdd_flat(_dl_ver.full_content or "")
                _CDD_DL_BLOCKS = [
                    ("Course Details",          "Course Details"),
                    ("Course Structure",        "Course Structure & Module Assessments"),
                    ("Course Level Assessment", "Course Level Assessment"),
                ]
                _dl_parts_md   = []
                _dl_blocks_docx = []
                import re as _re_dl
                for _bk, _blabel in _CDD_DL_BLOCKS:
                    _bcontent = _cdd_dl_parsed.get(_bk, "").strip()
                    if not _bcontent:
                        continue
                    # Strip Progression Logic lines (same as UI renderer)
                    if _bk == "Course Structure":
                        _bclines = _bcontent.splitlines()
                        _bclean = []
                        _bskip = False
                        for _bl in _bclines:
                            if _re_dl.search(r'(?i)progression\s+logic', _bl):
                                _bskip = True; continue
                            if _bskip and (_re_dl.match(r'^\s*(#{1,4}|\*{2}|\-)', _bl) or _bl.strip() == ""):
                                if _bl.strip() != "": _bskip = False
                            if not _bskip:
                                _bclean.append(_bl)
                        _bcontent = "\n".join(_bclean).strip()
                    # Strip backend-only phrases (Validation Complete, etc.)
                    _bcontent = _strip_ui_hidden_text(_bcontent)
                    if not _bcontent:
                        continue
                    _dl_parts_md.append(f"## {_blabel}\n\n{_bcontent}")
                    _dl_blocks_docx.append((_blabel, _bcontent))

                _md_dl = "\n\n---\n\n".join(_dl_parts_md) if _dl_parts_md else (_dl_ver.full_content or "")
                _cdd_blocks = _dl_blocks_docx if _dl_blocks_docx else [("CDD Content", _md_dl)]
                _cdd_exp_base = ExportRequest(
                    fmt="md",
                    topic=sel_cdd.title,
                    blocks=_cdd_blocks,
                    user_name=user_name,
                    is_admin=_is_admin,
                    entity_type="cdd",
                    entity_id=sel_cdd.id,
                    project_id=st.session_state.get("selected_project_id"),
                    course_id=st.session_state.get("selected_course_id"),
                    file_name=f"CDD_{sel_cdd.title.replace(' ','_')}_{sel_cdd.active_version}.md",
                )
                _dl_c1.download_button(
                    "⬇️ Markdown (.md)",
                    data=_md_dl,
                    file_name=f"CDD_{sel_cdd.title.replace(' ','_')}_{sel_cdd.active_version}.md",
                    mime="text/markdown",
                    use_container_width=True,
                    key="cdd_dl_md"
                )
                _cdd_docx_r = export_content(db, _dc_replace(
                    _cdd_exp_base,
                    fmt="docx",
                    file_name=f"CDD_{sel_cdd.title.replace(' ','_')}_{sel_cdd.active_version}.docx",
                ))
                if _cdd_docx_r.success:
                    _dl_c2.download_button(
                        "⬇️ Word (.docx)",
                        data=_cdd_docx_r.data,
                        file_name=_cdd_docx_r.file_name,
                        mime=_cdd_docx_r.mime_type,
                        use_container_width=True,
                        key="cdd_dl_docx"
                    )
                else:
                    _dl_c2.error(_cdd_docx_r.error_message)

                # ── Download Prompt Used ────────────────────────────────────
                render_prompt_download_button(
                    db, "cdd",
                    project_name=_proj_name, cluster_name=ctx.cluster_name, course_name=_crs_name,
                    button_label="⬇️ Download Prompt Used (.md)",
                    key=f"cdd_dl_prompt_{sel_cdd_id}",
                    use_container_width=True,
                )

    # =========================================================================
    # Req 2: Prompt Configuration + Generate — full width below both panels
    # Moved out of the narrow left column so the prompt controls are not
    # squeezed into 40 % of the page (matches the Style tab layout).
    # =========================================================================
    st.divider()
    st.markdown(
        "<div style='font-size:0.78rem;color:#6b7280;margin-bottom:10px;'>"
        "📝 Fill in the course fields on the left, then configure the prompt "
        "and generate your CDD below.</div>",
        unsafe_allow_html=True,
    )

    _cdd_panel_sys, _cdd_panel_usr, _cdd_extra_instructions = render_inline_prompt_controls(
        db, "cdd",
        project_id=_proj_id, cluster_id=ctx.cluster_id, course_id=_crs_id,
        user_name=user_name, model_choice=model_choice,
        default_system=CDD_SYSTEM_PROMPT,
        default_user=CDD_USER_PROMPT_TEMPLATE,
        extra_placeholder="e.g. Focus on clinical simulation. Include DEI examples. Emphasise Bloom's levels 4–6.",
        project_name=_proj_name, cluster_name=ctx.cluster_name, course_name=_crs_name,
        user_role=user_role,
    )

    # Generate (65 %) + Download Prompt (35 %)
    _cdd_gen_c, _cdd_dl_c = st.columns([0.65, 0.35])
    with _cdd_dl_c:
        render_prompt_download_button(
            db, "cdd",
            project_name=_proj_name, cluster_name=ctx.cluster_name, course_name=_crs_name,
            button_label="⬇️ Download Prompt",
            key="_cdd_dl_prompt_btn",
            use_container_width=True,
        )
    cdd_gen_btn = _cdd_gen_c.button(
        "🤖 Generate CDD with AI",
        use_container_width=True, type="primary",
        key="_cdd_gen_btn",
    )

    if cdd_gen_btn and not cdd_course_title:
        st.warning("⚠️ Please enter a **Course Title** in the left panel before generating.")

    if cdd_gen_btn and cdd_course_title:
        if not rbac_gate(user_role, "cdd.generate", "Generating a CDD"):
            st.stop()
        model_choice = st.session_state.get("model_choice", "GPT-5.4")
        target_audience = st.session_state.get("target_audience", "")
        expert_domain = st.session_state.get("expert_domain", "")
        aud_cat = st.session_state.get("sidebar_aud_cat", "Professional/Corporate")

        if _cdd_selected_style:
            st.info(f"🎨 **Style '{_cdd_selected_style.name}'** will be applied to this CDD.", icon="🎨")

        cdd_extra_instructions = _cdd_extra_instructions
        _cdd_extra_block = (
            f"**Additional Instructions:**\n{cdd_extra_instructions.strip()}"
            if cdd_extra_instructions and cdd_extra_instructions.strip()
            else ""
        )
        _style_context_cdd = ""
        if _cdd_selected_style:
            _style_context_cdd = build_style_context(db, _cdd_selected_style)
            _cdd_extra_block = (
                f"**ACTIVE STYLE — Apply throughout this CDD:**\n{_style_context_cdd}\n\n"
                + _cdd_extra_block
            )

        with st.status("📋 Generating Course Design Document...", expanded=True) as cdd_status:
            cdd_status.write("🧠 Stage 1: Generating CDD content with AI...")
            if _cdd_panel_sys and _cdd_panel_usr:
                _cdd_sys = _cdd_panel_sys
                _cdd_usr = safe_format(
                    _cdd_panel_usr,
                    course_title=cdd_course_title,
                    course_name=cdd_course_title,
                    target_audience=target_audience,
                    expert_domain=expert_domain,
                    audience_level=aud_cat,
                    estimated_duration=str(cdd_duration_hours),
                    extra_instructions_block=_cdd_extra_block,
                    extra_instructions=_cdd_extra_block,
                    style_guidelines=_style_context_cdd if _cdd_selected_style else "",
                    grade_level=target_audience,
                )
            else:
                _cdd_sys, _cdd_usr, _, _ = _build_cdd_prompts(
                    db,
                    course_name=cdd_course_title,
                    target_audience=target_audience,
                    expert_domain=expert_domain,
                    audience_level=aud_cat,
                    estimated_duration=cdd_duration_hours,
                    extra_instructions=_cdd_extra_block,
                    style_guidelines=_style_context_cdd if _cdd_selected_style else "",
                )
            cdd_output = call_llm(model_choice, _cdd_sys, _cdd_usr, usage_ctx=UsageLogContext(
                user_name=user_name,
                project_id=st.session_state.get("selected_project_id"),
                course_id=st.session_state.get("selected_course_id"),
                entity_type="cdd",
            ))

            if cdd_output.startswith("ERROR"):
                cdd_status.update(label="❌ CDD Generation Failed", state="error")
                st.error(cdd_output)
            else:
                cdd_status.write("📦 Stage 2: Parsing CDD sections...")
                sections = parse_sections_from_text(cdd_output)
                _flat_parsed = parse_cdd_flat(cdd_output)
                for _fk, _fv in _flat_parsed.items():
                    if _fk.startswith("_"):
                        continue
                    if _fv.strip():
                        sections[_fk] = _fv

                doc_title = cdd_doc_title or f"{cdd_course_title} — CDD"
                new_cdd = CourseDesignDocument(
                    title=doc_title, course_title=cdd_course_title,
                    description="", active_version="v1",
                    workflow_state="draft", created_by=user_name,
                    project_id=st.session_state.get("selected_project_id"),
                    course_id=st.session_state.get("selected_course_id"),
                )
                db.add(new_cdd); db.commit(); db.refresh(new_cdd)

                gen_params = {
                    "course_title": cdd_course_title,
                    "target_audience": target_audience,
                    "expert_domain": expert_domain,
                    "estimated_duration_hours": cdd_duration_hours,
                    "extra_instructions": cdd_extra_instructions or "",
                }
                v1 = CDDVersion(
                    cdd_id=new_cdd.id, version="v1", full_content=cdd_output,
                    sections=json.dumps(sections), generation_params=json.dumps(gen_params),
                    change_reason="Initial AI generation", is_active=True, created_by=user_name
                )
                db.add(v1); db.commit()
                log_event(db, "cdd_created", user_name, f"CDD '{doc_title}' created (v1)", {"cdd_id": new_cdd.id})
                log_audit_event(db, user_name, "cdd.created", entity_type="cdd", entity_id=new_cdd.id,
                                project_id=_proj_id, course_id=_crs_id,
                                metadata={"title": doc_title, "sections": len(sections)})
                cdd_status.update(
                    label=f"✅ CDD '{doc_title}' created with {len(sections)} sections!",
                    state="complete",
                )
                # Auto-pin the new CDD and persist to DB (Req 3)
                st.session_state["active_cdd_id"] = new_cdd.id
                set_active_cdd(db, _crs_id, new_cdd.id)
                notify_deferred("cdd_created", f"CDD '{doc_title}' created successfully with {len(sections)} sections.")
                auto_save_instructions(
                    db, "cdd", cdd_extra_instructions,
                    name=cdd_course_title[:80],
                    project_id=_proj_id, cluster_id=None, course_id=_crs_id,
                    user_name=user_name,
                )
                st.rerun()
