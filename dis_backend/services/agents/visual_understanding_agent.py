"""Agent for DIS pipeline step: visual_understanding."""
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


class VisualUnderstandingAgent(BasePipelineAgent):
    """Visual Understanding agent.

    This is a LangGraph node. Update this file when this step behavior changes.
    """
    step_name = "visual_understanding"
    purpose = "Visual Understanding"

    def run(self, state: PipelineState) -> PipelineState:
        ctx = self.ctx
        visual_units = []
        if state.get('has_images'):
            for i, s in enumerate(state.get('slide_texts', []) or [], 1):
                if '[Image' in s:
                    if ctx.cfg.pipeline.vision_enabled and ctx.cfg.pipeline.llm_provider != 'mock':
                        # Placeholder for future Bedrock vision call. We keep it explicit because
                        # image/slide vision can be expensive and slower than text extraction.
                        summary = 'Image or diagram detected. Vision is enabled; detailed vision call can be added here.'
                    else:
                        summary = 'Image or diagram detected. Vision disabled, so no vision-model call was made.'
                    visual_units.append({'unit_number': i, 'visual_summary': summary})
        state['visual_units'] = visual_units
        return ctx.step_done(state, 'visual_understanding')

        # =============================================================================
        # STEP: content_classification
        # Purpose: Content Classification pipeline step.
        # =============================================================================

