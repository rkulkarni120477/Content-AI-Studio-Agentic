"""AIM client profile for safe content ingestion metadata.

Important design:
- This is not a separate AIM pipeline.
- Rules come from config/clients/aim.yaml under aim_content_rules.
- Content team decisions such as version priority, visibility, excluded folders,
  and calendar-source preference can be changed in YAML without code changes.
"""
from __future__ import annotations
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.settings import TenantConfig
from services.client_profiles.base import BaseClientProfile


DEFAULT_AIM_RULES: Dict[str, Any] = {
    "default_course_id": "general_science_i",
    "default_program_id": "aim",
    "block_day_pattern": r"B(?P<block_number>\d+)D(?P<day_number>\d+)",
    "version_priority": {
        "lesson_pdf": ["Finalized PDFs", "Final PDFs"],
        "slide_deck": ["Final Slide Deck"],
        "quiz": ["Quizzes/5-PDFs/Updated PDFs", "Quizzes/4-Finalized/Updated Quizzes"],
        "quiz_answer_key": ["Quizzes/5-PDFs/Updated PDFs", "Quizzes/4-Finalized/Updated Quizzes"],
        "final_exam": ["Quizzes/5-PDFs", "Quizzes/4-Finalized"],
        "final_exam_answer_key": ["Quizzes/5-PDFs", "Quizzes/4-Finalized"],
        "project": ["Projects/4-PDF", "Projects/3-Final"],
        "project_instructor_guide": ["Projects/4-PDF", "Projects/3-Final"],
        "hangar_activity": ["Hangar Activities"],
        "study_questions": ["Study Questions"],
        "course_calendar": ["Teaching Outline & Syllabus"],
        "syllabus": ["Teaching Outline & Syllabus"],
    },
    "exclude_path_contains": ["Archive", "Archived", "1-Original", "2-Edited", "Retake"],
    "restricted_filename_contains": ["Answer Key", "Instructor Guide", "Exam Key", "Quiz Key"],
    "visibility_by_content_type": {
        "lesson_pdf": "student",
        "slide_deck": "student",
        "quiz": "student",
        "final_exam": "student",
        "project": "student",
        "hangar_activity": "student",
        "study_questions": "student",
        "course_calendar": "instructor_only",
        "syllabus": "student",
        "quiz_answer_key": "instructor_only",
        "final_exam_answer_key": "instructor_only",
        "project_instructor_guide": "instructor_only",
        "archive_or_working_version": "internal_only",
    },
    "blueprint_source_types": ["course_calendar", "syllabus"],
    "course_generation_source_types": ["lesson_pdf", "slide_deck", "quiz", "final_exam", "project", "hangar_activity", "study_questions"],
    "calendar_mapping_required_types": ["quiz", "final_exam", "project", "hangar_activity", "study_questions"],
    "calendar_mapping_strategy": "calendar_first_then_filename",
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _norm_path(path: str) -> str:
    return (path or "").replace("\\", "/")


def _contains_any(text: str, values: List[str]) -> bool:
    low = (text or "").lower()
    return any(str(v).lower() in low for v in values or [])


def _first_int(value: str) -> Optional[int]:
    m = re.search(r"\d+", value or "")
    return int(m.group(0)) if m else None


class AIMClientProfile(BaseClientProfile):
    def __init__(self, tenant_cfg: TenantConfig):
        super().__init__(tenant_cfg)
        configured = (self.rules.get("aim_content_rules") or self.rules.get("content_ingestion_rules") or {})
        self.aim_rules = _deep_merge(DEFAULT_AIM_RULES, configured)

    def enrich_metadata(self, filename: str, source_relative_path: str, raw_text: str, doc_type: str, current_metadata: Dict[str, Any]) -> Dict[str, Any]:
        meta = dict(current_metadata or {})
        rel = _norm_path(source_relative_path or filename)
        name = filename or Path(rel).name
        name_stem = Path(name).stem
        full = f"{rel}/{name}".replace("//", "/")

        content_type = self._content_type(doc_type, name, rel)
        block_number, day_number = self._extract_block_day(full, raw_text)
        block_id = f"B{block_number}" if block_number else (meta.get("block_id") or "")
        day_id = f"B{block_number}D{day_number}" if block_number and day_number else (meta.get("day_id") or "")
        topic = self._topic_from_name(name_stem, content_type)
        version = self._version(content_type, rel)
        excluded = self._is_excluded(rel)
        restricted = self._is_restricted(content_type, name, rel)
        visibility = self._visibility(content_type, restricted, excluded)
        source_priority = self._source_priority(content_type, rel)
        status = "exclude_by_default" if excluded else ("approved_candidate" if source_priority <= 1 else "needs_review")

        meta.update({
            "program_id": meta.get("program_id") or self.aim_rules.get("default_program_id", "aim"),
            "course_id": meta.get("course_id") or self.aim_rules.get("default_course_id", "general_science_i"),
            "document_type": content_type,
            "content_type": content_type,
            "block_id": block_id,
            "block_number": block_number,
            "day_id": day_id,
            "day_number": day_number,
            "title": meta.get("title") or self._clean_title(name_stem, content_type),
            "topic": meta.get("topic") or topic,
            "version": version,
            "visibility": visibility,
            "restricted": restricted,
            "access_level": "admin_only" if restricted else "client",
            "status": status,
            "source_priority": source_priority,
            "is_archive_or_working_version": excluded,
            "is_generation_candidate": (not excluded and not restricted and content_type in self.aim_rules.get("course_generation_source_types", [])),
            "use_for_blueprint": (not excluded and content_type in self.aim_rules.get("blueprint_source_types", [])),
            "use_for_course_generation": (not excluded and not restricted and content_type in self.aim_rules.get("course_generation_source_types", [])),
            "calendar_mapping_required": content_type in self.aim_rules.get("calendar_mapping_required_types", []),
            "mapping_source_preference": self.aim_rules.get("calendar_mapping_strategy", "calendar_first_then_filename"),
        })

        # Assessment/project identifiers. Calendar can later override administered/mapped day.
        quiz_no = self._quiz_number(name)
        if quiz_no:
            meta["quiz_number"] = quiz_no
            meta["quiz_id"] = f"B{block_number}Q{quiz_no}" if block_number else f"Q{quiz_no}"
        project_no = self._project_number(name)
        if project_no:
            meta["project_number"] = project_no
            meta["project_id"] = f"B{block_number}P{project_no}" if block_number else f"P{project_no}"

        # Store filename-derived mapping separately so calendar mapping can safely override it.
        if day_id:
            meta.setdefault("filename_day_id", day_id)
            if content_type in {"lesson_pdf", "slide_deck"}:
                meta.setdefault("mapped_day", day_id)
            else:
                meta.setdefault("suggested_day_from_filename", day_id)
        if content_type in {"quiz", "final_exam", "project", "hangar_activity", "study_questions"}:
            meta.setdefault("mapped_day_source", "calendar_required")

        return meta

    def _content_type(self, doc_type: str, name: str, rel: str) -> str:
        low_name = name.lower()
        low_rel = rel.lower()
        ext = Path(name).suffix.lower().lstrip(".")
        # Quiz context also covers the compact AIM naming (e.g. "B2Q1.docx",
        # "B2Q1.Key.docx") and the Quizzes/ folder, not just the word "quiz".
        is_quiz_ctx = ("quiz" in low_name or "quizzes/" in low_rel
                       or re.search(r"\bb\d*q\d+", low_name) is not None)
        # Answer-key detection must catch both the verbose "... Answer Key.pdf"
        # form and the compact "...Key.docx"/"B2Q1.Key.docx" form. A leading
        # separator (space/dot/dash/underscore) before "key" avoids matching
        # words that merely contain the letters (e.g. "turnbuckle", "monkey").
        is_key = ("answer key" in low_name or "exam key" in low_name or "quiz key" in low_name
                  or re.search(r"[ ._-]key\b", low_name) is not None)
        if is_key:
            if "final exam" in low_name:
                return "final_exam_answer_key"
            if "instructor guide" in low_name:
                return "project_instructor_guide"
            return "quiz_answer_key" if is_quiz_ctx else "project_instructor_guide"
        if "instructor guide" in low_name:
            return "project_instructor_guide"
        if "final exam" in low_name:
            return "final_exam"
        if is_quiz_ctx or doc_type == "quiz_exam":
            return "quiz"
        if "project" in low_name or "projects/" in low_rel:
            return "project"
        if "hangar" in low_rel or "hangar" in low_name:
            return "hangar_activity"
        if "study question" in low_rel or "study question" in low_name or doc_type == "study_questions":
            return "study_questions"
        if "calendar" in low_name or doc_type == "course_calendar":
            return "course_calendar"
        if "syllabus" in low_name or doc_type == "syllabus":
            return "syllabus"
        # "Finalized PDFs" is AIM's folder of PDF lesson decks; the older rule
        # only matched "Final PDFs", so these were falling through to "other".
        if ("final pdf" in low_rel or "finalized pdf" in low_rel) and ext == "pdf":
            return "lesson_pdf"
        if "final slide" in low_rel or ext in {"ppt", "pptx"} or doc_type == "lesson_slide_deck":
            return "slide_deck"
        # Weak, last-resort heuristic: a stray "test" in the name is treated as an
        # exam only if nothing above matched, so procedure names like
        # "Round-Out Test" classify by their folder (hangar activity) first.
        if re.search(r"\btest\b", low_name):
            return "final_exam"
        return doc_type or "other"

    def _extract_block_day(self, text: str, raw_text: str) -> tuple[Optional[int], Optional[int]]:
        pattern = self.aim_rules.get("block_day_pattern") or DEFAULT_AIM_RULES["block_day_pattern"]
        blob = f"{text}\n{(raw_text or '')[:1500]}"
        try:
            m = re.search(pattern, blob, re.I)
        except re.error:
            m = re.search(DEFAULT_AIM_RULES["block_day_pattern"], blob, re.I)
        block_number = None
        day_number = None
        if m:
            gd = m.groupdict()
            block_number = int(gd.get("block_number") or 0) or None
            day_number = int(gd.get("day_number") or 0) or None
        if not block_number:
            m2 = re.search(r"\bBlock\s*0*(\d+)\b", blob, re.I)
            if m2:
                block_number = int(m2.group(1))
        return block_number, day_number

    def _clean_title(self, stem: str, content_type: str) -> str:
        title = re.sub(r"^B\d+D\d+\s*[-_–—]?\s*", "", stem, flags=re.I)
        title = title.replace("_", " ").replace("-", " ")
        title = re.sub(r"\s+", " ", title).strip()
        return title or stem

    def _topic_from_name(self, stem: str, content_type: str) -> str:
        title = self._clean_title(stem, content_type)
        title = re.sub(r"\bDay\s*\d+\b", "", title, flags=re.I)
        title = re.sub(r"\bQuiz\s*\d+\b", "", title, flags=re.I)
        title = re.sub(r"\bfrom\b", "", title, flags=re.I)
        title = re.sub(r"\s+", " ", title).strip(" -–—")
        return title[:120]

    def _version(self, content_type: str, rel: str) -> str:
        low = rel.lower()
        if "updated pdf" in low or "updated pdfs" in low:
            return "updated_pdf"
        if "updated quiz" in low or "updated quizzes" in low:
            return "updated_quiz"
        if "final pdf" in low or "/4-pdf" in low:
            return "pdf_final"
        if "/3-final" in low or "finalized" in low:
            return "final"
        if "archive" in low or "archived" in low:
            return "archive"
        if "1-original" in low:
            return "original"
        if "2-edited" in low:
            return "edited"
        return "unknown"

    def _is_excluded(self, rel: str) -> bool:
        return _contains_any(rel, self.aim_rules.get("exclude_path_contains", []))

    def _is_restricted(self, content_type: str, name: str, rel: str) -> bool:
        if content_type in {"quiz_answer_key", "final_exam_answer_key", "project_instructor_guide"}:
            return True
        return _contains_any(f"{rel}/{name}", self.aim_rules.get("restricted_filename_contains", []))

    def _visibility(self, content_type: str, restricted: bool, excluded: bool) -> str:
        if excluded:
            return "internal_only"
        if restricted:
            return "instructor_only"
        return (self.aim_rules.get("visibility_by_content_type", {}) or {}).get(content_type, "student")

    def _source_priority(self, content_type: str, rel: str) -> int:
        priorities = self.aim_rules.get("version_priority", {}).get(content_type, []) or []
        low = rel.lower()
        for idx, prefix in enumerate(priorities, start=1):
            if str(prefix).lower() in low:
                return idx
        return 99

    def _quiz_number(self, name: str) -> Optional[int]:
        m = re.search(r"\bQuiz\s*0*(\d+)\b", name or "", re.I)
        if not m:
            # Compact AIM form, e.g. "B2Q1", "B2Q10.Key".
            m = re.search(r"\bB\d*Q\s*0*(\d+)\b", name or "", re.I)
        return int(m.group(1)) if m else None

    def _project_number(self, name: str) -> str:
        m = re.search(r"\bProject\s*([0-9]+(?:[-_.][0-9A-Za-z]+)?)", name or "", re.I)
        return m.group(1).replace("_", "-") if m else ""
