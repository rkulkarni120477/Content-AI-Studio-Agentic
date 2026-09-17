"""Agent for DIS pipeline step: content_unit_creation."""
from __future__ import annotations

import hashlib
import json
import re
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
from services.ebook_page_chunker import (
    build_ebook_page_units,
    extract_pdf_pages,
    outline_chapter_map,
    parse_page_texts_from_raw,
)
from services.ebook_page_tagger import tag_ebook_page_units, tagger_config_from_pipeline


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
        elif doc_type == 'course_calendar' and (calendar.get('sheets') or calendar.get('days')):
            # One Source Library / OpenSearch unit PER SHEET (title = sheet name),
            # not per teaching day. Day rows live in Postgres dis_calendar_days for
            # enumerate/Blueprint. A 32-sheet ALL-blocks workbook must not explode
            # into ~640 section dropdown entries.
            from services.aim_calendar import iter_calendar_sheets
            sheets = iter_calendar_sheets(calendar)
            doc_meta = dict(state.get('doc_metadata') or {})
            # Drop document-level block so multi-sheet units keep their own stamp.
            if len(sheets) > 1:
                doc_meta.pop('block', None)
                doc_meta.pop('block_number', None)
                doc_meta.pop('block_id', None)
            for i, sheet in enumerate(sheets, 1):
                days = sheet.get('days') or []
                texts = [str(d.get('source_text') or d.get('topic') or '').strip() for d in days]
                text = "\n\n".join(t for t in texts if t)
                acs: List[str] = []
                seen_acs = set()
                for d in days:
                    for code in d.get('acs_codes') or []:
                        if code and code not in seen_acs:
                            seen_acs.add(code)
                            acs.append(code)
                sheet_name = sheet.get('sheet_name') or f"Sheet {i}"
                sheet_index = int(sheet.get('sheet_index') if sheet.get('sheet_index') is not None else i - 1)
                block = sheet.get('block') or (doc_meta.get('block') if len(sheets) == 1 else None)
                block_number = sheet.get('block_number')
                if block_number is None and block:
                    m = re.search(r"(\d+)", str(block))
                    block_number = int(m.group(1)) if m else None
                units.append({
                    'content_unit_id': f"{state['job_id']}:calendar_sheet_{sheet_index}",
                    'unit_type': 'calendar_sheet',
                    'unit_number': i,
                    'title': sheet_name,
                    'text': text,
                    'visual_summary': '',
                    'keywords': list(dict.fromkeys(keywords(text) + acs)),
                    'topics': keywords(text, limit=12),
                    'metadata': {
                        **doc_meta,
                        'block': block,
                        'block_number': block_number,
                        'block_id': sheet.get('block_id') or (f"B{block_number}" if block_number else ''),
                        'sheet_name': sheet_name,
                        'sheet_index': sheet_index,
                        'schedule': sheet.get('schedule') or 'unknown',
                        'total_days': int(sheet.get('total_days_detected') or len(days)),
                        'acs_codes': acs,
                        'document_type': 'course_calendar',
                        'content_type': 'course_calendar',
                    },
                    'assets': [],
                })
            self._tag_calendar_sheet_units(state, units)
        elif (
            str(effective_type or '').lower() == 'hangar_activity'
            and (state.get('hangar_structure') or {}).get('sheets')
        ):
            units = self._hangar_sheet_units(state)
        elif str(effective_type or doc_type or '').lower() == 'hangar_activity' and (
            str(state.get('file_type') or '').lower() == 'pdf'
            or str(state.get('filename') or '').lower().endswith('.pdf')
        ):
            # Hangar PDFs: one unit per page (ebook pattern), not word-chunks.
            units = self._hangar_page_units(state)
        elif str(effective_type or doc_type or '').lower() == 'ebook_reference':
            units = self._ebook_page_units(state, effective_type or doc_type)
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

    def _tag_calendar_sheet_units(self, state: PipelineState, units: List[Dict[str, Any]]) -> None:
        """Ebook-style LLM topics/summary/ACS on each calendar sheet (fail-soft)."""
        if not units:
            return
        from services.calendar_sheet_tagger import tag_calendar_sheet_units
        from services.ebook_page_tagger import mark_units_pending, tagger_config_from_pipeline

        ctx = self.ctx
        use_llm = (
            ctx.cfg.pipeline.llm_provider != 'mock'
            and (ctx.cfg.pipeline.bedrock_enabled or ctx.cfg.pipeline.anthropic_enabled)
        )
        tcfg = tagger_config_from_pipeline(ctx.cfg.pipeline)
        if use_llm and tcfg['enabled']:
            errors = state.setdefault('errors', [])
            try:
                tag_calendar_sheet_units(
                    units,
                    call_llm_fn=call_llm,
                    model_id=tcfg['model_id'],
                    batch_size=tcfg['batch_size'],
                    enabled=True,
                    token_guard=getattr(ctx, 'guard', None),
                    errors=errors,
                )
            except TokenLimitError as exc:
                errors.append(str(exc))
                mark_units_pending(units)
        else:
            mark_units_pending(units)

    def _hangar_sheet_units(self, state: PipelineState) -> List[Dict[str, Any]]:
        """One Source Library / OpenSearch unit per Block hangar sheet."""
        from services.aim_hangar import iter_hangar_sheets

        hangar = state.get('hangar_structure') or {}
        sheets = iter_hangar_sheets(hangar)
        doc_meta = dict(state.get('doc_metadata') or {})
        if len(sheets) > 1:
            doc_meta.pop('block', None)
            doc_meta.pop('block_number', None)
            doc_meta.pop('block_id', None)
        units: List[Dict[str, Any]] = []
        for i, sheet in enumerate(sheets, 1):
            days = sheet.get('days') or []
            texts = [str(d.get('source_text') or '').strip() for d in days]
            text = "\n\n".join(t for t in texts if t)
            acs: List[str] = []
            seen_acs = set()
            for d in days:
                for code in d.get('acs_codes') or []:
                    if code and code not in seen_acs:
                        seen_acs.add(code)
                        acs.append(code)
            sheet_name = sheet.get('sheet_name') or f"Sheet {i}"
            sheet_index = int(
                sheet.get('sheet_index') if sheet.get('sheet_index') is not None else i - 1
            )
            block = sheet.get('block') or (doc_meta.get('block') if len(sheets) == 1 else None)
            block_number = sheet.get('block_number')
            if block_number is None and block:
                m = re.search(r"(\d+)", str(block))
                block_number = int(m.group(1)) if m else None
            units.append({
                'content_unit_id': f"{state['job_id']}:hangar_sheet_{sheet_index}",
                'unit_type': 'hangar_sheet',
                'unit_number': i,
                'title': sheet_name,
                'text': text,
                'visual_summary': '',
                'keywords': list(dict.fromkeys(keywords(text) + acs)),
                'topics': keywords(text, limit=12),
                'metadata': {
                    **doc_meta,
                    'block': block,
                    'block_number': block_number,
                    'block_id': sheet.get('block_id') or (f"B{block_number}" if block_number else ''),
                    'sheet_name': sheet_name,
                    'sheet_index': sheet_index,
                    'schedule': sheet.get('schedule') or 'unknown',
                    'total_days': int(sheet.get('total_days_with_hangar') or len(days)),
                    'acs_codes': acs,
                    'document_type': 'hangar_activity',
                    'content_type': 'hangar_activity',
                    'tagging_status': 'pending',
                },
                'assets': [],
            })
        self._tag_hangar_sheet_units(state, units)
        return units

    def _tag_hangar_sheet_units(self, state: PipelineState, units: List[Dict[str, Any]]) -> None:
        if not units:
            return
        from services.hangar_sheet_tagger import tag_hangar_sheet_units
        from services.ebook_page_tagger import mark_units_pending, tagger_config_from_pipeline

        ctx = self.ctx
        use_llm = (
            ctx.cfg.pipeline.llm_provider != 'mock'
            and (ctx.cfg.pipeline.bedrock_enabled or ctx.cfg.pipeline.anthropic_enabled)
        )
        tcfg = tagger_config_from_pipeline(ctx.cfg.pipeline)
        if use_llm and tcfg['enabled']:
            errors = state.setdefault('errors', [])
            try:
                tag_hangar_sheet_units(
                    units,
                    call_llm_fn=call_llm,
                    model_id=tcfg['model_id'],
                    batch_size=tcfg['batch_size'],
                    enabled=True,
                    token_guard=getattr(ctx, 'guard', None),
                    errors=errors,
                )
            except TokenLimitError as exc:
                errors.append(str(exc))
                mark_units_pending(units)
        else:
            mark_units_pending(units)

    def _hangar_page_units(self, state: PipelineState) -> List[Dict[str, Any]]:
        """One content unit per physical hangar PDF page, with LLM tags."""
        ctx = self.ctx
        raw_bytes = state.get('raw_bytes') or b''
        page_texts: List[Dict[str, Any]] = []
        filename = str(state.get('filename') or '').lower()
        is_pdf = (
            str(state.get('file_type') or '').lower() == 'pdf'
            or filename.endswith('.pdf')
        )

        if raw_bytes and is_pdf:
            page_texts = extract_pdf_pages(raw_bytes, max_chars=0)
            if page_texts:
                state['page_texts'] = page_texts
                state['page_count'] = len(page_texts)
                state['raw_text'] = "\n\n".join(
                    f"[Page {p['pdf_page']}]\n{p.get('text') or ''}" for p in page_texts
                )
        if not page_texts:
            page_texts = list(state.get('page_texts') or [])
        if not page_texts:
            page_texts = parse_page_texts_from_raw(state.get('raw_text') or '')

        title = (state.get('doc_metadata') or {}).get('title') or state.get('filename') or 'Source'
        doc_meta = dict(state.get('doc_metadata') or {})
        doc_meta['document_type'] = 'hangar_activity'
        doc_meta['content_type'] = 'hangar_activity'
        # Short hangar PDFs rarely have ebook outlines — skip chapter citation.
        units = build_ebook_page_units(
            job_id=state['job_id'],
            pages=page_texts,
            doc_metadata=doc_meta,
            unit_type='hangar_page',
            title=title,
            outline_map={},
        )
        # Ensure UI can label pages even without chapter-printed page_number.
        for u in units:
            meta = dict(u.get('metadata') or {})
            if not meta.get('page_number') and meta.get('pdf_page') is not None:
                meta['page_number'] = str(meta['pdf_page'])
            meta['document_type'] = 'hangar_activity'
            meta['content_type'] = 'hangar_activity'
            u['metadata'] = meta

        use_llm = (
            ctx.cfg.pipeline.llm_provider != 'mock'
            and (ctx.cfg.pipeline.bedrock_enabled or ctx.cfg.pipeline.anthropic_enabled)
        )
        tcfg = tagger_config_from_pipeline(ctx.cfg.pipeline)
        if use_llm and tcfg['enabled'] and units:
            errors = state.setdefault('errors', [])
            try:
                from services.hangar_page_tagger import tag_hangar_page_units
                tag_hangar_page_units(
                    units,
                    call_llm_fn=call_llm,
                    model_id=tcfg['model_id'],
                    batch_size=tcfg['batch_size'],
                    enabled=True,
                    token_guard=getattr(ctx, 'guard', None),
                    errors=errors,
                )
            except TokenLimitError as exc:
                errors.append(str(exc))
                from services.ebook_page_tagger import mark_units_pending
                mark_units_pending(units)
        else:
            from services.ebook_page_tagger import mark_units_pending
            mark_units_pending(units)
        return units

    def _ebook_page_units(self, state: PipelineState, effective_type: str) -> List[Dict[str, Any]]:
        """One content unit per physical PDF page, with location + LLM tags."""
        ctx = self.ctx
        raw_bytes = state.get('raw_bytes') or b''
        page_texts: List[Dict[str, Any]] = []

        # Classification runs AFTER extraction, so the first pass may have been
        # truncated by max_extracted_chars (~250k). For ebook_reference always
        # re-extract with no cap when raw bytes are available — page_count on a
        # truncated ExtractionResult equals pages_read, so comparing lengths
        # cannot detect the truncate.
        if raw_bytes:
            page_texts = extract_pdf_pages(raw_bytes, max_chars=0)
            if page_texts:
                state['page_texts'] = page_texts
                state['page_count'] = len(page_texts)
                state['raw_text'] = "\n\n".join(
                    f"[Page {p['pdf_page']}]\n{p.get('text') or ''}" for p in page_texts
                )
        if not page_texts:
            page_texts = list(state.get('page_texts') or [])
        if not page_texts:
            page_texts = parse_page_texts_from_raw(state.get('raw_text') or '')

        title = (state.get('doc_metadata') or {}).get('title') or state.get('filename') or 'Source'
        unit_type = ctx.cfg.document_processing.unit_type_map.get(effective_type, 'page')
        outline = outline_chapter_map(raw_bytes) if raw_bytes else {}
        units = build_ebook_page_units(
            job_id=state['job_id'],
            pages=page_texts,
            doc_metadata=state.get('doc_metadata') or {},
            unit_type=unit_type,
            title=title,
            outline_map=outline,
        )

        # Batched per-page LLM content tags. Fail-soft: heuristic topics stay.
        use_llm = (
            ctx.cfg.pipeline.llm_provider != 'mock'
            and (ctx.cfg.pipeline.bedrock_enabled or ctx.cfg.pipeline.anthropic_enabled)
        )
        tcfg = tagger_config_from_pipeline(ctx.cfg.pipeline)
        if use_llm and tcfg['enabled'] and units:
            errors = state.setdefault('errors', [])
            try:
                tag_ebook_page_units(
                    units,
                    call_llm_fn=call_llm,
                    model_id=tcfg['model_id'],
                    batch_size=tcfg['batch_size'],
                    enabled=True,
                    token_guard=ctx.guard,
                    errors=errors,
                )
            except TokenLimitError as exc:
                errors.append(str(exc))
        return units
