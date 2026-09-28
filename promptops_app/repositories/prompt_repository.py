"""Prompt Repository — Prompt, PromptVersion, and PromptFixing database access."""

from datetime import datetime

from sqlalchemy import func, or_

from promptops_app.database import (
    Prompt,
    PromptFixing,
    PromptTag,
    PromptVersion,
    UserPromptPreference,
)


def tenant_scope_condition(project_id: int | None, is_platform_admin: bool):
    """Boolean SQL condition: does this row belong to the caller's tenant?

    # ponytail: overlaps with app.core.tenant_context.apply_tenant_filter,
    # which predates Prompt.project_id and is built around a string tenant_id
    # column (or a bare int project_id FK) rather than this read/write split
    # (see visible_to_tenant vs writable_by_tenant below — apply_tenant_filter
    # has no write-side equivalent at all). Kept separate rather than forced
    # into that shape; unify if a third model needs this exact read/write
    # distinction and the duplication starts actually costing something.

    True (no restriction) for a platform admin — they may continue seeing
    every tenant's prompts, per the tenant-isolation ticket's own carve-out.
    Otherwise: a prompt with project_id NULL is shared/global and visible to
    everyone; one with project_id set is visible only to that same tenant.
    Callers AND this into their existing query — it never replaces a role/
    visibility check, just adds the tenant boundary on top.
    """
    if is_platform_admin:
        return None
    if project_id is not None:
        return or_(Prompt.project_id.is_(None), Prompt.project_id == project_id)
    return Prompt.project_id.is_(None)


def visible_to_tenant(row_project_id: int | None, project_id: int | None, is_platform_admin: bool) -> bool:
    """Boolean twin of ``tenant_scope_condition`` for a single already-fetched
    row (e.g. a resolved parent/target prompt) instead of a query filter.
    Same rule, one place — see ``tenant_scope_condition`` for the semantics.
    """
    return is_platform_admin or row_project_id is None or row_project_id == project_id


def writable_by_tenant(row_project_id: int | None, project_id: int | None, is_platform_admin: bool) -> bool:
    """Stricter than ``visible_to_tenant`` — the gate for content-mutating
    actions (edit, delete, new version, promote, attachment add/remove).

    A NULL ``project_id`` means "no single tenant owns this, everyone may
    read it" — it does not mean "everyone may edit or delete it". Only a
    platform admin or the row's own tenant may write; a shared/global row is
    otherwise read-only to tenant callers.
    """
    if is_platform_admin:
        return True
    return row_project_id is not None and row_project_id == project_id


# ---------------------------------------------------------------------------
# Tags
#
# ``prompt_tags`` rows are canonical for every kind (Phase 8 hygiene). The
# legacy comma-string ``prompts.tags`` is kept mirrored by set_prompt_tags so
# the Streamlit surfaces and a rollback of this convergence stay coherent;
# readers must prefer the rows.
# ---------------------------------------------------------------------------

def parse_tags(raw: str | None) -> list[str]:
    """Split a legacy comma-string into normalized tags (order kept, deduped)."""
    seen: dict[str, None] = {}
    for part in (raw or "").split(","):
        tag = part.strip()
        if tag:
            seen.setdefault(tag, None)
    return list(seen)


def set_prompt_tags(db, prompt: Prompt, tags: list[str] | str | None) -> None:
    """Replace *prompt*'s tags in both stores. Caller commits.

    Rows for kept tags are reused rather than recreated — a delete + insert of
    the same composite ``(prompt_id, tag)`` PK in one flush would collide.
    """
    normalized = parse_tags(tags) if isinstance(tags, str) or tags is None else parse_tags(",".join(tags))
    keep = set(normalized)
    current = {t.tag for t in prompt.tag_rows}
    prompt.tag_rows = [t for t in prompt.tag_rows if t.tag in keep] + [
        PromptTag(tag=t) for t in normalized if t not in current
    ]
    prompt.tags = ",".join(normalized)


# ---------------------------------------------------------------------------
# Basic lookups
# ---------------------------------------------------------------------------

def list_all_prompts(db, *, project_id: int | None = None, is_platform_admin: bool = False):
    # Soft-deleted rows are archived — never listed (doc §9).
    q = db.query(Prompt).filter(Prompt.deleted_at.is_(None))
    if (cond := tenant_scope_condition(project_id, is_platform_admin)) is not None:
        q = q.filter(cond)
    return q.order_by(Prompt.name.asc()).all()


def get_prompt_by_name(db, name: str):
    return db.query(Prompt).filter(Prompt.name == name).first()


def count_prompts(db, *, date_from=None, date_to=None) -> int:
    q = db.query(Prompt)
    if date_from is not None:
        q = q.filter(Prompt.created_at >= date_from)
    if date_to is not None:
        q = q.filter(Prompt.created_at < date_to)
    return q.count()


# ---------------------------------------------------------------------------
# Component-scoped queries
# ---------------------------------------------------------------------------

def get_default_prompt(
    db,
    component_type: str,
    variant: str | None = None,
    *,
    variant_fallback: bool = True,
) -> Prompt | None:
    """Return the is_default=True asset for *(component_type, variant)*, or None.

    Pipeline rows only — library rows can never resolve for generation, even
    if one were mislabeled with a component_type/is_default.

    Variant semantics (the partial unique index guarantees at most one default
    per exact ``(component_type, variant)`` pair):
    - ``variant=None`` — the NULL-variant default only. A variant-specific
      default can never resolve for a variant-less request (no sideways match).
    - ``variant="x"`` — the exact ``(component_type, "x")`` default first; when
      *variant_fallback* (default), fall back to the NULL-variant default —
      never to a different variant.
    - ``variant_fallback=False`` — exact variant only; callers with their own
      bespoke fallback (Generate's interactive path) use this so a requested
      variant can never silently resolve another variant's template.
    """
    base = db.query(Prompt).filter(
        Prompt.component_type == component_type,
        Prompt.is_default == True,  # noqa: E712
        Prompt.prompt_kind == "pipeline",
        # Archived/soft-deleted rows never resolve (doc §9). No write path
        # soft-deletes pipeline rows today — this is the forward guard.
        Prompt.deleted_at.is_(None),
    )
    if variant is not None:
        row = base.filter(Prompt.variant == variant).first()
        if row is not None or not variant_fallback:
            return row
    return base.filter(Prompt.variant.is_(None)).first()


def list_prompts_by_component(
    db, component_type: str, *, project_id: int | None = None, is_platform_admin: bool = False,
) -> list[Prompt]:
    """Return all live prompts whose component_type matches, default first then alpha.

    Soft-deleted rows are excluded — this feeds CAS selection dropdowns
    (doc §9: archived prompts never appear in selection).
    """
    q = db.query(Prompt).filter(Prompt.component_type == component_type,
                                Prompt.deleted_at.is_(None))
    if (cond := tenant_scope_condition(project_id, is_platform_admin)) is not None:
        q = q.filter(cond)
    return q.order_by(Prompt.is_default.desc(), Prompt.name.asc()).all()


def list_prompts_tagged(db, tag: str) -> list[Prompt]:
    """Return prompts carrying *tag* (case-insensitive substring match).

    Matches canonical ``prompt_tags`` rows first, with the legacy comma-string
    as a fallback so databases that predate the tags backfill migration keep
    resolving (the Generate pickers call this live).
    """
    pattern = f"%{tag}%"
    tagged = (
        db.query(PromptTag.prompt_id)
        .filter(PromptTag.tag.ilike(pattern))
        .scalar_subquery()
    )
    return (
        db.query(Prompt)
        .filter(or_(Prompt.id.in_(tagged), Prompt.tags.ilike(pattern)),
                Prompt.deleted_at.is_(None))
        .order_by(Prompt.name.asc())
        .all()
    )


# ---------------------------------------------------------------------------
# Version management
# ---------------------------------------------------------------------------

def list_versions_for_prompt(db, prompt_id: int):
    return (
        db.query(PromptVersion)
        .filter(PromptVersion.prompt_id == prompt_id)
        .order_by(PromptVersion.created_at.asc())
        .all()
    )


def get_active_version(db, prompt_id: int) -> PromptVersion | None:
    """Return the currently-active PromptVersion for *prompt_id*."""
    versions = list_versions_for_prompt(db, prompt_id)
    active = [v for v in versions if v.is_active]
    return active[-1] if active else (versions[-1] if versions else None)


def get_prompt_version(db, prompt_id: int, version: str):
    return (
        db.query(PromptVersion)
        .filter(
            PromptVersion.prompt_id == prompt_id,
            PromptVersion.version == version,
        )
        .first()
    )


def _next_version_number(db, prompt_id: int) -> int:
    """Next sequential version_number for a prompt (numbers are DB-queried,
    never taken from possibly-stale relationship collections)."""
    current = (
        db.query(func.max(PromptVersion.version_number))
        .filter(PromptVersion.prompt_id == prompt_id)
        .scalar()
    )
    return (current or 0) + 1


def deploy_new_version(
    db,
    prompt: Prompt,
    system_prompt: str,
    user_prompt_template: str,
    version_tag: str,
    change_reason: str,
    created_by: str,
) -> PromptVersion:
    """Deactivate all existing versions, insert a new active one, and bump updated_at.

    Instant-deploy — the approval gate (Phase 8) decides at the API layer who
    may call this; retired versions are demoted from 'active' to 'approved' so
    workflow_state stays truthful.
    """
    db.query(PromptVersion).filter(
        PromptVersion.prompt_id == prompt.id
    ).update({PromptVersion.is_active: False})
    db.query(PromptVersion).filter(
        PromptVersion.prompt_id == prompt.id,
        PromptVersion.workflow_state == "active",
    ).update({PromptVersion.workflow_state: "approved"})
    new_ver = PromptVersion(
        prompt_id=prompt.id,
        version=version_tag,
        version_number=_next_version_number(db, prompt.id),
        workflow_state="active",
        system_prompt=system_prompt,
        user_prompt_template=user_prompt_template,
        change_reason=change_reason,
        is_active=True,
        created_by=created_by,
    )
    db.add(new_ver)
    prompt.active_version = version_tag
    prompt.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(new_ver)
    return new_ver


def commit_draft_version(
    db,
    prompt: Prompt,
    system_prompt: str,
    user_prompt_template: str,
    version_tag: str,
    change_reason: str,
    created_by: str,
) -> PromptVersion:
    """Insert a NEW version at workflow_state='draft', NOT active.

    The approval-gate write path (Phase 8): the currently-deployed version is
    untouched; activation happens later via the workflow-state transition
    (draft → in_review → approved → active).
    """
    new_ver = PromptVersion(
        prompt_id=prompt.id,
        version=version_tag,
        version_number=_next_version_number(db, prompt.id),
        workflow_state="draft",
        system_prompt=system_prompt,
        user_prompt_template=user_prompt_template,
        change_reason=change_reason,
        is_active=False,
        created_by=created_by,
    )
    db.add(new_ver)
    prompt.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(new_ver)
    return new_ver


def list_recent_versions(db, limit: int = 20):
    return (
        db.query(PromptVersion)
        .order_by(PromptVersion.created_at.desc())
        .limit(limit)
        .all()
    )


# ---------------------------------------------------------------------------
# Prompt-fixing — scope-level default locking
# ---------------------------------------------------------------------------

def resolve_fixed_prompt(
    db,
    component: str,
    *,
    project_id=None,
    cluster_id=None,
    course_id=None,
    acceptable_variants: "tuple | list | None" = None,
) -> "PromptFixing | None":
    """Return the most-specific PromptFixing for component + context, or None.

    Resolution order (most specific wins):
        course → cluster → project → global

    Each scope is checked only when the corresponding ID is provided.
    Crucially, each scope is matched on its OWN ID only — not on parent IDs.
    A course-scope fix is found by ``course_id`` alone, regardless of which
    project or cluster the course belongs to.  This keeps lookup correct
    even when the caller only knows some of the hierarchy IDs (e.g. cluster_id=None).

    Only locks that can actually be honoured are returned: the bound prompt
    must still exist and be live. A lock whose prompt was archived (or whose
    ``prompt_id`` was nulled by the FK's ON DELETE SET NULL) is skipped and the
    next, broader scope is consulted — matching what the loader does when it
    re-checks the row it was handed, so a dead course lock can no longer mask
    a live cluster lock.

    acceptable_variants:
        ``None`` (default) — legacy behavior: no variant/kind filtering.
        Otherwise a sequence of acceptable ``Prompt.variant`` values (``None``
        meaning the NULL variant); a fixing whose bound prompt is not a
        pipeline row with an acceptable variant is skipped, and the next
        (broader) scope is consulted instead — a scope lock for one variant
        never hijacks a request for another.
    """
    # Build a prioritised list of (scope_level, id_column, id_value).
    # Skip a scope when its key ID is None — no fix could have been set that way.
    checks: list[tuple[str, object, object]] = []
    if course_id is not None:
        checks.append(("course",   PromptFixing.course_id,   course_id))
    if cluster_id is not None:
        checks.append(("cluster",  PromptFixing.cluster_id,  cluster_id))
    if project_id is not None:
        checks.append(("project",  PromptFixing.project_id,  project_id))
    checks.append(("global", None, None))   # always check global as final fallback

    for scope, id_col, id_val in checks:
        # Inner join on the bound row: a lock only wins when its prompt exists
        # and is live, so NULL/archived targets fall through to broader scopes
        # instead of short-circuiting resolution to "no lock at all".
        q = (
            db.query(PromptFixing)
            .join(Prompt, PromptFixing.prompt_id == Prompt.id)
            .filter(
                PromptFixing.component   == component,
                PromptFixing.scope_level == scope,
                Prompt.deleted_at.is_(None),
            )
        )
        if id_col is not None:
            q = q.filter(id_col == id_val)
        if acceptable_variants is not None:
            variant_conds = [
                Prompt.variant.is_(None) if v is None else Prompt.variant == v
                for v in acceptable_variants
            ]
            q = q.filter(
                Prompt.prompt_kind == "pipeline",
                or_(*variant_conds) if variant_conds else False,
            )
        fixing = q.first()
        if fixing is not None:
            return fixing
    return None


# ---------------------------------------------------------------------------
# User prompt preferences — Priority 4 persistence
# ---------------------------------------------------------------------------

def save_user_prompt_preference(
    db,
    user_name: str,
    component: str,
    course_id,
    prompt_id: int,
    project_id=None,
) -> None:
    """Persist a user's last-chosen prompt for (component, course_id).

    Uses an upsert so the table stays to one row per (user, component, course).
    Never raises — caller should swallow any exception so a save failure never
    blocks generation.
    """
    existing = (
        db.query(UserPromptPreference)
        .filter(
            UserPromptPreference.user_name == user_name,
            UserPromptPreference.component == component,
            UserPromptPreference.course_id == course_id,
        )
        .first()
    )
    if existing:
        existing.prompt_id  = prompt_id
        existing.project_id = project_id
        existing.updated_at = datetime.utcnow()
    else:
        db.add(UserPromptPreference(
            user_name=user_name, component=component,
            course_id=course_id, project_id=project_id,
            prompt_id=prompt_id,
        ))
    db.commit()


def get_user_prompt_preference(
    db,
    user_name: str,
    component: str,
    course_id,
) -> "Prompt | None":
    """Return the Prompt the user last selected for (component, course_id), or None."""
    pref = (
        db.query(UserPromptPreference)
        .filter(
            UserPromptPreference.user_name == user_name,
            UserPromptPreference.component == component,
            UserPromptPreference.course_id == course_id,
        )
        .first()
    )
    return (pref.prompt if pref and pref.prompt_id else None)


def set_fixed_prompt(
    db,
    *,
    component: str,
    scope_level: str,
    project_id=None,
    cluster_id=None,
    course_id=None,
    prompt_id: int,
    fixed_by: str,
    fixed_by_role: str,
) -> "PromptFixing":
    """Upsert a prompt fixing for the given scope. Returns the PromptFixing record."""
    existing = (
        db.query(PromptFixing)
        .filter(
            PromptFixing.component   == component,
            PromptFixing.scope_level == scope_level,
            PromptFixing.project_id  == project_id,
            PromptFixing.cluster_id  == cluster_id,
            PromptFixing.course_id   == course_id,
        )
        .first()
    )
    if existing:
        existing.prompt_id     = prompt_id
        existing.fixed_by      = fixed_by
        existing.fixed_by_role = fixed_by_role
        existing.fixed_at      = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        return existing

    fixing = PromptFixing(
        component=component, scope_level=scope_level,
        project_id=project_id, cluster_id=cluster_id, course_id=course_id,
        prompt_id=prompt_id, fixed_by=fixed_by, fixed_by_role=fixed_by_role,
    )
    db.add(fixing)
    db.commit()
    db.refresh(fixing)
    return fixing


def unset_fixed_prompt(
    db,
    *,
    component: str,
    scope_level: str,
    project_id=None,
    cluster_id=None,
    course_id=None,
) -> bool:
    """Remove a prompt fixing for the given scope. Returns True if a row was deleted."""
    rows = (
        db.query(PromptFixing)
        .filter(
            PromptFixing.component   == component,
            PromptFixing.scope_level == scope_level,
            PromptFixing.project_id  == project_id,
            PromptFixing.cluster_id  == cluster_id,
            PromptFixing.course_id   == course_id,
        )
        .delete()
    )
    db.commit()
    return rows > 0
