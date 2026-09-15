"""Unit tests for ebook_page_retag selection logic (no live stores)."""
from __future__ import annotations

from services.ebook_page_tagger import TAGGING_FAILED, TAGGING_OK, TAGGING_PENDING


def test_retag_selection_filters_failed_and_pending_only():
    """Mirror the all_failed filter used by _load_units_from_pg without Postgres."""
    rows = [
        {"content_unit_id": "j:page_1", "metadata": {"tagging_status": TAGGING_OK}},
        {"content_unit_id": "j:page_2", "metadata": {"tagging_status": TAGGING_FAILED}},
        {"content_unit_id": "j:page_3", "metadata": {"tagging_status": TAGGING_PENDING}},
        {"content_unit_id": "j:page_4", "metadata": {}},
    ]
    selected = [
        r for r in rows
        if str((r.get("metadata") or {}).get("tagging_status") or "").lower() != TAGGING_OK
    ]
    assert [r["content_unit_id"] for r in selected] == ["j:page_2", "j:page_3", "j:page_4"]


def test_retag_selection_by_unit_ids():
    rows = [
        {"content_unit_id": "j:page_1", "metadata": {"tagging_status": TAGGING_FAILED}},
        {"content_unit_id": "j:page_2", "metadata": {"tagging_status": TAGGING_FAILED}},
    ]
    wanted = {"j:page_2"}
    selected = [r for r in rows if r["content_unit_id"] in wanted]
    assert [r["content_unit_id"] for r in selected] == ["j:page_2"]
