"""Getting the catalogue in.

Nothing else works until products exist: a rule needs a product, and the feed
needs its brand and its stock. This is the way in, one product at a time or a
whole price list at once.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Body, HTTPException, Query, status
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from repricer.api.deps import MerchantDep, SessionDep
from repricer.api.schemas import (
    AlgaTopChangeOut,
    AlgaTopImportOut,
    AvailabilityIn,
    RuleInline,
    ImportError,
    ImportIn,
    ImportResult,
    ProductIn,
    ProductManagementIn,
    ProductOut,
    RuleOut,
    XmlImportResult,
)
from repricer.api.security import ApiKeyGuard
from repricer.db.models import Product, ProductAvailability, RepricerRule, ShopSettings
from repricer.db.settings_store import load_settings
from repricer.uploader import MerchantIdentity
from repricer.uploader.algatop_import import AlgaTopFileError, AlgaTopRow, parse_algatop
from repricer.uploader.kaspi_import import ImportedOffer, parse_kaspi_xml
from repricer.pricing import PricingStrategy

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
    _apply(product, payload, is_new=True)
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
    _apply(product, payload, is_new=False)
    session.commit()
    session.refresh(product)
    return _product_out(product)


@router.patch("/{sku}/management", summary="Update cost, automation directions and warehouse stock")
def manage_product(sku: str, payload: ProductManagementIn,
                   session: SessionDep, merchant: MerchantDep) -> ProductOut:
    product = _by_sku(session, merchant, sku)
    for name in payload.model_fields_set - {"availabilities"}:
        value = getattr(payload, name)
        if name == "category" and isinstance(value, str):
            value = value.strip() or None
        setattr(product, name, value)
    if payload.availabilities is not None:
        # Update existing points only: a typo must not silently remove a warehouse.
        by_store = {entry.store_id: entry for entry in product.availabilities}
        if any(entry.store_id not in by_store for entry in payload.availabilities):
            raise HTTPException(422, "Неизвестный склад. Сначала импортируйте его из XML магазина.")
        for entry in payload.availabilities:
            stock = by_store[entry.store_id]
            stock.available = entry.available
            stock.stock_count = entry.stock_count
            stock.preorder_days = entry.preorder_days
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
        is_new = product is None
        if product is None:
            product = Product(merchant_id=merchant.merchant_id, sku=item.sku)
            session.add(product)
            created += 1
        else:
            updated += 1
        _apply(product, item, is_new=is_new)

    session.commit()
    logger.info(
        "merchant={}: import added {} products, updated {}, rejected {}",
        merchant.merchant_id,
        created,
        updated,
        len(errors),
    )
    return ImportResult(created=created, updated=updated, errors=errors)


@router.post("/import-xml", summary="Preview or import the shop's Kaspi XML price list")
def import_kaspi_xml(
    content: Annotated[bytes, Body(media_type="application/xml")],
    session: SessionDep,
    merchant: MerchantDep,
    preview: bool = False,
    infer_card_ids: bool = False,
) -> XmlImportResult:
    try:
        catalog = parse_kaspi_xml(content, infer_card_ids_from_sku=infer_card_ids)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    if catalog.merchant_id != merchant.merchant_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "merchantid в XML не совпадает с ID магазина в настройках",
        )
    known = {
        product.sku: product
        for product in session.scalars(
            select(Product)
            .where(Product.merchant_id == merchant.merchant_id,
                   Product.sku.in_([offer.sku for offer in catalog.offers]))
            .options(selectinload(Product.availabilities), selectinload(Product.rules))
        )
    }
    created = len(catalog.offers) - len(known)
    unlinked = sum(
        not (offer.kaspi_product_id or known[offer.sku].kaspi_product_id)
        if offer.sku in known else not bool(offer.kaspi_product_id)
        for offer in catalog.offers
    )
    result = XmlImportResult(
        total=len(catalog.offers), created=created, updated=len(known),
        unlinked=unlinked,
        inferred_cards=sum(offer.inferred_card_id for offer in catalog.offers),
        preview=preview,
    )
    if preview:
        return result
    for offer in catalog.offers:
        product = known.get(offer.sku)
        if product is None:
            product = Product(merchant_id=merchant.merchant_id, sku=offer.sku,
                              kaspi_product_id="")
            session.add(product)
        _apply_xml_offer(product, offer)
    session.commit()
    return result


#: How many before/after rows the AlgaTop preview returns.
ALGATOP_PREVIEW_ROWS = 300


@router.post("/import-algatop", summary="Preview or import AlgaTop's product export")
def import_algatop(
    content: Annotated[bytes, Body(media_type="application/octet-stream")],
    session: SessionDep,
    merchant: MerchantDep,
    preview: bool = False,
) -> AlgaTopImportOut:
    """Carry every product over from AlgaTop exactly as it stands there.

    Per product and city: the price in the feed becomes AlgaTop's current
    price, and the min/max price, step, both directions, cost, status, stock
    and pre-order are taken over. Limits come in as tenge, so they no longer
    follow a percentage. Products missing from the catalogue are created when
    the shop has a single pickup point to put them in.

    Rows that cannot be read are reported and skipped; ``preview`` runs the
    whole import and then undoes it, so the merchant sees what would change.
    """
    try:
        rows, row_errors = parse_algatop(content)
    except AlgaTopFileError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    errors = [ImportError(sku=item.sku or f"строка {item.line}", reason=f"строка {item.line}: {item.reason}")
              for item in row_errors]
    if not rows:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "в файле нет ни одной строки товара")

    shop = load_settings(session)
    known = {
        product.sku: product
        for product in session.scalars(
            select(Product)
            .where(Product.merchant_id == merchant.merchant_id,
                   Product.sku.in_({row.sku for row in rows}))
            .options(selectinload(Product.availabilities), selectinload(Product.rules))
        )
    }
    stores = session.scalars(
        select(ProductAvailability.store_id)
        .join(Product, ProductAvailability.product_id == Product.id)
        .where(Product.merchant_id == merchant.merchant_id)
        .distinct()
    ).all()
    # A new product needs a pickup point to be in the feed; with one shop-wide
    # store there is no doubt which one it is.
    only_store = stores[0] if len(stores) == 1 else None

    savepoint = session.begin_nested()
    touched: set[str] = set()
    created: set[str] = set()
    not_found: list[str] = []
    changes: list[AlgaTopChangeOut] = []
    prices_changed = limits_changed = 0
    switched_off: list[str] = []
    switched_on: list[str] = []
    for row in rows:
        product = known.get(row.sku)
        if product is None and (only_store is None or not row.kaspi_product_id):
            not_found.append(row.sku)
            continue
        # Checked before anything is written: a refused row must not still
        # change the status, cost or price of the product, or create it.
        try:
            limits = _algatop_limits(product, row)
        except ValueError as exc:
            errors.append(ImportError(sku=row.sku, reason=f"строка {row.line}: {exc}"))
            continue
        if product is None:
            assert only_store is not None
            product = Product(merchant_id=merchant.merchant_id, sku=row.sku,
                              kaspi_product_id=row.kaspi_product_id, title=row.title or row.sku)
            product.availabilities.append(ProductAvailability(store_id=only_store))
            session.add(product)
            known[row.sku] = product
            created.add(row.sku)
        was_active = product.is_active if row.sku not in created else None
        change = _apply_algatop_row(product, row, shop, limits)
        touched.add(row.sku)
        if was_active is True and not product.is_active and row.sku not in switched_off:
            switched_off.append(row.sku)
        if was_active is False and product.is_active and row.sku not in switched_on:
            switched_on.append(row.sku)
        if change.price_before != change.price_after:
            prices_changed += 1
        if (change.min_before, change.max_before) != (change.min_after, change.max_after):
            limits_changed += 1
        if change.price_before != change.price_after or (
            (change.min_before, change.max_before) != (change.min_after, change.max_after)
        ):
            changes.append(change)

    session.flush()
    if preview:
        savepoint.rollback()
    else:
        savepoint.commit()
        session.commit()
        logger.info(
            "merchant={}: AlgaTop import updated {} products, created {}, {} not found, {} errors",
            merchant.merchant_id, len(touched - created), len(created), len(not_found), len(errors),
        )
    return AlgaTopImportOut(
        rows=len(rows),
        updated=len(touched - created),
        created=len(created & touched),
        not_found=not_found[:50],
        not_found_total=len(not_found),
        errors=errors[:100],
        prices_changed=prices_changed,
        limits_changed=limits_changed,
        switched_off=switched_off,
        switched_on=switched_on,
        changes=changes[:ALGATOP_PREVIEW_ROWS],
        changes_total=len(changes),
        preview=preview,
    )


def _algatop_limits(product: Product | None, row: AlgaTopRow) -> tuple[Decimal, Decimal]:
    """The row's min and max price; a side AlgaTop left empty keeps the rule's.

    Raises ValueError when the two cross.
    """
    rule = _rule_for_city(product, row.city_id)
    minimum = row.min_price or (rule.min_price if rule else row.price)
    maximum = row.max_price or (rule.max_price if rule else row.price)
    if minimum > maximum:
        raise ValueError(f"мин. цена {minimum} выше макс. {maximum}")
    return minimum, maximum


def _rule_for_city(product: Product | None, city_id: str) -> RepricerRule | None:
    if product is None:
        return None
    return next((rule for rule in product.rules if rule.city_id == city_id), None)


def _apply_algatop_row(
    product: Product, row: AlgaTopRow, shop: ShopSettings, limits: tuple[Decimal, Decimal]
) -> AlgaTopChangeOut:
    if row.kaspi_product_id:
        product.kaspi_product_id = row.kaspi_product_id
    if row.title and not product.title:
        product.title = row.title
    if row.published is not None:
        product.is_active = row.published
    if row.purchase_price is not None:
        product.purchase_price = row.purchase_price
    if row.auto_decrease is not None:
        product.auto_decrease = row.auto_decrease
    if row.auto_increase is not None:
        product.auto_increase = row.auto_increase
    # AlgaTop's percentages are taken from the current price, so that is the
    # own price here too; the feed's <price> follows it as well.
    product.base_price = row.price
    if len(product.availabilities) == 1:
        store = product.availabilities[0]
        # AlgaTop shows «Остаток: 0 шт.» for products it has on sale, so a zero
        # means "not tracked", not "sold out": it never takes a product off
        # sale here, and whether a pickup point sells stays as it is.
        if row.stock:
            store.stock_count = row.stock
        if row.preorder_days is not None:
            store.preorder_days = row.preorder_days or None

    rule = _rule_for_city(product, row.city_id)
    before = (rule.current_price, rule.min_price, rule.max_price) if rule else (None, None, None)
    minimum, maximum = limits
    if rule is None:
        shared = shop.global_strategy if row.city_id in shop.global_city_ids else None
        rule = RepricerRule(
            city_id=row.city_id,
            strategy=shared or PricingStrategy.MANUAL,
            min_price=minimum,
            max_price=maximum,
            step=row.step or shop.global_step,
            target_position=shop.global_target_position if shared else None,
        )
        product.rules.append(rule)
    rule.set_limits_in_tenge(min_price=minimum, max_price=maximum)
    # A limit AlgaTop gives is that figure in tenge, even when it matches the
    # one a percentage produced: kept as a percentage, it would be recalculated
    # from AlgaTop's (usually lower) price below and the floor would drop.
    if row.min_price is not None:
        rule.min_percent = None
    if row.max_price is not None:
        rule.max_percent = None
    if row.step is not None:
        rule.step = row.step
    rule.current_price = row.price
    # Limits AlgaTop left empty and other cities set as a percentage follow the
    # new own price.
    product.refresh_percent_limits()
    return AlgaTopChangeOut(
        sku=product.sku,
        title=product.title,
        city_id=row.city_id,
        price_before=before[0],
        price_after=row.price,
        min_before=before[1],
        min_after=rule.min_price,
        max_before=before[2],
        max_after=rule.max_price,
    )


def _apply_xml_offer(product: Product, offer: ImportedOffer) -> None:
    product.title = offer.title
    product.brand = offer.brand
    product.is_active = True
    if offer.kaspi_product_id:
        if product.kaspi_product_id != offer.kaspi_product_id:
            product.image_url = None
            product.image_checked_at = None
        product.kaspi_product_id = offer.kaspi_product_id
    if offer.base_price is not None and product.base_price is None:
        # Only a product without an own price takes the export's: for one the
        # bot already prices, the export holds the bot's lowered price, and
        # anchoring percent limits to it would walk the floor down every import.
        product.base_price = offer.base_price
        product.refresh_percent_limits()

    existing_stores = {entry.store_id: entry for entry in product.availabilities}
    wanted_stores = {entry.store_id: entry for entry in offer.availabilities}
    for store_id, wanted in wanted_stores.items():
        entry = existing_stores.get(store_id)
        if entry is None:
            entry = ProductAvailability(store_id=store_id)
            product.availabilities.append(entry)
        entry.available = wanted.available
        entry.stock_count = wanted.stock_count
        entry.preorder_days = wanted.preorder_days
    for store_id, entry in existing_stores.items():
        if store_id not in wanted_stores:
            product.availabilities.remove(entry)

    existing_rules = {rule.city_id: rule for rule in product.rules}
    for city_id, price in offer.city_prices.items():
        rule = existing_rules.get(city_id)
        if rule is None:
            product.rules.append(
                RepricerRule(city_id=city_id, strategy=PricingStrategy.MANUAL,
                             min_price=price, max_price=price, current_price=price)
            )
        elif rule.strategy is PricingStrategy.MANUAL:
            rule.min_price = price
            rule.max_price = price
            rule.current_price = price


def _apply(product: Product, payload: ProductIn, *, is_new: bool) -> None:
    product.title = payload.title
    if payload.kaspi_product_id or not product.kaspi_product_id:
        if product.kaspi_product_id != payload.kaspi_product_id:
            product.image_url = None
            product.image_checked_at = None
        product.kaspi_product_id = payload.kaspi_product_id
    # A price list without a brand or price column sends nulls; that means "not
    # given", not "erase": the own price anchors percent limits and the feed.
    if payload.brand is not None:
        product.brand = payload.brand
    if payload.base_price is not None:
        product.base_price = payload.base_price
    # Re-sending a price list must not put back on sale what the owner took off:
    # the switch only moves when the request actually says so.
    if is_new or "is_active" in payload.model_fields_set:
        product.is_active = payload.is_active
    # No stores at all means "not given" too, not "delete every store": a product
    # without one cannot be in the feed, so a re-import of prices alone would
    # otherwise take the whole catalogue off sale.
    if payload.availabilities:
        _replace_availabilities(product, payload.availabilities)

    _apply_rules(product, payload.rules)
    # A percent-based floor means "this far below my price", so it moves when
    # the merchant's own price does.
    product.refresh_percent_limits()


def _replace_availabilities(product: Product, availabilities: list[AvailabilityIn]) -> None:
    # The list is the whole truth about stock: what it omits is gone. Rows are
    # matched by store_id and updated in place, because clearing the list first
    # would re-insert the same (product, store) pair before the delete lands and
    # trip the unique index.
    current = {entry.store_id: entry for entry in product.availabilities}
    wanted = {entry.store_id: entry for entry in availabilities}
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
            rule.is_active = wanted.is_active
        elif "is_active" in wanted.model_fields_set:
            # A paused rule stays paused unless the row says otherwise.
            rule.is_active = wanted.is_active
        rule.strategy = wanted.strategy
        # Limits sent in tenge are exactly those limits, so a changed one drops
        # the percentage the rule carried; an unchanged one keeps it.
        rule.set_limits_in_tenge(min_price=wanted.min_price, max_price=wanted.max_price)
        rule.step = wanted.step
        rule.target_position = wanted.target_position


def _product_out(product: Product) -> ProductOut:
    return ProductOut(
        sku=product.sku,
        title=product.title,
        kaspi_product_id=product.kaspi_product_id,
        image_url=product.image_url,
        brand=product.brand,
        base_price=product.base_price,
        purchase_price=product.purchase_price,
        category=product.category,
        commission_percent=product.commission_percent,
        delivery_cost=product.delivery_cost,
        auto_decrease=product.auto_decrease,
        auto_increase=product.auto_increase,
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
    if not product.availabilities:
        return "нет складов с остатком"
    if product.base_price is None and not product.rules:
        return "нет цены и правил по городам"
    if product.base_price is None and all(rule.current_price is None for rule in product.rules):
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
