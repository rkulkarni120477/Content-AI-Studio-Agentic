"""Agent for DIS pipeline step: content_classification."""
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


class ContentClassificationAgent(BasePipelineAgent):
    """Content Classification agent.

    This is a LangGraph node. Update this file when this step behavior changes.
    """
    step_name = "content_classification"
    purpose = "Content Classification"

    def run(self, state: PipelineState) -> PipelineState:
        ctx = self.ctx
        doc_processing = ctx.cfg.document_processing
        inferred = infer_doc_type(state.get('filename', ''), state.get('raw_text', ''), doc_processing)
        state['doc_type'] = inferred
        state['classification'] = 'exam_secret' if inferred in {'quiz_exam', 'quiz_answer_key', 'project_key'} else 'internal'
        if ctx.cfg.pipeline.llm_provider != 'mock' and (ctx.cfg.pipeline.bedrock_enabled or ctx.cfg.pipeline.anthropic_enabled):
            try:
                ctx.guard.check_or_raise(1000, 'content_classification')
                sample = (state.get('raw_text', '') or '')[:1200]
                allowed = '|'.join(doc_processing.enabled_document_types or [])
                prompt = f'''Classify this extracted document content. Return JSON only:\n{{"doc_type":"{allowed}", "classification":"public|internal|restricted|exam_secret"}}\nFilename: {state.get('filename')}\nContent:\n{sample}'''
                resp, inp, out = call_llm(ctx.models.classification, prompt, max_tokens=100)
                ctx.guard.record_usage(inp + out, 'content_classification')
                parsed = safe_json(resp)
                candidate = parsed.get('doc_type')
                if candidate in (doc_processing.enabled_document_types or []):
                    state['doc_type'] = candidate
                state['classification'] = parsed.get('classification', state.get('classification', 'internal'))
            except TokenLimitError as exc:
                state.setdefault('errors', []).append(str(exc))
        return ctx.step_done(state, 'content_classification')

        # =============================================================================
        # STEP: metadata_extraction
        # Purpose: Metadata Extraction pipeline step.
        # =============================================================================

