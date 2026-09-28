"""Checklist evaluation pass — grade each rule against the assembled lesson.

Runs the rules in small batches (bounded output), returns Pass/Fail/Warning/NA
tallies, and persists only the non-pass rows (absence = pass). A batch the LLM
can't grade is marked 'warning' (needs manual review) rather than silently passed.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from promptops_app.core.llm_client import safe_json_loads
from promptops_app.core.models import DEFAULT_MODEL_NAME
from promptops_app.services.ce_review.llm_util import output_cap
from promptops_app.services.llm_service import generate_with_metadata
from promptops_app.services.usage_service import UsageLogContext

_log = logging.getLogger(__name__)

_BATCH = 15                                   # rules per LLM call — keeps output small
_VALID = {"pass", "fail", "warning", "na"}
_MAX_LESSON_CHARS = 60_000                    # generous guard; a DLU is ~50-70k chars

_SYSTEM = (
    "You are a content-editor reviewing an educational lesson against a checklist. "
    "For each rule, decide: pass, fail, warning, or na (not applicable). "
    "Return ONLY JSON: {\"results\":[{\"item_key\":\"..\",\"status\":\"pass|fail|warning|na\","
    "\"explanation\":\"..\",\"recommendation\":\"..\"}]}. "
    "Give explanation + recommendation only for fail/warning; keep them one sentence."
)


def _grade_batch(lesson: str, rules: list[dict], *, model: str, usage_ctx) -> dict:
    """Grade one batch → {item_key: {status, explanation, recommendation}}. Empty on error."""
    rules_txt = "\n".join(f"- [{r['item_key']}] {r['rule_text']}" for r in rules)
    user = f"RULES:\n{rules_txt}\n\nLESSON:\n{lesson}"
    res = generate_with_metadata(model, _SYSTEM, user, usage_ctx=usage_ctx,
                                 max_tokens=output_cap(model))
    if getattr(res, "status", None) == "error" or getattr(res, "truncated", False):
        return {}
    data = safe_json_loads(res.text or "")
    rows = data.get("results", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
    out = {}
    for row in rows:
        if isinstance(row, dict) and row.get("item_key"):
            out[str(row["item_key"])] = row
    return out


def evaluate_checklist(db, review, lesson: str, items: list, *, model_choice: str | None = None) -> dict:
    """Grade all rules, persist non-pass rows, return {pass, fail, warning, na} tallies."""
    from promptops_app.database import ReviewChecklistResult

    model = model_choice or DEFAULT_MODEL_NAME
    lesson = (lesson or "")[:_MAX_LESSON_CHARS]
    usage_ctx = UsageLogContext(user_name=review.created_by or "", project_id=review.project_id,
                                course_id=review.course_id, entity_type="ce_review",
                                entity_id=str(review.id))
    rules = [{"item_key": it.item_key, "rule_text": it.rule_text} for it in items]
    tally = {"pass": 0, "fail": 0, "warning": 0, "na": 0}
    now = datetime.now(timezone.utc)

    # Grade in batches; collect every rule's outcome.
    graded: dict[str, dict] = {}
    for i in range(0, len(rules), _BATCH):
        batch = rules[i:i + _BATCH]
        graded.update(_grade_batch(lesson, batch, model=model, usage_ctx=usage_ctx))

    for it in items:
        row = graded.get(it.item_key)
        # Missing/invalid grade → warning (never silently pass an ungraded rule).
        status = str((row or {}).get("status", "")).strip().lower()
        if status not in _VALID:
            status = "warning"
        tally[status] += 1
        if status == "pass":
            continue
        db.add(ReviewChecklistResult(
            review_id=review.id, item_key=it.item_key, rule_text=it.rule_text, status=status,
            explanation=(row or {}).get("explanation") or ("Could not be graded automatically — review manually."
                                                           if not row else None),
            recommendation=(row or {}).get("recommendation"),
            created_at=now,
        ))
    db.flush()
    _log.info("checklist_pass review=%s tally=%s", review.id, tally)
    return tally
