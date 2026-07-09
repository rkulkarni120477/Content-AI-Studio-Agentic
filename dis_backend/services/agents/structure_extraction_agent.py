"""Agent for DIS pipeline step: structure_extraction."""
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


class StructureExtractionAgent(BasePipelineAgent):
    """Structure Extraction agent.

    This is a LangGraph node. Update this file when this step behavior changes.
    """
    step_name = "structure_extraction"
    purpose = "Structure Extraction"

    def run(self, state: PipelineState) -> PipelineState:
        ctx = self.ctx
        slide_texts = state.get('slide_texts', []) or []
        if slide_texts:
            sections = []
            for i, text in enumerate(slide_texts, 1):
                lines = [l.strip('# ') for l in text.splitlines() if l.strip()]
                title = next((l for l in lines if not l.startswith('[Slide')), f'Slide {i}')
                sections.append({'heading': title[:120], 'summary': ' '.join(lines[:4])[:400], 'page': i, 'unit_type': 'slide'})
            state['structured_sections'] = sections
        else:
            use_llm = ctx.cfg.pipeline.llm_provider != 'mock' and (ctx.cfg.pipeline.bedrock_enabled or ctx.cfg.pipeline.anthropic_enabled)
            if use_llm:
                try:
                    ctx.guard.check_or_raise(1500, 'structure_extraction')
                    sample = (state.get('raw_text', '') or '')[:1500]
                    resp, inp, out = call_llm(ctx.models.structure_extraction, f'Extract document structure as JSON with sections array. Document:\n{sample}', max_tokens=400)
                    ctx.guard.record_usage(inp + out, 'structure_extraction')
                    state['structured_sections'] = safe_json(resp).get('sections', [])
                except TokenLimitError as exc:
                    state.setdefault('errors', []).append(str(exc))
                    state['structured_sections'] = []
            else:
                text = (state.get('raw_text', '') or '')
                paras = [p.strip() for p in text.split('\n') if len(p.strip()) > 30][:8]
                state['structured_sections'] = [{'heading': f'Section {i+1}', 'summary': p[:400], 'page': i+1, 'unit_type': 'chunk'} for i, p in enumerate(paras)]
        return ctx.step_done(state, 'structure_extraction')

        # =============================================================================
        # STEP: specialized_structure_extraction
        # Purpose: Specialized Structure Extraction pipeline step.
        # =============================================================================

