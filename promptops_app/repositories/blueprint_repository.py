"""Blueprint Repository — ModuleBlueprint and BlueprintVersion database access."""

from promptops_app.database import ModuleBlueprint, BlueprintVersion


def _live_only(q, *, include_archived: bool):
    """Exclude archived blueprints unless the caller explicitly asks for them.

    Applied in SQL rather than filtered in the caller so that pagination counts,
    limits and every consumer of these helpers agree on what "the list" means.
    Archived rows are still reachable by id — see ``get_blueprint_by_id`` —
    because restoring and inspecting one has to work.
    """
    if include_archived:
        return q
    return q.filter(ModuleBlueprint.deleted_at.is_(None))


def list_blueprints_for_course(db, course_id: int = None, project_id: int = None, limit: int = 100,
                               *, include_archived: bool = False):
    q = _live_only(db.query(ModuleBlueprint), include_archived=include_archived)
    if course_id:
        q = q.filter(ModuleBlueprint.course_id == course_id)
    if project_id:
        q = q.filter(ModuleBlueprint.project_id == project_id)
    return q.order_by(ModuleBlueprint.created_at.desc()).limit(limit).all()


def list_all_blueprints(db, limit: int = 200, *, include_archived: bool = False):
    return (
        _live_only(db.query(ModuleBlueprint), include_archived=include_archived)
        .order_by(ModuleBlueprint.created_at.desc())
        .limit(limit)
        .all()
    )


def get_blueprint_by_id(db, bp_id: int):
    """Fetch by id, archived or not.

    Deliberately unfiltered: an archived blueprint still exists, and restoring
    it, inspecting it or refusing to pin it all need the row. Callers that must
    not act on an archive use ``design_doc_archive.assert_live``.
    """
    return db.query(ModuleBlueprint).filter(ModuleBlueprint.id == bp_id).first()


def list_blueprint_versions(db, bp_id: int):
    return (
        db.query(BlueprintVersion)
        .filter(BlueprintVersion.blueprint_id == bp_id)
        .order_by(BlueprintVersion.created_at.asc())
        .all()
    )


def get_blueprint_version(db, bp_id: int, version: str):
    return (
        db.query(BlueprintVersion)
        .filter(BlueprintVersion.blueprint_id == bp_id, BlueprintVersion.version == version)
        .first()
    )


def get_existing_module_blueprint(db, cdd_id: int, module_number: int):
    """The live blueprint for this CDD's module, if there is one.

    Archived blueprints are treated as absent so a regeneration builds a fresh
    one rather than writing new versions onto a document someone deliberately
    took out of circulation.
    """
    return (
        _live_only(db.query(ModuleBlueprint), include_archived=False)
        .filter(
            ModuleBlueprint.cdd_id == cdd_id,
            ModuleBlueprint.module_number == module_number,
        )
        .first()
    )


def get_existing_module_numbers(db, cdd_id: int) -> set:
    """Module numbers this CDD already has a *live* blueprint for."""
    rows = (
        _live_only(db.query(ModuleBlueprint.module_number), include_archived=False)
        .filter(ModuleBlueprint.cdd_id == cdd_id)
        .all()
    )
    return {r[0] for r in rows}


def create_blueprint_version(
    db,
    blueprint,
    content: str,
    sections_json: str,
    generation_params_json: str,
    change_summary: str,
    created_by: str,
    *,
    parent_version_id: int = None,
) -> "BlueprintVersion":
    """Create a new BlueprintVersion, deactivate others, set blueprint.active_version."""
    from promptops_app.database import BlueprintVersion
    existing = list_blueprint_versions(db, blueprint.id)
    next_label  = f"v{len(existing) + 1}"
    next_number = len(existing) + 1

    db.query(BlueprintVersion).filter(
        BlueprintVersion.blueprint_id == blueprint.id
    ).update({BlueprintVersion.is_active: False})

    new_ver = BlueprintVersion(
        blueprint_id      = blueprint.id,
        version           = next_label,
        version_number    = next_number,
        parent_version_id = parent_version_id,
        full_content      = content,
        sections          = sections_json,
        generation_params = generation_params_json,
        change_reason     = str(change_summary)[:500] if change_summary else "",
        is_active         = True,
        created_by        = created_by,
    )
    db.add(new_ver)
    blueprint.active_version = next_label
    db.commit()
    db.refresh(new_ver)
    return new_ver


def restore_blueprint_version(
    db,
    blueprint,
    version_label: str,
    created_by: str,
) -> tuple:
    """Restore a previous version by creating a new version copy of its content.

    Returns (new_version, None) on success, (None, error_msg) on failure.
    """
    source = get_blueprint_version(db, blueprint.id, version_label)
    if not source:
        return None, f"Version '{version_label}' not found."
    if source.is_active:
        return None, "This version is already active."

    new_ver = create_blueprint_version(
        db,
        blueprint,
        content               = source.full_content or "",
        sections_json         = source.sections or "",
        generation_params_json = source.generation_params or "",
        change_summary        = f"Restored from {version_label}",
        created_by            = created_by,
        parent_version_id     = source.id,
    )
    return new_ver, None


def count_blueprints_scoped(db, user_name: str = None, project_id: int = None, is_admin: bool = True,
                            *, include_archived: bool = False, date_from=None, date_to=None) -> int:
    q = _live_only(db.query(ModuleBlueprint), include_archived=include_archived)
    if not is_admin and user_name:
        q = q.filter(ModuleBlueprint.created_by == user_name)
        if project_id:
            q = q.filter(ModuleBlueprint.project_id == project_id)
    if date_from is not None:
        q = q.filter(ModuleBlueprint.created_at >= date_from)
    if date_to is not None:
        q = q.filter(ModuleBlueprint.created_at <= date_to)
    return q.count()
