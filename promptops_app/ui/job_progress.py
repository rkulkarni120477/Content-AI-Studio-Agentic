"""Job progress UI components for the Generate page.

Public API
----------
render_active_job(db, job_id) -> bool
    Renders the running/completed/failed card for one job.
    Returns True if the job is still active (caller should sleep + rerun),
    False if the job has reached a terminal state or job_id is unknown.

render_job_history(db, user_name, project_id, is_admin)
    Renders a paginated table of recent jobs for this user / project.
"""

import json
import time

import streamlit as st

from promptops_app.jobs.job_status import ALL_STAGES, JobStatus
from promptops_app.repositories import job_repository
from promptops_app.ui.pagination import paginate

# ── Colour palette ─────────────────────────────────────────────────────────
_STATUS_COLOUR = {
    JobStatus.QUEUED:    ("#f59e0b", "#fffbeb"),   # amber
    JobStatus.RUNNING:   ("#6366f1", "#eef2ff"),   # indigo
    JobStatus.COMPLETED: ("#10b981", "#f0fdf4"),   # green
    JobStatus.FAILED:    ("#ef4444", "#fef2f2"),   # red
    JobStatus.CANCELLED: ("#6b7280", "#f9fafb"),   # grey
}
_STATUS_ICON = {
    JobStatus.QUEUED:    "🕐",
    JobStatus.RUNNING:   "⚙️",
    JobStatus.COMPLETED: "✅",
    JobStatus.FAILED:    "❌",
    JobStatus.CANCELLED: "✕",
}


# ── Stage stepper ─────────────────────────────────────────────────────────────

def _render_stage_stepper(current_progress: int) -> None:
    """Show a horizontal stepper that highlights completed / active stages."""
    cols = st.columns(len(ALL_STAGES))
    for i, (pct, label) in enumerate(ALL_STAGES):
        with cols[i]:
            if current_progress >= pct:
                st.markdown(
                    f"<div style='text-align:center;'>"
                    f"<div style='font-size:1.1rem;'>✅</div>"
                    f"<div style='font-size:0.65rem;color:#10b981;font-weight:700;'>{label}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            elif current_progress >= (ALL_STAGES[i - 1][0] if i > 0 else 0):
                st.markdown(
                    f"<div style='text-align:center;'>"
                    f"<div style='font-size:1.1rem;'>⚙️</div>"
                    f"<div style='font-size:0.65rem;color:#6366f1;font-weight:700;'>{label}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f"<div style='text-align:center;'>"
                    f"<div style='font-size:1.1rem;color:#d1d5db;'>○</div>"
                    f"<div style='font-size:0.65rem;color:#9ca3af;'>{label}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )


# ── Main active-job card ──────────────────────────────────────────────────────

def render_active_job(db, job_id: str) -> bool:
    """Render job status card.  Returns True while the job is still active."""
    job = job_repository.get_job(db, job_id)
    if not job:
        st.warning("⚠️ Job record not found.")
        return False

    colour, bg = _STATUS_COLOUR.get(job.status, ("#6b7280", "#f9fafb"))
    icon        = _STATUS_ICON.get(job.status, "•")

    # ── Status header card ────────────────────────────────────────────────
    st.markdown(
        f"<div style='background:{bg};border:1.5px solid {colour}33;"
        f"border-radius:12px;padding:14px 20px;margin-bottom:12px;'>"
        f"<div style='display:flex;align-items:center;gap:10px;'>"
        f"<span style='font-size:1.4rem;'>{icon}</span>"
        f"<div>"
        f"<div style='font-size:0.7rem;font-weight:700;text-transform:uppercase;"
        f"letter-spacing:.08em;color:{colour};margin-bottom:2px;'>"
        f"{job.status.upper()}</div>"
        f"<div style='font-size:0.92rem;font-weight:600;color:#111827;'>"
        f"{job.current_step or job.status.title()}</div>"
        f"</div></div></div>",
        unsafe_allow_html=True,
    )

    # ── Running state: progress bar + stage stepper + cancel ─────────────
    if job.status in JobStatus.ACTIVE:
        st.progress(job.progress / 100)
        _render_stage_stepper(job.progress)

        _c1, _c2 = st.columns([4, 1])
        _c1.caption(f"Job `{job.id[:8]}…` · started {job.created_at.strftime('%H:%M:%S') if job.created_at else '—'}")
        if _c2.button("✕ Cancel", key=f"cancel_job_{job_id}", use_container_width=True):
            ok, msg = job_repository.cancel_job(db, job_id, job.created_by, is_admin=True)
            if ok:
                st.toast("Job cancelled.")
            else:
                st.error(msg)
            st.rerun()

        # Poll every 2 s — blocks this session only
        time.sleep(2)
        st.rerun()
        return True

    # ── Completed state ────────────────────────────────────────────────────
    if job.status == JobStatus.COMPLETED:
        _res    = json.loads(job.result_json or "{}")
        _gen_id = _res.get("generation_id") or job.result_entity_id
        _n_blks = _res.get("blocks", 0)

        if _gen_id:
            st.session_state.last_gen_id = _gen_id

        st.success(
            f"✅ **Generation complete** — **{_n_blks}** block(s) created. "
            "Open the **Editor** tab to review and refine."
        )
        if _gen_id:
            st.markdown(
                f"<div style='background:#0f2027;border:1px solid #1e3a5f;border-radius:8px;"
                f"padding:10px 16px;font-size:0.83rem;color:#cbd5e1;margin:6px 0 12px;'>"
                f"🔗 Generation <strong>#{_gen_id}</strong> · "
                f"{_n_blks} block(s) queued for review</div>",
                unsafe_allow_html=True,
            )
        if st.button("🔄 Start Another Generation", key=f"clear_job_{job_id}", type="primary"):
            st.session_state.pop("active_gen_job_id", None)
            st.rerun()
        return False

    # ── Failed state ───────────────────────────────────────────────────────
    if job.status == JobStatus.FAILED:
        st.error("❌ **Generation failed.**")
        if job.error_message:
            with st.expander("Show error details", expanded=False):
                st.code(job.error_message, language=None)
        if st.button("🔄 Try Again", key=f"retry_job_{job_id}", type="primary"):
            st.session_state.pop("active_gen_job_id", None)
            st.rerun()
        return False

    # ── Cancelled state ────────────────────────────────────────────────────
    if job.status == JobStatus.CANCELLED:
        st.info("Generation job was cancelled.")
        if st.button("🔄 Start New Generation", key=f"clear_cancelled_{job_id}"):
            st.session_state.pop("active_gen_job_id", None)
            st.rerun()
        return False

    return False


# ── Job history table ─────────────────────────────────────────────────────────

def render_job_history(
    db,
    *,
    user_name: str,
    project_id: int = None,
    is_admin: bool = False,
    page_size: int = 10,
    page_key: str = "gen_job_history_page",
) -> None:
    """Render a paginated table of recent generation jobs."""
    total = job_repository.count_jobs(
        db, user_name=user_name, project_id=project_id, is_admin=is_admin,
    )
    if total == 0:
        st.info("No generation jobs yet.")
        return

    offset, _ = paginate(total, page_size, page_key)
    jobs = job_repository.list_jobs(
        db,
        user_name=user_name,
        project_id=project_id,
        is_admin=is_admin,
        limit=page_size,
        offset=offset,
    )

    rows = []
    for j in jobs:
        _res    = json.loads(j.result_json or "{}") if j.result_json else {}
        _n_blks = _res.get("blocks", "—")
        _dur    = "—"
        if j.completed_at and j.created_at:
            secs = int((j.completed_at - j.created_at).total_seconds())
            _dur = f"{secs}s" if secs < 60 else f"{secs // 60}m {secs % 60}s"
        rows.append({
            "ID":       j.id[:8] + "…",
            "Status":   f"{_STATUS_ICON.get(j.status, '•')} {j.status}",
            "Step":     (j.current_step or "—")[:40],
            "Blocks":   _n_blks,
            "Duration": _dur,
            "By":       j.created_by or "—",
            "Started":  j.created_at.strftime("%m-%d %H:%M") if j.created_at else "—",
        })

    st.dataframe(rows, hide_index=True, use_container_width=True)
