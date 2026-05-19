"""Prompt loader — file-based templates with DB override support.

Load order (highest priority first)
-------------------------------------
1. **Database** — when a ``db`` session is provided and a matching
   ``Prompt`` / ``PromptVersion`` row exists (admin-updatable at runtime).
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

from dataclasses import dataclass, field
from pathlib import Path

_TEMPLATE_DIR = Path(__file__).parent / "templates"


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
}


# ── Public API ────────────────────────────────────────────────────────────────

def load_template(
    name: str,
    *,
    version: str = "latest",
    db=None,
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
    """
    if db is not None:
        tmpl = _from_db(db, name, version)
        if tmpl is not None:
            return tmpl

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


def _from_db(db, name: str, version: str) -> PromptTemplate | None:
    """Try to load from the Prompt / PromptVersion ORM tables.  Returns None on any miss."""
    try:
        from promptops_app.database import Prompt, PromptVersion

        prompt = db.query(Prompt).filter(Prompt.name == name).first()
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
        return PromptTemplate(
            name            = name,
            version         = pv.version,
            description     = prompt.description or "",
            system_template = pv.system_prompt or "",
            user_template   = pv.user_prompt_template or "",
            required_vars   = list(meta.get("required_vars", [])),
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
