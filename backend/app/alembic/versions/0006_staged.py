"""add staged flag to document

Staged documents are uploaded but skip the auto-pipeline; they're visible
to discovery and front-matter scans but excluded from retrieval and from
default IngestionRun selection. Promotion = a manual IngestionRun against
staged_only=true that, on success, flips the flag back to false.

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-07
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "document",
        sa.Column("staged", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Partial index — only staged rows; the unstaged majority gets no index
    # bloat. Powers "show me staged docs" and the staged_only run filter.
    op.create_index(
        "ix_document_staged_true",
        "document",
        ["id"],
        unique=False,
        postgresql_where=sa.text("staged = true"),
    )


def downgrade() -> None:
    op.drop_index("ix_document_staged_true", table_name="document")
    op.drop_column("document", "staged")
