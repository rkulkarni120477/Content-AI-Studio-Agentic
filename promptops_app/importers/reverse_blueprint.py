"""Reverse-generate module blueprints from reconstructed course content.

For each ``CourseModule`` we feed its reconstructed blocks to the
``reverse_blueprint`` prompt, parse the result with the SAME parser the forward
pipeline uses (``cdd_parser.parse_sections_from_text``), and persist a
``ModuleBlueprint`` + active ``BlueprintVersion`` via the existing repository —
so the Blueprint tab, version history, and per-item regenerate all work with
zero editor changes. See reverse_cas.md §S5.

Isolation: this reuses the forward prompt-builder / llm_service / parser /
repository exactly; it adds no branch to any of them and never calls
``run_generation_job``. Per-module failures degrade to warnings (the reverse-gen
stage is non-fatal and retryable — the Editor stays usable).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Callable

from promptops_app.database import Generation, ModuleBlueprint
from promptops_app.importers.reverse_common import ModuleContent, render_module_content
from promptops_app.parsers.cdd_parser import parse_sections_from_text
from promptops_app.prompts.prompt_builder import build_prompt
from promptops_app.repositories.blueprint_repository import create_blueprint_version
from promptops_app.repositories.course_repository import set_active_blueprint
from promptops_app.services import llm_service
from promptops_app.services.usage_service import UsageLogContext

_log = logging.getLogger(__name__)

# progress_cb(done_modules, total_modules)
ProgressCb = Callable[[int, int], None]


@dataclass
class BlueprintBuildResult:
    created: int = 0
    blueprint_ids: list[int] = field(default_factory=list)
    first_blueprint_id: int | None = None
    warnings: list[str] = field(default_factory=list)


def build_blueprints(
    db,
    *,
    course,
    model_choice: str,
    user_name: str,
    modules: list[ModuleContent],
    progress_cb: ProgressCb | None = None,
) -> BlueprintBuildResult:
    """Create one active ModuleBlueprint per module; pin the first as course active."""
    result = BlueprintBuildResult()
    total = len(modules)

    for idx, mc in enumerate(modules, start=1):
        try:
            bp = _build_one(db, course, mc, idx, model_choice, user_name)
            result.created += 1
            result.blueprint_ids.append(bp.id)
            if result.first_blueprint_id is None:
                result.first_blueprint_id = bp.id
        except Exception as exc:  # non-fatal: one module failing must not stop the rest
            db.rollback()
            _log.warning("reverse_blueprint: module %r failed: %s", mc.module.title, exc)
            result.warnings.append(f"Blueprint for module '{mc.module.title}' could not be generated: {exc}")
        if progress_cb is not None:
            progress_cb(idx, total)

    # Course-level active pin — land the user on the first module's blueprint.
    if result.first_blueprint_id is not None:
        set_active_blueprint(db, course.id, result.first_blueprint_id)

    return result


def _build_one(db, course, mc: ModuleContent, module_number: int, model_choice: str, user_name: str) -> ModuleBlueprint:
    variables = {
        "course_name": course.name or "",
        "module_title": mc.module.title or f"Module {module_number}",
        "module_content": render_module_content(mc),
        "extra_instructions": "",
    }
    system_prompt, user_prompt, tpl_name, tpl_version = build_prompt(
        "reverse_blueprint", variables, db=db,
        project_id=course.project_id, course_id=course.id,
    )

    usage_ctx = UsageLogContext(
        user_name=user_name, project_id=course.project_id, course_id=course.id,
        entity_type="reverse_blueprint", entity_id=str(module_number),
        prompt_template=tpl_name, prompt_version=tpl_version,
    )
    llm = llm_service.generate_with_metadata(model_choice, system_prompt, user_prompt, usage_ctx)
    if llm.status == "error":
        raise RuntimeError(llm.text or "LLM error")

    raw_output = llm.text or ""
    sections = parse_sections_from_text(raw_output)

    blueprint = ModuleBlueprint(
        cdd_id=None,                         # linked to the reconstructed CDD in stage 6
        title=f"{mc.module.title} Blueprint",
        module_title=f"Module {module_number}",
        module_number=module_number,
        active_version="v1",
        project_id=course.project_id,
        course_id=course.id,
        created_by=user_name,
    )
    db.add(blueprint)
    db.commit()
    db.refresh(blueprint)

    generation_params = {
        "source": "reverse_import",
        "prompt_name": tpl_name,
        "prompt_version": tpl_version,
        "module_number": module_number,
    }
    create_blueprint_version(
        db,
        blueprint,
        content=raw_output,
        sections_json=json.dumps(sections),
        generation_params_json=json.dumps(generation_params),
        change_summary="Reconstructed from imported content",
        created_by=user_name,
    )

    _link_generations_to_blueprint(db, mc, blueprint.id)
    return blueprint


def _link_generations_to_blueprint(db, mc: ModuleContent, blueprint_id: int) -> None:
    """Point this module's item-generations at its reconstructed blueprint.

    Import writes one Generation per item with ``blueprint_id`` unset (blueprints
    do not exist yet at reconstruction time). Linking them here lets the Editor's
    "Select File (Topic)" dropdown group each item under its module — the same
    ``Generation.blueprint_id`` grouping the scratch flow relies on. The
    generations are resolved through their blocks' ``generation_id``; if none is
    found the module's items simply stay under "Other / Unlinked" (still visible).
    """
    gen_ids = {b.generation_id for b in mc.blocks if getattr(b, "generation_id", None)}
    if not gen_ids:
        return
    db.query(Generation).filter(Generation.id.in_(gen_ids)).update(
        {Generation.blueprint_id: blueprint_id}, synchronize_session=False
    )
    db.commit()
