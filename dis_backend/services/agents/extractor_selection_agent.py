"""Agent for DIS pipeline step: extractor_selection."""
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


class ExtractorSelectionAgent(BasePipelineAgent):
    """Extractor Selection agent.

    This is a LangGraph node. Update this file when this step behavior changes.
    """
    step_name = "extractor_selection"
    purpose = "Extractor Selection"

    def run(self, state: PipelineState) -> PipelineState:
        ctx = self.ctx
        state['extractor'] = state.get('expected_extractor', 'generic_extractor')
        return ctx.step_done(state, 'extractor_selection')

        # =============================================================================
        # STEP: content_extraction
        # Purpose: Content Extraction pipeline step.
        # =============================================================================

