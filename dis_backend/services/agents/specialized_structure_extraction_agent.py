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
from services.blocks import block_label


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
        # metadata_tagging runs before this step, so the client profile's
        # content_type is already resolved and is the authoritative type (it is what
        # unit_type_map and the retrieval gates key on). It is checked first because
        # an AKTR rollup that the classifier typed as an assessment would otherwise
        # be captured by the quiz branch below.
        effective_type = (state.get('doc_metadata') or {}).get('content_type') or doc_type
        if effective_type == 'knowledge_test_report':
            state['knowledge_test_structure'] = self._extract_knowledge_test(state, filename)
            state['specialized_structure_type'] = 'knowledge_test_report'
            blocks = state['knowledge_test_structure'].get('blocks') or []
            meta = state.setdefault('doc_metadata', {})
            # The rollup covers many blocks, so record WHICH ones instead of a single
            # `block`. enrich_metadata blanks the doc-level block for this type; the
            # per-block attribution lives on the content units.
            meta['blocks_covered'] = [b.get('block') for b in blocks]
            meta['knowledge_test_blocks_detected'] = len(blocks)
            skipped = state['knowledge_test_structure'].get('skipped_sheets') or []
            if skipped:
                state.setdefault('errors', []).append(
                    'knowledge_test_report: no performance data read from '
                    + ', '.join(f"{s.get('sheet')} ({s.get('reason')})" for s in skipped))
        elif doc_type == 'course_calendar':
            state['calendar_structure'] = self._extract_calendar(state, filename, text, tables, doc_processing)
            state['specialized_structure_type'] = 'course_calendar'
            state.setdefault('doc_metadata', {})['block'] = block_label(state['calendar_structure'].get('block') or infer_block(filename, text, doc_processing.structure_patterns))
            state.setdefault('doc_metadata', {})['calendar_days_detected'] = len(state['calendar_structure'].get('days', []))
        elif doc_type == 'syllabus':
            state['syllabus_structure'] = extract_syllabus_structure(filename, text, tables, doc_processing)
            state['specialized_structure_type'] = 'syllabus'
            state.setdefault('doc_metadata', {})['block'] = block_label(state['syllabus_structure'].get('block') or infer_block(filename, text, doc_processing.structure_patterns))
        elif doc_type in {'quiz_exam', 'quiz_answer_key'} or str(effective_type).lower() in {
            'quiz', 'quiz_exam', 'final_exam', 'quiz_answer_key', 'final_exam_answer_key',
        }:
            # AIM remaps quiz_exam → quiz / final_exam on content_type after
            # classification. Using only pipeline doc_type skipped exam structure
            # once the LLM classifier (or an empty extract) left doc_type as other.
            state['quiz_structure'] = extract_quiz_structure(filename, text, doc_processing)
            state['specialized_structure_type'] = state['quiz_structure'].get('structure_type', doc_type or effective_type)
        elif doc_type in {'project_activity', 'project_key', 'instructor_guide'}:
            state['project_structure'] = extract_project_structure(filename, text, doc_processing)
            state['specialized_structure_type'] = state['project_structure'].get('structure_type', doc_type)
        return ctx.step_done(state, 'specialized_structure_extraction')

    def _extract_knowledge_test(self, state, filename):
        """Parse an AKTR workbook into one record per block.

        Needs the workbook itself: the flattened text loses which sheet a row came
        from, and the sheet IS the block. Without raw bytes there is nothing to
        parse, so an empty result is returned and the generic word-chunking path
        takes over — which cannot attribute blocks, hence the recorded reason.
        """
        raw_bytes = state.get('raw_bytes') or b''
        if not raw_bytes:
            state.setdefault('errors', []).append(
                'knowledge_test_report: raw bytes unavailable, per-block attribution skipped')
            return {'structure_type': 'knowledge_test_report', 'blocks': [], 'skipped_sheets': []}
        try:
            from services.knowledge_test_report import (
                looks_like_aktr_report,
                build_knowledge_test_structure,
            )
            if not looks_like_aktr_report(raw_bytes):
                state.setdefault('errors', []).append(
                    'knowledge_test_report: no AKTR sheet structure found, per-block attribution skipped')
                return {'structure_type': 'knowledge_test_report', 'blocks': [], 'skipped_sheets': []}
            return build_knowledge_test_structure(raw_bytes, filename)
        except Exception as exc:  # noqa: BLE001 — never sink the document over this
            state.setdefault('errors', []).append(f'knowledge_test_report: {exc}')
            return {'structure_type': 'knowledge_test_report', 'blocks': [], 'skipped_sheets': []}

    def _extract_calendar(self, state, filename, text, tables, doc_processing):
        """Use the AIM column-aware parser for AIM teacher-calendar workbooks;
        fall back to the generic row-based extractor for everything else."""
        raw_bytes = state.get('raw_bytes') or b''
        is_excel = str(filename).lower().endswith(('.xlsx', '.xls'))
        if raw_bytes and is_excel:
            try:
                from services.aim_calendar import (
                    looks_like_aim_teacher_calendar,
                    build_calendar_structure,
                )
                if looks_like_aim_teacher_calendar(raw_bytes):
                    block_hint = (state.get('doc_metadata') or {}).get('block')
                    return build_calendar_structure(raw_bytes, filename, block_hint)
                state.setdefault('errors', []).append(
                    f'course_calendar: {filename} is a spreadsheet but not in the AIM '
                    'teacher-calendar layout; parsed with the generic row extractor')
            except Exception as exc:  # noqa: BLE001 -> safe generic fallback below
                # Recorded, not swallowed: the generic extractor numbers every row
                # day 1 on an AIM layout, so "the AIM parser raised" and "this is not
                # an AIM calendar" have wildly different consequences and used to be
                # the same silent `pass`.
                state.setdefault('errors', []).append(
                    f'course_calendar: AIM parser failed on {filename} '
                    f'({type(exc).__name__}: {exc}); fell back to the generic extractor')
        return extract_calendar_structure(filename, text, tables, doc_processing)

        # =============================================================================
        # STEP: content_unit_creation
        # Purpose: Content Unit Creation pipeline step.
        # =============================================================================

