"""Queries shared by the API, the worker and the Telegram bot."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select, true
from sqlalchemy.orm import Session, aliased

from repricer.db.models import PriceHistory, RepricerRule


def latest_changes(
    session: Session, product_ids: Sequence[int]
) -> dict[tuple[int, str], PriceHistory]:
    """The most recent price change of every rule of these products, in one query.

    Each rule takes the single newest row of its (product, city) straight off
    the history index, so the cost follows the number of rules, not the length
    of the history: a DISTINCT ON over the whole history read every row ever
    written, which after a month of repricing took seconds per call. Every
    caller looks changes up by rule, so history of a city without a rule is
    never needed.
    """
    if not product_ids:
        return {}
    newest = (
        select(PriceHistory)
        .where(
            PriceHistory.product_id == RepricerRule.product_id,
            PriceHistory.city_id == RepricerRule.city_id,
        )
        .order_by(PriceHistory.created_at.desc(), PriceHistory.id.desc())
        .limit(1)
        .lateral()
    )
    change = aliased(PriceHistory, newest)
    statement = (
        select(change)
        .select_from(RepricerRule)
        .join(newest, true())
        .where(RepricerRule.product_id.in_(product_ids))
    )
    return {(row.product_id, row.city_id): row for row in session.scalars(statement)}
