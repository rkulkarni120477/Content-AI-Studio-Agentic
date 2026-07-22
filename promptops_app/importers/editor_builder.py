"""Reconstruct CAS Editor rows (CourseModule / Generation / Block) from an ICourse.

This is the heart of Goal B: an imported IMSCC becomes a **normal** CAS course
whose Editor is fully populated and editable. It writes the *same rows the
scratch pipeline writes* — it just fills them from parsed IMSCC content instead
of from an LLM. Downstream (Editor / Workflow / export) stays origin-agnostic;
there is no ``source_type`` branch anywhere.

Row shape (mirrors ``generation_jobs.py:458-493``):
  * one :class:`CourseModule` per ``IModule`` (``position`` preserved);
  * one :class:`Generation` per item (``prompt_name/version="import"``, ``topic``
    = the item title) — the editor reaches blocks *through* generations and lists
    each generation as one entry in the "Select File (Topic)" dropdown, so every
    imported item shows individually (grouped by module once the reverse-Blueprint
    stage links ``Generation.blueprint_id`` — see ``reverse_blueprint``);
  * one :class:`Block` per item (``workflow_state="draft"``, ``module_id`` set,
    ``content`` = markdown, ``content_html`` = original body HTML for pages).

Every id is set manually (no DB-level FK / cascade in this graph). Items are
persisted one-at-a-time with a single retry, so a single bad item degrades to a
warning instead of failing the whole import.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from promptops_app.core.constants import ChangeSource, canonical_block_type
from promptops_app.database import Block, CourseModule, Generation
from promptops_app.importers import provenance
from promptops_app.importers.internal_model import (
    ASSIGNMENT_KIND,
    DISCUSSION_KIND,
    ICourse,
    IModule,
    IPage,
    IAssessment,
    PAGE_KIND,
    QUIZ_KIND,
)
from promptops_app.repositories.block_repo import save_block_version

_log = logging.getLogger(__name__)

_IMPORT_PROMPT_NAME = "import"
_IMPORT_PROMPT_VERSION = "import"

# Item kind → (Block.block_type, provenance canvas_type).
_KIND_TO_BLOCK_TYPE = {
    PAGE_KIND: "lesson",
    QUIZ_KIND: "quiz",
    ASSIGNMENT_KIND: "assignment",
    DISCUSSION_KIND: "discussion",
}

# progress_cb(done_modules, total_modules, label)
ProgressCb = Callable[[int, int, str], None]


@dataclass
class BuildResult:
    modules_created: int = 0
    blocks_created: int = 0
    warnings: list[str] = field(default_factory=list)


def build(
    db,
    course: ICourse,
    *,
    course_id: int,
    project_id: int,
    import_id: int,
    user_name: str,
    progress_cb: ProgressCb | None = None,
) -> BuildResult:
    """Write CourseModule/Generation/Block rows for ``course``. Returns a summary.

    Carries over any parser warnings already on ``course.warnings`` so the caller
    surfaces one consolidated list.
    """
    result = BuildResult(warnings=list(course.warnings))
    total = len(course.modules)

    for done, module in enumerate(course.modules, start=1):
        try:
            _build_module(db, module, course_id, project_id, import_id, user_name, result)
        except Exception as exc:  # a module-level failure must not kill the import
            db.rollback()
            _log.warning("Import build: module %r failed: %s", module.title, exc)
            result.warnings.append(f"Module '{module.title}' could not be reconstructed: {exc}")
        if progress_cb is not None:
            progress_cb(done, total, f"Reconstructing '{module.title}'")

    return result


def _build_module(
    db,
    module: IModule,
    course_id: int,
    project_id: int,
    import_id: int,
    user_name: str,
    result: BuildResult,
) -> None:
    course_module = CourseModule(
        course_id=course_id,
        title=module.title,
        position=module.position,
    )
    db.add(course_module)
    db.commit()
    db.refresh(course_module)
    result.modules_created += 1

    provenance.record(
        db,
        import_id=import_id,
        canvas_identifier=module.provenance_id,
        canvas_type=provenance.CANVAS_MODULE,
        cas_entity_type=provenance.CAS_MODULE,
        cas_entity_id=course_module.id,
    )
    db.commit()

    if not module.items:
        return

    for position, item in enumerate(module.items):
        try:
            _persist_item_with_retry(
                db, item, course_module.id, project_id, course_id,
                user_name, import_id, position, result,
            )
        except Exception as exc:
            db.rollback()
            _log.warning("Import build: item %r failed: %s", getattr(item, "title", "?"), exc)
            result.warnings.append(f"Skipped item '{getattr(item, 'title', '?')}': {exc}")


def _persist_item_with_retry(
    db,
    item,
    module_id: int,
    project_id: int,
    course_id: int,
    user_name: str,
    import_id: int,
    position: int,
    result: BuildResult,
) -> None:
    last_exc: Exception | None = None
    for attempt in (1, 2):
        try:
            _persist_item(
                db, item, module_id, project_id, course_id,
                user_name, import_id, position, result,
            )
            return
        except Exception as exc:  # noqa: PERF203 - one retry, background job
            db.rollback()
            last_exc = exc
            _log.info("Import build: item %r attempt %d failed: %s", getattr(item, "title", "?"), attempt, exc)
    assert last_exc is not None
    raise last_exc


def _persist_item(
    db,
    item,
    module_id: int,
    project_id: int,
    course_id: int,
    user_name: str,
    import_id: int,
    position: int,
    result: BuildResult,
) -> None:
    block_type, canvas_type, content, content_html = _item_payload(item, result)
    block_type = canonical_block_type(block_type)
    label = item.title or block_type.title()

    # One Generation per item so each imported item is an individually selectable
    # entry in the Editor's "Select File (Topic)" dropdown (matches the scratch
    # flow's per-topic granularity). ``blueprint_id`` is left unset here and linked
    # by the reverse-Blueprint stage — blueprints do not exist yet at this point.
    generation = Generation(
        topic=label,
        prompt_name=_IMPORT_PROMPT_NAME,
        prompt_version=_IMPORT_PROMPT_VERSION,
        block_type=block_type,
        output_text=content or label,
        project_id=project_id,
        course_id=course_id,
        created_by=user_name,
    )
    db.add(generation)
    db.flush()  # assign generation.id without committing

    block = Block(
        generation_id=generation.id,
        block_type=block_type,
        block_label=label,
        content=content,
        content_html=content_html,
        module_id=module_id,
        position=position,
        workflow_state="draft",
        version_num=1,
    )
    db.add(block)
    db.flush()  # assign block.id without committing

    save_block_version(
        db,
        block,
        change_source=ChangeSource.IMPORT,
        change_note="Initial version",
        created_by=user_name or "import",
        commit=False,
    )

    provenance.record(
        db,
        import_id=import_id,
        canvas_identifier=item.provenance_id,
        canvas_type=canvas_type,
        cas_entity_type=provenance.CAS_BLOCK,
        cas_entity_id=block.id,
    )
    db.commit()
    result.blocks_created += 1


def _item_payload(item, result: BuildResult) -> tuple[str, str, str, str | None]:
    """Return ``(block_type, canvas_type, content, content_html)`` for an item."""
    if isinstance(item, IPage):
        block_type = _KIND_TO_BLOCK_TYPE[PAGE_KIND]
        content = item.markdown or ""
        content_html = item.raw_body_html or None
        if not content and not content_html:
            result.warnings.append(f"Page '{item.title}' imported with empty content.")
        return block_type, provenance.CANVAS_PAGE, content, content_html

    if isinstance(item, IAssessment):
        block_type = _KIND_TO_BLOCK_TYPE.get(item.kind, "quiz")
        content = item.body_markdown or ""
        if not content:
            result.warnings.append(f"Assessment '{item.title}' imported with no questions/body.")
        return block_type, provenance.CANVAS_QUIZ, content, None

    # Unknown item type — persist a placeholder lesson so nothing is silently lost.
    result.warnings.append(f"Unknown item type for '{getattr(item, 'title', '?')}'; stored as empty lesson.")
    return "lesson", provenance.CANVAS_PAGE, "", None
