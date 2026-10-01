"""What a tenant calls the deliverable, on the server side.

``projects.ui_labels`` already carries each tenant's vocabulary — AIM calls a
CDD a "Blueprint" — and the frontend has substituted it into on-screen text for
some time. The server never read it, so everything the server WRITES kept the
default word: the stored document title (``… — CDD``), the heading at the top of
the rendered markdown (``# Course Design Document — …``), the banner on every
worksheet of the exported workbook, and the download filename. A tenant that had
renamed the deliverable still received a file called ``CDD_….xlsx`` whose every
sheet said CDD across the top.

This is deliberately a narrow crossing of the "display-only" boundary that
``app/schemas/ui_labels`` documents: it is read for the words that go into a
DELIVERABLE a person reads, and nowhere else. No server logic branches on it, no
stored identifier changes, and no prompt is built from it — ``component_type``
stays ``"cdd"``, the routes stay ``/api/v1/cdd/...``, and a tenant that has set
no override gets exactly the wording it gets today.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from app.schemas.ui_labels import parse_ui_labels

log = logging.getLogger(__name__)

#: What each key is called when a tenant has set no override. Must match
#: DEFAULT_LABELS in frontend/src/config/tenantLabels.js, so a document titled
#: server-side and a screen labelled client-side use the same word.
DEFAULTS = {"cdd": "CDD", "blueprint": "Blueprint", "title": "Title", "style": "Style"}

#: The long form, used where the deliverable is named in prose rather than as a
#: short label. Only the default has one — a tenant's own word IS its long form,
#: because "Blueprint" is what they call the thing, not an abbreviation of it.
LONG_FORM = {"CDD": "Course Design Document"}


def label(db: Any, project_id: Optional[str], key: str = "cdd") -> str:
    """The tenant's word for *key*, or the default.

    Never raises and never blocks a generation: a missing project, an
    unreadable column or a database that is momentarily unavailable all yield
    the default wording, which is the behaviour every caller had before.
    """
    default = DEFAULTS.get(key, key)
    if db is None or not project_id:
        return default
    try:
        from promptops_app.database import Project
        project = db.get(Project, project_id)
        if project is None:
            return default
        return parse_ui_labels(project.ui_labels).get(key) or default
    except Exception as exc:  # noqa: BLE001 — wording must never fail a build
        log.warning("deliverable label lookup failed for project=%s: %s", project_id, exc)
        return default


def long_label(db: Any, project_id: Optional[str], key: str = "cdd") -> str:
    """The prose form — "Course Design Document" by default, the tenant's own
    word when it has one. Used for the heading that opens the rendered document.
    """
    word = label(db, project_id, key)
    return LONG_FORM.get(word, word)


def filename_slug(word: str) -> str:
    """A label reduced to something safe in a download filename."""
    return "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in word).strip("_") or "Export"


def apply_terminology(text: str, db: Any, project_id: Optional[str], keys=None) -> str:
    """Swap default product words in a display/deliverable string for the tenant's.

    Whole-word, case-sensitive, singular + plural, in Title/UPPER/lower case.
    UPPER covers ALL-CAPS markers in generated content (e.g. "CONTENT TYPE:
    BLUEPRINT"). Display-only — used for the text that goes INTO a deliverable
    (heading, filename, rendered body); it never touches stored data, identifiers
    or prompts. Mirrors the frontend applyTerminology
    (frontend/src/config/tenantLabels.js). A tenant with no override, or any
    lookup failure, yields the text unchanged.
    """
    if not text or not isinstance(text, str):
        return text
    out = text
    for key in (keys or list(DEFAULTS.keys())):
        default = DEFAULTS.get(key)
        word = label(db, project_id, key)
        if not default or not word or word == default:
            continue
        # Title/UPPER/lower, plural before singular; dedupe by source so an
        # acronym like CDD (whose UPPER form equals its default) maps only once.
        variants = [
            (f"{default}s", f"{word}s"), (default, word),
            (f"{default.upper()}S", f"{word.upper()}S"), (default.upper(), word.upper()),
            (f"{default.lower()}s", f"{word.lower()}s"), (default.lower(), word.lower()),
        ]
        seen = set()
        for frm, to in variants:
            if not frm or frm in seen or frm == to:
                continue
            seen.add(frm)
            out = re.sub(rf"\b{re.escape(frm)}\b", to, out)
    return out
