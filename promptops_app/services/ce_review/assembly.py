"""Lesson assembly + fingerprint for a CE review run.

A review reviews one *generation* (lesson) holistically, so its blocks are
assembled in order into a single document. The fingerprint of that document is
what "skip-unchanged" compares: an identical fingerprint + review basis means the
content has not changed since the last run, so the previous result is returned.
"""
from __future__ import annotations

import hashlib
from typing import List, Tuple


def ordered_blocks(generation) -> list:
    """Return a generation's blocks in display order (position, then id)."""
    blocks = list(getattr(generation, "blocks", None) or [])
    return sorted(blocks, key=lambda b: (getattr(b, "position", 0) or 0, b.id))


def assemble_generation(generation) -> Tuple[str, List[dict]]:
    """Assemble a generation's blocks into one document.

    Returns ``(text, parts)`` where *text* is the full assembled lesson and
    *parts* lists ``{block_id, block_label, content}`` per block (kept so later
    steps can anchor a finding back to the block it came from).
    """
    parts: list[dict] = []
    chunks: list[str] = []
    for b in ordered_blocks(generation):
        label = (b.block_label or "").strip()
        content = (b.content or "")
        parts.append({"block_id": b.id, "block_label": label, "content": content})
        if label:
            chunks.append(f"## {label}\n\n{content}")
        else:
            chunks.append(content)
    return "\n\n".join(chunks).strip(), parts


def fingerprint(text: str, *, basis_version: str = "") -> str:
    """Stable sha256 of the assembled content plus the review-basis version.

    Including *basis_version* means a checklist change also invalidates the
    skip-unchanged match, so editing the rules triggers a fresh review even when
    the content itself is unchanged.
    """
    h = hashlib.sha256()
    h.update((text or "").encode("utf-8"))
    h.update(b"\x00")
    h.update((basis_version or "").encode("utf-8"))
    return h.hexdigest()
