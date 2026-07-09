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
        urls['studio_payload'] = ctx.writer.write_json(f'{prefix}/studio_payload/payload.json', state.get('studio_payload', {}))
        return ctx.step_done(state, 'processed_storage')

        # =============================================================================
        # STEP: rds_upsert
        # Purpose: Rds Upsert pipeline step.
        # =============================================================================

