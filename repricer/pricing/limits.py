"""Price limits expressed as a percentage of the product's own price.

A merchant thinks in percentages ("never more than 10% below my price"), but the
engine, the stop-loss and the feed all work in tenge. So a percentage is stored
next to the absolute limits and the absolute limits stay the single source of
truth: whenever the base price changes, they are recomputed from it.

The anchor is deliberately the product's **base price** — the merchant's own
price — and never the price the repricer last published. Anchoring a floor to a
price the repricer itself keeps lowering would move the floor down with every
cycle, and the product would walk to zero one step at a time.

Pure module: no database, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

#: A floor of more than 90% off is almost certainly a typo, not a strategy.
MAX_DISCOUNT_PERCENT = Decimal(90)
#: A ceiling far above the own price is harmless, but keep it finite.
MAX_MARKUP_PERCENT = Decimal(500)
_ONE = Decimal(1)
_HUNDRED = Decimal(100)


@dataclass(frozen=True, slots=True)
class PriceLimits:
    """The pair the engine actually uses, in whole tenge."""

    min_price: Decimal
    max_price: Decimal


@dataclass(frozen=True, slots=True)
class PercentLimits:
    """How far below and above the base price the repricer may go.

    ``min_percent`` is a discount (10 → 10% below the base price) and
    ``max_percent`` a markup (5 → 5% above it). Both are percentages of the same
    base price, so a 100 000 ₸ product with 10/5 gets 90 000 – 105 000 ₸.

    Either side may be ``None``, which means that limit stays whatever tenge
    figure it already has: merchants routinely set a floor as a percentage and a
    ceiling by hand, or the other way round.
    """

    min_percent: Decimal | None = None
    max_percent: Decimal | None = None

    def __post_init__(self) -> None:
        for name, value, ceiling in (
            ("min_percent", self.min_percent, MAX_DISCOUNT_PERCENT),
            ("max_percent", self.max_percent, MAX_MARKUP_PERCENT),
        ):
            if value is None:
                continue
            if not isinstance(value, Decimal):
                raise TypeError(f"{name} must be a Decimal, got {type(value).__name__}")
            if not value.is_finite():
                raise ValueError(f"{name} must be a finite number, got {value}")
            if value < 0:
                raise ValueError(f"{name} must not be negative, got {value}")
            if value > ceiling:
                raise ValueError(f"{name} must not exceed {ceiling}%, got {value}")


def limits_from_percent(
    base_price: Decimal, percent: PercentLimits, current: PriceLimits | None = None
) -> PriceLimits:
    """Turn "10% down, 5% up" into the two absolute prices for that base price.

    The floor rounds up and the ceiling rounds down, so rounding can only make
    the allowed band narrower than what was asked for — never wider than the
    merchant's intent. The floor is at least 1 ₸, which the database requires.

    A side set to ``None`` keeps its figure from ``current``.
    """
    if not isinstance(base_price, Decimal):
        raise TypeError(f"base_price must be a Decimal, got {type(base_price).__name__}")
    if not base_price.is_finite() or base_price <= 0:
        raise ValueError(f"base_price must be a positive number, got {base_price}")
    if current is None and (percent.min_percent is None or percent.max_percent is None):
        raise ValueError("a limit left in tenge needs the current limits to keep")

    if percent.min_percent is None:
        assert current is not None
        floor = current.min_price
    else:
        minimum = base_price * (_ONE - percent.min_percent / _HUNDRED)
        floor = max(minimum.quantize(_ONE, rounding=ROUND_CEILING), _ONE)
    if percent.max_percent is None:
        assert current is not None
        ceiling = current.max_price
    else:
        maximum = base_price * (_ONE + percent.max_percent / _HUNDRED)
        ceiling = maximum.quantize(_ONE, rounding=ROUND_FLOOR)
    # The database refuses a ceiling below the floor, and so would the engine.
    return PriceLimits(min_price=floor, max_price=max(ceiling, floor))


def percent_from_limits(base_price: Decimal, limits: PriceLimits) -> PercentLimits:
    """The inverse, for showing absolute limits as percentages in the interface.

    Percentages outside the accepted range are clamped: an old absolute floor of
    1 ₸ on a 100 000 ₸ product is a 99.999% discount, which is not something the
    merchant would be allowed to type in.
    """
    if not base_price.is_finite() or base_price <= 0:
        raise ValueError(f"base_price must be a positive number, got {base_price}")
    discount = (_ONE - limits.min_price / base_price) * _HUNDRED
    markup = (limits.max_price / base_price - _ONE) * _HUNDRED
    return PercentLimits(
        min_percent=_clamp(discount, MAX_DISCOUNT_PERCENT),
        max_percent=_clamp(markup, MAX_MARKUP_PERCENT),
    )


def limits_from_current_price(
    price: Decimal, percent: PercentLimits, current: PriceLimits | None = None
) -> PriceLimits:
    """The same maths against a price the merchant is looking at right now.

    The bulk tools work this way ("set a floor 10% below the current price for
    everything"): the merchant sees today's price and means that one. The result
    is then stored as tenge plus percentages, and later recalculations use the
    product's base price, so the floor stays put as the repricer moves the price.
    """
    return limits_from_percent(price, percent, current)


def recalculated_limits(
    base_price: Decimal | None, percent: PercentLimits | None, current: PriceLimits
) -> PriceLimits:
    """The limits a rule should have after its product's base price changed.

    This is the function every write path calls: it decides nothing about the
    strategy, only about the two numbers. Rules that were set in tenge (no
    percentages) keep their limits, and so do rules whose product lost its base
    price, because guessing there would quietly move a stop-loss.
    """
    if percent is None or base_price is None or base_price <= 0:
        return current
    if percent.min_percent is None and percent.max_percent is None:
        return current
    return limits_from_percent(base_price, percent, current)


def _clamp(value: Decimal, ceiling: Decimal) -> Decimal:
    return min(max(value, Decimal(0)), ceiling).quantize(Decimal("0.01"))
