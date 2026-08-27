"""No module may reference a name that does not exist.

A NameError in a rarely-taken branch is invisible until the day that branch runs,
and the branches that run rarest are error handlers — the code that exists
precisely for when things are already going wrong.

That is not hypothetical. `_attach_indexed_units` degrades the Source Library to
"unknown" when the vector store cannot be queried, and its handler called
`log.warning` in a module that defines `logger`. In every environment with a
working index that line is dead code, so the whole suite passed. Then a
deployment was pointed at an OpenSearch index that did not exist yet, every
listing request reached the handler, and the endpoint answered
`{"detail":"Internal error","type":"NameError"}` — the fail-soft path taking the
service down instead of keeping it up.

Python cannot catch this at import time; the name is resolved only when the line
executes. A static pass can, in about a second.

Uses ruff's F821, which is already a dev dependency and already configured in
pyproject (`select = ["F", ...]`) — a guard that needs a new tool installed is a
guard that quietly stops running.

Whole-package rather than per-module on purpose: the value is entirely in the
modules nobody thought to check.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Scanned paths. Tests are excluded deliberately — fixtures legitimately
#: introduce names a static pass cannot see, and a false failure here would train
#: people to ignore this check.
TARGETS = ["services", "api", "config", "storage", "main.py"]


def _ruff(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "ruff", *args],
        capture_output=True, text=True, cwd=str(ROOT),
    )


def test_the_check_actually_runs():
    """Guard the guard. If ruff is missing, the test below reports zero offences
    and passes — announcing a check the suite is not performing, which is worse
    than having no check at all."""
    assert _ruff("--version").returncode == 0, (
        "ruff is not runnable, so the undefined-name check below is inert. "
        "Install the dev extra: pip install -e '.[dev]'")


def test_no_module_references_an_undefined_name():
    targets = [p for p in TARGETS if (ROOT / p).exists()]
    result = _ruff("check", "--select", "F821", "--no-cache",
                   "--output-format", "concise", *targets)
    offences = [line for line in result.stdout.splitlines() if "F821" in line]
    assert not offences, (
        "these names do not exist and will raise NameError the moment the line "
        "runs — which, for an error handler, means the first time something else "
        "goes wrong:\n  " + "\n  ".join(offences))
