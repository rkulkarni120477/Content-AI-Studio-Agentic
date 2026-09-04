"""Persist a normalized Outline import as a blueprint.

Extracted from the ``/blueprints/import`` route so the SAME persistence runs on
both paths:
  * the synchronous route (small files), and
  * the async Celery worker (``outline_import_jobs.run_outline_import_job``),
    which moves the slow LLM restructure off the request thread so a reverse
    proxy can't 504 it.

Behaviour is byte-for-byte what the sync route did before: find an existing
Outline for this (kind, unit) and add a new version, else create a fresh one at
v1; best-effort DIS upsert; pin active; audit. Returns the blueprint, its new
version row, and whether a new document was created — the caller builds the
HTTP response (sync) or records the result on the job (async).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Tuple

from promptops_app.database import BlueprintVersion, ModuleBlueprint
from promptops_app.repositories import blueprint_repository
from promptops_app.repositories.course_repository import get_course_by_id, set_active_blueprint
from promptops_app.services.audit_service import log_audit_event

_log = logging.getLogger(__name__)


def _unit_of(title: str, module_number):
    """The (kind, number) an existing blueprint covers. A day Outline is titled
    "Day N: …"; a module Outline "Module N: …". An older module blueprint titled
    "<topic> Blueprint" (number only in module_number) is treated as that module —
    but a day Outline (which also carries module_number=N) is classified 'day' by
    its title first, so the two never collide."""
    m = re.match(r"(?i)^\s*(day|module)\s+(\d+)\b", title or "")
    if m:
        return m.group(1).lower(), int(m.group(2))
    if module_number is not None:
        return "module", int(module_number)
    return None, None


def persist_imported_outline(db, result, *, course_id: int, project_id: int,
                             cdd_id, source_filename: str, current_user) -> Tuple[ModuleBlueprint, BlueprintVersion, bool]:
    """Create/version + pin an imported Outline. Returns (blueprint, version, was_new).

    ``result`` is an ``OutlineImportResult`` from ``normalize_import``. ``current_user``
    only needs ``.username`` (used for created_by, audit, and DIS attribution).
    """
    kind = result.kind                # "day" | "module"
    unit = result.unit_number         # guaranteed resolved by normalize_import
    word = "Day" if kind == "day" else "Module"
    username = current_user.username

    generation_params = {
        "prompt_source": "imported",
        "import_method": result.method,
        "source_filename": source_filename,
        "is_dlu": result.is_dlu,
        "import_warnings": result.warnings,
        "outline_kind": kind,
        "unit_number": unit,
        "topic": result.topic,
        "cdd_id": cdd_id,
    }
    change_reason = f"Imported from {source_filename}"

    # Find a live Outline already covering this unit in this course, so a re-upload
    # adds a new version instead of a duplicate. Matching is scoped to the same kind
    # (a day file never versions over a same-numbered module Outline). Prefer the
    # course's pinned Outline when it is a match, else the most recent (newest-first).
    course = get_course_by_id(db, course_id)
    pinned_id = course.active_blueprint_id if course else None
    candidates = [
        bp for bp in blueprint_repository.list_blueprints_for_course(
            db, course_id=course_id, project_id=project_id, limit=10000)
        if _unit_of(bp.title, bp.module_number) == (kind, unit)
    ]
    target = next((bp for bp in candidates if bp.id == pinned_id), None) or (candidates[0] if candidates else None)

    if target is not None:
        version_record = blueprint_repository.create_blueprint_version(
            db, target,
            content=result.raw_output,
            sections_json=json.dumps(result.sections),
            generation_params_json=json.dumps(generation_params),
            change_summary=change_reason,
            created_by=username,
        )
        bp = target
        bp_title = bp.title
        was_new = False
    else:
        bp_title = result.derived_title
        bp = ModuleBlueprint(
            cdd_id=cdd_id,
            title=bp_title,
            module_title=f"{word} {unit}",
            module_number=unit,
            active_version="v1",
            project_id=project_id,
            course_id=course_id,
            created_by=username,
        )
        db.add(bp)
        db.commit()
        db.refresh(bp)
        version_record = BlueprintVersion(
            blueprint_id=bp.id,
            version="v1",
            full_content=result.raw_output,
            sections=json.dumps(result.sections),
            generation_params=json.dumps(generation_params),
            change_reason=change_reason,
            is_active=True,
            created_by=username,
        )
        db.add(version_record)
        db.commit()
        db.refresh(version_record)
        was_new = True

    _log.info(
        "outline_import_persist  user=%s  course=%s  file=%r  method=%s  kind=%s  "
        "unit=%s  dlu=%s  bp_id=%d  new=%s  version=%s",
        username, course_id, source_filename, result.method, kind, unit,
        result.is_dlu, bp.id, was_new, version_record.version,
    )

    # Copy the imported Outline to DIS/S3 for retrieval and listing — best-effort,
    # never blocks the import. Lazy import so this module doesn't pull app.core at
    # import time in worker contexts.
    try:
        from app.core.dis_client import dis_client
        dis_client.generated_upsert_sync({
            "generated_doc_id": f"blueprint_{bp.id}",
            "generated_type": "blueprint",
            "title": bp_title,
            "content": result.raw_output,
            "summary": result.raw_output[:500],
            "active": True,
            "metadata": {
                "selected_module": f"{word} {unit}",
                "module_number": unit,
                "course_id": course_id,
                "project_id": project_id,
                "cdd_id": cdd_id,
                "prompt_source": "imported",
            },
            "source_documents_used": [],
            "cas_ref": {"entity": "blueprint", "id": bp.id},
            "created_by": username,
        }, current_user=current_user)
    except Exception as exc:
        _log.warning("dis_imported_outline_upsert_failed blueprint_id=%s error=%s", bp.id, exc)

    set_active_blueprint(db, course_id, bp.id)

    log_audit_event(db, username, "blueprint.created",
                    entity_type="blueprint", entity_id=bp.id,
                    project_id=project_id, course_id=course_id,
                    metadata={
                        "title": bp_title,
                        "prompt_source": "imported",
                        "import_method": result.method,
                        "source_filename": source_filename,
                        "outline_kind": kind,
                        "unit_number": unit,
                        "new_document": was_new,
                        "version": version_record.version,
                        "output": result.raw_output,
                    })

    return bp, version_record, was_new
