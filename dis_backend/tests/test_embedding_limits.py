"""An embedding that fails must fail the indexing, not index an empty vector.

The vector index is what block-wide generation searches. A unit indexed with
`"embedding": []` is present in the index and unreachable by the kNN query — and
until now that is exactly what happened whenever embedding failed:
`generate_embeddings` returned [] and `opensearch_upsert` fell back to the raw
content units, indexing them unembedded and reporting a completed job.

It reached prod: 60 AIM units carry no embedding vector. The trigger was a
configuration one — every client set `max_input_chars: 50000` against
amazon.titan-embed-text-v2:0, whose hard limit is 8,192 TOKENS (~32k chars), so a
long unit was rejected with "Too many input tokens: 9450".

These pin both halves: the cap now sits under the model's limit, and a failed
embedding is refused rather than silently degraded.
"""
from __future__ import annotations

from pathlib import Path

import yaml

CLIENTS = Path(__file__).resolve().parents[1] / "config" / "clients"

#: amazon.titan-embed-text-v2:0 accepts 8,192 tokens. English averages ~4 chars
#: per token, but dense technical text runs nearer 3 — the ceiling has to hold at
#: the worst realistic ratio, not the average.
TITAN_V2_MAX_TOKENS = 8192
WORST_CHARS_PER_TOKEN = 3


def _embedding_configs():
    for path in sorted(CLIENTS.glob("*.yaml")):
        cfg = (yaml.safe_load(path.read_text()) or {}).get("embedding") or {}
        if cfg.get("model_id"):
            yield path.name, cfg


def test_every_client_stays_under_its_embedding_models_token_limit():
    checked = 0
    for name, cfg in _embedding_configs():
        if "titan-embed-text-v2" not in cfg["model_id"]:
            continue
        checked += 1
        ceiling = TITAN_V2_MAX_TOKENS * WORST_CHARS_PER_TOKEN
        assert cfg["max_input_chars"] <= ceiling, (
            f"{name}: max_input_chars={cfg['max_input_chars']} can exceed Titan v2's "
            f"{TITAN_V2_MAX_TOKENS}-token limit. Long units will be rejected by the "
            f"model at ingestion time."
        )
    assert checked, "no Titan-v2 client configs found — this test is asserting nothing"


def test_unembedded_units_are_refused_rather_than_indexed(monkeypatch):
    from services import indexing

    class _Emb:
        enabled = True

    class _VS:
        enabled = True
        provider = "opensearch"
        index_name = "test-index"
        auth_mode = "basic"

    class _Cfg:
        embedding = _Emb()
        vector_store = _VS()

    # content units exist, embeddings do not: the failure mode being closed.
    state = {"content_units": [{"content_unit_id": "u1", "text": "x"}],
             "embedding_ready_chunks": []}
    result = indexing.opensearch_upsert(_Cfg(), state)
    assert result["status"] == "failed"
    assert "unembedded" in result["error"]


def test_embeddings_switched_off_still_indexes_text(monkeypatch):
    """The refusal must be narrow: keyword-only setups are a valid configuration
    and must keep working, or this guard breaks a supported deployment."""
    from services import indexing

    calls = {}

    class _Emb:
        enabled = False
        dimension = 1024

    class _VS:
        enabled = True
        provider = "opensearch"
        index_name = "test-index"
        auth_mode = "basic"

    class _Cfg:
        embedding = _Emb()
        vector_store = _VS()

    monkeypatch.setattr(indexing, "_vector_store_write_client", lambda cfg: object())
    monkeypatch.setattr(indexing, "ensure_index", lambda *a, **k: None)
    def _record_and_skip_bulk(index_name, st, units):
        # Returns NO actions so opensearch_upsert skips helpers.bulk entirely —
        # the point here is which units it decided to send, not the transport.
        calls["units"] = units
        return []

    monkeypatch.setattr(indexing, "_build_bulk_actions", _record_and_skip_bulk)

    state = {"content_units": [{"content_unit_id": "u1", "text": "x"}],
             "embedding_ready_chunks": []}
    result = indexing.opensearch_upsert(_Cfg(), state)
    assert result["status"] == "completed"
    assert len(calls["units"]) == 1


def test_generate_embeddings_passes_configured_bedrock_credentials(monkeypatch):
    """Re-index failed with Unable to locate credentials because generate_embeddings
    built a bare boto3 client and ignored DIS_BEDROCK_* / AWS_* from settings."""
    import json
    import types

    from services import indexing

    seen = {}

    class _Body:
        def read(self):
            return json.dumps({"embedding": [0.1, 0.2]})

    class _Client:
        def invoke_model(self, modelId=None, body=None):
            return {"body": _Body()}

    monkeypatch.setattr(indexing, "get_settings", lambda: types.SimpleNamespace(
        aws_region="us-east-1",
        bedrock_client_kwargs=lambda: {
            "region_name": "us-east-1",
            "aws_access_key_id": "AKIATEST",
            "aws_secret_access_key": "secret",
        },
    ))
    monkeypatch.setattr(indexing, "_bedrock_runtime_client", lambda kwargs: seen.update(kwargs) or _Client())

    cfg = types.SimpleNamespace(
        embedding=types.SimpleNamespace(
            enabled=True, region="us-east-1", model_id="amazon.titan-embed-text-v2:0",
            dimension=1024, max_input_chars=24000,
        ),
        storage=types.SimpleNamespace(s3=types.SimpleNamespace(region="us-east-1")),
    )
    result = indexing.generate_embeddings(cfg, {"content_units": [{"title": "t", "text": "hello"}]})
    assert result["status"] == "completed"
    assert seen["aws_access_key_id"] == "AKIATEST"


def test_missing_credentials_error_names_the_env_file():
    from services.indexing import _embedding_credential_error

    class NoCredentialsError(Exception):
        pass

    msg = _embedding_credential_error(NoCredentialsError("Unable to locate credentials"))
    assert "dis_backend/.env" in msg
    assert "DIS_BEDROCK_ACCESS_KEY_ID" in msg
