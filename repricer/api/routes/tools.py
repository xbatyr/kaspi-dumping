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
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from repricer.api.catalog_query import category_filter
from repricer.api.deps import MerchantDep, SessionDep
from repricer.api.routes.strategy import anchor_price, set_product_limits
from repricer.api.schemas import (
    BulkChangeOut,
    BulkSaleIn,
    BulkSaleOut,
    BulkToolsIn,
    BulkToolsOut,
    LimitSpec,
    MarginOut,
    MarginPreviewIn,
)
from repricer.api.security import ApiKeyGuard
from repricer.db.models import Product, RepricerRule, ShopSettings
from repricer.db.settings_store import load_settings
from repricer.pricing import (
    InvertedLimitsError,
    MarginInputs,
    PercentLimits,
    PriceLimits,
    break_even_price,
    calculate_margin,
)

router = APIRouter(prefix="/api/tools", tags=["tools"], dependencies=[ApiKeyGuard])


#: How many before/after rows the preview returns; the totals cover everything.
PREVIEW_ROWS = 200
_ONE = Decimal(1)
_HUNDRED = Decimal(100)


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
    if payload.rule_ids:
        statement = statement.where(
            Product.id.in_(
                select(RepricerRule.product_id).where(RepricerRule.id.in_(payload.rule_ids))
            )
        )
    if (category := category_filter(payload.category)) is not None:
        statement = statement.where(category)
    products = list(session.scalars(statement))
    if not products:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "товары не найдены")
    # "Для товаров на продаже" narrows what gets new limits or a raised price.
    # Switching off lowering for what is *not* on sale still sees everything,
    # or the two options would cancel each other out.
    in_scope = [product for product in products if not payload.only_on_sale or product.on_sale()]

    skipped: Counter[str] = Counter()
    prices_raised = decrease_disabled = steps_set = directions_set = 0

    # All or nothing: every product's new band is worked out before any of them
    # is touched, and a single inverted band refuses the whole selection. Half a
    # catalogue with new limits is worse than none, because the merchant can no
    # longer tell which products the new limits were applied to.
    plans: list[_LimitsPlan] = []
    if payload.min_limit is not None or payload.max_limit is not None:
        conflicts: list[str] = []
        for product in in_scope:
            try:
                plan = _plan_limits(product, settings, payload, skipped)
            except InvertedLimitsError as exc:
                # A kept side comes from a NUMERIC column: 110000.00 reads as 110000.
                low, high = (format(value.normalize(), "f") for value in (exc.min_price, exc.max_price))
                conflicts.append(f"{product.sku} ({low} > {high})")
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

    # Everything is written inside a savepoint, so a preview runs exactly the
    # same code as the real thing and is then simply undone.
    savepoint = session.begin_nested()
    for plan in plans:
        _apply_limits(plan, settings)
    for product in in_scope:
        if payload.step is not None and _set_step(product, payload.step):
            steps_set += 1
        if _set_directions(product, payload.auto_decrease, payload.auto_increase):
            directions_set += 1
        if payload.raise_to_max and _raise_to_max(product):
            prices_raised += 1
    for product in products:
        if payload.disable_decrease_when_off_sale and not product.on_sale():
            if product.auto_decrease:
                product.auto_decrease = False
                decrease_disabled += 1

    changes = [plan.change() for plan in plans if plan.moves]
    session.flush()
    if payload.dry_run:
        savepoint.rollback()
    else:
        savepoint.commit()
        session.commit()
    return BulkToolsOut(
        products_seen=len(products),
        limits_set=len(plans),
        prices_raised=prices_raised,
        decrease_disabled=decrease_disabled,
        steps_set=steps_set,
        directions_set=directions_set,
        skipped=dict(skipped),
        dry_run=payload.dry_run,
        changes=changes[:PREVIEW_ROWS],
        changes_total=len(changes),
    )


@dataclass(frozen=True, slots=True)
class _LimitsPlan:
    """One product's new band, checked but not yet written."""

    product: Product
    before: PriceLimits | None
    limits: PriceLimits
    percent: PercentLimits | None
    step: int
    #: Which of the two sides this request actually sets for this product.
    sets_min: bool
    sets_max: bool

    @property
    def moves(self) -> bool:
        return self.before != self.limits

    def change(self) -> BulkChangeOut:
        return BulkChangeOut(
            sku=self.product.sku,
            title=self.product.title,
            min_before=self.before.min_price if self.before else None,
            min_after=self.limits.min_price,
            max_before=self.before.max_price if self.before else None,
            max_after=self.limits.max_price,
        )


def _plan_limits(
    product: Product, settings: ShopSettings, payload: BulkToolsIn, skipped: Counter[str]
) -> _LimitsPlan | None:
    """This product's new floor and ceiling, each worked out the way it was asked.

    A side the request leaves out ("Не менять") keeps what the rule has: its
    percentage if it was set as one, its tenge figure otherwise. That is also how
    the floor can end up above the ceiling, which raises InvertedLimitsError.
    """
    if not product.kaspi_product_id:
        skipped["нет карточки Kaspi"] += 1
        return None

    existing = next(
        (rule for rule in product.rules if rule.max_price > rule.min_price),
        product.rules[0] if product.rules else None,
    )
    # A product "already has" a limit once it has a real band, or the limit was
    # set as a percentage. An imported product sits at min = max = its price.
    has_min = any(rule.min_percent is not None or rule.max_price > rule.min_price for rule in product.rules)
    has_max = any(rule.max_percent is not None or rule.max_price > rule.min_price for rule in product.rules)
    min_spec = payload.min_limit if payload.overwrite_min or not has_min else None
    max_spec = payload.max_limit if payload.overwrite_max or not has_max else None
    if min_spec is None and max_spec is None:
        skipped["границы уже заданы"] += 1
        return None

    anchor = anchor_price(product)
    before = PriceLimits(existing.min_price, existing.max_price) if existing else None
    # Without a rule yet, the side left alone starts at the own price.
    kept = before or (PriceLimits(anchor, anchor) if anchor is not None else None)

    floor = _side(min_spec, lower=True, product=product, settings=settings, anchor=anchor)
    ceiling = _side(max_spec, lower=False, product=product, settings=settings, anchor=anchor)
    for result in (floor, ceiling):
        if isinstance(result, str):
            skipped[result] += 1
            return None
    if (floor is None or ceiling is None) and kept is None:
        skipped["нет своей цены"] += 1
        return None
    assert not isinstance(floor, str) and not isinstance(ceiling, str)
    minimum = floor if floor is not None else kept.min_price  # type: ignore[union-attr]
    maximum = ceiling if ceiling is not None else kept.max_price  # type: ignore[union-attr]
    if maximum < minimum:
        raise InvertedLimitsError(minimum, maximum)
    if maximum == minimum:
        # Not inverted, just no room to move: the bot would hold the price.
        skipped["границы совпали"] += 1
        return None

    # Only a percentage of the own price is remembered and keeps following it;
    # a side set any other way is a tenge figure from now on.
    min_percent = (
        (min_spec.value if min_spec.mode == "percent" else None)
        if min_spec is not None
        else (existing.min_percent if existing else None)
    )
    max_percent = (
        (max_spec.value if max_spec.mode == "percent" else None)
        if max_spec is not None
        else (existing.max_percent if existing else None)
    )
    percent = (
        PercentLimits(min_percent=min_percent, max_percent=max_percent)
        if min_percent is not None or max_percent is not None
        else None
    )
    step = existing.step if existing else settings.global_step
    return _LimitsPlan(
        product,
        before,
        PriceLimits(minimum, maximum),
        percent,
        step,
        sets_min=min_spec is not None,
        sets_max=max_spec is not None,
    )


def _side(
    spec: LimitSpec | None,
    *,
    lower: bool,
    product: Product,
    settings: ShopSettings,
    anchor: Decimal | None,
) -> Decimal | str | None:
    """One side in whole tenge, None when it is left alone, or why it cannot be set.

    The floor rounds up and the ceiling down, so rounding only ever narrows the
    band the merchant asked for.
    """
    if spec is None:
        return None
    rounding = ROUND_CEILING if lower else ROUND_FLOOR
    sign = -1 if lower else 1
    match spec.mode:
        case "percent":
            if anchor is None:
                return "нет своей цены"
            value = anchor * (_ONE + sign * spec.value / _HUNDRED)
        case "tenge_offset":
            if anchor is None:
                return "нет своей цены"
            value = anchor + sign * spec.value
        case "fixed":
            value = spec.value
        case "cost_markup":
            if product.purchase_price is None:
                return "нет себестоимости"
            break_even = break_even_price(settings.margin_inputs(anchor or _ONE, product))
            if break_even is None:
                return "комиссия и налог съедают всю цену"
            value = break_even * (_ONE + spec.value / _HUNDRED)
    return max(value.quantize(_ONE, rounding=rounding), _ONE)


def _apply_limits(plan: _LimitsPlan, settings: ShopSettings) -> None:
    """Write a checked band. A percentage is stored on the rules, so that side
    keeps following the own price afterwards instead of freezing at today's figure.
    """
    set_product_limits(plan.product, settings, plan.limits, plan.step, plan.percent)
    # A floor only means something if the bot may go down to it, and the same
    # for the ceiling upwards: that is what these two tools are really for.
    if plan.sets_min:
        plan.product.auto_decrease = True
    if plan.sets_max:
        plan.product.auto_increase = True


def _set_step(product: Product, step: int) -> bool:
    changed = [rule for rule in product.rules if rule.step != step]
    for rule in changed:
        rule.step = step
    return bool(changed)


def _set_directions(product: Product, decrease: bool | None, increase: bool | None) -> bool:
    """The explicit «Автоснижение» / «Автоповышение» switches win over the ones
    a new floor or ceiling turned on."""
    changed = False
    if decrease is not None and product.auto_decrease != decrease:
        product.auto_decrease = decrease
        changed = True
    if increase is not None and product.auto_increase != increase:
        product.auto_increase = increase
        changed = True
    return changed


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
        # Kaspi takes whole tenge, and a ceiling may have been typed with tiyn.
        ceiling = rule.max_price.quantize(_ONE, rounding=ROUND_FLOOR)
        if rule.current_price is None or rule.current_price < ceiling:
            rule.request_price(ceiling)
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
