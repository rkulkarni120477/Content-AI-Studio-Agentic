"""Legacy ``{single}``-brace → ``{{double}}``-brace template conversion.

The four seeded ``default_*`` pipeline rows (and the constants they were seeded
from) carry Python-``.format`` style ``{placeholder}`` bodies, while the
registry renderer (``prompt_builder.render``) substitutes ``{{placeholder}}``.
Until converted, a flag-on resolution of a seeded row would emit the literal
``{placeholder}`` text — the recorded enablement precondition for
``PROMPT_RESOLVE_BY_COMPONENT``.

This module is the single conversion implementation, shared by:

* ``seed_data()`` — fresh databases seed already-converted bodies;
* ``scripts/convert_seeded_prompt_braces.py`` — converts the rows of an
  already-seeded database in place (rehearsal, then prod in the enablement
  window).

Only identifier-shaped tokens (``{course_title}``) are touched; anything with
quotes, spaces, or nesting (JSON examples, set literals) can never match, and
already-``{{double}}`` placeholders are left alone.
"""

from __future__ import annotations

import re

# {ident} not already part of a {{ident}} pair.
_SINGLE_RE = re.compile(r"(?<!\{)\{([a-zA-Z_][a-zA-Z0-9_]*)\}(?!\})")

# Placeholder renames applied during conversion: the seeded bodies use the
# legacy .format() names, but the live routers supply the registry names
# (blueprints.py supplies `extra_instructions`, never the legacy
# `extra_instructions_block` — without the rename the converted blueprint
# default would render a literal placeholder). cdd.py supplies both names with
# the same value, so the rename is output-identical there.
LEGACY_RENAMES: dict[str, str] = {
    "extra_instructions_block": "extra_instructions",
}


def find_single_brace_vars(text: str) -> list[str]:
    """Unique ``{single}``-brace identifiers in *text*, in appearance order."""
    seen: dict[str, None] = {}
    for m in _SINGLE_RE.finditer(text or ""):
        seen[m.group(1)] = None
    return list(seen)


def convert_legacy_braces(
    text: str,
    renames: dict[str, str] | None = LEGACY_RENAMES,
) -> tuple[str, list[str]]:
    """Convert ``{ident}`` → ``{{ident}}`` (applying *renames*) in *text*.

    Returns ``(converted_text, converted_identifiers)`` where the identifiers
    are the ORIGINAL names found, in appearance order. Idempotent: already
    ``{{double}}`` placeholders never match.
    """
    renames = renames or {}
    converted: dict[str, None] = {}

    def _sub(m: re.Match) -> str:
        name = m.group(1)
        converted[name] = None
        return "{{" + renames.get(name, name) + "}}"

    return _SINGLE_RE.sub(_sub, text or ""), list(converted)
