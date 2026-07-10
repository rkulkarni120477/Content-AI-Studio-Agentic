"""Agent for DIS pipeline step: raw_storage."""
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


class RawStorageAgent(BasePipelineAgent):
    """Raw Storage agent.

    This is a LangGraph node. Update this file when this step behavior changes.
    """
    step_name = "raw_storage"
    purpose = "Raw Storage"

    def run(self, state: PipelineState) -> PipelineState:
        ctx = self.ctx
        raw = state.get('raw_bytes', b'')
        state['file_sha256'] = hashlib.sha256(raw).hexdigest()
        state['raw_storage_key'] = state.get('s3_key', '')
        return ctx.step_done(state, 'raw_storage')

        # =============================================================================
        # STEP: deduplication
        # Purpose: Deduplication pipeline step.
        # =============================================================================

