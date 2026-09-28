"""
Phase 4 unit tests — prompt_library_service rewritten onto the native tables.

Per PROMPT_CONSOLIDATION_PLAN.md Phase 4: each rewritten service function must
produce output identical in SHAPE to the old pl_*-backed implementation
(values differ only in id type — int vs UUID string), and the two prompt
kinds must never cross: a library row can never reach the generation
resolvers, and a caller without pipeline-manager access can never receive a
pipeline row from the library service.

Uses the shared SQLite ``db`` fixture — these tests exercise real queries, so
a MagicMock session isn't sufficient.
"""

from __future__ import annotations

from promptops_app.database import (
    AuditLog,
    Prompt,
    PromptRequest,
    PromptReview,
    PromptTag,
    PromptVariable,
    PromptVersion,
    Team,
)
from promptops_app.services import prompt_library_service as svc


def _mk_library_prompt(db, title="Lib Prompt", content="Hello {{name}}", **kw) -> Prompt:
    p = Prompt(prompt_kind="library", title=title, description="d", category="Cat",
               visibility=kw.pop("visibility", "global"), owner="alice",
               created_at=svc.now_utc(), updated_at=svc.now_utc(), **kw)
    db.add(p)
    db.flush()
    svc.set_prompt_content(db, p, content, create_version=True, note="Initial version",
                           created_by="alice")
    db.flush()
    return p


def _mk_pipeline_prompt(db, name="cdd_generation", component_type="cdd") -> Prompt:
    p = Prompt(prompt_kind="pipeline", name=name, component_type=component_type,
               is_default=True, owner="admin")
    db.add(p)
    db.flush()
    return p


# ---------------------------------------------------------------------------
# Output-shape parity with the pl_*-backed implementation
# ---------------------------------------------------------------------------

OLD_PROMPT_DICT_KEYS = {
    "id", "parent_id", "title", "content", "description", "category",
    "visibility", "teams", "created_by", "created_at", "updated_at",
    "last_used_at", "tags", "variables",
}
OLD_RELATION_KEYS = {
    "parent", "children", "_child_count", "can_have_children", "versions",
    "attachments", "_version_count", "_review_stats",
}


class TestSerializerShapes:
    def test_prompt_to_dict_shape(self, db):
        p = _mk_library_prompt(db)
        db.add(PromptTag(prompt_id=p.id, tag="t1"))
        db.add(PromptVariable(prompt_id=p.id, name="name", label="Name", sort_order=0))
        db.flush()
        d = svc.prompt_to_dict(db, p)
        assert OLD_PROMPT_DICT_KEYS | OLD_RELATION_KEYS <= set(d)
        assert isinstance(d["id"], int)
        assert d["content"] == "Hello {{name}}"
        assert d["created_by"] == "alice"          # owner -> created_by
        assert d["tags"] == ["t1"]
        assert d["variables"] == [{"name": "name", "label": "Name", "hint": ""}]
        assert d["versions"][0]["version"] == 1    # numeric, from version_number
        assert d["versions"][0]["note"] == "Initial version"  # change_reason -> note
        # Library dicts carry NO pipeline block (exact legacy shape).
        assert "pipeline" not in d
        assert "system_prompt" not in d["versions"][0]

    def test_pipeline_rows_serialize_the_console_block(self, db):
        from promptops_app.database import PromptVersion

        p = _mk_pipeline_prompt(db, name="default_blueprint_prompt",
                                component_type="blueprint")
        p.variant = "teacher"
        p.active_version = "v1"
        db.add(PromptVersion(
            prompt_id=p.id, version="v1", version_number=1,
            system_prompt="SYS", user_prompt_template="USER {{cdd_context}}",
            workflow_state="active", is_active=True, created_by="admin",
        ))
        db.flush()

        d = svc.prompt_to_dict(db, p)
        assert d["prompt_kind"] == "pipeline"
        assert d["pipeline"] == {
            "name": "default_blueprint_prompt",
            "component_type": "blueprint",
            "variant": "teacher",
            "is_default": True,
            "active_version": "v1",
            "system_prompt": "SYS",
            "workflow_state": "active",
        }
        v = d["versions"][0]
        assert v["label"] == "v1"
        assert v["system_prompt"] == "SYS"
        assert v["workflow_state"] == "active"
        assert v["is_active"] is True

    def test_pipeline_tags_prefer_rows_with_legacy_fallback(self, db):
        # Canonical prompt_tags rows win (Phase 8 tags hygiene)…
        p = _mk_pipeline_prompt(db, name="tagged_pipeline")
        p.tags = "legacy_a,legacy_b"
        db.add(PromptTag(prompt_id=p.id, tag="canonical"))
        db.flush()
        assert svc.prompt_to_dict(db, p)["tags"] == ["canonical"]
        # …and a row that predates the backfill still shows its legacy string.
        q = _mk_pipeline_prompt(db, name="legacy_pipeline", component_type="quiz")
        q.tags = "legacy_a, legacy_b"
        db.flush()
        assert svc.prompt_to_dict(db, q)["tags"] == ["legacy_a", "legacy_b"]

    def test_review_request_team_dict_shapes(self, db):
        p = _mk_library_prompt(db)
        r = PromptReview(prompt_id=p.id, username="bob", rating=4, feedback="ok",
                         created_at=svc.now_utc(), updated_at=svc.now_utc())
        req = PromptRequest(title="Need X", requested_by="bob", type="new", status="open",
                            created_at=svc.now_utc(), updated_at=svc.now_utc())
        team = Team(name="P1", created_by="admin", created_at=svc.now_utc())
        db.add_all([r, req, team])
        db.flush()
        assert set(svc.review_to_dict(r)) == {
            "id", "prompt_id", "username", "rating", "feedback", "created_at", "updated_at"}
        assert set(svc.request_to_dict(req)) == {
            "id", "title", "description", "type", "prompt_id", "requested_by",
            "status", "admin_notes", "created_at", "updated_at"}
        assert set(svc.team_to_dict(team)) == {
            "id", "name", "created_at", "created_by", "user_count", "prompt_count"}
        assert isinstance(svc.team_to_dict(team)["id"], int)

    def test_audit_event_dict_shape_and_derived_action(self, db):
        svc.log_event(db, "prompt.create", "create", entity_type="prompt",
                      entity_id=42, summary="s", actor_username="alice", actor_role="admin")
        e = db.query(AuditLog).filter(AuditLog.action == "prompt.create").one()
        d = svc.audit_event_to_dict(e)
        assert {"id", "event_type", "action", "actor_username", "actor_role",
                "entity_type", "entity_id", "summary", "changes", "ip_address",
                "user_agent", "created_at"} == set(d)
        assert d["event_type"] == "prompt.create"
        assert d["action"] == "create"             # derived suffix, not stored twice
        assert d["actor_username"] == "alice"      # user_id -> actor_username
        assert d["entity_id"] == 42                # digit string surfaces as int


class TestContentVersioning:
    def test_silent_edit_does_not_version(self, db):
        p = _mk_library_prompt(db)
        svc.set_prompt_content(db, p, "edited", create_version=False)
        db.flush()
        assert svc.get_prompt_content(p) == "edited"
        assert len(p.versions) == 1                # version list must not grow

    def test_create_version_bumps_and_activates(self, db):
        p = _mk_library_prompt(db)
        v2 = svc.set_prompt_content(db, p, "v2 body", create_version=True, note="second")
        db.flush()
        assert v2.version_number == 2 and v2.version == "v2"
        assert v2.is_active and v2.workflow_state == "active"
        assert p.active_version == "v2"
        assert [v.is_active for v in sorted(p.versions, key=lambda v: v.version_number)] == [False, True]
        assert svc.get_prompt_content(p) == "v2 body"

    def test_system_prompt_stays_null_for_library_versions(self, db):
        p = _mk_library_prompt(db)
        svc.set_prompt_content(db, p, "more", create_version=True)
        db.flush()
        assert all(v.system_prompt is None for v in p.versions)


class TestRenderEngine:
    def test_double_brace_semantics_unchanged(self):
        out = svc.render_prompt_content("Hi {{a}} and {{b}}", {"a": "X"})
        assert out == "Hi X and {{b}}"             # unsupplied stays literal
        assert svc.extract_var_names("{{a}} {{b}} {{a}}") == ["a", "b"]


# ---------------------------------------------------------------------------
# Kind separation (Decision 1) — the Phase 4 acceptance tests
# ---------------------------------------------------------------------------

class TestKindSeparation:
    def test_library_queries_never_return_pipeline_rows(self, db):
        _mk_library_prompt(db)
        _mk_pipeline_prompt(db)
        db.flush()
        for q in (svc.base_prompt_query(db),
                  svc.visible_prompts_query(db, "admin", None),
                  svc.visible_prompts_query(db, "author", None)):
            assert all(p.prompt_kind == "library" for p in q.all())

    def test_browse_kind_stripped_for_non_admins(self, db):
        _mk_library_prompt(db)
        _mk_pipeline_prompt(db)
        db.flush()
        # Authors and reviewers get library-only regardless of requested kind.
        for role in ("author", "reviewer"):
            for kind in ("pipeline", "all", "library", None):
                rows = svc.browse_prompts_query(db, role, None, kind=kind).all()
                assert all(p.prompt_kind == "library" for p in rows), (role, kind)
        # Admins can browse pipeline rows explicitly; the default stays library.
        assert all(p.prompt_kind == "library"
                   for p in svc.browse_prompts_query(db, "admin", None).all())
        pipeline_rows = svc.browse_prompts_query(db, "admin", None, kind="pipeline").all()
        assert pipeline_rows and all(p.prompt_kind == "pipeline" for p in pipeline_rows)

    def test_can_access_prompt_gates_pipeline_rows(self, db):
        pipe = _mk_pipeline_prompt(db)
        assert svc.can_access_prompt(pipe, "admin", None)
        assert not svc.can_access_prompt(pipe, "reviewer", None)
        assert not svc.can_access_prompt(pipe, "author", None)

    def test_generation_resolvers_never_see_library_rows(self, db):
        """A library prompt can never be injected into a generation call:
        the loader resolves by Prompt.name (NULL for library rows) and
        get_default_prompt by component_type+is_default (None/False)."""
        from promptops_app.prompts.prompt_loader import _from_db
        from promptops_app.repositories.prompt_repository import get_default_prompt

        # A library prompt masquerading with a pipeline stem as its TITLE —
        # name stays NULL, so neither resolver may pick it up.
        _mk_library_prompt(db, title="cdd_generation", content="{{sneaky}}")
        db.flush()
        assert _from_db(db, "cdd_generation", "latest") is None
        assert get_default_prompt(db, "cdd") is None

        # And the real pipeline row IS resolved once present.
        _mk_pipeline_prompt(db, name="cdd_generation", component_type="cdd")
        db.flush()
        resolved = get_default_prompt(db, "cdd")
        assert resolved is not None and resolved.prompt_kind == "pipeline"


class TestScopeFilter:
    """Phase 11 (doc §8) — course/cluster/project scope filters on browse."""

    def _hierarchy(self, db):
        from promptops_app.database import Cluster, Course, Project

        proj = Project(name="SF Proj", created_by="admin")
        db.add(proj)
        db.flush()
        clus = Cluster(name="SF Clus", project_id=proj.id, created_by="admin")
        db.add(clus)
        db.flush()
        course = Course(name="SF Course", project_id=proj.id,
                        cluster_id=clus.id, created_by="admin")
        other = Course(name="SF Other", project_id=proj.id,
                       cluster_id=clus.id, created_by="admin")
        db.add_all([course, other])
        db.flush()
        return proj, clus, course, other

    def _fix(self, db, component, prompt_id, scope_level, **ids):
        from promptops_app.database import PromptFixing

        db.add(PromptFixing(component=component, scope_level=scope_level,
                            prompt_id=prompt_id, fixed_by="admin",
                            fixed_by_role="admin",
                            project_id=ids.get("project_id"),
                            cluster_id=ids.get("cluster_id"),
                            course_id=ids.get("course_id")))
        db.flush()

    def _pipe(self, db, name, **kw):
        p = Prompt(prompt_kind="pipeline", name=name, owner="admin",
                   component_type=kw.pop("component_type", "cdd"), **kw)
        db.add(p)
        db.flush()
        return p

    def _browse(self, db, args):
        q = svc.browse_prompts_query(db, "admin", None, kind="pipeline")
        return {p.name for p in svc.apply_list_filters(q, args).all()}

    def test_course_filter_matches_covering_chain_plus_defaults(self, db):
        proj, clus, course, other = self._hierarchy(db)
        self._pipe(db, "sf_default", is_default=True)   # inherited default
        p_course = self._pipe(db, "sf_course_lock")
        p_other = self._pipe(db, "sf_other_lock")
        p_cluster = self._pipe(db, "sf_cluster_lock")
        self._pipe(db, "sf_unbound")                    # noise: must be excluded
        _mk_library_prompt(db, title="sf lib")          # noise: wrong kind
        self._fix(db, "cdd", p_course.id, "course", course_id=course.id)
        self._fix(db, "cdd", p_other.id, "course", course_id=other.id)
        self._fix(db, "style", p_cluster.id, "cluster", cluster_id=clus.id)

        got = self._browse(db, {"course_id": str(course.id)})
        assert got == {"sf_default", "sf_course_lock", "sf_cluster_lock"}

    def test_project_filter_includes_subtree_locks(self, db):
        proj, clus, course, other = self._hierarchy(db)
        p_course = self._pipe(db, "sf_leaf_lock")
        p_proj = self._pipe(db, "sf_proj_lock")
        self._fix(db, "cdd", p_course.id, "course", course_id=course.id)
        self._fix(db, "style", p_proj.id, "project", project_id=proj.id)

        got = self._browse(db, {"project_id": str(proj.id)})
        assert got == {"sf_leaf_lock", "sf_proj_lock"}

    def test_cluster_filter_spans_both_directions(self, db):
        proj, clus, course, other = self._hierarchy(db)
        p_course = self._pipe(db, "sf_c_lock")
        p_proj = self._pipe(db, "sf_p_lock")
        self._fix(db, "cdd", p_course.id, "course", course_id=course.id)
        self._fix(db, "style", p_proj.id, "project", project_id=proj.id)

        got = self._browse(db, {"cluster_id": str(clus.id)})
        assert got == {"sf_c_lock", "sf_p_lock"}

    def test_global_lock_reaches_every_scope(self, db):
        proj, clus, course, other = self._hierarchy(db)
        p_glob = self._pipe(db, "sf_glob")
        self._fix(db, "cdd", p_glob.id, "global")
        for args in ({"course_id": str(course.id)},
                     {"cluster_id": str(clus.id)},
                     {"project_id": str(proj.id)}):
            assert "sf_glob" in self._browse(db, args)

    def test_unknown_or_malformed_scope_returns_empty(self, db):
        self._pipe(db, "sf_default2", is_default=True)
        assert self._browse(db, {"course_id": "999999"}) == set()
        assert self._browse(db, {"project_id": "not-a-number"}) == set()

    def test_scope_filter_never_leaks_to_non_admins(self, db):
        proj, clus, course, other = self._hierarchy(db)
        p = self._pipe(db, "sf_hidden", is_default=True)
        self._fix(db, "cdd", p.id, "course", course_id=course.id)
        _mk_library_prompt(db, title="sf lib vis")
        for role in ("author", "reviewer"):
            q = svc.browse_prompts_query(db, role, None, kind="pipeline")
            rows = svc.apply_list_filters(q, {"course_id": str(course.id)}).all()
            assert rows == []


class TestDuplicateDetection:
    """Phase 11 (doc §10) — advisory normalized-content dedup probe."""

    def test_normalization_matches_across_whitespace_and_case(self, db):
        p = _mk_library_prompt(db, title="Original", content="Hello   {{name}}\nWelcome!")
        db.flush()
        dup = svc.find_duplicate_prompt(db, "hello {{name}} welcome!")
        assert dup is not None and dup.id == p.id

    def test_no_match_across_kinds_or_for_different_content(self, db):
        _mk_library_prompt(db, title="Lib", content="SHARED BODY")
        db.flush()
        assert svc.find_duplicate_prompt(db, "SHARED BODY", kind="pipeline") is None
        assert svc.find_duplicate_prompt(db, "something else") is None
        assert svc.find_duplicate_prompt(db, "   ") is None

    def test_exclude_id_skips_the_row_being_edited(self, db):
        p = _mk_library_prompt(db, title="Self", content="EDIT ME")
        db.flush()
        assert svc.find_duplicate_prompt(db, "edit me", exclude_id=p.id) is None

    def test_soft_deleted_rows_never_match(self, db):
        p = _mk_library_prompt(db, title="Gone", content="GHOST BODY")
        p.deleted_at = svc.now_utc()
        db.flush()
        assert svc.find_duplicate_prompt(db, "ghost body") is None


class TestCasCategoryFilter:
    """Phase 12 — the doc's six CAS categories as unified-list filter values."""

    def _pipe(self, db, name, component_type, variant=None):
        p = Prompt(prompt_kind="pipeline", name=name, owner="admin",
                   component_type=component_type, variant=variant)
        db.add(p)
        db.flush()
        return p

    def _seed(self, db):
        self._pipe(db, "cc_style", "style")
        self._pipe(db, "cc_cdd", "cdd")
        self._pipe(db, "cc_bp", "blueprint")
        self._pipe(db, "cc_bp_teacher", "blueprint", "teacher")
        self._pipe(db, "cc_quiz", "quiz")
        self._pipe(db, "cc_lesson", "generate")
        self._pipe(db, "cc_lesson_var", "generate", "lesson")
        self._pipe(db, "cc_interactive", "generate", "interactive")
        _mk_library_prompt(db, title="cc lib", content="cc lib body")

    def _browse(self, db, label, role="admin"):
        q = svc.browse_prompts_query(db, role, None, kind="all")
        rows = svc.apply_list_filters(q, {"cas_category": label}).all()
        return {p.name or p.title for p in rows}

    def test_each_label_maps_to_its_resolution_key(self, db):
        self._seed(db)
        assert self._browse(db, "Style") == {"cc_style"}
        assert self._browse(db, "CDD") == {"cc_cdd"}
        assert self._browse(db, "Blueprint") == {"cc_bp", "cc_bp_teacher"}
        assert self._browse(db, "Assessment") == {"cc_quiz"}

    def test_generate_splits_between_lesson_generation_and_component(self, db):
        self._seed(db)
        assert self._browse(db, "Lesson Generation") == {"cc_lesson", "cc_lesson_var"}
        assert self._browse(db, "Component") == {"cc_interactive"}

    def test_label_matching_is_case_insensitive_unknown_is_empty(self, db):
        self._seed(db)
        assert self._browse(db, "lesson generation") == {"cc_lesson", "cc_lesson_var"}
        assert self._browse(db, "Nonsense") == set()

    def test_cas_filter_never_leaks_to_non_admins(self, db):
        self._seed(db)
        for role in ("author", "reviewer"):
            assert self._browse(db, "CDD", role=role) == set()


class TestPipelineTagListing:
    """Phase 12b — the console tag filter runs over pipeline rows (admins)."""

    def test_kind_pipeline_lists_pipeline_tags_for_admins_only(self, db):
        from promptops_app.database import PromptTag

        p = Prompt(prompt_kind="pipeline", name="tg_pipe", owner="admin",
                   component_type="cdd")
        db.add(p)
        db.flush()
        db.add(PromptTag(prompt_id=p.id, tag="cdd"))
        lib = _mk_library_prompt(db, title="tg lib")
        db.add(PromptTag(prompt_id=lib.id, tag="kickoff"))
        db.flush()
        assert svc.list_distinct_tags(db, "admin", None, kind="pipeline") == ["cdd"]
        # The library default is unchanged.
        assert svc.list_distinct_tags(db, "admin", None) == ["kickoff"]
        # Non-admins asking for pipeline silently fall back to their library
        # scope — same no-leak shape as browse.
        assert svc.list_distinct_tags(db, "author", None, kind="pipeline") == ["kickoff"]


class TestStateFilter:
    """Phase 12e (doc §8) — filter by the active version's workflow_state."""

    def _pipe_with_state(self, db, name, state):
        p = Prompt(prompt_kind="pipeline", name=name, owner="admin",
                   component_type="cdd")
        db.add(p)
        db.flush()
        db.add(PromptVersion(prompt_id=p.id, version="v1", version_number=1,
                             user_prompt_template="body", is_active=True,
                             workflow_state=state))
        db.flush()
        return p

    def test_state_filters_on_the_active_version(self, db):
        self._pipe_with_state(db, "st_draft", "draft")
        self._pipe_with_state(db, "st_active", "active")
        q = svc.browse_prompts_query(db, "admin", None, kind="pipeline")
        assert {p.name for p in svc.apply_list_filters(q, {"state": "draft"}).all()} == {"st_draft"}
        q = svc.browse_prompts_query(db, "admin", None, kind="pipeline")
        assert {p.name for p in svc.apply_list_filters(q, {"state": "active"}).all()} == {"st_active"}
        q = svc.browse_prompts_query(db, "admin", None, kind="pipeline")
        assert svc.apply_list_filters(q, {"state": "in_review"}).all() == []


class TestSearchFilter:
    """CAS-107 — ``q`` matches the name the console renders, and nothing the
    user cannot see.

    Both halves of the bug came from one omission: ``q`` LIKE'd ``Prompt.title``,
    which is NULL on every pipeline row, so the console's only searchable
    columns were description and the version body. Searching a prompt's exact
    on-screen name returned nothing, while unrelated rows matched on template
    text hidden below the card's 72px clip.
    """

    def _pipe(self, db, name, *, description="", body="body"):
        p = Prompt(prompt_kind="pipeline", name=name, owner="admin",
                   component_type="cdd", description=description)
        db.add(p)
        db.flush()
        db.add(PromptVersion(prompt_id=p.id, version="v1", version_number=1,
                             user_prompt_template=body, is_active=True,
                             workflow_state="active"))
        db.flush()
        return p

    def _browse(self, db, args, kind="pipeline"):
        q = svc.browse_prompts_query(db, "admin", None, kind=kind)
        return {svc._prompt_base_dict(p)["title"]
                for p in svc.apply_list_filters(q, args).all()}

    def test_pipeline_row_is_found_by_its_displayed_name(self, db):
        # The ticket's repro verbatim: the exact on-screen name returned 0 rows
        # because that name lives in `name` and only `title` was searched.
        self._pipe(db, "Gen Ai generator")
        self._pipe(db, "unrelated")
        assert self._browse(db, {"q": "Gen Ai generator"}) == {"Gen Ai generator"}
        # Case- and substring-insensitive, same as before for titles.
        assert self._browse(db, {"q": "gen ai"}) == {"Gen Ai generator"}

    def test_library_row_is_still_found_by_its_title(self, db):
        # The other kind fills `title`, not `name` — the coalesce must not have
        # traded one broken kind for the other.
        _mk_library_prompt(db, title="Searchable Lib")
        assert "Searchable Lib" in self._browse(db, {"q": "searchable"}, kind="all")

    def test_body_text_does_not_match(self, db):
        """The assertion that matters: the row that WAS wrongly returned.

        `q` must not reach the version template — that is what put AIM DLU
        Outline and default_generate_prompt in a search for "test", with the
        matching text ~1.5k characters below the card's visible area."""
        self._pipe(db, "AIM DLU Outline", body="write a unit test for the day")
        self._pipe(db, "test01", description="test 12")
        assert self._browse(db, {"q": "test"}) == {"test01"}

    def test_description_still_matches(self, db):
        # Descriptions render unclipped on the card, so a hit there is legible
        # and stays searchable — dropping the body must not drop this too.
        self._pipe(db, "BP - Animesh", description="Just for testing")
        assert self._browse(db, {"q": "testing"}) == {"BP - Animesh"}

    def test_a_to_z_sorts_pipeline_rows_by_displayed_name(self, db):
        # The UI's "A → Z" (sort=title) ordered by a column that is NULL on
        # every pipeline row, leaving all of them tied and the option inert.
        for name in ("cherry", "Apple", "banana"):
            self._pipe(db, name)
        q = svc.browse_prompts_query(db, "admin", None, kind="pipeline")
        rows = svc.apply_list_filters(q, {"sort": "title"}).all()
        assert [r.name for r in rows] == ["Apple", "banana", "cherry"]


class TestPromotionHelpers:
    """Phase 12 — registry-name helpers behind promote-to-pipeline."""

    def test_slugify_prompt_name(self, db):
        assert svc.slugify_prompt_name("Management Blueprint Prompt!") == \
            "management_blueprint_prompt"
        assert svc.slugify_prompt_name("  --  ") == "promoted_prompt"
        assert svc.slugify_prompt_name("") == "promoted_prompt"

    def test_unique_prompt_name_suffixes_past_taken_names(self, db):
        db.add(Prompt(prompt_kind="pipeline", name="ph_stem", owner="admin"))
        db.add(Prompt(prompt_kind="pipeline", name="ph_stem_2", owner="admin"))
        db.flush()
        assert svc.unique_prompt_name(db, "ph_stem") == "ph_stem_3"
        assert svc.unique_prompt_name(db, "ph_fresh") == "ph_fresh"

    def test_unique_prompt_name_counts_soft_deleted_rows(self, db):
        db.add(Prompt(prompt_kind="pipeline", name="ph_gone", owner="admin",
                      deleted_at=svc.now_utc()))
        db.flush()
        assert svc.unique_prompt_name(db, "ph_gone") == "ph_gone_2"


# ---------------------------------------------------------------------------
# Browse-list serializer (list-load performance work)
# ---------------------------------------------------------------------------

# Relation expansions that belong to the DETAIL payload only. Each one cost a
# lazy load or an extra query per row, and none is rendered by a list view.
LIST_EXCLUDED_KEYS = {"versions", "attachments", "parent", "children"}

# Counters the list rows carry in place of the arrays above.
LIST_COUNTER_KEYS = {"_review_stats", "_version_count", "_child_count"}


class TestListSerializer:
    def test_list_dict_drops_the_detail_only_relations(self, db):
        p = _mk_library_prompt(db)
        db.add(PromptTag(prompt_id=p.id, tag="t1"))
        db.add(PromptVariable(prompt_id=p.id, name="name", label="Name", sort_order=0))
        db.flush()

        d = svc.prompt_to_list_dict(p)
        assert LIST_EXCLUDED_KEYS.isdisjoint(d)
        # …while still carrying everything a list row renders.
        assert OLD_PROMPT_DICT_KEYS | LIST_COUNTER_KEYS | {"prompt_kind", "archived",
                                                           "can_have_children"} <= set(d)
        assert d["content"] == "Hello {{name}}"
        assert d["tags"] == ["t1"]
        assert d["variables"] == [{"name": "name", "label": "Name", "hint": ""}]
        # Library rows carry no pipeline block, exactly as in the detail dict.
        assert "pipeline" not in d

    def test_list_dict_agrees_with_detail_dict_on_every_shared_key(self, db):
        """Drift guard: the two serializers share ``_prompt_base_dict``, so a
        key published by both must always carry the same value. If this fails,
        the list and detail payloads have started to disagree."""
        p = _mk_library_prompt(db, title="Parity")
        db.add(PromptTag(prompt_id=p.id, tag="parity"))
        db.flush()

        detail = svc.prompt_to_dict(db, p)
        listed = svc.prompt_to_list_dict(p)
        shared = (set(detail) & set(listed)) - LIST_COUNTER_KEYS
        assert shared  # guard against the intersection silently emptying
        for key in shared:
            assert listed[key] == detail[key], key

    def test_pipeline_list_row_keeps_the_console_block_minus_system_prompt(self, db):
        p = _mk_pipeline_prompt(db, name="default_blueprint_prompt",
                                component_type="blueprint")
        p.variant = "teacher"
        p.active_version = "v1"
        db.add(PromptVersion(
            prompt_id=p.id, version="v1", version_number=1,
            system_prompt="SYS", user_prompt_template="USER {{cdd_context}}",
            workflow_state="active", is_active=True, created_by="admin",
        ))
        db.flush()

        d = svc.prompt_to_list_dict(p)
        # The status badge needs is_default + workflow_state; nothing in a list
        # renders the system prompt, which was 31% of the response body.
        assert d["pipeline"] == {
            "name": "default_blueprint_prompt",
            "component_type": "blueprint",
            "variant": "teacher",
            "is_default": True,
            "active_version": "v1",
            "workflow_state": "active",
        }
        # The detail payload still carries it.
        assert svc.prompt_to_dict(db, p)["pipeline"]["system_prompt"] == "SYS"

    def test_counters_replace_the_dropped_arrays(self, db):
        parent = _mk_library_prompt(db, title="Has follow-ups")
        _mk_library_prompt(db, title="Follow-up", parent_id=parent.id)
        db.add(PromptReview(prompt_id=parent.id, username="bob", rating=4))
        db.flush()

        stats = svc.review_stats_batch(db, [parent.id])
        counts = svc.child_count_batch(db, [parent.id])
        d = svc.prompt_to_list_dict(parent, review_stats=stats.get(parent.id),
                                    child_count=counts.get(parent.id, 0))
        assert d["_version_count"] == 1          # was len(versions)
        assert d["_child_count"] == 1            # was len(children)
        assert d["_review_stats"] == {"count": 1, "avg": 4.0}
        # Defaults stay render-safe when no batch data is passed.
        bare = svc.prompt_to_list_dict(parent)
        assert bare["_review_stats"] == {"count": 0, "avg": 0}
        assert bare["_child_count"] == 0

    def test_list_loaders_do_not_corrupt_a_later_detail_dict(self, db):
        """Loader options must not outlive the list response.

        A cancelled (``noload``) collection is marked permanently loaded-and-
        empty on the instance, so a detail dict built from the same identity map
        would silently publish an empty collection for a prompt that has rows.
        Deferred version columns are safe by comparison — re-reading them costs a
        query but returns the truth. This pins both.
        """
        from promptops_app.database import PromptAttachment

        p = _mk_library_prompt(db, title="Has an attachment")
        svc.set_prompt_content(db, p, "revised body", create_version=True,
                               note="why it changed", created_by="alice")
        db.add(PromptAttachment(prompt_id=p.id, original_name="brief.pdf",
                                stored_name="stored.pdf", size_bytes=10,
                                uploaded_by="alice"))
        db.flush()
        db.expire_all()

        # A list query runs first, then the SAME instance is serialized through
        # the detail serializer — the shape a shared session would produce.
        rows = svc.list_query_loaders(svc.browse_prompts_query(db, "admin", None)).all()
        listed = next(r for r in rows if r.id == p.id)
        detail = svc.prompt_to_dict(db, listed)

        assert [a["original_name"] for a in detail["attachments"]] == ["brief.pdf"]
        assert detail["versions"][-1]["note"] == "why it changed"
        assert svc.prompt_to_list_dict(listed)["content"] == "revised body"

    def test_list_query_loaders_leave_the_result_set_untouched(self, db):
        """Loader options are a performance concern only — they must not change
        which rows come back or how the active version resolves."""
        a = _mk_library_prompt(db, title="Loaded A")
        svc.set_prompt_content(db, a, "v2 body", create_version=True, note="second",
                               created_by="alice")
        db.flush()
        _mk_library_prompt(db, title="Loaded B")

        plain = svc.browse_prompts_query(db, "admin", None)
        loaded = svc.list_query_loaders(svc.browse_prompts_query(db, "admin", None))
        assert [p.id for p in plain.all()] == [p.id for p in loaded.all()]

        row = next(p for p in loaded.all() if p.id == a.id)
        assert svc.prompt_to_list_dict(row)["content"] == "v2 body"
        assert svc.prompt_to_list_dict(row)["_version_count"] == 2
