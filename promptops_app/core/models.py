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
    # Max output tokens to request for this model. Used by block-wide reduce so a
    # larger-context model gets headroom instead of the flat 16384 cap that
    # truncates long sectioned output (D6). Conservative defaults; tune in eval.
    max_output_tokens: int = 16384


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
        # The ONLY Anthropic model this account invokes reliably: 3/3 consecutive
        # InvokeModel calls in both ap-south-1 and us-east-1. Sonnet 5 / Opus 5 /
        # Sonnet 4.6 are listed by Bedrock but each gave one spurious success then
        # failed every repeat, so they are not usable yet.
        api_model_id="global.anthropic.claude-sonnet-4-5-20250929-v1:0",
        # A cap below the model's real limit truncates long sectioned output, which
        # shows up as missing worksheet rows rather than an error. Bedrock does not
        # reject oversized max_tokens (128000 was accepted in a probe), so this is
        # sized generously on purpose.
        max_output_tokens=64000,
    ),
    ModelDef(
        display_name="Claude Haiku 4.5 (Bedrock)",
        description=(
            "Anthropic Claude Haiku on AWS Bedrock — fastest response times "
            "for high-volume or latency-sensitive generation."
        ),
        tags=("fast",),
        provider="bedrock",
        # NOT CURRENTLY INVOKABLE on this AWS account. The bare on-demand ID is
        # rejected ("Retry with the ID or ARN of an inference profile"), and every
        # inference-profile form returns AccessDeniedException ("Model access is
        # denied due to IAM user or service role is not authorized") — verified live
        # via InvokeModel in both us-east-1 and ap-south-1. The account simply has no
        # Bedrock model access for Haiku 4.5; no prefix fixes that.
        #
        # Kept in the catalog so stored user preferences keep resolving and so the
        # entry is one access-grant away from working. Selecting it falls back to
        # OpenAI, which is reported honestly as the actual model used.
        api_model_id="global.anthropic.claude-haiku-4-5-20251001-v1:0",
        max_output_tokens=16384,
    ),
    ModelDef(
        display_name="Claude Opus 5 (Bedrock)",
        description=(
            "Anthropic Claude Opus 5 on AWS Bedrock — the most capable model, "
            "for the hardest reasoning and long-horizon content generation, with "
            "a 1M-token context window."
        ),
        tags=("reasoning", "premium"),
        provider="bedrock",
        # NOT CURRENTLY INVOKABLE: 0/3 in both regions (AccessDeniedException), same
        # as Haiku 4.5. It is LISTED by Bedrock and returned one spurious success
        # during probing, which is exactly why a single OK is not evidence. Kept in
        # the catalog because it is one model-access grant from being the right
        # premium model; selecting it degrades to the fallback chain.
        api_model_id="global.anthropic.claude-opus-5",
        max_output_tokens=64000,
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
# Every name ever shown in the picker must keep resolving: these are stored in
# cookies, session state, and courses' config_model_choice, so an unmapped old name
# would silently resolve to the catalog default and change which model a course has
# been generating with. Point superseded names at their current generation.
_COMPAT_ALIASES: dict[str, str] = {
    "Sonnet 4.5 (Bedrock)": "Claude Sonnet 4.5 (Bedrock)",
    "Claude Sonnet 4.6 (Bedrock)": "Claude Sonnet 4.5 (Bedrock)",
    "Claude Sonnet 5 (Bedrock)": "Claude Sonnet 4.5 (Bedrock)",
    "Claude Opus 4.8 (Bedrock)": "Claude Opus 5 (Bedrock)",
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


# ── Quality tiers for block-wide generation (D2) ────────────────────────────
# A UI-selectable quality tier picks the REDUCE model for block-wide CDD /
# Blueprint. The per-day MAP model is pinned separately (DIS side) so the digest
# cache is shared across tiers — switching tier is one reduce call, not a full
# rebuild. Standard is the default.

@dataclass(frozen=True)
class TierModels:
    tier: str
    reduce_model: str          # display_name resolvable via resolve_model()
    max_output_tokens: int


# All three tiers resolve to Sonnet 4.5: it is the only Anthropic model this account
# invokes reliably (3/3 per region). Haiku 4.5, Opus 4.8, Opus 5, Sonnet 5 and
# Sonnet 4.6 all measure 0/3, and a tier resolving to an uninvokable model fails the
# whole generation for anyone who picks it.
#
# So tier selection does NOT differentiate model capability today. Restore
# draft->Haiku and premium->Opus 5 once those access grants land AND the IDs measure
# clean on repeated probes from the target environment.
_TIER_REDUCE_MODEL: dict[str, str] = {
    "draft":    "Claude Sonnet 4.5 (Bedrock)",
    "standard": "Claude Sonnet 4.5 (Bedrock)",
    "premium":  "Claude Sonnet 4.5 (Bedrock)",
}
DEFAULT_TIER = "standard"


def resolve_tier(tier: str | None) -> TierModels:
    """Map a quality tier ('draft'|'standard'|'premium') to its REDUCE model.

    Unknown/blank tiers fall back to the default tier rather than raising, so a
    stale UI value can never break generation.
    """
    key = (tier or DEFAULT_TIER).strip().lower()
    name = _TIER_REDUCE_MODEL.get(key)
    if name is None:
        key, name = DEFAULT_TIER, _TIER_REDUCE_MODEL[DEFAULT_TIER]
    model = resolve_model(name)
    return TierModels(tier=key, reduce_model=name, max_output_tokens=model.max_output_tokens)
