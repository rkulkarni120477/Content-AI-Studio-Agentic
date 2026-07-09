"""Registry for all DIS LangGraph pipeline agents."""
from __future__ import annotations
from typing import Type
from services.agents.base import BasePipelineAgent
from services.agents.source_discovery_agent import SourceDiscoveryAgent
from services.agents.upload_intake_agent import UploadIntakeAgent
from services.agents.file_type_classification_agent import FileTypeClassificationAgent
from services.agents.raw_storage_agent import RawStorageAgent
from services.agents.deduplication_agent import DeduplicationAgent
from services.agents.extractor_selection_agent import ExtractorSelectionAgent
from services.agents.content_extraction_agent import ContentExtractionAgent
from services.agents.visual_understanding_agent import VisualUnderstandingAgent
from services.agents.content_classification_agent import ContentClassificationAgent
from services.agents.metadata_extraction_agent import MetadataExtractionAgent
from services.agents.metadata_tagging_agent import MetadataTaggingAgent
from services.agents.structure_extraction_agent import StructureExtractionAgent
from services.agents.specialized_structure_extraction_agent import SpecializedStructureExtractionAgent
from services.agents.content_unit_creation_agent import ContentUnitCreationAgent
from services.agents.quality_check_agent import QualityCheckAgent
from services.agents.studio_payload_preparation_agent import StudioPayloadPreparationAgent
from services.agents.processed_storage_agent import ProcessedStorageAgent
from services.agents.structure_store_upsert_agent import StructureStoreUpsertAgent
from services.agents.embedding_generation_agent import EmbeddingGenerationAgent
from services.agents.vector_store_upsert_agent import VectorStoreUpsertAgent
from services.agents.validation_report_agent import ValidationReportAgent
from services.agents.checkpoint_save_agent import CheckpointSaveAgent
from services.agents.finalize_agent import FinalizeAgent

STEP_ORDER = ['source_discovery', 'upload_intake', 'file_type_classification', 'raw_storage', 'deduplication', 'extractor_selection', 'content_extraction', 'visual_understanding', 'content_classification', 'metadata_extraction', 'metadata_tagging', 'structure_extraction', 'specialized_structure_extraction', 'content_unit_creation', 'quality_check', 'studio_payload_preparation', 'processed_storage', 'structure_store_upsert', 'embedding_generation', 'vector_store_upsert', 'validation_report', 'checkpoint_save', 'finalize']

AGENT_REGISTRY: dict[str, Type[BasePipelineAgent]] = {
    "source_discovery": SourceDiscoveryAgent,
    "upload_intake": UploadIntakeAgent,
    "file_type_classification": FileTypeClassificationAgent,
    "raw_storage": RawStorageAgent,
    "deduplication": DeduplicationAgent,
    "extractor_selection": ExtractorSelectionAgent,
    "content_extraction": ContentExtractionAgent,
    "visual_understanding": VisualUnderstandingAgent,
    "content_classification": ContentClassificationAgent,
    "metadata_extraction": MetadataExtractionAgent,
    "metadata_tagging": MetadataTaggingAgent,
    "structure_extraction": StructureExtractionAgent,
    "specialized_structure_extraction": SpecializedStructureExtractionAgent,
    "content_unit_creation": ContentUnitCreationAgent,
    "quality_check": QualityCheckAgent,
    "studio_payload_preparation": StudioPayloadPreparationAgent,
    "processed_storage": ProcessedStorageAgent,
    "structure_store_upsert": StructureStoreUpsertAgent,
    "embedding_generation": EmbeddingGenerationAgent,
    "vector_store_upsert": VectorStoreUpsertAgent,
    "validation_report": ValidationReportAgent,
    "checkpoint_save": CheckpointSaveAgent,
    "finalize": FinalizeAgent,
}

def get_agent(name: str) -> Type[BasePipelineAgent]:
    try:
        return AGENT_REGISTRY[name]
    except KeyError as exc:
        raise KeyError(f"Unknown pipeline agent: {name}") from exc
