"""Step 2 — CE review run skeleton: lifecycle, skip-unchanged, basis resolution.

No LLM is involved yet (the passes arrive in Steps 3-4), so the whole run is
exercised for real against the in-memory test DB. The one-active-run guard is a
Postgres partial unique index and is verified on the RDS, not here (SQLite does
not enforce it).
"""
from __future__ import annotations

import pytest

from promptops_app.database import (
    Block, ContentReview, Generation, ReviewChecklist, ReviewChecklistItem, Style,
)
from promptops_app.services.ce_review.review_runner import (
    ReviewError, execute_review, prepare_review,
)


PROJECT_ID = 4242


@pytest.fixture(autouse=True)
def _stub_llm(monkeypatch):
    """Keep both review passes offline by default; specific tests override this."""
    from types import SimpleNamespace
    from promptops_app.services.ce_review import checklist_pass, issue_pass
    monkeypatch.setattr(checklist_pass, "generate_with_metadata",
                        lambda *a, **k: SimpleNamespace(status="ok", truncated=False, text='{"results":[]}'))
    monkeypatch.setattr(issue_pass, "generate_with_metadata",
                        lambda *a, **k: SimpleNamespace(status="ok", truncated=False, text='{"issues":[]}'))


def _make_generation(db, *, topic="Lesson 1", blocks=("Intro body", "Lesson body")):
    gen = Generation(
        prompt_name="lesson", prompt_version="v1", block_type="lesson",
        topic=topic, output_text="x", project_id=PROJECT_ID, course_id=None,
    )
    db.add(gen)
    db.flush()
    for i, content in enumerate(blocks):
        db.add(Block(generation_id=gen.id, block_type="lesson",
                     block_label=f"Block {i+1}", content=content, position=i))
    db.flush()
    db.refresh(gen)
    return gen


def _make_checklist(db, *, version=1):
    cl = ReviewChecklist(project_id=PROJECT_ID, name="CE Standard", version=version, status="active")
    db.add(cl)
    db.flush()
    db.add(ReviewChecklistItem(checklist_id=cl.id, project_id=PROJECT_ID,
                               item_key="r001", rule_text="Be measurable.", position=0))
    db.flush()
    return cl


def test_run_lifecycle_with_checklist(db):
    _make_checklist(db)
    gen = _make_generation(db)

    review, reused = prepare_review(db, gen, actor="alice")
    assert reused is False
    assert review.run_status == "queued"
    assert review.review_basis == "checklist"
    assert review.content_fingerprint
    assert review.checklist_version.startswith("cl:")

    execute_review(db, review)
    assert review.run_status == "completed"
    assert review.completed_at is not None
    assert review.counts["findings"]["total"] == 0


def test_skip_unchanged_returns_same_run(db):
    _make_checklist(db)
    gen = _make_generation(db)

    review, _ = prepare_review(db, gen, actor="alice")
    execute_review(db, review)

    again, reused = prepare_review(db, gen, actor="bob")
    assert reused is True
    assert again.id == review.id           # identical content + basis → same run
    # Only one row exists for this generation.
    assert db.query(ContentReview).filter(ContentReview.generation_id == gen.id).count() == 1


def test_content_change_triggers_new_run(db):
    _make_checklist(db)
    gen = _make_generation(db)
    review, _ = prepare_review(db, gen, actor="alice")
    execute_review(db, review)

    # Edit a block → fingerprint changes → a fresh run.
    blk = db.query(Block).filter(Block.generation_id == gen.id).first()
    blk.content = "Intro body — revised with new material"
    db.flush()
    db.refresh(gen)

    review2, reused = prepare_review(db, gen, actor="alice")
    assert reused is False
    assert review2.id != review.id
    assert review2.content_fingerprint != review.content_fingerprint


def test_checklist_version_bump_triggers_new_run(db):
    _make_checklist(db, version=1)
    gen = _make_generation(db)
    review, _ = prepare_review(db, gen, actor="alice")
    execute_review(db, review)

    # A new checklist version supersedes the old → basis version changes → new run.
    db.query(ReviewChecklist).update({"status": "archived"})
    _make_checklist(db, version=2)
    db.flush()

    review2, reused = prepare_review(db, gen, actor="alice")
    assert reused is False
    assert review2.checklist_version.endswith(":v2")


def test_style_fallback_when_no_checklist(db):
    # No checklist; an active global style with writing rules.
    db.add(Style(style_id="s1", name="House Style", is_active=True,
                 custom_instructions="Use active voice."))
    db.flush()
    gen = _make_generation(db)

    review, reused = prepare_review(db, gen, actor="alice")
    assert review.review_basis == "style"
    assert review.checklist_version.startswith("style:")


def test_no_basis_raises(db):
    gen = _make_generation(db)   # no checklist, no style
    with pytest.raises(ReviewError):
        prepare_review(db, gen, actor="alice")


def test_empty_content_raises(db):
    _make_checklist(db)
    gen = _make_generation(db, blocks=("", "   "))
    with pytest.raises(ReviewError):
        prepare_review(db, gen, actor="alice")


def test_checklist_pass_grades_and_stores_non_pass(db, monkeypatch):
    from types import SimpleNamespace
    from promptops_app.database import ReviewChecklistResult
    from promptops_app.services.ce_review import checklist_pass

    cl = _make_checklist(db)                      # r001 exists
    # add two more rules
    for k in ("r002", "r003"):
        db.add(ReviewChecklistItem(checklist_id=cl.id, project_id=PROJECT_ID,
                                   item_key=k, rule_text=f"Rule {k}", position=int(k[-1])))
    db.flush()
    gen = _make_generation(db)
    review, _ = prepare_review(db, gen, actor="alice")

    # Fake LLM: r001 pass, r002 fail, r003 warning.
    def fake_llm(model, system, user, **kw):
        return SimpleNamespace(status="ok", truncated=False, text=(
            '{"results":[{"item_key":"r001","status":"pass"},'
            '{"item_key":"r002","status":"fail","explanation":"missing","recommendation":"add it"},'
            '{"item_key":"r003","status":"warning","explanation":"weak"}]}'))
    monkeypatch.setattr(checklist_pass, "generate_with_metadata", fake_llm)

    execute_review(db, review)

    assert review.counts["checklist"] == {"pass": 1, "fail": 1, "warning": 1, "na": 0}
    rows = db.query(ReviewChecklistResult).filter(ReviewChecklistResult.review_id == review.id).all()
    assert {r.item_key for r in rows} == {"r002", "r003"}        # only non-pass stored
    assert next(r for r in rows if r.item_key == "r002").recommendation == "add it"


def test_issue_detection_anchors_tiers_and_dedups(db, monkeypatch):
    from types import SimpleNamespace
    from promptops_app.database import ReviewFinding
    from promptops_app.services.ce_review import issue_pass

    _make_checklist(db)
    # Realistic block length so a one-phrase fix is a small fraction (inline tier).
    body = ("Safety matters on every shift. The user should of read the manual before starting the task, "
            "and must always confirm the equipment is powered down and locked out before any maintenance begins.")
    gen = _make_generation(db, blocks=(body,))
    review, _ = prepare_review(db, gen, actor="alice")

    # Per-block returns a grammar fix (anchored) + a duplicate; cross-block returns empty.
    def fake(model, system, user, **kw):
        if "CROSS-block" in system:
            return SimpleNamespace(status="ok", truncated=False, text='{"issues":[]}')
        return SimpleNamespace(status="ok", truncated=False, text=(
            '{"issues":[{"category":"grammar_language","severity":"blocker","title":"grammar",'
            '"detail":"should have","quote":"should of read","replacement":"should have read"},'
            '{"category":"grammar_language","severity":"minor","title":"dup","quote":"should of read","replacement":"should have read"}]}'))
    monkeypatch.setattr(issue_pass, "generate_with_metadata", fake)

    execute_review(db, review)

    rows = db.query(ReviewFinding).filter(ReviewFinding.review_id == review.id).all()
    assert len(rows) == 1                        # duplicate quote deduped
    f = rows[0]
    assert f.severity == "major"                 # grammar clamped down from blocker
    assert f.tier == "inline" and f.auto_applicable is True   # short replacement, anchored
    assert review.counts["findings"]["total"] == 1


def _checklist_llm(monkeypatch, status, item_key="r001"):
    """Stub the checklist pass to grade one rule with *status*."""
    from types import SimpleNamespace
    from promptops_app.services.ce_review import checklist_pass
    monkeypatch.setattr(checklist_pass, "generate_with_metadata", lambda *a, **k: SimpleNamespace(
        status="ok", truncated=False,
        text=f'{{"results":[{{"item_key":"{item_key}","status":"{status}","explanation":"x"}}]}}'))


def test_verdict_mandatory_fail_blocks(db, monkeypatch):
    cl = _make_checklist(db)
    cl.items[0].is_mandatory = True                      # r001 mandatory
    db.flush()
    gen = _make_generation(db)
    review, _ = prepare_review(db, gen, actor="alice")
    _checklist_llm(monkeypatch, "fail")
    execute_review(db, review)
    assert review.verdict == "changes_required" and review.counts["blockers"] == 1


def test_verdict_nonmandatory_fail_is_warning(db, monkeypatch):
    _make_checklist(db)                                  # r001 NOT mandatory
    gen = _make_generation(db)
    review, _ = prepare_review(db, gen, actor="alice")
    _checklist_llm(monkeypatch, "fail")
    execute_review(db, review)
    assert review.verdict == "warnings" and review.counts["blockers"] == 0


def test_verdict_all_clean_is_ready(db, monkeypatch):
    _make_checklist(db)
    gen = _make_generation(db)
    review, _ = prepare_review(db, gen, actor="alice")
    _checklist_llm(monkeypatch, "pass")                 # rule passes → no stored result; no findings
    execute_review(db, review)
    assert review.verdict == "ready"


def test_verdict_flips_to_ready_after_dismiss(db, monkeypatch):
    from promptops_app.database import Block
    from promptops_app.services.ce_review.fix_service import dismiss
    _make_checklist(db)
    gen = _make_generation(db)
    review, _ = prepare_review(db, gen, actor="alice")
    _checklist_llm(monkeypatch, "pass")
    execute_review(db, review)
    # Inject one open finding → verdict becomes warnings.
    block = db.query(Block).filter(Block.generation_id == gen.id).first()
    f = _make_finding(db, review, block, "Intro body", "better intro")
    from promptops_app.services.ce_review.verdict import compute
    review.verdict = compute(db, review)["verdict"]; db.flush()
    assert review.verdict == "warnings"
    dismiss(db, review, f, "alice")                     # dismiss last open issue
    assert review.verdict == "ready"


def test_rereview_carries_dismissals_and_chains(db, monkeypatch):
    from types import SimpleNamespace
    from promptops_app.database import Block, ContentReview, ReviewDismissal, ReviewFinding
    from promptops_app.services.ce_review import issue_pass
    from promptops_app.services.ce_review.fix_service import dismiss
    _make_checklist(db)
    body = ("Safety matters on every shift. The user should of read the manual before starting the task, "
            "and must confirm the equipment is powered down before any maintenance begins.")
    gen = _make_generation(db, blocks=(body,))

    # First review finds one grammar issue.
    def one_issue(model, system, user, **kw):
        if "CROSS-block" in system:
            return SimpleNamespace(status="ok", truncated=False, text='{"issues":[]}')
        return SimpleNamespace(status="ok", truncated=False, text=(
            '{"issues":[{"category":"grammar_language","severity":"major","title":"grammar",'
            '"quote":"should of read","replacement":"should have read"}]}'))
    monkeypatch.setattr(issue_pass, "generate_with_metadata", one_issue)

    r1, _ = prepare_review(db, gen, actor="alice")
    execute_review(db, r1)
    f = db.query(ReviewFinding).filter(ReviewFinding.review_id == r1.id, ReviewFinding.status == "open").one()
    dismiss(db, r1, f, "alice")                          # dismiss it → recorded in review_dismissals
    assert db.query(ReviewDismissal).filter_by(generation_id=gen.id).count() == 1

    # Edit content so a fresh run is created (not skip-unchanged), same issue re-detected.
    block = db.query(Block).filter(Block.generation_id == gen.id).first()
    block.content = body + " Always wear PPE."
    db.flush()
    r2, reused = prepare_review(db, gen, actor="alice")
    assert reused is False and r2.rerun_of_id == r1.id     # chained
    execute_review(db, r2)

    # The same issue is carried as dismissed, not resurfaced as open.
    opened = db.query(ReviewFinding).filter(ReviewFinding.review_id == r2.id, ReviewFinding.status == "open").count()
    carried = db.query(ReviewFinding).filter(ReviewFinding.review_id == r2.id, ReviewFinding.status == "dismissed").count()
    assert opened == 0 and carried == 1
    assert r2.counts.get("prev", {}).get("findings") == 1  # delta vs prior run


def test_review_writes_audit_event(db):
    from promptops_app.database import AuditLog
    _make_checklist(db)
    gen = _make_generation(db)
    review, _ = prepare_review(db, gen, actor="alice")
    execute_review(db, review)
    row = db.query(AuditLog).filter(AuditLog.action == "content.reviewed").first()
    assert row is not None and row.entity_id == str(gen.id)


def _make_finding(db, review, block, quote, repl, *, auto=True):
    from promptops_app.database import ReviewFinding
    f = ReviewFinding(review_id=review.id, block_id=block.id, project_id=PROJECT_ID,
                      category="grammar_language", severity="major", anchor_quote=quote,
                      suggested_replacement=repl, tier="inline", auto_applicable=auto, status="open")
    db.add(f); db.flush()
    return f


def test_apply_one_swaps_snapshots_and_reanchors(db):
    from promptops_app.database import Block, BlockVersion
    from promptops_app.services.ce_review.fix_service import apply_one
    _make_checklist(db)
    gen = _make_generation(db, blocks=("AAA BBB CCC end.",))
    review, _ = prepare_review(db, gen, actor="alice")
    block = db.query(Block).filter(Block.generation_id == gen.id).first()

    f1 = _make_finding(db, review, block, "BBB", "XXX")
    f2 = _make_finding(db, review, block, "BBB CCC", "YYY")   # overlaps f1's target

    ok, _ = apply_one(db, review, f1, "alice")
    assert ok is True
    db.refresh(block); db.refresh(f1); db.refresh(f2)
    assert block.content == "AAA XXX CCC end."          # deterministic swap
    assert f1.status == "applied" and f1.applied_version_id                # restore point linked
    assert f2.status == "stale"                          # re-anchored: its quote is gone
    assert db.query(BlockVersion).filter(BlockVersion.block_id == block.id).count() == 1


def test_apply_stale_when_content_changed(db):
    from promptops_app.database import Block
    from promptops_app.services.ce_review.fix_service import apply_one
    _make_checklist(db)
    gen = _make_generation(db, blocks=("hello world here",))
    review, _ = prepare_review(db, gen, actor="alice")
    block = db.query(Block).filter(Block.generation_id == gen.id).first()
    f = _make_finding(db, review, block, "missing phrase", "fixed")   # quote not in content
    ok, reason = apply_one(db, review, f, "alice")
    assert ok is False and f.status == "stale" and "re-review" in reason


def test_apply_many_one_snapshot_per_block(db):
    from promptops_app.database import Block, BlockVersion
    from promptops_app.services.ce_review.fix_service import apply_many
    _make_checklist(db)
    gen = _make_generation(db, blocks=("one two three four",))
    review, _ = prepare_review(db, gen, actor="alice")
    block = db.query(Block).filter(Block.generation_id == gen.id).first()
    _make_finding(db, review, block, "one", "1")
    _make_finding(db, review, block, "four", "4")
    summary = apply_many(db, review, None, "alice")       # None = all eligible
    db.refresh(block)
    assert summary["applied"] == 2
    assert block.content == "1 two three 4"
    assert db.query(BlockVersion).filter(BlockVersion.block_id == block.id).count() == 1   # one restore point


def test_dismiss_leaves_content_unchanged(db):
    from promptops_app.database import Block
    from promptops_app.services.ce_review.fix_service import dismiss
    _make_checklist(db)
    gen = _make_generation(db, blocks=("keep me exactly",))
    review, _ = prepare_review(db, gen, actor="alice")
    block = db.query(Block).filter(Block.generation_id == gen.id).first()
    f = _make_finding(db, review, block, "keep me", "changed")
    before = block.content
    ok, _ = dismiss(db, review, f, "alice", reason="not needed")
    db.refresh(block); db.refresh(f)
    assert ok and f.status == "dismissed" and f.dismiss_reason == "not needed"
    assert block.content == before


def test_ungraded_rule_becomes_warning_not_silent_pass(db, monkeypatch):
    from types import SimpleNamespace
    from promptops_app.database import ReviewChecklistResult
    from promptops_app.services.ce_review import checklist_pass

    _make_checklist(db)                           # one rule r001
    gen = _make_generation(db)
    review, _ = prepare_review(db, gen, actor="alice")

    # LLM returns nothing for the rule → must be warning, and stored.
    monkeypatch.setattr(checklist_pass, "generate_with_metadata",
                        lambda *a, **k: SimpleNamespace(status="ok", truncated=False, text='{"results":[]}'))
    execute_review(db, review)

    assert review.counts["checklist"]["warning"] == 1
    assert db.query(ReviewChecklistResult).filter(ReviewChecklistResult.review_id == review.id).count() == 1
