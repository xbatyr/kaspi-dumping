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
from repricer.pricing import PricingStrategy

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
    product: Product, settings: ShopSettings, minimum: Decimal, maximum: Decimal, step: int
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
        rule.is_active = True
    for rule in product.rules:
        if rule.city_id not in settings.global_city_ids:
            rule.is_active = False


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
    step = payload.step or current_step
    if settings.global_strategy is None or not settings.global_city_ids:
        if not product.rules:
            product.rules.append(RepricerRule(
                city_id=DEFAULT_CITY_ID, strategy=PricingStrategy.MANUAL,
                min_price=payload.min_price, max_price=payload.max_price,
                step=step, current_price=product.base_price,
            ))
        else:
            for rule in product.rules:
                rule.min_price = payload.min_price
                rule.max_price = payload.max_price
                rule.step = step
    else:
        _apply(product, settings, payload.min_price, payload.max_price, step)
    session.commit()
    return [RuleOut.model_validate(rule) for rule in product.rules]
