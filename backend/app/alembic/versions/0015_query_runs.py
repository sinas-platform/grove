"""query runs — server-supervised question pipelines

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-19
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "query_run",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False, server_default="full"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("subqueries", postgresql.JSONB()),
        sa.Column("searches", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("parent_result_id", postgresql.UUID(as_uuid=True)),
        sa.Column("answer_id", postgresql.UUID(as_uuid=True)),
        sa.Column("synthesis_chat_id", sa.String(64)),
        sa.Column("run_discovery", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("error", sa.Text()),
        sa.Column("telemetry", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("roles", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_query_run_status", "query_run", ["status"])


def downgrade() -> None:
    op.drop_index("ix_query_run_status", table_name="query_run")
    op.drop_table("query_run")
