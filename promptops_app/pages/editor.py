"""Editor page renderer.

Extracted from the legacy Streamlit monolith so each page can be maintained independently.
"""

import json
import streamlit as st
from datetime import datetime, timezone

from promptops_app.prompt_templates import (
    PERSONA_PREFIX_TEMPLATE,
    IMPROVISE_DEFAULT_REQUEST,
    IMPROVISE_BLOCK_PROMPT_TEMPLATE,
)
from promptops_app.database import (
    Block, CourseDesignDocument, FeedbackSignal, Generation,
    ModuleBlueprint, Prompt, PromptVersion, Review,
    search_blocks, log_event,
)
from promptops_app.auth.permissions import rbac_check, rbac_gate, role_label
from promptops_app.services.audit_service import log_audit_event
from promptops_app.core.config import PROMPTOPS_EDITOR_PAGE_SIZE
from promptops_app.services.llm_service import generate_text as call_llm
from promptops_app.services.usage_service import UsageLogContext
from promptops_app.parsers.blueprint_parser import (
    parse_items_from_section, patch_item_in_section, regen_single_item,
)
from promptops_app.repositories.block_repo import (
    save_block_version, get_block_versions, restore_block_version,
)
from promptops_app.services.evaluation_service import (
    score_content_quality, llm_evaluate_block,
    check_plagiarism_content, get_initial_quality_metadata,
)
from dataclasses import replace as _dc_replace
from promptops_app.services.export_service import (
    export_content, export_html, export_docx,
    ExportRequest, TEMPLATE_LABELS,
)
from promptops_app.repositories import (
    blueprint_repository, cdd_repository, generation_repository, prompt_repository,
    user_repository,
)
from promptops_app.services.workflow_service import submit_for_review
from promptops_app.services import autosave_service
from promptops_app.services import validation_service
from promptops_app.core.constants import WorkflowState
from promptops_app.ui import validation_panel
from promptops_app.ui.components import status_badge, _section_badge
from promptops_app.ui.generation_controls import render_prompt_download_button


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
    # ── Download Full Course (shown when course is complete) ──────────────
    _dl_cdd_id = st.session_state.get("active_cdd_id")
    _dl_bp_ids = [
        bp.id for bp in blueprint_repository.list_blueprints_for_course(db, course_id=_crs_id, project_id=_proj_id)
    ] if _proj_id and _crs_id else []
    _dl_gens = generation_repository.list_course_generations(db, _proj_id, _crs_id) if _proj_id and _crs_id else []
    _dl_gen_ids = [g.id for g in _dl_gens]
    _dl_all_blocks = generation_repository.list_blocks_for_gen_ids(db, _dl_gen_ids) if _dl_gen_ids else []
    _dl_course_complete = (
        len(_dl_gens) > 0 and
        len(_dl_all_blocks) > 0 and
        all(b.workflow_state.lower() in ("approved", "published") for b in _dl_all_blocks)
    )
    if _dl_course_complete:
        st.markdown(
            "<div style='background:#f0fdf4;border:2px solid #86efac;border-radius:12px;"
            "padding:16px 20px;margin-bottom:16px;display:flex;align-items:center;gap:16px;'>"
            "<div style='font-size:2rem;'>🎉</div>"
            "<div><div style='font-weight:700;font-size:1.05rem;color:#15803d;margin-bottom:2px;'>"
            "Course Generation Complete!</div>"
            "<div style='font-size:0.84rem;color:#166534;'>All blocks are approved. "
            "Validate content below before downloading the full course package.</div></div></div>",
            unsafe_allow_html=True
        )
        _dl_crs_name = _crs_name or "Course"
        _dl_topic    = _dl_crs_name
        _dl_content  = [(b.block_label, b.content) for b in _dl_all_blocks if b.content]

        # ── Validation gate ───────────────────────────────────────────────────
        _val_key = f"course_val_{_crs_id or 'all'}"
        _teacher_mode = st.session_state.get("teacher_mode", False)

        _v_col1, _v_col2 = st.columns([1, 5])
        with _v_col1:
            if st.button(
                "🔍 Validate Content",
                key="btn_validate_course",
                type="primary",
                use_container_width=True,
                help="Run quality checks before exporting.",
            ):
                st.session_state[_val_key] = validation_service.validate_blocks(
                    _dl_all_blocks, teacher_mode=_teacher_mode
                )
        with _v_col2:
            _existing_vr = st.session_state.get(_val_key)
            if _existing_vr:
                n_e = _existing_vr["summary"]["errors"]
                n_w = _existing_vr["summary"]["warnings"]
                if n_e:
                    st.markdown(
                        f"<span style='background:#fef2f2;color:#991b1b;font-size:0.8rem;"
                        f"padding:4px 12px;border-radius:10px;font-weight:700;'>"
                        f"❌ {n_e} error(s) — fix before exporting</span>",
                        unsafe_allow_html=True,
                    )
                elif n_w:
                    st.markdown(
                        f"<span style='background:#fffbeb;color:#92400e;font-size:0.8rem;"
                        f"padding:4px 12px;border-radius:10px;font-weight:700;'>"
                        f"⚠️ {n_w} warning(s) — confirm to export</span>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        "<span style='background:#f0fdf4;color:#166534;font-size:0.8rem;"
                        "padding:4px 12px;border-radius:10px;font-weight:700;'>"
                        "✅ Validation passed — ready to export</span>",
                        unsafe_allow_html=True,
                    )
            else:
                st.caption("Click **Validate Content** to check for issues before exporting.")

        _val_result = st.session_state.get(_val_key)
        if _val_result:
            validation_panel.render(_val_result)
            _export_ok = validation_panel.export_allowed(_val_result, key_prefix="course")
        else:
            # Validation not yet run — allow export but show a soft prompt
            _export_ok = True
            st.info(
                "💡 Run **Validate Content** first for a quality check. "
                "You can still export, but unvalidated content may contain issues."
            )

        if _export_ok:
            _fc_tmpl_sel = st.selectbox(
                "Export template",
                list(TEMPLATE_LABELS.keys()),
                format_func=lambda k: TEMPLATE_LABELS[k],
                key="dl_full_course_template",
                help="Choose layout for HTML and DOCX. Markdown is always plain text.",
            )
            _dl_c1, _dl_c2, _dl_c3, _dl_c4 = st.columns(4)
            _fc_base = ExportRequest(
                fmt="md", topic=_dl_topic, blocks=_dl_content,
                user_name=user_name, is_admin=_is_admin,
                entity_type="full_course",
                project_id=st.session_state.get("selected_project_id"),
                course_id=st.session_state.get("selected_course_id"),
                template=_fc_tmpl_sel,
            )
            with _dl_c1:
                _fc_md = export_content(db, _fc_base)
                if _fc_md.success:
                    st.download_button(
                        "⬇️ Markdown",
                        data=_fc_md.data,
                        file_name=f"{_dl_crs_name.replace(' ', '_')}_full_course.md",
                        mime="text/markdown",
                        use_container_width=True,
                        key="dl_full_course_md",
                    )
            with _dl_c2:
                _fc_html = export_content(db, _dc_replace(_fc_base, fmt="html"))
                if _fc_html.success:
                    st.download_button(
                        "⬇️ HTML",
                        data=_fc_html.data,
                        file_name=f"{_dl_crs_name.replace(' ', '_')}_full_course.html",
                        mime="text/html",
                        use_container_width=True,
                        key="dl_full_course_html",
                    )
            with _dl_c3:
                _fc_docx = export_content(db, _dc_replace(_fc_base, fmt="docx"))
                if _fc_docx.success:
                    st.download_button(
                        "⬇️ DOCX",
                        data=_fc_docx.data,
                        file_name=f"{_dl_crs_name.replace(' ', '_')}_full_course.docx",
                        mime=_fc_docx.mime_type,
                        use_container_width=True,
                        key="dl_full_course_docx",
                    )
            with _dl_c4:
                _fc_zip = export_content(db, _dc_replace(_fc_base, fmt="zip"))
                if _fc_zip.success:
                    st.download_button(
                        "⬇️ ZIP Bundle",
                        data=_fc_zip.data,
                        file_name=f"{_dl_crs_name.replace(' ', '_')}_full_course.zip",
                        mime="application/zip",
                        use_container_width=True,
                        key="dl_full_course_zip",
                    )
        # ── Download Prompt Used for Generation ───────────────────────────
        render_prompt_download_button(
            db, "generate",
            project_name=_proj_name, cluster_name=ctx.cluster_name, course_name=_crs_name,
            button_label="⬇️ Download Prompt Used (.md)",
            key="editor_dl_prompt_used",
            use_container_width=False,
        )
        st.divider()

    st.markdown(_section_badge("✏️", "Content Editor & Export",
        "Review, edit, and refine generated blocks. Submit quality reviews, run AI evaluations, and export to Markdown, JSON, HTML, or DOCX."),
        unsafe_allow_html=True)

    # Content Search Bar
    with st.expander("🔍 Search All Content", expanded=False):
        search_q = st.text_input("Search blocks by content or label", "", help="Find specific content across all your generated blocks.")
        if search_q:
            search_results = search_blocks(db, search_q)
            if search_results:
                st.caption(f"Found {len(search_results)} matching block(s):")
                st.dataframe([{"ID": b.id, "Label": b.block_label, "State": b.workflow_state, "Gen#": b.generation_id} for b in search_results], use_container_width=True)
            else:
                st.info("No blocks match your search.")

    col_ed1, col_ed2 = st.columns([0.5, 0.5])
    
    # ── Context-scoped generation list ───────────────────────────────────────
    # If a Blueprint is pinned, show only generations from that blueprint.
    # If a CDD is pinned (but no blueprint), show only generations from that CDD.
    # Otherwise show all.
    _ed_active_bp_id  = st.session_state.get("active_blueprint_id")
    _ed_active_cdd_id = st.session_state.get("active_cdd_id")
    _ed_scope_label   = ""

    if _ed_active_bp_id:
        _ed_bp_obj = blueprint_repository.get_blueprint_by_id(db, _ed_active_bp_id)
        _ed_scope_label = f"🧩 Showing content for: **{_ed_bp_obj.title if _ed_bp_obj else 'Active Blueprint'}**"
    elif _ed_active_cdd_id:
        _ed_cdd_obj = cdd_repository.get_cdd_by_id(db, _ed_active_cdd_id)
        _ed_scope_label = f"📘 Showing content for: **{_ed_cdd_obj.title if _ed_cdd_obj else 'Active CDD'}**"

    if _ed_scope_label:
        st.markdown(
            f"<div style='background:#eef2ff;border:1px solid #c7d2fe;"
            f"border-radius:8px;padding:7px 12px;font-size:0.82rem;"
            f"color:#3730a3;margin-bottom:10px;'>{_ed_scope_label} — "
            f"<span style='font-weight:400;'>Switch to a different Blueprint/CDD tab to change scope.</span></div>",
            unsafe_allow_html=True
        )

    all_gens = generation_repository.list_generations_scoped(
        db,
        blueprint_id=_ed_active_bp_id,
        cdd_id=_ed_active_cdd_id if not _ed_active_bp_id else None,
        limit=PROMPTOPS_EDITOR_PAGE_SIZE,
    )
    if all_gens:
        gen_options = {f"{g.topic} (ID: #{g.id}) — {g.created_at.strftime('%Y-%m-%d %H:%M')}": g.id for g in all_gens}
        
        # Try to default to the last generated ID if it exists in options
        last_id = st.session_state.get("last_gen_id")
        default_ix = 0
        if last_id:
            for i, (label, gid_val) in enumerate(gen_options.items()):
                if gid_val == last_id:
                    default_ix = i
                    break
        
        selected_gen_label = col_ed1.selectbox(
            "Select File (Topic)",
            options=list(gen_options.keys()),
            index=default_ix,
            help="Select the generation topic you want to review or edit."
        )
        gid = gen_options[selected_gen_label]

        # ── Traceability badge for selected generation ────────────────────
        sel_gen_obj = generation_repository.get_generation_by_id(db, gid)
        if sel_gen_obj:
            t_cdd_label = "None"
            t_bp_label  = "None"
            if sel_gen_obj.cdd_id:
                t_cdd = cdd_repository.get_cdd_by_id(db, sel_gen_obj.cdd_id)
                t_cdd_label = f"{t_cdd.title} ({sel_gen_obj.cdd_version})" if t_cdd else f"CDD #{sel_gen_obj.cdd_id}"
            if sel_gen_obj.blueprint_id:
                t_bp = blueprint_repository.get_blueprint_by_id(db, sel_gen_obj.blueprint_id)
                t_bp_label = f"{t_bp.title} ({sel_gen_obj.blueprint_version})" if t_bp else f"Blueprint #{sel_gen_obj.blueprint_id}"

            trace_color = "#0f2027" if (sel_gen_obj.cdd_id or sel_gen_obj.blueprint_id) else "#1a1a1a"
            st.markdown(
                f"""<div style='background:{trace_color}; border:1px solid #1e3a5f; border-radius:6px;
                                padding:8px 14px; margin:6px 0 12px 0; font-size:0.8rem; color:#94a3b8;'>
                    🔗 <strong style='color:#cbd5e1;'>Generation Trace:</strong>&nbsp;
                    Prompt <code style='color:#a5b4fc;'>{sel_gen_obj.prompt_name} / {sel_gen_obj.prompt_version}</code>
                    &nbsp;·&nbsp; CDD <code style='color:#6ee7b7;'>{t_cdd_label}</code>
                    &nbsp;·&nbsp; Blueprint <code style='color:#fcd34d;'>{t_bp_label}</code>
                </div>""",
                unsafe_allow_html=True
            )
    else:
        col_ed1.info("No generations found yet.")
        gid = None

    # Export Actions — MD, JSON, HTML, DOCX
    # Non-admin users may only export when all blocks in the generation are approved or published.
    _export_blks_check = generation_repository.list_blocks_for_generation(db, gid) if gid else []
    _all_blocks_exportable = (
        len(_export_blks_check) > 0 and
        all(b.workflow_state.lower() in WorkflowState.EXPORTABLE for b in _export_blks_check)
    )
    _can_export = rbac_check(user_role, "export.course") and (_is_admin or _all_blocks_exportable)
    if not _can_export and gid and not _is_admin:
        col_ed2.warning(
            "🔒 Export locked — all blocks must be **Approved** or **Published** before exporting. "
            "Submit content for review and get it approved first."
        )

    # ── Compact validation indicator for this generation ─────────────────────
    _gen_val_key = f"gen_val_{gid}"
    if _can_export and gid:
        _gv_btn, _gv_result = col_ed2.columns([1, 2])
        if _gv_btn.button("🔍 Validate", key=f"btn_val_gen_{gid}", help="Check content quality"):
            st.session_state[_gen_val_key] = validation_service.validate_blocks(
                _export_blks_check
            )
        _gen_vr = st.session_state.get(_gen_val_key)
        if _gen_vr:
            with _gv_result:
                validation_panel.render(_gen_vr, compact=True)
            if _gen_vr["summary"]["errors"]:
                _can_export = False
                col_ed2.error(
                    f"❌ Export blocked — {_gen_vr['summary']['errors']} error(s) found. "
                    "See the full validation panel in the Course Download section above."
                )

    # Template selector for per-generation exports
    _gen_tmpl_sel = col_ed2.selectbox(
        "Export template",
        list(TEMPLATE_LABELS.keys()),
        format_func=lambda k: TEMPLATE_LABELS[k],
        key=f"gen_export_template_{gid}",
        help="Storyboard and Teacher Guide add extra structure to DOCX/HTML exports.",
        disabled=not _can_export,
    ) if _can_export else "default"

    def _gen_export_req(fmt: str) -> ExportRequest:
        g = generation_repository.get_generation_by_id(db, gid)
        blks = generation_repository.list_blocks_for_generation(db, gid)
        return ExportRequest(
            fmt=fmt,
            topic=g.topic if g else f"Generation #{gid}",
            blocks=[(b.block_label, b.content) for b in blks],
            user_name=user_name,
            is_admin=_is_admin,
            entity_type="generation",
            entity_id=gid,
            project_id=_proj_id,
            course_id=_crs_id,
            template=_gen_tmpl_sel,
        )

    exp1, exp2, exp3, exp4, exp5 = col_ed2.columns(5)
    if exp1.button("💾 MD", use_container_width=True, disabled=not _can_export):
        _r = export_content(db, _gen_export_req("md"))
        if _r.success:
            st.download_button("⬇️ Download", _r.data, file_name=_r.file_name, mime=_r.mime_type)
        else:
            st.error(_r.error_message)
    if exp2.button("📦 JSON", use_container_width=True, disabled=not _can_export):
        _r = export_content(db, _gen_export_req("json"))
        if _r.success:
            st.download_button("⬇️ Download", _r.data, file_name=_r.file_name, mime=_r.mime_type)
        else:
            st.error(_r.error_message)
    if exp3.button("🌐 HTML", use_container_width=True, disabled=not _can_export):
        _r = export_content(db, _gen_export_req("html"))
        if _r.success:
            st.download_button("⬇️ Download", _r.data, file_name=_r.file_name, mime=_r.mime_type)
        else:
            st.error(_r.error_message)
    if exp4.button("📄 DOCX", use_container_width=True, disabled=not _can_export):
        _r = export_content(db, _gen_export_req("docx"))
        if _r.success:
            st.download_button("⬇️ Download", _r.data, file_name=_r.file_name, mime=_r.mime_type)
        else:
            st.error(_r.error_message)
    if exp5.button("📑 PDF", use_container_width=True, disabled=not _can_export):
        _r = export_content(db, _gen_export_req("pdf"))
        if _r.success:
            st.download_button("⬇️ Download", _r.data, file_name=_r.file_name, mime=_r.mime_type)
        else:
            st.warning(_r.error_message)

    blks = generation_repository.list_blocks_for_generation(db, gid)
    if not blks:
        st.info("💡 **Tip:** No blocks here yet. Go to the **Generate Course** tab, create content, and it will appear here for editing.")
    else:
        st.caption(f"📝 Showing {len(blks)} block(s) for Generation #{gid}")
        for b in blks:
            st.markdown(f"### {status_badge(b.workflow_state)} {b.block_label} *(Block #{b.id})*", unsafe_allow_html=True)
            
            # 📊 Dedicated Plagiarism & Citation Dashboard
            with st.expander("📊 Plagiarism & Citation Dashboard", expanded=True):
                d_col1, d_col2, d_col3 = st.columns(3)
                
                # Col 1: Plagiarism (AI Authenticity)
                with d_col1:
                    st.markdown("**🛡️ Plagiarism (AI Detect)**")
                    _plag_report = b.plagiarism_report or ""
                    _check_failed = (
                        "could not be completed" in _plag_report.lower()
                        or "evaluation error" in _plag_report.lower()
                        or "deferred" in _plag_report.lower()
                    )
                    if b.plagiarism_score is None:
                        if _check_failed:
                            st.markdown(
                                "<span style='color:#f59e0b;font-weight:700;'>⚠ Check failed</span>",
                                unsafe_allow_html=True,
                            )
                            st.caption("Detection backend unavailable.")
                        else:
                            st.caption("Not checked yet — click 'Check Content Authenticity'.")
                    else:
                        p_color = (
                            "#ef4444" if b.plagiarism_score > 70
                            else "#f59e0b" if b.plagiarism_score > 30
                            else "#10b981"
                        )
                        st.markdown(
                            f"<h2 style='color:{p_color}; margin:0;'>{b.plagiarism_score}%</h2>",
                            unsafe_allow_html=True,
                        )
                        st.caption("AI Likelihood Score")
                        if _plag_report:
                            st.info(_plag_report)

                # Col 2: Citations (RAG Sources)
                with d_col2:
                    st.markdown("**📚 Citations**")
                    if b.sources:
                        try:
                            src_list = json.loads(b.sources)
                            if src_list:
                                for s in src_list:
                                    st.markdown(f"• `{s}`")
                            else: st.caption("No sources cited.")
                        except: st.caption("No sources cited.")
                    else:
                        st.caption("No sources cited.")

                # Col 3: Quality & Evaluation
                with d_col3:
                    st.markdown("**📐 AI Evaluation**")
                    if b.eval_score is not None:
                        s_color = "#10b981" if b.eval_score > 80 else "#f59e0b" if b.eval_score > 50 else "#ef4444"
                        st.markdown(f"<h2 style='color:{s_color}; margin:0;'>{b.eval_score}/100</h2>", unsafe_allow_html=True)
                        try:
                            e_rep = json.loads(b.eval_report)
                            if e_rep.get("missing_sections"):
                                st.warning(f"Missing: {', '.join(e_rep['missing_sections'])}")
                        except: pass
                    else:
                        st.caption("No evaluation data yet.")
                
                st.divider()
                st.markdown("**🤖 Expert AI Review**")
                if b.ai_review:
                    st.markdown(b.ai_review)
                else:
                    st.caption("No AI review available. Save or Regenerate to trigger.")

            # ── Scope selectors for feedback signals ─────────────────────────
            _SCOPE_OPTS  = {"⚡ Apply Once": "one_time", "🧠 Use as Learning": "learning"}
            _SCOPE_LABEL = {
                "one_time": ("⚡", "#f59e0b", "#fffbeb", "Apply Once",
                             "Used for this block only. Not stored as a learning signal."),
                "learning": ("🧠", "#6366f1", "#eef2ff", "Use as Learning",
                             "Stored as a reusable signal to improve future generations."),
            }

            ed_left, ed_right = st.columns([0.5, 0.5])
            with ed_left:
                # ── AUTOSAVE SESSION-STATE KEYS ───────────────────────────
                _txt_key         = f"txt_{b.id}"
                _as_init_key     = f"_as_init_{b.id}"
                _as_recovery_key = f"_as_show_recovery_{b.id}"
                _as_dirty_key    = f"_as_dirty_{b.id}"
                _as_changed_key  = f"_as_changed_at_{b.id}"
                _as_saved_key    = f"_as_saved_at_{b.id}"
                _as_prev_key     = f"_as_prev_{b.id}"

                # ── DRAFT RECOVERY CHECK (once per session per block) ─────
                if not st.session_state.get(_as_init_key):
                    st.session_state[_as_init_key] = True
                    if autosave_service.has_recoverable_draft(b):
                        st.session_state[_as_recovery_key] = True

                if st.session_state.get(_as_recovery_key):
                    _draft_age = autosave_service.draft_age_label(b)
                    st.markdown(
                        f"<div style='background:#fef9c3;border:1px solid #fde047;"
                        f"border-radius:8px;padding:10px 14px;margin-bottom:8px;'>"
                        f"<div style='font-weight:700;font-size:0.85rem;"
                        f"color:#713f12;margin-bottom:4px;'>"
                        f"📂 Unsaved draft found ({_draft_age})</div>"
                        f"<div style='font-size:0.78rem;color:#92400e;'>"
                        f"A draft was autosaved before your last session ended. "
                        f"Restore it to continue where you left off, or dismiss to keep "
                        f"the current saved version.</div></div>",
                        unsafe_allow_html=True,
                    )
                    _dr1, _dr2 = st.columns(2)
                    if _dr1.button(
                        "📂 Restore Draft", key=f"restore_draft_{b.id}",
                        use_container_width=True,
                    ):
                        st.session_state[_txt_key]      = b.draft_content
                        st.session_state[_as_recovery_key] = False
                        st.session_state[_as_dirty_key]    = True
                        st.rerun()
                    if _dr2.button(
                        "✕ Dismiss", key=f"dismiss_draft_{b.id}",
                        use_container_width=True,
                    ):
                        st.session_state[_as_recovery_key] = False
                        st.rerun()

                # ── EDIT AREA ─────────────────────────────────────────────
                _orig_content = b.content or ""
                new_cnt = st.text_area(
                    "✏️ Edit Block (Markdown)",
                    _orig_content,
                    key=_txt_key,
                    height=360,
                    help="Edit the block content directly. You can attach a reason below before saving."
                )
                _content_changed = new_cnt.strip() != _orig_content.strip()

                # ── AUTOSAVE TRACKING ─────────────────────────────────────
                # Detect intra-rerun change to reset the debounce clock.
                _now_utc  = datetime.now(timezone.utc)
                _as_prev  = st.session_state.get(_as_prev_key, new_cnt)
                if new_cnt != _as_prev:
                    st.session_state[_as_prev_key]    = new_cnt
                    st.session_state[_as_changed_key] = _now_utc
                    if new_cnt.strip() != _orig_content.strip():
                        st.session_state[_as_dirty_key] = True

                _as_dirty      = st.session_state.get(_as_dirty_key, False)
                _as_changed_at = st.session_state.get(_as_changed_key)
                _as_saved_at   = st.session_state.get(_as_saved_key)
                _as_locked     = autosave_service.is_locked(b)

                # Fire autosave when: dirty + quiet ≥10s + block not locked
                if (
                    _as_dirty and not _as_locked and
                    _as_changed_at is not None and
                    (_now_utc - _as_changed_at).total_seconds()
                        >= autosave_service.AUTOSAVE_INTERVAL_SECONDS
                ):
                    autosave_service.save_draft(db, b, new_cnt, user_name)
                    _as_saved_at = _now_utc
                    st.session_state[_as_saved_key] = _as_saved_at
                    st.session_state[_as_dirty_key] = False
                    _as_dirty = False

                # ── IMPLICIT FEEDBACK (direct edit) ───────────────────────
                if _content_changed:
                    st.markdown(
                        "<div style='background:#fff7ed;border:1px solid #fed7aa;"
                        "border-radius:8px;padding:8px 12px;font-size:0.82rem;color:#92400e;"
                        "margin:4px 0 6px 0;'>✏️ <strong>You've edited this block.</strong> "
                        "Optionally describe why and choose how to use this feedback.</div>",
                        unsafe_allow_html=True
                    )
                    edit_reason = st.text_input(
                        "Why did you make this edit? (optional)",
                        placeholder="e.g. Too wordy, needed a professional tone, missing examples",
                        key=f"edit_reason_{b.id}"
                    )
                    edit_scope_label = st.radio(
                        "Feedback scope for this edit:",
                        list(_SCOPE_OPTS.keys()),
                        horizontal=True,
                        key=f"edit_scope_{b.id}",
                        help="Apply Once: used for this block only. Use as Learning: stored as a reusable signal."
                    )
                    edit_scope = _SCOPE_OPTS[edit_scope_label]
                    _icon, _fg, _bg, _lbl, _tip = _SCOPE_LABEL[edit_scope]
                    st.markdown(
                        f"<div style='background:{_bg};border:1px solid {_fg}33;"
                        f"border-radius:6px;padding:5px 10px;font-size:0.78rem;color:{_fg};'>"
                        f"{_icon} <strong>{_lbl}</strong> — {_tip}</div>",
                        unsafe_allow_html=True
                    )
                else:
                    edit_reason = ""
                    edit_scope  = "one_time"

                # ── SAVE EDIT button ──────────────────────────────────────
                if st.button("💾 Save Edit", key=f"btn_{b.id}", use_container_width=True,
                             help="Save your changes to this block."):
                    _gen_for_fb = generation_repository.get_generation_by_id(db, b.generation_id)
                    original_before_save = _orig_content
                    b.content = new_cnt
                    b.updated_at = datetime.now(timezone.utc)
                    plagi_res, eval_data, ai_rev_text = get_initial_quality_metadata(new_cnt, b.block_type)
                    # Only persist a real plagiarism score; check_error=True means
                    # both backends failed and the value (0) is not meaningful.
                    if not plagi_res.get("check_error"):
                        b.plagiarism_score  = plagi_res.get("confidence_score")
                        b.plagiarism_report = plagi_res.get("explanation")
                    b.eval_score  = eval_data.get("structural_score", 0)
                    b.eval_report = json.dumps(eval_data)
                    b.ai_review   = ai_rev_text
                    db.commit()

                    # Store feedback signal
                    if _content_changed:
                        db.add(FeedbackSignal(
                            block_id=b.id,
                            generation_id=b.generation_id,
                            prompt_name=_gen_for_fb.prompt_name if _gen_for_fb else None,
                            block_type=b.block_type,
                            signal_source="edit",
                            feedback_scope=edit_scope,
                            original_content=original_before_save,
                            final_content=new_cnt,
                            edit_reason=edit_reason or None,
                            topic=_gen_for_fb.topic if _gen_for_fb else None,
                            author=user_name,
                        ))
                        db.commit()
                        _scope_emoji = "🧠" if edit_scope == "learning" else "⚡"
                        log_audit_event(db, user_name, "content.edited", entity_type="block",
                                        entity_id=b.id, project_id=_proj_id, course_id=_crs_id,
                                        metadata={"block_label": b.block_label, "reason": edit_reason or None,
                                                  "scope": edit_scope})
                        st.toast(f"✅ Saved  {_scope_emoji} Feedback recorded as {'Learning' if edit_scope == 'learning' else 'One-time'}")
                    else:
                        st.toast("✅ Changes Saved & Quality Score Refreshed")
                    # Clear autosave draft — manual save supersedes it.
                    autosave_service.clear_draft(db, b)
                    st.session_state.pop(_as_dirty_key, None)
                    st.session_state.pop(_as_saved_key, None)
                    _as_dirty    = False
                    _as_saved_at = None

                # ── SAVE STATUS INDICATOR ─────────────────────────────────
                _as_status = autosave_service.status_html(
                    dirty=_as_dirty,
                    saved_at=_as_saved_at,
                    locked=_as_locked,
                )
                if _as_status:
                    st.markdown(_as_status, unsafe_allow_html=True)

                # ── SUBMIT FOR REVIEW (quick action from Editor) ──────────────
                if b.workflow_state.lower() in (
                    WorkflowState.DRAFT, WorkflowState.CHANGES_REQUESTED, WorkflowState.REJECTED
                ):
                    if rbac_check(user_role, "workflow.submit"):
                        _gen_for_submit = generation_repository.get_generation_by_id(db, b.generation_id)
                        _can_submit_this = (
                            _is_admin or _is_lead or
                            (_gen_for_submit and _gen_for_submit.created_by == user_name)
                        )
                        if _can_submit_this:
                            st.markdown(
                                "<div style='background:#f0fdf4;border:1px solid #bbf7d0;"
                                "border-radius:8px;padding:8px 12px;margin:8px 0 4px 0;'>"
                                "<span style='font-size:0.75rem;font-weight:700;"
                                "text-transform:uppercase;letter-spacing:.06em;color:#15803d;'>"
                                "📤 Submit for Review</span></div>",
                                unsafe_allow_html=True,
                            )
                            _reviewers_ed = user_repository.list_reviewers_and_admins(db)
                            _rev_names_ed = [u.username for u in _reviewers_ed]
                            if _rev_names_ed:
                                with st.form(f"ed_submit_{b.id}"):
                                    _sel_rev_ed = st.selectbox(
                                        "Assign Reviewer",
                                        _rev_names_ed,
                                        key=f"ed_rev_sel_{b.id}",
                                        label_visibility="collapsed",
                                    )
                                    if st.form_submit_button(
                                        "📤 Submit for Review", use_container_width=True
                                    ):
                                        _ok_s, _err_s = submit_for_review(
                                            db, b, _sel_rev_ed, user_name
                                        )
                                        if _ok_s:
                                            log_audit_event(
                                                db, user_name, "workflow.submitted",
                                                entity_type="block", entity_id=b.id,
                                                project_id=_proj_id, course_id=_crs_id,
                                                metadata={"block_label": b.block_label,
                                                          "reviewer": _sel_rev_ed},
                                            )
                                            st.toast(f"✅ Block #{b.id} submitted to {_sel_rev_ed}")
                                            st.rerun()
                                        else:
                                            st.error(_err_s)
                            else:
                                st.caption("No reviewers available — ask an Admin to add reviewers.")

                st.markdown(
                    "<div style='"
                    "display:flex;align-items:center;gap:8px;"
                    "margin-top:14px;margin-bottom:2px;"
                    "padding:8px 12px;"
                    "background:linear-gradient(90deg,#eef2ff 0%,#f5f3ff 100%);"
                    "border-left:3px solid #6366f1;"
                    "border-radius:0 6px 6px 0;"
                    "'>"
                    "<span style='font-size:1rem;'>✨</span>"
                    "<span style='"
                    "font-size:0.8rem;font-weight:700;text-transform:uppercase;"
                    "letter-spacing:.07em;color:#4338ca;"
                    "'>Regenerate / Improvise Prompt</span>"
                    "<span style='"
                    "font-size:0.72rem;font-weight:400;color:#6b7280;text-transform:none;letter-spacing:0;"
                    "'>— Add instructions below to guide the AI</span>"
                    "</div>",
                    unsafe_allow_html=True
                )

                st.divider()

                # ── REGENERATE / IMPROVISE ────────────────────────────────
                st.markdown(
                    "<div style='font-size:0.75rem;font-weight:700;text-transform:uppercase;"
                    "letter-spacing:.08em;color:#6366f1;margin-bottom:6px;'>🔄 Regenerate / Improvise</div>",
                    unsafe_allow_html=True
                )
                if b.workflow_state.lower() != "draft":
                    st.caption("ℹ️ Move this block back to Draft in Workflow to regenerate.")

                improvise_prompt = st.text_area(
                    "Instruction",
                    placeholder="e.g. Improve flow, add real-world examples, simplify for beginners, rewrite as a quiz.",
                    key=f"improvise_prompt_{b.id}",
                    height=72,
                    label_visibility="collapsed"
                )
                regen_scope_label = st.radio(
                    "Feedback scope:",
                    list(_SCOPE_OPTS.keys()),
                    horizontal=True,
                    key=f"regen_scope_{b.id}",
                    help="Apply Once: used for this regeneration only. Use as Learning: stored for future improvements."
                )
                regen_scope = _SCOPE_OPTS[regen_scope_label]
                _icon, _fg, _bg, _lbl, _tip = _SCOPE_LABEL[regen_scope]
                st.markdown(
                    f"<div style='background:{_bg};border:1px solid {_fg}33;"
                    f"border-radius:6px;padding:5px 10px;font-size:0.78rem;color:{_fg};margin-bottom:6px;'>"
                    f"{_icon} <strong>{_lbl}</strong> — {_tip}</div>",
                    unsafe_allow_html=True
                )

                # ── Per-item regeneration panel ─────────────────────────────────
                _ed_items = parse_items_from_section(b.content or "")
                if _ed_items:
                    with st.expander(
                        f"🎯 Regenerate a single item  ({len(_ed_items)} items found)",
                        expanded=False
                    ):
                        st.markdown(
                            "<div style='font-size:0.78rem;color:#6b7280;margin-bottom:6px;'>"
                            "Click ⟳ next to any item to regenerate <strong>only that item</strong>. "
                            "All siblings are preserved exactly.</div>",
                            unsafe_allow_html=True
                        )
                        _ed_ri = st.text_input(
                            "Instruction",
                            placeholder="e.g. Make more specific, add a real-world example",
                            key=f"ed_item_inst_{b.id}",
                            label_visibility="collapsed"
                        )
                        _ed_is_lbl = st.radio(
                            "Scope",
                            ["⚡ Apply Once", "🧠 Use as Learning"],
                            horizontal=True,
                            key=f"ed_item_scope_{b.id}",
                            label_visibility="collapsed"
                        )
                        _ed_is = "one_time" if "Once" in _ed_is_lbl else "learning"
                        _gfi = generation_repository.get_generation_by_id(db, b.generation_id)
                        _ed_ls = ""
                        if _ed_is == "learning":
                            _ed_pr = generation_repository.list_learning_signals(db, b.block_type)
                            _ed_st = "\n".join(
                                f"- {s.user_instruction or s.edit_reason}"
                                for s in _ed_pr if (s.user_instruction or s.edit_reason)
                            )
                            if _ed_st:
                                _ed_ls = f"\n\nLEARNED PREFERENCES:\n{_ed_st}"
                        for _ei, _eitem in enumerate(_ed_items):
                            _ec1, _ec2 = st.columns([0.85, 0.15])
                            _esh = _eitem["text"][:75] + ("..." if len(_eitem["text"]) > 75 else "")
                            _ec1.markdown(
                                f"<div style='font-size:0.8rem;padding:3px 0;color:#374151;'>"
                                f"<code style='background:#eef2ff;color:#4338ca;padding:1px 4px;"
                                f"border-radius:3px;font-size:0.7rem;'>{_ei+1}</code> "
                                f"{_esh}</div>",
                                unsafe_allow_html=True
                            )
                            if _ec2.button(
                                "⟳",
                                key=f"ed_ir_{b.id}_{_ei}",
                                help=f"Regenerate item {_ei+1} only. All other items unchanged.",
                                use_container_width=True
                            ):
                                with st.spinner(f"Regenerating item {_ei+1}…"):
                                    _new_ei = regen_single_item(
                                        section_title=b.block_label or b.block_type,
                                        section_content=b.content or "",
                                        item_index=_ei,
                                        item_text=_eitem["text"],
                                        custom_instruction=_ed_ri,
                                        model_choice=st.session_state.get("model_choice", "GPT-5.4"),
                                        learning_signals=_ed_ls,
                                    )
                                    _pat_ed = patch_item_in_section(b.content or "", _ei, _new_ei)
                                    _orig_ed = b.content
                                    b.content    = _pat_ed
                                    b.updated_at = datetime.now(timezone.utc)
                                    db.commit()
                                    db.add(FeedbackSignal(
                                        block_id=b.id,
                                        generation_id=b.generation_id,
                                        prompt_name=_gfi.prompt_name if _gfi else None,
                                        block_type=b.block_type,
                                        signal_source="regenerate",
                                        feedback_scope=_ed_is,
                                        original_content=_eitem["text"],
                                        final_content=_new_ei,
                                        user_instruction=_ed_ri or "(item regenerated)",
                                        topic=_gfi.topic if _gfi else None,
                                        author=user_name,
                                    ))
                                    db.commit()
                                    _se2 = "🧠" if _ed_is == "learning" else "⚡"
                                    st.toast(f"✅ Item {_ei+1} regenerated {_se2}. Others unchanged.")
                                    st.rerun()

                st.markdown(
                    "<div style='font-size:0.72rem;color:#9ca3af;margin-bottom:4px;'>"
                    "— or regenerate the full block below —</div>",
                    unsafe_allow_html=True
                )
                if st.button("🔄 Regenerate", key=f"reg_{b.id}", use_container_width=True,
                             type="primary",
                             help="Regenerate this block using the instruction above."):
                    model_choice = st.session_state.model_choice
                    _gen_regen = generation_repository.get_generation_by_id(db, b.generation_id)
                    _prompt    = prompt_repository.get_prompt_by_name(db, _gen_regen.prompt_name)
                    _ver       = prompt_repository.get_prompt_version(db, _prompt.id, _gen_regen.prompt_version)

                    # Build learning-augmented system prompt if scope == learning
                    _learning_signals = ""
                    if regen_scope == "learning":
                        _prior = generation_repository.list_learning_signals(db, b.block_type)
                        if _prior:
                            _sigs = "\n".join(
                                f"- {s.user_instruction or s.edit_reason}"
                                for s in _prior if (s.user_instruction or s.edit_reason)
                            )
                            if _sigs:
                                _learning_signals = (
                                    f"\n\nLEARNED PREFERENCES (apply these in addition to the instruction below):\n{_sigs}"
                                )

                    persona_prefix = PERSONA_PREFIX_TEMPLATE.format(
                        expert_exp=st.session_state.get("expert_exp", 20),
                        expert_domain=st.session_state.get("expert_domain", "Nursing"),
                        aud_cat=st.session_state.get("sidebar_aud_cat", "Professional/Corporate"),
                        target_audience=st.session_state.get("target_audience", "")
                    )
                    system_p = persona_prefix + _ver.system_prompt + _learning_signals
                    improvise_instruction = improvise_prompt.strip() if improvise_prompt else IMPROVISE_DEFAULT_REQUEST
                    user_p = IMPROVISE_BLOCK_PROMPT_TEMPLATE.format(
                        topic=_gen_regen.topic,
                        block_type=b.block_type,
                        improvise_instruction=improvise_instruction,
                        original_content=b.content,
                    )

                    with st.spinner("Regenerating…"):
                        _regen_orig = b.content
                        new_out = call_llm(model_choice, system_p, user_p, usage_ctx=UsageLogContext(
                            user_name=user_name,
                            project_id=ctx.project_id,
                            course_id=ctx.course_id,
                            entity_type="regen",
                            entity_id=str(b.id),
                        ))
                        if not new_out.startswith("ERROR"):
                            b.content = new_out
                            b.updated_at = datetime.now(timezone.utc)
                            plagi_res, eval_data, ai_rev_text = get_initial_quality_metadata(new_out, b.block_type)
                            if not plagi_res.get("check_error"):
                                b.plagiarism_score  = plagi_res.get("confidence_score")
                                b.plagiarism_report = plagi_res.get("explanation")
                            b.eval_score  = eval_data.get("structural_score", 0)
                            b.eval_report = json.dumps(eval_data)
                            b.ai_review   = ai_rev_text
                            db.commit()

                            # Store feedback signal
                            db.add(FeedbackSignal(
                                block_id=b.id,
                                generation_id=b.generation_id,
                                prompt_name=_gen_regen.prompt_name,
                                block_type=b.block_type,
                                signal_source="regenerate",
                                feedback_scope=regen_scope,
                                original_content=_regen_orig,
                                final_content=new_out,
                                user_instruction=improvise_instruction,
                                topic=_gen_regen.topic,
                                author=user_name,
                            ))
                            db.commit()
                            _scope_emoji = "🧠" if regen_scope == "learning" else "⚡"
                            st.toast(f"✅ Regenerated  {_scope_emoji} {'Learning signal saved' if regen_scope == 'learning' else 'One-time only'}")
                            st.rerun()
                        else:
                            st.error(new_out)

            with ed_right:
                st.caption("Live Preview")
                st.markdown(
                    f"<div style='border:1px solid #e4e7ef;border-radius:10px;"
                    f"padding:16px;height:420px;overflow-y:auto;background:#fafbff;'>"
                    f"{new_cnt}</div>",
                    unsafe_allow_html=True
                )

            # Option 2: Reviewer Comment
            with st.expander(f"📝 Reviewer Comment (Block #{b.id})"):
                rev1, rev2 = st.columns([0.5, 0.5])
                with rev1:
                    st.caption("📝 **Submit Formal Review**")
                    with st.form(f"review_form_{b.id}"):
                        rev_score = st.slider("Quality Score", 1, 5, 3, key=f"rev_score_{b.id}")
                        rev_approved = st.checkbox("Approve this block", key=f"rev_appr_{b.id}")
                        rev_comments = st.text_area("Review Comments", placeholder="Describe strengths, weaknesses, and suggestions...", key=f"rev_com_{b.id}")
                        if st.form_submit_button("✅ Submit Review", use_container_width=True):
                            review = Review(
                                generation_id=b.generation_id,
                                block_id=b.id,
                                reviewer=user_name,
                                reviewer_role=role_label(user_role),
                                score=rev_score,
                                approved=rev_approved,
                                comments=rev_comments
                            )
                            db.add(review)
                            b.rating = rev_score
                            b.reviewer_comment = rev_comments
                            db.commit()
                            log_event(db, "review", user_name, f"Reviewed Block #{b.id} (score={rev_score}, approved={rev_approved})")
                            st.toast("✅ Review submitted and recorded!")

                    # Show existing reviews
                    past_reviews = generation_repository.list_reviews_for_block(db, b.id)
                    if past_reviews:
                        st.caption(f"📜 **{len(past_reviews)} Review(s) on record:**")
                        for pr in past_reviews:
                            approval_badge = "✅ Approved" if pr.approved else "❌ Not Approved"
                            st.markdown(f"**{pr.reviewer}** ({pr.reviewer_role}) — Score: {'⭐' * (pr.score or 0)} — {approval_badge}")
                            if pr.comments:
                                st.caption(pr.comments)
                with rev2:
                    st.caption("🎯 AI Quality Analysis")
                    if st.button("🤖 Run AI Evaluation", key=f"ai_eval_{b.id}", use_container_width=True):
                        with st.spinner("AI is analyzing your content..."):
                            quality = score_content_quality(b.content)
                        st.metric("Overall Grade", quality.get("grade", "N/A"), delta=f"{quality.get('total_score', 0)}/100")
                        st.progress(min(quality.get("total_score", 50), 100) / 100)
                        qm1, qm2 = st.columns(2)
                        qm1.metric("Structure", f"{quality.get('structure', 0)}/30")
                        qm2.metric("Depth", f"{quality.get('depth', 0)}/30")
                        qm3, qm4 = st.columns(2)
                        qm3.metric("Engagement", f"{quality.get('engagement', 0)}/30")
                        qm4.metric("Readability", f"{quality.get('readability', 0)}/25")
                        if quality.get("suggestions"):
                            st.info(f"💡 **AI Suggestions:** {quality['suggestions']}")

                    if st.button("📝 Request AI Review", key=f"ai_rev_{b.id}", use_container_width=True):
                        with st.spinner("AI reviewer is writing feedback..."):
                            review_text = llm_evaluate_block(b.content, b.block_type)
                        st.markdown(review_text)

                    if st.button("🤖 Check Content Authenticity", key=f"ai_plag_{b.id}", use_container_width=True):
                        with st.spinner("Running AI-content detection (LLM analysis)…"):
                            plag_res = check_plagiarism_content(b.content)

                        if plag_res.get("check_error"):
                            # Both LLM and HuggingFace backends failed — do NOT show
                            # a false "0% / human-written" result; show a clear error.
                            st.warning(
                                "⚠️ **Plagiarism check could not be completed.** "
                                "The detection backend returned an error. "
                                "Check server logs for details."
                            )
                            st.caption(
                                "Possible causes: LLM API key missing / rate-limited, "
                                "or `transformers`/`torch` not installed for the local fallback."
                            )
                            st.info(plag_res.get("explanation", "No detail available."))
                        else:
                            score = plag_res.get("confidence_score", 0)
                            if plag_res.get("is_plagiarized"):
                                st.error(
                                    f"⚠️ **High AI-content likelihood** — "
                                    f"{score}% probability of AI-generated content."
                                )
                            else:
                                st.success(
                                    f"✅ **Likely original / human-written** — "
                                    f"AI likelihood: {score}%"
                                )

                            st.markdown("**Analysis:**")
                            st.info(plag_res.get("explanation", "No explanation provided."))

                            if plag_res.get("raw_data"):
                                st.markdown("**Raw Detection Data:**")
                                st.json(plag_res["raw_data"], expanded=False)

                            # Persist the result so the dashboard reflects the latest check.
                            b.plagiarism_score  = plag_res.get("confidence_score")
                            b.plagiarism_report = plag_res.get("explanation")
                            db.commit()
                            st.rerun()

            # ── Version History ───────────────────────────────────────────────
            with st.expander(f"⏱️ Version History — Block #{b.id}", expanded=False):
                _bv_list = get_block_versions(db, b.id)
                if not _bv_list:
                    st.caption(
                        "No snapshots yet. Click **Save Snapshot** below to capture "
                        "the current content, or use Regenerate to auto-save."
                    )
                else:
                    _bv_rows = [
                        {
                            "v#":     f"v{_v.version_num}",
                            "When":   _v.created_at.strftime("%Y-%m-%d %H:%M") if _v.created_at else "—",
                            "By":     _v.created_by or "—",
                            "Source": _v.change_source or "—",
                            "Words":  _v.word_count or 0,
                            "State":  _v.workflow_state_at_save or "—",
                            "Note":   (_v.change_note or "")[:50],
                        }
                        for _v in _bv_list
                    ]
                    st.dataframe(_bv_rows, use_container_width=True, hide_index=True)

                    if rbac_check(user_role, "editor.edit") and len(_bv_list) > 0:
                        _restore_opts = {
                            f"v{_v.version_num} — {_v.change_source} "
                            f"({_v.created_at.strftime('%m-%d %H:%M') if _v.created_at else '?'})": _v.id
                            for _v in _bv_list
                        }
                        _sel_ver = st.selectbox(
                            "Restore content to version",
                            options=list(_restore_opts.keys()),
                            key=f"restore_ver_{b.id}",
                        )
                        if st.button(
                            "⏪ Restore Selected Version",
                            key=f"restore_btn_{b.id}",
                            help="Saves current content as a snapshot, then restores the selected version.",
                        ):
                            _restored, _err = restore_block_version(
                                db, b, _restore_opts[_sel_ver], user_name
                            )
                            if _err:
                                st.error(_err)
                            else:
                                st.toast(f"✅ Block #{b.id} restored to {_sel_ver}")
                                st.rerun()

                # Manual snapshot button (always shown)
                if rbac_check(user_role, "editor.edit"):
                    _snap_note = st.text_input(
                        "Snapshot note (optional)",
                        placeholder="e.g. 'Before client review'",
                        key=f"snap_note_{b.id}",
                    )
                    if st.button(
                        "💾 Save Snapshot",
                        key=f"snap_btn_{b.id}",
                        help="Capture the current block content as a named version.",
                    ):
                        save_block_version(
                            db, b,
                            change_source="manual_snapshot",
                            change_note=_snap_note or "Manual snapshot",
                            created_by=user_name,
                        )
                        st.toast(f"✅ Snapshot v{b.version_num} saved for Block #{b.id}")
                        st.rerun()

            st.divider()

