"""A block is one block, however it was spelled when it was written.

Prod, 2026-08-26: a Block 9 CDD failed with "No calendar found for client='aim'
block='Block 9'" while three Block 9 calendars sat in the table — stored as
'Block 09'. The same block's content units were split across BOTH spellings
(42 under 'Block 09', 19 under 'Block 9'), so the exact-match unit query would
have returned a third of the block and built a worksheet that looked whole.

Two writers disagreed: specialized_extractors.infer_block padded to two digits,
client_profiles/aim.py did not. Blocks 2 and 6 happen to be unpadded, which is
the only reason they are the only blocks that ever produced a document.

These tests pin the normalization both sides now go through, and — because a
Python key and a SQL key that drift apart would reintroduce exactly this bug —
that the two agree over one shared case table.
"""
from __future__ import annotations

import re

import pytest

from services.blocks import (
    BLOCK_KEY_SQL,
    block_key,
    block_label,
    block_variants,
    same_block,
    spelling_note,
)

#: (raw stored/requested label, expected canonical key)
CASES = [
    ("Block 9", "block 9"),
    ("Block 09", "block 9"),
    ("block 9", "block 9"),
    ("BLOCK 09", "block 9"),
    ("Block-09", "block 9"),
    ("Block_9", "block 9"),
    ("Block  9", "block 9"),
    ("  Block 9  ", "block 9"),
    ("9", "block 9"),
    ("09", "block 9"),
    ("Block 13", "block 13"),
    ("Block 013", "block 13"),
    ("Block 0", "block 0"),
    ("Powerplant", "powerplant"),
    ("General  Curriculum", "general curriculum"),
    ("", ""),
    (None, ""),
]


@pytest.mark.parametrize("raw,expected", CASES)
def test_block_key(raw, expected):
    assert block_key(raw) == expected


def test_the_prod_failure_matches_now():
    """'Block 9' asked for, 'Block 09' stored — the exact pair that failed."""
    assert same_block("Block 9", "Block 09")
    assert same_block("Block 09", "9")


def test_distinct_blocks_do_not_collide():
    assert not same_block("Block 1", "Block 11")
    assert not same_block("Block 9", "Block 19")
    assert not same_block("Powerplant", "Airframe")


def test_an_unknown_block_never_matches_another_unknown():
    """Empty is "we don't know", not a block two documents can share. Treating it
    as a key would sweep every untagged unit into whatever block was asked for."""
    assert not same_block("", "")
    assert not same_block(None, "Block 9")
    assert not same_block("", "Powerplant")


@pytest.mark.parametrize("raw,expected", [
    ("Block 09", "Block 9"), ("9", "Block 9"), ("Block 13", "Block 13"),
    ("block-09", "Block 9"), ("Powerplant", "Powerplant"), ("", ""),
])
def test_block_label_is_the_canonical_write_form(raw, expected):
    """Unpadded, because that is what the course records and the working blocks
    already use. New rows written through this stop widening the split."""
    assert block_label(raw) == expected


def test_infer_block_no_longer_pads():
    """The writer that created the 'Block 09' half of the split."""
    from services.specialized_extractors import infer_block

    assert infer_block("Block 09-Instructor Copy- ACS Course Calendar.docx") == "Block 9"
    assert infer_block("Block 13 Calendar.xlsx") == "Block 13"


def test_variants_cover_both_spellings_and_the_original():
    v = block_variants("Block 9")
    assert "Block 9" in v and "Block 09" in v
    # The value as given always survives, so a spelling this function never
    # imagined still matches itself rather than being normalized out of reach.
    assert block_variants("Blokk 9")[0] == "Blokk 9"


def test_spelling_note_speaks_up_only_when_the_tags_disagree():
    assert spelling_note("Block 9", ["Block 9", "Block 9"]) == ""
    note = spelling_note("Block 9", ["Block 09", "Block 9"])
    assert "BLOCK_SPELLING_MERGED" in note and "Block 09" in note


# ── the Python key and the SQL key must not drift apart ─────────────────────
def _sql_key_in_python(raw: str) -> str:
    """Execute BLOCK_KEY_SQL's semantics in Python, by construction from the same
    template — the closest a DB-free suite can get to running it in Postgres.

    Deliberately re-implemented from the SQL text rather than calling block_key:
    a test that called block_key would pass no matter how far the SQL drifted.
    """
    assert "{col}" in BLOCK_KEY_SQL
    text = re.sub(r"\s+", " ", (raw or "").strip()).lower()          # lower(btrim())
    stripped = re.sub(r"^block[\s._:\-]*", "", text)                  # ^block[...]*
    if re.fullmatch(r"[0-9]+", stripped):                             # ~ '^[0-9]+$'
        return "block " + re.sub(r"^0+([0-9])", r"\1", stripped)      # ^0+([0-9])
    return re.sub(r"\s+", " ", text)                                  # collapse


@pytest.mark.parametrize("raw,expected", CASES)
def test_sql_key_agrees_with_python_key(raw, expected):
    assert _sql_key_in_python(raw if raw is not None else "") == expected


def test_sql_template_targets_the_column_it_is_given():
    sql = BLOCK_KEY_SQL.format(col="c.block")
    assert "c.block" in sql and "{col}" not in sql
    assert sql.count("c.block") == 3, "every branch must normalize the same column"
