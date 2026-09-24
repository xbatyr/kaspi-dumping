"""Store one shared strategy and city selection for the shop.

Revision ID: d24a1e79a302
Revises: bccd44db25c0
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d24a1e79a302"
down_revision: str | None = "bccd44db25c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("shop_settings", sa.Column("global_strategy", sa.String(32), nullable=True))
    op.add_column("shop_settings", sa.Column("global_step", sa.Integer(), server_default="1", nullable=False))
    op.add_column("shop_settings", sa.Column("global_target_position", sa.Integer(), nullable=True))
    op.add_column("shop_settings", sa.Column("global_ignored_merchants", postgresql.ARRAY(sa.String(64)), server_default=sa.text("'{}'"), nullable=False))
    op.add_column("shop_settings", sa.Column("global_city_ids", postgresql.ARRAY(sa.String(16)), server_default=sa.text("'{}'"), nullable=False))


def downgrade() -> None:
    op.drop_column("shop_settings", "global_city_ids")
    op.drop_column("shop_settings", "global_ignored_merchants")
    op.drop_column("shop_settings", "global_target_position")
    op.drop_column("shop_settings", "global_step")
    op.drop_column("shop_settings", "global_strategy")
