"""Resolve what a CE review judges content against.

Order (decided with the product owner):
  1. The project's active uploaded checklist (``ReviewChecklist``), if any.
  2. Otherwise, the project's active Style *writing rules* (custom instructions
     / generated summary) — the same fallback the legacy CE validation uses.
  3. Otherwise, nothing — the caller blocks the run with a clear message.

Returns a small immutable result carrying the basis kind, a stable version
string (folded into the content fingerprint so a rules change invalidates a
skip-unchanged match), and the source ids/text later passes need.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ReviewBasis:
    basis: str                       # "checklist" | "style" | "none"
    version: str                     # stable token, e.g. "cl:3:v2" or "style:7:v4"
    label: str                       # human-readable, for the UI
    checklist_id: Optional[int] = None
    style_id: Optional[int] = None
    rules_text: Optional[str] = None  # style writing rules (checklist rules fetched separately)

    @property
    def is_reviewable(self) -> bool:
        return self.basis in ("checklist", "style")


def _style_writing_rules(style) -> Optional[str]:
    """Return the active style's writing rules text, or None."""
    if not style:
        return None
    parts = []
    ci = getattr(style, "custom_instructions", None)
    gs = getattr(style, "generated_summary", None)
    if ci and ci.strip():
        parts.append(f"WRITING INSTRUCTIONS:\n{ci.strip()}")
    if gs and gs.strip():
        parts.append(f"STYLE GUIDE:\n{gs.strip()}")
    return "\n\n".join(parts) if parts else None


def resolve_basis(db, *, project_id: Optional[int], course_id: Optional[int]) -> ReviewBasis:
    """Resolve the review basis for a project/course scope."""
    from promptops_app.database import ReviewChecklist, get_active_style

    # 1. Active uploaded checklist for this project.
    if project_id is not None:
        checklist = (
            db.query(ReviewChecklist)
            .filter(
                ReviewChecklist.project_id == project_id,
                ReviewChecklist.status == "active",
            )
            .order_by(ReviewChecklist.version.desc())
            .first()
        )
        if checklist is not None:
            return ReviewBasis(
                basis="checklist",
                version=f"cl:{checklist.id}:v{checklist.version}",
                label=f"{checklist.name} (v{checklist.version})",
                checklist_id=checklist.id,
            )

    # 2. Fall back to the active Style's writing rules.
    style = get_active_style(db, project_id=project_id, course_id=course_id)
    rules = _style_writing_rules(style)
    if style is not None and rules:
        from promptops_app.repositories.style_repository import get_active_style_version
        ver = get_active_style_version(db, style.id)
        ver_num = getattr(ver, "version_number", None) or 1
        return ReviewBasis(
            basis="style",
            version=f"style:{style.id}:v{ver_num}",
            label=f"Style rules — {style.name}",
            style_id=style.id,
            rules_text=rules,
        )

    # 3. Nothing to review against.
    return ReviewBasis(basis="none", version="none", label="No checklist or style rules")
