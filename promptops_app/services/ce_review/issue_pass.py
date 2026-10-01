"""Issue detection pass — find quality issues across a lesson.

Per-block pass finds block-local issues (5 categories); a cross-block pass finds
consistency + repetition. Each finding is anchored to a verbatim quote, given a
severity and a fix tier (inline | large | guidance), de-duplicated, and capped at
~30 (most severe first). Grammar/style/tone/formatting/repetition never rank
'blocker' — only real content issues do.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from promptops_app.core.llm_client import safe_json_loads
from promptops_app.core.models import DEFAULT_MODEL_NAME
from promptops_app.services.ce_review.anchoring import anchor_hash, contains, normalize
from promptops_app.services.ce_review.llm_util import output_cap
from promptops_app.services.llm_service import generate_with_metadata
from promptops_app.services.usage_service import UsageLogContext

_log = logging.getLogger(__name__)

_CAP = 30                                  # max findings shown per run
_QUOTE_MAX = 300
_REPL_MAX = 8000                           # absolute storage backstop
_SEV_RANK = {"blocker": 0, "major": 1, "minor": 2}
_CONTENT_CATS = {"incorrect_incomplete", "unsupported"}   # only these may be 'blocker'
_BLOCK_CATS = "incorrect_incomplete, formatting_structure, style_tone, grammar_language, unsupported"
_CROSS_CATS = "consistency, repetition"

_BLOCK_SYS = (
    "You review one block of an educational lesson for quality issues. "
    f"Use only these categories: {_BLOCK_CATS}. "
    'Return ONLY JSON: {"issues":[{"category":"..","severity":"blocker|major|minor",'
    '"title":"..","detail":"..","quote":"<verbatim snippet from the block>","replacement":"<fixed text or null>"}]}. '
    "quote must be copied exactly from the block. Keep title/detail one sentence. "
    "Only 'blocker' for wrong or unsupported information."
)
_CROSS_SYS = (
    "You review a full lesson for CROSS-block issues only. "
    f"Use only these categories: {_CROSS_CATS} (inconsistencies or repeated/redundant content). "
    'Return ONLY JSON: {"issues":[{"category":"..","severity":"major|minor",'
    '"title":"..","detail":"..","quote":"<verbatim snippet>"}]}.'
)
# Appended only when checklist rules are supplied — asks the model to map each issue
# to the related rule key (AC-3), or null when none applies.
_RULE_SUFFIX = (
    ' Also add "rule_key" to each issue: the checklist rule key (from the RULES list) '
    "this issue most relates to, or null if none. Use a key exactly as listed."
)


def _call(system: str, user: str, *, model: str, usage_ctx) -> tuple[list, bool]:
    """One detection call → (raw issue dicts, ok). ok=False if the AI call failed/truncated."""
    res = generate_with_metadata(model, system, user, usage_ctx=usage_ctx, max_tokens=output_cap(model))
    if getattr(res, "status", None) == "error" or getattr(res, "truncated", False):
        return [], False
    data = safe_json_loads(res.text or "")
    issues = data.get("issues", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
    return [i for i in issues if isinstance(i, dict)], True


def _clamp_severity(category: str, sev: str) -> str:
    """Normalise severity; non-content categories can't be 'blocker'."""
    sev = sev if sev in _SEV_RANK else "major"
    if category not in _CONTENT_CATS and sev == "blocker":
        sev = "major"
    return sev


def _tier(block_len: int, quote: str, replacement: str | None) -> tuple[str, bool]:
    """Classify the fix by how much of the block it touches. Returns (tier, auto_applicable)."""
    if not replacement or block_len == 0:
        return "guidance", False
    ratio = max(len(quote or ""), len(replacement)) / block_len
    if ratio < 0.15:
        return "inline", True
    if ratio <= 0.50:
        return "large", True
    return "guidance", False


def _build(raw: dict, *, block_id, block_content: str, project_id, valid_keys: set | None = None) -> dict | None:
    """Turn one raw LLM issue into a finding dict, or None to drop it."""
    category = str(raw.get("category", "")).strip().lower()
    quote = str(raw.get("quote") or "")[:_QUOTE_MAX]
    if not category or not quote.strip():
        return None
    severity = _clamp_severity(category, str(raw.get("severity", "")).strip().lower())
    anchored = contains(block_content, quote)
    replacement = raw.get("replacement")
    replacement = str(replacement)[:_REPL_MAX] if replacement else None
    tier, auto = (_tier(len(block_content or ""), quote, replacement) if anchored else ("guidance", False))
    # Related checklist rule (AC-3) — only kept when it's a real key from this checklist.
    rule_key = str(raw.get("rule_key") or "").strip()[:80] or None
    if rule_key and valid_keys is not None and rule_key not in valid_keys:
        rule_key = None
    # instrumentation: replacement size relative to block (tunes tier thresholds later)
    if replacement and block_content:
        _log.info("finding_ratio cat=%s ratio=%.3f tier=%s", category,
                  max(len(quote), len(replacement)) / len(block_content), tier)
    return {
        "block_id": block_id, "project_id": project_id, "category": category, "severity": severity,
        "title": (str(raw.get("title") or "")[:255] or None), "detail": (str(raw.get("detail") or "") or None),
        "anchor_quote": quote, "anchor_hash": anchor_hash(quote),
        "suggested_replacement": replacement, "tier": tier, "auto_applicable": auto,
        "checklist_item_key": rule_key,
        "fingerprint": anchor_hash(f"{block_id}|{category}|{quote}"),
    }


def detect_issues(db, review, parts: list[dict], *, model_choice: str | None = None,
                  rules: list[dict] | None = None) -> dict:
    """Run detection, persist findings (deduped, capped), return findings tally.

    `rules` (item_key/rule_text dicts) lets the model tag each issue with the
    related checklist rule key (AC-3); omitted → findings carry no rule key.
    """
    from promptops_app.database import ReviewFinding

    model = model_choice or DEFAULT_MODEL_NAME
    usage_ctx = UsageLogContext(user_name=review.created_by or "", project_id=review.project_id,
                                course_id=review.course_id, entity_type="ce_review", entity_id=str(review.id))
    findings: list[dict] = []
    failed_calls = 0            # track AI failures so a total outage isn't a clean run (#3)

    # Checklist rule reference — fed to the prompt so issues can map to a rule key (AC-3).
    rules = rules or []
    valid_keys = {r["item_key"] for r in rules} or None
    rules_ref = "\n".join(f"- [{r['item_key']}] {r['rule_text']}" for r in rules)
    block_sys = _BLOCK_SYS + _RULE_SUFFIX if rules else _BLOCK_SYS
    cross_sys = _CROSS_SYS + _RULE_SUFFIX if rules else _CROSS_SYS
    rules_block = f"RULES:\n{rules_ref}\n\n" if rules else ""

    # Per-block passes.
    for p in parts:
        content = p.get("content") or ""
        if not content.strip():
            continue
        issues, ok = _call(block_sys, f"{rules_block}BLOCK:\n{content}", model=model, usage_ctx=usage_ctx)
        if not ok:
            failed_calls += 1
        for raw in issues:
            f = _build(raw, block_id=p.get("block_id"), block_content=content,
                       project_id=review.project_id, valid_keys=valid_keys)
            if f:
                findings.append(f)

    # Cross-block pass (consistency + repetition) — quotes anchor to whichever block holds them.
    lesson = "\n\n".join(f"## {p.get('block_label') or ''}\n{p.get('content') or ''}" for p in parts)
    by_block = {p.get("block_id"): (p.get("content") or "") for p in parts}
    cross_issues, ok = _call(cross_sys, f"{rules_block}LESSON:\n{lesson}", model=model, usage_ctx=usage_ctx)
    if not ok:
        failed_calls += 1
    for raw in cross_issues:
        quote = str(raw.get("quote") or "")[:_QUOTE_MAX]
        host = next((bid for bid, c in by_block.items() if contains(c, quote)), None)
        f = _build(raw, block_id=host, block_content=by_block.get(host, ""),
                   project_id=review.project_id, valid_keys=valid_keys)
        if f:
            findings.append(f)

    # Dedup by fingerprint.
    seen, deduped = set(), []
    for f in findings:
        if f["fingerprint"] not in seen:
            seen.add(f["fingerprint"])
            deduped.append(f)

    # Carry-forward: fingerprints dismissed on a prior review of this lesson stay dismissed.
    from promptops_app.database import ReviewDismissal
    dismissed_fps = {d.fingerprint for d in db.query(ReviewDismissal.fingerprint)
                     .filter(ReviewDismissal.generation_id == review.generation_id).all()}
    open_f = [f for f in deduped if f["fingerprint"] not in dismissed_fps]
    carried = [f for f in deduped if f["fingerprint"] in dismissed_fps]

    open_f.sort(key=lambda f: _SEV_RANK.get(f["severity"], 3))
    kept, more = open_f[:_CAP], max(0, len(open_f) - _CAP)

    now = datetime.now(timezone.utc)
    for f in kept:
        db.add(ReviewFinding(review_id=review.id, status="open", created_at=now, **f))
    for f in carried:
        db.add(ReviewFinding(review_id=review.id, status="dismissed", created_at=now,
                             dismissed_at=now, dismiss_reason="Carried from a previous review", **f))
    db.flush()
    _log.info("issue_pass review=%s open=%d more=%d carried_dismissed=%d failed=%d",
              review.id, len(kept), more, len(carried), failed_calls)
    return {"total": len(kept), "more": more, "applied": 0, "dismissed": len(carried),
            "failed_calls": failed_calls}
