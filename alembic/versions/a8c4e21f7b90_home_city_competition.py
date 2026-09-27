"""home city competition

Revision ID: a8c4e21f7b90
Revises: 95f28d4fad05
Create Date: 2026-09-27 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a8c4e21f7b90'
down_revision: str | None = '95f28d4fad05'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Almaty, as repricer.cities.DEFAULT_CITY_ID; off by default, so existing
    # shops keep repricing every city until the owner switches it on.
    op.add_column(
        'shop_settings',
        sa.Column('home_city_id', sa.String(length=16), server_default='750000000', nullable=False),
    )
    op.add_column(
        'shop_settings',
        sa.Column('compete_home_city_only', sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column('shop_settings', 'compete_home_city_only')
    op.drop_column('shop_settings', 'home_city_id')
