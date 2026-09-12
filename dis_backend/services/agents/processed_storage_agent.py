"""Agent for DIS pipeline step: processed_storage."""
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
from services.source_library import write_source_content_and_index


class ProcessedStorageAgent(BasePipelineAgent):
    """Processed Storage agent.

    This is a LangGraph node. Update this file when this step behavior changes.
    """
    step_name = "processed_storage"
    purpose = "Processed Storage"

    def run(self, state: PipelineState) -> PipelineState:
        ctx = self.ctx
        prefix = ctx.writer.job_prefix(state.get('namespace', 'unknown'), state['job_id'])
        urls = state.setdefault('artifact_urls', {})
        urls['extracted_text'] = ctx.writer.write_json(f'{prefix}/extracted/extracted_text.json', {'text': state.get('raw_text', ''), 'page_count': state.get('page_count', 0)})
        urls['metadata'] = ctx.writer.write_json(f'{prefix}/extracted/metadata.json', state.get('doc_metadata', {}))
        urls['structure'] = ctx.writer.write_json(f'{prefix}/extracted/structure.json', state.get('structured_sections', []))
        urls['content_units'] = ctx.writer.write_json(f'{prefix}/extracted/content_units.json', state.get('content_units', []))
        if state.get('calendar_structure'):
            urls['calendar_structure'] = ctx.writer.write_json(f'{prefix}/extracted/calendar_structure.json', state.get('calendar_structure', {}))
        if state.get('syllabus_structure'):
            urls['syllabus_structure'] = ctx.writer.write_json(f'{prefix}/extracted/syllabus_structure.json', state.get('syllabus_structure', {}))
        if state.get('quiz_structure'):
            urls['quiz_structure'] = ctx.writer.write_json(f'{prefix}/extracted/quiz_structure.json', state.get('quiz_structure', {}))
        if state.get('project_structure'):
            urls['project_structure'] = ctx.writer.write_json(f'{prefix}/extracted/project_structure.json', state.get('project_structure', {}))
        payload = dict(state.get('studio_payload') or {})
        meta = dict(payload.get('metadata') or {})
        # Immediate upload wrote status=processing. This is the first moment the
        # catalogue should look finished to Source Library.
        meta['status'] = 'processed'
        payload['metadata'] = meta
        state['studio_payload'] = payload
        urls['studio_payload'] = ctx.writer.write_json(f'{prefix}/studio_payload/payload.json', payload)

        # Refresh the compact source index/content with the FINAL, fully-classified
        # metadata. Upload-time (upload_file/_create_job_record_and_upload) writes an
        # immediate placeholder record before this pipeline runs, so it carries empty
        # block/content_type/day_number/document_type even after classification finishes.
        # Source Library's list/filter/selector endpoints read only this compact index,
        # not the studio_payload, so without this refresh every ingested file shows
        # stale pre-classification metadata there forever.
        client_id = state.get('client_id')
        if client_id:
            write_source_content_and_index(ctx.cfg, client_id, payload, payload_key=urls['studio_payload'])
        return ctx.step_done(state, 'processed_storage')

        # =============================================================================
        # STEP: rds_upsert
        # Purpose: Rds Upsert pipeline step.
        # =============================================================================

