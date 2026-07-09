"""Agent for DIS pipeline step: content_unit_creation."""
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


class ContentUnitCreationAgent(BasePipelineAgent):
    """Content Unit Creation agent.

    This is a LangGraph node. Update this file when this step behavior changes.
    """
    step_name = "content_unit_creation"
    purpose = "Content Unit Creation"

    def run(self, state: PipelineState) -> PipelineState:
        ctx = self.ctx
        units: List[Dict[str, Any]] = []
        doc_type = state.get('doc_type', '')
        effective_type = (state.get('doc_metadata') or {}).get('content_type') or doc_type
        calendar = state.get('calendar_structure') or {}
        if doc_type == 'course_calendar' and calendar.get('days'):
            for day in calendar.get('days', []):
                day_no = day.get('day_number') or len(units) + 1
                text = day.get('source_text') or day.get('topic') or ''
                units.append({'content_unit_id': f"{state['job_id']}:calendar_day_{day_no}", 'unit_type': ctx.cfg.document_processing.unit_type_map.get('course_calendar', 'calendar_day'), 'unit_number': int(day_no), 'title': day.get('lesson_title') or f'Day {day_no}', 'text': text, 'visual_summary': '', 'keywords': keywords(text), 'topics': keywords((day.get('topic') or '') + ' ' + text, limit=12), 'metadata': {'day_number': day_no, 'block': calendar.get('block'), **state.get('doc_metadata', {})}, 'assets': []})
        else:
            slide_texts = state.get('slide_texts', []) or []
            visual_map = {v.get('unit_number'): v.get('visual_summary') for v in state.get('visual_units', []) or []}
            if slide_texts:
                for i, text in enumerate(slide_texts, 1):
                    lines = [l.strip() for l in text.splitlines() if l.strip()]
                    title = next((l.strip('# ') for l in lines if not l.startswith('[Slide')), f'Slide {i}')
                    units.append({'content_unit_id': f"{state['job_id']}:slide_{i}", 'unit_type': ctx.cfg.document_processing.unit_type_map.get(effective_type, ctx.cfg.document_processing.unit_type_map.get(doc_type, 'slide')), 'unit_number': i, 'title': title[:160], 'text': text, 'visual_summary': visual_map.get(i, ''), 'keywords': keywords(text), 'topics': keywords(title + ' ' + text, limit=10), 'metadata': {'slide_number': i, **state.get('doc_metadata', {})}, 'assets': []})
            else:
                text = state.get('raw_text', '') or ''
                words = text.split()
                doc_type_norm = str(effective_type or doc_type or '').lower()
                meta = state.get('doc_metadata', {}) or {}
                style_doc_types = {'style_guide', 'authoring_guide', 'authoring_guidelines', 'copyediting_guidelines', 'sample_lesson', 'sample_chapter', 'approved_template'}
                cdd_doc_types = {'syllabus', 'course_outline', 'program_overview', 'learning_objectives'}
                blueprint_doc_types = {'course_calendar', 'syllabus', 'chapter_outline', 'module_map', 'block_schedule'}
                purpose = str(meta.get('purpose') or '').strip().lower().replace('-', '_')
                # Product rule for CAS: style/CDD/blueprint reference files must
                # remain coherent. They are usually small authoritative documents,
                # and splitting them loses rule/order context. Only broader course
                # generation content uses semantic chunks.
                if (doc_type_norm in style_doc_types or doc_type_norm in cdd_doc_types or doc_type_norm in blueprint_doc_types
                        or bool(meta.get('use_for_style')) or bool(meta.get('use_for_cdd')) or bool(meta.get('use_for_blueprint'))
                        or purpose in {'style', 'cdd', 'blueprint'}):
                    size = max(len(words), 1)
                    overlap = 0
                else:
                    size = max(200, ctx.cfg.pipeline.chunk_size)
                    overlap = min(ctx.cfg.pipeline.chunk_overlap, size // 4)
                step = max(1, size - overlap)
                idx = 1
                for pos in range(0, len(words), step):
                    chunk = ' '.join(words[pos:pos + size]).strip()
                    if not chunk:
                        continue
                    units.append({'content_unit_id': f"{state['job_id']}:unit_{idx}", 'unit_type': ctx.cfg.document_processing.unit_type_map.get(effective_type, ctx.cfg.document_processing.unit_type_map.get(doc_type, 'page' if state.get('file_type') == 'pdf' else 'chunk')), 'unit_number': idx, 'title': state.get('doc_metadata', {}).get('title', state.get('filename', 'Source')), 'text': chunk, 'visual_summary': '', 'keywords': keywords(chunk), 'topics': keywords(chunk, limit=10), 'metadata': {'chunk_index': idx - 1, **state.get('doc_metadata', {})}, 'assets': []})
                    idx += 1
        state['content_units'] = units
        state['chunks'] = [{'chunk_id': u['content_unit_id'], 'text': u['text'], 'chunk_index': u['unit_number'] - 1} for u in units]
        return ctx.step_done(state, 'content_unit_creation')

        # =============================================================================
        # STEP: quality_check
        # Purpose: Quality Check pipeline step.
        # =============================================================================

