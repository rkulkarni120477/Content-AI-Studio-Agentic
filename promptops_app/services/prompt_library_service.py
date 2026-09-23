"""
Prompt Library service layer — backed by the NATIVE prompt tables.

Phase 4 of PROMPT_CONSOLIDATION_PLAN.md: every query targets the native models
(``Prompt`` filtered to ``prompt_kind='library'``, ``PromptVersion``,
``PromptTag``, ``PromptVariable``, ``PromptAttachment``, ``Team``,
``PromptTeamLink``, ``PromptReview``, ``PromptRequest``, ``AuditLog``) instead
of the retired ``pl_*`` models. Public function signatures are unchanged from
the pl_*-backed version so the router cutover stays mechanical.

Model mapping (Phase 0.5 / Phase 1 decisions):
  * a library prompt's *current content* is the ACTIVE ``PromptVersion``'s
    ``user_prompt_template`` (``system_prompt`` stays NULL for library rows;
    native ``prompts`` deliberately has no content column)
  * ``created_by`` (API field) <-> ``Prompt.owner``
  * version display label = ``'v' || version_number``; library versions are
    instant-publish (``workflow_state='active'``)
  * ``prompt_tags`` is canonical for every kind (Phase 8 tags hygiene); the
    legacy comma-string ``prompts.tags`` stays mirrored on pipeline writes and
    is the read fallback for databases predating backfill ``000100000003``
  * audit is unified into ``audit_logs``: the PL ``event_type`` (dotted, e.g.
    ``prompt.create``) is stored in ``AuditLog.action``; the short PL "action"
    is derived as the suffix after the last dot

Kind separation (Decision 1): all library queries filter
``prompt_kind='library'``. Pipeline rows are browseable only through
``browse_prompts_query`` and only for callers passing
``can_manage_pipeline_prompts`` — a non-qualified caller is silently stripped
to library-only, never 403'd (no existence leak). Writes through this service
never touch pipeline rows.

All functions take an explicit SQLAlchemy ``Session`` (``db``). "Manager"
access (see-all, create/edit/delete) maps to the host roles ``admin`` and
``reviewer``; ``author`` is a regular library reader.
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Query, Session, noload, selectinload

from promptops_app.database import (
    AuditLog,
    Cluster,
    Course,
    Project,
    Prompt,
    PromptAttachment,
    PromptFixing,
    PromptRequest,
    PromptReview,
    PromptTag,
    PromptTeamLink,
    PromptVariable,
    PromptVersion,
    Team,
)
from promptops_app.repositories.prompt_repository import tenant_scope_condition, visible_to_tenant

# Roles that can see everything and manage prompts (PromptLibrary "manager").
MANAGER_ROLES = ("admin", "reviewer")

# Roles allowed to SEE pipeline rows in the console (browse only — writes to
# pipeline rows stay on /api/v1/prompts until the Phase 8 approval gate).
# Formalized as the `prompt.pipeline.edit` permission in Phase 8.
PIPELINE_MANAGER_ROLES = ("admin",)

# Dotted action families the Prompt Library writes to the unified audit_logs
# table. The PL audit UI shows only these (native CAS writers use snake_case
# verbs like 'workflow_transition', so the families don't collide).
PL_AUDIT_ACTION_PREFIXES = ("prompt.", "request.", "attachment.", "team.")


def is_manager(role: str | None) -> bool:
    return (role or "") in MANAGER_ROLES


def can_manage_pipeline_prompts(role: str | None) -> bool:
    return (role or "") in PIPELINE_MANAGER_ROLES


# ---------------------------------------------------------------------------
# Time formatting
# ---------------------------------------------------------------------------

def now_utc() -> datetime:
    """Naive UTC — matches the native tables' ``datetime.utcnow`` convention."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def fmt_dt(dt) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Variable rendering ({{double}} — library rows ONLY; pipeline rows render
# through prompt_builder, never through this engine — Decision 2)
# ---------------------------------------------------------------------------

_VAR_RE = re.compile(r"\{\{(\w+)\}\}")


def render_prompt_content(content: str, variables: dict | None) -> str:
    values = variables or {}

    def repl(match: "re.Match[str]") -> str:
        name = match.group(1)
        if name in values:
            return str(values[name])
        return match.group(0)

    return _VAR_RE.sub(repl, content or "")


def extract_var_names(content: str) -> list[str]:
    seen: list[str] = []
    for m in _VAR_RE.finditer(content or ""):
        if m.group(1) not in seen:
            seen.append(m.group(1))
    return seen


# ---------------------------------------------------------------------------
# Content <-> active version (the library "current content" accessor)
# ---------------------------------------------------------------------------

def active_version(p: Prompt) -> PromptVersion | None:
    """The version whose snapshot is the prompt's current content."""
    versions = p.versions or []
    live = [v for v in versions if v.is_active]
    pool = live or versions
    if not pool:
        return None
    return max(pool, key=lambda v: (v.version_number or 0, v.id or 0))


def get_prompt_content(p: Prompt) -> str:
    v = active_version(p)
    return (v.user_prompt_template if v else "") or ""


_WS_RE = re.compile(r"\s+")


def normalize_prompt_content(text: str) -> str:
    """Whitespace-collapsed, lowercased body — the doc-§10 dedup key."""
    return _WS_RE.sub(" ", (text or "").strip()).lower()


def find_duplicate_prompt(db: Session, content: str, *, kind: str = "library",
                          exclude_id: int | None = None) -> Prompt | None:
    """First live prompt of *kind* whose ACTIVE version matches *content*
    after normalization, or None. Advisory only (create warns, never blocks)
    — dedup for defaults is structural; this catches manual paste-copies.
    In-Python scan: the comparison needs whitespace collapse, and the
    candidate set (live rows of one kind) stays small.
    """
    norm = normalize_prompt_content(content)
    if not norm:
        return None
    rows = (
        db.query(Prompt, PromptVersion.user_prompt_template)
        .join(PromptVersion, (PromptVersion.prompt_id == Prompt.id)
              & PromptVersion.is_active.is_(True))
        .filter(Prompt.deleted_at.is_(None), Prompt.prompt_kind == kind)
    )
    if exclude_id is not None:
        rows = rows.filter(Prompt.id != exclude_id)
    for p, body in rows.all():
        if normalize_prompt_content(body) == norm:
            return p
    return None


def set_prompt_content(db: Session, p: Prompt, content: str, *,
                       create_version: bool = False, note: str = "",
                       created_by: str | None = None) -> PromptVersion:
    """Write the prompt's current content.

    create_version=True  -> append a new instant-publish version (library
                            semantics: bump version_number, activate it).
    create_version=False -> "silent edit": overwrite the active version's
                            snapshot in place (mirrors the old pl_prompts
                            .content overwrite; the version list must not grow).
    """
    # Query the DB (not the possibly-stale relationship collection) so
    # back-to-back calls in one session can't reuse a version_number.
    db.flush()
    versions = (db.query(PromptVersion)
                .filter(PromptVersion.prompt_id == p.id).all())
    live = [v for v in versions if v.is_active]
    pool = live or versions
    current = max(pool, key=lambda v: (v.version_number or 0, v.id or 0)) if pool else None
    if create_version or current is None:
        n = max((v.version_number or 0 for v in versions), default=0) + 1
        for v in versions:
            v.is_active = False
        current = PromptVersion(
            prompt_id=p.id,
            version=f"v{n}",
            version_number=n,
            workflow_state="active",   # library rows are instant-publish
            system_prompt=None,
            user_prompt_template=content,
            change_reason=note or ("Initial version" if n == 1 else ""),
            is_active=True,
            created_by=created_by,
            created_at=now_utc(),
        )
        db.add(current)
        p.active_version = f"v{n}"
    else:
        current.user_prompt_template = content
    db.flush()
    db.expire(p, ["versions"])   # cached collection may predate this write
    return current


# ---------------------------------------------------------------------------
# Team many-to-many helpers
# ---------------------------------------------------------------------------

def parse_teams_from_data(data: dict) -> list[int] | None:
    """Return team ids from an API payload; None if teams/team not present."""
    if "teams" in data:
        raw = data["teams"]
        if raw is None:
            return []
        return [int(str(t).strip()) for t in raw if str(t).strip()]
    if "team" in data:
        t = str(data.get("team") or "").strip()
        return [int(t)] if t else []
    return None


def get_prompt_team_ids(prompt: Prompt) -> list[int]:
    return sorted(link.team_id for link in (prompt.team_links or []))


def set_prompt_teams(db: Session, prompt: Prompt, team_ids: list[int]) -> None:
    """Replace prompt team links (the redundant scalar team_id is gone)."""
    existing = {link.team_id: link for link in (prompt.team_links or [])}
    want = set(team_ids)
    for tid in want - set(existing):
        db.add(PromptTeamLink(prompt_id=prompt.id, team_id=tid))
    for tid, row in list(existing.items()):
        if tid not in want:
            db.delete(row)


# ---------------------------------------------------------------------------
# Query building (kind + visibility + filters + sort)
# ---------------------------------------------------------------------------

def base_prompt_query(db: Session) -> Query:
    """Library rows only — every read AND write path in the library flow
    starts here, so pipeline rows are unreachable through it (Decision 1)."""
    return (
        db.query(Prompt)
        .filter(Prompt.prompt_kind == "library", Prompt.deleted_at.is_(None))
    )


def browse_prompts_query(db: Session, role: str | None, user_team,
                         kind: str | None = None,
                         include_deleted: bool = False,
                         archived_only: bool = False,
                         project_id: int | None = None,
                         is_platform_admin: bool = False) -> Query:
    """Console browse across kinds. ``kind`` in {library, pipeline, all};
    callers without pipeline-manager access are silently stripped to
    library-only regardless of the requested kind (server-side, no 403).

    Archival is a third axis, orthogonal to kind and to the active version's
    workflow_state (doc §9 "unless explicitly enabled"), and both flags below
    are pipeline-manager-only — silently ignored for everyone else, same
    convention as ``kind``:
    - neither: live rows only (the default every other surface relies on).
    - ``include_deleted``: live + archived, for a caller that wants the whole
      set in one list.
    - ``archived_only``: archived rows ONLY, which is what a filter labelled
      "Archived" has to mean. It wins over ``include_deleted`` — the narrower
      request is never widened — and it must stay server-side: the list count
      and the CSV export both read the returned rows, so a client-side filter
      would leave both of them reporting the unfiltered set.
    """
    kind = (kind or "library").strip().lower()
    if kind not in ("library", "pipeline", "all") or not can_manage_pipeline_prompts(role):
        kind = "library"
    if not can_manage_pipeline_prompts(role):
        include_deleted = archived_only = False
    q = db.query(Prompt)
    if archived_only:
        q = q.filter(Prompt.deleted_at.isnot(None))
    elif not include_deleted:
        q = q.filter(Prompt.deleted_at.is_(None))
    if kind != "all":
        q = q.filter(Prompt.prompt_kind == kind)
    return apply_visibility_filter(q, role or "author", user_team,
                                   project_id=project_id, is_platform_admin=is_platform_admin)


# Columns of the active version that the LIST serializer reads. ``system_prompt``
# and ``change_reason`` are excluded on purpose: nothing in a list view renders
# them, and on a real tenant they carried most of the version payload. They stay
# deferred rather than raiseload'd — an accidental access then costs one extra
# query instead of a 500, and ``TestListPayloadAndQueryBudget`` fails on it.
_LIST_VERSION_COLUMNS = (
    PromptVersion.prompt_id,
    PromptVersion.version,
    PromptVersion.version_number,
    PromptVersion.workflow_state,
    PromptVersion.user_prompt_template,
    PromptVersion.is_active,
)


def list_query_loaders(q: Query) -> Query:
    """Eager-load exactly what :func:`prompt_to_list_dict` touches — no more.

    ``Prompt.versions`` has no ``lazy=`` on the model, so it is a plain lazy
    load: serializing a page of rows without this costs ONE QUERY PER ROW (a
    47-row page measured 101 queries / 8.5 s against RDS). ``tag_rows``,
    ``variables`` and ``team_links`` are already ``lazy="selectin"``, so they
    cost one batched query each for the whole page and need no option here.

    ``children`` is selectin by default, never appears in a list row, and is the
    expensive one — every child loaded is itself a ``Prompt`` whose own selectin
    collections then load in turn — so it is cancelled outright. Nothing reads
    ``Prompt.children`` anywhere: the detail payload builds its children from the
    tenant-filtered ``list_children`` query, so cancelling it cannot alter a
    response. (The one hard ``db.delete`` of a prompt lives in the registry
    router, a different request and session, and the FK carries
    ``ondelete=CASCADE`` of its own — so no ORM cascade depends on this
    collection being loaded here either.)

    ``attachments`` is deliberately NOT cancelled even though the list ignores
    it. ``noload`` marks the collection permanently loaded-and-empty on the
    instance, so any later ``prompt_to_dict`` on the same identity map would
    publish ``attachments: []`` for a prompt that has some. Request-scoped
    sessions make that unreachable today, which is exactly the kind of guarantee
    that quietly stops holding — one extra batched query is the cheaper side of
    that trade. See ``test_list_loaders_do_not_corrupt_a_later_detail_dict``.
    """
    return q.options(
        selectinload(Prompt.versions).load_only(*_LIST_VERSION_COLUMNS),
        noload(Prompt.children),
    )


def apply_sort(q: Query, sort: str) -> Query:
    if sort == "title":
        return q.order_by(Prompt.title)
    if sort == "created":
        return q.order_by(Prompt.created_at.desc())
    if sort == "category":
        return q.order_by(Prompt.category, Prompt.title)
    return q.order_by(Prompt.updated_at.desc())


def apply_visibility_filter(q: Query, role: str | None, user_team,
                            project_id: int | None = None,
                            is_platform_admin: bool = False) -> Query:
    # Tenant boundary first, unconditionally — role/visibility below govern
    # what a caller sees WITHIN their own tenant (or globally-shared rows),
    # never across tenants. is_manager/admin is a per-tenant credential, so
    # it must not bypass this the way it bypasses the role checks below.
    if (cond := tenant_scope_condition(project_id, is_platform_admin)) is not None:
        q = q.filter(cond)
    if is_manager(role):
        return q
    if not user_team:
        return q.filter(Prompt.visibility == "global")
    return q.filter(
        or_(
            Prompt.visibility == "global",
            (Prompt.visibility == "team")
            & Prompt.team_links.any(PromptTeamLink.team_id == user_team),
        )
    )


def _active_content_matches(like: str):
    """Filter: the active version's snapshot matches ``like`` (replaces the
    old LIKE over pl_prompts.content — content lives on the version now)."""
    return Prompt.versions.any(
        PromptVersion.is_active.is_(True)
        & func.lower(PromptVersion.user_prompt_template).like(like)
    )


def apply_list_filters(q: Query, args: dict) -> Query:
    search = (args.get("q") or "").strip()
    if search:
        like = f"%{search.lower()}%"
        q = q.filter(
            or_(
                func.lower(Prompt.title).like(like),
                func.lower(Prompt.description).like(like),
                _active_content_matches(like),
            )
        )

    category = (args.get("category") or "").strip()
    if category:
        q = q.filter(func.lower(Prompt.category) == category.lower())

    tag = (args.get("tag") or "").strip()
    if tag:
        q = q.join(PromptTag).filter(func.lower(PromptTag.tag) == tag.lower())

    vis_f = (args.get("visibility") or "").strip()
    if vis_f:
        q = q.filter(Prompt.visibility == vis_f)

    roots_only = str(args.get("roots_only") or "1").strip().lower()
    if roots_only in ("1", "true", "yes"):
        q = q.filter(Prompt.parent_id.is_(None))

    parent_id = str(args.get("parent_id") or "").strip()
    if parent_id:
        try:
            q = q.filter(Prompt.parent_id == int(parent_id))
        except ValueError:
            q = q.filter(False)

    cas_category = (args.get("cas_category") or "").strip()
    if cas_category:
        q = _apply_cas_category_filter(q, cas_category)

    # Workflow-status filter (doc §8): a prompt's status is its ACTIVE
    # version's workflow_state (what the detail page badges show).
    state = (args.get("state") or "").strip().lower()
    if state:
        q = q.filter(Prompt.versions.any(
            PromptVersion.is_active.is_(True)
            & (PromptVersion.workflow_state == state)))

    scope = {}
    for field in ("course_id", "cluster_id", "project_id"):
        raw = str(args.get(field) or "").strip()
        if raw:
            try:
                scope[field] = int(raw)
            except ValueError:
                return q.filter(False)
    if scope:
        q = _apply_scope_filter(q, scope)

    return apply_sort(q, args.get("sort", "updated"))


# The requirements doc's six CAS workflow categories, resolved back to the
# load-bearing (component_type [, variant]) resolution keys. This map is the
# only place a display label is translated into classification — labels
# themselves never drive generation.
_CAS_CATEGORY_KEYS = {
    "style": ("style",),
    "cdd": ("cdd",),
    "blueprint": ("blueprint",),
    "assessment": ("quiz",),
    "lesson generation": ("generate",),
    "component": ("generate", "interactive"),
}

# Components a library prompt may be promoted into (matches the resolution
# keys the generation loader understands).
VALID_PIPELINE_COMPONENTS = ("style", "cdd", "blueprint", "generate", "quiz")


def _apply_cas_category_filter(q: Query, label: str) -> Query:
    """Filter to the pipeline rows of one CAS workflow category (Phase 12).

    ``label`` is one of the doc's six category names (case-insensitive);
    unknown labels match nothing. The generate component splits the way the
    display labels do: Component = the exact interactive slot, Lesson
    Generation = every other generate row. Library rows carry no component,
    so the filter implies pipeline-kind — non-admin browse queries are
    already library-only and simply match nothing (no existence leak).
    """
    key = _CAS_CATEGORY_KEYS.get(label.strip().lower())
    if key is None:
        return q.filter(False)
    component = key[0]
    q = q.filter(Prompt.prompt_kind == "pipeline",
                 Prompt.component_type == component)
    if component == "generate":
        if len(key) == 2:
            q = q.filter(Prompt.variant == key[1])
        else:
            q = q.filter(or_(Prompt.variant.is_(None),
                             Prompt.variant != "interactive"))
    return q


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify_prompt_name(title: str) -> str:
    """Library title → pipeline registry-name stem (snake_case)."""
    slug = _SLUG_RE.sub("_", (title or "").lower()).strip("_")
    return slug or "promoted_prompt"


def unique_prompt_name(db: Session, base: str) -> str:
    """First free registry name for *base* (``prompts.name`` is globally
    unique, soft-deleted rows included, so the scan spans all rows)."""
    taken = {
        n for (n,) in db.query(Prompt.name)
        .filter(Prompt.name.isnot(None), Prompt.name.like(f"{base}%"))
        .all()
    }
    if base not in taken:
        return base
    i = 2
    while f"{base}_{i}" in taken:
        i += 1
    return f"{base}_{i}"


def _apply_scope_filter(q: Query, scope: dict) -> Query:
    """Filter to the prompts a course/cluster/project *uses* (Phase 11, doc §8).

    Matches pipeline rows locked anywhere relevant to the scope — its upward
    covering chain (a course inherits cluster/project/global locks) AND its
    downward subtree (a project "uses" the locks set on its clusters and
    courses) — plus the component defaults every scope inherits. The most
    specific id wins when several are given. Library rows carry no scope, so
    any scope filter implies pipeline-kind — for callers whose browse query
    is already library-only (non-admins) this simply matches nothing,
    preserving the no-existence-leak boundary.
    """
    sess = q.session
    conds = [PromptFixing.scope_level == "global"]
    if "course_id" in scope:
        course = sess.get(Course, scope["course_id"])
        if course is None:
            return q.filter(False)
        conds.append((PromptFixing.scope_level == "course")
                     & (PromptFixing.course_id == course.id))
        if course.cluster_id is not None:
            conds.append((PromptFixing.scope_level == "cluster")
                         & (PromptFixing.cluster_id == course.cluster_id))
        conds.append((PromptFixing.scope_level == "project")
                     & (PromptFixing.project_id == course.project_id))
    elif "cluster_id" in scope:
        cluster = sess.get(Cluster, scope["cluster_id"])
        if cluster is None:
            return q.filter(False)
        conds.append((PromptFixing.scope_level == "cluster")
                     & (PromptFixing.cluster_id == cluster.id))
        conds.append((PromptFixing.scope_level == "project")
                     & (PromptFixing.project_id == cluster.project_id))
        conds.append((PromptFixing.scope_level == "course")
                     & PromptFixing.course_id.in_(
                         select(Course.id).where(Course.cluster_id == cluster.id)))
    else:
        pid = scope["project_id"]
        if sess.get(Project, pid) is None:
            return q.filter(False)
        conds.append((PromptFixing.scope_level == "project")
                     & (PromptFixing.project_id == pid))
        conds.append((PromptFixing.scope_level == "cluster")
                     & PromptFixing.cluster_id.in_(
                         select(Cluster.id).where(Cluster.project_id == pid)))
        conds.append((PromptFixing.scope_level == "course")
                     & PromptFixing.course_id.in_(
                         select(Course.id).where(Course.project_id == pid)))
    fixed_ids = select(PromptFixing.prompt_id).where(
        PromptFixing.prompt_id.isnot(None), or_(*conds)
    )
    return q.filter(
        Prompt.prompt_kind == "pipeline",
        or_(Prompt.id.in_(fixed_ids), Prompt.is_default.is_(True)),
    )


def visible_prompts_query(db: Session, role: str | None, user_team,
                          project_id: int | None = None,
                          is_platform_admin: bool = False) -> Query:
    return apply_visibility_filter(base_prompt_query(db), role or "author", user_team,
                                   project_id=project_id, is_platform_admin=is_platform_admin)


def list_distinct_categories(db: Session, role: str | None, user_team,
                             project_id: int | None = None,
                             is_platform_admin: bool = False) -> list[str]:
    q = visible_prompts_query(db, role, user_team, project_id=project_id, is_platform_admin=is_platform_admin)
    rows = (
        q.with_entities(Prompt.category)
        .filter(Prompt.category.isnot(None), Prompt.category != "")
        .distinct()
        .order_by(Prompt.category)
        .all()
    )
    return [row[0] for row in rows if row[0]]


def list_distinct_tags(db: Session, role: str | None, user_team, *, category: str | None = None,
                       kind: str = "library",
                       project_id: int | None = None,
                       is_platform_admin: bool = False) -> list[str]:
    # kind="pipeline" backs the console's tag filter now that only CAS
    # pipeline prompts are surfaced (Phase 12b) — pipeline managers only;
    # anyone else silently falls back to the library scope (no leak).
    if kind == "pipeline" and can_manage_pipeline_prompts(role):
        q = db.query(Prompt).filter(Prompt.deleted_at.is_(None),
                                    Prompt.prompt_kind == "pipeline")
        if (cond := tenant_scope_condition(project_id, is_platform_admin)) is not None:
            q = q.filter(cond)
    else:
        q = visible_prompts_query(db, role, user_team, project_id=project_id, is_platform_admin=is_platform_admin)
    if category:
        q = q.filter(func.lower(Prompt.category) == category.strip().lower())
    rows = (
        q.join(PromptTag, PromptTag.prompt_id == Prompt.id)
        .with_entities(PromptTag.tag)
        .filter(PromptTag.tag.isnot(None), PromptTag.tag != "")
        .distinct()
        .order_by(PromptTag.tag)
        .all()
    )
    return [row[0] for row in rows if row[0]]


# ---------------------------------------------------------------------------
# Hierarchy (parent / follow-up, one level only)
# ---------------------------------------------------------------------------

class HierarchyError(ValueError):
    """Invalid parent_id for a prompt."""


def child_count(db: Session, prompt_id: int) -> int:
    return base_prompt_query(db).filter(Prompt.parent_id == prompt_id).count()


def resolve_parent_id(db: Session, parent_id, prompt_id: int | None = None,
                      project_id: int | None = None, is_platform_admin: bool = False) -> int | None:
    if parent_id is None or str(parent_id).strip() == "":
        return None
    try:
        parent_id = int(str(parent_id).strip())
    except ValueError:
        raise HierarchyError("Parent prompt not found")
    if prompt_id and parent_id == prompt_id:
        raise HierarchyError("A prompt cannot be its own parent")

    parent = base_prompt_query(db).filter_by(id=parent_id).first()
    # Same "not found" for another tenant's prompt as for a truly nonexistent
    # one — no enumeration oracle for parent_id probing either.
    if parent and not visible_to_tenant(parent.project_id, project_id, is_platform_admin):
        parent = None
    if not parent:
        raise HierarchyError("Parent prompt not found")
    if parent.parent_id is not None:
        raise HierarchyError("Follow-up prompts cannot have child prompts")

    if prompt_id:
        current = base_prompt_query(db).filter_by(id=prompt_id).first()
        if current and child_count(db, prompt_id) > 0:
            raise HierarchyError("Prompts with follow-ups cannot become follow-ups themselves")

    return parent_id


def list_children(db: Session, parent_id: int, *, project_id: int | None = None,
                  is_platform_admin: bool = False) -> list[Prompt]:
    q = base_prompt_query(db).filter(Prompt.parent_id == parent_id)
    if (cond := tenant_scope_condition(project_id, is_platform_admin)) is not None:
        q = q.filter(cond)
    return q.order_by(Prompt.updated_at.desc()).all()


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------

def can_access_prompt(prompt: Prompt, role: str | None, user_team,
                      project_id: int | None = None,
                      is_platform_admin: bool = False) -> bool:
    # Tenant boundary first — a prompt owned by another tenant is invisible
    # regardless of role/kind; manager/admin is a per-tenant credential and
    # must not bypass this (only a genuine platform admin can).
    if not visible_to_tenant(prompt.project_id, project_id, is_platform_admin):
        return False
    if prompt.prompt_kind != "library":
        # Pipeline rows are visible only to pipeline managers (Decision 1);
        # everyone else gets the same 404 as a nonexistent id — no leak.
        return can_manage_pipeline_prompts(role)
    if is_manager(role):
        return True
    vis = prompt.visibility or "draft"
    if vis in ("Public", "public"):
        vis = "global"
    elif vis in ("Private", "private"):
        vis = "draft"
    elif vis == "Team":
        vis = "global"
    if vis == "global":
        return True
    if vis == "team":
        team_ids = get_prompt_team_ids(prompt)
        if not team_ids:
            return True
        return user_team in team_ids
    return False


# ---------------------------------------------------------------------------
# Generation bindings — what an archive would take away
# ---------------------------------------------------------------------------

_SCOPE_ID_ATTR = {"project": "project_id", "cluster": "cluster_id", "course": "course_id"}
_SCOPE_MODEL = {"project": Project, "cluster": Cluster, "course": Course}


def _fixing_scope_name(db: Session, f: PromptFixing) -> str | None:
    """Human name of the scope a lock is bound to (None for global locks)."""
    model = _SCOPE_MODEL.get(f.scope_level)
    if model is None:
        return None
    sid = getattr(f, _SCOPE_ID_ATTR[f.scope_level], None)
    if sid is None:
        return None
    row = db.get(model, sid)
    return getattr(row, "name", None) if row is not None else None


def _fixing_summary(db: Session, f: PromptFixing) -> dict:
    return {
        "id": f.id,
        "component": f.component,
        "scope_level": f.scope_level,
        "scope_name": _fixing_scope_name(db, f),
    }


def prompt_usage(db: Session, prompt: Prompt) -> dict:
    """The generation bindings *prompt* currently holds.

    Two things make a pipeline row live: the component-default flag (every
    scope without a lock inherits it) and PromptFixing scope locks. Archiving
    a row that holds either silently changes what other people's courses
    generate, so ``blocking`` marks the cases the delete endpoint refuses
    without an explicit force.

    Library rows are never resolved for generation and can never be bound to a
    scope (``set_fixing`` rejects them), so their usage is always empty.
    """
    if prompt.prompt_kind != "pipeline":
        return {
            "prompt_id": prompt.id,
            "prompt_kind": prompt.prompt_kind,
            "is_default": False,
            "component_type": None,
            "variant": None,
            "fixings": [],
            "blocking": False,
        }
    fixings = (
        db.query(PromptFixing)
        .filter(PromptFixing.prompt_id == prompt.id)
        .order_by(PromptFixing.component.asc(), PromptFixing.id.asc())
        .all()
    )
    is_default = bool(prompt.is_default)
    return {
        "prompt_id": prompt.id,
        "prompt_kind": prompt.prompt_kind,
        "is_default": is_default,
        "component_type": prompt.component_type,
        "variant": prompt.variant,
        "fixings": [_fixing_summary(db, f) for f in fixings],
        "blocking": is_default or bool(fixings),
    }


def clear_prompt_bindings(db: Session, prompt: Prompt) -> dict:
    """Release every generation binding held by *prompt* (the forced-archive
    path) and return what was released, for the audit event.

    Scope locks are DELETED rather than left with a NULL ``prompt_id``: a lock
    that resolves to nothing behaves exactly like no lock at resolution time,
    but a NULLed row keeps showing up in every fixings listing and "used by"
    facet, so removing it keeps the stored state and the resolved state in
    agreement. Restoring the prompt later does NOT restore its bindings —
    they are re-bound deliberately, from the Titles view.

    Flushed, not committed: the caller owns the transaction.
    """
    released = {"was_default": bool(prompt.is_default), "fixings": []}
    if prompt.prompt_kind != "pipeline":
        return released
    prompt.is_default = False
    for f in db.query(PromptFixing).filter(PromptFixing.prompt_id == prompt.id).all():
        released["fixings"].append(_fixing_summary(db, f))
        db.delete(f)
    db.flush()
    return released


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------

def _parent_summary(p: Prompt | None) -> dict | None:
    if not p:
        return None
    return {"id": p.id, "title": p.title}


def _child_summary(p: Prompt) -> dict:
    return {
        "id": p.id,
        "title": p.title,
        "description": p.description or "",
        "updated_at": fmt_dt(p.updated_at),
    }


def _prompt_tags_list(p: Prompt) -> list[str]:
    # prompt_tags rows are canonical for every kind (Phase 8 tags hygiene).
    # Pipeline rows fall back to the legacy comma-string so databases that
    # predate the backfill migration (000100000003) keep showing their tags.
    if p.tag_rows:
        return [t.tag for t in p.tag_rows]
    if p.prompt_kind == "pipeline":
        return [t.strip() for t in (p.tags or "").split(",") if t.strip()]
    return []


def _prompt_base_dict(p: Prompt) -> dict:
    """Every field that comes from the prompt row itself plus its small batched
    collections (tags, variables, team links).

    Shared verbatim by the detail serializer (:func:`prompt_to_dict`) and the
    list serializer (:func:`prompt_to_list_dict`), so the two can never drift
    apart on a key they both publish. Touches no relationship that is lazy on
    the model, so it is safe to call on a row loaded by a list query.
    """
    return {
        "id": p.id,
        "parent_id": p.parent_id,
        "title": p.title if p.title is not None else (p.name or ""),
        "content": get_prompt_content(p),
        "description": p.description or "",
        "category": p.category or "",
        "visibility": p.visibility,
        "teams": get_prompt_team_ids(p),
        "created_by": p.owner or "",
        "created_at": fmt_dt(p.created_at),
        "updated_at": fmt_dt(p.updated_at),
        "last_used_at": fmt_dt(p.last_used_at),
        "tags": _prompt_tags_list(p),
        "variables": [
            {"name": v.name, "label": v.label or "", "hint": v.hint or ""}
            for v in sorted(p.variables or [], key=lambda x: x.sort_order or 0)
        ],
        "prompt_kind": p.prompt_kind,
        # Soft-deleted rows are only ever serialized on the explicit
        # include-archived path (pipeline managers) — additive key.
        "archived": p.deleted_at is not None,
    }


def _pipeline_block(p: Prompt, *, include_system_prompt: bool) -> dict:
    """Additive pipeline block for the console (Phase 7b). Only admins ever
    receive pipeline rows (browse strips them server-side for everyone else),
    so exposing the resolution keys + workflow state here leaks nothing.
    Library dicts keep their exact legacy shape.

    ``include_system_prompt`` is False on the list path: no list view renders
    the active system prompt, and on a real tenant it was 31% of the whole
    response body.
    """
    av = active_version(p)
    block = {
        "name": p.name or "",
        "component_type": p.component_type,
        "variant": p.variant,
        "is_default": bool(p.is_default),
        "active_version": p.active_version,
        "workflow_state": av.workflow_state if av else None,
    }
    if include_system_prompt:
        block["system_prompt"] = (av.system_prompt if av else "") or ""
    return block


def prompt_to_dict(db: Session, p: Prompt, include_relations: bool = True, *,
                   project_id: int | None = None, is_platform_admin: bool = False) -> dict:
    d = _prompt_base_dict(p)
    if p.prompt_kind == "pipeline":
        d["pipeline"] = _pipeline_block(p, include_system_prompt=True)
    if include_relations:
        # Parent/children traverse ORM relationships with no tenant predicate
        # of their own, so both ends need the same gate applied explicitly —
        # a shared/global parent is visible to every tenant, but its children
        # list must still only ever show the caller's own tenant's rows.
        parent = p.parent if p.parent_id else None
        if parent is not None and not visible_to_tenant(parent.project_id, project_id, is_platform_admin):
            parent = None
        children = list_children(db, p.id, project_id=project_id, is_platform_admin=is_platform_admin)
        d["parent"] = _parent_summary(parent)
        d["children"] = [_child_summary(c) for c in children]
        d["_child_count"] = len(children)
        d["can_have_children"] = p.parent_id is None
        d["versions"] = [
            {
                "version": v.version_number,
                "content": v.user_prompt_template or "",
                "note": v.change_reason or "",
                "created_by": v.created_by or "",
                "created_at": fmt_dt(v.created_at),
                # Pipeline-only extras; library version dicts keep their shape.
                **(
                    {
                        "label": v.version,
                        "system_prompt": v.system_prompt or "",
                        "workflow_state": v.workflow_state,
                        "is_active": bool(v.is_active),
                    }
                    if p.prompt_kind == "pipeline" else {}
                ),
            }
            for v in sorted(p.versions or [], key=lambda x: x.version_number or 0)
        ]
        d["attachments"] = [
            {
                "id": a.id,
                "original_name": a.original_name,
                "stored_name": a.stored_name,
                "size": a.size_bytes or 0,
                "uploaded_by": a.uploaded_by or "",
                "uploaded_at": fmt_dt(a.uploaded_at),
            }
            for a in (p.attachments or [])
        ]
        d["_version_count"] = len(p.versions or [])
        d["_review_stats"] = {"count": 0, "avg": 0}
    return d


def review_stats_batch(db: Session, prompt_ids: list) -> dict:
    if not prompt_ids:
        return {}
    rows = (
        db.query(PromptReview.prompt_id, func.count(PromptReview.id), func.avg(PromptReview.rating))
        .filter(PromptReview.prompt_id.in_(prompt_ids))
        .group_by(PromptReview.prompt_id)
        .all()
    )
    return {r[0]: {"count": r[1], "avg": round(float(r[2] or 0), 1)} for r in rows}


def child_count_batch(db: Session, parent_ids: list, *, project_id: int | None = None,
                      is_platform_admin: bool = False) -> dict:
    if not parent_ids:
        return {}
    q = db.query(Prompt.parent_id, func.count(Prompt.id)).filter(
        Prompt.parent_id.in_(parent_ids), Prompt.deleted_at.is_(None),
    )
    if (cond := tenant_scope_condition(project_id, is_platform_admin)) is not None:
        q = q.filter(cond)
    rows = q.group_by(Prompt.parent_id).all()
    return {r[0]: r[1] for r in rows}


def enrich(db: Session, prompt: Prompt, stats: dict, child_counts: dict | None = None, *,
          project_id: int | None = None, is_platform_admin: bool = False) -> dict:
    d = prompt_to_dict(db, prompt, project_id=project_id, is_platform_admin=is_platform_admin)
    s = stats.get(prompt.id, {"count": 0, "avg": 0})
    d["_review_stats"] = s
    d["_version_count"] = len(prompt.versions or [])
    if child_counts is not None and prompt.parent_id is None:
        d["_child_count"] = child_counts.get(prompt.id, 0)
    return d


def prompt_to_list_dict(p: Prompt, *, review_stats: dict | None = None,
                        child_count: int = 0) -> dict:
    """Row shape for the browse list (GET /prompts, GET /prompts/search).

    A deliberately narrower projection than :func:`prompt_to_dict`. The list
    views (card grid, list table, CSV export) read the base fields plus the
    three ``_``-prefixed counters; the relation expansions only the detail page
    renders — the full ``versions`` array, ``attachments``, ``parent``,
    ``children`` — are omitted, as is the active ``system_prompt``. On a real
    tenant (47 pipeline prompts) those were 88% of the response body: 863 KB
    down to 102 KB.

    Deliberately takes no ``Session``: a list row must be serializable purely
    from already-loaded state, which makes a per-row query impossible by
    construction. Pair it with :func:`list_query_loaders` so the collections it
    does read are batched for the whole page.

    ``_version_count`` replaces the dropped ``versions`` array for the callers
    that only counted it, and ``_child_count`` the dropped ``children`` array.
    """
    d = _prompt_base_dict(p)
    if p.prompt_kind == "pipeline":
        d["pipeline"] = _pipeline_block(p, include_system_prompt=False)
    d["can_have_children"] = p.parent_id is None
    d["_review_stats"] = review_stats or {"count": 0, "avg": 0}
    d["_version_count"] = len(p.versions or [])
    d["_child_count"] = child_count
    return d


def review_to_dict(r: PromptReview) -> dict:
    return {
        "id": r.id,
        "prompt_id": r.prompt_id,
        "username": r.username,
        "rating": r.rating,
        "feedback": r.feedback or "",
        "created_at": fmt_dt(r.created_at),
        "updated_at": fmt_dt(r.updated_at),
    }


def request_to_dict(req: PromptRequest) -> dict:
    return {
        "id": req.id,
        "title": req.title,
        "description": req.description or "",
        "type": req.type,
        "prompt_id": req.prompt_id,
        "requested_by": req.requested_by,
        "status": req.status,
        "admin_notes": req.admin_notes or "",
        "created_at": fmt_dt(req.created_at),
        "updated_at": fmt_dt(req.updated_at),
    }


def team_to_dict(t: Team, user_count: int = 0, prompt_count: int = 0) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "created_at": fmt_dt(t.created_at),
        "created_by": t.created_by or "",
        "user_count": user_count,
        "prompt_count": prompt_count,
    }


# ---------------------------------------------------------------------------
# Change snapshot / diff (for audit "changes")
# ---------------------------------------------------------------------------

def build_prompt_snapshot(db: Session, p: Prompt) -> dict:
    return {
        "title": p.title,
        "content": get_prompt_content(p),
        "description": p.description or "",
        "category": p.category or "",
        "visibility": p.visibility,
        "teams": get_prompt_team_ids(p),
        "tags": sorted(_prompt_tags_list(p)),
    }


def compute_prompt_changes(before: dict, after: dict) -> dict:
    changes: dict = {}
    for key in set(before) | set(after):
        old = before.get(key)
        new = after.get(key)
        if old != new:
            changes[key] = {"old": old, "new": new}
    return changes


# ---------------------------------------------------------------------------
# Audit logging (unified audit_logs table — Decision 4)
# ---------------------------------------------------------------------------

SENSITIVE_KEYS = frozenset({"password", "password_hash", "token", "secret"})


def redact_changes(changes: dict | None) -> dict | None:
    if not changes:
        return changes
    out: dict = {}
    for key, value in changes.items():
        if key.lower() in SENSITIVE_KEYS:
            out[key] = {"old": "[redacted]", "new": "[redacted]"}
        else:
            out[key] = value
    return out


def log_event(
    db: Session,
    event_type: str,
    action: str,
    *,
    entity_type: str | None = None,
    entity_id=None,
    summary: str = "",
    changes: dict | None = None,
    actor_username: str | None = None,
    actor_role: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Write a PL audit event to the unified ``audit_logs`` table.

    ``event_type`` (the dotted string, e.g. ``prompt.create``) lands in
    ``AuditLog.action``; the short ``action`` argument is accepted for
    signature compatibility but derived from the suffix on read.
    """
    try:
        db.add(AuditLog(
            user_id=actor_username or "system",
            action=event_type,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            summary=summary,
            changes=redact_changes(changes),
            actor_role=actor_role,
            ip_address=(ip_address or "")[:45] or None,
            user_agent=(user_agent or "")[:512] or None,
            created_at=now_utc(),
        ))
        db.flush()
    except Exception:  # audit must never break the main request
        pass


def _short_action(action: str | None) -> str:
    return (action or "").rsplit(".", 1)[-1]


def _opaque_entity_id(value: str | None):
    """entity_id is stored as String; PL entity ids are integers after the
    cutover — return them as ints so they compare equal to serialized ids."""
    if value is None:
        return None
    return int(value) if value.isdigit() else value


def audit_event_to_dict(e: AuditLog) -> dict:
    return {
        "id": e.id,
        "event_type": e.action,
        "action": _short_action(e.action),
        "actor_username": e.user_id,
        "actor_role": e.actor_role,
        "entity_type": e.entity_type,
        "entity_id": _opaque_entity_id(e.entity_id),
        "summary": e.summary or "",
        "changes": e.changes,
        "ip_address": e.ip_address,
        "user_agent": e.user_agent,
        "created_at": fmt_dt(e.created_at),
    }


def pl_audit_scope(q: Query) -> Query:
    """Restrict a query over AuditLog to the PL action families, so the PL
    audit UI doesn't surface unrelated CAS audit rows from the shared table."""
    return q.filter(or_(*[
        AuditLog.action.like(f"{prefix}%") for prefix in PL_AUDIT_ACTION_PREFIXES
    ]))


def events_to_csv(events: list) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "created_at", "event_type", "action", "actor_username", "actor_role",
        "entity_type", "entity_id", "summary", "ip_address", "user_agent", "changes",
    ])
    for e in events:
        writer.writerow([
            fmt_dt(e.created_at),
            e.action,
            _short_action(e.action),
            e.user_id or "",
            e.actor_role or "",
            e.entity_type or "",
            e.entity_id or "",
            e.summary or "",
            e.ip_address or "",
            e.user_agent or "",
            json.dumps(e.changes) if e.changes else "",
        ])
    return buf.getvalue()
