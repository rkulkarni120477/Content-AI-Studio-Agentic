#!/usr/bin/env python
"""
Seed the missing taxonomy default prompt rows into an existing database.

Phase 8 taxonomy seeds (signed off 2026-07-07): fresh databases get the full
default set from ``seed_data()``, but already-seeded databases (rehearsal,
prod) predate the three new rows —

  * default_blueprint_teacher_prompt  (blueprint, teacher)  — from the
    TEACHER_BLUEPRINT_* constants, {single}->{{double}} converted at seed time
  * default_blueprint_student_prompt  (blueprint, student)  — from the
    BLUEPRINT_* constants, converted likewise
  * default_quiz_prompt               (quiz, NULL)          — verbatim from
    templates/quiz_generation.md (already {{double}})

This script calls the same ``seed_default_component_prompts()`` the seeder
uses, so it is idempotent: rows whose names exist are skipped, a second run
creates nothing. Exact (component, variant) rows win over the NULL-variant
fallback, and the seeded text equals what the legacy tiers serve today, so
flag-on output is unchanged and flag-off ignores the rows entirely.

Usage:
  .venv/bin/python scripts/seed_taxonomy_defaults.py \
      --database-url "$(scripts/rehearsal_db.sh url)" --dry-run   # plan only
  .venv/bin/python scripts/seed_taxonomy_defaults.py \
      --database-url "..." --apply                                 # commit

For prod add --allow-non-local (runbook window; RDS snapshot/PITR first).
Exits non-zero on any verification failure.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

EXPECTED_DEFAULTS = {
    ("style", None), ("cdd", None), ("blueprint", None), ("generate", None),
    ("blueprint", "teacher"), ("blueprint", "student"), ("quiz", None),
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--database-url", required=True)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true",
                      help="Run the seeding + verification, then roll back")
    mode.add_argument("--apply", action="store_true")
    ap.add_argument("--allow-non-local", action="store_true",
                    help="Required to touch any non-localhost DB (i.e. prod)")
    args = ap.parse_args()

    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url
    from sqlalchemy.orm import sessionmaker

    url = make_url(args.database_url)
    host = url.host or "localhost"
    local = host in ("localhost", "127.0.0.1", "::1")
    print(f"Target: host={host} db={url.database} "
          f"mode={'DRY-RUN' if args.dry_run else 'APPLY'}")
    if not local and not args.allow_non_local:
        sys.exit(f"REFUSING: {host} is not localhost. Prod seeding is a "
                 f"runbook-window step (MIGRATION_RUNBOOK.md); pass "
                 f"--allow-non-local only after the RDS snapshot.")

    engine = create_engine(args.database_url)
    db = sessionmaker(bind=engine)()

    from promptops_app.database import (
        Prompt,
        PromptVersion,
        seed_default_component_prompts,
    )
    from promptops_app.prompts.brace_conversion import find_single_brace_vars

    failures: list[str] = []
    try:
        created = seed_default_component_prompts(db)
        for name in created:
            print(f"  created: {name}")
        if not created:
            print("  nothing to create — all default rows already present")

        # Verify: one is_default row per expected taxonomy line, each with an
        # active v1 whose body carries no legacy {single}-brace tokens.
        defaults = (
            db.query(Prompt)
            .filter(Prompt.is_default.is_(True), Prompt.prompt_kind == "pipeline")
            .all()
        )
        lines = {(p.component_type, p.variant) for p in defaults}
        missing = EXPECTED_DEFAULTS - lines
        if missing:
            failures.append(f"missing default lines after seeding: {sorted(missing, key=str)}")
        for p in defaults:
            active = (
                db.query(PromptVersion)
                .filter(PromptVersion.prompt_id == p.id,
                        PromptVersion.is_active.is_(True))
                .one_or_none()
            )
            if active is None:
                failures.append(f"{p.name}: no active version")
                continue
            stray = (find_single_brace_vars(active.system_prompt or "")
                     + find_single_brace_vars(active.user_prompt_template or ""))
            if p.name in created and stray:
                failures.append(f"{p.name}: seeded body has single-brace tokens {stray}")

        if failures:
            db.rollback()
            print("\nVERIFICATION FAILED — rolled back:")
            for f in failures:
                print(f"  ! {f}")
            sys.exit(1)

        if args.dry_run:
            db.rollback()
            print(f"\nDRY-RUN OK — {len(created)} row(s) would be created; rolled back.")
        else:
            db.commit()
            print(f"\nAPPLIED — {len(created)} row(s) created.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
