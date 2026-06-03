"""entity creation gating: creation_mode + entity_proposal + unresolved_entity_mention

Per-EntityType policy for what happens when the ingestion agent reports a
mention whose canonical_form (after alias matching) doesn't match an existing
entity:
  - open    : insert the Entity directly (current behavior; default)
  - review  : insert an EntityProposal; mention points at the proposal until
              approved, then promoted to a real Entity
  - closed  : do not create; park the mention in unresolved_entity_mention
              for a human to either match-to-existing, promote, or dismiss

Mirrors the existing RelationshipProposal / UnresolvedRelationship pattern.

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-03
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "entity_type",
        sa.Column(
            "creation_mode",
            sa.String(10),
            nullable=False,
            server_default="open",
        ),
    )
    op.create_check_constraint(
        "ck_entity_type_creation_mode",
        "entity_type",
        "creation_mode IN ('open', 'review', 'closed')",
    )

    op.create_table(
        "entity_proposal",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "entity_type_id",
            UUID(as_uuid=True),
            sa.ForeignKey("entity_type.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("canonical_form", sa.String(500), nullable=False),
        sa.Column("extra_metadata", JSONB),
        sa.Column("proposing_agent", sa.String(200)),
        sa.Column("reasoning", sa.Text),
        sa.Column(
            "evidence_document_id",
            UUID(as_uuid=True),
            sa.ForeignKey("document.id", ondelete="SET NULL"),
        ),
        sa.Column("evidence_span", JSONB),
        sa.Column("confidence", sa.Float),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending", index=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by", UUID(as_uuid=True)),
        sa.Column("promoted_entity_id", UUID(as_uuid=True), sa.ForeignKey("entity.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_entity_proposal_pending",
        "entity_proposal",
        ["status"],
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.create_table(
        "unresolved_entity_mention",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "entity_type_id",
            UUID(as_uuid=True),
            sa.ForeignKey("entity_type.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("mention_text", sa.String(500), nullable=False),
        sa.Column(
            "document_id",
            UUID(as_uuid=True),
            sa.ForeignKey("document.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("document_version_id", UUID(as_uuid=True)),
        sa.Column("span", JSONB, nullable=False),
        sa.Column("confidence", sa.Float),
        sa.Column("proposing_agent", sa.String(200)),
        sa.Column("reasoning", sa.Text),
        sa.Column("status", sa.String(20), nullable=False, server_default="unresolved", index=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_by", UUID(as_uuid=True)),
        sa.Column(
            "resolved_entity_id",
            UUID(as_uuid=True),
            sa.ForeignKey("entity.id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_unresolved_entity_mention_open",
        "unresolved_entity_mention",
        ["entity_type_id", "mention_text"],
        postgresql_where=sa.text("status = 'unresolved'"),
    )


def downgrade() -> None:
    op.drop_index("ix_unresolved_entity_mention_open", table_name="unresolved_entity_mention")
    op.drop_table("unresolved_entity_mention")
    op.drop_index("ix_entity_proposal_pending", table_name="entity_proposal")
    op.drop_table("entity_proposal")
    op.drop_constraint("ck_entity_type_creation_mode", "entity_type")
    op.drop_column("entity_type", "creation_mode")
