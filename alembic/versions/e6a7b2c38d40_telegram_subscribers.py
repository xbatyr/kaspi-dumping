"""Store Telegram chats subscribed to price changes.

Revision ID: e6a7b2c38d40
Revises: d24a1e79a302
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e6a7b2c38d40"
down_revision: str | None = "d24a1e79a302"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_subscribers",
        sa.Column("merchant_id", sa.String(64), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("subscribed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("merchant_id", "chat_id", name=op.f("pk_telegram_subscribers")),
    )


def downgrade() -> None:
    op.drop_table("telegram_subscribers")
