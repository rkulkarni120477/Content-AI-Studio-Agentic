"""Tests for editor_builder + the import job (Session 3 — reconstruction).

Uses the shared in-memory SQLite ``db`` fixture (tests/conftest.py). Reconstruction
must produce the *same rows the scratch pipeline produces* — CourseModule +
Generation + Block — so the Editor renders imported courses with zero changes.
"""

from __future__ import annotations

import json
import tempfile

from sqlalchemy.orm import sessionmaker

from promptops_app.core.constants import ChangeSource
from promptops_app.database import (
    Block,
    BlockVersion,
    Cluster,
    Course,
    CourseImport,
    CourseModule,
    Generation,
    GenerationJob,
)
from promptops_app.importers import editor_builder, provenance
from promptops_app.importers.imscc_importer import parse_package
from tests.importers.fixtures import build_content_imscc


def _new_course(db, name="Imported", project_id=1, cluster_id=None) -> Course:
    course = Course(name=name, project_id=project_id, cluster_id=cluster_id)
    db.add(course)
    db.commit()
    db.refresh(course)
    return course


def test_build_creates_modules_generations_blocks(db):
    course = _new_course(db)
    icourse = parse_package(build_content_imscc())

    result = editor_builder.build(
        db, icourse, course_id=course.id, project_id=1, import_id=42, user_name="u",
    )

    assert result.modules_created == 2
    assert result.blocks_created == 3          # Intro + Quiz 1 + Wrap Up

    mods = (
        db.query(CourseModule)
        .filter_by(course_id=course.id)
        .order_by(CourseModule.position)
        .all()
    )
    assert [m.title for m in mods] == ["Module A", "Module B"]

    gens = db.query(Generation).filter_by(course_id=course.id).all()
    assert len(gens) == 3                       # one generation per item
    assert all(g.prompt_name == "import" for g in gens)
    assert {g.topic for g in gens} == {"Intro", "Quiz 1", "Wrap Up"}

    blocks = db.query(Block).filter(Block.generation_id.in_([g.id for g in gens])).all()
    assert len(blocks) == 3
    assert all(b.workflow_state == "draft" for b in blocks)
    assert all(b.module_id is not None for b in blocks)
    assert all(b.block_type in {"lesson", "quiz", "assignment", "discussion"} for b in blocks)

    for block in blocks:
        versions = (
            db.query(BlockVersion)
            .filter(BlockVersion.block_id == block.id)
            .order_by(BlockVersion.version_num)
            .all()
        )
        assert len(versions) == 1
        assert versions[0].version_num == 1
        assert versions[0].change_source == ChangeSource.IMPORT
        assert versions[0].change_note == "Initial version"
        assert versions[0].content == (block.content or "")


def test_block_types_content_and_order(db):
    course = _new_course(db)
    editor_builder.build(
        db, parse_package(build_content_imscc()),
        course_id=course.id, project_id=1, import_id=42, user_name="u",
    )

    mod_a = (
        db.query(CourseModule)
        .filter_by(course_id=course.id)
        .order_by(CourseModule.position)
        .first()
    )
    ordered = db.query(Block).filter_by(module_id=mod_a.id).order_by(Block.position).all()
    assert [b.block_label for b in ordered] == ["Intro", "Quiz 1"]
    assert [b.position for b in ordered] == [0, 1]

    intro, quiz = ordered
    assert intro.block_type == "lesson"
    assert "Welcome to the course" in (intro.content or "")
    assert intro.content_html                    # original body HTML kept as fidelity fallback
    assert quiz.block_type == "quiz"
    assert "**Correct Answer:** B" in (quiz.content or "")


def test_provenance_rows_map_canvas_ids_to_entities(db):
    course = _new_course(db)
    editor_builder.build(
        db, parse_package(build_content_imscc()),
        course_id=course.id, project_id=1, import_id=77, user_name="u",
    )

    rows = provenance.list_for_import(db, 77)
    assert sum(1 for r in rows if r.canvas_type == "module") == 2
    assert sum(1 for r in rows if r.canvas_type == "page") == 2
    assert any(r.canvas_type == "quiz" for r in rows)

    block_ids = {b.id for b in db.query(Block).all()}
    module_ids = {m.id for m in db.query(CourseModule).all()}
    for r in rows:
        if r.cas_entity_type == "block":
            assert r.cas_entity_id in block_ids
        else:
            assert r.cas_entity_id in module_ids


def test_block_identifier_map_backs_provenance_export(db):
    # The map {block_id: canvas_identifier} is what provenance-aware export reads.
    course = _new_course(db)
    editor_builder.build(
        db, parse_package(build_content_imscc()),
        course_id=course.id, project_id=1, import_id=88, user_name="u",
    )
    id_map = provenance.block_identifier_map(db, 88)

    block_ids = {b.id for b in db.query(Block).all()}
    assert id_map                                   # non-empty
    assert set(id_map) <= block_ids                 # keys are real block ids
    assert all(v for v in id_map.values())          # every mapped id is non-empty


def test_run_import_job_end_to_end(db, monkeypatch, mock_llm):
    # mock_llm keeps the reverse-gen stages (Blueprint/CDD) offline + deterministic.
    from promptops_app.jobs import import_jobs
    from promptops_app.repositories import job_repository

    # The job opens its own SessionLocal — point it at the test engine so it
    # writes to the same in-memory DB the fixture reads.
    factory = sessionmaker(bind=db.get_bind(), autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr("promptops_app.jobs.import_jobs.SessionLocal", factory)

    course = _new_course(db, name="ViaJob", project_id=2)
    course_id = course.id
    # start_import (imports.py) creates the shell invisible — mirrored here
    # since this test drives the job directly, bypassing the router.
    course.is_active = False
    db.commit()
    ci = CourseImport(course_id=course_id, project_id=2, status="queued", package_name="x.imscc")
    db.add(ci)
    db.commit()
    db.refresh(ci)
    import_id = ci.id

    fd, pkg_path = tempfile.mkstemp(prefix="test_imscc_", suffix=".imscc")
    import os
    with os.fdopen(fd, "wb") as handle:
        handle.write(build_content_imscc())

    job_id = job_repository.create_job(
        db,
        user_name="u",
        request_params={
            "import_id": import_id, "course_id": course_id, "project_id": 2,
            "user_name": "u", "package_path": pkg_path, "package_name": "x.imscc",
        },
        project_id=2, course_id=course_id, job_type="import",
    )

    import_jobs.run_import_job(job_id)

    db.expire_all()
    job = db.query(GenerationJob).filter_by(id=job_id).first()
    assert job.status == "completed"
    assert job.result_entity_id == course_id      # result entity is the course
    assert job.progress == 100

    course = db.query(Course).filter_by(id=course_id).first()
    assert course.source_type == "imscc"
    assert course.import_id == import_id
    assert course.is_active is True   # unhidden the moment reconstruction succeeded

    record = db.query(CourseImport).filter_by(id=import_id).first()
    assert record.status == "completed"
    assert json.loads(record.structure_counts_json)["pages"] == 2

    gens = db.query(Generation).filter_by(course_id=course_id).all()
    blocks = db.query(Block).filter(Block.generation_id.in_([g.id for g in gens])).all()
    assert len(blocks) == 3
    assert not os.path.isfile(pkg_path)           # staged package cleaned up

    # Reverse-gen (stages 5–6) populated + pinned Blueprint and CDD.
    from promptops_app.database import CourseDesignDocument, ModuleBlueprint
    assert course.active_blueprint_id is not None
    assert course.active_cdd_id is not None
    assert db.query(ModuleBlueprint).filter_by(course_id=course_id).count() == 2
    assert db.query(CourseDesignDocument).filter_by(course_id=course_id).count() == 1


def test_cancel_mid_run_purges_the_course_entirely(db, monkeypatch):
    """The Cancel Import button: is_import_cancelled is checked at every stage
    boundary and inside the per-module progress callback, so a cancel lands
    even deep into reconstruction — not just before the job starts. Unlike a
    failed import (hidden but kept for its audit trail), a cancelled one is
    purged outright: the user asked for it to not exist."""
    from promptops_app.jobs import import_jobs
    from promptops_app.repositories import job_repository

    factory = sessionmaker(bind=db.get_bind(), autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr("promptops_app.jobs.import_jobs.SessionLocal", factory)
    monkeypatch.setattr("promptops_app.jobs.import_jobs.is_import_cancelled", lambda db, job_id: True)

    course = _new_course(db, name="Cancelled", project_id=4)
    course_id = course.id
    course.is_active = False
    db.commit()
    ci = CourseImport(course_id=course_id, project_id=4, status="queued", package_name="x.imscc")
    db.add(ci)
    db.commit()
    db.refresh(ci)
    import_id = ci.id

    fd, pkg_path = tempfile.mkstemp(prefix="test_imscc_", suffix=".imscc")
    import os
    with os.fdopen(fd, "wb") as handle:
        handle.write(build_content_imscc())

    job_id = job_repository.create_job(
        db,
        user_name="u",
        request_params={
            "import_id": import_id, "course_id": course_id, "project_id": 4,
            "user_name": "u", "package_path": pkg_path, "package_name": "x.imscc",
        },
        project_id=4, course_id=course_id, job_type="import",
    )

    import_jobs.run_import_job(job_id)

    db.expire_all()
    assert db.query(Course).filter_by(id=course_id).first() is None
    assert db.query(CourseImport).filter_by(id=import_id).first() is None
    assert db.query(GenerationJob).filter_by(id=job_id).first() is None
    assert not os.path.isfile(pkg_path)   # staged package still cleaned up


def test_cancel_landing_right_after_reconstruction_still_purges(db, monkeypatch):
    """Caught live: a real package can finish reconstruction (CPU-only, no
    LLM calls) in a couple seconds -- faster than the user's own Cancel
    click round-trips to the server. build() returning didn't re-check
    cancellation, so the course got unhidden with 21 modules / 155
    generations and the job just sat there reporting status=cancelled while
    the course stayed fully visible and "Imported". This reproduces that
    exact race: build() succeeds, and only THEN does the job get marked
    cancelled (simulating the two requests landing in that order) -- the
    check right after build() must still catch it and purge."""
    from promptops_app.jobs import import_jobs
    from promptops_app.jobs.job_status import set_cancelled
    from promptops_app.repositories import job_repository

    factory = sessionmaker(bind=db.get_bind(), autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr("promptops_app.jobs.import_jobs.SessionLocal", factory)

    course = _new_course(db, name="RaceCancelled", project_id=5)
    course_id = course.id
    course.is_active = False
    db.commit()
    ci = CourseImport(course_id=course_id, project_id=5, status="queued", package_name="x.imscc")
    db.add(ci)
    db.commit()
    db.refresh(ci)
    import_id = ci.id

    fd, pkg_path = tempfile.mkstemp(prefix="test_imscc_", suffix=".imscc")
    import os
    with os.fdopen(fd, "wb") as handle:
        handle.write(build_content_imscc())

    job_id = job_repository.create_job(
        db,
        user_name="u",
        request_params={
            "import_id": import_id, "course_id": course_id, "project_id": 5,
            "user_name": "u", "package_path": pkg_path, "package_name": "x.imscc",
        },
        project_id=5, course_id=course_id, job_type="import",
    )

    real_build = editor_builder.build

    def build_then_cancel(db_, *a, **kw):
        result = real_build(db_, *a, **kw)
        job = db_.query(GenerationJob).filter_by(id=job_id).first()
        set_cancelled(db_, job)   # the cancel request "lands" right here
        return result

    monkeypatch.setattr(import_jobs.editor_builder, "build", build_then_cancel)

    import_jobs.run_import_job(job_id)

    db.expire_all()
    assert db.query(Course).filter_by(id=course_id).first() is None
    assert db.query(CourseModule).filter_by(course_id=course_id).count() == 0
    assert db.query(Generation).filter_by(course_id=course_id).count() == 0
    assert db.query(CourseImport).filter_by(id=import_id).first() is None
    assert db.query(GenerationJob).filter_by(id=job_id).first() is None


def test_cancel_during_blueprints_leaves_no_orphans(db, monkeypatch):
    """Caught live on cas-dev: the cancel landed during Blueprint generation (one
    long LLM loop), so the course stayed visible for minutes. Now the cancel
    endpoint purges immediately; the worker, still inside build_blueprints,
    then writes a blueprint into the deleted course (ModuleBlueprint.course_id
    has no FK, so nothing stops it). The worker must stop at its next check
    (a deleted job reads as cancelled) and its purge must sweep that orphan
    even though the course row is already gone."""
    from types import SimpleNamespace

    from promptops_app.database import ModuleBlueprint
    from promptops_app.jobs import import_jobs
    from promptops_app.jobs.job_status import set_cancelled
    from promptops_app.repositories import course_repository, job_repository

    factory = sessionmaker(bind=db.get_bind(), autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr("promptops_app.jobs.import_jobs.SessionLocal", factory)

    course = _new_course(db, name="CancelMidBlueprint", project_id=6)
    course_id = course.id
    course.is_active = False
    db.commit()
    ci = CourseImport(course_id=course_id, project_id=6, status="queued", package_name="x.imscc")
    db.add(ci)
    db.commit()
    db.refresh(ci)
    import_id = ci.id

    fd, pkg_path = tempfile.mkstemp(prefix="test_imscc_", suffix=".imscc")
    import os
    with os.fdopen(fd, "wb") as handle:
        handle.write(build_content_imscc())

    job_id = job_repository.create_job(
        db,
        user_name="u",
        request_params={
            "import_id": import_id, "course_id": course_id, "project_id": 6,
            "user_name": "u", "package_path": pkg_path, "package_name": "x.imscc",
        },
        project_id=6, course_id=course_id, job_type="import",
    )

    def blueprints_then_endpoint_cancel(db_, **kw):
        # What cancel_import now does, landing mid-Blueprint...
        set_cancelled(db_, db_.query(GenerationJob).filter_by(id=job_id).first())
        course_repository.purge_course(db_, course_id)
        # ...then the worker's own in-flight write, into the now-deleted course.
        db_.add(ModuleBlueprint(title="late", module_title="late", course_id=course_id))
        db_.commit()
        return SimpleNamespace(warnings=[], blueprint_ids=[])

    monkeypatch.setattr(import_jobs.reverse_blueprint, "build_blueprints", blueprints_then_endpoint_cancel)

    import_jobs.run_import_job(job_id)

    db.expire_all()
    assert db.query(Course).filter_by(id=course_id).first() is None
    assert db.query(ModuleBlueprint).filter_by(course_id=course_id).count() == 0
    assert db.query(CourseModule).filter_by(course_id=course_id).count() == 0
    assert db.query(Generation).filter_by(course_id=course_id).count() == 0


def test_late_purge_during_finalize_is_treated_as_cancel_not_a_crash(db, monkeypatch):
    """PR #187 review: no is_import_cancelled() check covers the window
    between the last one (right after _reverse_generate) and
    _finalize/set_completed. A purge landing there deletes both the course
    and this job row out from under an already-expired ORM object, so the
    next write raises ObjectDeletedError/StaleDataError instead of the
    checked _ImportCancelled -- and used to propagate out of the job instead
    of being treated as the cancel it actually is."""
    from promptops_app.jobs import import_jobs
    from promptops_app.jobs.job_status import set_cancelled
    from promptops_app.repositories import course_repository, job_repository

    factory = sessionmaker(bind=db.get_bind(), autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr("promptops_app.jobs.import_jobs.SessionLocal", factory)

    course = _new_course(db, name="LatePurgeRace", project_id=7)
    course_id = course.id
    course.is_active = False
    db.commit()
    ci = CourseImport(course_id=course_id, project_id=7, status="queued", package_name="x.imscc")
    db.add(ci)
    db.commit()
    db.refresh(ci)
    import_id = ci.id

    fd, pkg_path = tempfile.mkstemp(prefix="test_imscc_", suffix=".imscc")
    import os
    with os.fdopen(fd, "wb") as handle:
        handle.write(build_content_imscc())

    job_id = job_repository.create_job(
        db,
        user_name="u",
        request_params={
            "import_id": import_id, "course_id": course_id, "project_id": 7,
            "user_name": "u", "package_path": pkg_path, "package_name": "x.imscc",
        },
        project_id=7, course_id=course_id, job_type="import",
    )

    real_finalize = import_jobs._finalize

    def finalize_after_late_endpoint_purge(db_, course_id_, course_import_, import_id_, result_):
        # What the cancel endpoint now does, landing after the last checked
        # is_import_cancelled() but before this function commits anything.
        set_cancelled(db_, db_.query(GenerationJob).filter_by(id=job_id).first())
        course_repository.purge_course(db_, course_id_)
        real_finalize(db_, course_id_, course_import_, import_id_, result_)

    monkeypatch.setattr(import_jobs, "_finalize", finalize_after_late_endpoint_purge)

    import_jobs.run_import_job(job_id)   # must not raise

    db.expire_all()
    assert db.query(Course).filter_by(id=course_id).first() is None
    assert db.query(CourseImport).filter_by(id=import_id).first() is None
    assert db.query(GenerationJob).filter_by(id=job_id).first() is None


def test_failed_import_job_hides_the_empty_course_shell(db, monkeypatch):
    """A course row is created eagerly (API layer), invisible from the start
    (is_active=False — see start_import), so the user can watch progress
    without the empty shell ever being badged "Imported" in the Titles list.
    If the job then fails outright, it stays exactly as invisible as it
    started — this test proves that path too, not just "nothing changed it".

    Asserted against list_courses_for_cluster(..., include_archived=True) —
    the exact call CoursesPage makes (dashboardThunks.js fetchCoursesThunk) —
    not list_courses_for_project, which no page actually calls with archived
    items included. is_active=False alone is not enough here: the Titles page
    deliberately shows archived courses (for permanent-delete management), so
    a failed-import shell needs its own exclusion, not just the archive flag.
    """
    from promptops_app.jobs import import_jobs
    from promptops_app.repositories import job_repository
    from promptops_app.repositories.course_repository import list_courses_for_cluster

    factory = sessionmaker(bind=db.get_bind(), autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr("promptops_app.jobs.import_jobs.SessionLocal", factory)

    cluster = Cluster(name="C", project_id=3)
    db.add(cluster)
    db.commit()
    db.refresh(cluster)

    course = _new_course(db, name="WillFail", project_id=3, cluster_id=cluster.id)
    course_id = course.id
    ci = CourseImport(course_id=course_id, project_id=3, status="queued", package_name="x.imscc")
    db.add(ci)
    db.commit()
    db.refresh(ci)
    import_id = ci.id
    # start_import (imports.py) sets these eagerly, before the job is even
    # enqueued — mirrored here since this test drives the job directly.
    course.import_id = import_id
    course.is_active = False
    db.commit()

    job_id = job_repository.create_job(
        db,
        user_name="u",
        request_params={
            # No file staged at this path — _read_package raises
            # PackageValidationError, matching a real "package no longer
            # available on the server" failure.
            "import_id": import_id, "course_id": course_id, "project_id": 3,
            "user_name": "u", "package_path": None, "package_name": "x.imscc",
        },
        project_id=3, course_id=course_id, job_type="import",
    )

    import_jobs.run_import_job(job_id)

    db.expire_all()
    job = db.query(GenerationJob).filter_by(id=job_id).first()
    assert job.status == "failed"

    record = db.query(CourseImport).filter_by(id=import_id).first()
    assert record.status == "failed"

    course = db.query(Course).filter_by(id=course_id).first()
    assert course.is_active is False

    visible_ids = {c.id for c in list_courses_for_cluster(db, cluster.id, include_archived=True)}
    assert course_id not in visible_ids


def test_the_title_is_not_shown_until_the_job_has_actually_run(db):
    """The reported bug: a tester blocked the import request in DevTools and
    the title still showed up as "Imported" with no content. Whether or not
    THAT specific request reaches the backend, run_import_job never even
    starting — a crashed worker, a lost dispatch, the process dying between
    the course commit and the job being enqueued — must not leave a visible,
    content-less "Imported" title either. Simulates exactly that: the course
    + import + job rows exist (mirroring start_import), but the job has never
    been run at all — no failure, no success, just never-happened."""
    from promptops_app.repositories import job_repository
    from promptops_app.repositories.course_repository import list_courses_for_cluster

    cluster = Cluster(name="C", project_id=9)
    db.add(cluster)
    db.commit()
    db.refresh(cluster)

    course = _new_course(db, name="JobNeverRan", project_id=9, cluster_id=cluster.id)
    course_id = course.id
    ci = CourseImport(course_id=course_id, project_id=9, status="queued", package_name="x.imscc")
    db.add(ci)
    db.commit()
    db.refresh(ci)
    # The exact sequence start_import commits before dispatch.submit() is even
    # called — this is the state the row sits in for however long the job
    # takes to actually start running.
    course.import_id = ci.id
    course.is_active = False
    db.commit()
    job_repository.create_job(
        db, user_name="u",
        request_params={
            "import_id": ci.id, "course_id": course_id, "project_id": 9,
            "user_name": "u", "package_path": "/nonexistent", "package_name": "x.imscc",
        },
        project_id=9, course_id=course_id, job_type="import",
    )
    # No import_jobs.run_import_job(job_id) call — the job is never run.

    assert db.query(Generation).filter_by(course_id=course_id).count() == 0
    visible_ids = {c.id for c in list_courses_for_cluster(db, cluster.id, include_archived=True)}
    assert course_id not in visible_ids


def test_import_job_failing_after_reconstruction_leaves_the_course_untouched(db, monkeypatch, mock_llm):
    """Review finding: the outer except also catches failures AFTER
    editor_builder.build already committed real content (e.g. a transient DB
    error in _finalize/set_completed). That course must stay fully visible
    and usable — it must not be archived alongside a genuinely empty shell
    just because something failed somewhere in the same try block."""
    from promptops_app.jobs import import_jobs
    from promptops_app.repositories import job_repository
    from promptops_app.repositories.course_repository import list_courses_for_cluster

    factory = sessionmaker(bind=db.get_bind(), autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr("promptops_app.jobs.import_jobs.SessionLocal", factory)
    monkeypatch.setattr(
        import_jobs, "_finalize",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("transient DB error")),
    )

    cluster = Cluster(name="C", project_id=4)
    db.add(cluster)
    db.commit()
    db.refresh(cluster)

    course = _new_course(db, name="ReconstructedThenFails", project_id=4, cluster_id=cluster.id)
    course_id = course.id
    ci = CourseImport(course_id=course_id, project_id=4, status="queued", package_name="x.imscc")
    db.add(ci)
    db.commit()
    db.refresh(ci)
    import_id = ci.id
    # start_import (imports.py) creates the shell invisible; only reconstruction
    # succeeding unhides it. Starting True here would let this test pass even
    # without that unhide step actually running.
    course.import_id = import_id
    course.is_active = False
    db.commit()

    import os
    fd, pkg_path = tempfile.mkstemp(prefix="test_imscc_", suffix=".imscc")
    with os.fdopen(fd, "wb") as handle:
        handle.write(build_content_imscc())

    job_id = job_repository.create_job(
        db,
        user_name="u",
        request_params={
            "import_id": import_id, "course_id": course_id, "project_id": 4,
            "user_name": "u", "package_path": pkg_path, "package_name": "x.imscc",
        },
        project_id=4, course_id=course_id, job_type="import",
    )

    import_jobs.run_import_job(job_id)

    db.expire_all()
    job = db.query(GenerationJob).filter_by(id=job_id).first()
    assert job.status == "failed"   # _finalize did blow up — the job itself must say so

    course = db.query(Course).filter_by(id=course_id).first()
    assert course.is_active is True   # reconstruction unhid it; the later failure must not re-hide it

    gens = db.query(Generation).filter_by(course_id=course_id).all()
    assert len(gens) == 3   # reconstruction really did complete before the failure

    visible_ids = {c.id for c in list_courses_for_cluster(db, cluster.id, include_archived=True)}
    assert course_id in visible_ids


def test_archiving_a_reconstructed_course_does_not_hide_it_alongside_empty_shells(db):
    """Review finding (over-fire): a course that DID reconstruct (real
    content) before a later stage failed keeps CourseImport.status=="failed"
    forever. If the user later archives that course through the ordinary
    archive action (only is_active changes), a status-keyed hiding rule would
    wrongly re-catch it the moment it's archived — burying real content
    behind a page it can no longer even be purged from. The content-based
    rule (course_repository._is_empty_import_shell) must not care about
    CourseImport.status at all."""
    from promptops_app.repositories.course_repository import list_courses_for_cluster

    cluster = Cluster(name="C", project_id=6)
    db.add(cluster)
    db.commit()
    db.refresh(cluster)

    course = _new_course(db, name="ReconstructedThenArchived", project_id=6, cluster_id=cluster.id)
    course_id = course.id
    editor_builder.build(
        db, parse_package(build_content_imscc()),
        course_id=course_id, project_id=6, import_id=99, user_name="u",
    )
    ci = CourseImport(course_id=course_id, project_id=6, status="failed", package_name="x.imscc")
    db.add(ci)
    db.commit()
    db.refresh(ci)
    course = db.query(Course).filter_by(id=course_id).first()
    course.import_id = ci.id
    db.commit()

    # The ordinary archive action — same field the courses.py archive
    # endpoint flips, nothing about CourseImport touched.
    course = db.query(Course).filter_by(id=course_id).first()
    course.is_active = False
    db.commit()

    assert db.query(Generation).filter_by(course_id=course_id).count() == 3

    visible_ids = {c.id for c in list_courses_for_cluster(db, cluster.id, include_archived=True)}
    assert course_id in visible_ids


def test_retry_on_a_never_reconstructed_import_does_not_resurrect_the_hidden_shell(db, monkeypatch, mock_llm):
    """Review finding (under-fire): POST .../retry (run_reverse_gen_job)
    unconditionally sets CourseImport.status="completed" even when it
    rebuilt nothing, because collect_course_modules found zero modules to
    work with. A status-keyed hiding rule would resurrect the still-empty,
    still-broken shell in the Titles list the instant someone clicks Retry
    on it. The content-based rule can't be moved by that flip."""
    from promptops_app.jobs import import_jobs
    from promptops_app.repositories import job_repository
    from promptops_app.repositories.course_repository import list_courses_for_cluster

    factory = sessionmaker(bind=db.get_bind(), autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr("promptops_app.jobs.import_jobs.SessionLocal", factory)

    cluster = Cluster(name="C", project_id=5)
    db.add(cluster)
    db.commit()
    db.refresh(cluster)

    course = _new_course(db, name="NeverBuilt", project_id=5, cluster_id=cluster.id)
    course_id = course.id
    ci = CourseImport(course_id=course_id, project_id=5, status="queued", package_name="x.imscc")
    db.add(ci)
    db.commit()
    db.refresh(ci)
    import_id = ci.id
    course.import_id = import_id
    course.is_active = False
    db.commit()

    # The initial import job fails outright (no staged package) — same empty
    # shell as test_failed_import_job_hides_the_empty_course_shell.
    job_id = job_repository.create_job(
        db, user_name="u",
        request_params={
            "import_id": import_id, "course_id": course_id, "project_id": 5,
            "user_name": "u", "package_path": None, "package_name": "x.imscc",
        },
        project_id=5, course_id=course_id, job_type="import",
    )
    import_jobs.run_import_job(job_id)

    db.expire_all()
    course = db.query(Course).filter_by(id=course_id).first()
    assert course.is_active is False
    visible_ids = {c.id for c in list_courses_for_cluster(db, cluster.id, include_archived=True)}
    assert course_id not in visible_ids

    # The user hits Retry. run_reverse_gen_job finds zero modules, returns
    # early from _reverse_generate, but still unconditionally marks the
    # CourseImport "completed".
    retry_job_id = job_repository.create_job(
        db, user_name="u",
        request_params={"import_id": import_id, "course_id": course_id, "project_id": 5, "user_name": "u"},
        project_id=5, course_id=course_id, job_type="import_reverse",
    )
    import_jobs.run_reverse_gen_job(retry_job_id)

    db.expire_all()
    record = db.query(CourseImport).filter_by(id=import_id).first()
    assert record.status == "completed"   # the mutable signal DID flip...

    course = db.query(Course).filter_by(id=course_id).first()
    assert course.is_active is False
    assert db.query(Generation).filter_by(course_id=course_id).count() == 0   # ...but nothing rebuilt it

    visible_ids = {c.id for c in list_courses_for_cluster(db, cluster.id, include_archived=True)}
    assert course_id not in visible_ids   # still hidden regardless of the status flip


def test_a_scratch_course_archived_before_anything_generated_stays_visible(db):
    """Round-3 review finding (PROBE-C): "archived + no content" alone also
    matches a title created from scratch and archived before the user ever
    generated anything — never imported, no CourseImport row at all. That is
    a normal, purgeable archived course, not an import shell, and must not
    disappear from the one list (include_archived=True) permanent-delete is
    reachable from."""
    from promptops_app.repositories.course_repository import list_courses_for_cluster

    cluster = Cluster(name="C", project_id=7)
    db.add(cluster)
    db.commit()
    db.refresh(cluster)

    course = _new_course(db, name="ScratchNeverGenerated", project_id=7, cluster_id=cluster.id)
    course_id = course.id
    assert course.import_id is None   # never an import

    course.is_active = False
    db.commit()

    visible_ids = {c.id for c in list_courses_for_cluster(db, cluster.id, include_archived=True)}
    assert course_id in visible_ids


def test_a_cdd_and_blueprint_only_course_archived_mid_flow_stays_visible(db):
    """Round-3 review finding (PROBE-D): a course with a real CDD and
    Blueprint but no generated blocks yet is a normal waypoint in the
    CDD -> Blueprint -> generate flow, not an empty import shell. Archiving
    it at that stage must not make the CDD/Blueprint work disappear from
    every listing."""
    from promptops_app.database import CourseDesignDocument, ModuleBlueprint
    from promptops_app.repositories.course_repository import list_courses_for_cluster

    cluster = Cluster(name="C", project_id=8)
    db.add(cluster)
    db.commit()
    db.refresh(cluster)

    course = _new_course(db, name="CddAndBlueprintOnly", project_id=8, cluster_id=cluster.id)
    course_id = course.id
    assert course.import_id is None   # never an import

    cdd = CourseDesignDocument(course_id=course_id, project_id=8, title="CDD",
                              course_title="CddAndBlueprintOnly", created_by="u")
    db.add(cdd)
    db.commit()
    db.refresh(cdd)
    bp = ModuleBlueprint(title="Module 1", module_title="Module 1", module_number=1,
                         course_id=course_id, project_id=8, cdd_id=cdd.id, created_by="u")
    db.add(bp)
    db.commit()

    assert db.query(Generation).filter_by(course_id=course_id).count() == 0

    course.is_active = False
    db.commit()

    visible_ids = {c.id for c in list_courses_for_cluster(db, cluster.id, include_archived=True)}
    assert course_id in visible_ids
