"""Analytics page renderer.

Extracted from the legacy Streamlit monolith so each page can be maintained independently.
"""

import json
import streamlit as st
from datetime import datetime, timezone

from sqlalchemy import text

from promptops_app.database import (
    Block, CourseDesignDocument, Document, FeedbackSignal, Generation,
    ModuleBlueprint, Project, ProjectUserAssignment, Prompt, PromptVersion,
    Review, SystemLog, User,
    engine, log_event, hash_password, _get_user_projects,
)
from promptops_app.auth.permissions import rbac_check, rbac_gate, role_label, ROLE_DISPLAY_TO_DB
from promptops_app.core.config import ANALYTICS_PAGE_SIZE, FEEDBACK_PAGE_SIZE
from promptops_app.repositories import (
    analytics_repository, generation_repository,
    project_repository, prompt_repository, user_repository,
    document_repository,
)
from promptops_app.repositories import usage_repository
from promptops_app.services.audit_service import (
    AUDIT_EVENTS, get_audit_trail, get_event_meta, count_trail,
)
from promptops_app.ui.components import _section_badge
from promptops_app.ui.pagination import paginate as _paginate


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
    st.markdown(_section_badge("📊", "Analytics & Observability",
        "Real-time metrics, prompt performance tracking, and full audit trails for every action on the platform."),
        unsafe_allow_html=True)

    _scope = dict(user_name=user_name, project_id=_proj_id, is_admin=_is_admin)

    if not _is_admin:
        st.caption(f"Showing your metrics for project **{_proj_name}** → course **{_crs_name}**.")
    else:
        st.caption("Admin view — cross-project metrics.")

    # Dynamic metrics
    gen_count   = analytics_repository.count_generations_scoped(db, **_scope)
    block_count = generation_repository.count_blocks_scoped(db, **_scope)
    asset_count = prompt_repository.count_prompts(db)
    doc_count   = document_repository.count_documents(db)
    from promptops_app.repositories import cdd_repository as _cdd_r, blueprint_repository as _bp_r
    cdd_count   = _cdd_r.count_cdds_scoped(db, **_scope)
    bp_count    = _bp_r.count_blueprints_scoped(db, **_scope)

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("🚀 Generations",   gen_count)
    c2.metric("🧩 Content Blocks", block_count)
    c3.metric("📚 Prompt Assets",  asset_count)
    c4.metric("📄 Documents",      doc_count)
    c5.metric("📋 CDDs",           cdd_count)
    c6.metric("🗂️ Blueprints",     bp_count)

    # Admin: cross-project comparison
    if _is_admin:
        st.divider()
        st.subheader("📁 Project-Level Comparison")
        all_projects_an = analytics_repository.list_active_projects_for_analytics(db)
        proj_rows = []
        for proj in all_projects_an:
            p_gen_ids = analytics_repository.get_project_generation_ids(db, proj.id)
            p_gens   = len(p_gen_ids)
            p_blocks = analytics_repository.count_project_blocks(db, p_gen_ids)
            p_cdds   = analytics_repository.count_project_cdds(db, proj.id)
            p_bps    = analytics_repository.count_project_blueprints(db, proj.id)
            proj_rows.append({"Project": proj.name, "Client": proj.client_name or "—",
                               "Generations": p_gens, "Blocks": p_blocks, "CDDs": p_cdds, "Blueprints": p_bps})
        if proj_rows:
            st.dataframe(proj_rows, use_container_width=True)

    st.divider()

    # Prompt Performance Tracking
    st.subheader("🏆 Prompt Performance Leaderboard")
    st.caption("Average quality rating per prompt template, computed from reviewer feedback.")
    all_gens = generation_repository.list_generations_all_scoped(db, **_scope)
    prompt_perf = {}
    if all_gens:
        _perf_gen_ids = [g.id for g in all_gens]
        _all_rated_blocks = generation_repository.list_rated_blocks_for_gen_ids(db, _perf_gen_ids)
        _blocks_by_gen = {}
        for _blk in _all_rated_blocks:
            _blocks_by_gen.setdefault(_blk.generation_id, []).append(_blk)
        for gen in all_gens:
            gen_blocks = _blocks_by_gen.get(gen.id, [])
            if gen_blocks:
                avg_rating = sum(b.rating for b in gen_blocks) / len(gen_blocks)
                key = f"{gen.prompt_name} ({gen.prompt_version})"
                if key not in prompt_perf:
                    prompt_perf[key] = {"total_rating": 0, "count": 0}
                prompt_perf[key]["total_rating"] += avg_rating
                prompt_perf[key]["count"] += 1

    if prompt_perf:
        perf_data = [{"Prompt": k, "Avg Rating": round(v["total_rating"] / v["count"], 2), "Samples": v["count"]} for k, v in prompt_perf.items()]
        perf_data.sort(key=lambda x: x["Avg Rating"], reverse=True)
        st.dataframe(perf_data, use_container_width=True)
    else:
        st.info("No rated content yet. Score blocks in the Editor tab to see performance data here.")

    st.subheader("🎯 Instructional Quality Trends")
    ratings = generation_repository.get_block_ratings_scoped(db, **_scope)
    if ratings:
        r_counts = [r[0] for r in ratings if r[0] is not None and r[0] > 0]
        if r_counts: st.area_chart(r_counts)
        else: st.info("No rated blocks yet.")
    else:
        st.info("No blocks generated yet.")

    st.subheader("📜 Interactive System History")
    history_tab1, history_tab2, history_tab3, history_tab4 = st.tabs(["Generations", "Registry Commits", "Document Uploads", "CDD & Blueprint Log"])

    with history_tab1:
        latest = generation_repository.list_recent_generations(db, limit=20, **_scope)
        if latest:
            from promptops_app.repositories import cdd_repository as _cdd_r2, blueprint_repository as _bp_r2
            rows = []
            for g in latest:
                cdd_lbl = "—"
                bp_lbl  = "—"
                if g.cdd_id:
                    cdd_obj = _cdd_r2.get_cdd_by_id(db, g.cdd_id)
                    cdd_lbl = f"{cdd_obj.title[:20]}… ({g.cdd_version})" if cdd_obj else f"CDD#{g.cdd_id}"
                if g.blueprint_id:
                    bp_obj = _bp_r2.get_blueprint_by_id(db, g.blueprint_id)
                    bp_lbl = f"{bp_obj.title[:20]}… ({g.blueprint_version})" if bp_obj else f"BP#{g.blueprint_id}"
                rows.append({
                    "ID": g.id, "Topic": g.topic, "Asset": g.prompt_name, "V": g.prompt_version,
                    "CDD": cdd_lbl, "Blueprint": bp_lbl,
                    "Time": g.created_at.strftime("%H:%M:%S")
                })
            st.dataframe(rows, use_container_width=True)
        else:
            st.info("No generations yet.")

    with history_tab2:
        ver_history = analytics_repository.list_recent_prompt_versions(db)
        if ver_history:
            st.dataframe([{"Asset ID": v.prompt_id, "V": v.version, "Notes": v.change_reason or "", "Time": v.created_at.strftime("%m-%d %H:%M")} for v in ver_history], use_container_width=True)
        else: st.info("No versions deployed yet.")

    with history_tab3:
        doc_history = analytics_repository.list_recent_doc_uploads(db)
        if doc_history:
            st.dataframe([{"Filename": d.filename, "Tag": d.doc_tag or "general", "Type": d.file_type, "User": d.uploaded_by, "Time": d.uploaded_at.strftime("%H:%M")} for d in doc_history], use_container_width=True)
        else: st.info("No documents uploaded yet.")

    with history_tab4:
        st.caption("Audit trail for all CDD and Blueprint creation and version commits.")
        cdd_logs = analytics_repository.list_cdd_blueprint_events(db)
        if cdd_logs:
            st.dataframe([{
                "Event": log.event_type.replace("_", " ").title(),
                "Actor": log.actor,
                "Details": log.details,
                "Time": log.created_at.strftime("%m-%d %H:%M:%S")
            } for log in cdd_logs], use_container_width=True)
        else:
            st.info("No CDD or Blueprint events yet. Create a CDD or Blueprint to see the trail here.")

    st.divider()

    # ── Feedback Library ──────────────────────────────────────────────────
    st.markdown("#### 🧠 Feedback Library — Learning Signals")
    st.markdown(
        "<p style='font-size:0.82rem;color:#6b7280;margin-top:-0.4rem;'>Only signals marked "
        "<strong>Use as Learning</strong> appear here. These are used to improve future "
        "regenerations automatically.</p>",
        unsafe_allow_html=True
    )

    # Count queries — no full-table load
    _fb_total    = generation_repository.count_feedback_signals(db)
    _fb_learning = generation_repository.count_feedback_signals_by_scope(db, "learning")
    _fb_onetime  = generation_repository.count_feedback_signals_by_scope(db, "one_time")

    _fl1, _fl2, _fl3 = st.columns(3)
    _fl1.metric("Total Signals", _fb_total)
    _fl2.metric("🧠 Learning",   _fb_learning, help="Reusable signals that improve future generations")
    _fl3.metric("⚡ One-time",   _fb_onetime,  help="Applied once, not reused")

    _fb_filter = st.selectbox(
        "Filter",
        ["🧠 Learning signals only", "⚡ One-time only", "All signals"],
        key="fb_filter"
    )
    _fb_scope = (
        "learning"  if "Learning"  in _fb_filter else
        "one_time"  if "One-time"  in _fb_filter else
        None
    )
    _fb_count = _fb_learning if _fb_scope == "learning" else _fb_onetime if _fb_scope == "one_time" else _fb_total

    if _fb_count == 0:
        st.info("No feedback signals yet. Use the Regenerate or Save Edit features to capture feedback.")
    else:
        _fb_offset, _ = _paginate(_fb_count, FEEDBACK_PAGE_SIZE, "analytics_feedback_page")
        _show_fb = generation_repository.list_feedback_signals_filtered(
            db, scope=_fb_scope, limit=FEEDBACK_PAGE_SIZE, offset=_fb_offset,
        )
        st.dataframe(
            [{
                "ID":          f.id,
                "Source":      f.signal_source.title(),
                "Scope":       "🧠 Learning" if f.feedback_scope == "learning" else "⚡ One-time",
                "Block Type":  f.block_type or "—",
                "Instruction": (f.user_instruction or f.edit_reason or "—")[:80],
                "Author":      f.author or "—",
                "Date":        f.created_at.strftime("%m-%d %H:%M"),
            } for f in _show_fb],
            hide_index=True, use_container_width=True
        )

    if _fb_learning > 0:
        with st.expander("📖 View full learning signal details", expanded=False):
            _learning_detail = generation_repository.list_feedback_signals_filtered(
                db, scope="learning", limit=10, offset=0,
            )
            for fb in _learning_detail:
                _src_badge = "🔄 Regen" if fb.signal_source == "regenerate" else "✏️ Edit"
                st.markdown(f"**#{fb.id} — {_src_badge} | {fb.block_type or 'unknown'} | {fb.created_at.strftime('%Y-%m-%d')}**")
                if fb.user_instruction:
                    st.markdown(f"*Instruction:* `{fb.user_instruction}`")
                if fb.edit_reason:
                    st.markdown(f"*Edit reason:* `{fb.edit_reason}`")
                st.divider()

    st.divider()

    # ── Review Analytics ──────────────────────────────────────────────────
    st.markdown("#### 📋 Review Analytics")
    _rev_total    = generation_repository.count_reviews(db)
    _rev_approved = generation_repository.count_approved_reviews(db)
    _rev_avg      = generation_repository.avg_review_score(db)
    if _rev_total > 0:
        rv1, rv2, rv3 = st.columns(3)
        rv1.metric("Total Reviews", _rev_total)
        rv2.metric("Approved",      f"{_rev_approved}/{_rev_total}")
        rv3.metric("Avg Score",     f"{_rev_avg:.1f}/5")
        _rev_offset, _ = _paginate(_rev_total, ANALYTICS_PAGE_SIZE, "analytics_reviews_page")
        all_reviews = analytics_repository.list_recent_reviews(db, limit=ANALYTICS_PAGE_SIZE, offset=_rev_offset)
        st.dataframe([{
            "Block": r.block_id, "Reviewer": r.reviewer, "Role": r.reviewer_role,
            "Score": f"{'⭐' * (r.score or 0)}", "Approved": "✅" if r.approved else "❌",
            "Comments": (r.comments or "")[:80], "Time": r.created_at.strftime("%m-%d %H:%M")
        } for r in all_reviews], use_container_width=True)
    else:
        st.info("No reviews submitted yet. Submit reviews in the Editor tab.")

    # System Event Log (Observability)
    st.subheader("🔍 System Event Log (Observability)")
    st.caption("Full trace of all system events — generations, uploads, exports, reviews, prompt changes.")
    _log_total = analytics_repository.count_system_logs(db)
    if _log_total > 0:
        _log_offset, _ = _paginate(_log_total, ANALYTICS_PAGE_SIZE, "analytics_syslog_page")
        sys_logs = analytics_repository.list_system_logs(db, limit=ANALYTICS_PAGE_SIZE, offset=_log_offset)
        st.dataframe([{
            "Event": log.event_type, "Actor": log.actor,
            "Details": log.details, "Time": log.created_at.strftime("%m-%d %H:%M:%S")
        } for log in sys_logs], use_container_width=True)
    else:
        st.info("No system events logged yet. Events will appear here as you use the platform.")

    # ── User Management — Admin only ──────────────────────────────────────
    if rbac_check(user_role, "users.create"):
        st.divider()
        st.markdown("#### 👥 User Management")
        st.markdown(
            "<p style='font-size:0.82rem;color:#6b7280;margin-top:-0.4rem;margin-bottom:1rem;'>"
            "Admin-only view. Manage platform users, roles, and access control.</p>",
            unsafe_allow_html=True
        )

        all_users = user_repository.list_all_users(db)

        # User table — show new display role names
        st.dataframe(
            [{
                "Username":    u.username,
                "Role":        role_label(u.role),
                "Status":      "✅ Active" if (u.is_active is None or u.is_active) else "🚫 Inactive",
                "Created":     u.created_at.strftime("%Y-%m-%d") if u.created_at else "—",
            } for u in all_users],
            hide_index=True, use_container_width=True
        )

        st.markdown("---")
        st.markdown("##### ➕ Add New User")
        with st.form("admin_add_user_form"):
            _au1, _au2, _au3 = st.columns(3)
            _new_uname      = _au1.text_input("Username *", placeholder="e.g. id_user6")
            _new_role_disp  = _au3.selectbox("Role *", ["ID", "Lead", "Admin"])
            _new_pwd        = st.text_input("Password *", type="password", placeholder="Set initial password")
            _add_user_btn   = st.form_submit_button("➕ Create User", type="primary")

        if _add_user_btn:
            if not _new_uname.strip() or not _new_pwd.strip():
                st.warning("Username and password are required.")
            elif user_repository.get_user_by_username(db, _new_uname.strip()):
                st.warning(f"Username '{_new_uname}' already exists.")
            else:
                _new_role_db = ROLE_DISPLAY_TO_DB.get(_new_role_disp, "author")
                _nu = User(
                    username=_new_uname.strip(),
                    password_hash=hash_password(_new_pwd),
                    role=_new_role_db,
                    permissions=json.dumps([]),
                    is_active=True,
                    created_at=datetime.now(timezone.utc)
                )
                db.add(_nu); db.commit()
                log_event(db, "user_created", user_name,
                          f"Admin created user '{_new_uname}' with role '{_new_role_db}' (display: {_new_role_disp})")
                from promptops_app.services.audit_service import log_audit_event as _lae
                _lae(db, user_name, "user.created", entity_type="user", entity_id=_new_uname,
                     metadata={"role": _new_role_db, "display_role": _new_role_disp})
                st.toast(f"✅ User '{_new_uname}' ({_new_role_disp}) created.")
                st.rerun()

        # Deactivate / reactivate existing users
        if len(all_users) > 1:
            st.markdown("##### 🔒 Toggle User Access")
            _toggle_opts = {f"{u.username} ({role_label(u.role)})": u.id for u in all_users if u.username != user_name}
            _toggle_sel  = st.selectbox("Select user to toggle", list(_toggle_opts.keys()), key="admin_toggle_user")
            _toggle_id   = _toggle_opts.get(_toggle_sel)
            _toggle_u    = user_repository.get_user_by_id(db, _toggle_id) if _toggle_id else None
            if _toggle_u:
                _is_active_now = _toggle_u.is_active if _toggle_u.is_active is not None else True
                _toggle_label  = "🚫 Deactivate" if _is_active_now else "✅ Reactivate"
                if st.button(_toggle_label, key="admin_toggle_btn"):
                    _toggle_u.is_active = not _is_active_now
                    db.commit()
                    log_event(db, "user_toggled", user_name,
                              f"Admin {'deactivated' if _is_active_now else 'reactivated'} user '{_toggle_u.username}'")
                    st.toast(f"✅ User '{_toggle_u.username}' {'deactivated' if _is_active_now else 'reactivated'}.")
                    st.rerun()
        # ── Clear Data — Admin only ───────────────────────────────────────
        st.divider()
        st.markdown("#### 🗑️ Clear Database")
        st.markdown(
            "<p style='font-size:0.82rem;color:#6b7280;margin-top:-0.4rem;margin-bottom:1rem;'>"
            "Permanently delete records from specific areas of the database. User accounts are never affected. "
            "Each action requires a confirmation click.</p>",
            unsafe_allow_html=True
        )

        # Show success message from previous clear action (before rerun clears it)
        if st.session_state.get("db_clear_success_msg"):
            st.success(st.session_state.pop("db_clear_success_msg"))

        # Session state keys to reset when their backing data is deleted
        _table_session_keys = {
            "course_design_documents": ["active_cdd_id"],
            "cdd_versions":            ["active_cdd_id"],
            "module_blueprints":       ["active_blueprint_id"],
            "blueprint_versions":      ["active_blueprint_id"],
            "generations":             [],
            "blocks":                  [],
            "styles":                  [],
            "style_documents":         [],
            "documents":               [],
            "prompts":                 [],
            "prompt_versions":         [],
            "reviews":                 [],
            "feedback_signals":        [],
            "workflow_events":         [],
            "system_logs":             [],
        }

        # Helper: run deletes + clear related session state + log + rerun
        def _clear_tables(tables: list[str], label: str):
            with engine.begin() as _conn:
                for tbl in tables:
                    _conn.execute(text(f"DELETE FROM {tbl}"))
            # Reset any session state keys tied to deleted data
            _keys_to_clear = set()
            for tbl in tables:
                _keys_to_clear.update(_table_session_keys.get(tbl, []))
            for _k in _keys_to_clear:
                st.session_state.pop(_k, None)
            # If blueprints were cleared, bump the form version so the
            # module selectbox gets a fresh key (no stale "✓" cached value)
            if "module_blueprints" in tables or "blueprint_versions" in tables:
                st.session_state["bp_clear_ver"] = st.session_state.get("bp_clear_ver", 0) + 1
            log_event(db, "db_cleared", user_name, f"Admin cleared: {label}")
            st.session_state["db_clear_success_msg"] = f"✅ {label} cleared successfully."
            st.rerun()

        # Confirmation state key helper
        def _confirm_key(tag: str) -> str:
            return f"db_clear_confirm_{tag}"

        _clear_items = [
            {
                "tag":    "cdds",
                "label":  "Clear Course Design Documents (CDDs)",
                "info":   "Removes all CDD drafts and their version history. Style and Blueprint data is kept.",
                "tables": ["cdd_versions", "course_design_documents"],
            },
            {
                "tag":    "blueprints",
                "label":  "Clear Module Blueprints",
                "info":   "Removes all blueprint drafts and their version history. CDDs and Lessons are kept.",
                "tables": ["blueprint_versions", "module_blueprints"],
            },
            {
                "tag":    "generations",
                "label":  "Clear Generated Lessons & Blocks",
                "info":   "Removes all generated lesson content, individual blocks, and block-level comments.",
                "tables": ["block_comments", "blocks", "generations"],
            },
            {
                "tag":    "styles",
                "label":  "Clear Styles & Style Documents",
                "info":   "Removes all instructional styles and their linked document associations. Uploaded documents themselves are kept.",
                "tables": ["style_documents", "styles"],
            },
            {
                "tag":    "documents",
                "label":  "Clear Uploaded Documents",
                "info":   "Removes all uploaded reference documents (PDF, DOCX, etc.) from the library.",
                "tables": ["documents"],
            },
            {
                "tag":    "prompts",
                "label":  "Clear Prompts & Prompt Versions",
                "info":   "Removes all prompt records and every version stored under them.",
                "tables": ["prompt_versions", "prompts"],
            },
            {
                "tag":    "reviews",
                "label":  "Clear Reviews & Feedback",
                "info":   "Removes all review records and user feedback signals collected during the review workflow.",
                "tables": ["reviews", "feedback_signals"],
            },
            {
                "tag":    "logs",
                "label":  "Clear Activity Logs",
                "info":   "Removes system event logs and workflow audit trail. Does not affect any content.",
                "tables": ["workflow_events", "system_logs"],
            },
        ]

        for _item in _clear_items:
            _c1, _c2 = st.columns([3, 1])
            _c1.markdown(f"**{_item['label']}**")
            _c1.markdown(
                f"<span style='font-size:0.78rem;color:#6b7280;'>{_item['info']}</span>",
                unsafe_allow_html=True
            )
            _ckey = _confirm_key(_item["tag"])
            if not st.session_state.get(_ckey, False):
                if _c2.button("🗑️ Clear", key=f"clear_btn_{_item['tag']}", use_container_width=True):
                    st.session_state[_ckey] = True
                    st.rerun()
            else:
                if _c2.button("⚠️ Confirm Clear", key=f"clear_confirm_{_item['tag']}", use_container_width=True, type="primary"):
                    _clear_tables(_item["tables"], _item["label"])
                if _c2.button("Cancel", key=f"clear_cancel_{_item['tag']}", use_container_width=True):
                    st.session_state[_ckey] = False
                    st.rerun()
            st.markdown("<hr style='margin:6px 0;border-color:#f1f5f9;'>", unsafe_allow_html=True)

        # ── Clear Everything (except users) ───────────────────────────────
        st.markdown("##### ⚠️ Clear Everything")
        st.markdown(
            "<span style='font-size:0.78rem;color:#6b7280;'>"
            "Wipes all CDDs, Blueprints, Lessons, Styles, Documents, Prompts, Reviews, and Logs in one shot. "
            "User accounts are preserved.</span>",
            unsafe_allow_html=True
        )
        _all_tables = [
            "block_comments", "blocks", "generations",
            "blueprint_versions", "module_blueprints",
            "cdd_versions", "course_design_documents",
            "style_documents", "styles",
            "documents",
            "prompt_versions", "prompts",
            "reviews", "feedback_signals",
            "workflow_events", "system_logs",
        ]
        _all_key = _confirm_key("everything")
        _ea1, _ea2 = st.columns([3, 1])
        if not st.session_state.get(_all_key, False):
            if _ea2.button("🗑️ Clear Everything", key="clear_all_btn", use_container_width=True):
                st.session_state[_all_key] = True
                st.rerun()
        else:
            _ea1.warning("This will permanently delete ALL data except user accounts. Are you sure?")
            _ca1, _ca2 = _ea2.columns(2)
            if _ca1.button("✅ Yes", key="clear_all_confirm", use_container_width=True, type="primary"):
                _clear_tables(_all_tables, "Everything (all data except users)")
            if _ca2.button("❌ No", key="clear_all_cancel", use_container_width=True):
                st.session_state[_all_key] = False
                st.rerun()

    elif rbac_check(user_role, "users.assign") and not rbac_check(user_role, "users.create"):
        # Lead can manage user assignments at project level, but cannot create/delete accounts
        st.divider()
        st.markdown("#### 👥 User Assignment (Lead Scope)")
        st.markdown(
            "<p style='font-size:0.82rem;color:#6b7280;margin-top:-0.4rem;margin-bottom:1rem;'>"
            "As Lead, you can manage user access within your assigned projects and courses. "
            "Use the Project and Course pages to assign users.</p>",
            unsafe_allow_html=True
        )
        _lead_projects = _get_user_projects(db, user_name, user_role)
        if _lead_projects:
            for _lp in _lead_projects:
                with st.expander(f"📁 {_lp.name}", expanded=False):
                    all_non_admin_lead = user_repository.list_non_admin_active_users(db)
                    existing_pa = project_repository.get_assigned_usernames(db, _lp.id)
                    for _u in all_non_admin_lead:
                        _is_a = _u.username in existing_pa
                        _nv = st.checkbox(
                            f"{_u.username} ({role_label(_u.role)})", value=_is_a,
                            key=f"lead_asgn_{_lp.id}_{_u.username}"
                        )
                        if _nv and not _is_a:
                            db.add(ProjectUserAssignment(project_id=_lp.id, username=_u.username))
                            db.commit()
                            st.toast(f"✅ {_u.username} added to {_lp.name}.")
                        elif not _nv and _is_a:
                            db.query(ProjectUserAssignment).filter(
                                ProjectUserAssignment.project_id == _lp.id,
                                ProjectUserAssignment.username == _u.username
                            ).delete()
                            db.commit()
                            st.toast(f"🔒 {_u.username} removed from {_lp.name}.")
        else:
            st.info("You are not assigned to any projects yet.")
    else:
        st.info("🔒 User Management is only available to Admins and Leads.")

    # =========================================================================
    # PERMISSION MATRIX (visible to all roles — shows own access summary)
    # =========================================================================
    st.divider()
    with st.expander("🔐 Role & Permission Reference", expanded=False):
        from promptops_app.ui.rbac_ui import render_my_permissions, render_permission_matrix
        _pm_tab1, _pm_tab2 = st.tabs(["My Permissions", "Full Matrix"])
        with _pm_tab1:
            render_my_permissions(user_role)
        with _pm_tab2:
            if rbac_check(user_role, "users.view"):
                render_permission_matrix()
            else:
                st.info("The full matrix is visible to Admin and Lead roles.")

    # =========================================================================
    # LLM COST DASHBOARD  (Admin + Lead; ID gets personal summary only)
    # =========================================================================
    st.divider()
    _render_cost_dashboard(db, ctx)

    # =========================================================================
    # AUDIT TRAIL  (Lead + Admin)
    # =========================================================================
    if not (_is_admin or _is_lead):
        return

    st.divider()
    st.markdown(
        _section_badge("🗒️", "Audit Trail",
            "Structured log of every significant action — workflow transitions, "
            "approvals, exports, logins, role changes, and more."),
        unsafe_allow_html=True,
    )

    from promptops_app.repositories.audit_repository import (
        export_to_csv_rows,
        list_distinct_actors as _audit_actors,
        list_distinct_actions as _audit_actions,
        list_distinct_entity_types as _audit_entities,
    )
    from promptops_app.ui.pagination import paginate as _at_paginate

    # ── Filter row 1: user / action / entity ────────────────────────────────
    _af1, _af2, _af3 = st.columns(3)
    with _af1:
        _actor_opts = ["All users"] + (_audit_actors(db) if _is_admin else [user_name])
        _at_actor_sel = st.selectbox("User", _actor_opts, key="at_actor")
        _at_actor = None if _at_actor_sel == "All users" else _at_actor_sel
        if not _is_admin:
            _at_actor = user_name
            st.caption("Showing your events only.")
    with _af2:
        _action_opts = ["All actions"] + _audit_actions(db)
        _at_action_sel = st.selectbox("Action", _action_opts, key="at_action")
        _at_action = None if _at_action_sel == "All actions" else _at_action_sel
    with _af3:
        _entity_opts = ["All entities"] + _audit_entities(db)
        _at_entity_sel = st.selectbox("Entity type", _entity_opts, key="at_entity")
        _at_entity = None if _at_entity_sel == "All entities" else _at_entity_sel

    # ── Filter row 2: project / course / date range ─────────────────────────
    _af4, _af5, _af6 = st.columns(3)
    with _af4:
        _at_proj_id = None
        if _is_admin:
            _proj_opts_an = {p.name: p.id for p in analytics_repository.list_active_projects_for_analytics(db)}
            _proj_sel_an  = st.selectbox("Project", ["All projects"] + list(_proj_opts_an.keys()), key="at_project")
            _at_proj_id   = _proj_opts_an.get(_proj_sel_an)
        else:
            st.caption(f"Project: {_proj_name}")
            _at_proj_id = _proj_id
    with _af5:
        _at_date_range = st.date_input(
            "Date range",
            value=(),
            help="Leave empty for all dates. Select one date or a range.",
            key="at_date_range",
        )
    with _af6:
        _at_page_size = st.selectbox("Rows per page", [25, 50, 100], index=0, key="at_page_size")

    # ── Parse date range ────────────────────────────────────────────────────
    _at_date_from = None
    _at_date_to   = None
    if isinstance(_at_date_range, (list, tuple)) and len(_at_date_range) >= 1:
        from datetime import datetime as _dt, timezone as _tz
        _at_date_from = _dt.combine(_at_date_range[0], _dt.min.time())
        if len(_at_date_range) >= 2:
            _at_date_to = _dt.combine(_at_date_range[1], _dt.max.time())

    # ── Count + paginate ─────────────────────────────────────────────────────
    _at_total = count_trail(
        db,
        user_id     = _at_actor,
        action      = _at_action,
        entity_type = _at_entity,
        project_id  = _at_proj_id,
        date_from   = _at_date_from,
        date_to     = _at_date_to,
    )

    if _at_total == 0:
        st.info("No audit events match your filters.")
    else:
        _at_offset, _ = _at_paginate(_at_total, _at_page_size, "audit_trail_page")
        _at_records = get_audit_trail(
            db,
            actor       = _at_actor,
            event_type  = _at_action,
            entity      = _at_entity,
            project_id  = _at_proj_id,
            date_from   = _at_date_from,
            date_to     = _at_date_to,
            limit       = _at_page_size,
            offset      = _at_offset,
        )

        _at_rows = []
        for rec in _at_records:
            meta = get_event_meta(rec.action)
            _at_rows.append({
                "When":         rec.created_at.strftime("%Y-%m-%d %H:%M:%S") if rec.created_at else "—",
                "User":         rec.user_id or "—",
                "Action":       f"{meta['icon']} {rec.action}",
                "Label":        meta.get("label", rec.action),
                "Entity type":  rec.entity_type or "—",
                "Entity ID":    rec.entity_id  or "—",
                "Project":      str(rec.project_id) if rec.project_id else "—",
                "IP":           rec.ip_address or "—",
            })

        st.caption(f"{_at_total} event(s) total · showing page results")
        st.dataframe(_at_rows, use_container_width=True, hide_index=True)

        # ── CSV export ───────────────────────────────────────────────────────
        if rbac_check(user_role, "export.audit_log"):
            import csv, io as _io
            _csv_rows = export_to_csv_rows(
                db, user_id=_at_actor, action=_at_action, entity_type=_at_entity,
                project_id=_at_proj_id, date_from=_at_date_from, date_to=_at_date_to,
            )
            _csv_buf = _io.StringIO()
            if _csv_rows:
                _csv_wr = csv.DictWriter(_csv_buf, fieldnames=list(_csv_rows[0].keys()))
                _csv_wr.writeheader(); _csv_wr.writerows(_csv_rows)
            from promptops_app.services.audit_service import log_audit_event as _lae2
            col_exp, _ = st.columns([1, 3])
            if col_exp.download_button(
                "⬇️ Export to CSV",
                data=_csv_buf.getvalue().encode(),
                file_name="audit_trail.csv",
                mime="text/csv",
            ):
                _lae2(db, user_name, "export.audit_log", project_id=_proj_id,
                      metadata={"rows": len(_csv_rows), "filters": {"action": _at_action, "actor": _at_actor}})


# =============================================================================
# LLM Cost Dashboard renderer
# =============================================================================

def _render_cost_dashboard(db, ctx):
    """Render the LLM Cost Dashboard section inside analytics.render_page().

    Visibility:
      Admin  — full cross-project view + all users
      Lead   — project-scoped view (assigned projects only)
      ID     — personal usage summary only (no costs)
    """
    from promptops_app.auth.permissions import rbac_check
    from promptops_app.ui.components import _section_badge

    _is_admin = ctx.is_admin
    _is_lead  = ctx.is_lead
    _user     = ctx.user_name
    _proj_id  = ctx.project_id

    # All three roles can see the section (ID sees limited personal view)
    st.markdown(
        _section_badge("💰", "LLM Cost Dashboard",
            "Token usage, estimated cost, and latency across all AI generation calls."),
        unsafe_allow_html=True,
    )

    # ── Filter bar ──────────────────────────────────────────────────────────
    _fc1, _fc2, _fc3, _fc4 = st.columns(4)
    with _fc1:
        _cost_date = st.date_input(
            "Date range", value=(), key="cost_date_range",
            help="Leave empty for all time. Select one date or a range.",
        )
    with _fc2:
        _all_models = usage_repository.list_distinct_models(db)
        _sel_model  = st.selectbox("Model", ["All models"] + _all_models, key="cost_model")
        _model_filter = None if _sel_model == "All models" else _sel_model
    with _fc3:
        if _is_admin:
            _all_users_cost = usage_repository.list_distinct_users(db)
            _sel_user  = st.selectbox("User", ["All users"] + _all_users_cost, key="cost_user")
            _user_filter = None if _sel_user == "All users" else _sel_user
        else:
            _user_filter = _user  # Lead/ID always scoped to self or project
            st.caption(f"User: **{_user}**" if not _is_lead else "Showing project scope")
    with _fc4:
        _entity_types = usage_repository.list_distinct_entity_types(db)
        _sel_etype = st.selectbox("Call type", ["All types"] + _entity_types, key="cost_etype")
        _etype_filter = None if _sel_etype == "All types" else _sel_etype

    # Parse date range
    _cost_date_from = _cost_date_to = None
    if isinstance(_cost_date, (list, tuple)):
        if len(_cost_date) >= 1:
            from datetime import datetime as _dt
            _cost_date_from = _dt.combine(_cost_date[0], _dt.min.time())
        if len(_cost_date) >= 2:
            _cost_date_to = _dt.combine(_cost_date[1], _dt.max.time())

    # Scope: Admin sees all; Lead sees assigned project; ID sees own calls only
    _scope_project = None if _is_admin else _proj_id
    _scope_user    = _user_filter if _is_admin else (_user if not _is_lead else None)

    # ── Summary KPIs ────────────────────────────────────────────────────────
    try:
        _kpi = usage_repository.get_summary(
            db,
            user_name  = _scope_user,
            project_id = _scope_project,
            date_from  = _cost_date_from,
            date_to    = _cost_date_to,
        )
    except Exception:
        st.info("No LLM usage data yet. Cost metrics will appear after the first AI generation.")
        return

    _k1, _k2, _k3, _k4, _k5 = st.columns(5)
    _k1.metric("Total Calls",     _kpi["total_calls"])
    _k2.metric("Total Tokens",    f"{_kpi['total_tokens']:,}")
    if _is_admin or _is_lead:
        _k3.metric("Est. Cost (USD)", f"${_kpi['total_cost']:.4f}")
        _k4.metric("Failed Calls",    _kpi["failed_calls"])
        _k5.metric("Avg Latency",     f"{_kpi['avg_duration_ms']:.0f} ms")
    else:
        _k3.metric("Input Tokens",  f"{_kpi['total_input_tokens']:,}")
        _k4.metric("Output Tokens", f"{_kpi['total_output_tokens']:,}")
        _k5.metric("Failed Calls",  _kpi["failed_calls"])

    if _kpi["total_calls"] == 0:
        st.info("No LLM calls recorded yet under the current filters.")
        return

    # ID role: personal summary only — no detailed tables
    if not (_is_admin or _is_lead):
        st.caption("Contact your Admin or Lead for cost breakdowns.")
        return

    # ── Tabs for breakdown dimensions ───────────────────────────────────────
    _tabs = ["By Model", "Monthly Trend", "By Project", "By Course", "By User", "Detail Log"]
    _t_model, _t_monthly, _t_proj, _t_course, _t_user, _t_detail = st.tabs(_tabs)

    with _t_model:
        st.caption("Cost and token totals grouped by model — useful for provider comparison.")
        _model_rows = usage_repository.cost_by_model(
            db, user_name=_scope_user, project_id=_scope_project,
            date_from=_cost_date_from, date_to=_cost_date_to,
        )
        if _model_rows:
            st.dataframe(_model_rows, use_container_width=True, hide_index=True)
            # Simple bar data for cost per model
            _bar_data = {r["Model"]: r["Cost ($)"] for r in _model_rows if r["Cost ($)"] > 0}
            if _bar_data:
                st.bar_chart(_bar_data, height=220)
        else:
            st.info("No data for this filter.")

    with _t_monthly:
        st.caption("Monthly token and cost trends — last 6 months.")
        _monthly_rows = usage_repository.monthly_usage(
            db,
            user_name  = _scope_user,
            project_id = _scope_project,
        )
        if _monthly_rows:
            st.dataframe(_monthly_rows, use_container_width=True, hide_index=True)
            _month_cost = {r["Month"]: r["Cost ($)"] for r in _monthly_rows}
            if any(v > 0 for v in _month_cost.values()):
                st.line_chart(_month_cost, height=220)
        else:
            st.info("No monthly data available.")

    with _t_proj:
        if not _is_admin:
            st.info("Project-level breakdown is available to Admin only.")
        else:
            st.caption("Cost attribution by project — includes token totals and call count.")
            _proj_rows = usage_repository.cost_by_project(
                db, date_from=_cost_date_from, date_to=_cost_date_to,
            )
            if _proj_rows:
                st.dataframe(_proj_rows, use_container_width=True, hide_index=True)
            else:
                st.info("No project-level data yet.")

    with _t_course:
        st.caption("Cost attribution by course.")
        _course_rows = usage_repository.cost_by_course(
            db,
            project_id = _scope_project,
            user_name  = _scope_user if not _is_admin else None,
            date_from  = _cost_date_from,
            date_to    = _cost_date_to,
        )
        if _course_rows:
            st.dataframe(_course_rows, use_container_width=True, hide_index=True)
        else:
            st.info("No course-level data yet.")

    with _t_user:
        if not _is_admin:
            st.info("Per-user breakdown is available to Admin only.")
        else:
            st.caption("Cost attribution by user — useful for chargeback and quota management.")
            _user_rows = usage_repository.cost_by_user(
                db, project_id=_scope_project,
                date_from=_cost_date_from, date_to=_cost_date_to,
            )
            if _user_rows:
                st.dataframe(_user_rows, use_container_width=True, hide_index=True)
            else:
                st.info("No per-user data yet.")

    with _t_detail:
        st.caption("Full paginated call log with filters.")
        from promptops_app.ui.pagination import paginate as _u_pag

        _detail_status = st.selectbox(
            "Status filter",
            ["All", "success", "retry_success", "fallback_success", "error"],
            key="cost_detail_status",
        )
        _d_status = None if _detail_status == "All" else _detail_status

        _total_detail = usage_repository.count_usage_logs(
            db,
            user_name   = _scope_user,
            project_id  = _scope_project,
            model_name  = _model_filter,
            status      = _d_status,
            entity_type = _etype_filter,
            date_from   = _cost_date_from,
            date_to     = _cost_date_to,
        )
        st.caption(f"{_total_detail:,} log entries match filters.")
        if _total_detail > 0:
            _d_offset, _ = _u_pag(_total_detail, 50, "cost_detail_page")
            _detail_rows = usage_repository.list_usage_logs(
                db,
                user_name   = _scope_user,
                project_id  = _scope_project,
                model_name  = _model_filter,
                status      = _d_status,
                entity_type = _etype_filter,
                date_from   = _cost_date_from,
                date_to     = _cost_date_to,
                limit=50, offset=_d_offset,
            )
            st.dataframe(
                [{
                    "When":      r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "—",
                    "User":      r.user_id or "—",
                    "Type":      r.entity_type or "—",
                    "Model":     (r.model_name or "—")[:40],
                    "In tok":    r.input_tokens or 0,
                    "Out tok":   r.output_tokens or 0,
                    "Cost ($)":  round(r.estimated_cost or 0, 5),
                    "ms":        r.duration_ms or 0,
                    "Status":    r.status,
                    "Template":  (r.prompt_template or "—")[:30],
                } for r in _detail_rows],
                hide_index=True, use_container_width=True,
            )

