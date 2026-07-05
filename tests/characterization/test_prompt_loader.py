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
            prompt_id=prompt.id, version="v2",
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


class TestDormantSeedBug:
    """The seeded default_* rows never intersect the loader's name keys.

    ⚠️ Deliberately captures the BUG (plan: "Current state" + Phase 8
    prerequisite). When Phase 8 re-keys resolution to component_type +
    is_default, flip these assertions.
    """

    def test_seeded_defaults_do_not_resolve(self, db):
        from promptops_app.database import Prompt, seed_data

        seed_data(db)
        # The seed created the default rows...
        seeded = {p.name for p in db.query(Prompt).all()}
        assert {"default_style_prompt", "default_cdd_prompt",
                "default_blueprint_prompt", "default_generate_prompt"} <= seeded

        # ...but every stem the pipeline actually asks for still resolves to
        # the FILE tier: console edits to these rows do not reach generation.
        for stem in ("style_understanding", "cdd_generation",
                     "blueprint_generation", "content_generation"):
            assert load_template(stem, db=db).source == "file"

    def test_stem_named_row_is_the_only_db_hit(self, db):
        from promptops_app.database import seed_data

        seed_data(db)
        make_db_prompt(db, "cdd_generation", system="live", user="live-user")
        assert load_template("cdd_generation", db=db).source == "db"
