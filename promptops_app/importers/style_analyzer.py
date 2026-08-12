"""Reverse-detect a reusable Style from reconstructed lessons.

Samples a handful of reconstructed lesson blocks, feeds them to the
``style_analysis`` prompt, and persists a ``Style`` + active ``StyleVersion``
via the existing helpers — then pins ``courses.active_style_id`` at course
scope. The Style tab, version history, and reuse across other courses then work
unchanged (a Style is course-independent once created). See reverse_cas.md §S6.

Reuses the forward ``create_style`` / ``create_style_version`` /
``set_active_style`` exactly; adds no branch and never calls
``run_generation_job``. Non-fatal: any failure degrades to a warning.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from promptops_app.database import create_style as db_create_style, set_active_style
from promptops_app.importers.reverse_common import ModuleContent
from promptops_app.prompts.prompt_builder import build_prompt
from promptops_app.repositories.style_repository import create_style_version
from promptops_app.services import llm_service
from promptops_app.services.usage_service import UsageLogContext

_log = logging.getLogger(__name__)

_MAX_SAMPLE_LESSONS = 4
_MAX_SAMPLE_CHARS = 2000


@dataclass
class StyleBuildResult:
    style_id: int | None = None
    warnings: list[str] = field(default_factory=list)


def build_style(
    db,
    *,
    course,
    model_choice: str,
    user_name: str,
    modules: list[ModuleContent],
) -> StyleBuildResult:
    """Detect + pin a reusable Style from sampled lessons. Non-fatal."""
    result = StyleBuildResult()

    samples = _sample_lessons(modules)
    if not samples.strip():
        result.warnings.append("No lesson content available to analyze for style.")
        return result

    try:
        variables = {
            "course_name": course.name or "",
            "lesson_samples": samples,
            "extra_instructions": "",
        }
        system_prompt, user_prompt, tpl_name, tpl_version = build_prompt(
            "style_analysis", variables, db=db,
            project_id=course.project_id, course_id=course.id,
        )

        usage_ctx = UsageLogContext(
            user_name=user_name, project_id=course.project_id, course_id=course.id,
            entity_type="reverse_style", entity_id=str(course.id),
            prompt_template=tpl_name, prompt_version=tpl_version,
        )
        llm = llm_service.generate_with_metadata(model_choice, system_prompt, user_prompt, usage_ctx)
        if llm.status == "error":
            result.warnings.append(f"Style detection skipped: {llm.text}")
            return result

        understanding = llm.text or ""

        style = db_create_style(
            db,
            f"{course.name} — Imported Style" if course.name else "Imported Style",
            "Detected automatically from imported course content.",
            "",           # no custom instructions
            [],           # no reference documents (style is inferred from lessons)
            user_name,
        )
        create_style_version(
            db, style,
            understanding_content=understanding,
            change_summary="Reconstructed from imported content",
            created_by=user_name,
        )
        set_active_style(db, style.id, scope="course", course_id=course.id)
        result.style_id = style.id

    except Exception as exc:  # non-fatal
        db.rollback()
        _log.warning("style_analyzer: build failed for course %s: %s", getattr(course, "id", "?"), exc)
        result.warnings.append(f"Style detection failed: {exc}")

    return result


def _sample_lessons(
    modules: list[ModuleContent],
    *,
    max_lessons: int = _MAX_SAMPLE_LESSONS,
    max_chars: int = _MAX_SAMPLE_CHARS,
) -> str:
    """Concatenate up to ``max_lessons`` lesson bodies as style-analysis input."""
    parts: list[str] = []
    for mc in modules:
        for block in mc.blocks:
            if (block.block_type or "").lower() != "lesson":
                continue
            body = (block.content or "").strip()
            if not body:
                continue
            if len(body) > max_chars:
                body = body[:max_chars].rstrip() + " …[truncated]"
            label = block.block_label or "Lesson"
            parts.append(f"### {label}\n{body}")
            if len(parts) >= max_lessons:
                return "\n\n".join(parts)
    return "\n\n".join(parts)
