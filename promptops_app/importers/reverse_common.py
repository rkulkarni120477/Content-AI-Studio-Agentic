"""Shared helpers for reverse-generation (Blueprint / CDD / Style).

Reverse-gen reads the *already reconstructed* CAS rows (CourseModule + Block,
written in Session 3) rather than the transient ICourse, so it operates on the
same data a user would see in the Editor. See reverse_cas.md §S5.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from promptops_app.database import Block, CourseModule

# Established default model choice in this codebase (see generation_jobs.py).
DEFAULT_IMPORT_MODEL = "GPT-5.6 Terra"

# Per-block content caps keep reverse-gen prompts within a sane token budget.
_BLUEPRINT_BLOCK_CHARS = 1500
_CDD_BLOCK_CHARS = 350


@dataclass
class ModuleContent:
    """A reconstructed module plus its ordered blocks (lessons + assessments)."""
    module: CourseModule
    blocks: list = field(default_factory=list)


def collect_course_modules(db, course_id: int) -> list[ModuleContent]:
    """Load the course's modules (ordered) with their blocks (ordered)."""
    modules = (
        db.query(CourseModule)
        .filter(CourseModule.course_id == course_id)
        .order_by(CourseModule.position, CourseModule.id)
        .all()
    )
    out: list[ModuleContent] = []
    for module in modules:
        blocks = (
            db.query(Block)
            .filter(Block.module_id == module.id)
            .order_by(Block.position, Block.id)
            .all()
        )
        out.append(ModuleContent(module=module, blocks=blocks))
    return out


def render_module_content(mc: ModuleContent, *, per_block_chars: int = _BLUEPRINT_BLOCK_CHARS) -> str:
    """Render one module's blocks as prompt input for reverse_blueprint."""
    parts: list[str] = []
    for i, block in enumerate(mc.blocks, start=1):
        kind = (block.block_type or "lesson").title()
        label = block.block_label or f"Item {i}"
        body = (block.content or "").strip()
        if len(body) > per_block_chars:
            body = body[:per_block_chars].rstrip() + " …[truncated]"
        parts.append(f"### {kind} {i}: {label}\n{body}" if body else f"### {kind} {i}: {label}\n(No content)")
    return "\n\n".join(parts) or "(This module has no content.)"


def render_course_structure(modules: list[ModuleContent], *, per_block_chars: int = _CDD_BLOCK_CHARS) -> str:
    """Render the whole course outline as prompt input for reverse_cdd."""
    lines: list[str] = []
    for mi, mc in enumerate(modules, start=1):
        lines.append(f"Module {mi}: {mc.module.title}")
        for bi, block in enumerate(mc.blocks, start=1):
            kind = block.block_type or "lesson"
            label = block.block_label or f"Item {bi}"
            excerpt = " ".join((block.content or "").split())[:per_block_chars]
            lines.append(f"  - [{kind}] {label}: {excerpt}".rstrip())
    return "\n".join(lines) or "(Empty course.)"
