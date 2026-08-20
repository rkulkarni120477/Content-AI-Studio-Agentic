"""
Unit tests — resolve_fixed_prompt only honours locks it can actually follow.

Archiving a pipeline prompt (the Prompt Library's delete) can leave a scope lock
pointing at a row that generation must no longer use. Resolution has to treat
that lock as absent and keep looking outward, exactly as the loader and the
course-grouped view already do — otherwise a dead course lock masks a live
cluster lock and the course silently drops to the component default instead of
the prompt its cluster is locked to.
"""

from __future__ import annotations

import pytest

from promptops_app.database import Prompt, PromptFixing
from promptops_app.repositories.prompt_repository import resolve_fixed_prompt

COURSE_ID, CLUSTER_ID, PROJECT_ID = 9101, 9102, 9103


def _prompt(db, name, *, archived=False, variant=None):
    p = Prompt(name=name, prompt_kind="pipeline", component_type="cdd", variant=variant)
    if archived:
        from datetime import datetime
        p.deleted_at = datetime(2026, 1, 1)
    db.add(p)
    db.flush()
    return p


def _lock(db, prompt, scope_level, **ids):
    f = PromptFixing(component="cdd", scope_level=scope_level, prompt_id=prompt.id,
                     fixed_by="tester", fixed_by_role="admin", **ids)
    db.add(f)
    db.flush()
    return f


@pytest.fixture(autouse=True)
def _clean(db):
    yield
    db.rollback()


def test_live_lock_still_resolves(db):
    p = _prompt(db, "liveness_live")
    _lock(db, p, "course", course_id=COURSE_ID)

    fixing = resolve_fixed_prompt(db, "cdd", course_id=COURSE_ID)
    assert fixing is not None and fixing.prompt_id == p.id


def test_archived_course_lock_falls_through_to_the_live_cluster_lock(db):
    dead = _prompt(db, "liveness_dead", archived=True)
    live = _prompt(db, "liveness_cluster")
    _lock(db, dead, "course", course_id=COURSE_ID)
    _lock(db, live, "cluster", cluster_id=CLUSTER_ID)

    fixing = resolve_fixed_prompt(db, "cdd", course_id=COURSE_ID, cluster_id=CLUSTER_ID)
    assert fixing is not None and fixing.prompt_id == live.id


def test_archived_lock_with_no_broader_lock_resolves_to_nothing(db):
    """Caller then falls back to the component default — not to the dead row."""
    dead = _prompt(db, "liveness_only_dead", archived=True)
    _lock(db, dead, "global")

    assert resolve_fixed_prompt(db, "cdd", course_id=COURSE_ID) is None


def test_lock_whose_prompt_id_was_nulled_is_skipped(db):
    """PromptFixing.prompt_id is ON DELETE SET NULL — a nulled lock must not
    short-circuit resolution before broader scopes are consulted."""
    live = _prompt(db, "liveness_after_null")
    db.add(PromptFixing(component="cdd", scope_level="course", prompt_id=None,
                        course_id=COURSE_ID, fixed_by="tester", fixed_by_role="admin"))
    _lock(db, live, "project", project_id=PROJECT_ID)
    db.flush()

    fixing = resolve_fixed_prompt(db, "cdd", course_id=COURSE_ID, project_id=PROJECT_ID)
    assert fixing is not None and fixing.prompt_id == live.id


def test_variant_filtering_still_applies_alongside_liveness(db):
    """The variant rule is unchanged: an interactive-only request never takes a
    NULL-variant lock, even when that lock is live."""
    lesson = _prompt(db, "liveness_variant_lesson")
    _lock(db, lesson, "course", course_id=COURSE_ID)

    assert resolve_fixed_prompt(db, "cdd", course_id=COURSE_ID,
                                acceptable_variants=("interactive",)) is None
    assert resolve_fixed_prompt(db, "cdd", course_id=COURSE_ID,
                                acceptable_variants=(None,)) is not None
