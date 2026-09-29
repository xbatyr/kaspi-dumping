"""min price short reason

Revision ID: c5d91e3a7f02
Revises: a8c4e21f7b90
Create Date: 2026-09-29 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = 'c5d91e3a7f02'
down_revision: str | None = 'a8c4e21f7b90'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REASONS = (
    "'strategy_target', 'capped_at_max', 'fallback_position', 'pinned_to_min', "
    "'no_competitors', 'fixed_price', 'already_first', 'direction_disabled'"
)


def upgrade() -> None:
    # «Не хватает мин. цены»: the engine now keeps the price instead of
    # dropping to the floor when first place is out of reach.
    op.drop_constraint(op.f("ck_price_history_decision_reason"), "price_history", type_="check")
    op.create_check_constraint(
        "decision_reason", "price_history", f"reason IN ({REASONS}, 'min_price_short')"
    )


def downgrade() -> None:
    op.execute("UPDATE price_history SET reason = 'pinned_to_min' WHERE reason = 'min_price_short'")
    op.drop_constraint(op.f("ck_price_history_decision_reason"), "price_history", type_="check")
    op.create_check_constraint("decision_reason", "price_history", f"reason IN ({REASONS})")
