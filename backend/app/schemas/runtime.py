from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel, OwnedOut, Span, TimestampedOut


# ─────────────────────────────────────────────────────────────
# Documents
# ─────────────────────────────────────────────────────────────
class DocumentOut(OwnedOut):
    filename: str
    summary: str | None = None
    toc: dict[str, Any] | None = None
    document_class_id: uuid.UUID | None = None
    classification_confidence: float | None = None
    collection_file_id: str | None = None


class DocumentPatch(BaseModel):
    summary: str | None = None
    toc: dict[str, Any] | None = None
    document_class_id: uuid.UUID | None = None
    classification_confidence: float | None = None


class DocumentVersionOut(TimestampedOut):
    document_id: uuid.UUID
    version: int
    content_md: str | None = None


class PropertyValueIn(BaseModel):
    property_id: uuid.UUID
    document_id: uuid.UUID
    document_version_id: uuid.UUID | None = None
    value: dict[str, Any] | list[Any] | str | int | float | bool | None
    source_span: Span | None = None
    method: Literal["auto", "manual"] = "auto"
    confidence: float | None = None
    schema_version: int = 1
    locked: bool = False
    reason: str | None = None


class PropertyValueOut(TimestampedOut):
    property_id: uuid.UUID
    document_id: uuid.UUID
    document_version_id: uuid.UUID | None = None
    value: Any
    source_span: dict[str, Any] | None = None
    method: str
    confidence: float | None = None
    schema_version: int
    locked: bool
    reason: str | None = None


# ─────────────────────────────────────────────────────────────
# Entities and relationships
# ─────────────────────────────────────────────────────────────
class EntityMentionIn(BaseModel):
    document_id: uuid.UUID
    document_version_id: uuid.UUID | None = None
    entity_id: uuid.UUID
    span: Span
    confidence: float | None = None


class EntityMentionOut(TimestampedOut, EntityMentionIn):
    pass


class EntityMentionWithEntityOut(TimestampedOut):
    document_id: uuid.UUID
    document_version_id: uuid.UUID | None = None
    entity_id: uuid.UUID
    span: dict[str, Any]
    confidence: float | None = None
    entity_canonical_form: str
    entity_type_id: uuid.UUID
    entity_type_name: str | None = None


class RelationshipIn(BaseModel):
    relationship_definition_id: uuid.UUID
    source_id: uuid.UUID
    target_id: uuid.UUID
    current_state_id: uuid.UUID | None = None
    evidence_document_id: uuid.UUID | None = None
    evidence_span: Span | None = None
    confidence: float | None = None
    notes: str | None = None


class RelationshipOut(TimestampedOut, RelationshipIn):
    last_verified_at: datetime | None = None


class RelationshipProposalIn(BaseModel):
    relationship_definition_id: uuid.UUID
    source_id: uuid.UUID
    target_id: uuid.UUID
    suggested_state_id: uuid.UUID | None = None
    proposing_agent: str | None = None
    reasoning: str | None = None
    evidence_document_id: uuid.UUID | None = None
    evidence_span: Span | None = None
    confidence: float | None = None


class RelationshipProposalOut(TimestampedOut, RelationshipProposalIn):
    status: Literal["pending", "approved", "rejected"]
    reviewed_at: datetime | None = None
    reviewed_by: uuid.UUID | None = None


# ─────────────────────────────────────────────────────────────
# Results, traces, answers
# ─────────────────────────────────────────────────────────────
class ResultOut(OwnedOut):
    query: str
    invoked_skill_names: list[str] | None = None
    status: str
    parent_result_id: uuid.UUID | None = None
    published_at: datetime | None = None


class ResultDocumentOut(TimestampedOut):
    result_id: uuid.UUID
    document_id: uuid.UUID
    document_version_id: uuid.UUID | None = None
    rank: int | None = None
    reason: str | None = None
    added_by_agent: str | None = None


class AnswerOut(OwnedOut):
    source_result_id: uuid.UUID | None = None
    source_dossier_id: uuid.UUID | None = None
    question: str
    status: str
    published_at: datetime | None = None


class ClaimOut(TimestampedOut):
    answer_id: uuid.UUID
    sequence: int
    claim_text: str
    claim_type: str | None = None


class ClaimEvidenceIn(BaseModel):
    document_id: uuid.UUID
    document_version_id: uuid.UUID | None = None
    span: Span
    stance: Literal["supports", "contradicts", "qualifies"] = "supports"
    relevance: float | None = None


class ClaimEvidenceOut(TimestampedOut):
    claim_id: uuid.UUID
    document_id: uuid.UUID
    document_version_id: uuid.UUID | None = None
    span: dict[str, Any]
    stance: str
    relevance: float | None = None
    validated: bool
    validation_reasoning: str | None = None


# ─────────────────────────────────────────────────────────────
# Dossiers
# ─────────────────────────────────────────────────────────────
class DossierIn(BaseModel):
    dossier_class_id: uuid.UUID
    name: str
    status: str | None = None


class DossierOut(OwnedOut):
    dossier_class_id: uuid.UUID
    name: str
    status: str | None = None
    closed_at: datetime | None = None


class DossierDocumentIn(BaseModel):
    document_id: uuid.UUID
    role: str | None = None


class DossierDocumentOut(TimestampedOut, DossierDocumentIn):
    dossier_id: uuid.UUID
    added_by: uuid.UUID | None = None


# ─────────────────────────────────────────────────────────────
# Retrieval — filter object lives in Sinas State, but we mirror it here
# ─────────────────────────────────────────────────────────────
class FieldFilter(BaseModel):
    field: str
    values: list[Any] | None = None
    op: Literal["eq", "in", "gte", "lte", "gt", "lt", "neq"] = "in"
    value: Any | None = None
    join: Literal["and", "or"] = "and"


class RegexFilter(BaseModel):
    field: str
    pattern: str


class GroveFilter(BaseModel):
    document_class_id: uuid.UUID | None = None
    field_filters: list[FieldFilter] = Field(default_factory=list)
    regex_filters: list[RegexFilter] = Field(default_factory=list)
    text_search: str | None = None
    dossier_id: uuid.UUID | None = None
    dossier_class_id: uuid.UUID | None = None
    explicit_excludes: list[uuid.UUID] = Field(default_factory=list)
    explicit_includes: list[uuid.UUID] = Field(default_factory=list)


class IntrospectIn(BaseModel):
    filter: GroveFilter | None = None
    fields: list[str] | None = None
    top_k: int = 25


class IntrospectFieldDistribution(ORMModel):
    field: str
    values: list[dict[str, Any]]
    total_documents: int


class IntrospectOut(BaseModel):
    candidate_count: int
    distributions: list[IntrospectFieldDistribution]
