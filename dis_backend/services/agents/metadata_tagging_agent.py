"""Agent for DIS pipeline step: metadata_tagging."""
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
from services.client_profiles import enrich_metadata


#: Fields describing which block/day a document belongs to. Grouped because a
#: document that legitimately spans blocks must keep ALL of them blank, not just
#: the label.
_BLOCK_SCOPE_FIELDS = ('block', 'block_id', 'block_number', 'day_id', 'day_number')


def apply_metadata_hints(meta: Dict[str, Any], hints: Dict[str, Any] | None) -> Dict[str, Any]:
    """Apply CAS's upload hints over DIS's own inference. Returns a new dict.

    Hints win, because they are facts about the upload rather than guesses about
    the file: CAS knows which course the user uploaded into, and therefore which
    block. That is what stops a subject-named folder (``Landing Gear Projects/``)
    from ingesting block-less and disappearing from every block-scoped retrieval —
    measured at 148 documents across six AIM courses before it was fixed.

    Two exceptions, both load-bearing:

    * An empty hint never clobbers a good inference, so a caller that has nothing
      to say can always send the field blank.
    * A document the client profile deliberately declared block-less keeps that
      blank. An AKTR knowledge-test rollup carries one sheet per block, so no
      doc-level block is true of it; enrich_metadata blanks the scope and sets
      ``spans_multiple_blocks`` (attribution is per content unit instead). Letting
      a hint overwrite that would re-file all sixteen blocks' data under whichever
      course the sheet happened to be uploaded under, which is precisely the defect
      the blanking exists to prevent.

    Kept a module-level function, not inline in ``run``: this is the whole
    precedence rule, and it needs to be testable without standing up the pipeline.
    """
    out = dict(meta)
    spans_blocks = bool(out.get('spans_multiple_blocks'))
    for key, value in (hints or {}).items():
        if value in (None, '', [], {}):
            continue
        if spans_blocks and key in _BLOCK_SCOPE_FIELDS:
            continue
        out[key] = value
    return out


class MetadataTaggingAgent(BasePipelineAgent):
    """Metadata Tagging agent.

    This is a LangGraph node. Update this file when this step behavior changes.
    """
    step_name = "metadata_tagging"
    purpose = "Metadata Tagging"

    def run(self, state: PipelineState) -> PipelineState:
        ctx = self.ctx
        meta = dict(state.get('doc_metadata', {}))
        doc_type = state.get('doc_type', 'other')
        client = ctx.cfg.get_client(state.get('client_id', ''))
        restricted_types = set(getattr(ctx.cfg.document_processing, 'restricted_document_types', []) or [])
        is_restricted = bool(getattr(client, 'restricted_content', False)) or doc_type in restricted_types
        access_level = 'admin_only' if is_restricted else 'client'
        meta.update({
            'tenant_id': state['tenant_id'],
            'client_id': state.get('client_id'),
            'namespace': state.get('namespace'),
            'job_id': state.get('job_id'),
            'source_file_name': state.get('filename'),
            'source_relative_path': state.get('source_relative_path'),
            'source_root': state.get('source_root'),
            'source_file_type': state.get('file_type'),
            'file_sha256': state.get('file_sha256'),
            'raw_storage_url': state.get('raw_storage_url'),
            'document_type': doc_type,
            'content_type': state.get('document_family'),
            'has_visual': state.get('has_images', False),
            'page_count': state.get('page_count', 0),
            'restricted': is_restricted,
            'access_level': access_level,
        })

        # Client profile enrichment. For AIM this adds safe ingestion metadata:
        # block/day, final/working version, student vs instructor visibility,
        # blueprint eligibility, course-generation eligibility, and
        # calendar-first mapping flags. Most behavior is controlled from YAML.
        meta = enrich_metadata(
            client_id=state.get('client_id') or '',
            filename=state.get('filename') or '',
            source_relative_path=state.get('source_relative_path') or state.get('filename') or '',
            raw_text=state.get('raw_text') or '',
            doc_type=doc_type,
            current_metadata=meta,
            tenant_cfg=ctx.cfg,
        )

        # CAS Source Library can pass user-corrected hints during upload.
        # These fields override AI guesses and drive dynamic filters in CAS.
        # The precedence rule (including why a block-less-on-purpose document keeps
        # its blank) lives in apply_metadata_hints, so it can be tested directly.
        meta = apply_metadata_hints(meta, state.get('metadata_hints'))
        purpose = str(meta.get('purpose') or '').strip()
        if purpose:
            safe_purpose = purpose.replace('-', '_')
            meta[f'use_for_{safe_purpose}'] = True
        if purpose == 'style' and not meta.get('document_type'):
            meta['document_type'] = 'style_guide'

        meta['tags'] = [
            f"tenant:{state.get('tenant_id')}",
            f"client:{state.get('client_id')}",
            f"type:{meta.get('document_type') or doc_type}",
            f"content_type:{meta.get('content_type') or state.get('document_family', 'unknown')}",
            f"visibility:{meta.get('visibility', 'unknown')}",
            f"access:{meta.get('access_level', access_level)}",
        ]
        state['doc_metadata'] = meta
        return ctx.step_done(state, 'metadata_tagging')

        # =============================================================================
        # STEP: structure_extraction
        # Purpose: Structure Extraction pipeline step.
        # =============================================================================

