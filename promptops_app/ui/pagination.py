"""Pagination helper — Prev/Next controls for Streamlit pages.

Usage:
    from promptops_app.ui.pagination import paginate

    total = document_repository.count_documents(db)
    offset, page = paginate(total, page_size=20, page_key="docs_page")
    items = document_repository.list_all_documents(db, limit=20, offset=offset)
"""

import streamlit as st


def paginate(total: int, page_size: int, page_key: str) -> tuple[int, int]:
    """Render Prev / Next navigation and return (offset, current_page).

    Args:
        total:     Total number of items across all pages.
        page_size: Number of items per page.
        page_key:  Unique st.session_state key — one per paginated section.

    Returns:
        (offset, current_page) where offset is the row number to start from.
    """
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = st.session_state.get(page_key, 1)
    page = max(1, min(page, total_pages))
    st.session_state[page_key] = page

    p1, p2, p3 = st.columns([1, 4, 1])
    if p1.button("← Prev", key=f"{page_key}_prev", disabled=(page <= 1)):
        st.session_state[page_key] = page - 1
        st.rerun()
    p2.caption(f"Page **{page}** of **{total_pages}** &nbsp;·&nbsp; {total} item(s)")
    if p3.button("Next →", key=f"{page_key}_next", disabled=(page >= total_pages)):
        st.session_state[page_key] = page + 1
        st.rerun()

    return (page - 1) * page_size, page
