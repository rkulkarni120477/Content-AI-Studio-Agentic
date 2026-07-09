"""Shared prompt-fragment persistence (consolidation Phase 9).

A fragment is a reusable block of prompt text keyed by a stable code-facing
``fragment_key`` (persona_tone, style_guide, guardrails, context_header,
output_contract_json, output_contract_markdown, regenerate_wrapper).
Fragments version append-only like registry prompts: exactly one active
version per fragment; edits create the next version and move the flag.
"""

from datetime import datetime

from sqlalchemy.orm import Session

from promptops_app.database import PromptFragment, PromptFragmentVersion

# Keys the admin API accepts. The first two are seeded from the legacy
# constants; the rest are reserved slots authored when their content is
# extracted from the stage templates (a content decision, not a code one).
KNOWN_FRAGMENT_KEYS = (
    "persona_tone",
    "style_guide",
    "guardrails",
    "context_header",
    "output_contract_json",
    "output_contract_markdown",
    "regenerate_wrapper",
)


def get_fragment(db: Session, key: str) -> PromptFragment | None:
    return (
        db.query(PromptFragment)
        .filter(PromptFragment.fragment_key == key)
        .one_or_none()
    )


def get_active_fragment_text(db: Session, key: str) -> str | None:
    """The active version's content for *key*, or None when the fragment
    doesn't exist / has no active version (callers fall back to constants)."""
    row = (
        db.query(PromptFragmentVersion)
        .join(PromptFragment,
              PromptFragment.id == PromptFragmentVersion.fragment_id)
        .filter(PromptFragment.fragment_key == key,
                PromptFragmentVersion.is_active.is_(True))
        .one_or_none()
    )
    return row.content if row is not None else None


def list_fragments(db: Session) -> list[PromptFragment]:
    return db.query(PromptFragment).order_by(PromptFragment.fragment_key).all()


def set_fragment_text(
    db: Session,
    key: str,
    content: str,
    *,
    created_by: str,
    change_reason: str = "",
    description: str | None = None,
) -> PromptFragmentVersion:
    """Create-or-bump: append the next version of *key* and activate it.

    Creates the fragment row on first write. Commits.
    """
    fragment = get_fragment(db, key)
    if fragment is None:
        fragment = PromptFragment(
            fragment_key=key,
            description=description,
            created_by=created_by,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(fragment)
        db.flush()
    elif description is not None:
        fragment.description = description

    numbers = [v.version_number for v in fragment.versions]
    next_number = (max(numbers) + 1) if numbers else 1
    for v in fragment.versions:
        v.is_active = False
    version = PromptFragmentVersion(
        fragment_id=fragment.id,
        version=f"v{next_number}",
        version_number=next_number,
        content=content,
        change_reason=change_reason,
        is_active=True,
        created_by=created_by,
        created_at=datetime.utcnow(),
    )
    db.add(version)
    fragment.active_version = version.version
    fragment.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(version)
    return version
