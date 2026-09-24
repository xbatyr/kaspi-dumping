"""Rules: what the dashboard reads and edits."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from repricer.api.deps import MerchantDep, SessionDep
from repricer.api.security import ApiKeyGuard
from repricer.api.schemas import (
    BulkToggleIn,
    BulkToggleOut,
    ProductRulesOut,
    RuleCreate,
    RuleListOut,
    RuleOut,
    RuleStatusOut,
    RuleUpdate,
)
from repricer.db.models import PriceHistory, Product, RepricerRule
from repricer.db.queries import latest_changes
from repricer.pricing import PricingStrategy
from repricer.uploader import MerchantIdentity

router = APIRouter(prefix="/api/rules", tags=["rules"], dependencies=[ApiKeyGuard])


@router.get("", summary="Products with their per-city rules and status")
def list_rules(
    session: SessionDep,
    merchant: MerchantDep,
    city_id: Annotated[str | None, Query(description="Only rules for this city.")] = None,
    strategy: Annotated[PricingStrategy | None, Query()] = None,
    is_active: Annotated[bool | None, Query(description="Filter rules by their switch.")] = None,
    search: Annotated[str | None, Query(description="Substring of the SKU or title.")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RuleListOut:
    filters = [Product.merchant_id == merchant.merchant_id]
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
        .options(selectinload(Product.rules))
        .order_by(Product.sku)
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
            is_active=product.is_active,
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


def _rule_by_id(session: Session, merchant: MerchantIdentity, rule_id: int) -> RepricerRule:
    rule = session.scalar(
        select(RepricerRule)
        .join(Product, RepricerRule.product_id == Product.id)
        .where(RepricerRule.id == rule_id, Product.merchant_id == merchant.merchant_id)
    )
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no rule with id {rule_id}")
    return rule
