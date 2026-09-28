"""Cache the public thumbnail of each linked Kaspi card.

Revision ID: aa31bfc95320
Revises: 95f28d4fad05
"""

from alembic import op
import sqlalchemy as sa

revision = "aa31bfc95320"
down_revision = "95f28d4fad05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("products", sa.Column("image_url", sa.String(length=512), nullable=True))
    op.add_column("products", sa.Column("image_checked_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("products", "image_checked_at")
    op.drop_column("products", "image_url")
