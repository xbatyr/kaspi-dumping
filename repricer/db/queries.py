"""Queries shared by the API, the worker and the Telegram bot."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from repricer.db.models import PriceHistory


def latest_changes(
    session: Session, product_ids: Sequence[int]
) -> dict[tuple[int, str], PriceHistory]:
    """The most recent price change of every (product, city), in one query.

    DISTINCT ON keeps the first row of each group, so the ordering below decides
    which row that is: the newest change.
    """
    if not product_ids:
        return {}
    statement = (
        select(PriceHistory)
        .where(PriceHistory.product_id.in_(product_ids))
        .distinct(PriceHistory.product_id, PriceHistory.city_id)
        .order_by(
            PriceHistory.product_id,
            PriceHistory.city_id,
            PriceHistory.created_at.desc(),
            PriceHistory.id.desc(),
        )
    )
    return {(row.product_id, row.city_id): row for row in session.scalars(statement)}
