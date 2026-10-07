"""File-backed prompt templates for the digest MAP step, with content hashing.

Why a loader here rather than reuse of the CAS prompt registry
--------------------------------------------------------------
``dis_backend`` and ``promptops_app`` are separate deployables with no shared
import path and no shared database, so the MAP prompt cannot resolve through
``promptops_app.prompts.prompt_loader``'s DB tier. What the CAS side *does* send
over the wire is ``map_guidance`` — the distilled judgment layer from the
admin's DB-maintained prompt (see ``prompt_guidance.resolve_prompt_guidance``),
which this prompt appends verbatim. So the split is:

* **DB-maintained, per course** → ``map_guidance`` (judgment, tone, emphasis).
* **File-maintained, per deployment** → this template (the extraction rubric and
  the ``concept_type`` taxonomy), previously a 70-line f-string literal in
  ``mapper.py`` that nobody could edit without a code change.
* **Code-owned, never editable** → the JSON schema line, injected as
  ``{{schema}}``. An admin can rewrite every word of the rubric and still cannot
  break the reply contract that ``mapper``/the worksheet renderer parse by key.

Cache correctness
-----------------
Per-day digests are content-addressed. Editing this template changes MAP output,
so the template's **content hash** is folded into the digest cache key via
``version()`` — an edit invalidates exactly the digests it would have changed, with
no manual bookkeeping. That is also why the cache below is **mtime-aware** rather
than load-once: a stale process serving a pre-edit template while reporting a
post-edit version would poison the cache with mislabeled digests.

A missing or unreadable template file falls back to the caller's built-in constant,
so a bad deploy degrades to the previous behavior rather than failing every build.
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
from pathlib import Path
from typing import Optional, Tuple

log = logging.getLogger(__name__)

_TEMPLATE_DIR = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "dis_backend"
    / "services"
    / "digests"
    / "templates"
)
_VAR_RE = re.compile(r"\{\{([a-zA-Z_][a-zA-Z0-9_]*)\}\}")

# name -> (mtime_ns, size, text, content_hash)
_CACHE: dict[str, Tuple[int, int, str, str]] = {}
_LOCK = threading.Lock()


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def load(name: str, builtin: str) -> Tuple[str, str]:
    """Return ``(template_text, content_hash)`` for *name*.

    Reads ``templates/<name>.md``, falling back to *builtin* when the file is
    absent or unreadable. Cached on (mtime, size) so an edit is picked up without
    a restart — important because the returned hash drives digest invalidation, so
    text and hash must never disagree.
    """
    path = _TEMPLATE_DIR / f"{name}.md"
    try:
        stat = path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        log.info("digest prompt template %s not found at %s — using built-in", name, path)
        return builtin, _hash(builtin)

    with _LOCK:
        cached = _CACHE.get(name)
        if cached is not None and (cached[0], cached[1]) == stamp:
            return cached[2], cached[3]

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("digest prompt template %s unreadable (%s) — using built-in", name, exc)
        return builtin, _hash(builtin)

    if not text.strip():
        log.warning("digest prompt template %s is empty — using built-in", name)
        return builtin, _hash(builtin)

    digest = _hash(text)
    with _LOCK:
        _CACHE[name] = (stamp[0], stamp[1], text, digest)
    log.info("digest prompt template loaded name=%s hash=%s chars=%d", name, digest, len(text))
    return text, digest


def render(template: str, variables: dict) -> str:
    """Substitute ``{{name}}`` placeholders. Unknown placeholders are left in
    place verbatim rather than raising — a partly-rendered prompt is recoverable
    (and visible in the logged prompt), an exception mid-block-build is not."""
    def _sub(m: "re.Match[str]") -> str:
        val = variables.get(m.group(1))
        return m.group(0) if val is None else str(val)
    return _VAR_RE.sub(_sub, template)


def check_contract(text: str, required_keys: tuple[str, ...]) -> list[str]:
    """Return the required reply keys *text* fails to mention (empty ⇒ ok).

    A necessary-not-sufficient check, but it catches the realistic failure: a
    template edit that drops a field the parser reads by name.
    """
    return [k for k in required_keys if k not in text]


def resolve(name: str, builtin: str, required_keys: tuple[str, ...],
            schema: str = "") -> Tuple[str, str]:
    """``load`` + contract check. Falls back to *builtin* when the template would
    not ask for every required reply key.

    The check runs against the template with ``{{schema}}`` already substituted,
    because that is where the keys legitimately come from: the whole point of
    injecting the schema is that an admin rewriting the rubric cannot drop a key.
    Checking the raw text instead would reject every correct template that relies
    on the injection (and would only pass ones that redundantly hardcode the keys).
    """
    text, digest = load(name, builtin)
    if text is not builtin:
        missing = check_contract(render(text, {"schema": schema}), required_keys)
        if missing:
            log.warning(
                "digest prompt template %s omits required reply keys %s — using built-in. "
                "Fix the template: keep the {{schema}} placeholder (the JSON contract is "
                "injected there) or name the keys explicitly.",
                name, ", ".join(missing),
            )
            return builtin, _hash(builtin)
    return text, digest


def clear_cache(name: Optional[str] = None) -> None:
    """Drop cached template text. For tests and admin-triggered reloads."""
    with _LOCK:
        if name is None:
            _CACHE.clear()
        else:
            _CACHE.pop(name, None)
