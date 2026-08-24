"""
Phase 11 (requirements doc §9) — archived prompts never reach CAS selection.

"Archived" maps onto ``prompts.deleted_at`` (soft delete). No write path
soft-deletes a pipeline row today — the Prompt Library delete is structurally
library-only and the registry DELETE is a hard delete — so these tests pin
the *guards*: every selection surface (dropdown pools, tag pickers, registry
list) and every resolution tier (component default, scope-fixing target,
legacy stem name) must ignore a soft-deleted row, now and after any future
soft-delete write path appears.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from .conftest import make_db_prompt


def _soft_delete(db, prompt) -> None:
    prompt.deleted_at = datetime.utcnow()
    db.commit()


class TestSelectionSurfaces:
    def test_component_dropdown_pool_excludes_archived(self, db):
        from promptops_app.repositories.prompt_repository import (
            list_prompts_by_component,
        )

        live = make_db_prompt(db, "arch_live", system="S", user="U",
                              component_type="cdd")
        dead = make_db_prompt(db, "arch_dead", system="S", user="U",
                              component_type="cdd")
        _soft_delete(db, dead)
        names = {p.name for p in list_prompts_by_component(db, "cdd")}
        assert live.name in names and dead.name not in names

    def test_tag_picker_excludes_archived(self, db):
        from promptops_app.repositories.prompt_repository import (
            list_prompts_tagged,
            set_prompt_tags,
        )

        live = make_db_prompt(db, "arch_tag_live", system="S", user="U")
        dead = make_db_prompt(db, "arch_tag_dead", system="S", user="U")
        set_prompt_tags(db, live, ["lesson"])
        set_prompt_tags(db, dead, ["lesson"])
        db.commit()
        _soft_delete(db, dead)
        names = {p.name for p in list_prompts_tagged(db, "lesson")}
        assert live.name in names and dead.name not in names

    def test_registry_list_excludes_archived(self, db):
        from promptops_app.repositories.prompt_repository import list_all_prompts

        dead = make_db_prompt(db, "arch_list_dead", system="S", user="U")
        _soft_delete(db, dead)
        assert dead.name not in {p.name for p in list_all_prompts(db)}

    def test_library_browse_excludes_archived(self, db):
        from promptops_app.database import Prompt
        from promptops_app.services import prompt_library_service as svc

        p = Prompt(prompt_kind="library", title="Archived lib", owner="a",
                   category="Cat", visibility="global")
        db.add(p)
        db.commit()
        _soft_delete(db, p)
        assert all(
            row.id != p.id
            for row in svc.browse_prompts_query(db, "admin", None, kind="all").all()
        )


class TestResolutionTiers:
    @pytest.fixture(autouse=True)
    def _flag_on(self, monkeypatch):
        monkeypatch.setenv("PROMPT_RESOLVE_BY_COMPONENT", "1")

    def test_archived_default_never_resolves(self, db):
        from promptops_app.repositories.prompt_repository import get_default_prompt

        dead = make_db_prompt(db, "arch_default", system="S", user="U",
                              component_type="cdd", is_default=True)
        _soft_delete(db, dead)
        assert get_default_prompt(db, "cdd") is None

    def test_fixing_bound_to_archived_row_falls_through_to_default(self, db):
        from promptops_app.prompts.prompt_loader import _resolve_pipeline_row
        from promptops_app.repositories.prompt_repository import set_fixed_prompt

        default = make_db_prompt(db, "arch_fb_default", system="S", user="U",
                                 component_type="cdd", is_default=True)
        locked = make_db_prompt(db, "arch_fb_locked", system="S", user="U",
                                component_type="cdd")
        set_fixed_prompt(db, component="cdd", scope_level="global",
                         prompt_id=locked.id, fixed_by="test_admin",
                         fixed_by_role="admin")
        db.commit()
        ctx = {"project_id": None, "cluster_id": None, "course_id": None}
        # Sanity: the lock wins while the bound row is live.
        assert _resolve_pipeline_row(db, "cdd_generation", **ctx).id == locked.id
        _soft_delete(db, locked)
        assert _resolve_pipeline_row(db, "cdd_generation", **ctx).id == default.id

    def test_legacy_stem_lookup_ignores_archived_row(self, db, monkeypatch):
        from promptops_app.prompts.prompt_loader import _from_db

        monkeypatch.setenv("PROMPT_RESOLVE_BY_COMPONENT", "0")
        dead = make_db_prompt(db, "cdd_generation", system="S", user="U")
        _soft_delete(db, dead)
        assert _from_db(db, "cdd_generation", "latest") is None


class TestExplicitArchiveAccess:
    """Phase 12e (doc §9 'unless explicitly enabled') — archived rows stay out
    of every default surface, but pipeline managers can opt in with
    include_archived=1 and restore a row."""

    def _archived_pipeline_row(self, db):
        dead = make_db_prompt(db, "arch_optin", system="S", user="U",
                              component_type="cdd")
        _soft_delete(db, dead)
        return dead

    def test_include_archived_surfaces_the_row_for_admins_only(
            self, client, auth_headers, author_headers, db):
        dead = self._archived_pipeline_row(db)
        base = "/api/v1/prompt-library/prompts?kind=pipeline"
        # Default: hidden.
        ids = [p["id"] for p in client.get(base, headers=auth_headers).json()]
        assert dead.id not in ids
        # Explicitly enabled: visible, flagged archived.
        rows = client.get(f"{base}&include_archived=1", headers=auth_headers).json()
        row = next((p for p in rows if p["id"] == dead.id), None)
        assert row is not None and row["archived"] is True
        # Non-admins: the flag is ignored, nothing pipeline-shaped leaks.
        rows = client.get(f"{base}&include_archived=1", headers=author_headers).json()
        assert [p for p in rows if p["id"] == dead.id] == []

    def test_admin_can_open_and_restore_an_archived_row(self, client, auth_headers, db):
        dead = self._archived_pipeline_row(db)
        r = client.get(f"/api/v1/prompt-library/prompts/{dead.id}", headers=auth_headers)
        assert r.status_code == 200 and r.json()["archived"] is True
        r = client.post(f"/api/v1/prompt-library/prompts/{dead.id}/restore",
                        headers=auth_headers)
        assert r.status_code == 200 and r.json()["archived"] is False
        # Restored: back on the default surfaces, restore is not repeatable.
        ids = [p["id"] for p in client.get(
            "/api/v1/prompt-library/prompts?kind=pipeline", headers=auth_headers).json()]
        assert dead.id in ids
        assert client.post(f"/api/v1/prompt-library/prompts/{dead.id}/restore",
                           headers=auth_headers).status_code == 404

    def test_non_admins_get_404_for_archived_pipeline_rows(
            self, client, author_headers, db):
        dead = self._archived_pipeline_row(db)
        assert client.get(f"/api/v1/prompt-library/prompts/{dead.id}",
                          headers=author_headers).status_code == 404
        assert client.post(f"/api/v1/prompt-library/prompts/{dead.id}/restore",
                           headers=author_headers).status_code == 403

    def test_reviewers_cannot_restore_what_they_cannot_see(self, client, db):
        # Reviewers hold prompt_library.manage but are NOT pipeline managers:
        # archived rows are invisible to them everywhere, so restore must be
        # the same 404 — nobody may restore what they cannot see.
        from app.core.security import hash_password
        from promptops_app.database import User

        db.add(User(username="arch_reviewer", password_hash=hash_password("test_password"),
                    role="reviewer", is_active=True, is_platform_admin=True))
        db.commit()
        r = client.post("/api/v1/auth/login",
                        json={"username": "arch_reviewer", "password": "test_password",
                              "platform_admin": True})
        headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

        lib = make_db_prompt(db, "arch_lib_reviewer", system="S", user="U")
        lib.prompt_kind = "library"
        lib.title = "Reviewer-visible once live"
        lib.visibility = "global"
        _soft_delete(db, lib)
        assert client.post(f"/api/v1/prompt-library/prompts/{lib.id}/restore",
                           headers=headers).status_code == 404
