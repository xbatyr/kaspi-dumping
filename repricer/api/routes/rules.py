"""Rules: what the dashboard reads and edits."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from repricer.api.deps import MerchantDep, SessionDep
from repricer.api.security import ApiKeyGuard
from repricer.api.schemas import (
    AvailabilityIn,
    BulkToggleIn,
    BulkToggleOut,
    BulkRuleUpdateIn,
    ProductRulesOut,
    ProductRuleConfigurationIn,
    RuleCreate,
    RuleListOut,
    RuleOut,
    RuleStatusOut,
    RuleUpdate,
)
from repricer.db.models import PriceHistory, Product, RepricerRule
from repricer.db.queries import latest_changes
from repricer.db.settings_store import settings_or_none
from repricer.pricing import PricingStrategy
from repricer.uploader import MerchantIdentity

router = APIRouter(prefix="/api/rules", tags=["rules"], dependencies=[ApiKeyGuard])


@router.put("/product/{sku}/configuration", summary="Save a product's strategy for its cities atomically")
def configure_product_rules(
    sku: str, payload: ProductRuleConfigurationIn, session: SessionDep, merchant: MerchantDep
) -> list[RuleOut]:
    product = session.scalar(
        select(Product)
        .where(Product.merchant_id == merchant.merchant_id, Product.sku == sku)
        .options(selectinload(Product.rules), selectinload(Product.availabilities))
    )
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no product with sku {sku}")
    _require_card(product, payload.strategy)
    if payload.strategy is PricingStrategy.FIXED_PRICE and product.base_price is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "fixed_price needs a base price")

    wanted = set(payload.city_ids)
    by_city = {rule.city_id: rule for rule in product.rules}
    for city_id in payload.city_ids:
        rule = by_city.get(city_id)
        if rule is None:
            rule = RepricerRule(product_id=product.id, city_id=city_id)
            session.add(rule)
            by_city[city_id] = rule
        rule.strategy = payload.strategy
        rule.min_price = payload.min_price
        rule.max_price = payload.max_price
        rule.step = payload.step
        rule.target_position = payload.target_position
        rule.ignored_merchants = list(payload.ignored_merchants)
        rule.is_active = True
    for city_id, rule in by_city.items():
        if city_id not in wanted:
            rule.is_active = False
    session.commit()
    changes = latest_changes(session, [product.id])
    return [
        _rule_out(rule, changes.get((product.id, rule.city_id)))
        for rule in sorted(by_city.values(), key=lambda item: item.city_id)
    ]


@router.get("", summary="Products with their per-city rules and status")
def list_rules(
    session: SessionDep,
    merchant: MerchantDep,
    city_id: Annotated[str | None, Query(description="Only rules for this city.")] = None,
    strategy: Annotated[PricingStrategy | None, Query()] = None,
    is_active: Annotated[bool | None, Query(description="Filter rules by their switch.")] = None,
    search: Annotated[str | None, Query(description="Substring of the SKU or title.")] = None,
    bot: Literal["all", "enabled", "disabled", "unlinked"] = "all",
    sort: Literal["sku", "title"] = "sku",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RuleListOut:
    filters = [Product.merchant_id == merchant.merchant_id]
    enabled = Product.rules.any(RepricerRule.is_active & (RepricerRule.strategy != PricingStrategy.MANUAL))
    if bot == "enabled":
        filters.append(enabled)
    elif bot == "disabled":
        filters.append(~enabled)
    elif bot == "unlinked":
        filters.append(Product.kaspi_product_id == "")
    if search:
        pattern = f"%{search}%"
        filters.append(or_(Product.sku.ilike(pattern), Product.title.ilike(pattern)))
    rule_filters = []
    if city_id is not None:
        rule_filters.append(RepricerRule.city_id == city_id)
    if strategy is not None:
        rule_filters.append(RepricerRule.strategy == strategy)
    if is_active is not None:
        rule_filters.append(RepricerRule.is_active == is_active)
    if rule_filters:
        # Keep only products that have a rule matching the filters.
        filters.append(
            Product.id.in_(select(RepricerRule.product_id).where(*rule_filters))
        )

    total = session.scalar(select(func.count()).select_from(Product).where(*filters)) or 0
    products = session.scalars(
        select(Product)
        .where(*filters)
        .options(selectinload(Product.rules), selectinload(Product.availabilities))
        .order_by(Product.title if sort == "title" else Product.sku, Product.id)
        .limit(limit)
        .offset(offset)
    ).all()

    changes = latest_changes(session, [product.id for product in products])
    items = [
        ProductRulesOut(
            sku=product.sku,
            title=product.title,
            kaspi_product_id=product.kaspi_product_id,
            brand=product.brand,
            base_price=product.base_price,
            purchase_price=product.purchase_price,
            auto_decrease=product.auto_decrease,
            auto_increase=product.auto_increase,
            is_active=product.is_active,
            availabilities=[AvailabilityIn(store_id=a.store_id, available=a.available,
                stock_count=a.stock_count, preorder_days=a.preorder_days) for a in product.availabilities],
            rules=[
                _rule_out(rule, changes.get((rule.product_id, rule.city_id)))
                for rule in sorted(product.rules, key=lambda rule: rule.city_id)
                if _matches(rule, city_id, strategy, is_active)
            ],
        )
        for product in products
    ]
    return RuleListOut(items=items, total=total, limit=limit, offset=offset)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create a rule for one product in one city",
    responses={404: {"description": "Unknown SKU"}, 409: {"description": "City already has a rule"}},
)
def create_rule(payload: RuleCreate, session: SessionDep, merchant: MerchantDep) -> RuleOut:
    product = _product_by_sku(session, merchant, payload.product_sku)
    _require_card(product, payload.strategy)
    existing = session.scalar(
        select(RepricerRule).where(
            RepricerRule.product_id == product.id, RepricerRule.city_id == payload.city_id
        )
    )
    if existing is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{payload.product_sku} already has a rule for city {payload.city_id} (id {existing.id})",
        )
    if payload.strategy is PricingStrategy.FIXED_PRICE and product.base_price is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"the fixed_price strategy holds the product's base_price, which {product.sku} does not have",
        )

    rule = RepricerRule(
        product_id=product.id,
        city_id=payload.city_id,
        strategy=payload.strategy,
        min_price=payload.min_price,
        max_price=payload.max_price,
        step=payload.step,
        target_position=payload.target_position,
        ignored_merchants=list(payload.ignored_merchants),
        is_active=payload.is_active,
    )
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return _rule_out(rule, _latest_change(session, rule))


@router.put("/{rule_id}", summary="Replace a rule's settings", responses={404: {"description": "Unknown rule"}})
def update_rule(
    rule_id: int, payload: RuleUpdate, session: SessionDep, merchant: MerchantDep
) -> RuleOut:
    rule = _rule_by_id(session, merchant, rule_id)
    _require_card(rule.product, payload.strategy)
    if payload.strategy is PricingStrategy.FIXED_PRICE and rule.product.base_price is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"the fixed_price strategy holds the product's base_price, which {rule.product.sku} does not have",
        )
    rule.strategy = payload.strategy
    rule.min_price = payload.min_price
    rule.max_price = payload.max_price
    rule.step = payload.step
    rule.target_position = payload.target_position
    rule.ignored_merchants = list(payload.ignored_merchants)
    rule.is_active = payload.is_active
    session.commit()
    session.refresh(rule)
    return _rule_out(rule, _latest_change(session, rule))


@router.post("/bulk-toggle", summary="Switch the repricer on or off for many rules")
def bulk_toggle(payload: BulkToggleIn, session: SessionDep, merchant: MerchantDep) -> BulkToggleOut:
    statement = (
        select(RepricerRule)
        .join(Product, RepricerRule.product_id == Product.id)
        .where(Product.merchant_id == merchant.merchant_id)
    )
    if payload.rule_ids:
        statement = statement.where(RepricerRule.id.in_(payload.rule_ids))
    else:
        statement = statement.where(Product.sku.in_(payload.product_skus))
        if payload.city_id is not None:
            statement = statement.where(RepricerRule.city_id == payload.city_id)

    rules = session.scalars(statement).all()
    changed = [rule for rule in rules if rule.is_active != payload.is_active]
    for rule in changed:
        rule.is_active = payload.is_active
    session.commit()
    return BulkToggleOut(updated=len(changed), rule_ids=sorted(rule.id for rule in changed))


@router.post("/bulk-update", summary="Change settings of selected rules")
def bulk_update(
    payload: BulkRuleUpdateIn, session: SessionDep, merchant: MerchantDep
) -> BulkToggleOut:
    ids = set(payload.rule_ids)
    rules = session.scalars(
        select(RepricerRule)
        .join(Product, RepricerRule.product_id == Product.id)
        .where(RepricerRule.id.in_(ids), Product.merchant_id == merchant.merchant_id)
        .options(selectinload(RepricerRule.product))
    ).all()
    if len(rules) != len(ids):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "some selected rules were not found")
    global_settings = settings_or_none(session)
    if (global_settings is not None and global_settings.global_strategy is not None
            and payload.strategy is not None and payload.strategy is not global_settings.global_strategy):
        raise HTTPException(status.HTTP_409_CONFLICT, "стратегия общая для всех товаров; измените её во вкладке Стратегии")

    # Validate every rule before changing any of them, so a mixed selection
    # cannot leave only part of the catalogue with new limits.
    for rule in rules:
        strategy = payload.strategy or rule.strategy
        _require_card(rule.product, strategy)
        minimum = payload.min_price if payload.min_price is not None else rule.min_price
        maximum = payload.max_price if payload.max_price is not None else rule.max_price
        position = (
            payload.target_position
            if payload.target_position is not None
            else rule.target_position
        )
        if maximum < minimum:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"{rule.product.sku}: max_price must not be below min_price",
            )
        if strategy is PricingStrategy.FIXED_PRICE and rule.product.base_price is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"{rule.product.sku}: fixed_price needs a base price",
            )
        if strategy is PricingStrategy.TARGET_POSITION and position is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"{rule.product.sku}: target_position strategy needs a position",
            )

    for rule in rules:
        if payload.strategy is not None:
            rule.strategy = payload.strategy
            if payload.strategy is not PricingStrategy.TARGET_POSITION:
                rule.target_position = None
        if payload.min_price is not None:
            rule.min_price = payload.min_price
        if payload.max_price is not None:
            rule.max_price = payload.max_price
        if payload.step is not None:
            rule.step = payload.step
        if payload.target_position is not None and rule.strategy is PricingStrategy.TARGET_POSITION:
            rule.target_position = payload.target_position
        if payload.ignored_merchants is not None:
            rule.ignored_merchants = list(payload.ignored_merchants)
        if payload.is_active is not None:
            rule.is_active = payload.is_active
    if (global_settings is not None and global_settings.global_strategy is not None
            and (payload.min_price is not None or payload.max_price is not None)):
        from repricer.api.routes.strategy import _apply

        products = {rule.product_id: rule.product for rule in rules}
        for product in products.values():
            if not product.kaspi_product_id:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{product.sku}: сначала привяжите карточку Kaspi")
            selected = next(rule for rule in rules if rule.product_id == product.id)
            if selected.max_price <= selected.min_price:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "для демпинга Max должен быть выше Min")
            _apply(product, global_settings, selected.min_price, selected.max_price, selected.step)
            if payload.is_active is False:
                for rule in product.rules:
                    rule.is_active = False
    session.commit()
    return BulkToggleOut(updated=len(rules), rule_ids=sorted(ids))


def _rule_out(rule: RepricerRule, change: PriceHistory | None) -> RuleOut:
    out = RuleOut.model_validate(rule)
    if change is not None:
        out.last_change = RuleStatusOut(
            computed_price=change.new_price,
            expected_position=change.expected_position,
            competitor_top1_price=change.competitor_top1_price,
            competitor_top1_merchant_id=change.competitor_top1_merchant_id,
            strategy_used=change.strategy_used,
            reason=change.reason,
            changed_at=change.created_at,
        )
    return out


def _latest_change(session: Session, rule: RepricerRule) -> PriceHistory | None:
    return latest_changes(session, [rule.product_id]).get((rule.product_id, rule.city_id))


def _matches(
    rule: RepricerRule,
    city_id: str | None,
    strategy: PricingStrategy | None,
    is_active: bool | None,
) -> bool:
    return (
        (city_id is None or rule.city_id == city_id)
        and (strategy is None or rule.strategy is strategy)
        and (is_active is None or rule.is_active == is_active)
    )


def _product_by_sku(session: Session, merchant: MerchantIdentity, sku: str) -> Product:
    product = session.scalar(
        select(Product).where(Product.merchant_id == merchant.merchant_id, Product.sku == sku)
    )
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no product with sku {sku}")
    return product


def _require_card(product: Product, strategy: PricingStrategy) -> None:
    if not product.kaspi_product_id and strategy not in {
        PricingStrategy.MANUAL, PricingStrategy.FIXED_PRICE
    }:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"{product.sku}: привяжите ID карточки Kaspi перед включением демпинга",
        )


def _rule_by_id(session: Session, merchant: MerchantIdentity, rule_id: int) -> RepricerRule:
    rule = session.scalar(
        select(RepricerRule)
        .join(Product, RepricerRule.product_id == Product.id)
        .where(RepricerRule.id == rule_id, Product.merchant_id == merchant.merchant_id)
    )
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no rule with id {rule_id}")
    return rule
