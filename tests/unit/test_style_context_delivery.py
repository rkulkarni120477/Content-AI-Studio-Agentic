"""Style stage 2 (AIM_PIPELINE_REPAIR_WORKFLOW.txt) -- two delivery bugs found
while implementing the original Y1 finding, after tracing every place
custom_instructions and understanding_status are actually touched:

1. build_style_context (database.py) was an if/else: once generated_summary
   existed, custom_instructions and uploaded style_documents stopped reaching
   ANY prompt -- an author's own typed instructions and reference documents,
   resolved and stored, were silently dropped from processing the moment an AI
   summary existed alongside them. Fixed to always include them.

2. understanding_status is set "stale" only by add_files_to_style and "fresh"
   only by create_style_version (restore / IMSCC import) -- the actual
   Generate/Refine route (POST /styles/{id}/understand) never touched it. So a
   style correctly marked stale after a file was added stayed stale forever,
   even once regenerated from that exact file. Fixed: the route now clears it.
"""

from __future__ import annotations

import pytest


# ── build_style_context: instructions/docs must survive a summary ──────────

@pytest.fixture()
def style_with_everything(db):
    from promptops_app.database import Document, Style, StyleDocument

    doc = Document(filename="house_style.pdf", content="DOC_MARKER_reference_text",
                   status="active", uploaded_by="tester")
    db.add(doc)
    db.commit()
    db.refresh(doc)

    style = Style(
        style_id="style-everything", name="Everything Style",
        custom_instructions="INSTRUCTIONS_MARKER_no_passive_voice",
        generated_summary="SUMMARY_MARKER_validated_understanding",
    )
    db.add(style)
    db.commit()
    db.refresh(style)
    db.add(StyleDocument(style_id=style.id, document_id=doc.id))
    db.commit()
    db.refresh(style)
    return style


class TestBuildStyleContextDoesNotDropInputsOnceASummaryExists:
    def test_the_summary_is_included(self, db, style_with_everything):
        from promptops_app.database import build_style_context

        ctx = build_style_context(db, style_with_everything)
        assert "SUMMARY_MARKER_validated_understanding" in ctx

    def test_custom_instructions_still_reach_the_prompt(self, db, style_with_everything):
        from promptops_app.database import build_style_context

        ctx = build_style_context(db, style_with_everything)
        assert "INSTRUCTIONS_MARKER_no_passive_voice" in ctx, (
            "custom_instructions were dropped once generated_summary existed"
        )

    def test_reference_documents_still_reach_the_prompt(self, db, style_with_everything):
        from promptops_app.database import build_style_context

        ctx = build_style_context(db, style_with_everything)
        assert "DOC_MARKER_reference_text" in ctx, (
            "uploaded style documents were dropped once generated_summary existed"
        )

    def test_a_style_with_no_summary_is_unaffected(self, db):
        """Regression guard: the no-summary path (custom_instructions + docs
        only) must produce exactly what it always did."""
        from promptops_app.database import Style, build_style_context

        style = Style(style_id="style-no-summary", name="No Summary Style",
                     custom_instructions="INSTRUCTIONS_MARKER_no_passive_voice")
        db.add(style)
        db.commit()

        ctx = build_style_context(db, style)
        assert "INSTRUCTIONS_MARKER_no_passive_voice" in ctx
        assert "SUMMARY_MARKER" not in ctx


# ── understanding_status must clear on a successful generate/refine ────────

class TestUnderstandingStatusClearsAfterGenerating:
    @pytest.fixture(autouse=True)
    def stub_llm_and_dis(self, monkeypatch):
        monkeypatch.setattr(
            "promptops_app.services.style_service.generate_style_understanding",
            lambda *a, **k: "Generated understanding text.",
        )
        monkeypatch.setattr(
            "promptops_app.services.style_service.regenerate_style_understanding",
            lambda *a, **k: "Regenerated understanding text.",
        )
        monkeypatch.setattr(
            "app.api.v1.routers.styles.dis_client.generated_upsert_sync",
            lambda *a, **k: None,
        )

    def test_a_stale_style_becomes_fresh_after_generating(self, client, auth_headers, db):
        from promptops_app.database import Style

        style = Style(style_id="style-stale-1", name="Stale Style",
                      understanding_status="stale")
        db.add(style)
        db.commit()
        db.refresh(style)

        resp = client.post(f"/api/v1/styles/{style.id}/understand", json={},
                           headers=auth_headers)

        assert resp.status_code == 200, resp.text
        db.refresh(style)
        assert style.understanding_status == "fresh", (
            "the flag that told the author to click Generate/Refine did not "
            "clear once they did exactly that"
        )

    def test_a_stale_style_with_an_existing_summary_becomes_fresh_on_refine(
        self, client, auth_headers, db
    ):
        """Regenerate (not first-time generate) is the router's OTHER branch --
        must clear the flag too, not only the fresh-generation path."""
        from promptops_app.database import Style

        style = Style(style_id="style-stale-2", name="Stale Refine Style",
                      generated_summary="Old understanding.",
                      understanding_status="stale")
        db.add(style)
        db.commit()
        db.refresh(style)

        resp = client.post(f"/api/v1/styles/{style.id}/understand", json={},
                           headers=auth_headers)

        assert resp.status_code == 200, resp.text
        db.refresh(style)
        assert style.understanding_status == "fresh"
