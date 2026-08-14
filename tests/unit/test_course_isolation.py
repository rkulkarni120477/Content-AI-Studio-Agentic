"""Course-level isolation: a course must only ever see its own CDDs and styles,
never a sibling course's, even when they share the same project.

Regression tests for the leak where list_cdds_for_scope/get_styles treated
course_id as advisory instead of a hard filter once project_id was also set.
"""

from __future__ import annotations

from promptops_app.database import Course, CourseDesignDocument, Project, Style, get_styles
from promptops_app.repositories.cdd_repository import list_cdds_for_scope


def _make_project(db, name="Proj"):
    proj = Project(name=name)
    db.add(proj)
    db.commit()
    db.refresh(proj)
    return proj


def _make_course(db, project_id, name, active_style_id=None):
    course = Course(project_id=project_id, name=name, active_style_id=active_style_id)
    db.add(course)
    db.commit()
    db.refresh(course)
    return course


def _make_cdd(db, *, project_id, course_id, title):
    cdd = CourseDesignDocument(title=title, course_title=title, project_id=project_id, course_id=course_id)
    db.add(cdd)
    db.commit()
    db.refresh(cdd)
    return cdd


def _make_style(db, style_id, name, project_id=None, course_id=None):
    style = Style(style_id=style_id, name=name, project_id=project_id, course_id=course_id)
    db.add(style)
    db.commit()
    db.refresh(style)
    return style


def test_list_cdds_for_scope_excludes_sibling_course(db):
    proj = _make_project(db)
    mktg = _make_course(db, proj.id, "MKTG")
    programming = _make_course(db, proj.id, "Introduction to Programming")

    _make_cdd(db, project_id=proj.id, course_id=mktg.id, title="MKTG — CDD")
    for i in range(5):
        _make_cdd(db, project_id=proj.id, course_id=programming.id, title=f"Programming CDD {i}")

    result = list_cdds_for_scope(db, project_id=proj.id, course_id=mktg.id)

    assert len(result) == 1
    assert result[0].course_id == mktg.id


def test_list_cdds_for_scope_matches_legacy_null_project_rows(db):
    proj = _make_project(db)
    course = _make_course(db, proj.id, "Legacy Course")
    _make_cdd(db, project_id=None, course_id=course.id, title="Legacy CDD")

    result = list_cdds_for_scope(db, project_id=proj.id, course_id=course.id)

    assert len(result) == 1
    assert result[0].course_id == course.id


def test_list_cdds_for_scope_project_only_still_returns_whole_project(db):
    """No course_id given at all -> legitimately project-wide, not a leak."""
    proj = _make_project(db)
    c1 = _make_course(db, proj.id, "Course 1")
    c2 = _make_course(db, proj.id, "Course 2")
    _make_cdd(db, project_id=proj.id, course_id=c1.id, title="CDD 1")
    _make_cdd(db, project_id=proj.id, course_id=c2.id, title="CDD 2")

    result = list_cdds_for_scope(db, project_id=proj.id, course_id=None)

    assert len(result) == 2


def test_get_styles_excludes_sibling_course_active_style(db):
    proj = _make_project(db)
    style_mktg = _make_style(db, "mktg-style", "MKTG Style")
    style_sibling = _make_style(db, "sibling-style", "Sibling Style")

    mktg = _make_course(db, proj.id, "MKTG", active_style_id=style_mktg.id)
    _make_course(db, proj.id, "Sibling Course", active_style_id=style_sibling.id)

    result = get_styles(db, project_id=proj.id, course_id=mktg.id)

    result_ids = {s.id for s in result}
    assert style_mktg.id in result_ids
    assert style_sibling.id not in result_ids


def test_get_styles_includes_project_default_as_fallback(db):
    proj = _make_project(db)
    project_style = _make_style(db, "project-default", "Project Default")
    proj.active_style_id = project_style.id
    db.commit()

    course = _make_course(db, proj.id, "No Own Style")  # active_style_id=None

    result = get_styles(db, project_id=proj.id, course_id=course.id)

    assert {s.id for s in result} == {project_style.id}


def test_get_styles_missing_course_row_still_returns_project_default(db):
    """Stale/deleted course_id must not silently drop an explicitly-passed project_id."""
    proj = _make_project(db)
    project_style = _make_style(db, "project-default-2", "Project Default 2")
    proj.active_style_id = project_style.id
    db.commit()

    result = get_styles(db, project_id=proj.id, course_id=999999)

    assert {s.id for s in result} == {project_style.id}


def test_get_styles_project_only_includes_all_courses(db):
    """No course_id given at all -> legitimately project-wide, not a leak."""
    proj = _make_project(db)
    style_a = _make_style(db, "style-a", "Style A")
    style_b = _make_style(db, "style-b", "Style B")
    _make_course(db, proj.id, "Course A", active_style_id=style_a.id)
    _make_course(db, proj.id, "Course B", active_style_id=style_b.id)

    result = get_styles(db, project_id=proj.id, course_id=None)

    assert {s.id for s in result} == {style_a.id, style_b.id}


def test_get_styles_returns_all_owned_styles_not_just_active(db):
    """A course must see every style it generated (owned), not only its active one."""
    proj = _make_project(db)
    course = _make_course(db, proj.id, "Aviation")
    owned_active = _make_style(db, "own-active", "Active Own", project_id=proj.id, course_id=course.id)
    owned_inactive_1 = _make_style(db, "own-1", "Inactive Own 1", project_id=proj.id, course_id=course.id)
    owned_inactive_2 = _make_style(db, "own-2", "Inactive Own 2", project_id=proj.id, course_id=course.id)
    course.active_style_id = owned_active.id
    db.commit()

    result = get_styles(db, project_id=proj.id, course_id=course.id)

    assert {s.id for s in result} == {owned_active.id, owned_inactive_1.id, owned_inactive_2.id}


def test_get_styles_excludes_sibling_owned_styles(db):
    """Ownership scoping must still hide styles generated by a sibling course."""
    proj = _make_project(db)
    mine = _make_course(db, proj.id, "Mine")
    sibling = _make_course(db, proj.id, "Sibling")
    my_style = _make_style(db, "mine-1", "Mine 1", project_id=proj.id, course_id=mine.id)
    _make_style(db, "sib-1", "Sibling 1", project_id=proj.id, course_id=sibling.id)

    result = get_styles(db, project_id=proj.id, course_id=mine.id)

    assert {s.id for s in result} == {my_style.id}


def test_get_styles_project_only_includes_owned_and_active(db):
    """Project-wide view returns owned styles plus every course's active style."""
    proj = _make_project(db)
    owned = _make_style(db, "proj-owned", "Owned by project", project_id=proj.id)
    active_a = _make_style(db, "act-a", "Active A")
    _make_course(db, proj.id, "Course A", active_style_id=active_a.id)

    result = get_styles(db, project_id=proj.id, course_id=None)

    assert {s.id for s in result} == {owned.id, active_a.id}
