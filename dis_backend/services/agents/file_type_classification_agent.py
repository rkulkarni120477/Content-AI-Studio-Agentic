"""Agent for DIS pipeline step: file_type_classification."""
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


class FileTypeClassificationAgent(BasePipelineAgent):
    """File Type Classification agent.

    This is a LangGraph node. Update this file when this step behavior changes.
    """
    step_name = "file_type_classification"
    purpose = "File Type Classification"

    def run(self, state: PipelineState) -> PipelineState:
        ctx = self.ctx
        ext = Path(state.get('filename', '')).suffix.lower().lstrip('.') or 'unknown'
        family_map = {'pptx': ('presentation', 'pptx_extractor'), 'pdf': ('document', 'pdf_extractor'), 'docx': ('document', 'docx_extractor'), 'xlsx': ('spreadsheet', 'xlsx_extractor'), 'csv': ('tabular', 'csv_extractor'), 'txt': ('text', 'txt_extractor'), 'json': ('data', 'json_extractor'), 'jpg': ('image', 'image_extractor'), 'jpeg': ('image', 'image_extractor'), 'png': ('image', 'image_extractor')}
        family, extractor_name = family_map.get(ext, ('unknown', 'generic_extractor'))
        state['file_extension'] = ext
        state['file_type'] = ext
        state['document_family'] = family
        state['expected_extractor'] = extractor_name
        return ctx.step_done(state, 'file_type_classification')

        # =============================================================================
        # STEP: raw_storage
        # Purpose: Raw Storage pipeline step.
        # =============================================================================

