"""Product cost, repricing directions and current market snapshot."""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision = "f7b3c1d290a4"
down_revision = "e6a7b2c38d40"
branch_labels = None
depends_on = None

OLD_REASONS = "'strategy_target', 'capped_at_max', 'fallback_position', 'pinned_to_min', 'no_competitors', 'fixed_price'"

def upgrade() -> None:
    op.add_column("products", sa.Column("purchase_price", sa.Numeric(12, 2), nullable=True))
    op.create_check_constraint("purchase_price_not_negative", "products", "purchase_price IS NULL OR purchase_price >= 0")
    op.add_column("products", sa.Column("auto_decrease", sa.Boolean(), server_default=sa.true(), nullable=False))
    op.add_column("products", sa.Column("auto_increase", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("repricer_rules", sa.Column("market_snapshot", postgresql.JSONB(), nullable=True))
    op.drop_constraint(op.f("ck_price_history_decision_reason"), "price_history", type_="check")
    op.create_check_constraint("decision_reason", "price_history", f"reason IN ({OLD_REASONS}, 'already_first', 'direction_disabled')")

def downgrade() -> None:
    op.execute("UPDATE price_history SET reason = 'strategy_target' WHERE reason IN ('already_first', 'direction_disabled')")
    op.drop_constraint(op.f("ck_price_history_decision_reason"), "price_history", type_="check")
    op.create_check_constraint("decision_reason", "price_history", f"reason IN ({OLD_REASONS})")
    op.drop_column("repricer_rules", "market_snapshot")
    op.drop_column("products", "auto_increase")
    op.drop_column("products", "auto_decrease")
    op.drop_constraint(op.f("ck_products_purchase_price_not_negative"), "products", type_="check")
    op.drop_column("products", "purchase_price")
