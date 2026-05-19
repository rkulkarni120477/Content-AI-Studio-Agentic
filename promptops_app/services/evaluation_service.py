"""Content quality and plagiarism evaluation service.

All LLM calls go through llm_client, not called directly.
The only Streamlit dependency is @st.cache_resource on get_plagi_model —
replace with functools.lru_cache before FastAPI migration (Phase 8).

Extracted from core/shared.py (Phase 3 refactoring).
"""

import logging

from promptops_app.prompt_templates import EVAL_PROMPT, SCORING_PROMPT, META_PROMPT, REVIEW_PROMPT, PLAGIARISM_PROMPT
from promptops_app.core.llm_client import safe_json_loads
from promptops_app.core.config import SYNC_QUALITY_CHECKS, SYNC_PLAGIARISM_CHECKS
from promptops_app.core.config import settings as _eval_cfg
from promptops_app.services.llm_service import generate_text as _llm

_EVAL_MODEL   = _eval_cfg.default_model  # follows PROMPTOPS_DEFAULT_MODEL — works with OpenAI or Bedrock
_log_eval     = logging.getLogger(__name__)
_log_plagiarism = logging.getLogger(__name__ + ".plagiarism")

# ── Prompt-library helpers ────────────────────────────────────────────────────
# The validation template from the library is optional — the inline constants
# above remain the fallback so nothing breaks if the template file is missing.

def _get_validation_prompts(block_type: str) -> tuple[str, str]:
    """Return (system_prompt, template_version) for the validation template.

    Falls back to the inline EVAL_PROMPT constant on any load error.
    """
    try:
        from promptops_app.prompts.prompt_loader import load_template
        from promptops_app.prompts.prompt_builder import render_safe
        tmpl = load_template("validation")
        system = render_safe(tmpl.system_template, {"block_type": block_type})
        return system or EVAL_PROMPT.format(block_type=block_type), tmpl.version
    except Exception:
        return EVAL_PROMPT.format(block_type=block_type), "v1-inline"


# ---------------------------------------------------------------------------
# Fast local evaluator (no LLM, runs on every save)
# ---------------------------------------------------------------------------

def lightweight_evaluate_text(text: str, block_type: str) -> dict:
    """Structural scoring using heuristics only. No LLM call.

    Used on generation/save paths for fast UX. Set PROMPTOPS_SYNC_QUALITY_CHECKS=true
    to switch to the full LLM evaluator on every save.
    """
    text = text or ""
    lower = text.lower()
    words = text.split()

    required = {
        "lesson":         ["introduction", "summary"],
        "quiz":           ["question"],
        "assessment":     ["question"],
        "course_outline": ["module"],
        "course_package": ["module"],
    }
    req     = required.get((block_type or "").lower(), [])
    missing = [item for item in req if item not in lower]

    has_headings = "#" in text or any(
        line.strip().endswith(":") for line in text.splitlines()
    )
    has_bullets = "\n- " in text or "\n* " in text or any(
        line.strip().startswith(("1.", "2.", "3.")) for line in text.splitlines()
    )

    score = 60
    score += 15 if has_headings else 0
    score += 10 if has_bullets else 0
    score += 15 if not missing else max(0, 15 - len(missing) * 7)
    if len(words) < 40:
        score -= 10

    return {
        "missing_sections":  missing,
        "word_count":        len(words),
        "has_headings":      has_headings,
        "has_bullets":       has_bullets,
        "readability_level": "Medium",
        "banned_phrases_found": [],
        "structural_score":  max(0, min(100, score)),
        "evaluation_mode":   "fast_local",
    }


# ---------------------------------------------------------------------------
# LLM-driven evaluator (slow, used on explicit user request)
# ---------------------------------------------------------------------------

def evaluate_text(text: str, block_type: str) -> dict:
    """Full LLM-driven structural evaluation.

    Checks required sections, readability, and banned phrases dynamically.
    Falls back to lightweight_evaluate_text on LLM/parse failure.
    """
    system_prompt, _ver = _get_validation_prompts(block_type)
    result = _llm(_EVAL_MODEL, system_prompt, f"CONTENT:\n{text}")
    try:
        data = safe_json_loads(result)
        if data:
            return data
        raise ValueError("Empty or invalid JSON")
    except Exception:
        lower = text.lower()
        required = {
            "lesson":         ["introduction", "summary", "example"],
            "quiz":           ["questions"],
            "course_outline": ["modules"],
            "course_package": ["outline"],
        }
        missing = [item for item in required.get(block_type, []) if item not in lower]
        return {
            "missing_sections":     missing,
            "word_count":           len(text.split()),
            "has_headings":         "#" in text,
            "has_bullets":          "-" in text or "*" in text,
            "readability_level":    "Medium",
            "banned_phrases_found": [],
            "structural_score":     50,
        }


def compare_outputs(left: str, right: str) -> dict:
    return {
        "left_length":        len(left),
        "right_length":       len(right),
        "left_has_example":   "example" in left.lower(),
        "right_has_example":  "example" in right.lower(),
        "recommendation":     "Prefer the structured version.",
    }


def score_content_quality(text: str) -> dict:
    """Use LLM to evaluate content quality across structure, depth, engagement, readability."""
    result = _llm(_EVAL_MODEL, SCORING_PROMPT, f"CONTENT TO EVALUATE:\n{text}")
    try:
        res = safe_json_loads(result)
        if res:
            return res
        raise ValueError("Empty response")
    except Exception:
        words = text.split()
        return {
            "total_score":  50,
            "grade":        "C",
            "structure":    15,
            "depth":        15,
            "engagement":   10,
            "readability":  10,
            "word_count":   len(words),
            "suggestions":  "Could not parse LLM evaluation.",
        }


def generate_prompt_template_with_llm(description: str) -> dict:
    """Use LLM to generate a prompt template from a user description."""
    result = _llm(_EVAL_MODEL, META_PROMPT, f"USER DESCRIPTION: {description}")
    try:
        res = safe_json_loads(result)
        if res:
            return res
        raise ValueError("Empty response")
    except Exception:
        return {
            "name":                 "custom_prompt",
            "system_prompt":        "You are a helpful AI assistant.",
            "user_prompt_template": "Create a {block_type} about {topic}.",
            "tags":                 "custom",
            "description":          description,
        }


def llm_evaluate_block(text: str, block_type: str) -> str:
    """Use LLM for intelligent, contextual content review."""
    return _llm(_EVAL_MODEL, REVIEW_PROMPT.format(block_type=block_type), f"CONTENT:\n{text}")


def evaluate_readability(text: str) -> dict:
    """Fast local readability heuristics — no LLM call."""
    words = text.split()
    avg_word_len = sum(len(w) for w in words) / len(words) if words else 0.0
    sentences = text.count(".") + text.count("!") + text.count("?")
    complexity = "Low" if avg_word_len < 5 else "Medium" if avg_word_len < 7 else "High"
    return {
        "avg_word_length": f"{avg_word_len:.2f}",
        "complexity":      complexity,
        "sentence_count":  sentences,
    }


# ---------------------------------------------------------------------------
# Plagiarism / AI-content detection
# DEPRECATED — detection is now async via Copyleaks (plagiarism_service.py).
# These stubs remain so that old call-sites (shared.py imports) don't crash.
# ---------------------------------------------------------------------------

def get_plagi_model():
    """Removed — HuggingFace local model replaced by Copyleaks async API."""
    return None, None


def check_plagiarism_content(text: str) -> dict:
    """DEPRECATED — synchronous plagiarism check removed.

    Plagiarism is now handled asynchronously via Copyleaks
    (``services/plagiarism_service.py`` + ``jobs/plagiarism_jobs.py``).
    This stub is kept so legacy import sites don't crash at startup.
    It returns a deferred result so no score is stored on existing callers.
    """
    _log_plagiarism.debug("check_plagiarism_content called — returning deferred stub")
    return {
        "confidence_score": None,
        "explanation": (
            "Plagiarism is now checked asynchronously via Copyleaks. "
            "Use the Editor panel to trigger a scan."
        ),
        "check_error": True,
    }


# ---------------------------------------------------------------------------
# Combined metadata helper (used on generation/save paths)
# ---------------------------------------------------------------------------

def get_initial_quality_metadata(content: str, block_type: str) -> tuple:
    """Return (plagiarism_result, eval_data, ai_review_text) for a new block.

    Uses fast/local checks by default. Set env vars to true for LLM checks:
      PROMPTOPS_SYNC_PLAGIARISM_CHECKS=true
      PROMPTOPS_SYNC_QUALITY_CHECKS=true
    """
    # Plagiarism is always deferred — Copyleaks async scan is triggered
    # separately after blocks are saved (see generation_jobs.py).
    plagi_res = {
        "confidence_score": None,
        "explanation":      "Async Copyleaks scan queued — results appear in the Editor.",
        "check_error":      True,
    }
    _ = SYNC_PLAGIARISM_CHECKS   # config flag kept for reference; not used anymore

    if SYNC_QUALITY_CHECKS:
        eval_data  = evaluate_text(content, block_type)
        ai_review  = llm_evaluate_block(content, block_type)
    else:
        eval_data  = lightweight_evaluate_text(content, block_type)
        ai_review  = "Deferred for faster UX. Use Request AI Review from the Editor when needed."

    return plagi_res, eval_data, ai_review
