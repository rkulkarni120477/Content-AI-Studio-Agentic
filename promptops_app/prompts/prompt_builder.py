"""Prompt builder — safe {{variable}} substitution with validation.

Variable syntax
---------------
Templates use ``{{variable_name}}`` (double braces).  This is distinct from
Python f-string ``{var}`` syntax, making templates safe to store in text files
or a database without accidental Python interpolation.

Quick reference
---------------
    from promptops_app.prompts.prompt_builder import render, validate, build_prompt

    # Simple substitution
    text = render("Hello {{name}}!", {"name": "Shubham"})

    # Check before rendering (returns missing var list)
    ok, missing = validate(template_str, variables)

    # Load + render a named template end-to-end
    system, user, tpl_name, tpl_ver = build_prompt(
        "cdd_generation",
        {"course_name": "CTE Nursing", "target_audience": "Grade 9", ...},
    )
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass  # avoid circular imports in type hints

# Matches {{identifier}} — one or more word chars, underscores, digits
_VAR_RE = re.compile(r"\{\{([a-zA-Z_][a-zA-Z0-9_]*)\}\}")


class PromptVariableError(ValueError):
    """A resolved template's DECLARED required variables are not all supplied.

    Deliberately narrower than ``render(strict=True)`` (which would fail on
    every ``{{placeholder}}`` in the body, including deliberately-optional
    ones): only variables an admin declared for the prompt (``prompt_variables``
    rows, or the registry's ``required_vars`` for file-tier templates) are
    enforced. Raised so a misconfigured declaration surfaces loudly instead of
    silently degrading to the constant-fallback tier or emitting literal
    placeholder text.
    """

    def __init__(self, template: str, version: str, missing: list[str]):
        self.template = template
        self.version = version
        self.missing = list(missing)
        super().__init__(
            f"Prompt template '{template}' ({version}) declares required "
            f"variables this call does not supply: {', '.join(sorted(missing))}"
        )


# ── Core primitives ───────────────────────────────────────────────────────────

def extract_variables(template: str) -> list[str]:
    """Return unique variable names found in a template, in appearance order."""
    seen: dict[str, None] = {}
    for m in _VAR_RE.finditer(template):
        seen[m.group(1)] = None
    return list(seen)


def validate(template: str, variables: dict) -> tuple[bool, list[str]]:
    """Return (all_present, missing_variable_names).

    A variable is considered missing when its key is absent from *variables*.
    A ``None`` value is treated as "provided but empty" (not missing).
    """
    required = extract_variables(template)
    missing  = [v for v in required if v not in variables]
    return len(missing) == 0, missing


def render(template: str, variables: dict, *, strict: bool = True) -> str:
    """Replace ``{{variable}}`` placeholders with values from *variables*.

    Parameters
    ----------
    template:
        String containing ``{{variable_name}}`` placeholders.
    variables:
        Mapping of name → value.  Values are coerced to ``str``.
        ``None`` values are treated as empty string in non-strict mode.
    strict:
        When ``True`` (default) raise ``ValueError`` if any placeholder
        has no corresponding key in *variables*.
        When ``False``, leave unresolved placeholders in place (useful for
        partial / incremental rendering).

    Returns
    -------
    str  — rendered template.
    """
    if strict:
        ok, missing = validate(template, variables)
        if not ok:
            raise ValueError(
                f"Missing required template variables: {', '.join(sorted(missing))}"
            )

    def _sub(m: re.Match) -> str:
        key = m.group(1)
        val = variables.get(key)
        if val is None:
            return m.group(0)  # always leave unresolved placeholders in place
        return str(val)

    return _VAR_RE.sub(_sub, template)


def render_safe(template: str, variables: dict) -> str:
    """Non-strict render — leave unknown ``{{variables}}`` in place."""
    return render(template, variables, strict=False)


# ── High-level builder ────────────────────────────────────────────────────────

def build_prompt(
    template_name: str,
    variables: dict,
    *,
    version: str = "latest",
    db=None,
    strict: bool = False,
    project_id=None,
    cluster_id=None,
    course_id=None,
    variant=None,
    require_variant: bool = False,
) -> tuple[str, str, str, str]:
    """Load a named template, optionally validate variables, and render both parts.

    Parameters
    ----------
    template_name:
        Logical template name (matches file stem, e.g. ``"cdd_generation"``).
    variables:
        Dict of ``{{placeholder}} → value`` pairs.  Missing optional vars are
        silently left empty; missing required vars raise ``ValueError`` when
        *strict=True*.
    version:
        ``"latest"`` (default) or a specific version string like ``"v2"``.
    db:
        SQLAlchemy session.  When provided, the DB Prompt/PromptVersion tables
        are consulted first (admin-updatable prompts take precedence over files).
    strict:
        When ``True``, raise ``ValueError`` for any missing variable.
        Default ``False`` — leave placeholders as-is so callers can supply
        partial variable sets.
    project_id / cluster_id / course_id:
        Optional generation context, forwarded to the loader for scope-fixed
        resolution when component-keyed resolution is enabled.
    variant / require_variant:
        Optional pipeline variant (exact match preferred, NULL-variant row as
        fallback, never a different variant); ``require_variant=True`` allows
        only an exact-variant DB row — no fallback tier at all (the caller
        keeps its own bespoke fallback).  See ``load_template``.

    Returns
    -------
    (system_prompt, user_prompt, template_name, resolved_version)
        The rendered strings plus the name and version actually used
        (for storing in Generation.prompt_name / prompt_version).
    """
    from promptops_app.prompts.prompt_loader import load_template  # deferred to break circular

    tmpl = load_template(
        template_name, version=version, db=db,
        project_id=project_id, cluster_id=cluster_id, course_id=course_id,
        variant=variant, require_variant=require_variant,
    )

    # Declared-variable enforcement (Phase 8): required_vars comes from the
    # prompt's prompt_variables declarations (DB tier) or the registry (file
    # tier). Gated behind the resolution flag so flag-off stays byte-identical
    # to the legacy leave-placeholders-in-place behavior. A `None` value counts
    # as supplied (mirrors validate()) — only an absent key is a violation.
    from promptops_app.prompts.prompt_loader import component_resolution_enabled

    if component_resolution_enabled():
        _missing = [v for v in tmpl.required_vars if v not in variables]
        if _missing:
            raise PromptVariableError(tmpl.name, tmpl.version, _missing)

    system = render(tmpl.system_template, variables, strict=strict)
    user   = render(tmpl.user_template,   variables, strict=strict)

    return system, user, tmpl.name, tmpl.version


# ── Context builder helpers ───────────────────────────────────────────────────

def build_context_variables(
    *,
    course_name: str = "",
    grade_level: str = "",
    learning_objectives: str = "",
    style_guidelines: str = "",
    cdd_context: str = "",
    blueprint_context: str = "",
    document_summary: str = "",
    teacher_mode: bool = False,
    student_mode: bool | None = None,  # None → inferred as not teacher_mode
    output_format: str = "lesson",
    **extra,
) -> dict:
    """Convenience factory: build the standard variable dict from named kwargs.

    All parameters are optional with safe defaults so callers can pass only
    what they have without worrying about KeyErrors.
    Extra keyword arguments are merged in verbatim, allowing callers to supply
    template-specific variables (e.g. ``topic=``, ``selected_module=``).
    """
    if student_mode is None:
        student_mode = not teacher_mode
    base = {
        "course_name":          course_name,
        "grade_level":          grade_level,
        "learning_objectives":  learning_objectives,
        "style_guidelines":     style_guidelines,
        "cdd_context":          cdd_context,
        "blueprint_context":    blueprint_context,
        "document_summary":     document_summary,
        "teacher_mode":         "Yes" if teacher_mode else "No",
        "student_mode":         "Yes" if student_mode else "No",
        "output_format":        output_format,
    }
    base.update(extra)
    return base
