"""Reverse-generate a Course Design Document from reconstructed course content.

Aggregates the whole course outline (modules → lessons → assessments) and feeds
it to the ``reverse_cdd`` prompt, then parses + persists exactly like the forward
CDD pipeline: ``parse_sections_from_text`` merged with the non-underscore
``parse_cdd_flat`` blocks → ``create_cdd_version(is_active=True)`` →
``set_active_cdd``. The CDD tab, versions, and regenerate then work unchanged.

Runs AFTER blueprints (reverse order: content → module structure → course
design). Non-fatal: any failure leaves the Editor + blueprints usable and is
retryable. See reverse_cas.md §S5.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from promptops_app.database import CourseDesignDocument
from promptops_app.importers.reverse_common import ModuleContent, render_course_structure
from promptops_app.parsers.cdd_parser import parse_cdd_flat, parse_sections_from_text
from promptops_app.prompts.prompt_builder import build_prompt
from promptops_app.repositories.cdd_repository import create_cdd_version
from promptops_app.repositories.course_repository import set_active_cdd
from promptops_app.services import llm_service

_log = logging.getLogger(__name__)


@dataclass
class CddBuildResult:
    cdd_id: int | None = None
    warnings: list[str] = field(default_factory=list)


def build_cdd(
    db,
    *,
    course,
    model_choice: str,
    user_name: str,
    modules: list[ModuleContent],
) -> CddBuildResult:
    """Create the course's CDD (parent + active version) and pin it. Non-fatal."""
    result = CddBuildResult()
    if not modules:
        return result

    try:
        variables = {
            "course_name": course.name or "",
            "target_audience": course.config_target_audience or "",
            "expert_domain": course.config_expert_domain or "",
            "course_content": render_course_structure(modules),
            "extra_instructions": "",
        }
        system_prompt, user_prompt, tpl_name, tpl_version = build_prompt(
            "reverse_cdd", variables, db=db,
            project_id=course.project_id, course_id=course.id,
        )

        llm = llm_service.generate_with_metadata(model_choice, system_prompt, user_prompt)
        if llm.status == "error":
            result.warnings.append(f"Course design (CDD) reconstruction skipped: {llm.text}")
            return result

        raw_output = llm.text or ""
        sections = parse_sections_from_text(raw_output)
        # Merge the flat CDD blocks (Course Details / Structure / Assessment),
        # mirroring the forward generate path exactly.
        for key, value in parse_cdd_flat(raw_output).items():
            if not key.startswith("_") and (value or "").strip():
                sections[key] = value

        cdd = CourseDesignDocument(
            title=f"{course.name} — CDD" if course.name else "Imported Course — CDD",
            course_title=course.name or "",
            description="",
            active_version="v1",
            workflow_state="draft",
            created_by=user_name,
            project_id=course.project_id,
            course_id=course.id,
        )
        db.add(cdd)
        db.commit()
        db.refresh(cdd)

        generation_params = {
            "source": "reverse_import",
            "prompt_name": tpl_name,
            "prompt_version": tpl_version,
        }
        create_cdd_version(
            db,
            cdd,
            content=raw_output,
            sections_json=json.dumps(sections),
            generation_params_json=json.dumps(generation_params),
            change_summary="Reconstructed from imported content",
            created_by=user_name,
        )
        set_active_cdd(db, course.id, cdd.id)
        result.cdd_id = cdd.id

    except Exception as exc:  # non-fatal
        db.rollback()
        _log.warning("reverse_cdd: build failed for course %s: %s", getattr(course, "id", "?"), exc)
        result.warnings.append(f"Course design (CDD) reconstruction failed: {exc}")

    return result
