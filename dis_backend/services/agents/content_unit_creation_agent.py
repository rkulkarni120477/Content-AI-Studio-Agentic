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
        knowledge_test = state.get('knowledge_test_structure') or {}
        if knowledge_test.get('blocks'):
            # One unit per block sheet, each carrying that block's OWN attribution.
            # Word-chunking this file instead (the else branch) produced units stamped
            # with the whole document's single `block`, which for a 16-sheet AKTR
            # rollup meant every block's performance data was filed under the first
            # one and invisible to `metadata_json->>'block' = %s` for the other
            # fifteen. Chunk boundaries also split a block's table in half.
            unit_type = ctx.cfg.document_processing.unit_type_map.get(effective_type, 'knowledge_test_item')
            for i, blk in enumerate(knowledge_test['blocks'], 1):
                codes = [c.get('acs_code') for c in blk.get('codes') or [] if c.get('acs_code')]
                text = blk.get('source_text') or ''
                units.append({
                    'content_unit_id': f"{state['job_id']}:knowledge_test_{blk.get('block_number') or i}",
                    'unit_type': unit_type,
                    'unit_number': i,
                    'title': blk.get('caption') or f"{blk.get('block')} — Most Missed ACS Codes",
                    'text': text,
                    'visual_summary': '',
                    'keywords': list(dict.fromkeys(keywords(text) + codes)),
                    'topics': keywords(text, limit=12),
                    # doc_metadata is spread FIRST so this block's own attribution
                    # wins over the document-level (deliberately blank) one.
                    'metadata': {
                        **state.get('doc_metadata', {}),
                        'block': blk.get('block'),
                        'block_number': blk.get('block_number'),
                        'block_id': f"B{blk.get('block_number')}" if blk.get('block_number') else '',
                        'acs_codes': codes,
                        'missed_codes': blk.get('codes') or [],
                        'sheet_name': blk.get('sheet'),
                    },
                    'assets': [],
                })
        elif doc_type == 'course_calendar' and calendar.get('days'):
            for day in calendar.get('days', []):
                day_no = day.get('day_number') or len(units) + 1
                text = day.get('source_text') or day.get('topic') or ''
                # Carry structured calendar fields into unit metadata so they are
                # searchable/filterable in the vector store and usable to tag other
                # ingested content (quiz/project/lesson) back to a block+day. Only
                # keys present on the day survive, so non-AIM calendars are unaffected.
                # These units are persisted to BOTH the vector store (OpenSearch) and
                # the structure store (RDS) downstream; metadata_json in RDS preserves
                # these same fields. See services/aim_calendar.py "Storage scope".
                cal_meta = {k: day[k] for k in (
                    'block_id', 'block_number', 'subject_unit', 'subject_day',
                    'day_type', 'acs_codes', 'acs_codes_raw', 'handbook_refs',
                    'projects', 'project_acs_codes', 'quiz', 'supplemental_resources',
                    'test_prep_activities', 'hangar_activities',
                ) if k in day}
                unit_keywords = list(dict.fromkeys(keywords(text) + list(day.get('acs_codes') or [])))
                # doc_metadata is spread FIRST, and this day's own day_number LAST.
                # Spread last, it clobbered the per-day value with the DOCUMENT's
                # day_number — which for a calendar is None (the AIM profile derives it
                # from a B#D# filename token that a whole-block calendar has no reason
                # to carry). Measured on Block 6: all 20 calendar_day units stored
                # day_number=None, so ENUMERATE could attribute none of them, acs_by_day
                # came out empty for every day, and each day row reached the Blueprint
                # with no ACS codes at all.
                units.append({'content_unit_id': f"{state['job_id']}:calendar_day_{day_no}", 'unit_type': ctx.cfg.document_processing.unit_type_map.get('course_calendar', 'calendar_day'), 'unit_number': int(day_no), 'title': day.get('lesson_title') or f'Day {day_no}', 'text': text, 'visual_summary': '', 'keywords': unit_keywords, 'topics': keywords((day.get('topic') or '') + ' ' + text, limit=12), 'metadata': {**state.get('doc_metadata', {}), 'block': calendar.get('block') or (state.get('doc_metadata') or {}).get('block'), **cal_meta, 'day_number': day_no}, 'assets': []})
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

