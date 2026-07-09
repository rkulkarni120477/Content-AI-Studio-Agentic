"""Agent for DIS pipeline step: quality_check."""
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


class QualityCheckAgent(BasePipelineAgent):
    """Quality Check agent.

    This is a LangGraph node. Update this file when this step behavior changes.
    """
    step_name = "quality_check"
    purpose = "Quality Check"

    def run(self, state: PipelineState) -> PipelineState:
        ctx = self.ctx
        text_len = len(state.get('raw_text', '') or '')
        unit_count = len(state.get('content_units', []) or [])
        passed = text_len > 20 and unit_count > 0
        report = {'passed': passed, 'overall_score': 0.9 if passed else 0.3, 'text_length': text_len, 'content_unit_count': unit_count, 'has_images': state.get('has_images', False), 'mode': 'rule_based'}
        use_llm = ctx.cfg.pipeline.llm_provider != 'mock' and (ctx.cfg.pipeline.bedrock_enabled or ctx.cfg.pipeline.anthropic_enabled)
        if use_llm:
            try:
                ctx.guard.check_or_raise(800, 'quality_check')
                sample = (state.get('raw_text', '') or '')[:1200]
                prompt = f'Review extraction quality. Return JSON only with passed boolean, overall_score 0-1, warnings array. Document type={state.get("doc_type")}. Text sample:\n{sample}'
                resp, inp, out = call_llm(ctx.models.quality_check, prompt, max_tokens=180)
                ctx.guard.record_usage(inp + out, 'quality_check')
                llm_report = safe_json(resp)
                if llm_report:
                    report.update(llm_report)
                    report['mode'] = 'llm'
            except TokenLimitError as exc:
                state.setdefault('errors', []).append(str(exc))
        state['quality_report'] = report
        return ctx.step_done(state, 'quality_check')

        # =============================================================================
        # STEP: license_tagging
        # Purpose: License Tagging pipeline step.
        # =============================================================================

