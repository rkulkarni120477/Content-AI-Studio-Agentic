#!/usr/bin/env python
"""
Convert the seeded default_* pipeline rows' {single}-brace bodies to {{double}}.

The recorded enablement precondition for PROMPT_RESOLVE_BY_COMPONENT: the four
system-seeded default rows (default_style_prompt / default_cdd_prompt /
default_blueprint_prompt / default_generate_prompt) were seeded from legacy
.format()-style constants; flag-on they resolve for live generation but the
{{double}}-brace renderer would pass their {single} placeholders through as
literal text. This script converts every PromptVersion body of those rows in
place (and renames extra_instructions_block → extra_instructions, which the
routers actually supply — see brace_conversion.LEGACY_RENAMES).

Usage:
  # 1. dry-run against the rehearsal DB — prints the per-row plan, rolls back
  .venv/bin/python scripts/convert_seeded_prompt_braces.py \
      --database-url "$(scripts/rehearsal_db.sh url)" --dry-run

  # 2. apply (rehearsal / staging). For prod: enablement-window step, add
  #    --allow-non-local after the RDS snapshot per MIGRATION_RUNBOOK.md.
  .venv/bin/python scripts/convert_seeded_prompt_braces.py \
      --database-url "..." --apply

Idempotent: a second run finds nothing to convert. Aborts (rolls back) if any
targeted body contains an identifier not in the per-component expectation
below — unexpected drift means a human should look first. Rows whose names
don't exist are skipped with a warning (a DB seeded after the seed_data() fix
already has {{double}} bodies and simply reports zero conversions).

seed_data() itself now seeds converted bodies, so this script is only needed
for databases seeded before that fix (prod, rehearsal).

Exits non-zero on any verification failure.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from promptops_app.prompts.brace_conversion import (  # noqa: E402
    LEGACY_RENAMES,
    convert_legacy_braces,
    find_single_brace_vars,
)

# Per-row expectation: identifiers we may find and convert (verified against
# prod-parity rehearsal data, 2026-07-05 — 15 placeholders total). Anything
# outside this set aborts the run.
EXPECTED: dict[str, set[str]] = {
    "default_style_prompt": {"style_context"},
    "default_cdd_prompt": {
        "course_title", "target_audience", "expert_domain",
        "audience_level", "estimated_duration", "extra_instructions_block",
    },
    "default_blueprint_prompt": {
        "cdd_context", "selected_module", "extra_instructions_block",
    },
    "default_generate_prompt": {
        "lesson_topic", "lesson_title", "lesson_objective",
        "content_type", "context_injection",
    },
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--database-url", required=True)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true",
                      help="Run the full conversion + verification, then roll back")
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
        sys.exit(f"REFUSING: {host} is not localhost. Prod conversion is an "
                 f"enablement-window step (MIGRATION_RUNBOOK.md); pass "
                 f"--allow-non-local only after the RDS snapshot.")

    engine = create_engine(args.database_url)
    db = sessionmaker(bind=engine)()

    from promptops_app.database import Prompt, PromptVersion

    failures: list[str] = []
    total_converted = 0
    try:
        for name, expected in EXPECTED.items():
            prompt = (
                db.query(Prompt)
                .filter(Prompt.name == name, Prompt.prompt_kind == "pipeline")
                .first()
            )
            if prompt is None:
                print(f"  {name}: not present — skipped")
                continue

            versions = (
                db.query(PromptVersion)
                .filter(PromptVersion.prompt_id == prompt.id)
                .order_by(PromptVersion.id)
                .all()
            )
            for v in versions:
                found = set(find_single_brace_vars(v.system_prompt or "")) | \
                        set(find_single_brace_vars(v.user_prompt_template or ""))
                if not found:
                    print(f"  {name} {v.version}: already converted (0 tokens)")
                    continue
                unexpected = found - expected
                if unexpected:
                    failures.append(
                        f"{name} {v.version}: unexpected single-brace tokens "
                        f"{sorted(unexpected)} — refusing to convert blind"
                    )
                    continue

                new_sys, conv_s = convert_legacy_braces(v.system_prompt or "")
                new_usr, conv_u = convert_legacy_braces(v.user_prompt_template or "")
                v.system_prompt = new_sys or v.system_prompt
                v.user_prompt_template = new_usr or v.user_prompt_template
                renamed = sorted(n for n in found if n in LEGACY_RENAMES)
                total_converted += len(found)
                print(f"  {name} {v.version}: converted {sorted(found)}"
                      + (f" (renamed {renamed} → "
                         f"{[LEGACY_RENAMES[n] for n in renamed]})"
                         if renamed else ""))

                # Post-conversion verification on the pending state.
                residual = (find_single_brace_vars(v.system_prompt or "")
                            + find_single_brace_vars(v.user_prompt_template or ""))
                if residual:
                    failures.append(
                        f"{name} {v.version}: residual single tokens {residual}"
                    )
                body = (v.system_prompt or "") + (v.user_prompt_template or "")
                for tok in expected:
                    want = "{{" + LEGACY_RENAMES.get(tok, tok) + "}}"
                    if tok in found and want not in body:
                        failures.append(
                            f"{name} {v.version}: {want} missing after conversion"
                        )

        if failures:
            db.rollback()
            print("\nVERIFICATION FAILED — rolled back:")
            for f in failures:
                print(f"  ✗ {f}")
            sys.exit(1)

        if args.dry_run:
            db.rollback()
            print(f"\nDRY-RUN OK — {total_converted} placeholder(s) would be "
                  f"converted; rolled back.")
        else:
            db.commit()
            print(f"\nAPPLIED — {total_converted} placeholder(s) converted.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
