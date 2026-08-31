"""Reusable Phase 8 DIS tenant metadata templates.

Families (explicit selection only):

- minimal     identity wrapper; runtime defaults for schema/UI/registry
- academic    reusable academic metadata_schemas + source_ui (not an AIM clone)
- publishing  reusable publishing metadata_schemas + source_ui (not a Cengage clone)

Templates never copy DEFAULT_FIELDS, secrets, stores, document_processing,
client profiles, or AIM Topic. Topic is override-only.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict

TEMPLATE_FAMILIES = frozenset({"minimal", "academic", "publishing"})
PROVISIONING_STAMP_VERSION = "1"

# Academic field set is the AIM/Academian overlap minus Topic (DECISION-004)
# and minus AIM-only knowledge_test types.
_ACADEMIC_DOCUMENT_TYPES = [
    "course_calendar",
    "syllabus",
    "lesson_slide_deck",
    "project_activity",
    "project_key",
    "quiz_exam",
    "quiz_answer_key",
    "study_questions",
    "ebook_reference",
    "instructor_guide",
    "student_handout",
    "other",
]

_ACADEMIC_UNIT_TYPES = [
    "calendar_day",
    "syllabus_section",
    "project_task",
    "quiz_question",
    "answer_key_item",
    "slide",
    "page",
    "chunk",
    "image",
]

_PUBLISHING_DOCUMENT_TYPES = [
    "textbook_pdf",
    "chapter_manuscript",
    "appendix_manuscript",
    "cdd_layout_pdf",
    "mudd_layout_pdf",
    "transition_guide",
    "authoring_guidelines",
    "art_guidelines",
    "art_manuscript",
    "ppt_template",
    "csv_manifest",
    "other",
]


def _academic_schema_body() -> Dict[str, Any]:
    return {
        "required_fields": [
            {
                "name": "course_name",
                "type": "string",
                "hint": "Course name inferred from folder, file name, or source content",
            },
            {"name": "document_type", "type": "enum", "values": list(_ACADEMIC_DOCUMENT_TYPES)},
            {"name": "unit_type", "type": "enum", "values": list(_ACADEMIC_UNIT_TYPES)},
        ],
        "optional_fields": [
            {"name": "program", "type": "string"},
            {"name": "block", "type": "string"},
            {"name": "module_name", "type": "string"},
            {"name": "lesson_name", "type": "string"},
            {
                "name": "difficulty_level",
                "type": "enum",
                "values": ["introductory", "intermediate", "advanced"],
            },
            {"name": "keywords", "type": "list"},
            {"name": "visual_summary", "type": "string"},
        ],
    }


def _academic_source_ui() -> Dict[str, Any]:
    return {
        "common_filters": ["purpose", "document_type", "visibility", "status", "search"],
        "taxonomy_filters": [
            {"key": "course_name", "label": "Course", "type": "select"},
            {"key": "block", "label": "Block", "type": "select"},
            {"key": "module_name", "label": "Module", "type": "select"},
        ],
        "upload": {
            "fields": [
                {"key": "document_type", "label": "Document Type", "control": "select"},
                {"key": "module_name", "label": "Module", "control": "text", "order": 1},
            ],
        },
    }


def _publishing_schema_body() -> Dict[str, Any]:
    # Domain fields only. System-derived keys (file_sha256, content_hash,
    # client_id, source_file_name) are not tenant metadata configuration.
    return {
        "required_fields": [
            {"name": "document_type", "type": "enum", "values": list(_PUBLISHING_DOCUMENT_TYPES)},
            {
                "name": "access_level",
                "type": "enum",
                "values": ["public", "internal", "instructor_only", "restricted"],
            },
        ],
        "optional_fields": [
            {"name": "product_title", "type": "string", "hint": "Book/product title"},
            {"name": "product_code", "type": "string"},
            {"name": "isbn_13", "type": "string"},
            {"name": "edition", "type": "string"},
            {"name": "copyright_year", "type": "string"},
            {"name": "chapter_number", "type": "string"},
            {"name": "chapter_title", "type": "string"},
            {"name": "section_number", "type": "string"},
            {"name": "section_title", "type": "string"},
            {"name": "learning_objective_id", "type": "string"},
            {"name": "learning_objective_text", "type": "string"},
            {"name": "visual_summary", "type": "string"},
        ],
    }


def _publishing_source_ui() -> Dict[str, Any]:
    # Presentation keys match the Field Registry (chapter/module/learning_objective
    # aliases). This is not a second metadata authority.
    return {
        "common_filters": ["purpose", "document_type", "visibility", "status", "search"],
        "taxonomy_filters": [
            {"key": "course_name", "label": "Course / Product", "type": "select"},
            {"key": "chapter", "label": "Chapter", "type": "select"},
            {"key": "module", "label": "Module / Section", "type": "select"},
            {"key": "learning_objective", "label": "Learning Objective", "type": "text"},
        ],
    }


def template_sections(family: str) -> Dict[str, Any]:
    """Return deepcopy sections for *family* (no identity, no stamp)."""
    if family == "minimal":
        return {}
    if family == "academic":
        return {
            "metadata_schemas_body": _academic_schema_body(),
            "source_ui": _academic_source_ui(),
        }
    if family == "publishing":
        return {
            "metadata_schemas_body": _publishing_schema_body(),
            "source_ui": _publishing_source_ui(),
        }
    raise KeyError(family)


def deepcopy_sections(family: str) -> Dict[str, Any]:
    return deepcopy(template_sections(family))
