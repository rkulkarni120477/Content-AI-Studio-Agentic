"""Detecting — and where it is unambiguous, repairing — unbalanced markdown emphasis.

A line whose ``**`` markers do not pair up renders wrong for every reader: the
label loses its weight and a stray asterisk appears in the prose. It is a small
visual defect with a disproportionate cost, because nothing downstream detects
it. The content commits cleanly, the version history looks normal, and the
damage is *sticky* — every later save preserves untouched lines byte for byte,
so one malformed label propagates through every subsequent version of the
document until somebody edits that exact line by hand.

Two distinct producers were found in stored content:

1. **A bullet strip that ate an emphasis delimiter.** Per-item regeneration asks
   the model for one item's text and the caller re-adds the list prefix, so a
   bullet the model echoed has to be removed. The pattern that did this matched
   a zero-width gap after the bullet character, which made the opening ``*`` of
   ``**Label:**`` look like a bullet. Every item type was affected, because each
   one re-adds its own prefix afterwards::

       - **Supplemental References:** …  ->  - *Supplemental References:** …
       2. **Objective 2: …**            ->  2. *Objective 2: …**
       ## **Description:** …            ->  ## *Description:** …
       **Course Goal:** …               ->  *Course Goal:** …

   This is the shape :func:`repair_emphasis` fixes, and it is safe to fix
   because the reading is forced: the closing ``**`` is intact, so the opener is
   the half that lost a character.

2. **The model's own malformed output**, e.g. ``*Self-esteem**needs*`` where
   ``*Self-esteem needs*`` was meant. There is no single correct repair for that
   — the intended text is a guess — so it is reported and left alone.

The dividing line between the two is deliberate. Guessing at ambiguous markup
would trade a visible defect for an invisible one, which is the worse trade.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: A list or heading prefix, which carries no emphasis meaning and so is skipped
#: before looking for a delimiter. ``*`` is included as a bullet only when real
#: whitespace follows it — that is exactly what distinguishes a bullet from an
#: emphasis opener, and getting it wrong here would reintroduce the defect this
#: module exists to repair.
_PREFIX = r"(\s*(?:[-•]\s*|\*\s+|\d+[.):]\s+|#{1,6}\s+)?)"

#: The prefix followed by a lone ``*`` that hugs text — a bold opener missing a
#: character, or a legitimate italic opener. Which one it is depends on how it
#: closes, which :func:`_repair_line` decides.
_LONE_OPENER_RE = re.compile(_PREFIX + r"\*(?=[^*\s])")


@dataclass(frozen=True)
class Unbalanced:
    """One line whose ``**`` markers do not pair up."""

    line_number: int          # 1-based, for a message a human can act on
    text: str
    repairable: bool          # True only for the bullet-strip residue shape

    def describe(self) -> str:
        state = "repaired" if self.repairable else "left as-is (ambiguous)"
        return f"line {self.line_number}: {self.text.strip()[:120]!r} — {state}"


def _is_unbalanced(line: str) -> bool:
    """Whether *line* holds an odd number of ``**`` markers.

    Odd means broken with certainty: ``**`` cannot span lines in markdown, so an
    unpaired one has nothing to close it. An even count is not proof of health
    (``*a**b*`` is even and wrong), but it is not proof of damage either, and
    this module only reports what it can stand behind.
    """
    return line.count("**") % 2 == 1


def _repair_line(line: str) -> str | None:
    """Return *line* with a lost opening ``*`` restored, or None if unrepairable.

    Only one shape qualifies: a lone ``*`` at the start of the line's content
    that is closed by ``**``. That combination cannot be anything but a bold
    opener missing a character, because a well-formed italic run closes with a
    single ``*``.
    """
    m = _LONE_OPENER_RE.match(line)
    if not m:
        return None

    opener = m.end() - 1                     # index of the lone '*'
    after = re.search(r"\*+", line[opener + 1:])
    if not after or len(after.group(0)) < 2:
        # Closed by a single '*' (a healthy italic run) or not closed at all.
        # Either way the missing character is not here, so do not invent one.
        return None

    return line[:opener] + "*" + line[opener:]


def find_unbalanced(text: str) -> list[Unbalanced]:
    """Every line in *text* whose ``**`` markers do not pair up."""
    out: list[Unbalanced] = []
    for i, line in enumerate(text.split("\n"), start=1):
        if _is_unbalanced(line):
            out.append(Unbalanced(i, line, _repair_line(line) is not None))
    return out


def repair_emphasis(text: str) -> tuple[str, list[Unbalanced]]:
    """Repair the unambiguous shape in *text*; report every unbalanced line.

    Returns ``(text, findings)``. Lines with balanced markers are returned byte
    for byte — no reformatting, no normalisation — so this is safe to apply to
    content that is mostly fine. ``findings`` covers repaired and unrepaired
    lines alike, so a caller can log what it could not fix rather than shipping
    a quiet best effort.
    """
    if not text or "**" not in text:
        return text or "", []

    findings: list[Unbalanced] = []
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if not _is_unbalanced(line):
            continue
        fixed = _repair_line(line)
        findings.append(Unbalanced(i + 1, line, fixed is not None))
        if fixed is not None:
            lines[i] = fixed
    return "\n".join(lines), findings
