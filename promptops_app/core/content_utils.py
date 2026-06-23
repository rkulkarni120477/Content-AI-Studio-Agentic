"""
Pure-Python content utilities — zero Streamlit dependency.

These functions were extracted from ``core/shared.py`` and ``ui/components.py``
so they can be imported safely by:
  - FastAPI routers          (app/api/v1/routers/)
  - Celery background tasks  (jobs/generation_jobs.py)

Nothing in this file may import Streamlit, st.session_state, or any ui/ module.
"""

from __future__ import annotations

import re
from typing import Optional

from promptops_app.core.config import (
    PROMPTOPS_MAX_CONTEXT_CHARS,
    PROMPTOPS_MAX_SOURCE_CHARS,
    _clip_text,
)


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------

def fill_template(template: str, payload: dict) -> str:
    """Replace {key} placeholders in a prompt template string.

    Extracted from ui/components.py — pure string operation, no Streamlit.
    """
    out = template
    for key, value in payload.items():
        out = out.replace("{" + key + "}", str(value or ""))
    return out


# ---------------------------------------------------------------------------
# Context building for generation prompts
# ---------------------------------------------------------------------------

def make_source_context(filename: str, content: str, max_chars: int = None) -> str:
    """Wrap a source document in a clearly delimited block for prompt injection."""
    max_chars = max_chars or PROMPTOPS_MAX_SOURCE_CHARS
    safe_content = _clip_text(content or "", max_chars)
    return f"\n\n[START SOURCE: {filename}]\n{safe_content}\n[END SOURCE: {filename}]"


def trim_generation_context(context: str) -> str:
    """Apply a global character cap to the combined supplementary context string."""
    return _clip_text(context or "", PROMPTOPS_MAX_CONTEXT_CHARS)


def build_context_injection(
    db,
    cdd_id: Optional[int],
    blueprint_id: Optional[int],
    target_audience: str = "",
) -> tuple[str, str, str]:
    """
    Build the CDD + Blueprint context string injected into every generation prompt.

    Returns
    -------
    (injection_text, cdd_version_label, blueprint_version_label)

    Extracted from core/shared.py — pure SQLAlchemy, no Streamlit.
    """
    from promptops_app.core.llm_client import safe_json_loads
    from promptops_app.database import (
        CourseDesignDocument,
        ModuleBlueprint,
        get_active_blueprint_version,
        get_active_cdd_version,
    )
    from promptops_app.parsers.cdd_parser import extract_cdd_summary
    from promptops_app.parsers.blueprint_parser import extract_blueprint_summary
    from promptops_app.prompt_templates import CONTEXT_INJECTION_TEMPLATE

    cdd_version_label       = "None"
    blueprint_version_label = "None"
    cdd_obj = bp_obj = None

    cdd_ver = get_active_cdd_version(db, cdd_id)       if cdd_id       else None
    bp_ver  = get_active_blueprint_version(db, blueprint_id) if blueprint_id else None

    if cdd_id:
        cdd_obj = db.query(CourseDesignDocument).filter(CourseDesignDocument.id == cdd_id).first()
    if blueprint_id:
        bp_obj  = db.query(ModuleBlueprint).filter(ModuleBlueprint.id == blueprint_id).first()

    if cdd_ver:
        cdd_version_label = f"{cdd_obj.title if cdd_obj else 'CDD'} ({cdd_ver.version})"
    if bp_ver:
        blueprint_version_label = f"{bp_obj.title if bp_obj else 'Blueprint'} ({bp_ver.version})"

    cdd_sections = safe_json_loads(cdd_ver.sections) if cdd_ver and cdd_ver.sections else {}
    bp_sections  = safe_json_loads(bp_ver.sections)  if bp_ver  and bp_ver.sections  else {}

    def _find(sections: dict, keys: list) -> str:
        for k in keys:
            for sk, sv in sections.items():
                if k.lower() in sk.lower():
                    return sv
        return ""

    learning_objectives = _find(bp_sections,  ["Learning Objectives"]) or \
                          _find(cdd_sections, ["Learning Objectives"])
    tone_guidelines     = _find(cdd_sections, ["Tone & Style", "Tone", "Style"])
    key_concepts        = _find(bp_sections,  ["Key Concepts"]) or \
                          _find(cdd_sections, ["Key Concepts", "Terminology"])
    quality_standards   = _find(cdd_sections, ["Quality Standards", "Constraints"])

    cdd_summary       = extract_cdd_summary(cdd_ver)       if cdd_ver else "No CDD linked."
    blueprint_summary = extract_blueprint_summary(bp_ver)   if bp_ver  else "No Blueprint linked."

    injection = CONTEXT_INJECTION_TEMPLATE.format(
        cdd_title          = cdd_obj.title       if cdd_obj else "N/A",
        cdd_version        = cdd_ver.version     if cdd_ver else "N/A",
        cdd_summary        = cdd_summary,
        blueprint_title    = bp_obj.title        if bp_obj  else "N/A",
        blueprint_version  = bp_ver.version      if bp_ver  else "N/A",
        blueprint_summary  = blueprint_summary,
        learning_objectives= learning_objectives or "Refer to CDD/Blueprint documents.",
        tone_guidelines    = tone_guidelines     or "Professional, clear, and engaging.",
        key_concepts       = key_concepts        or "Refer to Blueprint key concepts section.",
        target_audience    = target_audience     or "As defined in CDD.",
        quality_standards  = quality_standards   or "High quality, structured, well-cited content.",
    )
    return injection, cdd_version_label, blueprint_version_label


# ---------------------------------------------------------------------------
# Content block splitting
# ---------------------------------------------------------------------------

def split_into_blocks(output_text: str) -> list[tuple]:
    """
    Split raw LLM output into content blocks on ## headings.

    Returns a list of (label, order, content, sources) tuples.
    Extracted from core/shared.py — pure Python, no Streamlit.
    """
    def _extract_sources(text: str) -> list[str]:
        return list(set(
            m.strip()
            for m in re.findall(r"\[Source:\s*(.*?)\]", text)
            if m.strip()
        ))

    lines     = output_text.splitlines()
    blocks    = []
    label     = "Body"
    order_val = 1
    current: list[str] = []

    for line in lines:
        if line.startswith("## "):
            if current:
                blk_text = "\n".join(current).strip()
                blocks.append((label, order_val, blk_text, _extract_sources(blk_text)))
                order_val += 1
            label   = line.replace("## ", "", 1).strip()
            current = [line]
        else:
            current.append(line)

    if current:
        blk_text = "\n".join(current).strip()
        blocks.append((label, order_val, blk_text, _extract_sources(blk_text)))

    return blocks or [("Body", 1, output_text, _extract_sources(output_text))]


# ---------------------------------------------------------------------------
# Completion status helpers (used by generations router + assessment gate)
# ---------------------------------------------------------------------------

def get_module_completion_status(db, blueprint_id: int) -> dict:
    """
    Check how many lessons in a blueprint have been generated.

    Used to gate module assessment generation — all lessons must be
    complete before the assessment can be generated.

    Returns { total_lessons, generated_lessons, completed, lesson_labels, missing_lesson_labels }
    """
    result = {
        "total_lessons":    0,
        "generated_lessons": 0,
        "completed":        False,
        "lesson_labels":    [],
        "missing_lesson_labels": [],
    }
    try:
        from promptops_app.database import Generation, get_active_blueprint_version
        from promptops_app.parsers.blueprint_parser import parse_blueprint_components

        bp_ver = get_active_blueprint_version(db, blueprint_id)
        if not bp_ver:
            return result

        components    = parse_blueprint_components(bp_ver)
        lesson_comps  = [c for c in components if c["type"] == "lesson"]
        result["total_lessons"] = len(lesson_comps)
        result["lesson_labels"] = [c["label"] for c in lesson_comps]

        gens       = db.query(Generation).filter(Generation.blueprint_id == blueprint_id).all()
        gen_topics = {g.topic.lower() for g in gens}

        def _is_generated(label: str) -> bool:
            ll = label.lower()
            return any(ll in gt or gt in ll for gt in gen_topics)

        result["generated_lessons"] = sum(1 for lc in lesson_comps if _is_generated(lc["label"]))
        result["missing_lesson_labels"] = [lc["label"] for lc in lesson_comps if not _is_generated(lc["label"])]
        result["completed"] = result["generated_lessons"] >= result["total_lessons"] > 0

    except Exception:
        pass
    return result


def get_all_modules_completion(db, cdd_id: int) -> dict:
    """
    Check completion status across all blueprints for a CDD.

    Used to gate course-level assessment generation.

    Returns { total_modules, completed_modules, completed }
    """
    try:
        from promptops_app.database import ModuleBlueprint

        blueprints = (
            db.query(ModuleBlueprint)
            .filter(ModuleBlueprint.cdd_id == cdd_id)
            .order_by(ModuleBlueprint.module_number)
            .all()
        )

        if not blueprints:
            return {"total_modules": 0, "completed_modules": 0, "completed": False}

        completed = sum(
            1 for bp in blueprints
            if get_module_completion_status(db, bp.id).get("completed")
        )

        return {
            "total_modules":    len(blueprints),
            "completed_modules": completed,
            "completed":        completed >= len(blueprints),
        }
    except Exception:
        return {"total_modules": 0, "completed_modules": 0, "completed": False}
