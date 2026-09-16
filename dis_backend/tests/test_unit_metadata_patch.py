"""Unit tests for patch_unit_metadata validation (no live stores)."""
from __future__ import annotations

import pytest

from services.ebook_page_retag import UnitMetadataPatchError, patch_unit_metadata
from services.source_library import _view_units_for_content


def test_patch_unit_metadata_rejects_synthetic_view_page(monkeypatch):
    with pytest.raises(UnitMetadataPatchError) as exc:
        patch_unit_metadata(
            tenant_cfg=object(),
            client_id="c1",
            job_id="j1",
            unit_id="view_page_3",
            title="Nope",
        )
    assert exc.value.status_code == 400
    assert "Synthetic" in exc.value.message


def test_patch_unit_metadata_requires_fields():
    with pytest.raises(UnitMetadataPatchError) as exc:
        patch_unit_metadata(
            tenant_cfg=object(),
            client_id="c1",
            job_id="j1",
            unit_id="j1:page_1",
        )
    assert exc.value.status_code == 400
    assert "No unit metadata" in exc.value.message


def test_patch_unit_metadata_requires_unit_id():
    with pytest.raises(UnitMetadataPatchError) as exc:
        patch_unit_metadata(
            tenant_cfg=object(),
            client_id="c1",
            job_id="j1",
            unit_id="",
            title="x",
        )
    assert exc.value.status_code == 400


def test_view_units_surface_section_tags():
    doc = {
        "content_units": [
            {
                "content_unit_id": "j:page_1",
                "unit_number": 1,
                "title": "Landing gear",
                "text": "Oleo strut basics",
                "topics": ["oleo strut", "servicing"],
                "metadata": {
                    "page_number": "1-1",
                    "pdf_page": 1,
                    "acs_codes": ["AM.I.D.K1"],
                    "summary": "Oleo strut basics.",
                    "tagging_status": "ok",
                },
            }
        ]
    }
    units = _view_units_for_content(doc)
    assert len(units) == 1
    assert units[0]["unit_id"] == "j:page_1"
    assert units[0]["title"] == "Landing gear"
    assert units[0]["topics"] == ["oleo strut", "servicing"]
    assert units[0]["acs_codes"] == ["AM.I.D.K1"]
    assert units[0]["summary"] == "Oleo strut basics."
    assert units[0]["page_number"] == "1-1"
    assert units[0]["tagging_status"] == "ok"
