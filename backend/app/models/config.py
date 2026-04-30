from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models._common import TimestampMixin, uuid_pk


# ─────────────────────────────────────────────────────────────
# Document classes
# ─────────────────────────────────────────────────────────────
class DocumentClass(Base, TimestampMixin):
    __tablename__ = "document_class"

    id: Mapped[uuid.UUID] = uuid_pk()
    # Immutable slug — used as a stable handle in permission strings
    # (`grove.documents/<slug>.read:own`). Constrained to [a-z0-9_], max 64.
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    summarization_guidance: Mapped[str | None] = mapped_column(Text)
    classification_hints: Mapped[str | None] = mapped_column(Text)

    properties: Mapped[list["DocumentClassProperty"]] = relationship(
        back_populates="document_class", cascade="all, delete-orphan"
    )
    entity_types: Mapped[list["DocumentClassEntityType"]] = relationship(
        back_populates="document_class", cascade="all, delete-orphan"
    )


class DocumentClassProperty(Base, TimestampMixin):
    __tablename__ = "document_class_property"
    __table_args__ = (UniqueConstraint("document_class_id", "name"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    document_class_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_class.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    schema: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    guidance: Mapped[str | None] = mapped_column(Text)
    manual: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cardinality: Mapped[str] = mapped_column(String(10), default="one", nullable=False)  # one|many
    schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    document_class: Mapped[DocumentClass] = relationship(back_populates="properties")


# ─────────────────────────────────────────────────────────────
# Entity types
# ─────────────────────────────────────────────────────────────
class EntityType(Base, TimestampMixin):
    __tablename__ = "entity_type"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    guidance: Mapped[str | None] = mapped_column(Text)


class DocumentClassEntityType(Base):
    __tablename__ = "document_class_entity_type"

    document_class_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_class.id", ondelete="CASCADE"), primary_key=True
    )
    entity_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entity_type.id", ondelete="CASCADE"), primary_key=True
    )
    document_class: Mapped[DocumentClass] = relationship(back_populates="entity_types")


# ─────────────────────────────────────────────────────────────
# Relationship definitions and states
# ─────────────────────────────────────────────────────────────
class RelationshipDefinition(Base, TimestampMixin):
    __tablename__ = "relationship_definition"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    # source/target ref types: "document_class" | "entity_type" | "dossier_class"
    source_ref_type: Mapped[str] = mapped_column(String(40), nullable=False)
    source_ref_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    target_ref_type: Mapped[str] = mapped_column(String(40), nullable=False)
    target_ref_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    cardinality: Mapped[str] = mapped_column(String(10), default="many", nullable=False)
    extraction_guidance: Mapped[str | None] = mapped_column(Text)
    discovery_guidance: Mapped[str | None] = mapped_column(Text)

    states: Mapped[list["RelationshipState"]] = relationship(
        back_populates="definition", cascade="all, delete-orphan"
    )


class RelationshipState(Base, TimestampMixin):
    __tablename__ = "relationship_state"
    __table_args__ = (UniqueConstraint("relationship_definition_id", "name"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    relationship_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("relationship_definition.id", ondelete="CASCADE"),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    counts_as_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    definition: Mapped[RelationshipDefinition] = relationship(back_populates="states")


# ─────────────────────────────────────────────────────────────
# Dossier configuration (optional — only used when configured)
# ─────────────────────────────────────────────────────────────
class DossierClass(Base, TimestampMixin):
    __tablename__ = "dossier_class"

    id: Mapped[uuid.UUID] = uuid_pk()
    # Immutable slug — used as a stable handle in permission strings
    # (`grove.dossiers/<slug>.read:own`). Constrained to [a-z0-9_], max 64.
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    guidance: Mapped[str | None] = mapped_column(Text)
    summarization_guidance: Mapped[str | None] = mapped_column(Text)
    classification_hints: Mapped[str | None] = mapped_column(Text)

    properties: Mapped[list["DossierClassProperty"]] = relationship(
        back_populates="dossier_class", cascade="all, delete-orphan"
    )
    document_classes: Mapped[list["DossierClassDocumentClass"]] = relationship(
        back_populates="dossier_class", cascade="all, delete-orphan"
    )


class DossierClassProperty(Base, TimestampMixin):
    __tablename__ = "dossier_class_property"
    __table_args__ = (UniqueConstraint("dossier_class_id", "name"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    dossier_class_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dossier_class.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    schema: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    guidance: Mapped[str | None] = mapped_column(Text)
    manual: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cardinality: Mapped[str] = mapped_column(String(10), default="one", nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    dossier_class: Mapped[DossierClass] = relationship(back_populates="properties")


class DossierClassDocumentClass(Base):
    __tablename__ = "dossier_class_document_class"

    dossier_class_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dossier_class.id", ondelete="CASCADE"), primary_key=True
    )
    document_class_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_class.id", ondelete="CASCADE"), primary_key=True
    )
    required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cardinality: Mapped[str] = mapped_column(String(10), default="many", nullable=False)

    dossier_class: Mapped[DossierClass] = relationship(back_populates="document_classes")


# ─────────────────────────────────────────────────────────────
# Playbook scope (skills live in Sinas; only the join lives here)
# ─────────────────────────────────────────────────────────────
class PlaybookScope(Base, TimestampMixin):
    """Maps a Sinas Skill (by namespace + name) to applicable classes.

    Empty rows for a given skill mean 'applies everywhere'.
    """

    __tablename__ = "playbook_scope"
    __table_args__ = (
        UniqueConstraint(
            "skill_namespace",
            "skill_name",
            "document_class_id",
            "dossier_class_id",
            name="uq_playbook_scope",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    skill_namespace: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    skill_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    document_class_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_class.id", ondelete="CASCADE")
    )
    dossier_class_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dossier_class.id", ondelete="CASCADE")
    )
