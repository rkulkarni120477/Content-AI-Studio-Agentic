"""Config-driven specialized structure extraction for DIS.

The common pipeline is client-neutral. Client-specific document labels,
keywords, and structure patterns live in config/clients/<client_id>.yaml under
`document_processing`. To support a new client, first update YAML; only add code
when a completely new structure shape is required.
"""
from __future__ import annotations
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.settings import DocumentProcessingConfig, StructurePatternConfig

from services.blocks import block_label

DEFAULT_DOC_RULES = [
    {
        "doc_type": "syllabus",
        "filename_keywords": ["syllabus"],
        "text_keywords": ["syllabus", "course description", "course objectives", "learning outcomes", "grading and evaluation"],
        "priority": 10,
    },
    {
        "doc_type": "course_calendar",
        "filename_keywords": ["calendar", "schedule"],
        "text_keywords": ["course calendar", "day 1", "day 2", "week/lesson", "topics covered"],
        "priority": 20,
    },
    {
        "doc_type": "quiz_answer_key",
        "filename_keywords": ["answer key", "key"],
        "text_keywords": ["answer key", "correct answer", "rationale"],
        "priority": 30,
    },
    {
        "doc_type": "quiz_exam",
        "filename_keywords": ["quiz", "exam", "final exam", "test"],
        "text_keywords": ["instructions", "multiple choice", "question #", "__/1 pt"],
        "priority": 40,
    },
    {
        "doc_type": "project_key",
        "filename_keywords": ["project", "instructor guide", "key"],
        "text_keywords": ["instructor guide", "answer key", "grading"],
        "priority": 45,
    },
    {
        "doc_type": "project_activity",
        "filename_keywords": ["project", "activity", "lab"],
        "text_keywords": ["procedures", "task 1", "learning outcomes", "equipment and tools"],
        "priority": 50,
    },
    {
        "doc_type": "study_questions",
        "filename_keywords": ["study questions", "review questions"],
        "text_keywords": ["study questions", "self-study", "review questions"],
        "priority": 60,
    },
    {
        "doc_type": "lesson_slide_deck",
        "filename_keywords": ["day", "lecture", "slides", "presentation"],
        "text_keywords": ["daily maintenance plan", "today's focus", "debrief", "slide"],
        "priority": 70,
    },
    {
        "doc_type": "ebook_reference",
        "filename_keywords": ["ebook", "handbook", "far-amt", "faa"],
        "text_keywords": ["contents", "chapter", "federal aviation regulations", "handbook"],
        "priority": 80,
    },
]


def _cfg(cfg: Optional[DocumentProcessingConfig]) -> DocumentProcessingConfig:
    return cfg or DocumentProcessingConfig(document_type_rules=DEFAULT_DOC_RULES)


def _rule_dicts(cfg: Optional[DocumentProcessingConfig]) -> List[Dict[str, Any]]:
    c = _cfg(cfg)
    rules = [r.model_dump() if hasattr(r, "model_dump") else dict(r) for r in (c.document_type_rules or [])]
    if not rules:
        rules = DEFAULT_DOC_RULES
    return rules


def infer_doc_type(filename: str, raw_text: str = "", cfg: Optional[DocumentProcessingConfig] = None) -> str:
    """Classify a document using client YAML rules first, then generic rules.

    Rules are weighted by priority. Filename hits score higher than body hits to
    protect files like `Project KEY` vs student-facing project documents.
    """
    c = _cfg(cfg)
    name = Path(filename).stem.lower()
    text = (raw_text or "")[:8000].lower()
    best_doc_type = c.fallback_doc_type or "other"
    best_score = -1
    best_priority = 999999

    for rule in _rule_dicts(c):
        doc_type = rule.get("doc_type", "other")
        if c.enabled_document_types and doc_type not in c.enabled_document_types:
            continue
        score = 0
        for kw in rule.get("filename_keywords", []) or []:
            if kw and kw.lower() in name:
                score += 8
        for kw in rule.get("text_keywords", []) or []:
            if kw and kw.lower() in text:
                score += 3
        for pattern in rule.get("regex_patterns", []) or []:
            try:
                if re.search(pattern, f"{name}\n{text}", re.I):
                    score += 5
            except re.error:
                continue
        priority = int(rule.get("priority", 100))
        if score > best_score or (score == best_score and score > 0 and priority < best_priority):
            best_score = score
            best_priority = priority
            best_doc_type = doc_type

    return best_doc_type if best_score > 0 else (c.fallback_doc_type or "other")


def infer_block(filename: str, raw_text: str = "", patterns: Optional[StructurePatternConfig] = None) -> str:
    p = patterns or StructurePatternConfig()
    blob = f"{filename}\n{(raw_text or '')[:3000]}"
    try:
        m = re.search(p.block_regex, blob, re.I)
    except re.error:
        m = None
    if m:
        for g in m.groups():
            if g and str(g).isdigit():
                # Canonical unpadded form. This used to emit f"Block {int(g):02d}",
                # which is where the 'Block 09' half of the split tagging came from:
                # this writer padded while client_profiles/aim.py did not, so one
                # block's data ended up under two tags that no `=` could reconcile.
                # Reads normalize (services/blocks), so old padded rows still match;
                # writing the canonical form stops the split from growing.
                return block_label(g)
        return block_label(m.group(0))
    return ""


def clean_cell(value: Any) -> str:
    return str(value or "").replace("\r", " ").strip()


def row_to_text(row: List[Any]) -> str:
    return " | ".join(clean_cell(c) for c in row if clean_cell(c))


def parse_day_number(text: str, patterns: Optional[StructurePatternConfig] = None) -> int | None:
    p = patterns or StructurePatternConfig()
    try:
        m = re.search(p.day_regex, text or "", re.I)
    except re.error:
        m = None
    if not m:
        return None
    for g in m.groups():
        if g and str(g).isdigit():
            return int(g)
    return None


def _extract_week(text: str, patterns: Optional[StructurePatternConfig] = None) -> int | None:
    p = patterns or StructurePatternConfig()
    try:
        m = re.search(p.week_regex, text or "", re.I)
    except re.error:
        m = None
    return int(m.group(1)) if m and m.group(1).isdigit() else None


def _contains_any(text: str, words: List[str]) -> bool:
    low = (text or "").lower()
    return any(w.lower() in low for w in (words or []))


def _extract_if_keyword(text: str, words: List[str]) -> List[str]:
    return [text[:300]] if _contains_any(text, words) else []


def extract_calendar_structure(filename: str, raw_text: str, tables: List[Any] | None = None, cfg: Optional[DocumentProcessingConfig] = None) -> Dict[str, Any]:
    c = _cfg(cfg)
    p = c.structure_patterns
    tables = tables or []
    block = infer_block(filename, raw_text, p)
    course_name = Path(filename).stem.replace("_", " ")
    days: List[Dict[str, Any]] = []
    seen = set()

    def add_day(day_no: int | None, source_text: str, source: str, row: List[Any] | None = None):
        if not source_text:
            return
        key = (day_no or len(days) + 1, source_text[:120])
        if key in seen:
            return
        seen.add(key)
        try:
            topic = re.sub(p.day_regex, "", source_text, flags=re.I).strip(" -–—|:") or source_text
        except re.error:
            topic = source_text
        days.append({
            "day_number": day_no or len(days) + 1,
            "week_number": _extract_week(source_text, p),
            "topic": topic[:300],
            "lesson_title": topic[:180],
            "activities": _extract_if_keyword(source_text, p.activity_keywords),
            "assignments": _extract_if_keyword(source_text, p.assignment_keywords),
            "assessments": _extract_if_keyword(source_text, p.assessment_keywords),
            "source_text": source_text,
            "source_location": source,
            "source_row": [clean_cell(c) for c in row] if row else [],
        })

    for ti, table in enumerate(tables):
        if not table:
            continue
        for ri, row in enumerate(table):
            text = row_to_text(row)
            if not text or len(text) < 4:
                continue
            day_no = parse_day_number(text, p)
            first = clean_cell(row[0]) if row else ""
            if day_no is None and first.isdigit() and 1 <= int(first) <= 100:
                day_no = int(first)
            if day_no is not None or _contains_any(text, p.table_schedule_keywords):
                add_day(day_no, text, f"table_{ti+1}_row_{ri+1}", row)

    if not days:
        for li, line in enumerate([l.strip() for l in (raw_text or "").splitlines() if l.strip()]):
            day_no = parse_day_number(line, p)
            if day_no is not None:
                add_day(day_no, line, f"line_{li+1}")

    days = sorted(days, key=lambda d: (d.get("day_number") or 9999, d.get("source_location", "")))
    return {
        "structure_type": "course_calendar",
        "processing_profile": c.profile,
        "course_name": course_name,
        "block": block,
        "total_days_detected": len(days),
        "days": days,
        "confidence": "high" if days else "low",
        "warnings": [] if days else ["No day/session rows detected. Update document_processing.structure_patterns in client config or enable LLM extraction."],
    }


def extract_syllabus_structure(filename: str, raw_text: str, tables: List[Any] | None = None, cfg: Optional[DocumentProcessingConfig] = None) -> Dict[str, Any]:
    text = raw_text or ""
    c = _cfg(cfg)
    block = infer_block(filename, text, c.structure_patterns)
    return {
        "structure_type": "syllabus",
        "processing_profile": c.profile,
        "course_name": Path(filename).stem.replace("_", " "),
        "block": block,
        "course_description": _section_after(text, ["course description", "description"], 1200),
        "learning_outcomes": _bullets_after(text, ["learning outcomes", "objectives", "competencies", "course objectives"], 12),
        "materials": _bullets_after(text, ["materials", "required materials", "required text", "textbook", "resources"], 10),
        "grading_policy": _section_after(text, ["grading", "evaluation", "assessment"], 1000),
        "attendance_policy": _section_after(text, ["attendance"], 800),
        "assessment_policy": _section_after(text, ["assessment", "exam", "quiz"], 1000),
        "tables_detected": len(tables or []),
    }


def extract_quiz_structure(filename: str, raw_text: str, cfg: Optional[DocumentProcessingConfig] = None) -> Dict[str, Any]:
    text = raw_text or ""
    is_key = infer_doc_type(filename, text, cfg) in {"quiz_answer_key", "project_key"}
    question_lines = [l.strip() for l in text.splitlines() if re.search(r"^(?:\d+[.)]|Q\d+|Question)", l.strip(), re.I)]
    return {
        "structure_type": "quiz_answer_key" if is_key else "quiz_exam",
        "quiz_title": Path(filename).stem.replace("_", " "),
        "question_count_detected": len(question_lines),
        "questions_preview": question_lines[:20],
        "is_answer_key": is_key,
    }


def extract_project_structure(filename: str, raw_text: str, cfg: Optional[DocumentProcessingConfig] = None) -> Dict[str, Any]:
    text = raw_text or ""
    doc_type = infer_doc_type(filename, text, cfg)
    return {
        "structure_type": doc_type if doc_type in {"project_activity", "project_key", "instructor_guide"} else "project_activity",
        "project_title": Path(filename).stem.replace("_", " "),
        "is_instructor_guide": "instructor guide" in filename.lower() or doc_type == "instructor_guide",
        "is_answer_key": doc_type == "project_key",
        "objectives": _bullets_after(text, ["objective", "objectives", "purpose", "learning outcomes"], 10),
        "materials": _bullets_after(text, ["materials", "tools", "equipment", "supplies"], 10),
        "procedure": _section_after(text, ["procedure", "procedures", "instructions", "steps", "task 1"], 1800),
        "assessment": _section_after(text, ["assessment", "grading", "rubric", "evaluation"], 1000),
    }


def _section_after(text: str, headings: List[str], limit: int) -> str:
    low = text.lower()
    for h in headings:
        idx = low.find(h.lower())
        if idx >= 0:
            return text[idx:idx+limit].strip()
    return ""


def _bullets_after(text: str, headings: List[str], max_items: int) -> List[str]:
    sec = _section_after(text, headings, 2500)
    if not sec:
        return []
    items = []
    for line in sec.splitlines()[1:]:
        line = line.strip(" •-*\t")
        if len(line) > 5:
            items.append(line[:300])
        if len(items) >= max_items:
            break
    return items
