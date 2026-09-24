"""Getting the catalogue in.

Nothing else works until products exist: a rule needs a product, and the feed
needs its brand and its stock. This is the way in, one product at a time or a
whole price list at once.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from repricer.api.deps import MerchantDep, SessionDep
from repricer.api.schemas import (
    AvailabilityIn,
    RuleInline,
    ImportError,
    ImportIn,
    ImportResult,
    ProductIn,
    ProductOut,
    RuleOut,
)
from repricer.api.security import ApiKeyGuard
from repricer.db.models import Product, ProductAvailability, RepricerRule
from repricer.uploader import MerchantIdentity

router = APIRouter(prefix="/api/products", tags=["products"], dependencies=[ApiKeyGuard])


@router.get("", summary="Products in the catalogue")
def list_products(
    session: SessionDep,
    merchant: MerchantDep,
    search: Annotated[str | None, Query(description="Substring of the SKU or title.")] = None,
    only_blocked: Annotated[
        bool, Query(description="Only products the feed cannot publish yet.")
    ] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ProductOut]:
    statement = (
        select(Product)
        .where(Product.merchant_id == merchant.merchant_id)
        .options(selectinload(Product.availabilities), selectinload(Product.rules))
        .order_by(Product.sku)
        .limit(limit)
        .offset(offset)
    )
    if search:
        pattern = f"%{search}%"
        statement = statement.where(Product.sku.ilike(pattern) | Product.title.ilike(pattern))
    products = [_product_out(product) for product in session.scalars(statement)]
    if only_blocked:
        return [product for product in products if product.feed_blocker]
    return products


@router.get("/{sku}", summary="One product", responses={404: {"description": "Unknown SKU"}})
def get_product(sku: str, session: SessionDep, merchant: MerchantDep) -> ProductOut:
    return _product_out(_by_sku(session, merchant, sku))


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Add a product",
    responses={409: {"description": "That SKU is already in the catalogue"}},
)
def create_product(payload: ProductIn, session: SessionDep, merchant: MerchantDep) -> ProductOut:
    existing = session.scalar(
        select(Product).where(
            Product.merchant_id == merchant.merchant_id, Product.sku == payload.sku
        )
    )
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{payload.sku} is already in the catalogue")
    product = Product(merchant_id=merchant.merchant_id, sku=payload.sku)
    _apply(product, payload)
    session.add(product)
    session.commit()
    session.refresh(product)
    return _product_out(product)


@router.put("/{sku}", summary="Replace a product", responses={404: {"description": "Unknown SKU"}})
def update_product(
    sku: str, payload: ProductIn, session: SessionDep, merchant: MerchantDep
) -> ProductOut:
    product = _by_sku(session, merchant, sku)
    if payload.sku != sku:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "the SKU is the product's identity here and cannot be renamed",
        )
    _apply(product, payload)
    session.commit()
    session.refresh(product)
    return _product_out(product)


@router.post("/import", summary="Add or update many products at once")
def import_products(payload: ImportIn, session: SessionDep, merchant: MerchantDep) -> ImportResult:
    """Upserts by SKU, so the same price list can be sent again after an edit.

    One bad row does not sink the batch: it comes back in ``errors`` while the
    rest is saved.
    """
    known = {
        product.sku: product
        for product in session.scalars(
            select(Product)
            .where(
                Product.merchant_id == merchant.merchant_id,
                Product.sku.in_([item.sku for item in payload.items]),
            )
            .options(selectinload(Product.availabilities), selectinload(Product.rules))
        )
    }
    created = updated = 0
    errors: list[ImportError] = []
    seen: set[str] = set()

    for item in payload.items:
        if item.sku in seen:
            errors.append(ImportError(sku=item.sku, reason="дубль SKU в одной загрузке"))
            continue
        seen.add(item.sku)
        product = known.get(item.sku)
        if product is None:
            product = Product(merchant_id=merchant.merchant_id, sku=item.sku)
            session.add(product)
            created += 1
        else:
            updated += 1
        _apply(product, item)

    session.commit()
    logger.info(
        "merchant={}: import added {} products, updated {}, rejected {}",
        merchant.merchant_id,
        created,
        updated,
        len(errors),
    )
    return ImportResult(created=created, updated=updated, errors=errors)


def _apply(product: Product, payload: ProductIn) -> None:
    product.title = payload.title
    product.kaspi_product_id = payload.kaspi_product_id
    product.brand = payload.brand
    product.base_price = payload.base_price
    product.is_active = payload.is_active
    # The payload is the whole truth about stock: what it omits is gone. Rows are
    # matched by store_id and updated in place, because clearing the list first
    # would re-insert the same (product, store) pair before the delete lands and
    # trip the unique index.
    current = {entry.store_id: entry for entry in product.availabilities}
    wanted = {entry.store_id: entry for entry in payload.availabilities}
    for store_id, entry in wanted.items():
        existing = current.get(store_id)
        if existing is None:
            product.availabilities.append(
                ProductAvailability(
                    store_id=store_id,
                    available=entry.available,
                    stock_count=entry.stock_count,
                    preorder_days=entry.preorder_days,
                )
            )
        else:
            existing.available = entry.available
            existing.stock_count = entry.stock_count
            existing.preorder_days = entry.preorder_days
    for store_id, existing in current.items():
        if store_id not in wanted:
            product.availabilities.remove(existing)

    _apply_rules(product, payload.rules)


def _apply_rules(product: Product, rules: list[RuleInline]) -> None:
    """Create or update the cities that came with the product.

    Cities that were not mentioned keep their settings: an import of one city
    must never quietly switch off the others.
    """
    by_city = {rule.city_id: rule for rule in product.rules}
    for wanted in rules:
        rule = by_city.get(wanted.city_id)
        if rule is None:
            rule = RepricerRule(city_id=wanted.city_id, strategy=wanted.strategy,
                                min_price=wanted.min_price, max_price=wanted.max_price)
            product.rules.append(rule)
        rule.strategy = wanted.strategy
        rule.min_price = wanted.min_price
        rule.max_price = wanted.max_price
        rule.step = wanted.step
        rule.target_position = wanted.target_position
        rule.is_active = wanted.is_active


def _product_out(product: Product) -> ProductOut:
    return ProductOut(
        sku=product.sku,
        title=product.title,
        kaspi_product_id=product.kaspi_product_id,
        brand=product.brand,
        base_price=product.base_price,
        is_active=product.is_active,
        availabilities=[
            AvailabilityIn(
                store_id=entry.store_id,
                available=entry.available,
                stock_count=entry.stock_count,
                preorder_days=entry.preorder_days,
            )
            for entry in sorted(product.availabilities, key=lambda entry: entry.store_id)
        ],
        rules=[RuleOut.model_validate(rule) for rule in product.rules],
        feed_blocker=feed_blocker(product),
    )


def feed_blocker(product: Product) -> str | None:
    """Why this product would be left out of the price list, in the owner's words."""
    if not product.is_active:
        return "товар выключен"
    if not (product.brand or "").strip():
        return "не указан бренд"
    if not product.availabilities:
        return "нет складов с остатком"
    if not product.rules:
        return "нет правил по городам"
    if all(rule.current_price is None for rule in product.rules):
        return "цена ещё не рассчитана: дождитесь цикла воркера"
    return None


def _by_sku(session: Session, merchant: MerchantIdentity, sku: str) -> Product:
    product = session.scalar(
        select(Product)
        .where(Product.merchant_id == merchant.merchant_id, Product.sku == sku)
        .options(selectinload(Product.availabilities), selectinload(Product.rules))
    )
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no product with sku {sku}")
    return product
