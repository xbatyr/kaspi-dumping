"""Catalogue-wide tools and the margin calculator.

The tools are the things a merchant does to hundreds of products at once and
would otherwise do by hand: set a floor a few percent under every own price,
put every price back up to its ceiling after a price war, stop lowering what is
not on sale anyway.

Each tool refuses a product it cannot do safely (no Kaspi card, no price of its
own) and says how many it refused, instead of silently doing part of the work.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from repricer.api.deps import MerchantDep, SessionDep
from repricer.api.routes.strategy import anchor_price, set_product_limits
from repricer.api.schemas import BulkToolsIn, BulkToolsOut, MarginOut, MarginPreviewIn
from repricer.api.security import ApiKeyGuard
from repricer.db.models import Product, ShopSettings
from repricer.db.settings_store import load_settings
from repricer.pricing import (
    MarginInputs,
    PercentLimits,
    PriceLimits,
    calculate_margin,
    limits_from_percent,
)

router = APIRouter(prefix="/api/tools", tags=["tools"], dependencies=[ApiKeyGuard])


@router.post("/bulk", summary="Apply the catalogue tools to many products at once")
def bulk_tools(payload: BulkToolsIn, session: SessionDep, merchant: MerchantDep) -> BulkToolsOut:
    settings = load_settings(session)
    statement = (
        select(Product)
        .where(Product.merchant_id == merchant.merchant_id)
        .options(selectinload(Product.rules), selectinload(Product.availabilities))
        .order_by(Product.sku)
    )
    if payload.skus:
        statement = statement.where(Product.sku.in_(payload.skus))
    products = list(session.scalars(statement))
    if not products:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "товары не найдены")

    skipped: Counter[str] = Counter()
    limits_set = prices_raised = decrease_disabled = 0
    setting_limits = payload.set_min_percent is not None or payload.set_max_percent is not None

    for product in products:
        if setting_limits and _set_limits(product, settings, payload, skipped):
            limits_set += 1
        if payload.raise_to_max and _raise_to_max(product):
            prices_raised += 1
        if payload.disable_decrease_when_off_sale and not product.on_sale():
            if product.auto_decrease:
                product.auto_decrease = False
                decrease_disabled += 1

    session.commit()
    return BulkToolsOut(
        products_seen=len(products),
        limits_set=limits_set,
        prices_raised=prices_raised,
        decrease_disabled=decrease_disabled,
        skipped=dict(skipped),
    )


def _set_limits(
    product: Product, settings: ShopSettings, payload: BulkToolsIn, skipped: Counter[str]
) -> bool:
    """A floor and ceiling a percentage away from this product's own price.

    The percentage is stored on the rules, so the band keeps following the own
    price afterwards instead of freezing at today's figure.
    """
    if not product.kaspi_product_id:
        skipped["нет карточки Kaspi"] += 1
        return False
    anchor = anchor_price(product)
    if anchor is None:
        skipped["нет своей цены"] += 1
        return False

    existing = next(
        (rule for rule in product.rules if rule.max_price > rule.min_price),
        product.rules[0] if product.rules else None,
    )
    current = (
        PriceLimits(existing.min_price, existing.max_price)
        if existing
        else PriceLimits(anchor, anchor)
    )
    percent = PercentLimits(
        min_percent=(
            payload.set_min_percent
            if payload.set_min_percent is not None
            else (existing.min_percent if existing else None)
        ),
        max_percent=(
            payload.set_max_percent
            if payload.set_max_percent is not None
            else (existing.max_percent if existing else None)
        ),
    )
    limits = limits_from_percent(anchor, percent, current)
    if limits.max_price <= limits.min_price:
        skipped["границы совпали"] += 1
        return False
    step = existing.step if existing else settings.global_step
    set_product_limits(product, settings, limits, step, percent)
    # A floor only means something if the bot may go down to it, and the same
    # for the ceiling upwards: that is what these two tools are really for.
    if payload.set_min_percent is not None:
        product.auto_decrease = True
    if payload.set_max_percent is not None:
        product.auto_increase = True
    return True


def _raise_to_max(product: Product) -> bool:
    """Put the price back up to the ceiling, for when the competition has left.

    Only the price in the next price list moves; the bot's own next pass may
    lower it again if a competitor is still there.
    """
    raised = False
    for rule in product.rules:
        if rule.current_price is None or rule.current_price < rule.max_price:
            rule.current_price = rule.max_price
            raised = True
    return raised


@router.post("/margin", summary="What one sale leaves after commission, tax and delivery")
def preview_margin(
    payload: MarginPreviewIn, session: SessionDep, merchant: MerchantDep
) -> MarginOut:
    """The calculator behind the margin figures in the catalogue.

    Anything the request leaves out is taken from the product, and anything the
    product does not have from the shop settings.
    """
    settings = load_settings(session)
    product = None
    if payload.sku is not None:
        product = session.scalar(
            select(Product).where(
                Product.merchant_id == merchant.merchant_id, Product.sku == payload.sku
            )
        )
        if product is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "товар не найден")

    return MarginOut.of(
        calculate_margin(
            MarginInputs(
                price=payload.price,
                purchase_price=_first(
                    payload.purchase_price, product.purchase_price if product else None
                ),
                tax_percent=_first(payload.tax_percent, settings.tax_percent) or Decimal(0),
                commission_percent=_first(
                    payload.commission_percent,
                    product.commission_percent if product else None,
                    settings.commission_percent,
                )
                or Decimal(0),
                delivery_cost=_first(
                    payload.delivery_cost,
                    product.delivery_cost if product else None,
                    settings.delivery_cost,
                )
                or Decimal(0),
            )
        )
    )


def _first(*values: Decimal | None) -> Decimal | None:
    return next((value for value in values if value is not None), None)
