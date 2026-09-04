"""The block tag must be a fact about the upload, not a guess about the filename.

DIS derives ``block`` by regex over a document's path, filename and first 1,500
characters. A folder named for its subject rather than its block matches nothing,
so the document is stored with ``block: ""`` and is then invisible to every
block-scoped retrieval — while the Source Library still lists it as "Processed".

Measured on the AIM corpus 2026-08-27, before this fix: 148 documents lost across
six courses (59-79% of each). Block 9's CDD reported "Total Projects: 0" with
eleven landing-gear project files sitting in the store under
``Landing Gear Projects/``; Blocks 8 and 10 had no reachable teaching content at
all. Nothing anywhere reported a problem.

CAS knows the answer at upload time — the user picked a course, and the course is
named for its block — so these tests pin that it sends it.
"""
from unittest.mock import MagicMock

from app.api.v1.routers.source_library import _GLOBAL_COURSE_ID, _block_from_course


def _db_returning(course):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = course
    return db


def _course(name):
    c = MagicMock()
    c.name = name
    return c


def test_derives_the_block_from_the_courses_name():
    assert _block_from_course(_db_returning(_course("Block 9")), 101) == "Block 9"


def test_normalises_a_zero_padded_course_name():
    # The corpus carries the same block as "Block 9", "Block 09" and "9". New rows
    # must be written one way or the split keeps widening.
    assert _block_from_course(_db_returning(_course("Block 09")), 101) == "Block 9"


def test_reads_the_block_out_of_a_longer_course_title():
    assert _block_from_course(
        _db_returning(_course("Block 9 Aircraft Systems-II")), 101) == "Block 9"


def test_a_global_upload_is_never_stamped_with_a_block():
    # "Upload as Global" means visible in every course. A handbook that serves the
    # whole programme must not be filed under whichever course was on screen.
    assert _block_from_course(_db_returning(_course("Block 9")), _GLOBAL_COURSE_ID) == ""


def test_no_course_scope_means_no_block():
    assert _block_from_course(_db_returning(_course("Block 9")), None) == ""


def test_a_course_that_is_not_a_block_is_left_alone():
    # Non-AIM tenants have courses, not blocks. Guessing here would invent a tag.
    assert _block_from_course(_db_returning(_course("Principles of Marketing")), 56) == ""


def test_a_missing_course_returns_empty_rather_than_guessing():
    assert _block_from_course(_db_returning(None), 999) == ""


def test_a_broken_lookup_never_breaks_the_upload():
    db = MagicMock()
    db.query.side_effect = RuntimeError("database is on fire")
    assert _block_from_course(db, 101) == ""


# ---------------------------------------------------------------------------
# Upload policy
#
# Accepting a file this pipeline cannot read is not neutral: it is stored, listed
# as "Processed", and contributes nothing to any generation. 110 of AIM's 121 .doc
# files went in exactly that way and went unnoticed for a month. A refusal at the
# door costs the user one re-save; a silent empty ingestion cost weeks of blocks
# built against content nobody could see was missing.
# ---------------------------------------------------------------------------
import pathlib
import tempfile

import pytest
from fastapi import HTTPException

from app.api.v1.routers.source_library import _reject_unsupported
from app.core.upload_formats import load_upload_formats


def _refusal(names):
    with pytest.raises(HTTPException) as exc:
        _reject_unsupported(names)
    assert exc.value.status_code == 400
    return exc.value.detail


def test_supported_types_pass_through():
    _reject_unsupported(["Block 9 Syllabus.docx", "calendar.pdf", "deck.pptx",
                         "AKTR.xlsx", "notes.txt"])


def test_legacy_doc_is_refused_for_now():
    detail = _refusal(["Landing Gear Project A27.doc"])
    assert "not accepted yet" in detail
    # The reason has to say what to do next, or the refusal just blocks the user.
    assert ".docx" in detail


def test_legacy_binary_office_formats_are_refused():
    for name in ("deck.ppt", "sheet.xls"):
        detail = _refusal([name])
        assert "cannot be read" in detail


def test_an_unknown_type_is_refused_and_lists_what_is_accepted():
    detail = _refusal(["archive.zip"])
    assert ".zip is not a supported document type" in detail
    assert ".pdf" in detail and ".docx" in detail


def test_a_file_with_no_extension_is_refused_rather_than_guessed():
    assert "unknown" in _refusal(["READMEfile"])


def test_a_blocked_type_is_still_blocked_in_upper_case():
    assert "not accepted yet" in _refusal(["Landing Gear Projects/PROJECT A27.DOC"])


@pytest.mark.parametrize("name", [
    "REPORT.PDF", "Deck.PPTX", "Sheet.XLSX", "Guide.DocX", "Block 9.Pdf", "SCAN.JPG",
])
def test_a_supported_type_is_accepted_whatever_case_the_extension_arrives_in(name):
    """The dangerous direction. Windows scanners and older exports routinely emit
    .PDF and .DOCX; matching those literally against a lowercase list would refuse
    perfectly good documents with a message insisting PDFs are supported."""
    _reject_unsupported([name])


def test_a_hand_edited_config_with_capitals_or_dots_still_matches():
    import json
    from app.core import upload_formats as uf

    # The config is maintained by hand, so it will eventually contain ".PDF".
    cfg = pathlib.Path(tempfile.mkdtemp()) / "upload_formats.json"
    cfg.write_text(json.dumps({
        "supported_extensions": [".PDF", "DocX"],
        "blocked_extensions": {".DOC": "Re-save as .docx and upload that instead."},
    }))
    original = uf._config_path
    uf._config_path = lambda: cfg
    try:
        assert uf.unsupported_reasons(["a.pdf", "b.DOCX"]) == []
        assert "Re-save" in uf.unsupported_reasons(["c.Doc"])[0]
    finally:
        uf._config_path = original


def test_every_blocked_type_carries_a_reason_and_is_not_also_listed_supported():
    policy = load_upload_formats()
    supported = set(policy["supported_extensions"])
    assert policy["blocked_extensions"], "the shipped config blocks nothing — check it loaded"
    for ext, reason in policy["blocked_extensions"].items():
        assert ext not in supported, f"{ext} is both blocked and supported in the config"
        assert len(reason) > 40, f"{ext} needs a reason the user can act on"


def test_the_policy_comes_from_config_not_code(tmp_path, monkeypatch):
    """Editing config/upload_formats.json must change behaviour with no code change
    — that is the whole point of it being config."""
    import json
    from app.core import upload_formats as uf

    cfg = tmp_path / "upload_formats.json"
    cfg.write_text(json.dumps({
        "supported_extensions": ["rtf"],
        "blocked_extensions": {"pdf": "PDF is switched off in this deployment."},
    }))
    monkeypatch.setattr(uf, "_config_path", lambda: cfg)

    assert uf.unsupported_reasons(["notes.rtf"]) == []
    assert "switched off" in uf.unsupported_reasons(["a.pdf"])[0]


def test_an_unreadable_config_refuses_the_unusual_rather_than_waving_it_through(tmp_path, monkeypatch):
    from app.core import upload_formats as uf

    broken = tmp_path / "upload_formats.json"
    broken.write_text("{ not json")
    monkeypatch.setattr(uf, "_config_path", lambda: broken)

    # Falls back to a conservative built-in list rather than accepting everything:
    # if policy cannot be read, the safe direction is to refuse.
    assert uf.unsupported_reasons(["deck.pptx"]) == []
    assert uf.unsupported_reasons(["archive.zip"])


def test_one_bad_file_names_itself_so_a_batch_is_diagnosable():
    detail = _refusal(["good.pdf", "bad.doc"])
    assert "bad.doc" in detail
    assert "good.pdf" not in detail
