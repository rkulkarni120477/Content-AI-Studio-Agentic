"""Repair stored content whose markdown emphasis delimiters do not pair up.

Why a script and not a migration: the damage is content, not schema, and the
repair has to be *seen* before it is applied. ``--apply`` is opt-in and the
default is a dry run that prints every line it would change, old and new.

What it fixes is only the unambiguous shape — a lone ``*`` opening a line's
content that is closed by ``**``, which is the residue of an item-regeneration
bullet strip that read the first ``*`` of ``**Label:**`` as a bullet. Anything
else is listed and left alone; see promptops_app/parsers/markdown_emphasis.py.

The corruption is sticky, which is why old versions are worth repairing rather
than only the active one: a save preserves untouched lines byte for byte, so a
malformed label rides forward into every later version of the document, and any
future edit branched from an old version reintroduces it.

Both the markdown body and the parsed ``sections`` JSON hold the same text, and
both are repaired in one transaction per row — repairing one without the other
would leave a reader showing the broken copy depending on which it prefers.

    .venv/bin/python scripts/repair_markdown_emphasis.py            # dry run
    .venv/bin/python scripts/repair_markdown_emphasis.py --apply
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Run as a script, so the repo root is not on sys.path the way it is for a test
# or an app import. Matches scripts/seed_db.py and scripts/create_admin.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from promptops_app.database import SessionLocal  # noqa: E402
from promptops_app.parsers.markdown_emphasis import repair_emphasis  # noqa: E402

#: (table, id column, markdown column, sections JSON column or None)
TARGETS = [
    ("cdd_versions", "id", "full_content", "sections"),
    ("blueprint_versions", "id", "full_content", "sections"),
    ("block_versions", "id", "content", None),
    ("blocks", "id", "content", None),
    ("blocks", "id", "draft_content", None),
]


def _repair_sections(blob):
    """Repair each string value in a sections JSON blob.

    Returns ``(new_blob_or_None, findings)``. Non-string values and unparseable
    blobs are left exactly as found — this repairs text, it does not normalise
    JSON, so a row it cannot parse is reported rather than rewritten.
    """
    if not blob:
        return None, []
    try:
        parsed = json.loads(blob) if isinstance(blob, str) else blob
    except (TypeError, ValueError):
        return None, []
    if not isinstance(parsed, dict):
        return None, []

    findings, changed = [], False
    out = dict(parsed)
    for key, value in parsed.items():
        if not isinstance(value, str):
            continue
        fixed, found = repair_emphasis(value)
        for f in found:
            findings.append((key, f))
        if fixed != value:
            out[key] = fixed
            changed = True
    return (json.dumps(out) if changed else None), findings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="write the repairs; without it nothing is modified")
    args = ap.parse_args()

    session = SessionLocal()
    repaired = skipped = 0
    try:
        for table, id_col, md_col, json_col in TARGETS:
            cols = f"{id_col}, {md_col}" + (f", {json_col}" if json_col else "")
            rows = session.execute(text(
                f"SELECT {cols} FROM {table} WHERE {md_col} IS NOT NULL"
            )).fetchall()

            for row in rows:
                row_id, body = row[0], row[1]
                blob = row[2] if json_col else None

                new_body, body_findings = repair_emphasis(body or "")
                new_blob, blob_findings = _repair_sections(blob)

                if not body_findings and not blob_findings:
                    continue

                print(f"\n{table}#{row_id}")
                new_lines = new_body.split("\n")
                for f in body_findings:
                    print(f"  {md_col} line {f.line_number}:")
                    print(f"      old {f.text.strip()[:110]!r}")
                    if f.repairable:
                        print(f"      new {new_lines[f.line_number - 1].strip()[:110]!r}")
                    else:
                        print("      ..  left as-is — the repair would be a guess")
                for key, f in blob_findings:
                    state = "repairable" if f.repairable else "left as-is"
                    print(f"  {json_col}[{key}] line {f.line_number} ({state}): "
                          f"{f.text.strip()[:90]!r}")

                unrepairable = ([f for f in body_findings if not f.repairable]
                                + [f for _, f in blob_findings if not f.repairable])
                if unrepairable:
                    skipped += len(unrepairable)

                if not args.apply:
                    continue

                sets, params = [], {"rid": row_id}
                if new_body != (body or ""):
                    sets.append(f"{md_col} = :body")
                    params["body"] = new_body
                if new_blob is not None:
                    sets.append(f"{json_col} = :blob")
                    params["blob"] = new_blob
                if not sets:
                    continue
                session.execute(text(
                    f"UPDATE {table} SET {', '.join(sets)} WHERE {id_col} = :rid"
                ), params)
                repaired += 1

        if args.apply:
            session.commit()
            print(f"\nApplied: {repaired} row(s) repaired, "
                  f"{skipped} ambiguous line(s) left alone.")
        else:
            print(f"\nDry run — nothing written. {skipped} ambiguous line(s) would be "
                  f"left alone. Re-run with --apply to write the repairs.")
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
