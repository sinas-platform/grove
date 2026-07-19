"""query_run.effort — caller-owned expansiveness bound

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-19
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "query_run",
        sa.Column("effort", sa.String(10), nullable=False, server_default="medium"),
    )


def downgrade() -> None:
    op.drop_column("query_run", "effort")
