#!/usr/bin/env python3
"""
Phase 2 carry-over: migrate every pl_* row into the native prompt tables.

Carries the Prompt Library data (pl_prompts / pl_prompt_versions / pl_prompt_tags /
pl_prompt_variables / pl_teams / pl_prompt_teams / pl_attachments / pl_reviews /
pl_prompt_requests — 126 rows in prod as of 2026-07-05) into the native tables
(prompts / prompt_versions / prompt_tags / prompt_variables / teams /
prompt_team_links / prompt_attachments / prompt_reviews / prompt_requests) per
PROMPT_CONSOLIDATION_PLAN.md Phase 2 and the Phase 0.5 decisions:

  * pl_prompts.content      -> active PromptVersion.user_prompt_template
                               (system_prompt stays NULL — Decision 2);
                               {{double}}-brace variables preserved verbatim
  * pl_prompts.created_by   -> prompts.owner
  * prompt_kind             = 'library', name stays NULL
  * version label           = 'v' || version_number (pipeline admin UI contract)
  * pl_teams slug PK (P1..) -> integer teams.id; slug preserved in teams.name
  * every FK rewritten through three id-remap maps
    (pl prompt UUID -> int, team slug -> int, request UUID -> int)

Run-time re-assertion (Phase 1 verification): pl_prompts.content must equal the
head version's snapshot; on divergence a reconciliation version is appended
(change_reason='migration: unversioned edit') so no text is lost.

Idempotent: each migrated entity is recorded as an audit_logs row
(action='migration.pl_carryover', changes={'pl_id': ...}); re-runs resolve the
existing mapping and only fill in whatever is missing. A natural-key adoption
fallback (title + created_at for prompts, unique name for teams) guards against
duplicates even if the audit mapping is lost.

Usage:
  # 1. dry-run against the rehearsal DB — prints the full row-for-row plan, rolls back
  .venv/bin/python scripts/migrate_pl_to_native_prompts.py \
      --database-url "$(scripts/rehearsal_db.sh url)" --dry-run

  # 2. apply to the rehearsal DB, verify parity
  .venv/bin/python scripts/migrate_pl_to_native_prompts.py \
      --database-url "$(scripts/rehearsal_db.sh url)" --apply

  # 3. prod is a separate gated step (MIGRATION_RUNBOOK.md): RDS snapshot +
  #    pl_* dump in backups/ first, then add --allow-non-local.

Exits non-zero if any parity check fails (dry-run or apply).
"""

from __future__ import annotations

import argparse
import getpass
import glob
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

EXPECTED_HEAD_REVISION = "000100000002"
AUDIT_ACTION = "migration.pl_carryover"
AUDIT_USER = f"migration-script({getpass.getuser()})"
RECONCILE_REASON = "migration: unversioned edit"


def as_naive_utc(dt):
    """pl_* columns are timestamptz; native columns are naive-UTC timestamps."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


class Report:
    def __init__(self):
        self.lines: list[str] = []
        self.failures: list[str] = []
        self.counts: dict[str, int] = {}

    def row(self, msg: str):
        self.lines.append(f"  {msg}")

    def section(self, msg: str):
        self.lines.append(f"\n== {msg} ==")

    def migrated(self, kind: str, n: int = 1):
        self.counts[kind] = self.counts.get(kind, 0) + n

    def fail(self, msg: str):
        self.failures.append(msg)
        self.lines.append(f"  PARITY FAIL: {msg}")

    def dump(self):
        print("\n".join(self.lines))


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--database-url", required=True,
                    help="Target DB URL (use scripts/rehearsal_db.sh url for rehearsal)")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true",
                      help="Run everything in a transaction, print the plan, roll back")
    mode.add_argument("--apply", action="store_true", help="Commit the migration")
    ap.add_argument("--allow-non-local", action="store_true",
                    help="Required to touch any non-localhost DB (i.e. prod)")
    return ap.parse_args()


def guard_target(args):
    from sqlalchemy.engine.url import make_url
    url = make_url(args.database_url)
    host = url.host or "localhost"
    local = host in ("localhost", "127.0.0.1", "::1")
    print(f"Target: host={host} db={url.database} mode={'DRY-RUN' if args.dry_run else 'APPLY'}")
    if not local and not args.allow_non_local:
        sys.exit(f"REFUSING: {host} is not localhost. Prod apply is a separate gated step "
                 f"(MIGRATION_RUNBOOK.md); pass --allow-non-local only after the RDS snapshot "
                 f"and pl_* dump gates are met.")
    if not local and args.apply:
        dumps = glob.glob(str(REPO_ROOT / "backups" / "pl_tables_pre_migration_*.sql"))
        if not dumps:
            sys.exit("REFUSING: no pl_tables_pre_migration_*.sql dump in backups/ — "
                     "take one first (scripts/rehearsal_db.sh dump).")
        print(f"pl_* pre-migration dump present: {sorted(dumps)[-1]}")


def preflight(session):
    from sqlalchemy import text
    rev = session.execute(text("SELECT version_num FROM alembic_version")).scalar()
    if rev is None or rev < EXPECTED_HEAD_REVISION:
        sys.exit(f"REFUSING: alembic revision is {rev!r}; need >= {EXPECTED_HEAD_REVISION} "
                 f"(Phase 3 schema) on the target DB.")
    counts = {}
    for t in ("pl_prompts", "pl_prompt_versions", "pl_prompt_tags", "pl_prompt_variables",
              "pl_teams", "pl_prompt_teams", "pl_attachments", "pl_reviews",
              "pl_prompt_requests", "pl_audit_events"):
        counts[t] = session.execute(text(f"SELECT count(*) FROM {t}")).scalar()
    total = sum(counts.values())
    print(f"Source rows: {counts} (total {total})")
    return counts


def load_existing_mapping(session, models):
    """pl_id -> native id, from prior runs' audit rows (idempotency)."""
    mapping = {"prompt": {}, "team": {}, "request": {}}
    for row in (session.query(models.AuditLog)
                .filter(models.AuditLog.action == AUDIT_ACTION).all()):
        ch = row.changes or {}
        kind, pl_id = ch.get("entity"), ch.get("pl_id")
        if kind in mapping and pl_id is not None:
            mapping[kind][str(pl_id)] = ch.get("native_id")
    return mapping


def record_mapping(session, models, entity, pl_id, native_id, summary):
    session.add(models.AuditLog(
        user_id=AUDIT_USER,
        action=AUDIT_ACTION,
        entity_type=entity,
        entity_id=str(native_id),
        summary=summary,
        changes={"entity": entity, "pl_id": str(pl_id), "native_id": native_id},
        actor_role="migration",
        created_at=datetime.utcnow(),
    ))


def migrate_teams(session, models, pl, mapping, report):
    report.section("Teams (pl_teams -> teams; slug PK -> integer id, slug kept in name)")
    team_map = dict(mapping["team"])
    for t in session.query(pl.PLTeam).order_by(pl.PLTeam.id).all():
        if t.id in team_map:
            report.row(f"team {t.id!r}: already migrated -> teams.id={team_map[t.id]} (skip)")
            continue
        existing = session.query(models.Team).filter_by(name=t.name).one_or_none()
        if existing is not None:
            team_map[t.id] = existing.id
            record_mapping(session, models, "team", t.id, existing.id,
                           f"adopted existing team {t.name!r}")
            report.row(f"team {t.id!r}: adopted existing teams.id={existing.id} name={t.name!r}")
            continue
        team = models.Team(name=t.name, created_by=t.created_by,
                           created_at=as_naive_utc(t.created_at))
        session.add(team)
        session.flush()
        team_map[t.id] = team.id
        record_mapping(session, models, "team", t.id, team.id, f"migrated team {t.name!r}")
        report.migrated("teams")
        report.row(f"team {t.id!r} -> teams.id={team.id} name={t.name!r} "
                   f"created_by={t.created_by!r}")
    return team_map


def migrate_one_prompt(session, models, p, parent_native_id, report):
    prompt = models.Prompt(
        prompt_kind="library",
        name=None,                       # library rows leave the unique pipeline key NULL
        title=p.title,
        description=p.description,
        category=p.category,
        visibility=p.visibility,
        owner=p.created_by,              # pl created_by -> owner (Phase 1 verification)
        component_type=None,
        is_default=False,
        variant=None,
        parent_id=parent_native_id,
        created_at=as_naive_utc(p.created_at),
        updated_at=as_naive_utc(p.updated_at),
        last_used_at=as_naive_utc(p.last_used_at),
        deleted_at=as_naive_utc(p.deleted_at),
    )
    session.add(prompt)
    session.flush()

    versions = sorted(p.versions, key=lambda v: v.version_number)
    head_number = versions[-1].version_number if versions else None
    for v in versions:
        session.add(models.PromptVersion(
            prompt_id=prompt.id,
            version=f"v{v.version_number}",
            version_number=v.version_number,
            workflow_state="active",     # library rows are instant-publish
            system_prompt=None,
            user_prompt_template=v.content,   # {{double}} braces carried verbatim
            change_reason=v.note,
            is_active=(v.version_number == head_number),
            created_by=v.created_by,
            created_at=as_naive_utc(v.created_at),
        ))
        report.migrated("prompt_versions")

    # Run-time re-assertion: row body must equal the head snapshot, else the
    # unversioned edit is preserved as a reconciliation version (no text lost).
    reconciled = False
    if versions and p.content != versions[-1].content:
        reconciled = True
        session.execute(
            models.PromptVersion.__table__.update()
            .where(models.PromptVersion.prompt_id == prompt.id)
            .values(is_active=False)
        )
        head_number += 1
        session.add(models.PromptVersion(
            prompt_id=prompt.id,
            version=f"v{head_number}",
            version_number=head_number,
            workflow_state="active",
            system_prompt=None,
            user_prompt_template=p.content,
            change_reason=RECONCILE_REASON,
            is_active=True,
            created_by=p.created_by,
            created_at=as_naive_utc(p.updated_at) or datetime.utcnow(),
        ))
        report.migrated("prompt_versions")
    elif not versions and p.content is not None:
        # defensive: a versionless pl prompt still gets its body versioned
        reconciled = True
        head_number = 1
        session.add(models.PromptVersion(
            prompt_id=prompt.id, version="v1", version_number=1,
            workflow_state="active", system_prompt=None,
            user_prompt_template=p.content, change_reason=RECONCILE_REASON,
            is_active=True, created_by=p.created_by,
            created_at=as_naive_utc(p.created_at),
        ))
        report.migrated("prompt_versions")

    prompt.active_version = f"v{head_number}" if head_number else None

    for tag in p.tags:
        session.add(models.PromptTag(prompt_id=prompt.id, tag=tag.tag))
        report.migrated("prompt_tags")
    for var in p.variables:
        session.add(models.PromptVariable(
            prompt_id=prompt.id, name=var.name, label=var.label,
            hint=var.hint, sort_order=var.sort_order,
        ))
        report.migrated("prompt_variables")
    for att in p.attachments:
        session.add(models.PromptAttachment(
            prompt_id=prompt.id, original_name=att.original_name,
            stored_name=att.stored_name, size_bytes=att.size_bytes,
            uploaded_by=att.uploaded_by, uploaded_at=as_naive_utc(att.uploaded_at),
        ))
        report.migrated("prompt_attachments")
    for rev in p.reviews:
        session.add(models.PromptReview(
            prompt_id=prompt.id, username=rev.username, rating=rev.rating,
            feedback=rev.feedback, created_at=as_naive_utc(rev.created_at),
            updated_at=as_naive_utc(rev.updated_at),
        ))
        report.migrated("prompt_reviews")

    report.migrated("prompts")
    report.row(
        f"prompt {p.id} -> prompts.id={prompt.id} title={p.title!r} "
        f"category={p.category!r} visibility={p.visibility} owner={p.created_by!r} "
        f"versions={len(versions)}{' +1 reconciliation' if reconciled else ''} "
        f"active={prompt.active_version} tags={len(p.tags)} vars={len(p.variables)}"
    )
    return prompt


def migrate_prompts(session, models, pl, mapping, team_map, report):
    report.section("Prompts (pl_prompts -> prompts kind='library'; content -> head "
                   "version user_prompt_template, system_prompt NULL)")
    prompt_map = dict(mapping["prompt"])
    pl_prompts = (session.query(pl.PLPrompt)
                  .order_by(pl.PLPrompt.created_at, pl.PLPrompt.id).all())

    # natural-key adoption fallback if the audit mapping was lost. category is
    # part of the key: prod has two prompts sharing title + created_at
    # ('Pathway Specific Dev Prompt', Blueprint vs Storyboard Development).
    natural = {}
    for np_ in (session.query(models.Prompt)
                .filter(models.Prompt.prompt_kind == "library").all()):
        natural[(np_.title, np_.category, np_.created_at)] = np_.id

    # roots first, then children whose parents are mapped (parent_id remap)
    pending = list(pl_prompts)
    while pending:
        progressed = False
        remaining = []
        for p in pending:
            if p.id in prompt_map:
                report.row(f"prompt {p.id}: already migrated -> prompts.id={prompt_map[p.id]} (skip)")
                progressed = True
                continue
            key = (p.title, p.category, as_naive_utc(p.created_at))
            if key in natural:
                prompt_map[p.id] = natural[key]
                record_mapping(session, models, "prompt", p.id, natural[key],
                               f"adopted existing library prompt {p.title!r}")
                report.row(f"prompt {p.id}: adopted existing prompts.id={natural[key]} "
                           f"title={p.title!r}")
                progressed = True
                continue
            if p.parent_id is not None and p.parent_id not in prompt_map:
                remaining.append(p)   # parent not migrated yet
                continue
            parent_native = prompt_map.get(p.parent_id) if p.parent_id else None
            native = migrate_one_prompt(session, models, p, parent_native, report)
            prompt_map[p.id] = native.id
            record_mapping(session, models, "prompt", p.id, native.id,
                           f"migrated library prompt {p.title!r} "
                           f"({len(p.versions)} version(s), {len(p.tags)} tag(s))")
            progressed = True
        if not progressed and remaining:
            sys.exit(f"REFUSING: parent_id cycle or dangling parent among "
                     f"{[p.id for p in remaining]}")
        pending = remaining
    return prompt_map


def migrate_team_links(session, models, pl, prompt_map, team_map, report):
    report.section("Team links (pl_prompt_teams -> prompt_team_links)")
    n = 0
    for link in session.query(pl.PLPromptTeam).all():
        pid, tid = prompt_map[link.prompt_id], team_map[link.team_id]
        exists = session.query(models.PromptTeamLink).filter_by(
            prompt_id=pid, team_id=tid).one_or_none()
        if exists is None:
            session.add(models.PromptTeamLink(prompt_id=pid, team_id=tid))
            report.migrated("prompt_team_links")
            report.row(f"link prompt {link.prompt_id} x team {link.team_id!r} -> ({pid}, {tid})")
        n += 1
    if n == 0:
        report.row("none in source (0 rows)")


def migrate_requests(session, models, pl, mapping, prompt_map, report):
    report.section("Requests (pl_prompt_requests -> prompt_requests)")
    request_map = dict(mapping["request"])
    natural = {(nr.title, nr.requested_by, nr.created_at): nr.id
               for nr in session.query(models.PromptRequest).all()}
    for r in session.query(pl.PLPromptRequest).order_by(pl.PLPromptRequest.created_at).all():
        if r.id in request_map:
            report.row(f"request {r.id}: already migrated -> id={request_map[r.id]} (skip)")
            continue
        key = (r.title, r.requested_by, as_naive_utc(r.created_at))
        if key in natural:
            request_map[r.id] = natural[key]
            record_mapping(session, models, "request", r.id, natural[key],
                           f"adopted existing prompt request {r.title!r}")
            report.row(f"request {r.id}: adopted existing prompt_requests.id={natural[key]}")
            continue
        req = models.PromptRequest(
            title=r.title, description=r.description, type=r.type,
            prompt_id=prompt_map.get(r.prompt_id) if r.prompt_id else None,
            requested_by=r.requested_by, status=r.status, admin_notes=r.admin_notes,
            created_at=as_naive_utc(r.created_at), updated_at=as_naive_utc(r.updated_at),
        )
        session.add(req)
        session.flush()
        request_map[r.id] = req.id
        record_mapping(session, models, "request", r.id, req.id,
                       f"migrated prompt request {r.title!r}")
        report.migrated("prompt_requests")
        report.row(f"request {r.id} -> prompt_requests.id={req.id} title={r.title!r} "
                   f"type={r.type} status={r.status} requested_by={r.requested_by!r} "
                   f"prompt_id={req.prompt_id}")
    return request_map


def verify_parity(session, models, pl, prompt_map, team_map, request_map, report):
    """Row-for-row parity between pl_* source and the native tables."""
    report.section("Parity verification (source pl_* vs native)")
    carried = 0

    for t in session.query(pl.PLTeam).all():
        native = session.get(models.Team, team_map.get(t.id, -1))
        if native is None or native.name != t.name:
            report.fail(f"team {t.id!r}: no native row with name {t.name!r}")
        else:
            carried += 1

    for p in session.query(pl.PLPrompt).all():
        native = session.get(models.Prompt, prompt_map.get(p.id, -1))
        if native is None:
            report.fail(f"prompt {p.id}: not migrated")
            continue
        carried += 1
        for field, got, want in (
            ("kind", native.prompt_kind, "library"),
            ("name", native.name, None),
            ("title", native.title, p.title),
            ("description", native.description, p.description),
            ("category", native.category, p.category),
            ("visibility", native.visibility, p.visibility),
            ("owner", native.owner, p.created_by),
            ("created_at", native.created_at, as_naive_utc(p.created_at)),
            ("deleted_at", native.deleted_at, as_naive_utc(p.deleted_at)),
        ):
            if got != want:
                report.fail(f"prompt {p.id} field {field}: native={got!r} != pl={want!r}")

        nvers = {v.version_number: v for v in native.versions}
        for v in p.versions:
            nv = nvers.get(v.version_number)
            if nv is None:
                report.fail(f"prompt {p.id} v{v.version_number}: version row missing")
                continue
            carried += 1
            if nv.user_prompt_template != v.content:
                report.fail(f"prompt {p.id} v{v.version_number}: content mismatch")
            if nv.system_prompt is not None:
                report.fail(f"prompt {p.id} v{v.version_number}: system_prompt not NULL")
            if nv.version != f"v{v.version_number}":
                report.fail(f"prompt {p.id} v{v.version_number}: label {nv.version!r}")

        active = [v for v in native.versions if v.is_active]
        if len(active) != 1:
            report.fail(f"prompt {p.id}: {len(active)} active versions (want exactly 1)")
        elif active[0].user_prompt_template != p.content:
            report.fail(f"prompt {p.id}: active version content != pl_prompts.content "
                        f"(reconciliation failed)")
        elif native.active_version != f"v{active[0].version_number}":
            report.fail(f"prompt {p.id}: active_version label {native.active_version!r} "
                        f"!= v{active[0].version_number}")

        src_tags = {t.tag for t in p.tags}
        dst_tags = {t.tag for t in session.query(models.PromptTag)
                    .filter_by(prompt_id=native.id).all()}
        if src_tags != dst_tags:
            report.fail(f"prompt {p.id} tags: {sorted(src_tags)} != {sorted(dst_tags)}")
        carried += len(src_tags & dst_tags)

        src_vars = {(v.name, v.label, v.hint, v.sort_order) for v in p.variables}
        dst_vars = {(v.name, v.label, v.hint, v.sort_order)
                    for v in session.query(models.PromptVariable)
                    .filter_by(prompt_id=native.id).all()}
        if src_vars != dst_vars:
            report.fail(f"prompt {p.id} variables mismatch: missing="
                        f"{src_vars - dst_vars} extra={dst_vars - src_vars}")
        carried += len(src_vars & dst_vars)

    for r in session.query(pl.PLPromptRequest).all():
        native = session.get(models.PromptRequest, request_map.get(r.id, -1))
        if native is None:
            report.fail(f"request {r.id}: not migrated")
            continue
        carried += 1
        if (native.title, native.type, native.status, native.requested_by,
                native.description, native.admin_notes) != (
                r.title, r.type, r.status, r.requested_by, r.description, r.admin_notes):
            report.fail(f"request {r.id}: field mismatch")

    for name, model in (("pl_prompt_teams", pl.PLPromptTeam),
                        ("pl_attachments", pl.PLAttachment),
                        ("pl_reviews", pl.PLReview)):
        n = session.query(model).count()
        if n:
            report.fail(f"{name} has {n} rows — per-row parity not implemented "
                        f"beyond the carry above; verify manually")
        carried += n

    report.row(f"source rows accounted for in native tables: {carried}")
    report.counts["parity_rows"] = carried
    return carried


def main():
    args = parse_args()
    guard_target(args)
    os.environ["DATABASE_URL"] = args.database_url  # defensive: no module touches prod

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    import promptops_app.database as models
    import promptops_app.pl_models as pl

    engine = create_engine(args.database_url)
    Session = sessionmaker(bind=engine, autoflush=False, future=True)
    report = Report()

    with Session() as session:
        preflight(session)
        mapping = load_existing_mapping(session, models)
        if any(mapping.values()):
            print(f"Existing mapping found (re-run): "
                  f"{ {k: len(v) for k, v in mapping.items()} }")

        team_map = migrate_teams(session, models, pl, mapping, report)
        prompt_map = migrate_prompts(session, models, pl, mapping, team_map, report)
        migrate_team_links(session, models, pl, prompt_map, team_map, report)
        request_map = migrate_requests(session, models, pl, mapping, prompt_map, report)
        session.flush()
        verify_parity(session, models, pl, prompt_map, team_map, request_map, report)

        report.section("Summary")
        for k in sorted(report.counts):
            report.row(f"{k}: {report.counts[k]}")

        if args.dry_run:
            session.rollback()
            report.row("DRY-RUN: transaction rolled back, nothing written")
        elif report.failures:
            session.rollback()
            report.row("APPLY ABORTED: parity failures — transaction rolled back")
        else:
            session.commit()
            report.row("APPLIED: transaction committed")

    report.dump()
    if report.failures:
        print(f"\n{len(report.failures)} parity failure(s)", file=sys.stderr)
        sys.exit(1)
    print("\nOK")


if __name__ == "__main__":
    main()
