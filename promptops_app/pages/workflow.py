"""Workflow page renderer — Approval pipeline for generated content.

Flow:
  ID creates content → submits for review → Lead reviews →
  approves OR requests changes → Admin/Lead publishes → Admin archives.

All state transitions are delegated to workflow_service.py.
RBAC is enforced via rbac_check() before every action button.
"""

import streamlit as st

from promptops_app.database import (
    Block, Generation, Project, User, WorkflowEvent,
    settings, log_event, apply_transition_local,
)
from promptops_app.auth.permissions import rbac_check, rbac_gate
from promptops_app.core.config import PROMPTOPS_WORKFLOW_PAGE_SIZE
from promptops_app.core.constants import WorkflowState
from promptops_app.repositories import (
    generation_repository, project_repository, user_repository, course_repository,
)
from promptops_app.services.workflow_service import (
    submit_for_review,
    approve_block,
    request_changes,
    reject_block,
    publish_block,
    archive_block,
    bulk_approve,
    get_pending_for_reviewer,
    get_sla_status,
)
from promptops_app.ui.components import inject_premium_style, status_badge, _section_badge, _info_card


# ── Status badge helper (uses WorkflowState.badge_html) ──────────────────────

def _wf_badge(state: str) -> str:
    return WorkflowState.badge_html(state)


def render_page(db, ctx):
    user_role  = ctx.user_role
    user_name  = ctx.user_name
    _proj_id   = ctx.project_id
    _proj_name = ctx.project_name
    _crs_id    = ctx.course_id
    _crs_name  = ctx.course_name
    _is_admin  = ctx.is_admin
    _is_lead   = ctx.is_lead
    sla_hours  = settings.approval_sla_hours

    inject_premium_style()
    st.markdown(
        _section_badge("🚦", "Content Lifecycle",
            "Manage the approval pipeline — submit, review, approve, publish, and archive content blocks."),
        unsafe_allow_html=True,
    )

    # ── Reviewer queue banner ─────────────────────────────────────────────────
    if _is_lead or _is_admin:
        pending = get_pending_for_reviewer(db, user_name)
        if pending:
            st.markdown(
                _info_card(
                    f"📬 <strong>You have {len(pending)} block(s) pending your review.</strong> "
                    "See the <em>Approval Center</em> below.",
                    "#f59e0b",
                ),
                unsafe_allow_html=True,
            )

    # ── Helper: scope-aware block query ──────────────────────────────────────
    def _scoped_blocks(state_filter=None, course_id=None, project_id=None):
        return generation_repository.list_workflow_blocks_scoped(
            db,
            user_name    = user_name,
            project_id   = project_id if project_id is not None else _proj_id,
            is_admin     = _is_admin,
            is_lead      = _is_lead,
            course_id    = course_id,
            state_filter = state_filter,
            page_size    = PROMPTOPS_WORKFLOW_PAGE_SIZE,
        )

    # =========================================================================
    # FILTER BAR
    # =========================================================================
    st.markdown("#### 🔍 Filters")
    _f1, _f2, _f3, _f4 = st.columns([2, 2, 2, 2])

    _state_options = ["All statuses"] + [WorkflowState.label(s) for s in WorkflowState.ALL]
    _state_sel     = _f1.selectbox("Status", _state_options, key="wf_filter_status")
    _state_db      = (
        None if _state_sel == "All statuses"
        else {WorkflowState.label(s): s for s in WorkflowState.ALL}.get(_state_sel)
    )

    _reviewer_names = ["All reviewers"] + [u.username for u in user_repository.list_reviewers_and_admins(db)]
    _reviewer_sel   = _f2.selectbox("Reviewer", _reviewer_names, key="wf_filter_reviewer")
    _reviewer_db    = None if _reviewer_sel == "All reviewers" else _reviewer_sel

    _search_q = _f3.text_input("🔍 Search label", placeholder="e.g. Lesson 1", key="wf_search")

    if _is_admin:
        _all_proj = project_repository.list_active_projects(db)
        _proj_opts = {"All projects": None, **{p.name: p.id for p in _all_proj}}
        _proj_sel  = _f4.selectbox("Project", list(_proj_opts.keys()), key="wf_filter_proj")
        _filter_proj_id = _proj_opts.get(_proj_sel)
    else:
        _filter_proj_id = _proj_id
        _f4.caption(f"Project: **{_proj_name}**")

    # Course filter (second row)
    _f5, _f6 = st.columns([2, 6])
    _scope_proj_for_courses = _filter_proj_id or _proj_id
    if _scope_proj_for_courses:
        _courses_for_proj = course_repository.list_courses_for_project(db, _scope_proj_for_courses)
        if _courses_for_proj:
            _course_opts = {"All courses": None, **{c.name: c.id for c in _courses_for_proj}}
            _course_sel  = _f5.selectbox("Course", list(_course_opts.keys()), key="wf_filter_course")
            _filter_course_id = _course_opts.get(_course_sel)
        else:
            _filter_course_id = None
            _f5.caption("No courses in this project.")
    else:
        _filter_course_id = None

    # =========================================================================
    # KANBAN STATUS OVERVIEW
    # =========================================================================
    st.subheader("📋 Status Overview")
    if not _is_admin:
        st.caption(f"Your workflow — project **{_proj_name}** · course **{_crs_name}**")
    else:
        st.caption("Admin view — all projects and users.")

    _KANBAN_COLS = [
        (WorkflowState.DRAFT,              "#94a3b8"),
        (WorkflowState.IN_REVIEW,          "#f59e0b"),
        (WorkflowState.CHANGES_REQUESTED,  "#f97316"),
        (WorkflowState.APPROVED,           "#10b981"),
        (WorkflowState.PUBLISHED,          "#6366f1"),
        (WorkflowState.ARCHIVED,           "#6b7280"),
    ]

    kan_cols = st.columns(len(_KANBAN_COLS))
    for i, (state, color) in enumerate(_KANBAN_COLS):
        with kan_cols[i]:
            state_blocks = _scoped_blocks(state, course_id=_filter_course_id, project_id=_filter_proj_id)
            lbl = WorkflowState.label(state)
            st.markdown(
                f"<div style='background:rgba(148,163,184,0.05);padding:10px;"
                f"border-radius:8px;text-align:center;border-bottom:2px solid {color};"
                f"font-weight:600;margin-bottom:10px;'>{lbl}"
                f"<span style='font-size:0.8rem;color:{color};margin-left:6px;'>"
                f"({len(state_blocks)})</span></div>",
                unsafe_allow_html=True,
            )
            if not state_blocks:
                st.markdown(
                    "<p style='text-align:center;color:#64748b;font-size:0.8rem;"
                    "font-style:italic;'>—</p>",
                    unsafe_allow_html=True,
                )
            for b in state_blocks[:5]:
                sla = get_sla_status(b, sla_hours)
                sla_html = (
                    f"<span style='font-size:0.65rem;color:#f59e0b;'> {sla['label']}</span>"
                    if sla.get("label") else ""
                )
                rev_html = (
                    f"<span style='font-size:0.65rem;color:#94a3b8;'> → {b.assigned_reviewer}</span>"
                    if b.assigned_reviewer else ""
                )
                st.markdown(
                    f"<div style='background:rgba(255,255,255,0.03);padding:8px;"
                    f"border-radius:6px;margin-bottom:6px;border-left:3px solid {color}22;'>"
                    f"<p style='font-size:0.85rem;margin-bottom:2px;font-weight:500;'>"
                    f"{b.block_label[:25]}…</p>"
                    f"<p style='font-size:0.7rem;color:#94a3b8;'>"
                    f"#{b.id}{rev_html}{sla_html}</p></div>",
                    unsafe_allow_html=True,
                )

    st.divider()

    # =========================================================================
    # APPROVAL CENTER
    # =========================================================================
    st.subheader("⚙️ Approval Center")

    # Build filtered block list
    all_blocks = _scoped_blocks(_state_db, course_id=_filter_course_id, project_id=_filter_proj_id)

    # Apply reviewer filter
    if _reviewer_db:
        all_blocks = [b for b in all_blocks if b.assigned_reviewer == _reviewer_db]

    # Apply label search
    if _search_q:
        all_blocks = [b for b in all_blocks if _search_q.lower() in (b.block_label or "").lower()]

    if not all_blocks:
        st.info("No content blocks match your filters. Generate content from the **Generate** tab to begin.")
        _render_bulk_and_breakdown(
            db, user_name, user_role, _is_admin, _proj_id, sla_hours,
            lambda s=None: _scoped_blocks(s, course_id=_filter_course_id, project_id=_filter_proj_id),
        )
        return

    # Reviewer dropdown
    reviewers      = user_repository.list_reviewers_and_admins(db)
    reviewer_names = [u.username for u in reviewers]

    block_map = {
        f"#{b.id} — {b.block_label[:40]} [{WorkflowState.label(b.workflow_state)}]": b.id
        for b in all_blocks
    }
    sel_label = st.selectbox("Select Block", list(block_map.keys()))
    blk = generation_repository.get_block_by_id(db, block_map[sel_label])

    if not blk:
        st.warning("Block not found.")
        return

    state_lwr = blk.workflow_state.lower()
    sla_info  = get_sla_status(blk, sla_hours)

    ac_left, ac_right = st.columns([0.45, 0.55])

    # ── Left: block metadata ─────────────────────────────────────────────────
    with ac_left:
        st.markdown(f"**Block #{blk.id}** — {blk.block_label}")
        st.markdown(_wf_badge(blk.workflow_state), unsafe_allow_html=True)

        meta_rows = []
        if blk.submitted_by:
            meta_rows.append(f"📤 Submitted by: **{blk.submitted_by}**")
        if blk.assigned_reviewer:
            meta_rows.append(f"👤 Reviewer: **{blk.assigned_reviewer}**")
        if blk.approved_by:
            meta_rows.append(f"✅ Approved by: **{blk.approved_by}**")
        if blk.reviewed_by and blk.workflow_state.lower() in (
            WorkflowState.CHANGES_REQUESTED, WorkflowState.REJECTED,
        ):
            meta_rows.append(f"🔍 Reviewed by: **{blk.reviewed_by}**")
        if blk.review_comments and state_lwr in (
            WorkflowState.CHANGES_REQUESTED, WorkflowState.REJECTED, WorkflowState.APPROVED,
        ):
            meta_rows.append(f"💬 Comments: *{blk.review_comments[:120]}*")
        if blk.archived_by:
            meta_rows.append(f"🗄️ Archived by: **{blk.archived_by}**")
        if sla_info.get("label"):
            meta_rows.append(sla_info["label"])
        if blk.review_requested_at:
            meta_rows.append(
                f"🕐 Submitted: {blk.review_requested_at.strftime('%Y-%m-%d %H:%M')}"
            )
        for row in meta_rows:
            st.markdown(f"<small>{row}</small>", unsafe_allow_html=True)

        # Transition history
        events = generation_repository.list_workflow_events_for_block(db, blk.id)
        if events:
            with st.expander("📜 Transition History", expanded=False):
                for ev in events:
                    st.markdown(
                        f"<small style='color:#6b7280;'>"
                        f"`{ev.created_at.strftime('%m-%d %H:%M')}` "
                        f"**{ev.actor}** — {ev.from_state} → {ev.to_state} "
                        f"({ev.action})"
                        + (f" · _{ev.comment[:60]}_" if ev.comment else "")
                        + "</small>",
                        unsafe_allow_html=True,
                    )

    # ── Right: available actions ──────────────────────────────────────────────
    with ac_right:
        st.markdown("**Available Actions**")

        # ── SUBMIT FOR REVIEW ─────────────────────────────────────────────────
        if state_lwr in (WorkflowState.DRAFT, WorkflowState.REJECTED,
                         WorkflowState.CHANGES_REQUESTED):
            if rbac_check(user_role, "workflow.submit"):
                # Authors can only submit their own blocks; leads and admins can submit any
                _blk_gen = generation_repository.get_generation_by_id(db, blk.generation_id)
                _is_block_owner = _blk_gen and _blk_gen.created_by == user_name
                if user_role == "author" and not _is_block_owner:
                    st.warning(
                        "🔒 You can only submit your own content. "
                        "This block was created by another user."
                    )
                else:
                    with st.form(f"submit_form_{blk.id}"):
                        sel_reviewer = st.selectbox(
                            "Assign Reviewer",
                            options=reviewer_names or ["(no reviewers available)"],
                        )
                        if st.form_submit_button("📤 Submit for Review", use_container_width=True):
                            if reviewer_names:
                                ok, err = submit_for_review(db, blk, sel_reviewer, user_name)
                                if ok:
                                    st.toast(f"✅ Block #{blk.id} submitted to {sel_reviewer}")
                                    st.rerun()
                                else:
                                    st.error(err)
                            else:
                                st.error("No reviewers available.")

        # ── APPROVE / REQUEST CHANGES / REJECT ────────────────────────────────
        if state_lwr == WorkflowState.IN_REVIEW:
            if rbac_check(user_role, "workflow.approve"):
                with st.form(f"review_form_{blk.id}"):
                    review_comment = st.text_area(
                        "Review Comment",
                        placeholder="Optional notes for the author…",
                        height=80,
                    )
                    _r1, _r2, _r3 = st.columns(3)

                    if _r1.form_submit_button("✅ Approve", use_container_width=True, type="primary"):
                        ok, err = approve_block(db, blk, user_name, comment=review_comment)
                        if ok:
                            st.toast(f"✅ Block #{blk.id} approved!")
                            st.rerun()
                        else:
                            st.error(err)

                    if _r2.form_submit_button("🔁 Request Changes", use_container_width=True):
                        if not review_comment.strip():
                            st.error("Please describe what changes are needed.")
                        else:
                            ok, err = request_changes(db, blk, user_name, reason=review_comment)
                            if ok:
                                st.toast(f"🔁 Changes requested for Block #{blk.id}.")
                                st.rerun()
                            else:
                                st.error(err)

                    if _r3.form_submit_button("❌ Reject", use_container_width=True):
                        st.session_state[f"_reject_mode_{blk.id}"] = True
                        st.rerun()

                if st.session_state.get(f"_reject_mode_{blk.id}"):
                    with st.form(f"reject_form_{blk.id}"):
                        reject_reason = st.text_area(
                            "Rejection Reason (required)",
                            placeholder="Explain why this content is rejected…",
                            height=80,
                        )
                        rj1, rj2 = st.columns(2)
                        if rj1.form_submit_button("Confirm Rejection", type="primary", use_container_width=True):
                            if not reject_reason.strip():
                                st.error("Please provide a rejection reason.")
                            else:
                                ok, err = reject_block(db, blk, user_name, reason=reject_reason)
                                if ok:
                                    st.session_state.pop(f"_reject_mode_{blk.id}", None)
                                    st.toast(f"Block #{blk.id} rejected.")
                                    st.rerun()
                                else:
                                    st.error(err)
                        if rj2.form_submit_button("Cancel", use_container_width=True):
                            st.session_state.pop(f"_reject_mode_{blk.id}", None)
                            st.rerun()

        # ── PUBLISH ───────────────────────────────────────────────────────────
        if state_lwr == WorkflowState.APPROVED:
            if rbac_check(user_role, "workflow.publish"):
                if st.button("🚀 Publish Block", key=f"publish_{blk.id}",
                             use_container_width=True, type="primary"):
                    ok, err = publish_block(db, blk, user_name)
                    if ok:
                        st.toast(f"🚀 Block #{blk.id} published!")
                        st.rerun()
                    else:
                        st.error(err)

        # ── RESET TO DRAFT ────────────────────────────────────────────────────
        if state_lwr in (WorkflowState.APPROVED, WorkflowState.REJECTED,
                         WorkflowState.CHANGES_REQUESTED):
            if rbac_check(user_role, "workflow.reset_draft"):
                if st.button("↩️ Reset to Draft", key=f"reset_{blk.id}", use_container_width=True):
                    apply_transition_local(db, blk, "reset_to_draft", user_name)
                    log_event(db, "workflow_transition", user_name,
                              f"Block #{blk.id} reset to Draft", {"block_id": blk.id})
                    st.toast("Block reset to Draft.")
                    st.rerun()

        # ── ARCHIVE ───────────────────────────────────────────────────────────
        if state_lwr in (WorkflowState.APPROVED, WorkflowState.PUBLISHED):
            if rbac_check(user_role, "workflow.archive"):
                if st.button("🗄️ Archive Block", key=f"archive_{blk.id}", use_container_width=True):
                    ok, err = archive_block(db, blk, user_name)
                    if ok:
                        st.toast(f"Block #{blk.id} archived.")
                        st.rerun()
                    else:
                        st.error(err)

        if state_lwr == WorkflowState.PUBLISHED:
            st.success("✅ This block is Published — no further transitions available.")

        if state_lwr == WorkflowState.ARCHIVED:
            st.info("🗄️ This block is Archived.")

    _render_bulk_and_breakdown(
        db, user_name, user_role, _is_admin, _proj_id, sla_hours,
        lambda s=None: _scoped_blocks(s, course_id=_filter_course_id, project_id=_filter_proj_id),
    )


# ── Bulk approve + admin breakdown (extracted to keep render_page readable) ──

def _render_bulk_and_breakdown(db, user_name, user_role, _is_admin, _proj_id,
                               sla_hours, _scoped_blocks_fn):

    # BULK APPROVE
    if rbac_check(user_role, "workflow.bulk_approve"):
        st.divider()
        st.subheader("⚡ Bulk Approve")
        in_review_blocks = _scoped_blocks_fn(WorkflowState.IN_REVIEW)
        if not in_review_blocks:
            st.info("No blocks are currently In Review.")
        else:
            bulk_options = {
                f"#{b.id} — {b.block_label[:40]}": b.id
                for b in in_review_blocks
            }
            selected_labels = st.multiselect(
                f"Select blocks to approve ({len(in_review_blocks)} in review)",
                list(bulk_options.keys()),
                default=list(bulk_options.keys()),
                key="wf_bulk_select",
            )
            if selected_labels:
                if st.button(
                    f"✅ Bulk Approve {len(selected_labels)} block(s)",
                    type="primary", use_container_width=True,
                ):
                    ids_to_approve = [bulk_options[lbl] for lbl in selected_labels]
                    result = bulk_approve(db, ids_to_approve, user_name)
                    st.success(
                        f"Approved: {len(result['approved'])}  |  "
                        f"Skipped: {len(result['skipped'])}  |  "
                        f"Errors: {len(result['errors'])}"
                    )
                    st.rerun()

    # ADMIN: Cross-project breakdown
    if rbac_check(user_role, "analytics.view_all"):
        st.divider()
        st.subheader("🗂️ Admin — All Projects · User Breakdown")
        all_projects = project_repository.list_active_projects(db)
        for proj in all_projects:
            proj_gen_ids = [g.id for g in generation_repository.list_generations_for_project(db, proj.id)]
            if not proj_gen_ids:
                continue
            with st.expander(f"📁 {proj.name}"):
                user_gen_map: dict = {}
                for g in generation_repository.list_generations_by_ids(db, proj_gen_ids):
                    user_gen_map.setdefault(g.created_by, []).append(g.id)
                for uname, ugen_ids in sorted(user_gen_map.items()):
                    ublocks = generation_repository.list_blocks_for_gen_ids(db, ugen_ids)
                    if not ublocks:
                        continue
                    st.markdown(f"**👤 {uname}** — {len(ublocks)} block(s)")
                    state_summary: dict = {}
                    for b in ublocks:
                        state_summary[b.workflow_state] = state_summary.get(b.workflow_state, 0) + 1
                    badge_row = "  ".join(
                        f"<span style='margin-right:4px;'>{WorkflowState.badge_html(s)}</span> {n}"
                        for s, n in sorted(state_summary.items())
                    )
                    st.markdown(badge_row, unsafe_allow_html=True)
