"""One glance at whether the shop is ready to be repriced."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from fastapi import APIRouter

from repricer.api.deps import MerchantDep, SessionDep
from repricer.api.routes.products import feed_blocker
from repricer.api.schemas import StatusOut
from repricer.api.security import ApiKeyGuard
from repricer.db.models import PriceHistory, Product, RepricerRule
from repricer.db.queries import latest_changes
from repricer.db.settings_store import settings_or_none
from repricer.pricing import PricingStrategy
from repricer.uploader import KASPI_TIMEZONE

router = APIRouter(prefix="/api/status", tags=["status"], dependencies=[ApiKeyGuard])


@router.get("", summary="Is the shop ready, and what is the bot doing")
def get_status(session: SessionDep, merchant: MerchantDep) -> StatusOut:
    products = list(
        session.scalars(
            select(Product)
            .where(Product.merchant_id == merchant.merchant_id)
            .options(selectinload(Product.availabilities), selectinload(Product.rules))
        )
    )
    blockers: dict[str, int] = {}
    ready = 0
    for product in products:
        reason = feed_blocker(product)
        if reason is None:
            ready += 1
        else:
            blockers[reason] = blockers.get(reason, 0) + 1

    rules = [
        rule for product in products for rule in product.rules
        if rule.strategy is not PricingStrategy.MANUAL
    ]
    changes = latest_changes(session, [product.id for product in products])
    since = datetime.now(KASPI_TIMEZONE).replace(hour=0, minute=0, second=0, microsecond=0)
    changes_today = (
        session.scalar(
            select(func.count())
            .select_from(PriceHistory)
            .join(Product, PriceHistory.product_id == Product.id)
            .where(Product.merchant_id == merchant.merchant_id, PriceHistory.created_at >= since)
        )
        or 0
    )
    evaluated = [rule.last_evaluated_at for rule in rules if rule.last_evaluated_at is not None]
    shop = settings_or_none(session)

    return StatusOut(
        products_total=len(products),
        products_ready=ready,
        blockers=blockers,
        rules_active=sum(1 for rule in rules if rule.is_active),
        rules_paused=sum(1 for rule in rules if not rule.is_active),
        first_place=sum(
            1
            for rule in rules
            if rule.is_active
            and (change := changes.get((rule.product_id, rule.city_id))) is not None
            and change.expected_position == 1
        ),
        last_run_at=max(evaluated) if evaluated else None,
        changes_today=changes_today,
        feed_ready=ready > 0 and all(
            not product.is_active or feed_blocker(product) is None for product in products
        ),
        worker_enabled=shop.worker_enabled if shop is not None else False,
        global_strategy_configured=bool(shop and shop.global_strategy and shop.global_city_ids),
    )
