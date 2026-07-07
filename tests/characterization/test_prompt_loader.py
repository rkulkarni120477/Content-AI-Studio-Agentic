"""
Characterization: promptops_app/prompts/prompt_loader.py

Captures the 2-tier resolution (DB → file) exactly as it behaves today,
including the DORMANT-SEED NAME-KEY BUG: seed_data()'s default rows
(default_cdd_prompt etc., keyed by component_type + is_default) can never
satisfy load_template(), which looks up by exact Prompt.name == stem
(cdd_generation etc.). Phase 8 fixes this on purpose — when it does, the
tests in TestDormantSeedBug must be updated to assert the NEW behavior.
"""

from __future__ import annotations

import pytest

from promptops_app.prompts.prompt_loader import (
    _split,
    list_template_names,
    load_template,
)

from .conftest import make_db_prompt

# All templates shipped on disk.
FILE_TEMPLATES = [
    "blueprint_generation",
    "cdd_generation",
    "content_generation",
    "quiz_generation",
    "style_understanding",
    "validation",
]


class TestSplit:
    def test_marker_splits_system_and_user(self):
        raw = "--- SYSTEM ---\nsys text\n\n--- USER ---\nuser text"
        assert _split(raw) == ("sys text", "user text")

    def test_no_marker_means_all_user(self):
        assert _split("just a body") == ("", "just a body")

    def test_first_marker_wins(self):
        raw = "s\n--- USER ---\nu1\n--- USER ---\nu2"
        system, user = _split(raw)
        assert system == "s" and user == "u1\n--- USER ---\nu2"


class TestFileTier:
    def test_lists_all_disk_templates(self):
        assert list_template_names() == FILE_TEMPLATES

    @pytest.mark.parametrize("name", FILE_TEMPLATES)
    def test_every_disk_template_loads(self, name):
        tmpl = load_template(name)
        assert tmpl.source == "file"
        assert tmpl.name == name
        assert tmpl.version == "v1"
        assert tmpl.user_template  # never empty

    def test_registry_metadata_flows_through(self):
        tmpl = load_template("cdd_generation")
        assert tmpl.required_vars == [
            "course_name", "target_audience", "expert_domain",
        ]

    def test_unknown_name_raises_with_available_list(self):
        with pytest.raises(FileNotFoundError) as exc:
            load_template("nope")
        assert "cdd_generation" in str(exc.value)


class TestDbTier:
    def test_db_row_named_by_stem_takes_precedence(self, db):
        make_db_prompt(
            db, "cdd_generation",
            system="DB SYSTEM", user="DB USER {{course_name}}",
        )
        tmpl = load_template("cdd_generation", db=db)
        assert tmpl.source == "db"
        assert tmpl.system_template == "DB SYSTEM"
        assert tmpl.user_template == "DB USER {{course_name}}"

    def test_specific_version_targeting(self, db):
        from promptops_app.database import PromptVersion

        prompt = make_db_prompt(
            db, "cdd_generation", system="s1", user="u1", version="v1",
        )
        db.add(PromptVersion(
            prompt_id=prompt.id, version="v2", version_number=2,
            system_prompt="s2", user_prompt_template="u2",
            is_active=False, created_by="test_admin",
        ))
        db.commit()

        assert load_template("cdd_generation", db=db).version == "v1"  # active
        tmpl = load_template("cdd_generation", version="v2", db=db)
        assert tmpl.version == "v2" and tmpl.user_template == "u2"

    def test_no_active_version_falls_back_to_newest_row(self, db):
        make_db_prompt(
            db, "cdd_generation", system="s", user="u-inactive",
            is_active=False,
        )
        tmpl = load_template("cdd_generation", db=db)
        assert tmpl.source == "db"
        assert tmpl.user_template == "u-inactive"

    def test_missing_requested_version_falls_back_to_file(self, db):
        make_db_prompt(db, "cdd_generation", system="s", user="u")
        tmpl = load_template("cdd_generation", version="v99", db=db)
        assert tmpl.source == "file"

    def test_db_errors_are_swallowed_to_file_tier(self):
        class Boom:
            def query(self, *a, **kw):
                raise RuntimeError("db down")

        tmpl = load_template("cdd_generation", db=Boom())
        assert tmpl.source == "file"


PIPELINE_STEMS = ("style_understanding", "cdd_generation",
                  "blueprint_generation", "content_generation")


class TestDormantSeedBug:
    """The seeded default_* rows never intersect the loader's name keys.

    Phase 8 fixed this behind the PROMPT_RESOLVE_BY_COMPONENT flag (see
    TestComponentKeyedResolution). The flag ships OFF, so the legacy
    stem-name-only behavior below is still the default — these tests pin it.
    """

    def test_seeded_defaults_do_not_resolve_with_flag_off(self, db, monkeypatch):
        from promptops_app.database import Prompt, seed_data

        monkeypatch.delenv("PROMPT_RESOLVE_BY_COMPONENT", raising=False)
        seed_data(db)
        # The seed created the default rows...
        seeded = {p.name for p in db.query(Prompt).all()}
        assert {"default_style_prompt", "default_cdd_prompt",
                "default_blueprint_prompt", "default_generate_prompt"} <= seeded

        # ...but with the flag off, every stem the pipeline asks for still
        # resolves to the FILE tier — console edits to these rows do not
        # reach generation until the flag is enabled.
        for stem in PIPELINE_STEMS:
            assert load_template(stem, db=db).source == "file"

    def test_stem_named_row_is_the_only_db_hit(self, db, monkeypatch):
        from promptops_app.database import seed_data

        monkeypatch.delenv("PROMPT_RESOLVE_BY_COMPONENT", raising=False)
        seed_data(db)
        make_db_prompt(db, "cdd_generation", system="live", user="live-user")
        assert load_template("cdd_generation", db=db).source == "db"


class TestComponentKeyedResolution:
    """Phase 8 name-key fix: PROMPT_RESOLVE_BY_COMPONENT=1 re-keys the DB tier
    to scope fixing → component default → legacy stem name."""

    @pytest.fixture(autouse=True)
    def _flag_on(self, monkeypatch):
        monkeypatch.setenv("PROMPT_RESOLVE_BY_COMPONENT", "1")

    def test_seeded_defaults_resolve_for_every_stem(self, db):
        from promptops_app.database import seed_data

        seed_data(db)
        for stem in PIPELINE_STEMS:
            tmpl = load_template(stem, db=db)
            assert tmpl.source == "db", stem
            assert tmpl.name == stem  # callers keep recording the stem

    def test_editing_the_default_row_changes_resolution(self, db):
        # The acceptance criterion: an edit to the default row for a
        # component_type is what a subsequent load returns.
        from promptops_app.database import PromptVersion, seed_data
        from promptops_app.repositories.prompt_repository import get_default_prompt

        seed_data(db)
        default = get_default_prompt(db, "cdd")
        active = (
            db.query(PromptVersion)
            .filter(PromptVersion.prompt_id == default.id,
                    PromptVersion.is_active == True)  # noqa: E712
            .one()
        )
        active.user_prompt_template = "EDITED VIA CONSOLE {{course_name}}"
        db.commit()

        tmpl = load_template("cdd_generation", db=db)
        assert tmpl.user_template == "EDITED VIA CONSOLE {{course_name}}"

    def test_component_default_beats_stem_named_row(self, db):
        make_db_prompt(db, "cdd_generation", system="stem", user="stem-user")
        make_db_prompt(db, "any_admin_name", system="dflt", user="dflt-user",
                       component_type="cdd", is_default=True)
        assert load_template("cdd_generation", db=db).user_template == "dflt-user"

    def test_stem_name_still_works_as_legacy_secondary_key(self, db):
        # No default row for the component → the stem-named row still hits.
        make_db_prompt(db, "cdd_generation", system="stem", user="stem-user")
        tmpl = load_template("cdd_generation", db=db)
        assert tmpl.source == "db" and tmpl.user_template == "stem-user"

    def test_scope_fixing_overrides_the_default(self, db, course):
        from promptops_app.repositories.prompt_repository import set_fixed_prompt

        make_db_prompt(db, "default-cdd", system="d", user="default-user",
                       component_type="cdd", is_default=True)
        fixed = make_db_prompt(db, "course-cdd", system="f", user="fixed-user",
                               component_type="cdd")
        set_fixed_prompt(
            db, component="cdd", scope_level="course", course_id=course.id,
            prompt_id=fixed.id, fixed_by="test_admin", fixed_by_role="admin",
        )

        # Without the course context the default still wins…
        assert load_template("cdd_generation", db=db).user_template == "default-user"
        # …with it, the course-scope fixing wins.
        tmpl = load_template("cdd_generation", db=db, course_id=course.id)
        assert tmpl.user_template == "fixed-user"

    def test_global_fixing_applies_without_context_ids(self, db):
        from promptops_app.repositories.prompt_repository import set_fixed_prompt

        make_db_prompt(db, "default-cdd", system="d", user="default-user",
                       component_type="cdd", is_default=True)
        fixed = make_db_prompt(db, "global-cdd", system="g", user="global-user",
                               component_type="cdd")
        set_fixed_prompt(
            db, component="cdd", scope_level="global",
            prompt_id=fixed.id, fixed_by="test_admin", fixed_by_role="admin",
        )
        assert load_template("cdd_generation", db=db).user_template == "global-user"

    def test_library_rows_never_resolve_even_if_mislabeled(self, db):
        from promptops_app.database import Prompt, PromptVersion

        lib = Prompt(
            name=None, title="sneaky library row", prompt_kind="library",
            component_type="cdd", is_default=True, owner="test_author",
        )
        db.add(lib)
        db.commit()
        db.add(PromptVersion(
            prompt_id=lib.id, version="v1", version_number=1,
            user_prompt_template="LIB BODY",
            is_active=True, created_by="test_author",
        ))
        db.commit()

        # The kind guard keeps generation blind to it → file tier.
        assert load_template("cdd_generation", db=db).source == "file"

    def test_unmapped_stems_keep_stem_name_resolution(self, db):
        make_db_prompt(db, "validation", system="v", user="val-user")
        tmpl = load_template("validation", db=db)
        assert tmpl.source == "db" and tmpl.user_template == "val-user"


class TestVariantAwareResolution:
    """Phase 8 variant split: exact (component_type, variant) match first, then
    the NULL-variant row only when a variant was requested — never sideways
    across variants."""

    @pytest.fixture(autouse=True)
    def _flag_on(self, monkeypatch):
        monkeypatch.setenv("PROMPT_RESOLVE_BY_COMPONENT", "1")

    def test_exact_variant_default_beats_null_variant_default(self, db):
        make_db_prompt(db, "bp-base", system="b", user="base-user",
                       component_type="blueprint", is_default=True)
        make_db_prompt(db, "bp-teacher", system="t", user="teacher-user",
                       component_type="blueprint", is_default=True,
                       variant="teacher")
        tmpl = load_template("blueprint_generation", db=db, variant="teacher")
        assert tmpl.user_template == "teacher-user"

    def test_variant_request_falls_back_to_null_variant_default(self, db):
        # The Blueprint refinement case: no teacher/student row authored yet →
        # the NULL-variant seeded default keeps serving both modes.
        make_db_prompt(db, "bp-base", system="b", user="base-user",
                       component_type="blueprint", is_default=True)
        for v in ("teacher", "student"):
            assert load_template(
                "blueprint_generation", db=db, variant=v,
            ).user_template == "base-user"

    def test_never_sideways_across_variants(self, db):
        # Only a student-variant default exists → a teacher request must NOT
        # get it; with no NULL default either, it degrades to the file tier.
        make_db_prompt(db, "bp-student", system="s", user="student-user",
                       component_type="blueprint", is_default=True,
                       variant="student")
        tmpl = load_template("blueprint_generation", db=db, variant="teacher")
        assert tmpl.source == "file"

    def test_variantless_request_ignores_variant_defaults(self, db):
        # No sideways in the other direction: a variant-specific default can
        # never satisfy a request that asked for no variant.
        make_db_prompt(db, "bp-teacher", system="t", user="teacher-user",
                       component_type="blueprint", is_default=True,
                       variant="teacher")
        assert load_template("blueprint_generation", db=db).source == "file"

    def test_require_variant_resolves_only_the_exact_row(self, db):
        make_db_prompt(db, "gen-interactive", system="i", user="interactive-user",
                       component_type="generate", is_default=True,
                       variant="interactive")
        tmpl = load_template(
            "content_generation", db=db,
            variant="interactive", require_variant=True,
        )
        assert tmpl.source == "db"
        assert tmpl.user_template == "interactive-user"

    def test_require_variant_refuses_every_fallback_tier(self, db):
        # NULL-variant default + stem-named row + .md file all exist — none of
        # them is the interactive variant, so resolution must fail outright
        # (the caller keeps its own bespoke fallback).
        make_db_prompt(db, "gen-base", system="g", user="base-user",
                       component_type="generate", is_default=True)
        make_db_prompt(db, "content_generation", system="st", user="stem-user")
        with pytest.raises(FileNotFoundError):
            load_template(
                "content_generation", db=db,
                variant="interactive", require_variant=True,
            )

    def test_fixing_bound_to_other_variant_is_skipped(self, db, course):
        from promptops_app.repositories.prompt_repository import set_fixed_prompt

        make_db_prompt(db, "bp-base", system="b", user="base-user",
                       component_type="blueprint", is_default=True)
        teacher_row = make_db_prompt(
            db, "bp-teacher", system="t", user="teacher-user",
            component_type="blueprint", variant="teacher",
        )
        set_fixed_prompt(
            db, component="blueprint", scope_level="course",
            course_id=course.id, prompt_id=teacher_row.id,
            fixed_by="test_admin", fixed_by_role="admin",
        )
        # Teacher request honors the course lock…
        assert load_template(
            "blueprint_generation", db=db, course_id=course.id,
            variant="teacher",
        ).user_template == "teacher-user"
        # …a student request skips it and lands on the NULL-variant default.
        assert load_template(
            "blueprint_generation", db=db, course_id=course.id,
            variant="student",
        ).user_template == "base-user"

    def test_incompatible_narrow_fixing_falls_through_to_broader_scope(
        self, db, course,
    ):
        from promptops_app.repositories.prompt_repository import set_fixed_prompt

        teacher_row = make_db_prompt(
            db, "bp-teacher", system="t", user="teacher-user",
            component_type="blueprint", variant="teacher",
        )
        global_row = make_db_prompt(
            db, "bp-global", system="g", user="global-user",
            component_type="blueprint",
        )
        set_fixed_prompt(
            db, component="blueprint", scope_level="course",
            course_id=course.id, prompt_id=teacher_row.id,
            fixed_by="test_admin", fixed_by_role="admin",
        )
        set_fixed_prompt(
            db, component="blueprint", scope_level="global",
            prompt_id=global_row.id,
            fixed_by="test_admin", fixed_by_role="admin",
        )
        # Student request: course lock is teacher-only → the global fixing
        # (NULL-variant row, compatible) applies instead.
        assert load_template(
            "blueprint_generation", db=db, course_id=course.id,
            variant="student",
        ).user_template == "global-user"

    def test_null_variant_fixing_serves_variant_requests(self, db, course):
        from promptops_app.repositories.prompt_repository import set_fixed_prompt

        make_db_prompt(db, "bp-base", system="b", user="base-user",
                       component_type="blueprint", is_default=True)
        locked = make_db_prompt(db, "bp-locked", system="l", user="locked-user",
                                component_type="blueprint")
        set_fixed_prompt(
            db, component="blueprint", scope_level="course",
            course_id=course.id, prompt_id=locked.id,
            fixed_by="test_admin", fixed_by_role="admin",
        )
        for v in ("teacher", "student"):
            assert load_template(
                "blueprint_generation", db=db, course_id=course.id, variant=v,
            ).user_template == "locked-user"


class TestTaxonomySeeds:
    """Phase 8 taxonomy seeds (signed off 2026-07-07): blueprint teacher/student
    and quiz default rows ride seed_data(). The seeded text equals what the
    legacy tiers serve, so live output is unchanged — each line just becomes
    independently versionable through the console."""

    @pytest.fixture(autouse=True)
    def _flag_on(self, monkeypatch):
        monkeypatch.setenv("PROMPT_RESOLVE_BY_COMPONENT", "1")

    EXPECTED_LINES = {
        ("style", None), ("cdd", None), ("blueprint", None), ("generate", None),
        ("blueprint", "teacher"), ("blueprint", "student"), ("quiz", None),
    }

    def test_seed_creates_all_default_lines_idempotently(self, db):
        from promptops_app.database import (
            Prompt, seed_data, seed_default_component_prompts,
        )

        seed_data(db)
        lines = {
            (p.component_type, p.variant)
            for p in db.query(Prompt).filter(Prompt.is_default == True).all()  # noqa: E712
        }
        assert lines == self.EXPECTED_LINES
        # Re-running creates nothing — per-name skip keeps it idempotent.
        assert seed_default_component_prompts(db) == []

    def test_blueprint_variants_resolve_their_seeded_rows(self, db):
        from promptops_app.database import seed_data

        seed_data(db)
        teacher = load_template("blueprint_generation", db=db, variant="teacher")
        student = load_template("blueprint_generation", db=db, variant="student")
        assert teacher.source == "db" and student.source == "db"
        # Mode-specific text, not the shared NULL-variant default.
        assert "TEACHER-FACING" in teacher.system_template
        assert "TEACHER-FACING" not in student.system_template
        assert teacher.system_template != student.system_template

    def test_seeded_variant_bodies_are_double_braced(self, db):
        from promptops_app.database import Prompt, PromptVersion, seed_data
        from promptops_app.prompts.brace_conversion import find_single_brace_vars

        seed_data(db)
        for name in ("default_blueprint_teacher_prompt",
                     "default_blueprint_student_prompt",
                     "default_quiz_prompt"):
            p = db.query(Prompt).filter(Prompt.name == name).one()
            v = db.query(PromptVersion).filter_by(prompt_id=p.id).one()
            body = (v.system_prompt or "") + (v.user_prompt_template or "")
            assert find_single_brace_vars(body) == [], name
        # The rename reached the seeded teacher body too.
        tp = db.query(Prompt).filter_by(name="default_blueprint_teacher_prompt").one()
        tv = db.query(PromptVersion).filter_by(prompt_id=tp.id).one()
        assert "{{extra_instructions}}" in tv.user_prompt_template

    def test_quiz_seed_matches_file_tier(self, db):
        from promptops_app.database import seed_data

        file_tmpl = load_template("quiz_generation")  # no db → file tier
        seed_data(db)
        db_tmpl = load_template("quiz_generation", db=db)
        assert db_tmpl.source == "db"
        assert db_tmpl.system_template.strip() == file_tmpl.system_template.strip()
        assert db_tmpl.user_template.strip() == file_tmpl.user_template.strip()

    def test_quiz_seed_ignored_with_flag_off(self, db, monkeypatch):
        from promptops_app.database import seed_data

        monkeypatch.delenv("PROMPT_RESOLVE_BY_COMPONENT", raising=False)
        seed_data(db)
        assert load_template("quiz_generation", db=db).source == "file"
