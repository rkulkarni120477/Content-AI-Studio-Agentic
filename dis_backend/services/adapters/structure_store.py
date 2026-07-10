"""Structure store adapter layer.

RDS/PostgreSQL is implemented now. Add new structure stores here later, e.g.
DynamoDB, MongoDB, Snowflake, or a client-owned API.
"""
from __future__ import annotations
from typing import Any, Dict

class StructureStoreAdapter:
    provider = "base"
    def upsert(self, cfg: Any, state: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

class PostgresStructureStoreAdapter(StructureStoreAdapter):
    provider = "postgres"
    def upsert(self, cfg: Any, state: Dict[str, Any]) -> Dict[str, Any]:
        from services.indexing import rds_upsert
        return rds_upsert(cfg, state)

STRUCTURE_STORE_ADAPTERS = {
    "postgres": PostgresStructureStoreAdapter(),
    # Future examples:
    # "dynamodb": DynamoDBStructureStoreAdapter(),
    # "mongodb": MongoDBStructureStoreAdapter(),
    # "snowflake": SnowflakeStructureStoreAdapter(),
}

def get_structure_store_adapter(provider: str) -> StructureStoreAdapter:
    try:
        return STRUCTURE_STORE_ADAPTERS[provider]
    except KeyError as exc:
        raise KeyError(f"Unsupported structure_store provider: {provider}") from exc
