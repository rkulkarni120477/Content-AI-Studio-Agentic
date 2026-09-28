"""Continuity review pass — a second look at screen-to-screen transitions.

CAS AIM findings, Phase 5 (finding 9): the continuity rules already exist in
every generation prompt, but they sit at the end of a very long single-reply
prompt that also has to generate every section's actual content, and nothing
re-reads the finished result afterward. A model juggling everything else in
that prompt reliably underweights instructions near the end that ask it to
look back across sections it already wrote.

This runs as an OPTIONAL SECOND LLM call, after the primary generation (and
CE validation) has already produced the final content: it re-reads the whole
thing end-to-end and rewrites ONLY the sentences that hand off from one
section to the next — the same review the source prompt's own "CONTINUITY
CHECK" step already asks for, that a single pass never actually performs.

Generic, not tied to any one client's heading or section convention: it only
requires the finished text to have multiple markdown headings, which is
already how every generation type on this path renders distinct sections —
so the cost/latency of one more LLM call is only paid when there's enough
structure for a hand-off to matter (a single-block lesson or a quiz question
has nothing to review and is skipped).

Non-fatal, same contract as ce_validation_service: any failure at any step
returns the original content unchanged.
"""
from __future__ import annotations

import logging
import re
from typing import Callable, Optional

_log = logging.getLogger(__name__)

#: Below this many section headings there's at most one transition to
#: review — not worth an extra LLM call for a single-block lesson, a quiz,
#: or similar short content.
_MIN_SECTIONS_TO_REVIEW = 3

_HEADING_RE = re.compile(r"(?m)^#{1,3}\s+\S")

_CONTINUITY_SYSTEM = (
    "You are an instructional-design editor performing a continuity pass on "
    "already-finished multi-section educational content. Your ONLY job is to "
    "review the transition into each section from the one before it, and "
    "rewrite a transition ONLY where it is weak.\n\n"
    "A transition is weak if it does not explain WHY the next section "
    "follows from the one before it (e.g. component -> function, cause -> "
    "effect, problem -> solution, general -> specific, part -> whole) — "
    "including a generic opener like \"Next, let's look at...\" or \"Now we "
    "will cover...\" that states no relationship at all.\n\n"
    "Rules:\n"
    "- Do not change any content, facts, structure, headings, or section "
    "order — only the transition sentence(s) at the start of a section.\n"
    "- Do not add or remove sections.\n"
    "- Do not use backward references such as \"as discussed earlier\" or "
    "\"as you learned previously\" — each section must still make sense read "
    "on its own.\n"
    "- If a transition is already clear and specific, leave it untouched.\n"
    "- Return the COMPLETE text with only the weak transitions revised — "
    "never a summary, a diff, or a partial excerpt."
)


def _section_count(content: str) -> int:
    return len(_HEADING_RE.findall(content or ""))


def run_continuity_review(
    content: str,
    *,
    model_choice: str = "GPT-5.6 Terra",
    llm_call_fn: Optional[Callable] = None,
) -> str:
    """Re-read *content* end-to-end and rewrite weak section transitions.

    Returns *content* unchanged when there are fewer than
    ``_MIN_SECTIONS_TO_REVIEW`` headings (not enough hand-offs to matter) or
    on any failure — matching ``ce_validation_service.run_ce_validation``'s
    own non-fatal contract.
    """
    if not content or not content.strip():
        return content
    if _section_count(content) < _MIN_SECTIONS_TO_REVIEW:
        return content

    if llm_call_fn is None:
        from promptops_app.services.llm_service import generate_with_metadata as _llm_meta
        llm_call_fn = _llm_meta

    try:
        from promptops_app.core.models import resolve_model
        _result = llm_call_fn(
            model_choice, _CONTINUITY_SYSTEM, content,
            max_tokens=resolve_model(model_choice).max_output_tokens,
        )
        if _result.is_error:
            _log.warning("Continuity review LLM call failed: %s", _result.text)
            return content
        revised = _result.text.strip()
        if not revised:
            return content
        return revised
    except Exception as exc:  # noqa: BLE001 — non-fatal review pass
        _log.warning("Continuity review step failed: %s", exc)
        return content
