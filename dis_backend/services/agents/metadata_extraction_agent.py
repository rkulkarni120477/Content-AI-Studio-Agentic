"""Agent for DIS pipeline step: metadata_extraction."""
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


class MetadataExtractionAgent(BasePipelineAgent):
    """Metadata Extraction agent.

    This is a LangGraph node. Update this file when this step behavior changes.
    """
    step_name = "metadata_extraction"
    purpose = "Metadata Extraction"

    def run(self, state: PipelineState) -> PipelineState:
        ctx = self.ctx
        meta = {}
        use_llm = ctx.cfg.pipeline.llm_provider != 'mock' and (ctx.cfg.pipeline.bedrock_enabled or ctx.cfg.pipeline.anthropic_enabled)
        if use_llm:
            try:
                ctx.guard.check_or_raise(1500, 'metadata_extraction')
                schema = ctx.cfg.get_metadata_schema(state.get('client_id', ''))
                fields = []
                for f in schema.required_fields + schema.optional_fields:
                    if f.values:
                        fields.append(f'"{f.name}": one of {f.values[:10]}')
                    elif f.hint:
                        fields.append(f'"{f.name}": {f.hint}')
                    else:
                        fields.append(f'"{f.name}": {f.type}')
                field_text = '\n'.join(fields) or 'title, language, course_name, topic'
                sample = (state.get('raw_text', '') or '')[:1500]
                prompt = f'Extract client metadata. Return JSON only.\nClient fields:\n{field_text}\nAlways include title, language, word_count.\nDocument excerpt:\n{sample}'
                resp, inp, out = call_llm(ctx.models.metadata_extraction, prompt, max_tokens=350)
                ctx.guard.record_usage(inp + out, 'metadata_extraction')
                meta = safe_json(resp)
            except TokenLimitError as exc:
                state.setdefault('errors', []).append(str(exc))
                meta = {}
        meta.setdefault('title', Path(state.get('filename', 'source')).stem.replace('_', ' '))
        meta.setdefault('language', 'en')
        meta.setdefault('word_count', len((state.get('raw_text') or '').split()))
        meta['doc_type'] = state.get('doc_type', 'other')
        meta['classification_label'] = state.get('classification', 'internal')
        state['doc_metadata'] = meta
        return ctx.step_done(state, 'metadata_extraction')

        # =============================================================================
        # STEP: metadata_tagging
        # Purpose: Metadata Tagging pipeline step.
        # =============================================================================

