from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models._common import OwnedMixin, TimestampMixin, uuid_pk


# ─────────────────────────────────────────────────────────────
# Documents and versions
# ─────────────────────────────────────────────────────────────
class Document(Base, TimestampMixin, OwnedMixin):
    __tablename__ = "document"

    id: Mapped[uuid.UUID] = uuid_pk()
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    toc: Mapped[dict | None] = mapped_column(JSONB)
    document_class_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_class.id", ondelete="SET NULL"), index=True
    )
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    classification_confidence: Mapped[float | None] = mapped_column(Float)
    collection_file_id: Mapped[str | None] = mapped_column(String(500), index=True)

    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        foreign_keys="DocumentVersion.document_id",
    )


class DocumentVersion(Base, TimestampMixin):
    __tablename__ = "document_version"
    __table_args__ = (
        Index("ix_document_version_doc_v", "document_id", "version", unique=True),
        Index("ix_document_version_tsv", "content_tsvector", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_md: Mapped[str | None] = mapped_column(Text)
    content_tsvector: Mapped[str | None] = mapped_column(TSVECTOR)

    document: Mapped[Document] = relationship(
        back_populates="versions", foreign_keys=[document_id]
    )


# ─────────────────────────────────────────────────────────────
# Property values
# ─────────────────────────────────────────────────────────────
class PropertyValue(Base, TimestampMixin):
    __tablename__ = "property_value"

    id: Mapped[uuid.UUID] = uuid_pk()
    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_class_property.id", ondelete="CASCADE"),
        index=True,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document.id", ondelete="CASCADE"), index=True
    )
    document_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source_span: Mapped[dict | None] = mapped_column(JSONB)
    method: Mapped[str] = mapped_column(String(20), default="auto", nullable=False)  # auto|manual
    confidence: Mapped[float | None] = mapped_column(Float)
    schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)


# ─────────────────────────────────────────────────────────────
# Entities
# ─────────────────────────────────────────────────────────────
class Entity(Base, TimestampMixin):
    __tablename__ = "entity"

    id: Mapped[uuid.UUID] = uuid_pk()
    entity_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entity_type.id", ondelete="CASCADE"), index=True
    )
    canonical_form: Mapped[str] = mapped_column(String(500), nullable=False)
    extra_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB)


class EntityAlias(Base, TimestampMixin):
    __tablename__ = "entity_alias"

    id: Mapped[uuid.UUID] = uuid_pk()
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entity.id", ondelete="CASCADE"), index=True
    )
    alias: Mapped[str] = mapped_column(String(500), nullable=False)
    context_rules: Mapped[dict | None] = mapped_column(JSONB)


class EntityMention(Base, TimestampMixin):
    __tablename__ = "entity_mention"

    id: Mapped[uuid.UUID] = uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document.id", ondelete="CASCADE"), index=True
    )
    document_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entity.id", ondelete="CASCADE"), index=True
    )
    span: Mapped[dict] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)


# ─────────────────────────────────────────────────────────────
# Relationships (instances) and proposals
# ─────────────────────────────────────────────────────────────
class Relationship(Base, TimestampMixin):
    __tablename__ = "relationship"

    id: Mapped[uuid.UUID] = uuid_pk()
    relationship_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("relationship_definition.id", ondelete="CASCADE"),
        index=True,
    )
    # Polymorphic refs — type comes from the definition.
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    current_state_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("relationship_state.id", ondelete="SET NULL")
    )
    evidence_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document.id", ondelete="SET NULL")
    )
    evidence_span: Mapped[dict | None] = mapped_column(JSONB)
    confidence: Mapped[float | None] = mapped_column(Float)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)


class RelationshipProposal(Base, TimestampMixin):
    __tablename__ = "relationship_proposal"
    __table_args__ = (Index("ix_rel_proposal_pending", "status", postgresql_where="status = 'pending'"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    relationship_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("relationship_definition.id", ondelete="CASCADE"),
        index=True,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    suggested_state_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("relationship_state.id", ondelete="SET NULL")
    )
    proposing_agent: Mapped[str | None] = mapped_column(String(200))
    reasoning: Mapped[str | None] = mapped_column(Text)
    evidence_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document.id", ondelete="SET NULL")
    )
    evidence_span: Mapped[dict | None] = mapped_column(JSONB)
    confidence: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False, index=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


# ─────────────────────────────────────────────────────────────
# Results, traces, answers, claim evidence
# ─────────────────────────────────────────────────────────────
class Result(Base, TimestampMixin, OwnedMixin):
    __tablename__ = "result"
    __table_args__ = (
        Index("ix_result_published", "status", postgresql_where="status = 'published'"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    query: Mapped[str] = mapped_column(Text, nullable=False)
    invoked_skill_names: Mapped[list[str] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    parent_result_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("result.id", ondelete="SET NULL")
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResultDocument(Base, TimestampMixin):
    __tablename__ = "result_document"

    id: Mapped[uuid.UUID] = uuid_pk()
    result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("result.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document.id", ondelete="CASCADE"), index=True
    )
    document_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    rank: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(Text)
    added_by_agent: Mapped[str | None] = mapped_column(String(200))


class ResultTrace(Base):
    __tablename__ = "result_trace"

    id: Mapped[uuid.UUID] = uuid_pk()
    result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("result.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    agent: Mapped[str] = mapped_column(String(200), nullable=False)
    action: Mapped[str] = mapped_column(String(200), nullable=False)
    parameters: Mapped[dict | None] = mapped_column(JSONB)
    outcome: Mapped[dict | None] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Answer(Base, TimestampMixin, OwnedMixin):
    __tablename__ = "answer"

    id: Mapped[uuid.UUID] = uuid_pk()
    source_result_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("result.id", ondelete="SET NULL"), index=True
    )
    source_dossier_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dossier.id", ondelete="SET NULL"), index=True
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AnswerClaim(Base, TimestampMixin):
    __tablename__ = "answer_claim"

    id: Mapped[uuid.UUID] = uuid_pk()
    answer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("answer.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    claim_type: Mapped[str | None] = mapped_column(String(100))


class ClaimEvidence(Base, TimestampMixin):
    __tablename__ = "claim_evidence"

    id: Mapped[uuid.UUID] = uuid_pk()
    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("answer_claim.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document.id", ondelete="CASCADE"), index=True
    )
    document_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    span: Mapped[dict] = mapped_column(JSONB, nullable=False)
    stance: Mapped[str] = mapped_column(String(20), default="supports", nullable=False)
    relevance: Mapped[float | None] = mapped_column(Float)
    validated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    validation_reasoning: Mapped[str | None] = mapped_column(Text)


# ─────────────────────────────────────────────────────────────
# Dossiers (optional)
# ─────────────────────────────────────────────────────────────
class Dossier(Base, TimestampMixin, OwnedMixin):
    __tablename__ = "dossier"

    id: Mapped[uuid.UUID] = uuid_pk()
    dossier_class_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dossier_class.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str | None] = mapped_column(String(50))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DossierPropertyValue(Base, TimestampMixin):
    __tablename__ = "dossier_property_value"

    id: Mapped[uuid.UUID] = uuid_pk()
    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dossier_class_property.id", ondelete="CASCADE"), index=True
    )
    dossier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dossier.id", ondelete="CASCADE"), index=True
    )
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source_span: Mapped[dict | None] = mapped_column(JSONB)
    method: Mapped[str] = mapped_column(String(20), default="auto", nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)


class DossierDocument(Base, TimestampMixin):
    __tablename__ = "dossier_document"
    __table_args__ = (Index("ix_dossier_doc_unique", "dossier_id", "document_id", unique=True),)

    id: Mapped[uuid.UUID] = uuid_pk()
    dossier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dossier.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str | None] = mapped_column(String(100))
    added_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
