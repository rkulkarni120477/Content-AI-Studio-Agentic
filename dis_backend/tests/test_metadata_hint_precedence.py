"""CAS upload hints override DIS's own inference — except where DIS blanked a
field deliberately.

CAS now always sends the uploading course's block (it is the authoritative answer;
see app.api.v1.routers.source_library._block_from_course). That is what stops a
subject-named folder like ``Landing Gear Projects/`` from ingesting block-less and
vanishing from block-scoped retrieval.

But one document type is block-less ON PURPOSE: an AKTR knowledge-test rollup
carries one sheet per block, so no doc-level block is true of it, and
AIMClientProfile.enrich_metadata blanks the field and sets spans_multiple_blocks
(real attribution happens per content unit, one unit per sheet). An unguarded hint
would overwrite that blank with whichever course the sheet was uploaded under and
re-file all sixteen blocks' data under one block — the exact defect the blanking
was added to fix.
"""
from __future__ import annotations

from services.agents.metadata_tagging_agent import apply_metadata_hints as _apply_hints


def test_a_supplied_block_fills_what_the_filename_regex_could_not():
    meta = {"block": "", "source_file_name": "Landing Gear Systems Project 4 A52.docx"}
    assert _apply_hints(meta, {"block": "Block 9"})["block"] == "Block 9"


def test_a_supplied_block_corrects_a_wrong_inference():
    # The regex reads the first 1,500 characters, which is not always this block.
    assert _apply_hints({"block": "Block 1"}, {"block": "Block 9"})["block"] == "Block 9"


def test_an_empty_hint_never_clobbers_a_good_inference():
    # Pre-existing behaviour, and the reason sending "" is always safe.
    assert _apply_hints({"block": "Block 9"}, {"block": ""})["block"] == "Block 9"


def test_a_multi_block_document_keeps_its_deliberate_blank():
    meta = {"block": "", "block_id": "", "block_number": None, "spans_multiple_blocks": True}
    out = _apply_hints(meta, {"block": "Block 9", "block_id": "B9", "block_number": 9})
    assert out["block"] == ""
    assert out["block_id"] == ""
    assert out["block_number"] is None


def test_a_multi_block_document_still_accepts_non_block_hints():
    # The guard must be narrow: course scope and purpose still have to land, or the
    # rollup stops being isolated to the course it was uploaded for.
    out = _apply_hints(
        {"spans_multiple_blocks": True, "block": ""},
        {"block": "Block 9", "course_id": "101", "purpose": "blueprint"},
    )
    assert out["block"] == ""
    assert out["course_id"] == "101"
    assert out["purpose"] == "blueprint"
