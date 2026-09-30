"""test mode, raise cooldown, intercity rivals ignored by default

Revision ID: e3a9d5c7b1f2
Revises: b4e8c2f1a9d6
Create Date: 2026-09-30 14:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'e3a9d5c7b1f2'
down_revision: str | None = 'b4e8c2f1a9d6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Cutting the price under a store that delivers days later gave margin away
    # for nothing (462 897 -> 455 553 under an intercity seller with 3 stars),
    # so ignoring those stores is switched on for the shop and by default.
    op.alter_column('shop_settings', 'ignore_intercity_rivals', server_default=sa.true())
    op.execute("UPDATE shop_settings SET ignore_intercity_rivals = true")
    op.add_column(
        'shop_settings',
        sa.Column('test_mode', sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    # Two hours by default: a price cut is not taken back on the very next pass.
    op.add_column(
        'shop_settings',
        sa.Column('raise_cooldown_minutes', sa.Integer(), server_default=sa.text('120'), nullable=False),
    )
    op.create_check_constraint(
        'raise_cooldown_not_negative', 'shop_settings', 'raise_cooldown_minutes >= 0'
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f('ck_shop_settings_raise_cooldown_not_negative'), 'shop_settings', type_='check'
    )
    op.drop_column('shop_settings', 'raise_cooldown_minutes')
    op.drop_column('shop_settings', 'test_mode')
    op.alter_column('shop_settings', 'ignore_intercity_rivals', server_default=sa.false())
