"""Agent for DIS pipeline step: content_extraction."""
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


class ContentExtractionAgent(BasePipelineAgent):
    """Content Extraction agent.

    This is a LangGraph node. Update this file when this step behavior changes.
    """
    step_name = "content_extraction"
    purpose = "Content Extraction"

    def run(self, state: PipelineState) -> PipelineState:
        ctx = self.ctx
        processing = getattr(ctx.cfg, 'processing', None)
        extraction_options = {'max_pptx_slides': getattr(processing, 'max_pptx_slides', 80), 'pptx_extract_images': getattr(processing, 'pptx_extract_images', False), 'max_extracted_chars': getattr(processing, 'max_extracted_chars', 250000)}
        result: ExtractionResult = extract(state.get('filename', 'file.txt'), state.get('raw_bytes', b''), options=extraction_options)
        state['raw_text'] = result.text or ''
        state['page_count'] = result.page_count
        state['has_images'] = result.has_images
        state['tables'] = result.tables or []
        state['slide_texts'] = result.slide_texts or []
        # Structured per-page text for ebook_reference page-chunking. Kept on
        # state so ContentUnitCreationAgent does not have to re-parse [Page N]
        # markers unless the extraction was truncated by max_extracted_chars.
        state['page_texts'] = result.page_texts or []
        return ctx.step_done(state, 'content_extraction')

        # =============================================================================
        # STEP: visual_understanding
        # Purpose: Visual Understanding pipeline step.
        # =============================================================================

