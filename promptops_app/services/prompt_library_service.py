"""
Prompt Library service layer — ported (tenant-free) from the standalone
``prompt-library`` backend's ``services/`` + ``serializers.py``.

All functions take an explicit SQLAlchemy ``Session`` (``db``) — there is no Flask
session, no request context, and no tenant scoping. "Manager" access (see-all,
create/edit/delete) maps to the host roles ``admin`` and ``reviewer``; ``author``
is a regular library reader who can submit requests and reviews.
"""

from __future__ import annotations

import csv
import io
import json
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, or_
from sqlalchemy.orm import Query, Session

from promptops_app.pl_models import (
    PLAttachment,
    PLAuditEvent,
    PLPrompt,
    PLPromptRequest,
    PLPromptTag,
    PLPromptTeam,
    PLPromptVariable,
    PLPromptVersion,
    PLReview,
    PLTeam,
)

# Roles that can see everything and manage prompts (PromptLibrary "manager").
MANAGER_ROLES = ("admin", "reviewer")


def is_manager(role: str | None) -> bool:
    return (role or "") in MANAGER_ROLES


# ---------------------------------------------------------------------------
# Time formatting (matches standalone prompt_library.utils.time.fmt_dt)
# ---------------------------------------------------------------------------

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def fmt_dt(dt) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Variable rendering
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
# Team many-to-many helpers
# ---------------------------------------------------------------------------

def parse_teams_from_data(data: dict) -> list[str] | None:
    """Return team ids from an API payload; None if teams/team not present."""
    if "teams" in data:
        raw = data["teams"]
        if raw is None:
            return []
        return [str(t).strip() for t in raw if str(t).strip()]
    if "team" in data:
        t = (data.get("team") or "").strip()
        return [t] if t else []
    return None


def get_prompt_team_ids(prompt: PLPrompt) -> list[str]:
    if prompt.prompt_teams:
        return sorted(pt.team_id for pt in prompt.prompt_teams)
    if prompt.team_id:
        return [prompt.team_id]
    return []


def set_prompt_teams(db: Session, prompt: PLPrompt, team_ids: list[str]) -> None:
    """Replace prompt team links; keeps legacy team_id in sync (first team)."""
    existing = {pt.team_id: pt for pt in (prompt.prompt_teams or [])}
    want = set(team_ids)
    for tid in want - set(existing):
        db.add(PLPromptTeam(prompt_id=prompt.id, team_id=tid))
    for tid, row in list(existing.items()):
        if tid not in want:
            db.delete(row)
    prompt.team_id = team_ids[0] if team_ids else None


# ---------------------------------------------------------------------------
# Query building (visibility + filters + sort)
# ---------------------------------------------------------------------------

def base_prompt_query(db: Session) -> Query:
    return db.query(PLPrompt).filter(PLPrompt.deleted_at.is_(None))


def apply_sort(q: Query, sort: str) -> Query:
    if sort == "title":
        return q.order_by(PLPrompt.title)
    if sort == "created":
        return q.order_by(PLPrompt.created_at.desc())
    if sort == "category":
        return q.order_by(PLPrompt.category, PLPrompt.title)
    return q.order_by(PLPrompt.updated_at.desc())


def apply_visibility_filter(q: Query, role: str | None, user_team: str | None) -> Query:
    if is_manager(role):
        return q
    if not user_team:
        return q.filter(PLPrompt.visibility == "global")
    return q.filter(
        or_(
            PLPrompt.visibility == "global",
            (PLPrompt.visibility == "team")
            & PLPrompt.prompt_teams.any(PLPromptTeam.team_id == user_team),
        )
    )


def apply_list_filters(q: Query, args: dict) -> Query:
    search = (args.get("q") or "").strip()
    if search:
        like = f"%{search.lower()}%"
        q = q.filter(
            or_(
                func.lower(PLPrompt.title).like(like),
                func.lower(PLPrompt.description).like(like),
                func.lower(PLPrompt.content).like(like),
            )
        )

    category = (args.get("category") or "").strip()
    if category:
        q = q.filter(func.lower(PLPrompt.category) == category.lower())

    tag = (args.get("tag") or "").strip()
    if tag:
        q = q.join(PLPromptTag).filter(func.lower(PLPromptTag.tag) == tag.lower())

    vis_f = (args.get("visibility") or "").strip()
    if vis_f:
        q = q.filter(PLPrompt.visibility == vis_f)

    roots_only = str(args.get("roots_only") or "1").strip().lower()
    if roots_only in ("1", "true", "yes"):
        q = q.filter(PLPrompt.parent_id.is_(None))

    parent_id = (args.get("parent_id") or "").strip()
    if parent_id:
        q = q.filter(PLPrompt.parent_id == parent_id)

    return apply_sort(q, args.get("sort", "updated"))


def visible_prompts_query(db: Session, role: str | None, user_team: str | None) -> Query:
    return apply_visibility_filter(base_prompt_query(db), role or "author", user_team)


def list_distinct_categories(db: Session, role: str | None, user_team: str | None) -> list[str]:
    q = visible_prompts_query(db, role, user_team)
    rows = (
        q.with_entities(PLPrompt.category)
        .filter(PLPrompt.category.isnot(None), PLPrompt.category != "")
        .distinct()
        .order_by(PLPrompt.category)
        .all()
    )
    return [row[0] for row in rows if row[0]]


def list_distinct_tags(db: Session, role: str | None, user_team: str | None, *, category: str | None = None) -> list[str]:
    q = visible_prompts_query(db, role, user_team)
    if category:
        q = q.filter(func.lower(PLPrompt.category) == category.strip().lower())
    rows = (
        q.join(PLPromptTag, PLPromptTag.prompt_id == PLPrompt.id)
        .with_entities(PLPromptTag.tag)
        .filter(PLPromptTag.tag.isnot(None), PLPromptTag.tag != "")
        .distinct()
        .order_by(PLPromptTag.tag)
        .all()
    )
    return [row[0] for row in rows if row[0]]


# ---------------------------------------------------------------------------
# Hierarchy (parent / follow-up, one level only)
# ---------------------------------------------------------------------------

class HierarchyError(ValueError):
    """Invalid parent_id for a prompt."""


def child_count(db: Session, prompt_id: str) -> int:
    return base_prompt_query(db).filter(PLPrompt.parent_id == prompt_id).count()


def resolve_parent_id(db: Session, parent_id: str | None, prompt_id: str | None = None) -> str | None:
    if not parent_id:
        return None
    parent_id = parent_id.strip()
    if not parent_id:
        return None
    if prompt_id and parent_id == prompt_id:
        raise HierarchyError("A prompt cannot be its own parent")

    parent = base_prompt_query(db).filter_by(id=parent_id).first()
    if not parent:
        raise HierarchyError("Parent prompt not found")
    if parent.parent_id is not None:
        raise HierarchyError("Follow-up prompts cannot have child prompts")

    if prompt_id:
        current = base_prompt_query(db).filter_by(id=prompt_id).first()
        if current and child_count(db, prompt_id) > 0:
            raise HierarchyError("Prompts with follow-ups cannot become follow-ups themselves")

    return parent_id


def list_children(db: Session, parent_id: str) -> list[PLPrompt]:
    return (
        base_prompt_query(db)
        .filter(PLPrompt.parent_id == parent_id)
        .order_by(PLPrompt.updated_at.desc())
        .all()
    )


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------

def can_access_prompt(prompt: PLPrompt, role: str | None, user_team: str | None) -> bool:
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
# Serializers
# ---------------------------------------------------------------------------

def _parent_summary(p: PLPrompt | None) -> dict | None:
    if not p:
        return None
    return {"id": p.id, "title": p.title}


def _child_summary(p: PLPrompt) -> dict:
    return {
        "id": p.id,
        "title": p.title,
        "description": p.description or "",
        "updated_at": fmt_dt(p.updated_at),
    }


def prompt_to_dict(db: Session, p: PLPrompt, include_relations: bool = True) -> dict:
    d = {
        "id": p.id,
        "parent_id": p.parent_id,
        "title": p.title,
        "content": p.content,
        "description": p.description or "",
        "category": p.category or "",
        "visibility": p.visibility,
        "teams": get_prompt_team_ids(p),
        "created_by": p.created_by or "",
        "created_at": fmt_dt(p.created_at),
        "updated_at": fmt_dt(p.updated_at),
        "last_used_at": fmt_dt(p.last_used_at),
        "tags": [t.tag for t in (p.tags or [])],
        "variables": [
            {"name": v.name, "label": v.label or "", "hint": v.hint or ""}
            for v in sorted(p.variables or [], key=lambda x: x.sort_order)
        ],
    }
    if include_relations:
        parent = p.parent if p.parent_id else None
        children = list(p.children) if p.children else list_children(db, p.id)
        d["parent"] = _parent_summary(parent)
        d["children"] = [_child_summary(c) for c in children]
        d["_child_count"] = len(children)
        d["can_have_children"] = p.parent_id is None
        d["versions"] = [
            {
                "version": v.version_number,
                "content": v.content,
                "note": v.note or "",
                "created_by": v.created_by or "",
                "created_at": fmt_dt(v.created_at),
            }
            for v in sorted(p.versions or [], key=lambda x: x.version_number)
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
        db.query(PLReview.prompt_id, func.count(PLReview.id), func.avg(PLReview.rating))
        .filter(PLReview.prompt_id.in_(prompt_ids))
        .group_by(PLReview.prompt_id)
        .all()
    )
    return {r[0]: {"count": r[1], "avg": round(float(r[2] or 0), 1)} for r in rows}


def child_count_batch(db: Session, parent_ids: list) -> dict:
    if not parent_ids:
        return {}
    rows = (
        db.query(PLPrompt.parent_id, func.count(PLPrompt.id))
        .filter(PLPrompt.parent_id.in_(parent_ids), PLPrompt.deleted_at.is_(None))
        .group_by(PLPrompt.parent_id)
        .all()
    )
    return {r[0]: r[1] for r in rows}


def enrich(db: Session, prompt: PLPrompt, stats: dict, child_counts: dict | None = None) -> dict:
    d = prompt_to_dict(db, prompt)
    s = stats.get(prompt.id, {"count": 0, "avg": 0})
    d["_review_stats"] = s
    d["_version_count"] = len(prompt.versions or [])
    if child_counts is not None and prompt.parent_id is None:
        d["_child_count"] = child_counts.get(prompt.id, 0)
    return d


def review_to_dict(r: PLReview) -> dict:
    return {
        "id": r.id,
        "prompt_id": r.prompt_id,
        "username": r.username,
        "rating": r.rating,
        "feedback": r.feedback or "",
        "created_at": fmt_dt(r.created_at),
        "updated_at": fmt_dt(r.updated_at),
    }


def request_to_dict(req: PLPromptRequest) -> dict:
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


def team_to_dict(t: PLTeam, user_count: int = 0, prompt_count: int = 0) -> dict:
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

def build_prompt_snapshot(db: Session, p: PLPrompt) -> dict:
    return {
        "title": p.title,
        "content": p.content,
        "description": p.description or "",
        "category": p.category or "",
        "visibility": p.visibility,
        "teams": get_prompt_team_ids(p),
        "tags": sorted(t.tag for t in (p.tags or [])),
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
# Audit logging (simplified, tenant-free)
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
    entity_id: str | None = None,
    summary: str = "",
    changes: dict | None = None,
    actor_username: str | None = None,
    actor_role: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    try:
        event = PLAuditEvent(
            id=str(uuid.uuid4()),
            event_type=event_type,
            action=action,
            actor_username=actor_username,
            actor_role=actor_role,
            entity_type=entity_type,
            entity_id=entity_id,
            summary=summary,
            changes=redact_changes(changes),
            ip_address=ip_address,
            user_agent=(user_agent or "")[:512] or None,
        )
        db.add(event)
        db.flush()
    except Exception:  # audit must never break the main request
        pass


def audit_event_to_dict(e: PLAuditEvent) -> dict:
    return {
        "id": e.id,
        "event_type": e.event_type,
        "action": e.action,
        "actor_username": e.actor_username,
        "actor_role": e.actor_role,
        "entity_type": e.entity_type,
        "entity_id": e.entity_id,
        "summary": e.summary,
        "changes": e.changes,
        "ip_address": e.ip_address,
        "user_agent": e.user_agent,
        "created_at": fmt_dt(e.created_at),
    }


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
            e.event_type,
            e.action,
            e.actor_username or "",
            e.actor_role or "",
            e.entity_type or "",
            e.entity_id or "",
            e.summary,
            e.ip_address or "",
            e.user_agent or "",
            json.dumps(e.changes) if e.changes else "",
        ])
    return buf.getvalue()
