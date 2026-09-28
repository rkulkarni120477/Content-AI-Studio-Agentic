"""Anchor a finding to a verbatim quote in the content.

Step 4 needs only "does this quote exist in the block" (for auto-applicability)
plus a stable hash for dedup/dismissal. Robust offset re-location for applying a
fix arrives in Step 5.
"""
from __future__ import annotations

import hashlib
import re

_WS = re.compile(r"\s+")
# Fold smart quotes/dashes so an LLM's "quote" still matches the source text.
_FOLD = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                       "–": "-", "—": "-", " ": " "})


def normalize(s: str) -> str:
    """Whitespace/punctuation-folded, lowercased form for matching + hashing."""
    return _WS.sub(" ", (s or "").translate(_FOLD)).strip().lower()


def anchor_hash(quote: str) -> str:
    """Stable hash of a quote's normalized form."""
    return hashlib.sha256(normalize(quote).encode("utf-8")).hexdigest()


def contains(content: str, quote: str) -> bool:
    """True if *quote* is in *content* — exact, else whitespace/quote-insensitive."""
    if not quote:
        return False
    if quote in (content or ""):
        return True
    return normalize(quote) in normalize(content)


def _flex_pattern(quote: str) -> str:
    """Regex matching *quote* with flexible whitespace and smart/ascii quotes."""
    q = (quote or "").translate(_FOLD)          # fold pattern source to ascii forms
    out = []
    for tok in re.split(r"(\s+)", q):
        if tok == "":
            continue
        if tok.isspace():
            out.append(r"\s+")
        else:
            e = re.escape(tok)
            e = e.replace("'", "['‘’]").replace('"', '["“”]').replace(r"\-", "[-–—]")
            out.append(e)
    return "".join(out) or re.escape(q)


def locate(content: str, quote: str) -> tuple[int, int]:
    """Return the (start, end) span of *quote* in *content*, or (-1, -1).

    Exact match first; else a whitespace/quote-tolerant regex, which still yields
    real offsets in the original text for a deterministic in-place swap.
    """
    if not quote or not content:
        return (-1, -1)
    i = content.find(quote)
    if i >= 0:
        return (i, i + len(quote))
    m = re.search(_flex_pattern(quote), content)
    return (m.start(), m.end()) if m else (-1, -1)
