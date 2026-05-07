"""Generate page renderer.

Extracted from the legacy Streamlit monolith so each page can be maintained independently.
"""

import json
import re
import time
import streamlit as st
from concurrent.futures import ThreadPoolExecutor

from promptops_app.database import GenerationJob
from promptops_app.jobs import generation_jobs, job_runner
from promptops_app.jobs.job_status import JobStatus
from promptops_app.repositories import job_repository
from promptops_app.ui.job_progress import render_active_job

from promptops_app.prompt_templates import (
    PERSONA_PREFIX_TEMPLATE,
    LESSON_WITH_CONTEXT_SYSTEM,
    LESSON_WITH_CONTEXT_USER,
)
from promptops_app.database import (
    Block, CourseDesignDocument, Document, Generation, ModuleBlueprint,
    Prompt, PromptVersion,
    get_active_cdd_version, get_active_blueprint_version,
    get_active_style, build_style_context, resolve_document_references, log_event,
)
from promptops_app.auth.permissions import rbac_gate
from promptops_app.core.llm_client import call_llm, safe_json_loads
from promptops_app.core.shared import (
    build_context_injection,
    split_into_blocks,
    make_source_context,
    trim_generation_context,
    render_completion_gate,
    render_course_completion_gate,
    get_module_completion_status,
    get_all_modules_completion,
    get_cached_prompt_names,
    get_cached_active_document_names,
)
from promptops_app.parsers.blueprint_parser import (
    parse_blueprint_components,
    build_component_generation_prompt,
    is_component_type_module_level,
    is_component_type_course_level,
)
from promptops_app.parsers.file_parser import _parse_uploaded_file
from promptops_app.services.evaluation_service import get_initial_quality_metadata
from promptops_app.repositories import (
    blueprint_repository, cdd_repository, document_repository, prompt_repository,
)
from promptops_app.ui.components import fill_template, _section_badge
from promptops_app.ui.prompt_panel import render_prompt_panel
from promptops_app.ui.generation_controls import render_inline_prompt_controls, render_prompt_download_button
from promptops_app.ui.user_prompt_widget import auto_save_instructions


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
    st.markdown(_section_badge("⚙️", "Content Generation",
        "AI pipeline for generating lessons and course components. "
        "Context is auto-injected from the active CDD and Blueprint."),
        unsafe_allow_html=True)

    # ── Active background job tracker ────────────────────────────────────
    # Delegated to the reusable ui/job_progress component.
    # render_active_job() returns True while the job is still active and
    # handles its own polling loop (sleep + rerun) internally.
    _active_job_id = st.session_state.get("active_gen_job_id")
    if _active_job_id:
        _still_active = render_active_job(db, _active_job_id)
        if _still_active:
            return   # polling loop inside render_active_job handles rerun

    # ── Sequential workflow progress stepper ─────────────────────────────
    _s_cdd = bool(st.session_state.get("active_cdd_id"))
    _s_bp  = bool(st.session_state.get("active_blueprint_id"))
    _steps = [
        ("📘", "Pin CDD",       _s_cdd),
        ("🧩", "Pin Blueprint", _s_bp),
        ("⚙️", "Generate",      _s_cdd and _s_bp),
        ("✏️", "Edit",          False),
    ]
    _step_html = "<div style='display:flex;gap:0;align-items:center;margin-bottom:14px;'>"
    for _si, (_sico, _slbl, _sdone) in enumerate(_steps):
        _sfg  = "#16a34a" if _sdone else "#9ca3af"
        _sbg  = "#f0fdf4" if _sdone else "#f9fafb"
        _sbrd = "#86efac" if _sdone else "#e5e7eb"
        _step_html += (
            f"<div style='flex:1;background:{_sbg};border:1px solid {_sbrd};"
            f"border-radius:8px;padding:7px 10px;margin:0 3px;text-align:center;'>"
            f"<div style='font-size:1rem;'>{_sico}</div>"
            f"<div style='font-size:0.72rem;font-weight:700;color:{_sfg};'>{_slbl}</div>"
            f"<div style='font-size:0.65rem;color:{_sfg};'>{'✓' if _sdone else '○'}</div>"
            f"</div>"
        )
        if _si < len(_steps) - 1:
            _step_html += "<div style='color:#d1d5db;font-size:1.2rem;padding:0 2px;'>→</div>"
    _step_html += "</div>"
    st.markdown(_step_html, unsafe_allow_html=True)

    # ── CDD + Blueprint Context Banner ─────────────────────────────────────
    active_cdd_id    = st.session_state.get("active_cdd_id")
    active_bp_id     = st.session_state.get("active_blueprint_id")

    active_cdd_obj   = cdd_repository.get_cdd_by_id(db, active_cdd_id) if active_cdd_id else None
    active_bp_obj    = blueprint_repository.get_blueprint_by_id(db, active_bp_id) if active_bp_id else None

    cdd_label_disp   = f"{active_cdd_obj.title} ({active_cdd_obj.active_version})" if active_cdd_obj else "None linked"
    bp_label_disp    = f"{active_bp_obj.title} ({active_bp_obj.active_version})" if active_bp_obj else "None linked"
    _cdd_ok, _bp_ok  = bool(active_cdd_obj), bool(active_bp_obj)
    _cdd_color       = "#10b981" if _cdd_ok else "#ef4444"
    _bp_color        = "#10b981" if _bp_ok  else "#ef4444"

    # Active style display
    _active_style_gen_disp = get_active_style(db, project_id=_proj_id, course_id=_crs_id)
    _sty_disp_label = _active_style_gen_disp.name if _active_style_gen_disp else "None"
    _sty_disp_color = "#10b981" if _active_style_gen_disp else "#ef4444"

    st.markdown(
        f"""<div style='background:#ffffff;border:1px solid #e4e7ef;border-radius:12px;
                        padding:14px 20px;margin-bottom:16px;
                        display:flex;gap:0;align-items:stretch;'>
            <div style='flex:1;padding-right:20px;'>
                <div style='font-size:0.7rem;font-weight:600;text-transform:uppercase;
                            letter-spacing:.08em;color:#6b7280;margin-bottom:4px;'>📘 Active CDD</div>
                <div style='display:flex;align-items:center;gap:6px;'>
                    <span style='width:8px;height:8px;border-radius:50%;
                                 background:{_cdd_color};flex-shrink:0;'></span>
                    <strong style='color:#111827;font-size:0.9rem;'>{cdd_label_disp}</strong>
                </div>
            </div>
            <div style='width:1px;background:#e4e7ef;margin:0 20px;'></div>
            <div style='flex:1;padding-left:4px;'>
                <div style='font-size:0.7rem;font-weight:600;text-transform:uppercase;
                            letter-spacing:.08em;color:#6b7280;margin-bottom:4px;'>🧩 Active Blueprint</div>
                <div style='display:flex;align-items:center;gap:6px;'>
                    <span style='width:8px;height:8px;border-radius:50%;
                                 background:{_bp_color};flex-shrink:0;'></span>
                    <strong style='color:#111827;font-size:0.9rem;'>{bp_label_disp}</strong>
                </div>
            </div>
            <div style='width:1px;background:#e4e7ef;margin:0 20px;'></div>
            <div style='flex:1;padding-left:4px;'>
                <div style='font-size:0.7rem;font-weight:600;text-transform:uppercase;
                            letter-spacing:.08em;color:#6b7280;margin-bottom:4px;'>🎨 Active Style</div>
                <div style='display:flex;align-items:center;gap:6px;'>
                    <span style='width:8px;height:8px;border-radius:50%;
                                 background:{_sty_disp_color};flex-shrink:0;'></span>
                    <strong style='color:#111827;font-size:0.9rem;'>{_sty_disp_label}</strong>
                </div>
            </div>
            <div style='margin-left:auto;align-self:center;padding-left:20px;
                        font-size:0.78rem;color:#9ca3af;white-space:nowrap;'>
                Set via Style / CDD / Blueprint tabs
            </div>
        </div>""",
        unsafe_allow_html=True
    )

    # ── Optional Override ─────────────────────────────────────────────────
    # Pre-build override options outside the expander so eff_cdd_id/eff_bp_id
    # are always in scope regardless of whether the expander is open.
    all_cdds_gen = cdd_repository.list_all_cdds(db)
    all_bps_gen  = blueprint_repository.list_all_blueprints(db)
    cdd_ovr_opts = {"— Use pinned CDD —": None}
    cdd_ovr_opts.update({f"{c.title} ({c.active_version})": c.id for c in all_cdds_gen})
    bp_ovr_opts  = {"— Use pinned Blueprint —": None}
    bp_ovr_opts.update({f"{b.title} ({b.active_version})": b.id for b in all_bps_gen})

    with st.expander("🔀 Override Active CDD / Blueprint (optional)", expanded=False):
        st.caption("By default the pinned CDD & Blueprint are used. Change here for this generation only — does not affect the pin.")
        oc1, oc2 = st.columns(2)
        cdd_override_sel = oc1.selectbox("CDD Override", list(cdd_ovr_opts.keys()), key="gen_cdd_override")
        bp_override_sel  = oc2.selectbox("Blueprint Override", list(bp_ovr_opts.keys()), key="gen_bp_override")

    # Resolve effective IDs — reads widget state if the user made a selection
    _cdd_override_val = st.session_state.get("gen_cdd_override", "— Use pinned CDD —")
    _bp_override_val  = st.session_state.get("gen_bp_override",  "— Use pinned Blueprint —")
    eff_cdd_id = cdd_ovr_opts.get(_cdd_override_val) or active_cdd_id
    eff_bp_id  = bp_ovr_opts.get(_bp_override_val)  or active_bp_id

    # ── Load Blueprint components for dynamic dropdown ────────────────────
    _bp_ver_for_gen = get_active_blueprint_version(db, eff_bp_id) if eff_bp_id else None
    _blueprint_components = parse_blueprint_components(_bp_ver_for_gen) if _bp_ver_for_gen else []

    # If blueprint is pinned but structured parsing returned nothing,
    # build components from raw section keys — lessons and module assessment only.
    if eff_bp_id and _bp_ver_for_gen and not _blueprint_components:
        _raw_sections = safe_json_loads(_bp_ver_for_gen.sections) if _bp_ver_for_gen.sections else {}
        for _sk, _sv in _raw_sections.items():
            sk_lower = _sk.lower()
            if "lesson" in sk_lower:
                _blueprint_components.append({
                    "label":    _sk,
                    "value":    re.sub(r"[^a-z0-9]+", "_", sk_lower).strip("_")[:50],
                    "type":     "lesson",
                    "metadata": {"blueprint_section": _sk},
                })
            elif "module assessment" in sk_lower or "module-level assessment" in sk_lower:
                _blueprint_components.append({
                    "label":    _sk,
                    "value":    "module_assessment",
                    "type":     "assessment",
                    "metadata": {"blueprint_section": _sk},
                })

    if _blueprint_components:
        _comp_labels = [c["label"] for c in _blueprint_components]
        _comp_map    = {c["label"]: c for c in _blueprint_components}
        st.markdown(
            f"<div style='background:#f0fdf4;border:1px solid #a7f3d0;border-radius:8px;"
            f"padding:7px 12px;font-size:0.82rem;color:#065f46;margin-bottom:6px;'>"
            f"✅ <strong>{len(_blueprint_components)} component(s)</strong> loaded from Blueprint — "
            f"Content Type dropdown auto-populated.</div>",
            unsafe_allow_html=True
        )

    # ── Inline Prompt Controls (near Generate button) ────────────────────────
    _gen_sys_p, _gen_usr_p, _gen_extra_instructions = render_inline_prompt_controls(
        db, "generate",
        project_id=_proj_id, cluster_id=ctx.cluster_id, course_id=_crs_id,
        user_name=user_name, model_choice=model_choice,
        default_system=LESSON_WITH_CONTEXT_SYSTEM,
        default_user=LESSON_WITH_CONTEXT_USER,
        extra_placeholder=(
            "e.g. Use real-world case studies. Add a scenario-based opener. "
            "Include knowledge check questions at the end of each section."
        ),
        project_name=_proj_name, cluster_name=ctx.cluster_name, course_name=_crs_name,
        user_role=user_role,
    )

    # ── Completion gate: check if selected component type is allowed ────────
    _gen_allowed   = True
    _gen_gate_msg  = ""
    _active_bp_obj_gen = blueprint_repository.get_blueprint_by_id(db, eff_bp_id) if eff_bp_id else None

    # ── Generation Form ───────────────────────────────────────────────────
    with st.form(key="gen_config_form"):
        g_conf1, g_conf2 = st.columns(2)
        with g_conf1:
            st.markdown(
                "<div style='font-size:0.75rem;font-weight:700;text-transform:uppercase;"
                "letter-spacing:.08em;color:#4f46e5;margin-bottom:8px;'>📋 Content Type</div>",
                unsafe_allow_html=True
            )
            b_type = None
            _selected_component = None
            topic  = ""   # no longer entered manually; derived from component

            if _blueprint_components:
                _sel_comp_label = st.selectbox(
                    "📋 Content Type",
                    _comp_labels,
                    label_visibility="collapsed",
                    help="Scoped to the active Blueprint. Each option maps to a Blueprint-defined component."
                )
                _selected_component = _comp_map[_sel_comp_label]
                b_type = _selected_component["value"]
                # Show module prefix on selected item
                _mod_num = _active_bp_obj_gen.module_number if _active_bp_obj_gen else ""
                _mod_prefix = f"M{_mod_num} — " if _mod_num else ""
                st.caption(f"🧩 {_mod_prefix}{_sel_comp_label}  ·  type: `{_selected_component['type']}`")
            elif eff_bp_id:
                # Blueprint is pinned but has no parseable sections yet
                st.markdown(
                    "<div style='background:#fef9c3;border:1px solid #fde047;"
                    "border-radius:8px;padding:10px 14px;font-size:0.83rem;color:#854d0e;'>"
                    "⚠️ <strong>Blueprint has no sections yet.</strong><br>"
                    "The active Blueprint appears to be empty. "
                    "Generate or edit it in the Blueprint tab first.</div>",
                    unsafe_allow_html=True
                )
                # b_type and _selected_component remain None — generation is blocked below
            else:
                # No blueprint pinned at all
                st.markdown(
                    "<div style='background:#fef9c3;border:1px solid #fde047;"
                    "border-radius:8px;padding:10px 14px;font-size:0.83rem;color:#854d0e;'>"
                    "⚠️ <strong>No Blueprint pinned.</strong><br>"
                    "Pin a Blueprint via the Blueprint tab first. "
                    "The Content Type dropdown will auto-populate from lessons and components defined in it.</div>",
                    unsafe_allow_html=True
                )
                # b_type and _selected_component remain None — generation is blocked below

        with g_conf2:
            st.markdown(
                "<div style='font-size:0.75rem;font-weight:700;text-transform:uppercase;"
                "letter-spacing:.08em;color:#4f46e5;margin-bottom:8px;'>🛠️ Prompt & Source</div>",
                unsafe_allow_html=True
            )
            p_framework = None
            if b_type:
                available_prompts = get_cached_prompt_names()
                _prompt_opts = ["— Select a template —"] + available_prompts
                _prompt_sel = st.selectbox(
                    "Prompt Template",
                    _prompt_opts,
                    index=0,   # always default to "— Select a template —"
                    help="Select which prompt template drives generation. No template is pre-selected."
                )
                p_framework = None if _prompt_sel == "— Select a template —" else _prompt_sel
            else:
                st.info("👉 Select a Content Type to unlock prompt templates.")

        st.divider()
        if p_framework and p_framework != "— No templates yet —":
            with st.expander("📂 Supplementary File Uploads (optional)", expanded=False):
                st.caption("Upload extra reference files in addition to CDD/Blueprint context.")
                guidelines_file_uploader  = st.file_uploader("Guidelines (PDF/TXT)", type=["pdf","txt"], key="gen_guidelines")
                checklist_file_uploader   = st.file_uploader("Checklist (PDF/XLSX/TXT)", type=["pdf","xlsx","txt"], key="gen_checklist")
                chapter_file_uploader     = st.file_uploader("Chapter / Source Material (PDF/DOCX/TXT)", type=["pdf","docx","txt"], key="gen_chapter")
            available_docs = get_cached_active_document_names()
            ctx_docs = st.multiselect("Additional Source Materials from Library", available_docs,
                                      help="Supplement the CDD/Blueprint with uploaded documents.")
            # Context Database reference hint
            if available_docs:
                st.markdown(
                    f"<div style='background:#f0f9ff;border:1px solid #bae6fd;border-radius:8px;"
                    f"padding:8px 12px;font-size:0.8rem;color:#0369a1;margin-top:6px;'>"
                    f"📎 <strong>Context DB Reference</strong> — You can also reference documents directly "
                    f"in the topic field using backticks, e.g. "
                    f"<code>`{available_docs[0]}`</code>"
                    f"</div>",
                    unsafe_allow_html=True
                )

        # ── Assessment override confirmation (shown inside form) ──────────
        # Initialise to False; updated below if an assessment is selected
        # and lessons are not yet complete.  The checkbox is submitted as
        # part of the form so its value is guaranteed to be read together
        # with gen_btn on the same rerun.
        _assess_override = False
        if (
            _selected_component
            and is_component_type_module_level(_selected_component.get("value", ""))
            and "assessment" in _selected_component.get("label", "").lower()
            and eff_bp_id
        ):
            _gate_preview = get_module_completion_status(db, eff_bp_id)
            if not _gate_preview.get("completed"):
                _done_prev  = _gate_preview.get("generated_lessons", 0)
                _total_prev = _gate_preview.get("total_lessons", 0)
                st.markdown(
                    f"<div style='background:#fff7ed;border:1.5px solid #fdba74;"
                    f"border-radius:9px;padding:11px 15px;margin-top:8px;'>"
                    f"<div style='font-size:0.88rem;font-weight:700;color:#9a3412;margin-bottom:3px;'>"
                    f"⚠️ Not all lessons have been generated yet</div>"
                    f"<div style='font-size:0.82rem;color:#7c2d12;'>"
                    f"Only <strong>{_done_prev} of {_total_prev}</strong> lesson(s) are complete "
                    f"for this module. The assessment may lack full context.<br>"
                    f"Do you still want to generate the module assessments?</div></div>",
                    unsafe_allow_html=True
                )
                _assess_override = st.checkbox(
                    "Generate Anyway — I understand not all lessons are complete",
                    value=False,
                    key="assess_override_ckbx",
                )

        lb_c1, lb_c2, lb_c3 = st.columns([0.35, 0.3, 0.35])
        gen_btn = lb_c2.form_submit_button("🚀 Launch Pipeline", use_container_width=True, type="primary")
        if not _selected_component:
            st.warning("⚠️ Select a Content Type to continue.")
        elif not p_framework:
            st.warning("⚠️ Select a Prompt Template to continue.")

    # Derive effective topic from selected component (no manual entry)
    _eff_topic = _selected_component["label"] if _selected_component else ""
    st.session_state.gen_topic = _eff_topic

    if gen_btn:
        # ── Synchronous validation (fast, runs in UI thread) ─────────────
        if not rbac_gate(user_role, "generate.run", "Running the generation pipeline"):
            st.stop()
        if not _eff_topic:
            st.error("Please select a Blueprint component."); st.stop()
        if not p_framework:
            st.error("Please select a prompt template."); st.stop()

        # Completion gates (fast DB reads, still in UI thread)
        if _selected_component and eff_bp_id:
            _comp_val = _selected_component.get("value", "")
            if is_component_type_module_level(_comp_val):
                _mod_status = get_module_completion_status(db, eff_bp_id)
                if not render_completion_gate(_mod_status, _selected_component["label"], override=_assess_override):
                    st.stop()
            elif is_component_type_course_level(_comp_val) and eff_cdd_id:
                _course_status = get_all_modules_completion(db, eff_cdd_id)
                if not render_course_completion_gate(_course_status, _selected_component["label"]):
                    st.stop()

        prompt = prompt_repository.get_prompt_by_name(db, p_framework)
        if not prompt or not prompt.active_version:
            st.error("⚠️ No active version for this prompt."); st.stop()

        # ── Read uploaded file contents in the UI thread ──────────────────
        # UploadedFile objects cannot cross thread boundaries — read content
        # here and pass the strings via request_json.
        _supp_files = []
        for _uf, _src_label in [
            (guidelines_file_uploader, "guidelines"),
            (checklist_file_uploader,  "checklist"),
            (chapter_file_uploader,    "chapter"),
        ]:
            if _uf:
                _ufname, _ufcontent, _uferr = _parse_uploaded_file(_uf)
                if _uferr:
                    st.warning(f"⚠️ Could not read {_ufname}: {_uferr}")
                elif _ufcontent:
                    _supp_files.append({
                        "name":        _ufname,
                        "content":     _ufcontent,
                        "source_type": _src_label,
                    })

        # ── Build request params and submit job ───────────────────────────
        _extra_instr = _gen_extra_instructions
        _req_params = {
            "topic":               _eff_topic,
            "b_type":              b_type,
            "p_framework":         p_framework,
            "eff_cdd_id":          eff_cdd_id,
            "eff_bp_id":           eff_bp_id,
            "model_choice":        st.session_state.get("model_choice", "GPT-5.4"),
            "target_audience":     st.session_state.get("target_audience", ""),
            "expert_domain":       st.session_state.get("expert_domain", ""),
            "expert_exp":          st.session_state.get("expert_exp", 20),
            "aud_cat":             st.session_state.get("sidebar_aud_cat", "Professional/Corporate"),
            "ctx_docs":            ctx_docs if p_framework else [],
            "user_name":           user_name,
            "project_id":          st.session_state.get("selected_project_id"),
            "course_id":           st.session_state.get("selected_course_id"),
            "selected_component":  _selected_component or {},
            "supplementary_files": _supp_files,
            "extra_instructions":  _extra_instr,
        }

        _job_id = job_repository.create_job(
            db,
            user_name=user_name,
            request_params=_req_params,
            project_id=st.session_state.get("selected_project_id"),
            course_id=st.session_state.get("selected_course_id"),
        )
        job_runner.submit(generation_jobs.run_generation_job, _job_id)
        auto_save_instructions(
            db, "generate", _extra_instr,
            name=_eff_topic[:80] if _eff_topic else "Generate Instructions",
            project_id=_proj_id, cluster_id=None, course_id=_crs_id,
            user_name=user_name,
        )
        st.session_state["active_gen_job_id"] = _job_id
        st.toast("🚀 Generation queued — running in background...")
        st.rerun()

    else:
        if _blueprint_components:
            st.markdown(
                f"<div style='background:#f0fdf4;border:1px solid #a7f3d0;border-radius:10px;"
                f"padding:1rem 1.25rem;font-size:0.875rem;color:#065f46;'>"
                f"✅ <strong>Blueprint loaded</strong> — {len(_blueprint_components)} component(s) ready. "
                f"Select a component from the dropdown and click <strong>🚀 Launch Pipeline</strong>.</div>",
                unsafe_allow_html=True
            )
        else:
            st.markdown(
                "<div style='text-align:center;padding:3rem 2rem;'>"
                "<div style='font-size:2.5rem;margin-bottom:1rem;'>⚙️</div>"
                "<h3 style='color:#374151;margin-bottom:0.5rem;'>Configure your generation</h3>"
                "<p style='color:#6b7280;max-width:420px;margin:auto;line-height:1.6;'>"
                "Pin a <strong>CDD</strong> and <strong>Blueprint</strong> using the tabs above, "
                "then the Content Type dropdown will auto-populate with all Blueprint components."
                "</p></div>",
                unsafe_allow_html=True
            )

