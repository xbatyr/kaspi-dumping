"""Persist Telegram price approvals for test mode."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "f9c2a6d8b410"
down_revision = "e3a9d5c7b1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "price_proposals",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("rule_id", sa.BigInteger(), sa.ForeignKey("repricer_rules.id", ondelete="CASCADE"), nullable=False),
        sa.Column("merchant_id", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("current_price", sa.Numeric(12, 2)),
        sa.Column("proposed_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("message", sa.String(4000), nullable=False),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("delivered_to", postgresql.ARRAY(sa.String(32)), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by", sa.String(32)),
        sa.CheckConstraint("status IN ('pending','rejected','applied','superseded','expired')", name="proposal_status"),
    )
    op.create_index("ix_price_proposals_rule_created", "price_proposals", ["rule_id", "id"])


def downgrade() -> None:
    op.drop_table("price_proposals")
