"""Vector store adapter layer.

Currently OpenSearch is implemented through services.indexing. To support another
vector DB, add a provider class here and call it from the OpenSearch/Vector agent.
"""
from __future__ import annotations
from typing import Any, Dict

class VectorStoreAdapter:
    provider = "base"
    def upsert(self, cfg: Any, state: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

class OpenSearchVectorStoreAdapter(VectorStoreAdapter):
    provider = "opensearch"
    def upsert(self, cfg: Any, state: Dict[str, Any]) -> Dict[str, Any]:
        from services.indexing import opensearch_upsert
        return opensearch_upsert(cfg, state)

VECTOR_STORE_ADAPTERS = {
    "opensearch": OpenSearchVectorStoreAdapter(),
    # Future examples:
    # "pinecone": PineconeVectorStoreAdapter(),
    # "qdrant": QdrantVectorStoreAdapter(),
    # "weaviate": WeaviateVectorStoreAdapter(),
}

def get_vector_store_adapter(provider: str) -> VectorStoreAdapter:
    try:
        return VECTOR_STORE_ADAPTERS[provider]
    except KeyError as exc:
        raise KeyError(f"Unsupported vector_store provider: {provider}") from exc
