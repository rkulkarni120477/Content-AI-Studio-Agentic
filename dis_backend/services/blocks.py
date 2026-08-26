"""One canonical way to compare a block label, for every store DIS queries.

A block is written into the data by several independent writers, and they do not
agree on how to spell it:

  * ``specialized_extractors.infer_block`` emitted ``f"Block {int(g):02d}"`` —
    zero-padded, so single-digit blocks landed as ``Block 09``.
  * ``client_profiles/aim.py`` emits ``f"Block {block_number}"`` — unpadded.
  * The UI/course record asks for whatever the user typed: ``Block 9``.

Measured in prod on 2026-08-26, that produced a block whose content units were
split across BOTH spellings (``Block 09``: 42 units, ``Block 9``: 19) and whose
calendars existed only under the padded form. Every read compared with ``=``, so
``Block 9`` found no calendar at all — and would have found 19 of 61 units if it
had. Block 2 and Block 6 are unpadded, which is the entire reason they are the
only blocks that ever produced a document.

The fix is not to re-tag the data, it is to stop comparing raw strings. Every
lookup goes through a *key*: case-folded, whitespace-collapsed, the leading
"Block" word dropped, leading zeros stripped from a numeric remainder. So
``Block 09``, ``Block 9``, ``BLOCK-09``, ``09`` and ``9`` are one block, and a
non-numeric label (``Powerplant``) still compares as itself.

``BLOCK_KEY_SQL`` is the SQL twin of :func:`block_key`, applied to the stored
column so legacy rows in either spelling match without a migration.
``test_block_matching.py`` pins the two against the same case table.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, List

#: A leading "block" word plus whatever separates it from the number.
_LEADING_BLOCK = re.compile(r"^block[\s._:\-]*", re.I)
_LEADING_ZEROS = re.compile(r"^0+(\d)")
_WHITESPACE = re.compile(r"\s+")


def block_key(value: Any) -> str:
    """Canonical comparison key for a block label.

    ``Block 09`` / ``Block 9`` / ``block-9`` / ``09`` / ``9`` -> ``block 9``.
    A non-numeric label is case-folded and whitespace-collapsed but otherwise
    kept, so ``Powerplant`` stays its own block and never collides with another.
    An empty/None label returns ``""`` — callers must treat that as "no block",
    never as a key that matches something.
    """
    text = _WHITESPACE.sub(" ", str(value or "").strip()).lower()
    if not text:
        return ""
    rest = _LEADING_BLOCK.sub("", text).strip()
    if rest.isdigit():
        # Substitution hoisted out of the f-string on purpose: a backslash inside an
        # f-string expression is a SyntaxError before Python 3.12, and the DIS image
        # is python:3.11-slim while the dev venv is 3.12 — so the module imported
        # fine in every test and could not be imported at all in the container.
        digits = _LEADING_ZEROS.sub(r"\1", rest)
        return f"block {digits}"
    return text


#: SQL twin of block_key, as a format string taking the column expression. Kept
#: beside the Python so the two are read (and changed) together; the test module
#: runs both over one case table, the Python directly and this against the
#: regex semantics it mirrors.
BLOCK_KEY_SQL = """CASE
        WHEN regexp_replace(lower(btrim({col})), '^block[[:space:]._:-]*', '') ~ '^[0-9]+$'
        THEN 'block ' || regexp_replace(
                 regexp_replace(lower(btrim({col})), '^block[[:space:]._:-]*', ''),
                 '^0+([0-9])', '\\1')
        ELSE regexp_replace(lower(btrim({col})), '[[:space:]]+', ' ', 'g')
    END"""


def block_label(value: Any) -> str:
    """The canonical form to WRITE: ``Block 9`` (never ``Block 09``).

    Unpadded because that is what the course records and the UI use, and what
    the blocks that already work are stored as. New rows written through this
    stop widening the split; old rows keep matching via :func:`block_key`.
    """
    key = block_key(value)
    if not key:
        return ""
    if key.startswith("block ") and key[6:].isdigit():
        return f"Block {key[6:]}"
    return _WHITESPACE.sub(" ", str(value or "").strip())


def block_variants(value: Any) -> List[str]:
    """Plausible stored spellings of one block, for a store that cannot apply
    ``BLOCK_KEY_SQL`` — an OpenSearch ``terms`` filter, say. Includes the value
    as given, so a spelling this function never thought of still matches itself.
    """
    key = block_key(value)
    raw = str(value or "").strip()
    out: List[str] = [raw] if raw else []
    if key.startswith("block ") and key[6:].isdigit():
        n = key[6:]
        for form in (f"Block {n}", f"Block {int(n):02d}", f"block {n}",
                     f"block {int(n):02d}", n, f"{int(n):02d}"):
            if form not in out:
                out.append(form)
    elif key and key not in out:
        out.append(key)
    return out


def same_block(a: Any, b: Any) -> bool:
    """True when two labels name the same block. Empty never matches empty —
    "unknown" is not a block two documents can share."""
    ka = block_key(a)
    return bool(ka) and ka == block_key(b)


def spelling_note(requested: Any, found: Iterable[Any]) -> str:
    """A one-line note when the stored spellings differ from what was asked for,
    or differ from each other — else "".

    Returned rather than logged so the caller can put it in the enumerate flags:
    a lookup that quietly succeeded through normalization should still say so,
    because the underlying data IS inconsistent and someone has to see that.
    """
    seen = [s for s in {str(f or "").strip() for f in found} if s]
    req = str(requested or "").strip()
    odd = sorted(s for s in seen if s != req)
    if not odd:
        return ""
    return (f"BLOCK_SPELLING_MERGED — asked for {req!r}, matched "
            f"{', '.join(repr(s) for s in odd)} by normalized block key; the "
            f"stored tags disagree and should be reconciled at ingestion")
