"""Prompt loader — file-based templates with DB override support.

Load order (highest priority first)
-------------------------------------
1. **Database** — when a ``db`` session is provided and a matching
   ``Prompt`` / ``PromptVersion`` row exists (admin-updatable at runtime).
   With ``PROMPT_RESOLVE_BY_COMPONENT`` enabled, the DB tier resolves the
   pipeline row by scope fixing → component default → legacy stem ``name``;
   otherwise by stem ``name`` only.
2. **File**     — ``promptops_app/prompts/templates/<name>.md``.
3. ``FileNotFoundError`` if neither source is available.

Template file format
---------------------
Each ``.md`` file is split at the ``--- USER ---`` marker:

    --- SYSTEM ---
    <system prompt with {{variables}}>

    --- USER ---
    <user prompt with {{variables}}>

If the marker is absent the entire file content is treated as the user prompt
and the system prompt is empty.

Variable syntax in template files
-----------------------------------
``{{variable_name}}``  (double braces).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

_TEMPLATE_DIR = Path(__file__).parent / "templates"

# ── Component-keyed resolution (Phase 8 name-key fix) ─────────────────────────
#
# Historically the DB tier looked up rows by exact ``Prompt.name`` == the stem
# below, so the seeded ``default_*`` rows (keyed by component_type + is_default)
# never satisfied a live lookup. With PROMPT_RESOLVE_BY_COMPONENT enabled, the
# DB tier resolves pipeline prompts the way the management console keys them:
#
#   1. scope fixing        — resolve_fixed_prompt(component, course→cluster→project→global)
#   2. component default   — the is_default=True row for the component_type
#   3. legacy stem name    — Prompt.name == stem (secondary key, kept for
#                            admin-created stem-named rows)
#
# Both DB tiers are variant-aware: an exact (component_type, variant) row wins,
# the NULL-variant row is the fallback, and a different variant never matches
# (see _acceptable_variants).
#
# The flag defaults OFF: the seeded default rows' bodies still carry legacy
# ``{single}``-brace text that the ``{{double}}`` renderer would pass through
# verbatim — verify/convert the default rows' bodies in an environment before
# enabling it there. A bad default row degrades to the file/inline fallback
# tiers only via the flag; turn it off to restore stem-name behavior exactly.

_STEM_COMPONENT: dict[str, str] = {
    "style_understanding":  "style",
    "cdd_generation":       "cdd",
    "blueprint_generation": "blueprint",
    "content_generation":   "generate",
    # Quiz gets its own component key (no seeded default yet) so an assessment
    # can never resolve the lesson default for "generate".
    "quiz_generation":      "quiz",
}


def component_resolution_enabled() -> bool:
    """True when the PROMPT_RESOLVE_BY_COMPONENT feature flag is on."""
    return os.getenv("PROMPT_RESOLVE_BY_COMPONENT", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class PromptTemplate:
    """A resolved, version-tagged prompt template ready for rendering."""
    name:            str
    version:         str
    description:     str
    system_template: str
    user_template:   str
    required_vars:   list[str] = field(default_factory=list)
    optional_vars:   list[str] = field(default_factory=list)
    source:          str = "file"   # "file" | "db"


# ── Registry — metadata for all known templates ───────────────────────────────
#
# This table drives:
#   • required_vars enforcement in build_prompt(strict=True)
#   • Admin UI dropdowns showing what variables a template needs
#   • Version tracking

_REGISTRY: dict[str, dict] = {
    "style_understanding": {
        "version":      "v1",
        "description":  "Generate a Style Intelligence Layer from reference documents.",
        "required_vars": ["document_summary"],
        "optional_vars": ["style_guidelines", "extra_instructions"],
    },
    "cdd_generation": {
        "version":      "v1",
        "description":  "Generate a Course Design Document (CDD).",
        "required_vars": ["course_name", "target_audience", "expert_domain"],
        "optional_vars": [
            "grade_level", "audience_level", "estimated_duration",
            "style_guidelines", "extra_instructions",
        ],
    },
    "blueprint_generation": {
        "version":      "v1",
        "description":  "Generate a module-level blueprint from a CDD.",
        "required_vars": ["cdd_context", "selected_module"],
        "optional_vars": [
            "teacher_mode", "student_mode", "style_guidelines", "extra_instructions",
        ],
    },
    "content_generation": {
        "version":      "v1",
        "description":  "Generate student-facing lesson content with CDD/Blueprint context.",
        "required_vars": ["topic", "learning_objectives"],
        "optional_vars": [
            "course_name", "grade_level", "style_guidelines", "cdd_context",
            "blueprint_context", "context_injection", "output_format",
            "target_audience", "teacher_mode", "student_mode",
            "lesson_title", "lesson_objective", "content_type",
        ],
    },
    "quiz_generation": {
        "version":      "v1",
        "description":  "Generate a quiz or knowledge-check assessment.",
        "required_vars": ["topic", "learning_objectives"],
        "optional_vars": [
            "grade_level", "output_format", "style_guidelines",
            "cdd_context", "blueprint_context", "course_name",
        ],
    },
    "validation": {
        "version":      "v1",
        "description":  "Evaluate and score generated content for quality and structure.",
        "required_vars": [],
        "optional_vars": ["block_type", "output_format"],
    },
    "feedback_extraction": {
        "version":      "v1",
        "description":  "Extract structured reviewer feedback items from an uploaded document.",
        "required_vars": ["document_text"],
        "optional_vars": ["document_name"],
    },
    "feedback_recommendation": {
        "version":      "v1",
        "description":  "Recommend how to revise course content to address a reviewer feedback item.",
        "required_vars": ["feedback_text", "course_content"],
        "optional_vars": [
            "course_name", "feedback_theme", "feedback_sentiment", "feedback_location",
            "extra_instructions",
        ],
    },
    # ── Reverse pipeline (IMSCC import) — additive, file-only templates ─────────
    # Not in _STEM_COMPONENT, so they resolve straight from the file tier; the
    # forward scratch prompts above are never touched. See reverse_cas.md §S5.
    "reverse_blueprint": {
        "version":      "v1",
        "description":  "Reconstruct a module blueprint from imported module content.",
        "required_vars": ["module_title", "module_content"],
        "optional_vars": ["course_name", "extra_instructions"],
    },
    "reverse_cdd": {
        "version":      "v1",
        "description":  "Reconstruct a Course Design Document from imported course structure.",
        "required_vars": ["course_name", "course_content"],
        "optional_vars": ["target_audience", "expert_domain", "extra_instructions"],
    },
    "style_analysis": {
        "version":      "v1",
        "description":  "Reverse-detect the instructional style from imported lessons.",
        "required_vars": ["lesson_samples"],
        "optional_vars": ["course_name", "extra_instructions"],
    },
}


# ── Public API ────────────────────────────────────────────────────────────────

def load_template(
    name: str,
    *,
    version: str = "latest",
    db=None,
    project_id=None,
    cluster_id=None,
    course_id=None,
    variant=None,
    require_variant: bool = False,
) -> PromptTemplate:
    """Load a prompt template by name.

    Parameters
    ----------
    name:
        Logical name — matches ``.md`` filename stem and DB ``Prompt.name``.
    version:
        ``"latest"`` (default) resolves to the active DB version or the file.
        A specific string like ``"v2"`` targets an exact DB version.
    db:
        SQLAlchemy session.  When supplied, DB rows take precedence over files
        so admin edits made through the Prompts UI are picked up at runtime.
    project_id / cluster_id / course_id:
        Optional generation context, honored only when component-keyed
        resolution is enabled: the most specific ``PromptFixing`` for the
        stem's component wins over the component default.
    variant:
        Optional pipeline variant (e.g. ``"teacher"``/``"student"`` for
        blueprint, ``"interactive"`` for generate), honored only when
        component-keyed resolution is enabled.  An exact
        ``(component_type, variant)`` row wins; the NULL-variant row is the
        fallback; a different variant never matches.
    require_variant:
        When ``True``, only a DB row whose variant exactly equals *variant*
        may resolve — no NULL-variant, stem-name, or file fallback (those are
        all a *different* variant's template; the caller keeps its own bespoke
        fallback).  Raises ``FileNotFoundError`` when no such row exists.
    """
    if db is not None:
        tmpl = _from_db(
            db, name, version,
            project_id=project_id, cluster_id=cluster_id, course_id=course_id,
            variant=variant, require_variant=require_variant,
        )
        if tmpl is not None:
            return tmpl

    if require_variant:
        raise FileNotFoundError(
            f"No '{name}' pipeline row with variant={variant!r} is resolvable "
            f"(require_variant — stem/file tiers are not variant-eligible)"
        )

    return _from_file(name)


def list_template_names() -> list[str]:
    """Return names of all templates available on disk."""
    if not _TEMPLATE_DIR.exists():
        return list(_REGISTRY.keys())
    return sorted(p.stem for p in _TEMPLATE_DIR.glob("*.md"))


def get_registry_metadata(name: str) -> dict:
    """Return the registry metadata dict for *name* (empty dict if unknown)."""
    return dict(_REGISTRY.get(name, {}))


# ── Internal loaders ─────────────────────────────────────────────────────────

def _from_file(name: str) -> PromptTemplate:
    path = _TEMPLATE_DIR / f"{name}.md"
    if not path.exists():
        available = ", ".join(list_template_names()) or "none"
        raise FileNotFoundError(
            f"Prompt template '{name}' not found at {path}. "
            f"Available templates: {available}"
        )
    raw = path.read_text(encoding="utf-8")
    system_tmpl, user_tmpl = _split(raw)
    meta = _REGISTRY.get(name, {})
    return PromptTemplate(
        name            = name,
        version         = meta.get("version", "v1"),
        description     = meta.get("description", ""),
        system_template = system_tmpl,
        user_template   = user_tmpl,
        required_vars   = list(meta.get("required_vars", [])),
        optional_vars   = list(meta.get("optional_vars", [])),
        source          = "file",
    )


def _acceptable_variants(variant, require_variant: bool) -> tuple:
    """The Prompt.variant values a resolution may return, in preference order.

    - no variant requested        → NULL-variant rows only
    - variant requested           → exact variant, then NULL-variant fallback
    - require_variant             → exact variant only (caller has its own
                                    bespoke fallback — Generate's interactive path)

    Never sideways: a request for one variant can never resolve another.
    """
    if variant is None:
        return (None,)
    if require_variant:
        return (variant,)
    return (variant, None)


def _resolve_pipeline_row(
    db, stem: str, *,
    project_id, cluster_id, course_id,
    variant=None, require_variant: bool = False,
):
    """Component-keyed row resolution: scope fixing → component default → None.

    Only ever returns ``prompt_kind='pipeline'`` rows — a library row can never
    be injected into a generation call (Decision 1).
    """
    component = _STEM_COMPONENT.get(stem)
    if component is None:
        return None

    from promptops_app.database import Prompt
    from promptops_app.repositories.prompt_repository import (
        get_default_prompt,
        resolve_fixed_prompt,
    )

    acceptable = _acceptable_variants(variant, require_variant)

    fixing = resolve_fixed_prompt(
        db, component,
        project_id=project_id, cluster_id=cluster_id, course_id=course_id,
        acceptable_variants=acceptable,
    )
    if fixing is not None and fixing.prompt_id:
        row = (
            db.query(Prompt)
            .filter(Prompt.id == fixing.prompt_id,
                    Prompt.prompt_kind == "pipeline",
                    # A soft-deleted bound row never resolves (doc §9);
                    # falling through lands on the component default.
                    Prompt.deleted_at.is_(None))
            .first()
        )
        if row is not None:
            return row

    return get_default_prompt(
        db, component, variant, variant_fallback=not require_variant,
    )


def _from_db(
    db, name: str, version: str,
    *, project_id=None, cluster_id=None, course_id=None,
    variant=None, require_variant: bool = False,
) -> PromptTemplate | None:
    """Try to load from the Prompt / PromptVersion ORM tables.  Returns None on any miss."""
    try:
        from promptops_app.database import Prompt, PromptVersion

        prompt = None
        if component_resolution_enabled():
            prompt = _resolve_pipeline_row(
                db, name,
                project_id=project_id, cluster_id=cluster_id, course_id=course_id,
                variant=variant, require_variant=require_variant,
            )
        if prompt is None:
            if require_variant:
                # The stem-named row is not variant-keyed — never eligible here.
                return None
            # Legacy secondary key: exact stem-named row (never soft-deleted —
            # dormant guard, no write path soft-deletes pipeline rows today).
            prompt = db.query(Prompt).filter(Prompt.name == name,
                                             Prompt.deleted_at.is_(None)).first()
        if not prompt:
            return None

        if version == "latest":
            pv = (
                db.query(PromptVersion)
                .filter(PromptVersion.prompt_id == prompt.id,
                        PromptVersion.is_active == True)
                .first()
            )
            if pv is None:
                pv = (
                    db.query(PromptVersion)
                    .filter(PromptVersion.prompt_id == prompt.id)
                    .order_by(PromptVersion.id.desc())
                    .first()
                )
        else:
            pv = (
                db.query(PromptVersion)
                .filter(PromptVersion.prompt_id == prompt.id,
                        PromptVersion.version == version)
                .first()
            )

        if pv is None:
            return None

        meta = _REGISTRY.get(name, {})

        # DB-backed variable declarations (prompt_variables) supersede the
        # static registry for this row: an admin's declared variables are what
        # build_prompt enforces. Rows with no declarations keep the registry
        # metadata (matching the file tier).
        from promptops_app.database import PromptVariable

        declared = (
            db.query(PromptVariable)
            .filter(PromptVariable.prompt_id == prompt.id)
            .order_by(PromptVariable.sort_order, PromptVariable.id)
            .all()
        )
        required_vars = (
            [v.name for v in declared] if declared
            else list(meta.get("required_vars", []))
        )

        return PromptTemplate(
            name            = name,
            version         = pv.version,
            description     = prompt.description or "",
            system_template = pv.system_prompt or "",
            user_template   = pv.user_prompt_template or "",
            required_vars   = required_vars,
            optional_vars   = list(meta.get("optional_vars", [])),
            source          = "db",
        )
    except Exception:
        return None


def _split(raw: str) -> tuple[str, str]:
    """Split a template file into (system_template, user_template).

    Looks for ``--- USER ---``.  Everything before (stripped of
    ``--- SYSTEM ---`` marker) is the system prompt; everything after
    is the user prompt.  If the marker is absent the whole file is
    used as the user prompt with an empty system prompt.
    """
    sep = "--- USER ---"
    if sep in raw:
        before, after = raw.split(sep, 1)
        system = before.replace("--- SYSTEM ---", "").strip()
        user   = after.strip()
        return system, user
    return "", raw.strip()
