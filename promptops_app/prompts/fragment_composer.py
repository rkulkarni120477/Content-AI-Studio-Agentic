"""Fragment resolution + prompt composition (consolidation Phase 9).

``resolve_fragment`` is the read tier used by live generation paths:
DB fragment (flag-on only) → legacy constant fallback. With the flag off, or
with no authored fragment row, callers get exactly the constant they always
used — byte-identical legacy behavior.

``compose_prompt`` assembles the Phase 9 formula
    guardrails + context_header + persona_tone + stage_unique_template
    + output_contract
skipping any fragment that has no content. No live path calls it yet — the
stage templates still carry their own guardrails/contract text inline, and
extracting that text into fragments is a content decision. It ships tested
so authoring those fragments is purely a data exercise.

Fragments are {{double}}-brace text rendered non-strictly (unknown
placeholders stay literal, like every other non-strict render in the
pipeline). The legacy constants are .format-style; callers keep formatting
the constant themselves on the fallback path.
"""

from promptops_app.prompts.prompt_builder import render_safe
from promptops_app.prompts.prompt_loader import component_resolution_enabled


def resolve_fragment(db, key: str, *, fallback: str | None = None) -> tuple[str | None, str]:
    """Return ``(text, source)`` for fragment *key*.

    source is ``"db"`` when an active fragment version resolved (requires the
    PROMPT_RESOLVE_BY_COMPONENT flag, same rollout gate as the registry
    tiers), else ``"constant"`` with *fallback* returned verbatim.
    """
    if db is not None and component_resolution_enabled():
        from promptops_app.repositories.fragment_repository import (
            get_active_fragment_text,
        )
        text = get_active_fragment_text(db, key)
        if text is not None:
            return text, "db"
    return fallback, "constant"


def render_fragment(db, key: str, variables: dict, *, fallback_rendered: str) -> str:
    """Resolve *key* and render it with *variables* ({{double}}, non-strict).

    ``fallback_rendered`` is the already-formatted legacy text used when no
    fragment resolves — the caller keeps its own .format call so the fallback
    path stays byte-identical to pre-Phase-9 behavior.
    """
    text, source = resolve_fragment(db, key)
    if source == "db" and text is not None:
        return render_safe(text, variables)
    return fallback_rendered


def compose_prompt(
    db,
    stage_template: str,
    *,
    variables: dict | None = None,
    output_contract_key: str | None = None,
) -> str:
    """Assemble guardrails + context_header + persona_tone + stage template
    + output contract, skipping fragments with no content."""
    variables = variables or {}
    parts: list[str] = []
    for key in ("guardrails", "context_header", "persona_tone"):
        text, source = resolve_fragment(db, key)
        if source == "db" and text:
            parts.append(render_safe(text, variables))
    parts.append(stage_template)
    if output_contract_key:
        contract, source = resolve_fragment(db, output_contract_key)
        if source == "db" and contract:
            parts.append(render_safe(contract, variables))
    return "\n\n".join(p.strip() for p in parts if p and p.strip())
