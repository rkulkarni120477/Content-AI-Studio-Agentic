#!/usr/bin/env python
"""
Seed the Phase 9 prompt fragments into an existing database.

Fresh databases get the fragments from ``seed_data()``; already-seeded
databases (rehearsal, prod) predate them:

  * persona_tone — PERSONA_PREFIX_TEMPLATE, {single}->{{double}} converted
  * style_guide  — DEFAULT_STYLE_GUIDE verbatim

The constants stay in prompt_templates.py as the code fallback tier, and the
fragment tier only resolves with PROMPT_RESOLVE_BY_COMPONENT on — with the
seeded text identical to the constants, output is unchanged either way.

Usage:
  .venv/bin/python scripts/seed_prompt_fragments.py \
      --database-url "$(scripts/rehearsal_db.sh url)" --dry-run
  .venv/bin/python scripts/seed_prompt_fragments.py \
      --database-url "..." --apply       # add --allow-non-local for prod

Idempotent — existing fragment keys are skipped. Requires Alembic revision
>= 000100000006 (the fragment tables). Exits non-zero on verification failure.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

EXPECTED_KEYS = {"persona_tone", "style_guide"}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--database-url", required=True)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
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
        sys.exit("REFUSING: non-localhost DB. Pass --allow-non-local only "
                 "in a runbook window (MIGRATION_RUNBOOK.md).")

    engine = create_engine(args.database_url)
    db = sessionmaker(bind=engine)()

    from promptops_app.database import seed_prompt_fragments
    from promptops_app.prompts.brace_conversion import find_single_brace_vars
    from promptops_app.repositories.fragment_repository import (
        get_active_fragment_text,
        list_fragments,
    )

    failures: list[str] = []
    try:
        created = seed_prompt_fragments(db)
        for key in created:
            print(f"  created: {key}")
        if not created:
            print("  nothing to create — all fragment keys already present")

        keys = {f.fragment_key for f in list_fragments(db)}
        missing = EXPECTED_KEYS - keys
        if missing:
            failures.append(f"missing fragments after seeding: {sorted(missing)}")
        for key in EXPECTED_KEYS & keys:
            text = get_active_fragment_text(db, key)
            if not text:
                failures.append(f"{key}: no active version text")
            elif key in created and find_single_brace_vars(text):
                failures.append(f"{key}: seeded text has single-brace tokens")

        if failures:
            db.rollback()
            print("\nVERIFICATION FAILED — rolled back:")
            for f in failures:
                print(f"  ! {f}")
            sys.exit(1)

        if args.dry_run:
            db.rollback()
            print(f"\nDRY-RUN OK — {len(created)} fragment(s) would be created; rolled back.")
        else:
            db.commit()
            print(f"\nAPPLIED — {len(created)} fragment(s) created.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
