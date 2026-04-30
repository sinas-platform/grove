"""baseline schema

Revision ID: 0001
Revises:
Create Date: 2026-04-28
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Required Postgres extensions
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")  # gen_random_uuid()
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # ─────────── document_class ───────────
    op.create_table(
        "document_class",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("description", sa.Text()),
        sa.Column("summarization_guidance", sa.Text()),
        sa.Column("classification_hints", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "document_class_property",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_class_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_class.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("schema", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("guidance", sa.Text()),
        sa.Column("manual", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("required", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("cardinality", sa.String(10), nullable=False, server_default="one"),
        sa.Column("schema_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("document_class_id", "name"),
    )
    op.create_index("ix_dcp_class", "document_class_property", ["document_class_id"])

    # ─────────── entity_type ───────────
    op.create_table(
        "entity_type",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("description", sa.Text()),
        sa.Column("guidance", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "document_class_entity_type",
        sa.Column(
            "document_class_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_class.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "entity_type_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("entity_type.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    # ─────────── relationship_definition ───────────
    op.create_table(
        "relationship_definition",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("description", sa.Text()),
        sa.Column("source_ref_type", sa.String(40), nullable=False),
        sa.Column("source_ref_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_ref_type", sa.String(40), nullable=False),
        sa.Column("target_ref_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cardinality", sa.String(10), nullable=False, server_default="many"),
        sa.Column("extraction_guidance", sa.Text()),
        sa.Column("discovery_guidance", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "relationship_state",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "relationship_definition_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("relationship_definition.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("counts_as_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("relationship_definition_id", "name"),
    )
    op.create_index("ix_rs_def", "relationship_state", ["relationship_definition_id"])

    # ─────────── dossier_class ───────────
    op.create_table(
        "dossier_class",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("description", sa.Text()),
        sa.Column("guidance", sa.Text()),
        sa.Column("summarization_guidance", sa.Text()),
        sa.Column("classification_hints", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "dossier_class_property",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "dossier_class_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dossier_class.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("schema", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("guidance", sa.Text()),
        sa.Column("manual", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("required", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("cardinality", sa.String(10), nullable=False, server_default="one"),
        sa.Column("schema_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("dossier_class_id", "name"),
    )
    op.create_index("ix_dop_class", "dossier_class_property", ["dossier_class_id"])

    op.create_table(
        "dossier_class_document_class",
        sa.Column(
            "dossier_class_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dossier_class.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "document_class_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_class.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("required", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("cardinality", sa.String(10), nullable=False, server_default="many"),
    )

    # ─────────── playbook_scope ───────────
    op.create_table(
        "playbook_scope",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("skill_namespace", sa.String(200), nullable=False),
        sa.Column("skill_name", sa.String(200), nullable=False),
        sa.Column(
            "document_class_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_class.id", ondelete="CASCADE"),
        ),
        sa.Column(
            "dossier_class_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dossier_class.id", ondelete="CASCADE"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "skill_namespace",
            "skill_name",
            "document_class_id",
            "dossier_class_id",
            name="uq_playbook_scope",
        ),
    )
    op.create_index("ix_ps_ns", "playbook_scope", ["skill_namespace"])
    op.create_index("ix_ps_name", "playbook_scope", ["skill_name"])

    # ─────────── document, document_version ───────────
    op.create_table(
        "document",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("summary", sa.Text()),
        sa.Column("toc", postgresql.JSONB),
        sa.Column(
            "document_class_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_class.id", ondelete="SET NULL"),
        ),
        sa.Column("current_version_id", postgresql.UUID(as_uuid=True)),
        sa.Column("classification_confidence", sa.Float()),
        sa.Column("collection_file_id", sa.String(500)),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("roles", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_doc_class", "document", ["document_class_id"])
    op.create_index("ix_doc_owner", "document", ["owner_id"])
    op.create_index("ix_doc_collection_file", "document", ["collection_file_id"])
    op.create_index("ix_doc_roles", "document", ["roles"], postgresql_using="gin")

    op.create_table(
        "document_version",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("content_md", sa.Text()),
        sa.Column("content_tsvector", postgresql.TSVECTOR),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_dv_doc", "document_version", ["document_id"])
    op.create_index(
        "ix_document_version_doc_v",
        "document_version",
        ["document_id", "version"],
        unique=True,
    )
    op.create_index(
        "ix_document_version_tsv",
        "document_version",
        ["content_tsvector"],
        postgresql_using="gin",
    )
    # Auto-update tsvector trigger
    op.execute(
        """
        CREATE OR REPLACE FUNCTION document_version_tsv_update() RETURNS trigger AS $$
        BEGIN
          NEW.content_tsvector := to_tsvector('simple', coalesce(NEW.content_md, ''));
          RETURN NEW;
        END
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_document_version_tsv
        BEFORE INSERT OR UPDATE OF content_md ON document_version
        FOR EACH ROW EXECUTE FUNCTION document_version_tsv_update();
        """
    )

    # ─────────── property_value ───────────
    op.create_table(
        "property_value",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "property_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_class_property.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True)),
        sa.Column("value", postgresql.JSONB, nullable=False),
        sa.Column("source_span", postgresql.JSONB),
        sa.Column("method", sa.String(20), nullable=False, server_default="auto"),
        sa.Column("confidence", sa.Float()),
        sa.Column("schema_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("locked", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("reason", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_pv_prop", "property_value", ["property_id"])
    op.create_index("ix_pv_doc", "property_value", ["document_id"])

    # ─────────── entity, entity_alias, entity_mention ───────────
    op.create_table(
        "entity",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "entity_type_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("entity_type.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("canonical_form", sa.String(500), nullable=False),
        sa.Column("metadata", postgresql.JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_entity_type", "entity", ["entity_type_id"])

    op.create_table(
        "entity_alias",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "entity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("entity.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("alias", sa.String(500), nullable=False),
        sa.Column("context_rules", postgresql.JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_alias_entity", "entity_alias", ["entity_id"])

    op.create_table(
        "entity_mention",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True)),
        sa.Column(
            "entity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("entity.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("span", postgresql.JSONB, nullable=False),
        sa.Column("confidence", sa.Float()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_mention_doc", "entity_mention", ["document_id"])
    op.create_index("ix_mention_entity", "entity_mention", ["entity_id"])

    # ─────────── dossier ───────────
    op.create_table(
        "dossier",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "dossier_class_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dossier_class.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("status", sa.String(50)),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("roles", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_dossier_class", "dossier", ["dossier_class_id"])
    op.create_index("ix_dossier_owner", "dossier", ["owner_id"])
    op.create_index("ix_dossier_roles", "dossier", ["roles"], postgresql_using="gin")

    op.create_table(
        "dossier_property_value",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "property_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dossier_class_property.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "dossier_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dossier.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("value", postgresql.JSONB, nullable=False),
        sa.Column("source_span", postgresql.JSONB),
        sa.Column("method", sa.String(20), nullable=False, server_default="auto"),
        sa.Column("confidence", sa.Float()),
        sa.Column("schema_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("locked", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("reason", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_dpv_prop", "dossier_property_value", ["property_id"])
    op.create_index("ix_dpv_dossier", "dossier_property_value", ["dossier_id"])

    op.create_table(
        "dossier_document",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "dossier_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dossier.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(100)),
        sa.Column("added_by", postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_dd_dossier", "dossier_document", ["dossier_id"])
    op.create_index("ix_dd_doc", "dossier_document", ["document_id"])
    op.create_index(
        "ix_dossier_doc_unique", "dossier_document", ["dossier_id", "document_id"], unique=True
    )

    # ─────────── relationship, relationship_proposal ───────────
    op.create_table(
        "relationship",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "relationship_definition_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("relationship_definition.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "current_state_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("relationship_state.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "evidence_document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document.id", ondelete="SET NULL"),
        ),
        sa.Column("evidence_span", postgresql.JSONB),
        sa.Column("confidence", sa.Float()),
        sa.Column("last_verified_at", sa.DateTime(timezone=True)),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_rel_def", "relationship", ["relationship_definition_id"])
    op.create_index("ix_rel_source", "relationship", ["source_id"])
    op.create_index("ix_rel_target", "relationship", ["target_id"])

    op.create_table(
        "relationship_proposal",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "relationship_definition_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("relationship_definition.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "suggested_state_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("relationship_state.id", ondelete="SET NULL"),
        ),
        sa.Column("proposing_agent", sa.String(200)),
        sa.Column("reasoning", sa.Text()),
        sa.Column(
            "evidence_document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document.id", ondelete="SET NULL"),
        ),
        sa.Column("evidence_span", postgresql.JSONB),
        sa.Column("confidence", sa.Float()),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_rp_def", "relationship_proposal", ["relationship_definition_id"])
    op.create_index("ix_rp_status", "relationship_proposal", ["status"])
    op.create_index(
        "ix_rel_proposal_pending",
        "relationship_proposal",
        ["status"],
        postgresql_where=sa.text("status = 'pending'"),
    )

    # ─────────── result, result_document, result_trace ───────────
    op.create_table(
        "result",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("invoked_skill_names", postgresql.JSONB),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column(
            "parent_result_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("result.id", ondelete="SET NULL"),
        ),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("roles", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_result_owner", "result", ["owner_id"])
    op.create_index("ix_result_roles", "result", ["roles"], postgresql_using="gin")
    op.create_index(
        "ix_result_published",
        "result",
        ["status"],
        postgresql_where=sa.text("status = 'published'"),
    )

    op.create_table(
        "result_document",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "result_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("result.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True)),
        sa.Column("rank", sa.Integer()),
        sa.Column("reason", sa.Text()),
        sa.Column("added_by_agent", sa.String(200)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_rd_result", "result_document", ["result_id"])
    op.create_index("ix_rd_doc", "result_document", ["document_id"])

    op.create_table(
        "result_trace",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "result_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("result.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("agent", sa.String(200), nullable=False),
        sa.Column("action", sa.String(200), nullable=False),
        sa.Column("parameters", postgresql.JSONB),
        sa.Column("outcome", postgresql.JSONB),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_rt_result", "result_trace", ["result_id"])

    # ─────────── answer, answer_claim, claim_evidence ───────────
    op.create_table(
        "answer",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "source_result_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("result.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "source_dossier_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dossier.id", ondelete="SET NULL"),
        ),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("roles", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_answer_result", "answer", ["source_result_id"])
    op.create_index("ix_answer_dossier", "answer", ["source_dossier_id"])
    op.create_index("ix_answer_owner", "answer", ["owner_id"])
    op.create_index("ix_answer_roles", "answer", ["roles"], postgresql_using="gin")

    op.create_table(
        "answer_claim",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "answer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("answer.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("claim_text", sa.Text(), nullable=False),
        sa.Column("claim_type", sa.String(100)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_claim_answer", "answer_claim", ["answer_id"])

    op.create_table(
        "claim_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "claim_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("answer_claim.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True)),
        sa.Column("span", postgresql.JSONB, nullable=False),
        sa.Column("stance", sa.String(20), nullable=False, server_default="supports"),
        sa.Column("relevance", sa.Float()),
        sa.Column("validated", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("validation_reasoning", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_ce_claim", "claim_evidence", ["claim_id"])
    op.create_index("ix_ce_doc", "claim_evidence", ["document_id"])

    # Defaults: rely on Postgres for UUID generation when client doesn't supply
    for table in (
        "document_class",
        "document_class_property",
        "entity_type",
        "relationship_definition",
        "relationship_state",
        "dossier_class",
        "dossier_class_property",
        "playbook_scope",
        "document",
        "document_version",
        "property_value",
        "entity",
        "entity_alias",
        "entity_mention",
        "dossier",
        "dossier_property_value",
        "dossier_document",
        "relationship",
        "relationship_proposal",
        "result",
        "result_document",
        "result_trace",
        "answer",
        "answer_claim",
        "claim_evidence",
    ):
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN id SET DEFAULT gen_random_uuid()"
        )


def downgrade() -> None:
    # Single baseline migration — collapse on downgrade.
    for table in (
        "claim_evidence",
        "answer_claim",
        "answer",
        "result_trace",
        "result_document",
        "result",
        "relationship_proposal",
        "relationship",
        "dossier_document",
        "dossier_property_value",
        "dossier",
        "entity_mention",
        "entity_alias",
        "entity",
        "property_value",
        "document_version",
        "document",
        "playbook_scope",
        "dossier_class_document_class",
        "dossier_class_property",
        "dossier_class",
        "relationship_state",
        "relationship_definition",
        "document_class_entity_type",
        "entity_type",
        "document_class_property",
        "document_class",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS document_version_tsv_update()")
