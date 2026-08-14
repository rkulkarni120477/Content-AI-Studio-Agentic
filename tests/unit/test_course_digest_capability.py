"""The UI and the endpoint must gate block-wide generation on the SAME question.

They did not. The generation panel was gated on the caller's own DIS client
(``UserProfileResponse.digest_pipeline_enabled``) while ``POST /cdd/generate-block``
gated on the COURSE'S project client. Measured against the live database on
2026-08-13: **71 of 106 courses** would have shown the panel and then failed the POST
with ``400 Digest pipeline is not enabled for this course's client.`` — thrown before
``create_job`` and before the ``*.block_requested`` audit write, so the failure left
no job row and no audit row anywhere.

The endpoint's question is the correct one: the pipeline enumerates the COURSE'S own
Source Library, so a viewer whose personal client is enabled still has nothing to read
on a course that belongs to a client which is not. Courses therefore carry the answer.

Two independent guards here, because either alone can rot:
  * the value is right (this module's first half), and
  * every construction site actually sets it (the structural tests at the end) —
    a capability flag wired into one endpoint and defaulted at another does not read
    as missing data, it reads as a definitive "no". That is precisely how the
    block-wide panel vanished after login until auth.build_user_profile fixed it.
"""
from __future__ import annotations

import inspect
import types
from pathlib import Path

import pytest

from app.core.config import settings
from app.schemas.course import (
    CourseListItem,
    CourseRead,
    build_course_list_item,
    build_course_read,
)


@pytest.fixture
def digest_settings():
    """Save/restore the two knobs so tests can't leak into each other."""
    before = (settings.digest_pipeline_enabled, settings.digest_pipeline_clients)
    yield settings
    settings.digest_pipeline_enabled, settings.digest_pipeline_clients = before


class _Course:
    """Minimal ORM stand-in — from_attributes reads plain attributes."""

    def __init__(self, course_id=1, project_id=1, name="Block 2"):
        self.id = course_id
        self.name = name
        self.description = None
        self.project_id = project_id
        self.cluster_id = None
        self.active_cdd_id = None
        self.active_blueprint_id = None
        self.source_type = None
        self.import_id = None
        self.is_active = True
        self.created_at = None


class _DB:
    """db.get(Model, pk) over a fixed project client_name."""

    def __init__(self, client_name):
        self._client_name = client_name

    def get(self, model, pk):
        if model.__name__ == "Course":
            return types.SimpleNamespace(project_id=1)
        return types.SimpleNamespace(client_name=self._client_name)


# --------------------------------------------------------------------------- #
# The value
# --------------------------------------------------------------------------- #

def test_a_course_whose_client_is_allowlisted_is_enabled(digest_settings):
    from app.core.dis_access import digest_pipeline_enabled_for_course

    digest_settings.digest_pipeline_enabled = True
    digest_settings.digest_pipeline_clients = "aim"
    assert digest_pipeline_enabled_for_course(_DB("AIM"), course_id=1) is True


def test_a_course_on_another_client_is_not(digest_settings):
    """The 71-of-106 case: cengage and academian courses were showing the panel."""
    from app.core.dis_access import digest_pipeline_enabled_for_course

    digest_settings.digest_pipeline_enabled = True
    digest_settings.digest_pipeline_clients = "aim"
    assert digest_pipeline_enabled_for_course(_DB("Cengage"), course_id=1) is False


def test_the_master_switch_overrides_the_allowlist(digest_settings):
    from app.core.dis_access import digest_pipeline_enabled_for_course

    digest_settings.digest_pipeline_enabled = False
    digest_settings.digest_pipeline_clients = "aim"
    assert digest_pipeline_enabled_for_course(_DB("AIM"), course_id=1) is False


def test_an_unresolvable_client_is_not_enabled(digest_settings):
    """A project with no client_name must not inherit someone else's capability."""
    from app.core.dis_access import digest_pipeline_enabled_for_course

    digest_settings.digest_pipeline_enabled = True
    digest_settings.digest_pipeline_clients = "aim"
    assert digest_pipeline_enabled_for_course(_DB(""), course_id=1) is False


def test_it_matches_what_the_endpoint_itself_would_decide(digest_settings):
    """Pins the two against each other rather than against a literal: this helper
    exists only to be the same answer the router computes, and a divergence here is
    the whole bug coming back."""
    from app.core.dis_access import digest_pipeline_enabled_for_course, resolve_course_dis_client

    digest_settings.digest_pipeline_enabled = True
    digest_settings.digest_pipeline_clients = "aim"
    for client in ("AIM", "Cengage", ""):
        db = _DB(client)
        endpoint_answer = settings.digest_pipeline_on_for(resolve_course_dis_client(db, course_id=1))
        assert digest_pipeline_enabled_for_course(db, course_id=1) is endpoint_answer


# --------------------------------------------------------------------------- #
# The payloads
# --------------------------------------------------------------------------- #

def test_the_builders_carry_the_flag_onto_both_payloads():
    assert build_course_read(_Course(), digest_pipeline_enabled=True).digest_pipeline_enabled is True
    assert build_course_list_item(_Course(), digest_pipeline_enabled=True).digest_pipeline_enabled is True
    assert build_course_read(_Course(), digest_pipeline_enabled=False).digest_pipeline_enabled is False


def test_the_builders_preserve_everything_model_validate_produced():
    """The flag is grafted on after validation; that must not drop other fields."""
    course = _Course(course_id=48, project_id=23, name="Block 2 — General Science II")
    read = build_course_read(course, digest_pipeline_enabled=True)
    assert (read.id, read.project_id, read.name) == (48, 23, "Block 2 — General Science II")


def test_forgetting_the_flag_is_a_typeerror_not_a_silent_false():
    """The strongest available guard, and the reason it is a required keyword: a
    default would let a new call site quietly serve "disabled" to a browser."""
    with pytest.raises(TypeError):
        build_course_read(_Course())
    with pytest.raises(TypeError):
        build_course_list_item(_Course())


# --------------------------------------------------------------------------- #
# Structural: one builder, no stragglers
# --------------------------------------------------------------------------- #

def _router_source(module_name: str) -> str:
    """Located through the imported module rather than a path relative to the working
    directory, so this cannot pass vacuously by reading nothing when pytest runs from
    somewhere other than the repo root."""
    from importlib import import_module
    return Path(inspect.getsourcefile(import_module(module_name))).read_text()


@pytest.mark.parametrize("module_name", [
    "app.api.v1.routers.courses",
    "app.api.v1.routers.clusters",
])
def test_no_router_constructs_these_payloads_directly(module_name):
    """Every site must go through the builders. A stray ``model_validate`` would
    emit the schema default and hide the panel on a course that supports it."""
    src = _router_source(module_name)
    for bad in ("CourseRead.model_validate(", "CourseListItem.model_validate(",
                "CourseRead(", "CourseListItem("):
        assert bad not in src, (
            f"{module_name} constructs a course payload directly ({bad}) — use "
            f"build_course_read / build_course_list_item so digest_pipeline_enabled "
            f"cannot be silently defaulted."
        )


def test_listing_resolves_the_flag_once_per_project_not_once_per_course():
    """A per-row lookup is two DB gets per course for an answer that is a property of
    the project — N+1 on the busiest list endpoint in the app."""
    src = _router_source("app.api.v1.routers.courses")
    assert src.count("digest_pipeline_enabled_for_course(") == 4, (
        "expected exactly one resolve in list_courses plus one per CourseRead site"
    )
    assert "for c in courses[start: start + page_size]" in src
    # The resolve must sit outside the comprehension that builds the rows.
    body = src[src.index("def list_courses("):src.index("def ", src.index("def list_courses(") + 10)]
    assert body.index("digest_pipeline_enabled_for_course(") < body.index("items=[")


@pytest.mark.parametrize("model", [CourseRead, CourseListItem])
def test_both_payloads_expose_the_flag(model):
    """Pinned because the frontend reads it off whichever of the two it happens to
    hold — the pages gate on the selected course, which comes from the LIST."""
    assert "digest_pipeline_enabled" in model.model_fields
