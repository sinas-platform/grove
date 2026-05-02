"""record the Sinas chat id for each ingestion-run unit

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-02
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ingestion_run_unit",
        sa.Column("chat_id", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ingestion_run_unit", "chat_id")
