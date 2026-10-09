"""Track XML prices awaiting observation on Kaspi."""
from alembic import op
import sqlalchemy as sa

revision = "a4d90b26c118"
down_revision = "f9c2a6d8b410"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("repricer_rules", sa.Column("pending_price", sa.Numeric(12, 2)))
    op.add_column("repricer_rules", sa.Column("price_requested_at", sa.DateTime(timezone=True)))
    op.add_column("repricer_rules", sa.Column("price_confirmed_at", sa.DateTime(timezone=True)))
    # Existing XML targets whose last observed price differs are already waiting.
    op.execute("""UPDATE repricer_rules SET pending_price = current_price,
        price_requested_at = updated_at
        WHERE current_price IS NOT NULL
        AND market_snapshot->>'observed_price' IS NOT NULL
        AND (market_snapshot->>'observed_price')::numeric != current_price""")


def downgrade() -> None:
    op.drop_column("repricer_rules", "price_confirmed_at")
    op.drop_column("repricer_rules", "price_requested_at")
    op.drop_column("repricer_rules", "pending_price")
