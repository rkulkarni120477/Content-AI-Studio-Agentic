"""Phase 5 upload metadata characterization (CAS + DIS contract)."""
from __future__ import annotations

import inspect
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_cas_upload_form_accepts_taxonomy_hint_fields():
    from app.api.v1.routers import source_library

    src = inspect.getsource(source_library.upload_source_document)
    for field in (
        "purpose",
        "document_type",
        "project_id",
        "course_id",
        "block",
        "day",
        "chapter",
        "module_name",
        "learning_objective",
        "source_relative_path",
        "source_root",
    ):
        assert f"{field}:" in src or f"{field}=" in src


def test_cas_upload_hardcodes_visibility_internal():
    from app.api.v1.routers import source_library

    src = inspect.getsource(source_library.upload_source_document)
    assert '"visibility": "internal"' in src


def test_dis_upload_builds_metadata_hints_from_form_fields():
    src = (REPO_ROOT / "dis_backend" / "api" / "routers" / "ingestion.py").read_text(encoding="utf-8")
    assert "metadata_hints" in src
    for key in ("purpose", "document_type", "block", "day", "chapter", "module_name"):
        assert key in src
