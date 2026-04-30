"""add immutable slug to document_class and dossier_class

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-30
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ("document_class", "dossier_class"):
        # 1. Add nullable
        op.add_column(table, sa.Column("slug", sa.String(64), nullable=True))

        # 2. Backfill: lowercased, non-alphanumeric → "_", collapse repeats, trim.
        op.execute(
            f"""
            UPDATE {table}
            SET slug = trim(both '_' from
                regexp_replace(
                    regexp_replace(lower(name), '[^a-z0-9]+', '_', 'g'),
                    '_+', '_', 'g'
                )
            )
            """
        )
        # Empty string fallback for purely-non-alphanumeric names.
        op.execute(f"UPDATE {table} SET slug = 'unnamed' WHERE slug = '' OR slug IS NULL")

        # 3. Append numeric suffix to duplicates so we can apply UNIQUE.
        op.execute(
            f"""
            WITH ranked AS (
                SELECT id, slug,
                       row_number() OVER (PARTITION BY slug ORDER BY created_at, id) AS rn
                FROM {table}
            )
            UPDATE {table} t
            SET slug = t.slug || '_' || ranked.rn::text
            FROM ranked
            WHERE t.id = ranked.id AND ranked.rn > 1
            """
        )

        # 4. NOT NULL + unique index
        op.alter_column(table, "slug", nullable=False)
        op.create_index(
            f"ix_{table}_slug",
            table,
            ["slug"],
            unique=True,
        )

        # 5. Server-side guard: reject malformed slugs on insert/update.
        op.execute(
            f"""
            ALTER TABLE {table}
            ADD CONSTRAINT ck_{table}_slug_format
            CHECK (slug ~ '^[a-z0-9][a-z0-9_]{{0,63}}$')
            """
        )


def downgrade() -> None:
    for table in ("document_class", "dossier_class"):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS ck_{table}_slug_format")
        op.drop_index(f"ix_{table}_slug", table_name=table)
        op.drop_column(table, "slug")
