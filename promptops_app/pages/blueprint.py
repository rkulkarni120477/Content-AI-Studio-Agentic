"""Blueprint page renderer.

Extracted from the legacy Streamlit monolith so each page can be maintained independently.
"""

import json
import streamlit as st

from promptops_app.database import (
    BlueprintVersion, CourseDesignDocument, ModuleBlueprint,
    get_active_cdd_version, get_active_style, build_style_context, log_event,
)
from promptops_app.auth.permissions import rbac_gate
from promptops_app.core.llm_client import safe_json_loads
from promptops_app.services.llm_service import generate_text as call_llm
from promptops_app.services.usage_service import UsageLogContext
from promptops_app.core.shared import render_blueprint_content, extract_module_count_from_cdd
from promptops_app.repositories import blueprint_repository, cdd_repository
from promptops_app.parsers.cdd_parser import (
    parse_cdd_flat, parse_sections_from_text, _strip_ui_hidden_text, extract_cdd_summary,
)
from promptops_app.parsers.blueprint_parser import get_blueprint_prompts, _is_bp_section_hidden
from promptops_app.services.export_service import export_content, export_docx, ExportRequest
from promptops_app.ui.components import _section_badge
from promptops_app.services.audit_service import log_audit_event
from promptops_app.ui.notifications import notify_deferred
from promptops_app.prompts.prompt_builder import build_prompt as _lib_build_prompt
from promptops_app.prompt_templates import BLUEPRINT_SYSTEM_PROMPT, BLUEPRINT_USER_PROMPT_TEMPLATE
from promptops_app.ui.prompt_panel import safe_format
from promptops_app.ui.generation_controls import render_inline_prompt_controls, render_prompt_download_button
from promptops_app.ui.user_prompt_widget import auto_save_instructions


def _build_blueprint_prompts(db, *, cdd_context, selected_module, extra_instructions,
                              teacher_mode=False, style_guidelines="") -> tuple[str, str, str, str]:
    """Return (system, user, tpl_name, tpl_version) for blueprint generation.

    Tries prompt library first; falls back to get_blueprint_prompts().
    """
    try:
        variables = {
            "cdd_context":        cdd_context or "No CDD linked. Generate a comprehensive standalone module blueprint.",
            "selected_module":    selected_module,
            "extra_instructions": extra_instructions or "",
            "teacher_mode":       "Yes" if teacher_mode else "No",
            "student_mode":       "No" if teacher_mode else "Yes",
            "style_guidelines":   style_guidelines or "",
        }
        return _lib_build_prompt("blueprint_generation", variables, db=db)
    except Exception:
        mode = "teacher" if teacher_mode else "student"
        sys_p, usr_tmpl, _ = get_blueprint_prompts(mode)
        usr_p = usr_tmpl.format(
            cdd_context=cdd_context or "No CDD linked.",
            selected_module=selected_module,
            extra_instructions_block=extra_instructions or "",
        )
        return sys_p, usr_p, "blueprint_generation", "v1-inline"


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
    # BLUEPRINT TAB — Module Blueprint Pipeline
    # =====================================================================
    st.markdown(_section_badge("🧩", "Module Blueprint",
        "Generates directly from the linked CDD — module structure, lessons, and all components "
        "are derived automatically. No need to re-enter course metadata."),
        unsafe_allow_html=True)

    # ── Read-only Pinned Style + CDD panel (always at top, mandatory for normal users) ──
    _bp_active_style = get_active_style(db, project_id=_proj_id, course_id=_crs_id)
    _bp_pinned_cdd_id = st.session_state.get("active_cdd_id")
    _bp_pinned_cdd = cdd_repository.get_cdd_by_id(db, _bp_pinned_cdd_id) if _bp_pinned_cdd_id else None

    # Pinned context panel
    _pin_s_label  = _bp_active_style.name if _bp_active_style else "None — no active style"
    _pin_s_color  = "#10b981" if _bp_active_style else "#f59e0b"
    _pin_s_status = "● Active" if _bp_active_style else "○ Not set"
    _pin_c_label  = _bp_pinned_cdd.title if _bp_pinned_cdd else "None — no CDD pinned"
    _pin_c_color  = "#6366f1" if _bp_pinned_cdd else "#f59e0b"
    _pin_c_status = f"📌 Pinned ({_bp_pinned_cdd.active_version})" if _bp_pinned_cdd else "○ Not pinned"

    st.markdown(
        f"<div style='background:#f8faff;border:1.5px solid #c7d2fe;border-radius:10px;"
        f"padding:12px 18px;margin-bottom:14px;'>"
        f"<div style='font-size:0.7rem;font-weight:700;text-transform:uppercase;"
        f"letter-spacing:.1em;color:#6366f1;margin-bottom:8px;'>Approved Configuration (Read-Only)</div>"
        f"<div style='display:flex;gap:24px;flex-wrap:wrap;'>"
        f"<div><span style='font-size:0.72rem;color:#6b7280;'>🎨 Style</span><br>"
        f"<strong style='color:{_pin_s_color};'>{_pin_s_label}</strong> "
        f"<span style='font-size:0.7rem;color:{_pin_s_color};'>{_pin_s_status}</span></div>"
        f"<div><span style='font-size:0.72rem;color:#6b7280;'>📘 CDD</span><br>"
        f"<strong style='color:{_pin_c_color};'>{_pin_c_label}</strong> "
        f"<span style='font-size:0.7rem;color:{_pin_c_color};'>{_pin_c_status}</span></div>"
        f"</div></div>",
        unsafe_allow_html=True
    )

    # ── CTA banner for normal users ───────────────────────────────────────
    if not _is_admin:
        if _bp_pinned_cdd:
            st.markdown(
                f"<div style='background:#eef2ff;border:1.5px solid #a5b4fc;border-radius:8px;"
                f"padding:10px 16px;margin-bottom:12px;font-size:0.86rem;color:#3730a3;'>"
                f"👇 <strong>Generate Blueprint from Approved CDD:</strong> "
                f"Use the form below to create a new module blueprint based on the pinned CDD above.</div>",
                unsafe_allow_html=True
            )
        else:
            st.warning("No CDD is pinned yet. Create and pin a CDD in the CDD tab first, then return here to generate blueprints.")

    # ── Student | Teacher mode toggle ─────────────────────────────────────
    _bp_mode_sel = st.radio(
        "Generation Mode",
        ["Student", "Teacher"],
        index=0 if st.session_state.get("bp_generation_mode", "student") == "student" else 1,
        horizontal=True,
        key="bp_mode_radio",
        help="Student: learning content (existing behavior). Teacher: lesson plans, teacher deck, facilitation guides.",
    )
    st.session_state["bp_generation_mode"] = _bp_mode_sel.lower()

    bp_left, bp_right = st.columns([0.4, 0.6], gap="large")

    with bp_left:
        st.markdown("#### ➕ Create New Blueprint")

        # ── CDD selector — scoped to active project ───────────────────────
        all_cdds_bp = (
            cdd_repository.list_cdds_for_project(db, _proj_id)
            if _proj_id
            else cdd_repository.list_all_cdds(db)
        )
        cdd_bp_options = {"— None (standalone) —": None}
        cdd_bp_options.update({f"{c.title} ({c.active_version})": c.id for c in all_cdds_bp})

        active_cdd_id = st.session_state.get("active_cdd_id")
        default_cdd_idx = 0
        if active_cdd_id:
            for i, (k, v) in enumerate(cdd_bp_options.items()):
                if v == active_cdd_id:
                    default_cdd_idx = i
                    break

        # ── Detect modules + course-end items from pinned CDD ─────────────
        _bp_module_opts  = []   # [(label, module_number_or_key), ...]
        _bp_sel_cdd_id   = list(cdd_bp_options.values())[default_cdd_idx]
        if _bp_sel_cdd_id:
            _bp_cdd_v = get_active_cdd_version(db, _bp_sel_cdd_id)
            if _bp_cdd_v:
                _flat_cdd = parse_cdd_flat(_bp_cdd_v.full_content or "")
                _cdd_structure = _flat_cdd.get("Course Structure", "")
                _cdd_course_assessment = _flat_cdd.get("Course Level Assessment", "")
                _existing_bp_nums = blueprint_repository.get_existing_module_numbers(db, _bp_sel_cdd_id)

                # ── Modules: parse actual titles from Course Structure ────
                import re as _re_mod
                _mod_matches = list(_re_mod.finditer(
                    r'(?:^|\n)\s*[Mm]odule\s+(\d+)[:\s\-—]+([^\n]+)', _cdd_structure
                ))
                if _mod_matches:
                    for _mm in _mod_matches:
                        _mn = int(_mm.group(1))
                        _mtitle = _mm.group(2).strip().rstrip("*").strip()
                        done_tag = " ✓" if _mn in _existing_bp_nums else ""
                        _bp_module_opts.append((f"Module {_mn}: {_mtitle}{done_tag}", _mn))
                else:
                    _n_modules = extract_module_count_from_cdd(_bp_cdd_v)
                    for mn in range(1, _n_modules + 1):
                        done_tag = " ✓" if mn in _existing_bp_nums else ""
                        _bp_module_opts.append((f"Module {mn}{done_tag}", mn))

                # ── Course-end items: parse dynamically from Course Level Assessment ──
                # Extract the title of each option/item in the Course Level Assessment
                # block without hardcoding any labels.
                if _cdd_course_assessment.strip():
                    # Match lines like: "• **Title: Robotics Capstone**" or
                    # "**Title:** Robotics Capstone" or "Option 1:" or
                    # "Title: Something" (bold or plain)
                    _title_pat = _re_mod.compile(
                        r'(?:^|\n)\s*'
                        r'(?:•\s*)?'                       # optional bullet
                        r'\*{0,2}[Tt]itle\*{0,2}\s*:?\s*' # "Title:" or "**Title:**"
                        r'\*{0,2}([^\n\*]+?)\*{0,2}'       # captured title text
                        r'\s*(?:\n|$)',
                        _re_mod.MULTILINE
                    )
                    _end_titles = [
                        m.group(1).strip().rstrip("*:").strip()
                        for m in _title_pat.finditer(_cdd_course_assessment)
                        if m.group(1).strip()
                    ]
                    # Fallback: if no "Title:" lines found, try "Option N:" headings
                    if not _end_titles:
                        _opt_pat = _re_mod.compile(
                            r'(?:^|\n)\s*Option\s+\d+\s*:?\s*\n?\s*'
                            r'(?:•\s*)?\*{0,2}[Tt]itle\*{0,2}\s*:?\s*'
                            r'\*{0,2}([^\n\*]+?)\*{0,2}',
                            _re_mod.MULTILINE
                        )
                        _end_titles = [
                            m.group(1).strip().rstrip("*:").strip()
                            for m in _opt_pat.finditer(_cdd_course_assessment)
                            if m.group(1).strip()
                        ]
                    # Add each found course-end title as a dropdown option
                    # Use a string key "end_N" to distinguish from module numbers
                    for _ei, _etitle in enumerate(_end_titles):
                        _bp_module_opts.append(
                            (f"📋 {_etitle}", f"end_{_ei}")
                        )

        # ── Source CDD selector (outside form so it drives module list) ──────
        bp_linked_cdd = st.selectbox(
            "📘 Source CDD",
            list(cdd_bp_options.keys()),
            index=default_cdd_idx,
            help="Blueprint will be generated from this CDD.",
            key="_bp_linked_cdd",
        )

        with st.form("bp_create_form"):

            # Module selector — show only if CDD has defined modules
            _selected_module_num = 1
            _selected_is_course_end = False  # True when a course-end item is selected
            _selected_course_end_label = ""
            if _bp_module_opts:
                st.markdown(
                    "<label style='font-weight:600;font-size:0.85rem;color:#111827;'>"
                    "Select Module / Course-End Item</label>",
                    unsafe_allow_html=True
                )
                _mod_labels  = [o[0] for o in _bp_module_opts]
                _bp_form_ver = st.session_state.get("bp_clear_ver", 0)
                _mod_sel_lbl = st.selectbox(
                    "Select Module",
                    _mod_labels,
                    label_visibility="collapsed",
                    help="Modules and course-end assessments/projects from the CDD. ✓ = Blueprint already created.",
                    key=f"bp_mod_sel_{_bp_form_ver}"
                )
                _sel_key = next(
                    (n for lbl, n in _bp_module_opts if lbl == _mod_sel_lbl),
                    _bp_module_opts[0][1] if _bp_module_opts else 1
                )
                # Determine if this is a module (int) or a course-end item (string "end_N")
                if isinstance(_sel_key, str) and _sel_key.startswith("end_"):
                    _selected_is_course_end = True
                    _selected_course_end_label = _mod_sel_lbl.lstrip("📋 ").strip()
                    _selected_module_num = 0  # sentinel for course-end
                    st.markdown(
                        f"<div style='background:#eef2ff;border:1px solid #c7d2fe;"
                        f"border-radius:6px;padding:5px 10px;font-size:0.78rem;"
                        f"color:#4338ca;margin:2px 0 4px;'>"
                        f"📋 Course-end item selected: <strong>{_selected_course_end_label}</strong></div>",
                        unsafe_allow_html=True
                    )
                else:
                    _selected_module_num = int(_sel_key)
                    # Warn if blueprint already exists for this module
                    _existing_for_mod = blueprint_repository.get_existing_module_blueprint(db, _bp_sel_cdd_id, _selected_module_num)
                    if _existing_for_mod:
                        st.markdown(
                            f"<div style='background:#fef9c3;border:1px solid #fde047;"
                            f"border-radius:6px;padding:5px 10px;font-size:0.78rem;"
                            f"color:#854d0e;margin:2px 0 4px;'>"
                            f"⚠️ Blueprint already exists for Module {_selected_module_num}. "
                            f"Generating again will create a new version.</div>",
                            unsafe_allow_html=True
                        )
                    else:
                        st.markdown(
                            f"<div style='background:#f0fdf4;border:1px solid #86efac;"
                            f"border-radius:6px;padding:5px 10px;font-size:0.78rem;"
                            f"color:#166534;margin:2px 0 4px;'>"
                            f"✅ Ready to generate Module {_selected_module_num} Blueprint.</div>",
                            unsafe_allow_html=True
                        )
            else:
                st.caption("ℹ️ Module number will be 1 (no module structure detected in CDD).")

            bp_doc_title = st.text_input(
                "Blueprint Title (optional)",
                placeholder=f"e.g. Module {_selected_module_num} — Patient Assessment Blueprint",
                key="_bp_doc_title",
            )
            bp_form_btn = st.form_submit_button("Confirm Module Selection", use_container_width=True)

        # ── Inline Prompt Controls (near Generate button) ──────────────────
        _bp_panel_sys, _bp_panel_usr, _bp_extra_instructions = render_inline_prompt_controls(
            db, "blueprint",
            project_id=_proj_id, cluster_id=None, course_id=_crs_id,
            user_name=user_name, model_choice=model_choice,
            default_system=BLUEPRINT_SYSTEM_PROMPT,
            default_user=BLUEPRINT_USER_PROMPT_TEMPLATE,
            extra_placeholder="e.g. Focus on simulation-based lessons. Add a career spotlight per lesson.",
            project_name=_proj_name, cluster_name=ctx.cluster_name, course_name=_crs_name,
        )

        bp_gen_btn = st.button(
            "🤖 Generate Blueprint with AI",
            use_container_width=True, type="primary",
            key="_bp_gen_btn",
        )

        if bp_gen_btn:
            if not rbac_gate(user_role, "blueprint.generate", "Generating a Blueprint"):
                st.stop()
            model_choice  = st.session_state.get("model_choice", "GPT-5.4")
            linked_cdd_id = cdd_bp_options.get(st.session_state.get("_bp_linked_cdd", bp_linked_cdd))
            bp_extra_instructions = _bp_extra_instructions
            bp_doc_title = st.session_state.get("_bp_doc_title", "")

            # Show which style will be applied
            _pre_style_bp = get_active_style(db, project_id=_proj_id, course_id=_crs_id)
            if _pre_style_bp:
                st.info(f"🎨 **Style '{_pre_style_bp.name}'** will be applied to this Blueprint.", icon="🎨")

            cdd_context_text = ""
            _bp_cdd_title    = "Standalone Blueprint"
            if linked_cdd_id:
                cdd_ver = get_active_cdd_version(db, linked_cdd_id)
                if cdd_ver:
                    # Use full CDD content for Blueprint generation — not just summary
                    # Strip validation/backend-only blocks before passing to Blueprint prompt
                    _flat_cdd_bp = parse_cdd_flat(cdd_ver.full_content or "")
                    _cdd_ui_parts = []
                    for _ck in ["Course Details", "Course Structure", "Course Level Assessment"]:
                        _cv = _flat_cdd_bp.get(_ck, "").strip()
                        if _cv:
                            _cdd_ui_parts.append(f"### {_ck}\n{_cv}")
                    cdd_context_text = "\n\n".join(_cdd_ui_parts) or extract_cdd_summary(cdd_ver, max_chars=8000)
                _cdd_obj = cdd_repository.get_cdd_by_id(db, linked_cdd_id)
                if _cdd_obj:
                    _bp_cdd_title = _cdd_obj.course_title or _cdd_obj.title

            if not linked_cdd_id and not cdd_context_text:
                st.warning("⚠️ No CDD linked — generating standalone. Link a CDD for best results.")

            # Build extra block — different framing for course-end vs module
            if _selected_is_course_end and _selected_course_end_label:
                _bp_extra_block = (
                    f"**This is a Course-Level End item: '{_selected_course_end_label}'.**\n"
                    f"Generate the Blueprint specifically for this course-end item. "
                    f"It should align to all modules in the course."
                    + (f"\n\n**Additional Instructions:**\n{bp_extra_instructions.strip()}"
                       if bp_extra_instructions and bp_extra_instructions.strip() else "")
                )
            else:
                _bp_extra_block = (
                    f"**This is Module {_selected_module_num} of the course.**\n"
                    f"Generate the Blueprint specifically for Module {_selected_module_num}. "
                    f"Lesson numbering should start from Lesson 1 within this module."
                    + (f"\n\n**Additional Instructions:**\n{bp_extra_instructions.strip()}"
                       if bp_extra_instructions and bp_extra_instructions.strip() else "")
                )

            # Inject active style if available
            _active_style_bp = get_active_style(db, project_id=_proj_id, course_id=_crs_id)
            if _active_style_bp:
                _style_ctx_bp = build_style_context(db, _active_style_bp)
                _bp_extra_block = (
                    f"**ACTIVE STYLE — Maintain throughout this Blueprint:**\n{_style_ctx_bp}\n\n"
                    + _bp_extra_block
                )

            _gen_label = _selected_course_end_label if _selected_is_course_end else f"Module {_selected_module_num}"
            _selected_module_ref = _selected_course_end_label if _selected_is_course_end else f"Module {_selected_module_num}"
            _bp_gen_mode = st.session_state.get("bp_generation_mode", "student")
            with st.status(f"🗂️ Generating Blueprint: {_gen_label}...", expanded=True) as bp_status:
                bp_status.write("🔗 Stage 1: Injecting CDD context...")
                bp_status.write(f"🧩 Stage 2: Scoping to {_gen_label}...")
                bp_status.write(f"🧠 Stage 3: Generating {'Teacher' if _bp_gen_mode == 'teacher' else 'Student'} Blueprint with AI...")
                _active_style_bp = get_active_style(db, project_id=_proj_id, course_id=_crs_id)
                _style_ctx_bp    = build_style_context(db, _active_style_bp) if _active_style_bp else ""
                # Use the panel-resolved prompt (defaults to DB default_blueprint_prompt).
                # Fall back to _build_blueprint_prompts only if panel returned empty strings.
                if _bp_panel_sys and _bp_panel_usr:
                    _bp_sys_prompt = _bp_panel_sys
                    user_p = safe_format(
                        _bp_panel_usr,
                        cdd_context=cdd_context_text,
                        selected_module=_selected_module_ref,
                        extra_instructions_block=_bp_extra_block,
                        extra_instructions=_bp_extra_block,
                        style_guidelines=_style_ctx_bp,
                        teacher_mode="Yes" if _bp_gen_mode == "teacher" else "No",
                        student_mode="No"  if _bp_gen_mode == "teacher" else "Yes",
                    )
                else:
                    _bp_sys_prompt, user_p, _, _ = _build_blueprint_prompts(
                        db,
                        cdd_context=cdd_context_text,
                        selected_module=_selected_module_ref,
                        extra_instructions=_bp_extra_block,
                        teacher_mode=(_bp_gen_mode == "teacher"),
                        style_guidelines=_style_ctx_bp,
                    )
                bp_output = call_llm(model_choice, _bp_sys_prompt, user_p, usage_ctx=UsageLogContext(
                    user_name=user_name,
                    project_id=st.session_state.get("selected_project_id"),
                    course_id=st.session_state.get("selected_course_id"),
                    entity_type="blueprint",
                ))

                if bp_output.startswith("ERROR"):
                    bp_status.update(label="❌ Blueprint Generation Failed", state="error")
                    st.error(bp_output)
                else:
                    bp_status.write("📦 Stage 4: Parsing sections...")
                    sections = parse_sections_from_text(bp_output)
                    _inferred_title = bp_doc_title or f"{_gen_label} — {_bp_cdd_title} Blueprint"
                    new_bp = ModuleBlueprint(
                        cdd_id=linked_cdd_id, title=_inferred_title,
                        module_title=f"Module {_selected_module_num}",
                        module_number=_selected_module_num,
                        module_objective="See Blueprint Module Overview section.",
                        active_version="v1", workflow_state="draft", created_by=user_name,
                        project_id=st.session_state.get("selected_project_id"),
                        course_id=st.session_state.get("selected_course_id"),
                    )
                    db.add(new_bp); db.commit(); db.refresh(new_bp)
                    gen_params = {
                        "cdd_id": linked_cdd_id, "cdd_title": _bp_cdd_title,
                        "module_number": _selected_module_num,
                        "extra_instructions": bp_extra_instructions or "",
                        "mode": _bp_gen_mode,
                    }
                    v1 = BlueprintVersion(
                        blueprint_id=new_bp.id, version="v1", full_content=bp_output,
                        sections=json.dumps(sections), generation_params=json.dumps(gen_params),
                        change_reason="Initial AI generation", is_active=True, created_by=user_name
                    )
                    db.add(v1); db.commit()
                    log_event(db, "blueprint_created", user_name,
                              f"Blueprint '{_inferred_title}' created (v1)",
                              {"blueprint_id": new_bp.id, "cdd_id": linked_cdd_id,
                               "module_number": _selected_module_num})
                    log_audit_event(db, user_name, "blueprint.generated", entity_type="blueprint", entity_id=new_bp.id,
                                    project_id=_proj_id, course_id=_crs_id,
                                    metadata={"title": _inferred_title, "module": _selected_module_num,
                                              "cdd_id": linked_cdd_id, "mode": _bp_gen_mode})
                    bp_status.update(
                        label=f"✅ Module {_selected_module_num} Blueprint '{_inferred_title}' created — {len(sections)} sections!",
                        state="complete"
                    )
                    st.session_state["active_blueprint_id"] = new_bp.id
                    notify_deferred("bp_created", f"Blueprint '{_inferred_title}' created with {len(sections)} sections.")
                    auto_save_instructions(
                        db, "blueprint", bp_extra_instructions,
                        name=f"M{_selected_module_num} — {_bp_cdd_title}"[:80],
                        project_id=_proj_id, cluster_id=None, course_id=_crs_id,
                        user_name=user_name,
                    )
                    st.rerun()

    with bp_right:
        st.markdown("#### 📂 Your Module Blueprints")
        # Scope to active course (and project as fallback); ordered desc so index 0 = most recent
        all_bps = blueprint_repository.list_blueprints_for_course(db, course_id=_crs_id, project_id=_proj_id)

        if not all_bps:
            st.info("No Blueprints yet. Create your first Blueprint using the form on the left.")
        else:
            bp_options = {f"M{b.module_number}: {b.title} (ID: {b.id})": b.id for b in all_bps}

            sel_bp_label = st.selectbox(
                "Select Blueprint to View/Edit",
                list(bp_options.keys()),
                index=0
            )
            sel_bp_id = bp_options[sel_bp_label]
            sel_bp = blueprint_repository.get_blueprint_by_id(db, sel_bp_id)

            # Blueprint meta
            bm1, bm2, bm3 = st.columns(3)
            bm1.metric("Active Version", sel_bp.active_version or "—")
            bm2.metric("State", sel_bp.workflow_state.title())
            all_bp_vers = blueprint_repository.list_blueprint_versions(db, sel_bp_id)
            bm3.metric("Total Versions", len(all_bp_vers))

            # CDD linkage info
            if sel_bp.cdd_id:
                linked_cdd = cdd_repository.get_cdd_by_id(db, sel_bp.cdd_id)
                if linked_cdd:
                    st.info(f"🔗 Linked to CDD: **{linked_cdd.title}** ({linked_cdd.active_version})")

            ver_labels_bp = [v.version for v in all_bp_vers]
            if ver_labels_bp:
                sel_bp_ver_label = st.selectbox("View Version", ver_labels_bp,
                                                 index=ver_labels_bp.index(sel_bp.active_version) if sel_bp.active_version in ver_labels_bp else 0,
                                                 key="bp_ver_sel")
                sel_bp_ver = blueprint_repository.get_blueprint_version(db, sel_bp_id, sel_bp_ver_label)

            if sel_bp_ver:
                    # ── Schema-driven Blueprint UI rendering ──────────────
                    _bp_stored_params = safe_json_loads(sel_bp_ver.generation_params) if sel_bp_ver.generation_params else {}
                    _bp_view_mode = _bp_stored_params.get("mode", "student")
                    render_blueprint_content(
                        full_content=sel_bp_ver.full_content or "",
                        bp_id=sel_bp_id,
                        ver_label=sel_bp_ver_label,
                        sel_bp_ver=sel_bp_ver,
                        sel_bp=sel_bp,
                        db=db,
                        user_name=user_name,
                        model_choice=st.session_state.get("model_choice", "GPT-5.4"),
                        blueprint_mode=_bp_view_mode,
                    )

                    st.divider()
                    with st.expander("🚀 Save as New Version", expanded=False):
                        new_bp_v_tag = st.text_input("New Version Tag", value=f"v{len(all_bp_vers)+1}", key="bp_new_ver_tag")
                        new_bp_v_reason = st.text_input("Change Reason", placeholder="What changed?", key="bp_new_ver_reason")
                        if st.button("💾 Commit New Blueprint Version", key="bp_commit_ver"):
                            cur_bp_sections = safe_json_loads(sel_bp_ver.sections) if sel_bp_ver.sections else {}
                            db.query(BlueprintVersion).filter(BlueprintVersion.blueprint_id == sel_bp_id).update({BlueprintVersion.is_active: False})
                            new_bp_ver = BlueprintVersion(
                                blueprint_id=sel_bp_id, version=new_bp_v_tag,
                                full_content=sel_bp_ver.full_content,
                                sections=json.dumps(cur_bp_sections),
                                generation_params=sel_bp_ver.generation_params,
                                change_reason=new_bp_v_reason, is_active=True,
                                created_by=user_name
                            )
                            db.add(new_bp_ver)
                            sel_bp.active_version = new_bp_v_tag
                            db.commit()
                            log_event(db, "blueprint_version_committed", user_name, f"Blueprint {sel_bp.title} → {new_bp_v_tag}", {"bp_id": sel_bp_id})
                            st.toast(f"✅ Blueprint version {new_bp_v_tag} committed.")
                            st.rerun()

            # Pin as active for generation
            st.divider()
            if st.button("📌 Set as Active Blueprint for Generation", key="pin_bp", use_container_width=True, type="primary"):
                st.session_state["active_blueprint_id"] = sel_bp_id
                st.toast(f"✅ '{sel_bp.title}' set as active Blueprint for lesson generation.")

            # ── Download Blueprint ────────────────────────────────────────
            st.markdown("**📥 Download Blueprint**")
            _bp_dl_ver = blueprint_repository.get_blueprint_version(db, sel_bp_id, sel_bp.active_version)
            if _bp_dl_ver:
                _bpdl_c1, _bpdl_c2 = st.columns(2)
                # Build download from the same UI-visible sections as the renderer
                # (filtered through _is_bp_section_hidden + _strip_ui_hidden_text)
                _bp_raw_secs = safe_json_loads(_bp_dl_ver.sections) if _bp_dl_ver.sections else {}
                if not _bp_raw_secs and _bp_dl_ver.full_content:
                    _bp_raw_secs = parse_sections_from_text(_bp_dl_ver.full_content)

                _bp_dl_parts_md    = []
                _bp_dl_blocks_docx = []
                import re as _re_bpdl

                for _bp_sk, _bp_sv in _bp_raw_secs.items():
                    # Skip hidden backend-only sections (same filter as renderer)
                    if _is_bp_section_hidden(_bp_sk):
                        continue
                    _bp_sc = (_bp_sv or "").strip()
                    if not _bp_sc:
                        continue
                    # Strip backend-only phrases
                    _bp_sc = _strip_ui_hidden_text(_bp_sc)
                    if not _bp_sc:
                        continue
                    # Rename labels to match UI
                    _bp_dl = _bp_sk
                    _bp_dl = _re_bpdl.sub(r'(?i)lesson\s+structure\s*\(planks?\)', 'Lesson Structure (Topics)', _bp_dl)
                    if _re_bpdl.search(r'(?i)^lesson structure$', _bp_dl.strip()):
                        _bp_dl = 'Lesson Structure (Topics)'
                    _bp_dl = _bp_dl.replace("Purpose", "Goal")

                    _bp_dl_parts_md.append(f"## {_bp_dl}\n\n{_bp_sc}")
                    _bp_dl_blocks_docx.append((_bp_dl, _bp_sc))

                _bp_md_dl = "\n\n---\n\n".join(_bp_dl_parts_md) if _bp_dl_parts_md else (_bp_dl_ver.full_content or "")
                _bp_blocks = _bp_dl_blocks_docx if _bp_dl_blocks_docx else [("Blueprint Content", _bp_md_dl)]
                _bpdl_c1.download_button(
                    "⬇️ Markdown (.md)",
                    data=_bp_md_dl,
                    file_name=f"Blueprint_{sel_bp.module_title.replace(' ','_')}_{sel_bp.active_version}.md",
                    mime="text/markdown",
                    use_container_width=True,
                    key="bp_dl_md"
                )
                _bp_docx_r = export_content(db, ExportRequest(
                    fmt="docx",
                    topic=sel_bp.title,
                    blocks=_bp_blocks,
                    user_name=user_name,
                    is_admin=_is_admin,
                    entity_type="blueprint",
                    entity_id=sel_bp.id,
                    project_id=st.session_state.get("selected_project_id"),
                    course_id=st.session_state.get("selected_course_id"),
                    file_name=f"Blueprint_{sel_bp.module_title.replace(' ','_')}_{sel_bp.active_version}.docx",
                ))
                if _bp_docx_r.success:
                    _bpdl_c2.download_button(
                        "⬇️ Word (.docx)",
                        data=_bp_docx_r.data,
                        file_name=_bp_docx_r.file_name,
                        mime=_bp_docx_r.mime_type,
                        use_container_width=True,
                        key="bp_dl_docx"
                    )
                else:
                    _bpdl_c2.error(_bp_docx_r.error_message)

                # ── Download Prompt Used ────────────────────────────────────
                render_prompt_download_button(
                    db, "blueprint",
                    project_name=_proj_name, cluster_name=ctx.cluster_name, course_name=_crs_name,
                    button_label="⬇️ Download Prompt Used (.md)",
                    key=f"bp_dl_prompt_{sel_bp.id}",
                    use_container_width=True,
                )

