"""Agent for DIS pipeline step: specialized_structure_extraction."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List

from services.agents.base import BasePipelineAgent
from services.pipeline.common import PipelineState, call_llm, safe_json, keywords
from services.pipeline.extractors import ExtractionResult, extract
from services.specialized_extractors import (
    infer_doc_type,
    infer_block,
    extract_calendar_structure,
    extract_syllabus_structure,
    extract_quiz_structure,
    extract_project_structure,
)
from services.indexing import (
    rds_upsert as do_rds_upsert,
    generate_embeddings,
    opensearch_upsert as do_opensearch_upsert,
)
from services.token_guard import TokenLimitError


class SpecializedStructureExtractionAgent(BasePipelineAgent):
    """Specialized Structure Extraction agent.

    This is a LangGraph node. Update this file when this step behavior changes.
    """
    step_name = "specialized_structure_extraction"
    purpose = "Specialized Structure Extraction"

    def run(self, state: PipelineState) -> PipelineState:
        ctx = self.ctx
        doc_type = state.get('doc_type', '')
        filename = state.get('filename', '')
        text = state.get('raw_text', '') or ''
        tables = state.get('tables', []) or []
        doc_processing = ctx.cfg.document_processing
        state['specialized_structure_type'] = 'none'
        if doc_type == 'course_calendar':
            state['calendar_structure'] = extract_calendar_structure(filename, text, tables, doc_processing)
            state['specialized_structure_type'] = 'course_calendar'
            state.setdefault('doc_metadata', {})['block'] = state['calendar_structure'].get('block') or infer_block(filename, text, doc_processing.structure_patterns)
            state.setdefault('doc_metadata', {})['calendar_days_detected'] = len(state['calendar_structure'].get('days', []))
        elif doc_type == 'syllabus':
            state['syllabus_structure'] = extract_syllabus_structure(filename, text, tables, doc_processing)
            state['specialized_structure_type'] = 'syllabus'
            state.setdefault('doc_metadata', {})['block'] = state['syllabus_structure'].get('block') or infer_block(filename, text, doc_processing.structure_patterns)
        elif doc_type in {'quiz_exam', 'quiz_answer_key'}:
            state['quiz_structure'] = extract_quiz_structure(filename, text, doc_processing)
            state['specialized_structure_type'] = state['quiz_structure'].get('structure_type', doc_type)
        elif doc_type in {'project_activity', 'project_key', 'instructor_guide'}:
            state['project_structure'] = extract_project_structure(filename, text, doc_processing)
            state['specialized_structure_type'] = state['project_structure'].get('structure_type', doc_type)
        return ctx.step_done(state, 'specialized_structure_extraction')

        # =============================================================================
        # STEP: content_unit_creation
        # Purpose: Content Unit Creation pipeline step.
        # =============================================================================

