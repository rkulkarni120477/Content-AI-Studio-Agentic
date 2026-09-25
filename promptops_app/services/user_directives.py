"""Carry the generation form's own inputs into the block-wide digest pipeline.

Why this exists
---------------
The block-wide path (CDD / Block Blueprint via enumerate→map→reduce→verify) grew
from the legacy single-call path but never inherited its input handling. Audited
2026-08-13: of the controls on the generation form, only the selected prompt
reached a model. Style, Additional Instructions and Estimated Duration were
accepted by the API, persisted into ``generation_params`` — so provenance showed
them as if honoured — and then used by nothing. The UI went further and rendered
"🎨 <style> will be applied (active style)" above a button for which it was false.

Silently discarding a user's input is worse than rejecting it: the controls look
live, the output looks finished, and nothing anywhere says the two are unrelated.
This module closes that gap.

What goes where, and why it differs per stage
--------------------------------------------
The two LLM stages are not interchangeable, so the directives are not identical:

* **Additional Instructions → MAP and REDUCE.** This is per-run intent ("emphasise
  Bloom's 4-6"), and the fields it targets are largely MAP's: ``derived_objective``,
  ``misconceptions``, ``concept_scope`` and ``salient_excerpts`` are all written by
  the per-day extraction, and REDUCE can only *append* framing to them
  (``objective_block_framing`` is append-only by construction). Instructions that
  reached REDUCE alone would be unable to change the cells they are aimed at.

* **Style → MAP (compact) and REDUCE (full).** Style is a voice/convention layer
  that matters wherever prose is written, and prose is written at both stages. But
  the full style context can be very large, and MAP pays for every character 20
  times over, so MAP gets the compact form (the style's validated summary) and
  REDUCE gets the full context. See the two caps in ``AppSettings``.

* **Estimated Duration → REDUCE only.** A per-day extractor can do nothing with a
  block-level hour count, and the authoritative structure is the enumerated
  calendar, not a number typed on a form. It is passed as a sizing signal for the
  pacing/readiness commentary, with an explicit instruction to REPORT a clash with
  the enumerated day count rather than quietly reconciling it — a declared 8 hours
  against 20 enumerated days is a real planning discrepancy worth surfacing, and
  inventing a reconciliation would bury it.

Cost consequence, stated because it is easy to be surprised by
--------------------------------------------------------------
Whatever reaches MAP is folded into the per-day digest ``cache_key`` (mapper.py),
and a digest document is keyed ``{client}:{block}:day{n}`` — one per block-day, not
one per variant. So changing Style or Additional Instructions invalidates every day
of that block and forces a full cold rebuild (~20 Bedrock calls, several minutes),
and two courses on the same block with different styles will rebuild over each
other's digests. That is the correct behaviour — a digest extracted under different
instructions IS a different digest, and serving the old one would be the silent
wrongness this module removes — but it is not free, and it is why the compact form
goes to MAP rather than the full one.

Failure policy
--------------
Best-effort and additive, exactly like ``prompt_guidance``: every failure degrades
to "no directives" and the pipeline behaves as it did before. This module never
raises. What it *does* refuse to do is fail quietly — an unresolvable style id and
any truncation are logged at WARNING naming the limit to raise, truncated text
carries a marker so the model knows it is reading a fragment, and every applied
input (plus anything cut) is recorded in ``applied`` for the provenance row. A log
line rotates away; the provenance row is what someone still has months later when
they ask why the style they selected barely shows up in the document.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

#: Heading for the requester's own words. Deliberately the strongest label of the
#: three: among the *user* inputs this is the one typed for this specific run, so
#: where it conflicts with the (standing, prompt-derived) guidance it should win.
#: The two hard limits are restated inline because this text is appended to a
#: structured-extraction contract, and a model that "helpfully" adds a field to
#: satisfy an instruction breaks the renderer that reads digests by exact key.
_INSTRUCTIONS_HEADING = (
    "REQUESTER'S INSTRUCTIONS FOR THIS GENERATION (typed on the generation form for "
    "this specific run — apply them while filling the fields requested above, in "
    "preference to any standing guidance they conflict with; they must never add a "
    "field that was not requested, drop one that was, or alter the required output "
    "schema):"
)

#: Same precedence rule as _INSTRUCTIONS_HEADING above, for the single-call
#: generation path (generation_jobs.py) instead of the block-wide MAP/REDUCE
#: one. That path has no structured-extraction contract to protect (freeform
#: lesson content, not per-field digest cells), so this omits the "don't add
#: /drop a field, don't alter the output schema" caveats — they don't apply
#: here and would just be confusing. The core rule is identical: CAS findings
#: v0.1 (AIM DLU storyboard review) traced several "the built-in prompt
#: overrode my instructions" complaints to this path appending the
#: requester's words with no precedence stated at all, so a conflicting
#: built-in default won every time.
ADDITIONAL_INSTRUCTIONS_HEADING = (
    "**Additional Instructions** (typed by the requester for this specific run — "
    "apply them in preference to any standing guidance above that they conflict with):"
)

_STYLE_HEADING = (
    "ACTIVE INSTRUCTIONAL STYLE — apply throughout (voice, terminology, structure "
    "and conventions; it governs HOW content is written, never WHICH fields exist "
    "or what the sources say):"
)

#: Mirrors ``BlockWideGenerateRequest.extra_instructions``'s own max_length, so on the
#: normal path this never bites. Kept anyway because the async worker rebuilds the
#: request from job-row JSON, which is not re-validated, and the blast radius on this
#: particular boundary is the whole build: MAP reserves a fixed 40k characters for
#: prompt scaffold + guidance, and overshooting a model's window is a hard Bedrock
#: rejection that fails every day of the block rather than degrading one.
_MAX_INSTRUCTION_CHARS = 5000


def _duration_heading(hours: int) -> str:
    return (
        f"REQUESTER'S DECLARED DURATION: {hours} hour(s) for this block. The "
        "enumerated day-by-day calendar is the authoritative structure — treat this "
        "number only as the intended teaching time when commenting on pacing, depth "
        "and production readiness. If it is clearly inconsistent with the number of "
        "enumerated days, SAY SO in the readiness commentary rather than silently "
        "reconciling the two."
    )


@dataclass
class UserDirectives:
    """The form's inputs, rendered per stage, plus what was actually applied.

    ``map_text``/``reduce_text`` are ready to append to a prompt (empty string when
    there is nothing to say). ``applied`` is the provenance record — it exists so a
    reviewer reading a generated document can confirm which inputs reached which
    stage instead of inferring it, which is precisely what was impossible before.
    """

    map_text: str = ""
    reduce_text: str = ""
    applied: Dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.map_text or self.reduce_text)


#: Appended to any text this module truncates. Without it a cut lands mid-sentence and
#: the fragment still reads as a complete instruction — the model has no way to tell
#: that it is acting on half of what the user wrote.
_TRUNCATION_MARKER = "\n[…truncated — the requester's input was longer than this run allows]"


def _cap(text: str, limit: int, what: str, limit_name: str) -> tuple[str, int]:
    """Truncate to *limit* chars, loudly. Returns (text, chars_dropped).

    Truncation is a fidelity loss in the one place a user's explicit input reaches the
    model, so it is reported three ways rather than none: a WARNING naming the exact
    limit to raise, a marker in the text itself so the model knows it is reading a
    fragment, and the dropped count returned for the provenance row (a log line rotates
    away; the audit row is the durable record, and "we capped your style" is precisely
    what someone asking why their style barely showed up needs to see). Same discipline
    ``prompt_guidance`` adopted after silent capping quietly cost ~60% of AIM's own
    template.
    """
    text = (text or "").strip()
    if len(text) <= limit:
        return text, 0
    dropped = len(text) - limit
    log.warning(
        "user_directives_capped what=%s chars=%d cap=%d dropped=%d — raise %s to "
        "send it whole",
        what, len(text), limit, dropped, limit_name,
    )
    # Budget the marker inside the limit rather than on top of it: the caps exist to
    # bound what reaches a model, and a cap that can be exceeded by its own overflow
    # notice is not a cap. Degrades to a bare cut if the limit is smaller than the
    # marker, which no sane configuration reaches.
    keep = max(0, limit - len(_TRUNCATION_MARKER))
    return ((text[:keep] + _TRUNCATION_MARKER) if keep else text[:limit]), dropped


def fingerprint(text: str) -> str:
    """Short content hash of *text*, or "" when there is none.

    Public because provenance elsewhere needs the same primitive: block_wide_service
    fingerprints the composed MAP guidance with it, and two different hashing
    conventions in one audit row would make the fields incomparable.

    Truncated to 16 hex chars: this is a change-detector for an audit trail, not a
    security primitive, and 64 bits makes an accidental collision between two style
    revisions of the same course a non-consideration.
    """
    text = (text or "").strip()
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _positive_int(value: Any) -> Optional[int]:
    """A positive int, or None for anything else. Never raises.

    Both numeric inputs arrive from a validated schema in the normal case, but the
    async worker rebuilds the request from job-row JSON, so this is also the boundary
    where a hand-written or older job row is read.
    """
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _compact_style_text(style: Any) -> str:
    """The short form of a style, for the stage that pays per day.

    Prefers the generated summary (the "style intelligence layer" — a validated,
    already-condensed understanding of the style, which is exactly what is wanted
    here) and falls back to the author's custom instructions. Deliberately does NOT
    fall back to the raw style documents: truncating the first 2000 characters of an
    arbitrary reference PDF is not a summary of a style, it is a random excerpt, and
    it would consume MAP's context headroom to say almost nothing.
    """
    for attr in ("generated_summary", "custom_instructions"):
        text = (getattr(style, attr, "") or "").strip()
        if text:
            return text
    return ""


def _resolve_style(db: Any, style_id: Optional[int], cluster_id: Optional[int]):
    """Load the style and render its full + compact context. Never raises.

    ``style_id`` is expected already normalised (see ``_positive_int``); None means no
    style was selected.

    Only an EXPLICIT ``style_id`` is honoured — there is deliberately no fallback to
    ``get_active_style``, whose unscoped global ``is_active`` lookup currently
    resolves 75 of 104 courses to one tenant's style. Both generation forms already
    send the id they display, so resolving it here would add a second, invisible
    source of truth for a control the user can see.

    ``with_documents=True`` eager-loads the style's documents: ``build_style_context``
    walks them, and a lazy load would be a detached-instance error the moment this runs
    against a session the style did not come from.
    """
    if not style_id:
        return None, "", ""
    try:
        from promptops_app.database import build_style_context
        from promptops_app.repositories import style_repository

        style = style_repository.get_style_by_id(db, style_id, with_documents=True)
        if style is None:
            # A dangling pointer, not a no-op: the user picked a style that no longer
            # exists (course 48 pointed at deleted style 116 on 2026-08-13), and the
            # generation is about to run without the style they were promised.
            log.warning("user_directives_style_missing style_id=%s — generating without it", style_id)
            return None, "", ""
        return style, build_style_context(db, style, cluster_id=cluster_id) or "", _compact_style_text(style)
    except Exception as exc:  # noqa: BLE001 — additive layer; never sink a generation
        log.warning("user_directives_style_failed style_id=%s error=%s", style_id, exc)
        return None, "", ""


def _section(heading: str, body: str = "") -> str:
    return f"{heading}\n{body}".strip() if body else heading


def resolve_user_directives(db: Any, request_body: Any,
                            *, cluster_id: Optional[int] = None) -> UserDirectives:
    """Render the generation form's inputs into per-stage guidance text.

    ``cluster_id`` is threaded through to ``build_style_context`` so a cluster's
    auto-injected prompts ride along with the style, matching the legacy path. Note
    that this puts cluster prompts on the REDUCE side only, since they are part of the
    full context and not of the compact form MAP receives.

    Never raises: any failure yields a ``UserDirectives`` whose texts are empty, so
    the caller's pipeline runs exactly as it did before this module existed.
    """
    try:
        from app.core.config import settings

        instructions, dropped_instructions = _cap(
            getattr(request_body, "extra_instructions", "") or "",
            _MAX_INSTRUCTION_CHARS, "extra_instructions", "_MAX_INSTRUCTION_CHARS",
        )
        hours = _positive_int(getattr(request_body, "estimated_duration_hours", None))
        # Normalised once, here, rather than re-coerced when building `applied` below:
        # a junk style_id raising at that point would discard the instructions too,
        # and the three inputs are independent of one another.
        style_id = _positive_int(getattr(request_body, "style_id", None))

        style, style_full, style_compact = _resolve_style(db, style_id, cluster_id)
        style_full, dropped_reduce = _cap(
            style_full, settings.block_wide_style_chars_reduce,
            "style/reduce", "BLOCK_WIDE_STYLE_CHARS_REDUCE",
        )
        style_compact, dropped_map = _cap(
            style_compact, settings.block_wide_style_chars_map,
            "style/map", "BLOCK_WIDE_STYLE_CHARS_MAP",
        )

        map_parts: List[str] = []
        reduce_parts: List[str] = []

        if instructions:
            section = _section(_INSTRUCTIONS_HEADING, instructions)
            map_parts.append(section)
            reduce_parts.append(section)
        if style_compact:
            map_parts.append(_section(_STYLE_HEADING, style_compact))
        if style_full:
            reduce_parts.append(_section(_STYLE_HEADING, style_full))
        if hours:
            reduce_parts.append(_duration_heading(hours))

        applied = {
            # Suffix every "did it apply" flag with _applied, since the same dict also
            # carries the requested VALUES: reading a persisted provenance row months
            # later, `extra_instructions: true` beside `style_id: 116` invites exactly
            # the wrong reading of both.
            "extra_instructions_applied": bool(instructions),
            "extra_instructions_chars": len(instructions),
            "style_id": style_id,
            "style_name": getattr(style, "name", "") or "",
            # Resolved, not requested: a style id that pointed at a deleted row shows
            # here as style_applied_to_* False next to a non-null style_id, which is
            # the distinction that matters when someone asks why the style that the
            # form promised had no effect.
            "style_applied_to_map": bool(style_compact),
            "style_applied_to_reduce": bool(style_full),
            "style_chars_map": len(style_compact),
            "style_chars_reduce": len(style_full),
            # Pins the exact style TEXT this run used, without storing 12k characters
            # on every audit row. Styles are mutable and deletable — course 48's
            # style 116 was already gone by 2026-08-13 — so style_id + style_name
            # alone cannot reproduce what the model actually saw once someone edits
            # or removes the style. Two documents with the same fingerprint were
            # built from the same style text; differing fingerprints prove they were
            # not, which is the question an audit actually has to answer.
            "style_fingerprint": fingerprint(style_full),
            "estimated_duration_hours": hours,
        }
        # Only when something was actually cut, so the common case stays a clean row
        # and a present key always means "fidelity was lost here". The char counts
        # above are post-cap — they are what the model saw — so without this an
        # auditor cannot tell a 12k style from a 40k style cut down to 12k, and the
        # WARNING that said so has long since rotated away.
        truncated = {what: dropped for what, dropped in (
            ("extra_instructions", dropped_instructions),
            ("style_reduce", dropped_reduce),
            ("style_map", dropped_map),
        ) if dropped}
        if truncated:
            applied["truncated_chars"] = truncated
        log.info(
            "user_directives_built instructions=%s style_id=%s style_map_chars=%d "
            "style_reduce_chars=%d hours=%s",
            bool(instructions), style_id, len(style_compact), len(style_full), hours,
        )
        return UserDirectives(map_text="\n\n".join(map_parts),
                              reduce_text="\n\n".join(reduce_parts),
                              applied=applied)
    except Exception as exc:  # noqa: BLE001 — see module docstring
        log.warning("user_directives_failed error=%s", exc)
        return UserDirectives()


def compose_guidance(prompt_guidance: str, directives_text: str) -> str:
    """Join the prompt-derived guidance and the requester's directives into one blob.

    Composed on THIS side rather than sent to DIS as a second wire field, and that is
    a deliberate deployment decision. ``map_guidance`` is already folded into the
    per-day digest cache key, so an old DIS receiving a composed string still
    invalidates the right digests; an old DIS receiving a *new* field would ignore it
    (Pydantic ``extra='ignore'``) AND keep serving digests built without it — the
    silent-drop failure this whole change exists to remove, reintroduced during every
    mixed-version deploy window.
    """
    parts = [(prompt_guidance or "").strip(), (directives_text or "").strip()]
    return "\n\n".join(p for p in parts if p)
