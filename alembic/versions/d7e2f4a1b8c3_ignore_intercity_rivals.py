"""ignore intercity rivals

Revision ID: d7e2f4a1b8c3
Revises: c5d91e3a7f02
Create Date: 2026-09-29 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'd7e2f4a1b8c3'
down_revision: str | None = 'c5d91e3a7f02'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Off by default: the bot keeps competing with every store until asked not to.
    op.add_column(
        'shop_settings',
        sa.Column('ignore_intercity_rivals', sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column('shop_settings', 'ignore_intercity_rivals')
