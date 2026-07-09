"""Agent for DIS pipeline step: studio_payload_preparation."""
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


class StudioPayloadPreparationAgent(BasePipelineAgent):
    """Studio Payload Preparation agent.

    This is a LangGraph node. Update this file when this step behavior changes.
    """
    step_name = "studio_payload_preparation"
    purpose = "Studio Payload Preparation"

    def run(self, state: PipelineState) -> PipelineState:
        ctx = self.ctx
        payload = {'payload_version': 'dis-studio-context-v1', 'tenant_id': state.get('tenant_id'), 'client_id': state.get('client_id'), 'job_id': state.get('job_id'), 'namespace': state.get('namespace'), 'source_file': {'name': state.get('filename'), 'relative_path': state.get('source_relative_path'), 'source_root': state.get('source_root'), 'type': state.get('file_type'), 'raw_key': state.get('s3_key'), 'raw_url': state.get('raw_storage_url'), 'sha256': state.get('file_sha256'), 'size_bytes': len(state.get('raw_bytes') or b'')}, 'metadata': state.get('doc_metadata', {}), 'structure': state.get('structured_sections', []), 'calendar_structure': state.get('calendar_structure', {}), 'syllabus_structure': state.get('syllabus_structure', {}), 'quiz_structure': state.get('quiz_structure', {}), 'project_structure': state.get('project_structure', {}), 'content_units': state.get('content_units', []), 'quality_report': state.get('quality_report', {}), 'page_count': state.get('page_count', 0), 'file_size_bytes': len(state.get('raw_bytes') or b'')}
        state['studio_payload'] = payload
        return ctx.step_done(state, 'studio_payload_preparation')

        # =============================================================================
        # STEP: processed_storage
        # Purpose: Processed Storage pipeline step.
        # =============================================================================

