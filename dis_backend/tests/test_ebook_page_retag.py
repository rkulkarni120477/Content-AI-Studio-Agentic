"""Unit tests for ebook_page_retag selection + merge (no live stores)."""
from __future__ import annotations

from services.ebook_page_retag import merge_retag_units_into_list
from services.ebook_page_tagger import TAGGING_FAILED, TAGGING_OK, TAGGING_PENDING, tagging_counts


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


def test_merge_retag_preserves_sibling_ok_status():
    """Retry-this-page must not wipe earlier pages back to pending."""
    units = [
        {
            "content_unit_id": "j:page_1",
            "title": "p1",
            "topics": ["oleo"],
            "metadata": {"tagging_status": TAGGING_OK, "summary": "A", "acs_codes": ["AM.I.D.K1"]},
        },
        {
            "content_unit_id": "j:page_2",
            "title": "p2",
            "topics": [],
            "metadata": {"tagging_status": TAGGING_PENDING},
        },
        {
            "content_unit_id": "j:page_3",
            "title": "p3",
            "topics": [],
            "metadata": {"tagging_status": TAGGING_PENDING},
        },
    ]
    updated = {
        "j:page_2": {
            "content_unit_id": "j:page_2",
            "title": "p2 tagged",
            "topics": ["brakes"],
            "metadata": {
                "tagging_status": TAGGING_OK,
                "summary": "Brakes.",
                "acs_codes": ["AM.I.D.R1"],
                "topics": ["brakes"],
            },
        },
    }
    merge_retag_units_into_list(units, updated, replace_metadata=False)
    assert len(units) == 3
    assert units[0]["metadata"]["tagging_status"] == TAGGING_OK
    assert units[0]["metadata"]["summary"] == "A"
    assert units[1]["metadata"]["tagging_status"] == TAGGING_OK
    assert units[1]["topics"] == ["brakes"]
    assert units[1]["title"] == "p2 tagged"
    assert units[2]["metadata"]["tagging_status"] == TAGGING_PENDING
    counts = tagging_counts(units)
    assert counts["tagging_pending_count"] == 1
    assert counts["tagging_failed_count"] == 0


def test_merge_retag_appends_missing_unit_without_dropping_others():
    units = [
        {"content_unit_id": "j:page_1", "metadata": {"tagging_status": TAGGING_PENDING}},
    ]
    updated = {
        "j:page_99": {
            "content_unit_id": "j:page_99",
            "title": "new",
            "text": "x",
            "topics": ["t"],
            "metadata": {"tagging_status": TAGGING_OK},
        },
    }
    merge_retag_units_into_list(units, updated, replace_metadata=True)
    assert [u["content_unit_id"] for u in units] == ["j:page_1", "j:page_99"]
    assert units[0]["metadata"]["tagging_status"] == TAGGING_PENDING
    assert units[1]["metadata"]["tagging_status"] == TAGGING_OK


def test_batch_helpers():
    from services.ebook_page_retag import _batch_failed_count, _last_page_label
    batch = [
        {"metadata": {"tagging_status": TAGGING_OK, "page_number": "1"}},
        {"metadata": {"tagging_status": TAGGING_FAILED, "page_number": "2"}},
        {"metadata": {"tagging_status": TAGGING_PENDING, "pdf_page": 3}},
    ]
    assert _batch_failed_count(batch) == 1
    assert _last_page_label(batch) == 3
