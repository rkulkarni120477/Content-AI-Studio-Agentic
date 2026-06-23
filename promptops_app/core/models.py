"""LLM model catalog for Content AI Studio.

This file is the single source of truth for every model the application knows
about.  The rest of the codebase reads from MODEL_CATALOG / MODELS_BY_NAME
rather than hardcoding model strings or routing logic.

To add a new model
------------------
1. Append a ``ModelDef`` entry to ``MODEL_CATALOG``.
2. Restart the server — no other code changes are required.

To change an API model ID (e.g. after a provider releases a new version)
-------------------------------------------------------------------------
Edit ``api_model_id`` in the relevant ``ModelDef``.  The display_name shown
in the UI and stored in cookies stays unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Provider = Literal["openai", "bedrock"]


@dataclass(frozen=True)
class ModelDef:
    """Metadata for a single LLM option shown in the UI and used for routing."""

    display_name: str           # Key stored in session_state / cookie
    description:  str           # One sentence for the model card
    tags:         tuple[str, ...]  # Short chip labels ("default", "fast", …)
    provider:     Provider      # "openai" or "bedrock" — determines routing
    api_model_id: str           # Actual ID sent to the provider API
    is_default:   bool = False  # Pre-selected when no preference is saved


# ── Catalog ────────────────────────────────────────────────────────────────
# Order matters: shown top-to-bottom in the sidebar selector.
# OpenAI models appear first, Bedrock models second.

MODEL_CATALOG: tuple[ModelDef, ...] = (
    # ── OpenAI GPT Models ──────────────────────────────────────────────────
    ModelDef(
        display_name="GPT-5.4",
        description=(
            "Balanced intelligence and speed — the recommended choice for "
            "most eLearning content generation tasks."
        ),
        tags=("default", "reasoning"),
        provider="openai",
        api_model_id="gpt-4o",
        is_default=True,
    ),
    # ── AWS Bedrock Models ─────────────────────────────────────────────────
    ModelDef(
        display_name="Claude Sonnet 4.5 (Bedrock)",
        description=(
            "Anthropic Claude Sonnet on AWS Bedrock — excellent structured "
            "outputs, instruction-following, and JSON fidelity."
        ),
        tags=("structured",),
        provider="bedrock",
        api_model_id="global.anthropic.claude-sonnet-4-5-20250929-v1:0",
    ),
    ModelDef(
        display_name="Claude Haiku 4.5 (Bedrock)",
        description=(
            "Anthropic Claude Haiku on AWS Bedrock — fastest response times "
            "for high-volume or latency-sensitive generation."
        ),
        tags=("fast",),
        provider="bedrock",
        api_model_id="anthropic.claude-haiku-4-5-20251001-v1:0",
    ),
)

# ── Convenience lookups ────────────────────────────────────────────────────

MODELS_BY_NAME: dict[str, ModelDef] = {m.display_name: m for m in MODEL_CATALOG}

OPENAI_MODELS: tuple[ModelDef, ...] = tuple(
    m for m in MODEL_CATALOG if m.provider == "openai"
)
BEDROCK_MODELS: tuple[ModelDef, ...] = tuple(
    m for m in MODEL_CATALOG if m.provider == "bedrock"
)

# Backward-compatible aliases — maps an old display_name stored in cookies /
# session state to its new canonical display_name.
_COMPAT_ALIASES: dict[str, str] = {
    "Sonnet 4.5 (Bedrock)": "Claude Sonnet 4.5 (Bedrock)",
    "Claude Sonnet 4.6 (Bedrock)": "Claude Sonnet 4.5 (Bedrock)",
}

_DEFAULT: ModelDef = next((m for m in MODEL_CATALOG if m.is_default), MODEL_CATALOG[0])
DEFAULT_MODEL_NAME: str = _DEFAULT.display_name


# ── Public helpers ─────────────────────────────────────────────────────────

def resolve_model(display_name: str) -> ModelDef:
    """Return the ModelDef for *display_name*, applying compat aliases.

    Falls back gracefully to the default model if the name is unrecognised —
    prevents a crash when an old cookie contains a renamed model.
    """
    canonical = _COMPAT_ALIASES.get(display_name, display_name)
    return MODELS_BY_NAME.get(canonical, _DEFAULT)


def validate_model(display_name: str) -> ModelDef:
    """Return the ModelDef or raise ``ValueError`` for unknown model names.

    Use this in generation paths where silent fall-through must be avoided.
    """
    canonical = _COMPAT_ALIASES.get(display_name, display_name)
    if canonical not in MODELS_BY_NAME:
        raise ValueError(
            f"Unknown model '{display_name}'. "
            f"Valid choices: {list(MODELS_BY_NAME)}"
        )
    return MODELS_BY_NAME[canonical]
