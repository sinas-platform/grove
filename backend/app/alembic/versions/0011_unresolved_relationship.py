"""unresolved_relationship — citations whose target node isn't ingested yet

When an agent finds a relationship (e.g. an article citing an ECLI) but the
target node isn't in the graph, the edge is parked here keyed by `target_key`
instead of being dropped. A later resolver promotes it to a real `relationship`
when a node carrying that key is ingested — avoiding a backward scan over every
existing document on each new ingest.

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-01
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "unresolved_relationship",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relationship_definition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_key", sa.String(length=500), nullable=False),
        sa.Column("target_key_kind", sa.String(length=64), nullable=True),
        sa.Column("suggested_state_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("proposing_agent", sa.String(length=200), nullable=True),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("evidence_document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("evidence_span", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="unresolved"),
        sa.Column("resolved_relationship_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["relationship_definition_id"], ["relationship_definition.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["suggested_state_id"], ["relationship_state.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["evidence_document_id"], ["document.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["resolved_relationship_id"], ["relationship.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_unresolved_relationship_relationship_definition_id",
        "unresolved_relationship",
        ["relationship_definition_id"],
    )
    op.create_index(
        "ix_unresolved_relationship_source_id", "unresolved_relationship", ["source_id"]
    )
    op.create_index(
        "ix_unresolved_relationship_status", "unresolved_relationship", ["status"]
    )
    # Partial index on the resolver's hot path: find open candidates by key.
    op.create_index(
        "ix_unresolved_rel_open",
        "unresolved_relationship",
        ["target_key"],
        postgresql_where=sa.text("status = 'unresolved'"),
    )


def downgrade() -> None:
    op.drop_index("ix_unresolved_rel_open", table_name="unresolved_relationship")
    op.drop_index("ix_unresolved_relationship_status", table_name="unresolved_relationship")
    op.drop_index("ix_unresolved_relationship_source_id", table_name="unresolved_relationship")
    op.drop_index(
        "ix_unresolved_relationship_relationship_definition_id",
        table_name="unresolved_relationship",
    )
    op.drop_table("unresolved_relationship")
