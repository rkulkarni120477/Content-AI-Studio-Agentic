"""Agent for DIS pipeline step: source_discovery."""
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


class SourceDiscoveryAgent(BasePipelineAgent):
    """Source Discovery agent.

    This is a LangGraph node. Update this file when this step behavior changes.
    """
    step_name = "source_discovery"
    purpose = "Source Discovery"

    def run(self, state: PipelineState) -> PipelineState:
        ctx = self.ctx
        state['source_type'] = state.get('source_type', 'manual_or_folder_upload')
        return ctx.step_done(state, 'source_discovery')

        # =============================================================================
        # STEP: upload_intake
        # Purpose: Upload Intake pipeline step.
        # =============================================================================

