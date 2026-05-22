"""drop submitting_user_token columns from run tables

The no-worker design (see ingestion_runner and discovery_runner module
docstrings) drives all state transitions from GET handlers that carry the
user's bearer in-request. No need to persist tokens on the run row.

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-18
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ("discovery_run", "ingestion_run"):
        op.drop_column(table, "submitting_user_token")


def downgrade() -> None:
    for table in ("discovery_run", "ingestion_run"):
        op.add_column(
            table,
            sa.Column("submitting_user_token", sa.Text(), nullable=True),
        )
