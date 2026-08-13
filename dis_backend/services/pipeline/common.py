"""Shared objects/helpers used by every pipeline step.

Keep this file small. If one step fails, open services/pipeline/steps.py and search for "STEP: <step_name>".
"""
from __future__ import annotations
import json
import logging
import os
import re
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

class LLMCallFailed(RuntimeError):
    """The provider call did not succeed. No output was produced.

    ``call_llm`` used to swallow every exception and return valid JSON, on the theory
    that a degraded answer beats a crash. In practice that inverted the failure: a
    timeout, a throttle, a revoked credential and a missing model all became
    well-formed output that every downstream step accepted. 2026-08-13's incident is
    the full cost of it — a 20-day block built entirely from fallback stubs, every
    field defaulted, reported to the user as a successful generation, with the real
    cause visible only in a log line nobody reading the result could reach.

    Raising instead does not make callers fragile: each of the six call sites already
    sits inside per-item isolation (a per-day digest, a per-document pipeline step), so
    a failure still degrades exactly one unit. The difference is that degrading is now
    a decision each caller makes and records, rather than something that happens to it
    silently.

    Carries ``model`` and the originating exception's type name so the caller can
    report what failed without re-reading logs.
    """

    def __init__(self, model: str, cause: BaseException) -> None:
        self.model = model or ""
        self.cause_type = type(cause).__name__
        detail = str(cause).strip()
        super().__init__(
            f"LLM call to {self.model!r} failed: {self.cause_type}"
            + (f": {detail}" if detail else "")
        )


#: What ``call_llm`` USED to return when the provider call raised, and what older
#: persisted digests therefore recorded. Retained so a stored failure remains
#: identifiable, and because a model can in principle reply with this shape. Live
#: provider failures now raise :class:`LLMCallFailed` instead of returning it.
#: Named, and exported, so a
#: caller can tell "the call never succeeded" from "the model answered in the wrong
#: shape" — the JSON is valid either way, so downstream schema checks report both as a
#: malformed reply. Those need opposite fixes (credentials/timeouts/quota vs prompt or
#: token budget), and conflating them cost most of 2026-08-13.
LLM_FAILURE_STUB = '{"doc_type":"other","classification":"internal"}'


def is_llm_failure_stub(text: str) -> bool:
    """True when *text* is ``call_llm``'s failure fallback rather than model output.

    Compares parsed content, not the raw string: the stub reaches callers through
    ``safe_json`` and JSON formatting is not stable enough to match on bytes.
    """
    try:
        data = json.loads((text or "").strip())
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    return data.get("doc_type") == "other" and data.get("classification") == "internal"


def _env_positive_int(name: str, default: int) -> int:
    """A positive int from the environment, else *default*. Junk never becomes 0 —
    a zero timeout or zero attempts would be a far more destructive misreading of a
    typo than simply ignoring it."""
    try:
        value = int((os.getenv(name) or "").strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def bedrock_invoke_config() -> Any:
    """botocore Config for Bedrock ``InvokeModel``: explicit timeouts and retries.

    botocore's defaults are wrong for this call. Its read timeout is 60 seconds, and
    a content-rich MAP day routinely generates for longer — the socket then raises
    ReadTimeoutError, which ``call_llm``'s blanket ``except`` turns into a valid-JSON
    stub, i.e. a whole day of filler indistinguishable from real extraction.

    Measured on 2026-08-13: prod's 20-day Block 2 build lost exactly the five heaviest
    days (3, 6, 7, 8, 9) this way while the other fifteen succeeded, and each recorded
    ``{"doc_type":"other"}`` — the signature of that swallowed exception. CAS's own
    Bedrock client has used ``read_timeout=600`` since it hit the same wall
    (promptops_app/core/llm_client.py); DIS duplicates this helper by design and so
    never inherited the fix.

    Retries are ``adaptive`` rather than the legacy default: a block build issues one
    call per day, so throttling is a question of when rather than if, and adaptive
    mode backs off across calls using observed throttle rates instead of retrying each
    call in isolation. Note that botocore does NOT retry a read timeout that the
    server is still working on, which is why the timeout itself has to be right.
    """
    from botocore.config import Config as BotoConfig

    return BotoConfig(
        read_timeout=_env_positive_int("DIS_BEDROCK_READ_TIMEOUT", 600),
        connect_timeout=_env_positive_int("DIS_BEDROCK_CONNECT_TIMEOUT", 30),
        retries={
            "max_attempts": _env_positive_int("DIS_BEDROCK_MAX_ATTEMPTS", 4),
            "mode": "adaptive",
        },
    )


def llm_is_mocked(settings: Any = None) -> bool:
    """True when :func:`call_llm` will serve canned replies instead of calling a model.

    Split out of ``call_llm`` so the condition can be asked about rather than only
    experienced. A mock that is indistinguishable from a working model is the worst
    kind of failure: on 2026-08-13 prod's DIS built an entire 20-day block out of
    canned replies, every extracted field defaulted, and the pipeline reported it as
    a successful generation.

    ``bedrock_client_kwargs()`` is the single source of truth for the credentials the
    Bedrock client will ACTUALLY use, and it prefers the Bedrock-only DIS_BEDROCK_*
    pair over the shared AWS_* one. The previous check read ``aws_access_key_id``
    directly, so a deployment setting only DIS_BEDROCK_* kept serving mock replies
    while holding perfectly good credentials it never used — the two conditions must
    be derived from the same place or they drift exactly when it matters.
    """
    settings = settings if settings is not None else get_settings()
    if getattr(settings, "environment", "") != "development":
        return False
    if getattr(settings, "anthropic_api_key", None):
        return False
    try:
        return not (settings.bedrock_client_kwargs() or {}).get("aws_access_key_id")
    except Exception:
        # Never let a settings-shape surprise decide this. Falling back to the
        # narrower check keeps the old behaviour rather than silently flipping a
        # credentialled deployment onto the mock.
        return not getattr(settings, "aws_access_key_id", None)


def call_llm(model: str, prompt: str, max_tokens: int = 300) -> tuple[str, int, int]:
    settings = get_settings()
    if llm_is_mocked(settings):
        # Zero tokens, deliberately. This is what the real failure path at the bottom
        # of this function reports, and it is the signal digests.build's
        # preflight_extractor keys on to refuse a build outright. Reporting 400 made
        # the mock look like a working model to the one guard written to catch
        # precisely this, so preflight passed and 20 days of canned replies were
        # built and charged for. Zero is also simply honest: nothing was spent.
        if "document structure" in prompt.lower():
            return '{"sections":[{"heading":"Extracted Content","summary":"Main extracted document content.","page":1}]}', 0, 0
        if "extract metadata" in prompt.lower():
            return '{"title":"Untitled Source","language":"en","word_count":100}', 0, 0
        return '{"doc_type":"study_material","classification":"internal"}', 0, 0
    try:
        if settings.use_bedrock:
            import boto3, json as _json
            # Credentials come from settings.bedrock_client_kwargs(), which prefers the
            # Bedrock-only DIS_BEDROCK_* pair when set and otherwise uses the shared
            # AWS_* one. That split exists because model access is granted per IAM
            # principal, and the principal that can invoke the newer models is not the
            # one that owns DIS's S3 bucket and OpenSearch domain — so the model calls
            # and the storage calls need to be able to use different identities.
            #
            # Passing credentials explicitly (rather than relying on boto3's ambient
            # chain) is load-bearing: this deployment has no instance role, and the
            # resulting NoCredentialsError was swallowed by the except below into a
            # valid-JSON stub, which produced a complete-looking Blueprint with every
            # extracted field at its default.
            client = boto3.client("bedrock-runtime", config=bedrock_invoke_config(),
                                  **settings.bedrock_client_kwargs())
            payload = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": max_tokens,
                # Deterministic extraction is the point of temperature 0 here, so it is
                # sent when the model accepts it — but newer models REJECT it outright
                # (`ValidationException: temperature is deprecated for this model`),
                # which without the retry below is swallowed into the valid-JSON stub
                # and becomes a block of empty digests. Mirrors the same retry CAS
                # already has in promptops_app/core/llm_client.py.
                "temperature": 0.0,
                "messages": [{"role": "user", "content": prompt}],
            }
            try:
                resp = client.invoke_model(modelId=model, body=_json.dumps(payload))
            except Exception as exc:
                msg = str(exc)
                if "ValidationException" not in msg or "temperature" not in msg:
                    raise
                log.info("[LLM] %s rejects an explicit temperature — retrying without it", model)
                payload.pop("temperature", None)
                resp = client.invoke_model(modelId=model, body=_json.dumps(payload))
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
        # type(exc).__name__ as well as the text: a ReadTimeoutError's str() is often
        # nearly empty, and "which AWS exception was it" is the whole question when a
        # unit of work degrades.
        log.warning("[LLM] failed: %s: %s", type(exc).__name__, exc)
        # Raise rather than returning valid-looking JSON — see LLMCallFailed. Every
        # caller sits inside per-item isolation, so this still degrades one day or one
        # document; it just can no longer be mistaken for output.
        raise LLMCallFailed(model, exc) from exc

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
