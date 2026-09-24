"""Domain types for the pricing engine.

Everything under ``repricer.pricing`` is pure: no database, network or framework
imports. The engine takes plain values in and returns plain values out, so the
scraper, rule engine and persistence layers stay decoupled and the engine can be
called from Celery tasks, API handlers and back-tests alike.

Ranking model (the premise behind «Цена первого места»): Kaspi lists offers
cheapest first, and at equal price the better-rated store is listed first.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation
from enum import StrEnum

#: Kaspi only accepts whole-tenge prices.
PRICE_QUANTUM = Decimal("1")
#: Kaspi's offer list is only worth fighting over this deep.
MAX_TARGET_POSITION = 20


class PricingStrategy(StrEnum):
    #: «Стать первым»: undercut the cheapest competitor (P1) by ``step``.
    BEAT_FIRST = "beat_first"
    #: «Цена первого места»: match P1 when our rating is strictly higher than the
    #: leader's (we win the tie); otherwise undercut like BEAT_FIRST. The same
    #: match-if-we-out-rate rule applies to positions tried by the fallback.
    MATCH_FIRST = "match_first"
    #: «Прижиматься к первому месту»: sit at P1 + ``step`` to take 2nd place on
    #: store reputation without starting a price war with the leader.
    FOLLOW_SECOND = "follow_second"
    #: «Борьба за 2-20 место»: undercut whoever holds ``target_position`` now.
    #: With target_position 1 this is BEAT_FIRST.
    TARGET_POSITION = "target_position"
    #: Hold the product's ``base_price``; competitors are not consulted at all.
    FIXED_PRICE = "fixed_price"
    #: The price is managed by hand. The worker leaves these rules alone and the
    #: engine refuses to price them.
    MANUAL = "manual"


class DecisionReason(StrEnum):
    #: Our current XML price already ranks first; no new feed price is needed.
    ALREADY_FIRST = "already_first"
    #: The selected strategy's target was within [min_price, max_price].
    STRATEGY_TARGET = "strategy_target"
    #: The computed price was above max_price, so it was capped.
    CAPPED_AT_MAX = "capped_at_max"
    #: «Борьба за 2-20 место»: the strategy's target was below min_price, so the
    #: price takes the best position in 2..N reachable without breaching it.
    #: This fallback is automatic for every strategy, not a selectable one.
    FALLBACK_POSITION = "fallback_position"
    #: No position in 2..N was reachable either; the price sits at min_price.
    PINNED_TO_MIN = "pinned_to_min"
    #: No eligible competitors (none listed, or all ignored): price at max_price.
    NO_COMPETITORS = "no_competitors"
    #: FIXED_PRICE: the product's base price, whatever competitors are doing.
    FIXED_PRICE = "fixed_price"


@dataclass(frozen=True, slots=True)
class CompetitorOffer:
    """One store's offer on a Kaspi product card, for a single city."""

    merchant_id: str
    price: Decimal
    rating: float | None = None

    def __post_init__(self) -> None:
        _check_merchant_id(self.merchant_id, "merchant_id")
        price = _as_decimal(self.price, "price")
        if price <= 0:
            raise ValueError(f"price must be positive, got {price}")
        object.__setattr__(self, "price", price)
        _check_rating(self.rating, "rating")


@dataclass(frozen=True, slots=True)
class PricingConfig:
    """Repricing settings for one product in one city, detached from the database."""

    own_merchant_id: str
    strategy: PricingStrategy
    #: Stop-loss floor (cost + Kaspi commission + tax + fulfilment). Never breached.
    min_price: Decimal
    max_price: Decimal
    #: Undercut/follow step in whole tenge.
    step: int = 1
    #: Partner or sister stores that are never treated as competitors.
    ignored_merchants: frozenset[str] = frozenset()
    #: Used for MATCH_FIRST when our own offer is missing from the scraped list
    #: or has no rating there; the live rating from the list takes precedence.
    own_rating: float | None = None
    #: Position to fight for under TARGET_POSITION, 1..MAX_TARGET_POSITION.
    target_position: int | None = None
    #: The product's own price, required by FIXED_PRICE.
    base_price: Decimal | None = None

    def __post_init__(self) -> None:
        _check_merchant_id(self.own_merchant_id, "own_merchant_id")
        object.__setattr__(self, "strategy", PricingStrategy(self.strategy))

        min_price = _as_decimal(self.min_price, "min_price")
        max_price = _as_decimal(self.max_price, "max_price")
        if min_price <= 0:
            raise ValueError(f"min_price must be positive, got {min_price}")
        if max_price < min_price:
            raise ValueError(f"max_price ({max_price}) is below min_price ({min_price})")
        object.__setattr__(self, "min_price", min_price)
        object.__setattr__(self, "max_price", max_price)
        if self.floor_price > self.ceiling_price:
            raise ValueError(
                f"no whole-tenge price fits between min_price ({min_price}) and max_price ({max_price})"
            )

        if isinstance(self.step, bool) or not isinstance(self.step, int) or self.step < 1:
            raise ValueError(f"step must be a whole number of tenge >= 1, got {self.step!r}")

        ignored = frozenset(self.ignored_merchants)
        for merchant_id in ignored:
            _check_merchant_id(merchant_id, "ignored_merchants item")
        object.__setattr__(self, "ignored_merchants", ignored)

        _check_rating(self.own_rating, "own_rating")

        if self.target_position is not None and not (
            isinstance(self.target_position, int)
            and not isinstance(self.target_position, bool)
            and 1 <= self.target_position <= MAX_TARGET_POSITION
        ):
            raise ValueError(
                f"target_position must be between 1 and {MAX_TARGET_POSITION}, "
                f"got {self.target_position!r}"
            )
        if self.strategy is PricingStrategy.TARGET_POSITION and self.target_position is None:
            raise ValueError("strategy target_position needs a target_position")

        if self.base_price is not None:
            base_price = _as_decimal(self.base_price, "base_price")
            if base_price <= 0:
                raise ValueError(f"base_price must be positive, got {base_price}")
            object.__setattr__(self, "base_price", base_price)
        elif self.strategy is PricingStrategy.FIXED_PRICE:
            raise ValueError("strategy fixed_price needs the product's base_price")

    @property
    def floor_price(self) -> Decimal:
        """Lowest price the engine may set: ``min_price`` rounded up to whole tenge."""
        return self.min_price.quantize(PRICE_QUANTUM, rounding=ROUND_CEILING)

    @property
    def ceiling_price(self) -> Decimal:
        """Highest price the engine may set: ``max_price`` rounded down to whole tenge."""
        return self.max_price.quantize(PRICE_QUANTUM, rounding=ROUND_FLOOR)


@dataclass(frozen=True, slots=True)
class PricingDecision:
    """The engine's output: the price to set, plus enough context to log and audit it."""

    new_price: Decimal
    previous_price: Decimal | None
    strategy: PricingStrategy
    reason: DecisionReason
    #: 1-based rank at ``new_price`` under the ranking model in the module docstring.
    expected_position: int
    #: The competitor the price was derived from; None when pinned to min_price
    #: or when there are no competitors.
    reference_offer: CompetitorOffer | None
    #: Eligible competitors (own offer and ignored stores removed, one offer per
    #: store), in ranking order.
    competitors: tuple[CompetitorOffer, ...]

    @property
    def changed(self) -> bool:
        return self.previous_price != self.new_price

    @property
    def leader(self) -> CompetitorOffer | None:
        return self.competitors[0] if self.competitors else None


def _as_decimal(value: object, name: str) -> Decimal:
    """Normalise a money value to a finite Decimal.

    Fields are typed ``Decimal``, but scraped JSON yields ints and floats. Floats
    go through ``str`` so that 12990.0 stays exact.
    """
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int | float | str) and not isinstance(value, bool):
        try:
            result = Decimal(str(value))
        except InvalidOperation:
            raise ValueError(f"{name} is not a number: {value!r}") from None
    else:
        raise TypeError(f"{name} must be a Decimal, got {type(value).__name__}")
    if not result.is_finite():
        raise ValueError(f"{name} must be finite, got {value!r}")
    return result


def _check_merchant_id(value: object, name: str) -> None:
    # Strict on purpose: an int-vs-str mismatch would silently stop us excluding
    # our own offer, and the engine would undercut itself down to min_price.
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str, got {type(value).__name__}")
    if not value:
        raise ValueError(f"{name} must be non-empty")


def _check_rating(value: float | None, name: str) -> None:
    if value is not None and not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value!r}")
