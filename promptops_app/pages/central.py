"""Central Repository page — Admin-only single source of truth for reusable prompts, assets, and learnings."""

import streamlit as st

from promptops_app.repositories import central_repository
from promptops_app.services import central_service
from promptops_app.database import Prompt
from promptops_app.ui.components import _section_badge


# ── helpers ───────────────────────────────────────────────────────────────────

def _status_chip(status: str) -> str:
    color = "#10b981" if status == "active" else "#9ca3af"
    label = status.capitalize()
    return (
        f"<span style='background:{color}22;color:{color};border:1px solid {color}55;"
        f"border-radius:12px;padding:2px 10px;font-size:0.72rem;font-weight:700;"
        f"letter-spacing:.04em;'>{label}</span>"
    )


def _type_chip(item_type: str) -> str:
    colors = {"Prompt": "#6366f1", "Asset": "#f59e0b", "Learning": "#10b981"}
    color = colors.get(item_type, "#6b7280")
    return (
        f"<span style='background:{color}22;color:{color};border:1px solid {color}55;"
        f"border-radius:12px;padding:2px 10px;font-size:0.72rem;font-weight:700;'>{item_type}</span>"
    )


def _metric_card(label: str, value, icon: str = "") -> str:
    return (
        f"<div style='background:rgba(255,255,255,0.06);border:1px solid rgba(165,180,252,0.18);"
        f"border-radius:10px;padding:14px 16px;text-align:center;'>"
        f"<div style='font-size:1.6rem;font-weight:800;color:#e0e7ff;'>{icon} {value}</div>"
        f"<div style='font-size:0.72rem;color:#a5b4fc;text-transform:uppercase;"
        f"letter-spacing:.08em;margin-top:3px;'>{label}</div>"
        f"</div>"
    )


def _fmt_date(dt) -> str:
    return dt.strftime("%d %b %Y") if dt else "—"


# ── main renderer ─────────────────────────────────────────────────────────────

def render_page(db, ctx):
    if not ctx.is_admin:
        st.error("🔒 **Access Denied** — Central Repository is available to Admin users only.")
        return

    user_name = ctx.user_name

    st.markdown(
        _section_badge(
            "🗄️",
            "Central Repository",
            "Admin-only single source of truth for reusable prompts, assets, and learnings across all projects.",
        ),
        unsafe_allow_html=True,
    )

    # ── Metrics row ───────────────────────────────────────────────────────────
    total_active   = central_repository.count_items(db, status="active")
    total_archived = central_repository.count_items(db, status="archived")
    total_all      = total_active + total_archived

    all_items_for_metrics = central_repository.list_items(db, status=None, limit=5000)
    prompt_count   = sum(1 for i in all_items_for_metrics if i.item_type == "Prompt")
    asset_count    = sum(1 for i in all_items_for_metrics if i.item_type == "Asset")
    learning_count = sum(1 for i in all_items_for_metrics if i.item_type == "Learning")

    mc1, mc2, mc3, mc4, mc5 = st.columns(5)
    mc1.markdown(_metric_card("Total Items", total_all, "🗄️"), unsafe_allow_html=True)
    mc2.markdown(_metric_card("Active", total_active, "✅"), unsafe_allow_html=True)
    mc3.markdown(_metric_card("Prompts", prompt_count, "💬"), unsafe_allow_html=True)
    mc4.markdown(_metric_card("Assets", asset_count, "📦"), unsafe_allow_html=True)
    mc5.markdown(_metric_card("Learnings", learning_count, "📖"), unsafe_allow_html=True)

    st.markdown("<div style='margin-top:18px;'></div>", unsafe_allow_html=True)

    # ── Create / Import panel ─────────────────────────────────────────────────
    with st.expander("➕ Add Item to Repository", expanded=False):
        tab_manual, tab_import = st.tabs(["✏️ Create Manually", "📥 Import from Prompt Registry"])

        with tab_manual:
            with st.form("cr_create_form", clear_on_submit=True):
                st.markdown("**New Repository Item**")
                fc1, fc2 = st.columns(2)
                cr_title = fc1.text_input("Title *", placeholder="e.g. CTE Blueprint Prompt — Client A")
                cr_type  = fc2.selectbox("Type *", central_service.ITEM_TYPES)
                cr_desc  = st.text_area("Description", placeholder="What does this item do?", height=68)
                cr_content = st.text_area("Content *", placeholder="Paste the prompt, asset, or learning content here…", height=140)
                fa1, fa2, fa3 = st.columns(3)
                cr_source   = fa1.selectbox("Source Module", [""] + central_service.SOURCE_MODULES)
                cr_client   = fa2.text_input("Client Name", placeholder="e.g. Acme Corp")
                cr_cluster  = fa3.text_input("Cluster Name", placeholder="e.g. CTE")
                cr_tags = st.text_input("Tags (comma-separated)", placeholder="blueprint, cte, client-a")
                if st.form_submit_button("💾 Save to Repository", use_container_width=True):
                    ok, msg, item = central_service.create_repository_item(
                        db,
                        title=cr_title,
                        item_type=cr_type,
                        content=cr_content,
                        description=cr_desc,
                        source_module=cr_source,
                        client_name=cr_client,
                        cluster_name=cr_cluster,
                        tags=cr_tags,
                        created_by=user_name,
                    )
                    if ok:
                        st.success(f"✅ '{item.title}' saved to the repository.")
                        st.rerun()
                    else:
                        st.error(msg)

        with tab_import:
            st.caption("Pull an existing prompt from the Prompt Registry into the Central Repository.")
            all_prompts = db.query(Prompt).filter(Prompt.name.isnot(None)).order_by(Prompt.name).all()
            if not all_prompts:
                st.info("No prompts found in the Prompt Registry.")
            else:
                prompt_map = {f"{p.name} (v{p.active_version or '?'})": p.id for p in all_prompts}
                with st.form("cr_import_form", clear_on_submit=True):
                    selected_label = st.selectbox("Select Prompt", list(prompt_map.keys()))
                    im1, im2 = st.columns(2)
                    im_client  = im1.text_input("Client Name", placeholder="e.g. Acme Corp")
                    im_cluster = im2.text_input("Cluster Name", placeholder="e.g. CTE")
                    im_tags    = st.text_input("Additional Tags", placeholder="imported, registry")
                    if st.form_submit_button("📥 Import into Repository", use_container_width=True):
                        pid = prompt_map[selected_label]
                        ok, msg, item = central_service.import_from_prompt_registry(
                            db, pid, created_by=user_name,
                            client_name=im_client, cluster_name=im_cluster, tags=im_tags,
                        )
                        if ok:
                            st.success(f"✅ '{item.title}' imported successfully.")
                            st.rerun()
                        else:
                            st.error(msg)

    st.divider()

    # ── Filter & Search ───────────────────────────────────────────────────────
    st.markdown(
        "<div style='font-size:0.82rem;font-weight:700;color:#c7d2fe;"
        "text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px;'>"
        "🔍 Search & Filter</div>",
        unsafe_allow_html=True,
    )
    fs1, fs2, fs3, fs4, fs5 = st.columns([2, 1, 1, 1, 1])
    search_kw    = fs1.text_input("Search", placeholder="Keyword in title, content, or tags…", label_visibility="collapsed")
    filter_type  = fs2.selectbox("Type", ["All Types"] + central_service.ITEM_TYPES, label_visibility="collapsed")
    filter_status = fs3.selectbox("Status", ["active", "archived", "all"], label_visibility="collapsed")

    known_clients  = ["All Clients"]  + central_repository.distinct_clients(db)
    known_clusters = ["All Clusters"] + central_repository.distinct_clusters(db)
    filter_client  = fs4.selectbox("Client", known_clients, label_visibility="collapsed")
    filter_cluster = fs5.selectbox("Cluster", known_clusters, label_visibility="collapsed")

    items = central_repository.list_items(
        db,
        status=None if filter_status == "all" else filter_status,
        item_type=None if filter_type == "All Types" else filter_type,
        client_name=None if filter_client == "All Clients" else filter_client,
        search=search_kw.strip() or None,
    )

    # Client-side cluster filter (cluster_name stored as text)
    if filter_cluster != "All Clusters":
        items = [i for i in items if i.cluster_name == filter_cluster]

    st.caption(f"{len(items)} item(s) found")

    if not items:
        st.info("No repository items match the current filters. Use **Add Item** above to get started.")
        return

    # ── Item table ────────────────────────────────────────────────────────────
    # Detect active edit / view state from session
    _edit_id = st.session_state.get("cr_edit_id")
    _view_id = st.session_state.get("cr_view_id")

    for item in items:
        is_editing = (_edit_id == item.id)
        is_viewing = (_view_id == item.id)

        with st.container():
            row1, row2 = st.columns([6, 2])
            with row1:
                st.markdown(
                    f"<div style='margin-bottom:2px;'>"
                    f"<strong style='font-size:0.97rem;color:#e0e7ff;'>#{item.id} — {item.title}</strong>"
                    f"&nbsp;&nbsp;{_type_chip(item.item_type)}&nbsp;{_status_chip(item.status)}"
                    f"</div>"
                    f"<div style='font-size:0.76rem;color:#94a3b8;margin-top:2px;'>"
                    f"📁 {item.source_module or '—'} &nbsp;|&nbsp; "
                    f"🏢 {item.client_name or '—'} &nbsp;|&nbsp; "
                    f"🗂️ {item.cluster_name or '—'} &nbsp;|&nbsp; "
                    f"👤 {item.created_by or '—'} &nbsp;|&nbsp; "
                    f"📅 {_fmt_date(item.created_at)} &nbsp;|&nbsp; "
                    f"🔁 Used {item.usage_count or 0}× "
                    f"{'· Last: ' + _fmt_date(item.last_used_at) if item.last_used_at else ''}"
                    f"</div>"
                    f"<div style='font-size:0.74rem;color:#6366f1;margin-top:3px;'>"
                    f"🏷️ {item.tags or 'no tags'}"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            with row2:
                a1, a2, a3, a4 = st.columns(4)
                if a1.button("👁️", key=f"cr_view_{item.id}", help="View content"):
                    if _view_id == item.id:
                        st.session_state.pop("cr_view_id", None)
                    else:
                        st.session_state["cr_view_id"] = item.id
                        st.session_state.pop("cr_edit_id", None)
                    st.rerun()
                if a2.button("✏️", key=f"cr_edit_{item.id}", help="Edit item"):
                    if _edit_id == item.id:
                        st.session_state.pop("cr_edit_id", None)
                    else:
                        st.session_state["cr_edit_id"] = item.id
                        st.session_state.pop("cr_view_id", None)
                    st.rerun()
                if a3.button("♻️", key=f"cr_reuse_{item.id}", help="Copy content for reuse"):
                    ok, msg, content = central_service.reuse_item(db, item.id, by=user_name)
                    if ok:
                        st.session_state[f"cr_reuse_content_{item.id}"] = content
                        st.toast("✅ Content marked for reuse — see below.")
                        st.rerun()
                    else:
                        st.error(msg)
                if item.status == "active":
                    if a4.button("🗃️", key=f"cr_arch_{item.id}", help="Archive item"):
                        ok, msg = central_service.archive_repository_item(db, item.id, by=user_name)
                        st.toast(msg)
                        st.rerun()
                else:
                    if a4.button("🗑️", key=f"cr_del_{item.id}", help="Delete permanently"):
                        ok, msg = central_service.delete_repository_item(db, item.id, by=user_name)
                        st.toast(msg)
                        st.rerun()

        # Reuse content panel
        reuse_key = f"cr_reuse_content_{item.id}"
        if reuse_key in st.session_state:
            with st.container():
                st.markdown(
                    "<div style='background:rgba(16,185,129,0.08);border:1px solid #10b98144;"
                    "border-radius:8px;padding:10px 14px;margin:4px 0;'>"
                    "<span style='color:#10b981;font-weight:700;font-size:0.82rem;'>♻️ Reuse Content</span>"
                    "</div>",
                    unsafe_allow_html=True,
                )
                st.code(st.session_state[reuse_key], language=None)
                if st.button("✕ Close", key=f"cr_reuse_close_{item.id}"):
                    st.session_state.pop(reuse_key, None)
                    st.rerun()

        # View panel
        if is_viewing:
            with st.container():
                st.markdown(
                    f"<div style='background:rgba(99,102,241,0.08);border:1px solid #6366f144;"
                    f"border-radius:8px;padding:12px 16px;margin:4px 0;'>"
                    f"<div style='color:#818cf8;font-weight:700;font-size:0.82rem;"
                    f"margin-bottom:8px;'>👁️ Viewing: {item.title}</div>"
                    f"<div style='color:#94a3b8;font-size:0.78rem;margin-bottom:6px;'>"
                    f"{item.description or 'No description.'}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                st.code(item.content, language=None)
                if st.button("✕ Close View", key=f"cr_close_view_{item.id}"):
                    st.session_state.pop("cr_view_id", None)
                    st.rerun()

        # Edit panel
        if is_editing:
            with st.form(f"cr_edit_form_{item.id}"):
                st.markdown(f"**✏️ Editing: {item.title}**")
                ef1, ef2 = st.columns(2)
                e_title   = ef1.text_input("Title", value=item.title)
                e_type    = ef2.selectbox(
                    "Type", central_service.ITEM_TYPES,
                    index=central_service.ITEM_TYPES.index(item.item_type) if item.item_type in central_service.ITEM_TYPES else 0,
                )
                e_desc    = st.text_area("Description", value=item.description or "", height=68)
                e_content = st.text_area("Content", value=item.content or "", height=140)
                eg1, eg2, eg3 = st.columns(3)
                e_source  = eg1.selectbox(
                    "Source Module",
                    [""] + central_service.SOURCE_MODULES,
                    index=([""] + central_service.SOURCE_MODULES).index(item.source_module)
                    if item.source_module in central_service.SOURCE_MODULES else 0,
                )
                e_client  = eg2.text_input("Client Name", value=item.client_name or "")
                e_cluster = eg3.text_input("Cluster Name", value=item.cluster_name or "")
                e_tags    = st.text_input("Tags", value=item.tags or "")
                eb1, eb2 = st.columns(2)
                if eb1.form_submit_button("💾 Save Changes", use_container_width=True):
                    ok, msg = central_service.update_repository_item(
                        db, item.id, updated_by=user_name,
                        title=e_title, item_type=e_type, content=e_content,
                        description=e_desc, source_module=e_source,
                        client_name=e_client, cluster_name=e_cluster, tags=e_tags,
                    )
                    if ok:
                        st.session_state.pop("cr_edit_id", None)
                        st.toast("✅ Item updated.")
                        st.rerun()
                    else:
                        st.error(msg)
                if eb2.form_submit_button("✕ Cancel", use_container_width=True):
                    st.session_state.pop("cr_edit_id", None)
                    st.rerun()

        st.markdown(
            "<hr style='border:none;border-top:1px solid rgba(255,255,255,0.06);margin:8px 0;'>",
            unsafe_allow_html=True,
        )
