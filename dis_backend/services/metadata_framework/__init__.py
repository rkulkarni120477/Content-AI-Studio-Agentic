"""Config-driven metadata Field Registry for DIS (Phase 1)."""

from services.metadata_framework.registry import (
    DEFAULT_REGISTRY,
    FieldRegistry,
    FieldSpec,
    registry_for_tenant,
    registry_from_config,
)

__all__ = [
    "DEFAULT_REGISTRY",
    "FieldRegistry",
    "FieldSpec",
    "registry_for_tenant",
    "registry_from_config",
]
