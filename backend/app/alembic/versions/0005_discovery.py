"""discovery runs + config proposals

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-02
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ─────────────── discovery_run ───────────────
    op.create_table(
        "discovery_run",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("kind", sa.String(64), nullable=False),
        # pending → scanning → consolidating → completed | failed | cancelled
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        # Existing-config awareness:
        #   greenfield: ignore existing config, propose freely
        #   incremental: only propose what isn't already covered
        sa.Column("mode", sa.String(20), nullable=False, server_default="incremental"),
        # Filter shape mirrors RunFilter from ingestion runs (jsonb of the request)
        sa.Column("filter", postgresql.JSONB, nullable=False, server_default="{}"),
        # For property discovery, the parent class to discover properties for.
        sa.Column(
            "parent_class_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_class.id", ondelete="SET NULL"),
        ),
        sa.Column("sample_size", sa.Integer()),  # null = no sampling, scan all
        sa.Column("total_docs", sa.Integer, nullable=False, server_default="0"),
        sa.Column("scanned_docs", sa.Integer, nullable=False, server_default="0"),
        sa.Column("failed_docs", sa.Integer, nullable=False, server_default="0"),
        sa.Column("candidate_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("proposal_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", sa.Text()),
        sa.Column("started_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("consolidating_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_discovery_run_status", "discovery_run", ["status"])
    op.create_index("ix_discovery_run_created", "discovery_run", ["created_at"])

    # ─────────────── discovery_run_unit ───────────────
    op.create_table(
        "discovery_run_unit",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("discovery_run.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("error", sa.Text()),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("chat_id", sa.String(64)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_discovery_run_unit_run", "discovery_run_unit", ["run_id"])
    op.create_index(
        "ix_discovery_run_unit_pending",
        "discovery_run_unit",
        ["run_id", "status"],
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )

    # ─────────────── discovery_candidate ───────────────
    # Raw per-doc findings emitted by the discovery agent. Consolidation
    # turns these into config_proposal rows.
    op.create_table(
        "discovery_candidate",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("discovery_run.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column(
            "evidence_document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document.id", ondelete="SET NULL"),
        ),
        sa.Column("evidence_span", postgresql.JSONB),
        sa.Column("confidence", sa.Float()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_discovery_candidate_run", "discovery_candidate", ["run_id"])
    op.create_index("ix_discovery_candidate_kind", "discovery_candidate", ["kind"])

    # ─────────────── config_proposal ───────────────
    # Consolidated proposal — the dedup'd unit shown in the review queue.
    op.create_table(
        "config_proposal",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        # pending → approved | rejected | merged
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        # IDs of supporting discovery_candidate rows (uuid[] in jsonb).
        sa.Column("supporting_candidate_ids", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column(
            "discovery_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("discovery_run.id", ondelete="SET NULL"),
        ),
        # When approved: pointer to the config row that was created.
        sa.Column("created_resource_id", postgresql.UUID(as_uuid=True)),
        # When merged: pointer to the existing config row it was folded into.
        sa.Column("merged_into_id", postgresql.UUID(as_uuid=True)),
        sa.Column("notes", sa.Text()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_config_proposal_kind_status", "config_proposal", ["kind", "status"])
    op.create_index("ix_config_proposal_run", "config_proposal", ["discovery_run_id"])


def downgrade() -> None:
    op.drop_table("config_proposal")
    op.drop_table("discovery_candidate")
    op.drop_table("discovery_run_unit")
    op.drop_table("discovery_run")
