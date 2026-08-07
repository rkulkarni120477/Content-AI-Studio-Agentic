"""Shared objects/helpers used by every pipeline step.

Keep this file small. If one step fails, open services/pipeline/steps.py and search for "STEP: <step_name>".
"""
from __future__ import annotations
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, TypedDict

from config.settings import TenantConfig, get_settings
from services.artifacts import ArtifactWriter, write_step_artifact
from services.token_guard import TokenGuard

log = logging.getLogger(__name__)

class PipelineState(TypedDict, total=False):
    job_id: str
    tenant_id: str
    client_id: str
    user_id: str
    namespace: str
    filename: str
    source_relative_path: str
    source_root: str
    s3_key: str
    raw_storage_url: str
    raw_bytes: bytes
    file_sha256: str
    file_extension: str
    file_type: str
    document_family: str
    expected_extractor: str
    raw_text: str
    page_count: int
    has_images: bool
    slide_texts: List[str]
    tables: List[Any]
    doc_type: str
    classification: str
    doc_metadata: Dict[str, Any]
    structured_sections: List[Dict[str, Any]]
    calendar_structure: Dict[str, Any]
    syllabus_structure: Dict[str, Any]
    quiz_structure: Dict[str, Any]
    project_structure: Dict[str, Any]
    content_units: List[Dict[str, Any]]
    studio_payload: Dict[str, Any]
    artifact_urls: Dict[str, str]
    metadata_hints: Dict[str, Any]
    chunks: List[Dict[str, Any]]
    embedding_ready_chunks: List[Dict[str, Any]]
    skip_embedding: bool
    storage_targets: List[str]
    quality_report: Dict[str, Any]
    license_type: str
    completed_steps: List[str]
    errors: List[str]
    current_step: str

class PipelineContext:
    def __init__(self, tenant_cfg: TenantConfig, token_guard: TokenGuard):
        self.cfg = tenant_cfg
        self.models = tenant_cfg.pipeline.models
        self.guard = token_guard
        self.writer = ArtifactWriter(tenant_cfg)

    def step_done(self, state: PipelineState, name: str) -> PipelineState:
        state.setdefault("completed_steps", []).append(name)
        state["current_step"] = name
        try:
            url = write_step_artifact(self.cfg, state, name)
            state.setdefault("artifact_urls", {})[name] = url
        except Exception as exc:
            log.warning("[%s] step artifact failed for %s: %s", state.get("job_id"), name, exc)
        return state

def call_llm(model: str, prompt: str, max_tokens: int = 300) -> tuple[str, int, int]:
    settings = get_settings()
    if settings.environment == "development" and not settings.anthropic_api_key and not settings.aws_access_key_id:
        if "document structure" in prompt.lower():
            return '{"sections":[{"heading":"Extracted Content","summary":"Main extracted document content.","page":1}]}', 400, 50
        if "extract metadata" in prompt.lower():
            return '{"title":"Untitled Source","language":"en","word_count":100}', 400, 60
        return '{"doc_type":"study_material","classification":"internal"}', 400, 40
    try:
        if settings.use_bedrock:
            import boto3, json as _json
            kwargs = {"region_name": settings.aws_region}
            if settings.aws_endpoint_url:
                kwargs["endpoint_url"] = settings.aws_endpoint_url
            client = boto3.client("bedrock-runtime", **kwargs)
            body = _json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": max_tokens,
                "temperature": 0.0,
                "messages": [{"role": "user", "content": prompt}],
            })
            resp = client.invoke_model(modelId=model, body=body)
            result = _json.loads(resp["body"].read())
            text = result["content"][0]["text"]
            usage = result.get("usage", {})
            return text, usage.get("input_tokens", 500), usage.get("output_tokens", 100)
        import anthropic
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        anthropic_model = {
            "anthropic.claude-3-haiku-20240307-v1:0": "claude-3-haiku-20240307",
            "anthropic.claude-3-sonnet-20240229-v1:0": "claude-3-sonnet-20240229",
            "anthropic.claude-3-5-sonnet-20240620-v1:0": "claude-3-5-sonnet-20240620",
            "anthropic.claude-3-5-sonnet-20241022-v2:0": "claude-3-5-sonnet-20241022",
        }.get(model, model)
        msg = client.messages.create(
            model=anthropic_model,
            max_tokens=max_tokens,
            temperature=0.0,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text, msg.usage.input_tokens, msg.usage.output_tokens
    except Exception as exc:
        log.warning("[LLM] failed: %s", exc)
        return '{"doc_type":"other","classification":"internal"}', 0, 0

def safe_json(text: str) -> Dict[str, Any]:
    clean = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(clean)
    except Exception:
        pass
    # Models sometimes prepend conversational preamble before the JSON object
    # (e.g. "Here is the JSON digest:\n\n{...}") even when told to return only
    # JSON — the object itself is well-formed, just not at position 0. Extract
    # the outermost {...} span and retry rather than discarding a good parse.
    start, end = clean.find("{"), clean.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(clean[start:end + 1])
        except Exception:
            pass
    return {}

def keywords(text: str, limit: int = 20) -> List[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", text.lower())
    stop = {"the","and","for","with","this","that","from","into","about","have","are","was","were","will","you","your","slide","page"}
    seen, out = set(), []
    for w in words:
        if w in stop or w in seen:
            continue
        seen.add(w)
        out.append(w)
        if len(out) >= limit:
            break
    return out
