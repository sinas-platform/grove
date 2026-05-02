"""ingestion runs — bulk reprocessing tracking

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-02
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ingestion_run",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # pending → running → completed | failed | cancelled
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        # The stages the run will execute, in order. Stored as a JSON list of
        # stage keys (e.g. ["classifier", "entity_extractor"]).
        sa.Column("stages", postgresql.JSONB, nullable=False),
        # The filter params used to select documents (jsonb of the request).
        sa.Column("filter", postgresql.JSONB, nullable=False, server_default="{}"),
        # Counts maintained by the worker.
        sa.Column("total_units", sa.Integer, nullable=False, server_default="0"),
        sa.Column("done_units", sa.Integer, nullable=False, server_default="0"),
        sa.Column("failed_units", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", sa.Text()),
        sa.Column(
            "started_by",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_ingestion_run_status", "ingestion_run", ["status"])
    op.create_index("ix_ingestion_run_created", "ingestion_run", ["created_at"])

    op.create_table(
        "ingestion_run_unit",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ingestion_run.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stage", sa.String(64), nullable=False),
        # pending → running → succeeded | failed
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("error", sa.Text()),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_ingestion_run_unit_run", "ingestion_run_unit", ["run_id"])
    op.create_index("ix_ingestion_run_unit_doc", "ingestion_run_unit", ["document_id"])
    op.create_index(
        "ix_ingestion_run_unit_pending",
        "ingestion_run_unit",
        ["run_id", "status"],
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )


def downgrade() -> None:
    op.drop_table("ingestion_run_unit")
    op.drop_table("ingestion_run")
