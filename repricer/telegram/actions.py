"""What the commands actually do to the database.

Kept apart from the aiogram handlers: these are plain functions over a Session,
so they can be tested without a bot, a token or an event loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from html import escape

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from repricer.db.models import Product, RepricerRule
from repricer.scraper import Offer
from repricer.telegram.formatting import STRATEGY_NAMES, city, tenge

#: What the inline buttons under /sku shift min_price by.
MIN_PRICE_STEPS = (-5, -1, 1, 5)


def pause_all(session: Session, merchant_id: str) -> int:
    """Emergency stop: every rule of the store goes on pause. Returns how many."""
    rules = session.scalars(
        select(RepricerRule)
        .join(Product, RepricerRule.product_id == Product.id)
        .where(Product.merchant_id == merchant_id, RepricerRule.is_active)
    ).all()
    for rule in rules:
        rule.is_active = False
    session.commit()
    return len(rules)


def resume_all(session: Session, merchant_id: str) -> int:
    """The way back from /stop_all."""
    rules = session.scalars(
        select(RepricerRule)
        .join(Product, RepricerRule.product_id == Product.id)
        .where(Product.merchant_id == merchant_id, ~RepricerRule.is_active)
    ).all()
    for rule in rules:
        rule.is_active = True
    session.commit()
    return len(rules)


def find_product(session: Session, merchant_id: str, needle: str) -> Product | None:
    """Look a product up by SKU or by Kaspi product card ID, whichever was typed."""
    needle = needle.strip()
    if not needle:
        return None
    return session.scalar(
        select(Product)
        .where(
            Product.merchant_id == merchant_id,
            (Product.sku == needle) | (Product.kaspi_product_id == needle),
        )
        .options(selectinload(Product.rules))
    )


@dataclass(frozen=True, slots=True)
class MinPriceChange:
    sku: str
    city_id: str
    before: Decimal
    after: Decimal
    clamped: bool


def adjust_min_price(
    session: Session, merchant_id: str, rule_id: int, percent: int
) -> MinPriceChange | None:
    """Move a rule's stop-loss by a percentage, keeping it inside its own bounds."""
    rule = session.scalar(
        select(RepricerRule)
        .join(Product, RepricerRule.product_id == Product.id)
        .where(RepricerRule.id == rule_id, Product.merchant_id == merchant_id)
        .options(selectinload(RepricerRule.product))
    )
    if rule is None:
        return None

    before = rule.min_price
    target = (before * (100 + Decimal(percent)) / 100).quantize(
        Decimal(1), rounding=ROUND_HALF_UP
    )
    # min_price is a floor, not a wish: it may not pass max_price or reach zero.
    after = max(Decimal(1), min(target, rule.max_price))
    rule.min_price = after
    session.commit()
    return MinPriceChange(
        sku=rule.product.sku,
        city_id=rule.city_id,
        before=before,
        after=after,
        clamped=after != target,
    )


def render_offers(
    product: Product,
    rule: RepricerRule | None,
    offers: list[Offer],
    own_merchant_id: str,
    *,
    city_id: str,
    limit: int = 8,
) -> str:
    """The /sku answer: who sells this card in this city, and for how much."""
    lines = [
        f"🔎 <b>{escape(product.title)}</b>",
        f"SKU <code>{escape(product.sku)}</code> · {escape(city(city_id))}",
    ]
    if rule is not None:
        state = "активно" if rule.is_active else "на паузе"
        lines += [
            f"Стратегия: {escape(STRATEGY_NAMES.get(rule.strategy.value, rule.strategy.value))}"
            f" ({state})",
            f"Наша цена: <b>{tenge(rule.current_price)}</b> · "
            f"мин {tenge(rule.min_price)} / макс {tenge(rule.max_price)}",
        ]
    else:
        lines.append("<i>Правила для этого города нет: бот цену здесь не трогает.</i>")

    if not offers:
        lines.append("\nKaspi не вернул ни одного предложения по этой карточке.")
        return "\n".join(lines)

    lines.append("")
    for place, offer in enumerate(offers[:limit], start=1):
        mine = offer.merchant_id == own_merchant_id
        name = escape(offer.merchant_name or offer.merchant_id)
        rating = f" ★{offer.rating:.1f}" if offer.rating is not None else ""
        marker = "👉 " if mine else ""
        lines.append(f"{marker}{place}. {tenge(offer.price)} — {name}{rating}")
    if len(offers) > limit:
        lines.append(f"<i>…и ещё {len(offers) - limit} предложений</i>")
    if not any(offer.merchant_id == own_merchant_id for offer in offers):
        lines.append("\n⚠️ <i>Нашего предложения на карточке нет.</i>")
    return "\n".join(lines)


def render_min_price_change(change: MinPriceChange) -> str:
    text = (
        f"Мин. цена <b>{escape(change.sku)}</b> · {escape(city(change.city_id))}: "
        f"{tenge(change.before)} → <b>{tenge(change.after)}</b>"
    )
    if change.clamped:
        text += "\n<i>Значение упёрлось в границу правила.</i>"
    return text
