"""DIS metadata Field Registry (Phase 1).

Central definition of which metadata fields are promoted to:
  - index          → compact_source_record metadata projection
  - retrieval      → _metadata_from_record reconstruction
  - filter_options → source_filter_options response map
  - cas_list       → Source Library listing taxonomy columns

Empty / missing client ``metadata_framework`` falls back to DEFAULT_FIELDS,
which encode the pre-registry hardcoded lists exactly.

``acs_codes`` is intentionally absent. AIM profile transforms are not owned here.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


PROMOTE_INDEX = "index"
PROMOTE_RETRIEVAL = "retrieval"
PROMOTE_FILTER_OPTIONS = "filter_options"
PROMOTE_CAS_LIST = "cas_list"

VALID_PROMOTE = frozenset({
    PROMOTE_INDEX,
    PROMOTE_RETRIEVAL,
    PROMOTE_FILTER_OPTIONS,
    PROMOTE_CAS_LIST,
})

# How compact_source_record obtains a value for an index-promoted field.
EXTRACT_META_OR_EMPTY = "meta_or_empty"       # meta.get(key) or ""
EXTRACT_META_OR_BLOCK = "meta_or_block"       # meta.get(key) or meta.get("block") or ""
EXTRACT_PROMOTE_EMPTY = "promote_empty"       # unit-fallback promote, default ""
EXTRACT_PROMOTE_NONE = "promote_none"         # unit-fallback promote, default None
EXTRACT_BOOL_META = "bool_meta"               # bool(meta.get(key))
EXTRACT_RAW_META = "raw_meta"                 # meta.get(key) — may be None
EXTRACT_RECORD_GET = "record_get"             # rec.get(key) for retrieval rebuild
EXTRACT_DOC_TYPE_ALIAS = "doc_type_alias"     # retrieval-only: copy document_type


@dataclass(frozen=True)
class FieldSpec:
    """One registry field.

    ``key`` is the canonical storage key on the compact source record / retrieval
    metadata object. Compatibility adapters map UI / filter_options / alias names
    onto this key without changing AIM extraction behaviour.
    """
    key: str
    type: str = "string"
    tier: str = "structural"  # operational | structural | semantic
    promote: Tuple[str, ...] = ()
    filter_options_key: Optional[str] = None
    aliases: Tuple[str, ...] = ()
    extract: str = EXTRACT_META_OR_EMPTY

    def promotes(self, target: str) -> bool:
        return target in self.promote


def _spec(
    key: str,
    *promote: str,
    type: str = "string",
    tier: str = "structural",
    filter_options_key: Optional[str] = None,
    aliases: Tuple[str, ...] = (),
    extract: str = EXTRACT_META_OR_EMPTY,
) -> FieldSpec:
    return FieldSpec(
        key=key,
        type=type,
        tier=tier,
        promote=tuple(promote),
        filter_options_key=filter_options_key,
        aliases=aliases,
        extract=extract,
    )


# Default registry = exact pre-Phase-1 hardcoded behaviour.
# Order matters: compact_source_record and _metadata_from_record key order is pinned
# by characterization tests.
DEFAULT_FIELDS: Tuple[FieldSpec, ...] = (
    # --- retrieval operational (not index-metadata loop; scaffolding owns write) ---
    _spec("title", PROMOTE_RETRIEVAL, tier="operational", extract=EXTRACT_RECORD_GET),
    _spec("document_type", PROMOTE_RETRIEVAL, PROMOTE_FILTER_OPTIONS,
          tier="operational", filter_options_key="document_types", extract=EXTRACT_RECORD_GET),
    _spec("doc_type", PROMOTE_RETRIEVAL, tier="operational", extract=EXTRACT_DOC_TYPE_ALIAS),
    _spec("purpose", PROMOTE_RETRIEVAL, PROMOTE_FILTER_OPTIONS,
          tier="operational", filter_options_key="purposes", extract=EXTRACT_RECORD_GET),
    _spec("visibility", PROMOTE_RETRIEVAL, tier="operational", extract=EXTRACT_RECORD_GET),
    _spec("restricted", PROMOTE_RETRIEVAL, type="boolean", tier="operational", extract=EXTRACT_RECORD_GET),
    _spec("access_level", PROMOTE_RETRIEVAL, tier="operational", extract=EXTRACT_RECORD_GET),
    _spec("status", PROMOTE_RETRIEVAL, PROMOTE_FILTER_OPTIONS,
          tier="operational", filter_options_key="status", extract=EXTRACT_RECORD_GET),
    # filter_options-only operational (not rebuilt from registry on retrieval beyond above)
    _spec("source_file_type", PROMOTE_FILTER_OPTIONS,
          tier="operational", filter_options_key="source_file_types", extract=EXTRACT_RECORD_GET),
    # --- structural taxonomy + calendar-join (index + retrieval) ---
    _spec("course_name", PROMOTE_INDEX, PROMOTE_RETRIEVAL, PROMOTE_FILTER_OPTIONS, PROMOTE_CAS_LIST,
          filter_options_key="course_name"),
    _spec("block", PROMOTE_INDEX, PROMOTE_RETRIEVAL, PROMOTE_FILTER_OPTIONS, PROMOTE_CAS_LIST,
          filter_options_key="blocks", aliases=("block_name", "block_id", "block_number")),
    _spec("day", PROMOTE_INDEX, PROMOTE_RETRIEVAL, PROMOTE_FILTER_OPTIONS, PROMOTE_CAS_LIST,
          filter_options_key="days", aliases=("day_id", "day_number", "mapped_day")),
    _spec("chapter", PROMOTE_INDEX, PROMOTE_RETRIEVAL, PROMOTE_FILTER_OPTIONS, PROMOTE_CAS_LIST,
          filter_options_key="chapter", aliases=("chapter_number", "chapter_title")),
    _spec("module_name", PROMOTE_INDEX, PROMOTE_RETRIEVAL, PROMOTE_FILTER_OPTIONS, PROMOTE_CAS_LIST,
          filter_options_key="module", aliases=("module", "module_id", "section_title")),
    _spec("learning_objective", PROMOTE_INDEX, PROMOTE_RETRIEVAL, PROMOTE_FILTER_OPTIONS, PROMOTE_CAS_LIST,
          filter_options_key="learning_objective",
          aliases=("learning_objective_id", "learning_objective_text")),
    _spec("content_type", PROMOTE_INDEX, PROMOTE_RETRIEVAL),
    _spec("block_id", PROMOTE_INDEX, PROMOTE_RETRIEVAL, extract=EXTRACT_META_OR_BLOCK),
    _spec("block_number", PROMOTE_INDEX, PROMOTE_RETRIEVAL),
    _spec("day_number", PROMOTE_INDEX, PROMOTE_RETRIEVAL, type="integer", extract=EXTRACT_PROMOTE_NONE),
    _spec("day_id", PROMOTE_INDEX, PROMOTE_RETRIEVAL, extract=EXTRACT_PROMOTE_EMPTY),
    _spec("mapped_day", PROMOTE_INDEX, PROMOTE_RETRIEVAL),
    _spec("filename_day_id", PROMOTE_INDEX, PROMOTE_RETRIEVAL),
    _spec("quiz_number", PROMOTE_INDEX, PROMOTE_RETRIEVAL, type="integer", extract=EXTRACT_PROMOTE_NONE),
    _spec("project_number", PROMOTE_INDEX, PROMOTE_RETRIEVAL, extract=EXTRACT_PROMOTE_EMPTY),
    _spec("lesson_name", PROMOTE_INDEX, PROMOTE_RETRIEVAL),
    _spec("subject_unit", PROMOTE_INDEX, PROMOTE_RETRIEVAL, extract=EXTRACT_PROMOTE_EMPTY),
    _spec("course_id", PROMOTE_INDEX, PROMOTE_RETRIEVAL, tier="operational"),
    _spec("program_id", PROMOTE_INDEX, PROMOTE_RETRIEVAL, tier="operational"),
    _spec("calendar_mapping_required", PROMOTE_INDEX, PROMOTE_RETRIEVAL,
          type="boolean", tier="operational", extract=EXTRACT_BOOL_META),
    _spec("is_generation_candidate", PROMOTE_INDEX, PROMOTE_RETRIEVAL,
          type="boolean", tier="operational", extract=EXTRACT_RAW_META),
    _spec("is_archive_or_working_version", PROMOTE_INDEX, PROMOTE_RETRIEVAL,
          type="boolean", tier="operational", extract=EXTRACT_BOOL_META),
)


@dataclass(frozen=True)
class FieldRegistry:
    fields: Tuple[FieldSpec, ...] = field(default_factory=lambda: DEFAULT_FIELDS)

    def all(self) -> Sequence[FieldSpec]:
        return self.fields

    def for_promote(self, target: str) -> List[FieldSpec]:
        if target not in VALID_PROMOTE:
            raise ValueError(f"Unknown promote target: {target}")
        return [f for f in self.fields if f.promotes(target)]

    def keys_for(self, target: str) -> List[str]:
        return [f.key for f in self.for_promote(target)]

    def get(self, key: str) -> Optional[FieldSpec]:
        for f in self.fields:
            if f.key == key:
                return f
        return None

    def filter_options_map(self) -> Dict[str, str]:
        """Response key → compact-record field key (matches source_filter_options)."""
        out: Dict[str, str] = {}
        for f in self.for_promote(PROMOTE_FILTER_OPTIONS):
            resp = f.filter_options_key or f.key
            out[resp] = f.key
        return out


DEFAULT_REGISTRY = FieldRegistry()


def _normalize_promote(raw: Any) -> Tuple[str, ...]:
    if not raw:
        return ()
    if isinstance(raw, str):
        items = [raw]
    else:
        items = list(raw)
    out: List[str] = []
    for item in items:
        p = str(item).strip()
        if p in VALID_PROMOTE and p not in out:
            out.append(p)
    return tuple(out)


def _field_from_yaml(key: str, body: Mapping[str, Any], base: Optional[FieldSpec] = None) -> FieldSpec:
    """Build / overlay a FieldSpec from client YAML ``metadata_framework.fields``."""
    if base is None:
        base = FieldSpec(key=key)
    promote = _normalize_promote(body.get("promote")) if "promote" in body else base.promote
    aliases_raw = body.get("aliases", base.aliases)
    if isinstance(aliases_raw, str):
        aliases = (aliases_raw,)
    else:
        aliases = tuple(str(a) for a in (aliases_raw or ()))
    return replace(
        base,
        type=str(body.get("type", base.type) or "string"),
        tier=str(body.get("tier", base.tier) or "structural"),
        promote=promote or base.promote,
        filter_options_key=(
            str(body["filter_options_key"])
            if "filter_options_key" in body and body["filter_options_key"] is not None
            else base.filter_options_key
        ),
        aliases=aliases if "aliases" in body else base.aliases,
        extract=str(body.get("extract", base.extract) or base.extract),
    )


def registry_from_config(metadata_framework: Any = None) -> FieldRegistry:
    """Resolve registry for a tenant.

    Missing / empty ``metadata_framework`` or empty ``fields`` → DEFAULT_REGISTRY.
    When YAML defines ``fields``, they merge onto defaults (add or overlay by key).
    Order: default field order first, then any newly introduced YAML-only keys.
    """
    raw: Mapping[str, Any]
    if metadata_framework is None:
        return DEFAULT_REGISTRY
    if hasattr(metadata_framework, "model_dump"):
        raw = metadata_framework.model_dump() or {}
    elif isinstance(metadata_framework, Mapping):
        raw = dict(metadata_framework)
    else:
        return DEFAULT_REGISTRY

    yaml_fields = raw.get("fields")
    if not yaml_fields or not isinstance(yaml_fields, Mapping):
        return DEFAULT_REGISTRY

    by_key: Dict[str, FieldSpec] = {f.key: f for f in DEFAULT_FIELDS}
    ordered_keys: List[str] = [f.key for f in DEFAULT_FIELDS]
    for key, body in yaml_fields.items():
        k = str(key).strip()
        if not k:
            continue
        # Never allow acs_codes into the Field Registry.
        if k == "acs_codes":
            continue
        body_map = body if isinstance(body, Mapping) else {}
        by_key[k] = _field_from_yaml(k, body_map, by_key.get(k))
        if k not in ordered_keys:
            ordered_keys.append(k)
    return FieldRegistry(fields=tuple(by_key[k] for k in ordered_keys if k in by_key))


def registry_for_tenant(tenant_cfg: Any = None) -> FieldRegistry:
    mf = getattr(tenant_cfg, "metadata_framework", None) if tenant_cfg is not None else None
    return registry_from_config(mf)
