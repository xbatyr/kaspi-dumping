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
from dataclasses import dataclass
from decimal import Decimal

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from repricer.api.deps import MerchantDep, SessionDep
from repricer.api.routes.strategy import anchor_price, set_product_limits
from repricer.api.schemas import (
    BulkSaleIn,
    BulkSaleOut,
    BulkToolsIn,
    BulkToolsOut,
    MarginOut,
    MarginPreviewIn,
)
from repricer.api.security import ApiKeyGuard
from repricer.db.models import Product, ShopSettings
from repricer.db.settings_store import load_settings
from repricer.pricing import (
    InvertedLimitsError,
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
    # "Для товаров на продаже" narrows what gets new limits or a raised price.
    # Switching off lowering for what is *not* on sale still sees everything,
    # or the two options would cancel each other out.
    in_scope = [product for product in products if not payload.only_on_sale or product.on_sale()]

    skipped: Counter[str] = Counter()
    prices_raised = decrease_disabled = 0
    setting_limits = payload.set_min_percent is not None or payload.set_max_percent is not None

    # All or nothing: every product's new band is worked out before any of them
    # is touched, and a single inverted band refuses the whole selection. Half a
    # catalogue with new limits is worse than none, because the merchant can no
    # longer tell which products the percentages were applied to.
    plans: list[_LimitsPlan] = []
    if setting_limits:
        conflicts: list[str] = []
        for product in in_scope:
            try:
                plan = _plan_limits(product, settings, payload, skipped)
            except InvertedLimitsError as exc:
                conflicts.append(f"{product.sku} ({exc.min_price} > {exc.max_price})")
                continue
            if plan is not None:
                plans.append(plan)
        if conflicts:
            # Nothing has been written yet, and the request's session rolls the
            # read-only transaction back when it closes.
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"мин. цена окажется выше макс. у {len(conflicts)} товаров, ничего не изменено: "
                + ", ".join(conflicts[:10])
                + (" …" if len(conflicts) > 10 else ""),
            )

    for plan in plans:
        _apply_limits(plan, settings, payload)
    for product in in_scope:
        if payload.raise_to_max and _raise_to_max(product):
            prices_raised += 1
    for product in products:
        if payload.disable_decrease_when_off_sale and not product.on_sale():
            if product.auto_decrease:
                product.auto_decrease = False
                decrease_disabled += 1

    session.commit()
    return BulkToolsOut(
        products_seen=len(products),
        limits_set=len(plans),
        prices_raised=prices_raised,
        decrease_disabled=decrease_disabled,
        skipped=dict(skipped),
    )


@dataclass(frozen=True, slots=True)
class _LimitsPlan:
    """One product's new band, checked but not yet written."""

    product: Product
    limits: PriceLimits
    percent: PercentLimits
    step: int
    #: Which of the two sides this request actually sets for this product.
    sets_min: bool
    sets_max: bool


def _plan_limits(
    product: Product, settings: ShopSettings, payload: BulkToolsIn, skipped: Counter[str]
) -> _LimitsPlan | None:
    """A floor and ceiling a percentage away from this product's own price.

    A side the request leaves out ("Не менять") keeps what the rule has: its
    percentage if it was set as one, its tenge figure otherwise. That is also how
    the floor can end up above the ceiling, which raises InvertedLimitsError.
    """
    if not product.kaspi_product_id:
        skipped["нет карточки Kaspi"] += 1
        return None
    anchor = anchor_price(product)
    if anchor is None:
        skipped["нет своей цены"] += 1
        return None

    existing = next(
        (rule for rule in product.rules if rule.max_price > rule.min_price),
        product.rules[0] if product.rules else None,
    )
    # A product "already has" a limit once it has a real band, or the limit was
    # set as a percentage. An imported product sits at min = max = its price.
    has_min = any(rule.min_percent is not None or rule.max_price > rule.min_price for rule in product.rules)
    has_max = any(rule.max_percent is not None or rule.max_price > rule.min_price for rule in product.rules)
    new_min = payload.set_min_percent if payload.overwrite_min or not has_min else None
    new_max = payload.set_max_percent if payload.overwrite_max or not has_max else None
    if new_min is None and new_max is None:
        skipped["границы уже заданы"] += 1
        return None
    current = (
        PriceLimits(existing.min_price, existing.max_price)
        if existing
        else PriceLimits(anchor, anchor)
    )
    percent = PercentLimits(
        min_percent=(
            new_min if new_min is not None else (existing.min_percent if existing else None)
        ),
        max_percent=(
            new_max if new_max is not None else (existing.max_percent if existing else None)
        ),
    )
    limits = limits_from_percent(anchor, percent, current, strict=True)
    if limits.max_price == limits.min_price:
        # Not inverted, just no room to move: the bot would hold the price.
        skipped["границы совпали"] += 1
        return None
    step = existing.step if existing else settings.global_step
    return _LimitsPlan(
        product, limits, percent, step, sets_min=new_min is not None, sets_max=new_max is not None
    )


def _apply_limits(plan: _LimitsPlan, settings: ShopSettings, payload: BulkToolsIn) -> None:
    """Write a checked band. The percentage is stored on the rules, so the band
    keeps following the own price afterwards instead of freezing at today's figure.
    """
    set_product_limits(plan.product, settings, plan.limits, plan.step, plan.percent)
    # A floor only means something if the bot may go down to it, and the same
    # for the ceiling upwards: that is what these two tools are really for.
    if plan.sets_min:
        plan.product.auto_decrease = True
    if plan.sets_max:
        plan.product.auto_increase = True


@router.post("/sale", summary="Take many products off sale, or put them back")
def bulk_sale(payload: BulkSaleIn, session: SessionDep, merchant: MerchantDep) -> BulkSaleOut:
    """The selection bar's "Снять с продажи": one UPDATE for the whole selection.

    A product switched off leaves the next price list, which is how Kaspi takes
    an offer down; its rules and prices stay, so switching it back is lossless.
    """
    changed = session.scalars(
        update(Product)
        .where(
            Product.merchant_id == merchant.merchant_id,
            Product.sku.in_(payload.skus),
            Product.is_active != payload.is_active,
        )
        .values(is_active=payload.is_active)
        .returning(Product.id)
        .execution_options(synchronize_session=False)
    ).all()
    session.commit()
    return BulkSaleOut(updated=len(changed))


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
