"""Filtering, sorting and margins for the catalogue screens.

The product list is the screen a merchant spends the day on, so the menu above it
has to answer four questions at once: what is on sale, what kind of product is
it, is the bot working on it, and in what order do I want to see it.

All of that is one SQL query. The predicates here are plain column expressions
that compose with ``and_``, so the count and the page are filtered identically
and no product is ever fetched to be thrown away in Python. The indexes that
make it cheap are ``(merchant_id, category)`` and ``(merchant_id, is_active)``.

Margins live here too, because "sort by margin" and "show the margin" have to
agree: both go through the shop's tax, commission and delivery settings, with
the product's own figures taking precedence.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, Numeric, cast, func, or_
from sqlalchemy.sql.elements import UnaryExpression

from repricer.api.schemas import (
    NO_CATEGORY,
    MarginOut,
    ProductMarginsOut,
    ProductSort,
    SaleFilter,
)
from repricer.db.models import Product, ProductAvailability, RepricerRule, ShopSettings
from repricer.pricing import PricingStrategy, calculate_margin

_HUNDRED = Decimal(100)


def in_stock_somewhere() -> ColumnElement[bool]:
    """At least one pickup point that can actually ship the product."""
    return Product.availabilities.any(
        ProductAvailability.available
        & (
            ProductAvailability.stock_count.is_(None)
            | (ProductAvailability.stock_count > 0)
        )
    )


def on_sale() -> ColumnElement[bool]:
    """Kaspi shows the offer: the product is switched on and in stock."""
    return Product.is_active & in_stock_somewhere()


def sale_filter(value: SaleFilter) -> ColumnElement[bool] | None:
    if value == "on":
        return on_sale()
    if value == "off":
        return ~on_sale()
    return None


def category_filter(value: str | None) -> ColumnElement[bool] | None:
    """``__none__`` selects the products that have no category yet."""
    if not value:
        return None
    if value == NO_CATEGORY:
        return Product.category.is_(None)
    return Product.category == value


def bot_filter(value: str) -> ColumnElement[bool] | None:
    working = Product.rules.any(
        RepricerRule.is_active & (RepricerRule.strategy != PricingStrategy.MANUAL)
    )
    if value == "enabled":
        return working
    if value == "disabled":
        return ~working
    if value == "unlinked":
        return Product.kaspi_product_id == ""
    return None


def search_filter(value: str | None) -> ColumnElement[bool] | None:
    if not value:
        return None
    pattern = f"%{value.strip()}%"
    return or_(Product.sku.ilike(pattern), Product.title.ilike(pattern))


def profit_expression(shop: ShopSettings) -> ColumnElement[Decimal | None]:
    """Profit at the product's own price, as SQL.

    The same arithmetic as :func:`repricer.pricing.calculate_margin`, written as
    an expression so the database can order thousands of products by it. Products
    without a price or a purchase price sort last, which ``nulls_last`` below
    takes care of.
    """
    commission = func.coalesce(Product.commission_percent, shop.commission_percent)
    delivery = func.coalesce(Product.delivery_cost, shop.delivery_cost)
    kept = cast(1, Numeric(12, 4)) - (commission + shop.tax_percent) / _HUNDRED
    return Product.base_price * kept - delivery - Product.purchase_price


def order_by(sort: ProductSort, shop: ShopSettings) -> list[UnaryExpression[Any]]:
    """The ORDER BY for one of the catalogue's sort options.

    Every option ends with the SKU, so paging is stable when products tie.
    """
    first: list[UnaryExpression[Any]]
    match sort:
        case "title":
            first = [Product.title.asc()]
        case "price_asc":
            first = [Product.base_price.asc().nulls_last()]
        case "price_desc":
            first = [Product.base_price.desc().nulls_last()]
        case "margin_asc":
            # Loss-makers first: the reason a merchant sorts by margin at all.
            first = [profit_expression(shop).asc().nulls_last()]
        case "margin_desc":
            first = [profit_expression(shop).desc().nulls_last()]
        case "updated":
            first = [Product.updated_at.desc()]
        case _:
            first = [Product.sku.asc()]
    return [*first, Product.sku.asc(), Product.id.asc()]


def product_margins(
    shop: ShopSettings, product: Product, rule: RepricerRule | None
) -> ProductMarginsOut:
    """Profit at the price in the feed and at the two limits of one city.

    Without a rule there is only the product's own price to judge.
    """
    current = rule.current_price if rule and rule.current_price else product.base_price
    return ProductMarginsOut(
        current=_margin(shop, product, current),
        minimum=_margin(shop, product, rule.min_price if rule else None),
        maximum=_margin(shop, product, rule.max_price if rule else None),
    )


def _margin(shop: ShopSettings, product: Product, price: Decimal | None) -> MarginOut | None:
    if price is None or price <= 0:
        return None
    return MarginOut.of(calculate_margin(shop.margin_inputs(price, product)))
