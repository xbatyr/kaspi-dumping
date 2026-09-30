"""price history created_at index

Revision ID: b4e8c2f1a9d6
Revises: d7e2f4a1b8c3
Create Date: 2026-09-30 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = 'b4e8c2f1a9d6'
down_revision: str | None = 'd7e2f4a1b8c3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Concurrently, outside the migration's transaction: the old worker keeps
    # writing history while a deploy migrates, and a plain CREATE INDEX would
    # hold those writes until it finished.
    with op.get_context().autocommit_block():
        op.create_index(
            'ix_price_history_created_at',
            'price_history',
            ['created_at'],
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            'ix_price_history_created_at',
            table_name='price_history',
            postgresql_concurrently=True,
        )
