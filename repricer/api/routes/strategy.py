"""One shop strategy; product prices remain individual."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from repricer.api.deps import MerchantDep, SessionDep
from repricer.api.schemas import GlobalStrategyIn, GlobalStrategyOut, ProductPriceLimitsIn, RuleOut
from repricer.api.security import ApiKeyGuard
from repricer.db.models import Product, RepricerRule, ShopSettings
from repricer.db.settings_store import load_settings
from repricer.cities import DEFAULT_CITY_ID
from repricer.pricing import (
    PercentLimits,
    PriceLimits,
    PricingStrategy,
    limits_from_percent,
)

router = APIRouter(prefix="/api/strategy", tags=["strategy"], dependencies=[ApiKeyGuard])


def _configured(product: Product) -> bool:
    return bool(product.kaspi_product_id) and any(
        rule.max_price > rule.min_price for rule in product.rules
    )


def _out(settings: ShopSettings, products: list[Product]) -> GlobalStrategyOut:
    return GlobalStrategyOut(
        strategy=settings.global_strategy,
        step=settings.global_step,
        target_position=settings.global_target_position,
        ignored_merchants=list(settings.global_ignored_merchants),
        city_ids=list(settings.global_city_ids),
        configured_products=sum(_configured(product) for product in products),
    )


def _products(session: SessionDep, merchant: MerchantDep) -> list[Product]:
    return list(session.scalars(
        select(Product).where(Product.merchant_id == merchant.merchant_id)
        .options(selectinload(Product.rules))
    ))


def _apply(
    product: Product,
    settings: ShopSettings,
    minimum: Decimal,
    maximum: Decimal,
    step: int,
    percent: PercentLimits | None = None,
) -> None:
    assert settings.global_strategy is not None
    by_city = {rule.city_id: rule for rule in product.rules}
    source_price = next((rule.current_price for rule in product.rules if rule.current_price is not None), None)
    for city_id in settings.global_city_ids:
        rule = by_city.get(city_id)
        if rule is None:
            rule = RepricerRule(product_id=product.id, city_id=city_id, current_price=source_price)
            product.rules.append(rule)
        rule.strategy = settings.global_strategy
        rule.step = step
        rule.target_position = settings.global_target_position
        rule.ignored_merchants = list(settings.global_ignored_merchants)
        rule.min_price = minimum
        rule.max_price = maximum
        _remember_percent(rule, percent)
        rule.is_active = True
    for rule in product.rules:
        if rule.city_id not in settings.global_city_ids:
            rule.is_active = False


def _remember_percent(rule: RepricerRule, percent: PercentLimits | None) -> None:
    """Store how a limit was expressed, so it can follow the base price later.

    A side given in tenge clears its percentage: the merchant has just said, in
    tenge, what that limit is, and it must not move on its own afterwards.
    """
    if percent is None:
        rule.min_percent = None
        rule.max_percent = None
        return
    rule.min_percent = percent.min_percent
    rule.max_percent = percent.max_percent


def set_product_limits(
    product: Product,
    settings: ShopSettings,
    limits: PriceLimits,
    step: int,
    percent: PercentLimits | None = None,
) -> None:
    """Give one product its price band, wherever the merchant set it from.

    With a shop strategy configured the band goes to every city that strategy
    covers; without one the product keeps a single manual rule, so that limits
    can be prepared before the bot is ever switched on.
    """
    if settings.global_strategy is None or not settings.global_city_ids:
        if not product.rules:
            rule = RepricerRule(
                city_id=DEFAULT_CITY_ID,
                strategy=PricingStrategy.MANUAL,
                min_price=limits.min_price,
                max_price=limits.max_price,
                step=step,
                current_price=product.base_price,
            )
            _remember_percent(rule, percent)
            product.rules.append(rule)
        else:
            for rule in product.rules:
                rule.min_price = limits.min_price
                rule.max_price = limits.max_price
                rule.step = step
                _remember_percent(rule, percent)
    else:
        _apply(product, settings, limits.min_price, limits.max_price, step, percent)


@router.get("", summary="Shared strategy for all configured products")
def get_global_strategy(session: SessionDep, merchant: MerchantDep) -> GlobalStrategyOut:
    return _out(load_settings(session), _products(session, merchant))


@router.put("", summary="Apply one strategy to every configured product")
def put_global_strategy(
    payload: GlobalStrategyIn, session: SessionDep, merchant: MerchantDep
) -> GlobalStrategyOut:
    settings = load_settings(session)
    settings.global_strategy = payload.strategy
    settings.global_step = payload.step
    settings.global_target_position = payload.target_position if payload.strategy is PricingStrategy.TARGET_POSITION else None
    settings.global_ignored_merchants = list(payload.ignored_merchants)
    settings.global_city_ids = list(payload.city_ids)
    products = _products(session, merchant)
    for product in products:
        if not _configured(product):
            continue
        template = next(rule for rule in product.rules if rule.max_price > rule.min_price)
        _apply(product, settings, template.min_price, template.max_price, template.step)
    session.commit()
    return _out(settings, products)


@router.put("/products/{sku}/limits", summary="Set this product's personal price limits")
def put_product_limits(
    sku: str, payload: ProductPriceLimitsIn, session: SessionDep, merchant: MerchantDep
) -> list[RuleOut]:
    settings = load_settings(session)
    product = session.scalar(
        select(Product).where(Product.merchant_id == merchant.merchant_id, Product.sku == sku)
        .options(selectinload(Product.rules))
    )
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "товар не найден")
    if not product.kaspi_product_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "сначала привяжите карточку Kaspi")
    current_step = next(
        (rule.step for rule in product.rules if rule.strategy is not PricingStrategy.MANUAL),
        product.rules[0].step if product.rules else settings.global_step,
    )
    limits, percent = resolve_limits(payload, product)
    set_product_limits(product, settings, limits, payload.step or current_step, percent)
    session.commit()
    return [RuleOut.model_validate(rule) for rule in product.rules]


def anchor_price(product: Product) -> Decimal | None:
    """The price percentages are taken from: the merchant's own, not the bot's.

    Falling back to a repriced price only happens for products imported without
    a price of their own, and even then the percentage is stored, so the limits
    settle onto the base price as soon as there is one.
    """
    if product.base_price is not None and product.base_price > 0:
        return product.base_price
    return next((rule.current_price for rule in product.rules if rule.current_price), None)


def resolve_limits(
    payload: ProductPriceLimitsIn, product: Product
) -> tuple[PriceLimits, PercentLimits | None]:
    """Work out the two prices, from tenge, from percentages, or from both.

    A side the request leaves out keeps what it had, percentage included.
    """
    existing = next(
        (rule for rule in product.rules if rule.max_price > rule.min_price),
        product.rules[0] if product.rules else None,
    )
    current = (
        PriceLimits(existing.min_price, existing.max_price)
        if existing
        else PriceLimits(Decimal(1), Decimal(1))
    )
    minimum, maximum = current.min_price, current.max_price
    min_percent = existing.min_percent if existing else None
    max_percent = existing.max_percent if existing else None

    if payload.min_price is not None:
        minimum, min_percent = payload.min_price, None
    elif payload.min_percent is not None:
        min_percent = payload.min_percent
        minimum = limits_from_percent(
            _require_anchor(product), PercentLimits(min_percent=min_percent), current
        ).min_price
    if payload.max_price is not None:
        maximum, max_percent = payload.max_price, None
    elif payload.max_percent is not None:
        max_percent = payload.max_percent
        maximum = limits_from_percent(
            _require_anchor(product), PercentLimits(max_percent=max_percent), current
        ).max_price

    if maximum <= minimum:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"максимальная цена ({maximum} ₸) должна быть выше минимальной ({minimum} ₸)",
        )
    percent = (
        PercentLimits(min_percent=min_percent, max_percent=max_percent)
        if min_percent is not None or max_percent is not None
        else None
    )
    return PriceLimits(minimum, maximum), percent


def _require_anchor(product: Product) -> Decimal:
    anchor = anchor_price(product)
    if anchor is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"{product.sku}: проценты считаются от вашей цены, а её нет — задайте цену или лимит в тенге",
        )
    return anchor
