"""Competitor-driven price calculation for one product in one city."""

from __future__ import annotations

import math
from collections.abc import Iterable
from decimal import ROUND_FLOOR, Decimal
from typing import assert_never

from repricer.pricing.domain import (
    PRICE_QUANTUM,
    CompetitorOffer,
    DecisionReason,
    PricingConfig,
    PricingDecision,
    PricingStrategy,
)

class PricingEngine:
    """Computes the price for one product in one city from a snapshot of competitor offers.

    Stateless and free of I/O, so one instance can be shared across threads and
    Celery workers.

    The rules are AlgaTop's: the price only moves to take or keep first place,
    and moves no further than that takes. Evaluation:
      1. Drop our own offer (taking our live rating from it), ignored merchants
         and duplicate listings of the same store; rank the rest.
      2. Keep the current price when it already ranks first and our offer is
         visible (unless raising towards the next store is allowed).
      3. No competitors left: keep the current price. There is nothing to
         follow, so there is no reason to jump to ``max_price``.
      4. Apply the selected strategy against the leader (position 1).
      5. If that lands below ``min_price``, first place is out of reach («не
         хватает мин. цены»): keep the current price. Dropping to the floor
         would cost margin and still not win, and chasing a lower place would
         swing the price for nothing. With raising allowed, the price moves up
         to just under the store behind us, which keeps our place and wins
         back margin; with nobody behind (two sellers), it stays put.
      6. Cap at ``max_price``; a current price outside the bounds is brought
         back inside them.

    The result is always a whole-tenge price within [min_price, max_price].
    ``current_price`` should be the price shoppers see on Kaspi now, which is
    what AlgaTop works from.
    """

    def evaluate(
        self,
        config: PricingConfig,
        offers: Iterable[CompetitorOffer],
        current_price: Decimal | None = None,
    ) -> PricingDecision:
        """Return the price to set.

        ``offers`` is every offer on the product card for the rule's city, and may
        include our own. ``current_price`` is only used to report whether the
        price changed.
        """
        offers = tuple(offers)
        competitors, own_rating = _rank_competitors(config, offers)

        def hold() -> Decimal:
            """The current price, kept inside the bounds. A rule priced for the
            first time starts from the product's own price."""
            if current_price is not None:
                price = _whole_tenge(current_price)
            elif config.base_price is not None:
                price = _whole_tenge(config.base_price)
            else:
                price = config.ceiling_price
            return min(max(price, config.floor_price), config.ceiling_price)

        def decide(
            price: Decimal, reason: DecisionReason, reference: CompetitorOffer | None
        ) -> PricingDecision:
            if price > config.ceiling_price:
                price, reason = config.ceiling_price, DecisionReason.CAPPED_AT_MAX
            # Explicit bounds take priority if the old price is outside them.
            if (current_price is not None
                and config.floor_price <= current_price <= config.ceiling_price
                and config.strategy is not PricingStrategy.FIXED_PRICE
                and ((price < current_price and not config.auto_decrease)
                     or (price > current_price and not config.auto_increase))):
                price, reason = current_price, DecisionReason.DIRECTION_DISABLED
            return PricingDecision(
                new_price=price,
                previous_price=current_price,
                strategy=config.strategy,
                reason=reason,
                expected_position=_expected_position(price, competitors, own_rating),
                reference_offer=reference,
                competitors=competitors,
            )

        if config.strategy is PricingStrategy.MANUAL:
            raise ValueError(
                "manual rules are priced by hand; the worker must leave them alone"
            )
        if config.strategy is PricingStrategy.FIXED_PRICE:
            # Competitors are irrelevant here, so this runs even with no offers.
            base_price = _whole_tenge(config.base_price or config.ceiling_price)
            if base_price < config.floor_price:
                return decide(config.floor_price, DecisionReason.PINNED_TO_MIN, None)
            return decide(base_price, DecisionReason.FIXED_PRICE, None)

        # The XML feed can be ahead of Kaspi's displayed price while Kaspi has
        # not fetched it yet. Once the feed price would rank first, leave it
        # alone instead of chasing a rival's small moves every cycle.
        if (
            current_price is not None
            and not config.raise_when_first
            and any(offer.merchant_id == config.own_merchant_id for offer in offers)
            and config.floor_price <= current_price <= config.ceiling_price
            and _expected_position(current_price, competitors, own_rating) == 1
        ):
            return decide(current_price, DecisionReason.ALREADY_FIRST, None)

        if not competitors:
            return decide(hold(), DecisionReason.NO_COMPETITORS, None)

        target, reference = _strategy_target(config, competitors, own_rating)
        if target is None:
            # Fewer stores than the place we aim for: we hold it at any price.
            return decide(hold(), DecisionReason.STRATEGY_TARGET, None)
        if target >= config.floor_price:
            return decide(target, DecisionReason.STRATEGY_TARGET, reference)
        # The store to beat sells below our floor. Stay where we are, or with
        # raising allowed sit just under whoever comes after us.
        held = hold()
        # A store level with us that we out-rate is already behind us, and
        # moving past it would give our place away.
        behind = next(
            (
                offer
                for offer in competitors
                if offer.price > held or (offer.price == held and _outrates(own_rating, offer.rating))
            ),
            None,
        )
        if config.auto_increase and behind is not None:
            pegged = min(max(_price_to_get_ahead(config, behind, own_rating), held), config.ceiling_price)
            return decide(pegged, DecisionReason.MIN_PRICE_SHORT, behind)
        return decide(held, DecisionReason.MIN_PRICE_SHORT, reference)


def _rank_competitors(
    config: PricingConfig, offers: Iterable[CompetitorOffer]
) -> tuple[tuple[CompetitorOffer, ...], float | None]:
    own_rating = config.own_rating
    cheapest_by_merchant: dict[str, CompetitorOffer] = {}
    for offer in offers:
        if offer.merchant_id == config.own_merchant_id:
            # Never compete with ourselves. The scraped rating comes from the same
            # source as the competitors' ratings, so it wins over the configured one.
            if offer.rating is not None:
                own_rating = offer.rating
            continue
        if offer.merchant_id in config.ignored_merchants:
            continue
        seen = cheapest_by_merchant.get(offer.merchant_id)
        if seen is None or offer.price < seen.price:
            cheapest_by_merchant[offer.merchant_id] = offer
    return tuple(sorted(cheapest_by_merchant.values(), key=_rank_key)), own_rating


def _rank_key(offer: CompetitorOffer) -> tuple[Decimal, float, str]:
    # Cheapest first; at equal price the better-rated store leads (unrated last),
    # so MATCH_FIRST is always compared with the strongest store at that price.
    # merchant_id keeps the order deterministic.
    rating_key = -offer.rating if offer.rating is not None else math.inf
    return offer.price, rating_key, offer.merchant_id


def _strategy_target(
    config: PricingConfig, competitors: tuple[CompetitorOffer, ...], own_rating: float | None
) -> tuple[Decimal | None, CompetitorOffer | None]:
    """The price the strategy aims at, and the competitor it was derived from.

    None when the strategy has no store to price against.
    """
    leader = competitors[0]
    match config.strategy:
        case PricingStrategy.BEAT_FIRST | PricingStrategy.MATCH_FIRST:
            return _price_to_get_ahead(config, leader, own_rating), leader
        case PricingStrategy.FOLLOW_SECOND:
            return _whole_tenge(leader.price + config.step), leader
        case PricingStrategy.TARGET_POSITION:
            index = (config.target_position or 1) - 1
            if index >= len(competitors):
                # Fewer competitors than the target position: whatever we charge
                # already lands at or above it.
                return None, None
            occupant = competitors[index]
            return _price_to_get_ahead(config, occupant, own_rating), occupant
        case PricingStrategy.FIXED_PRICE | PricingStrategy.MANUAL:
            raise AssertionError(f"{config.strategy} is handled before competitors are ranked")
        case _:
            assert_never(config.strategy)


def _price_to_get_ahead(
    config: PricingConfig, offer: CompetitorOffer, own_rating: float | None
) -> Decimal:
    """Price that ranks us directly ahead of ``offer``.

    Under MATCH_FIRST, matching is enough when we out-rate the store; in every
    other case we undercut by ``step``.
    """
    if config.strategy is PricingStrategy.MATCH_FIRST and _outrates(own_rating, offer.rating):
        return _whole_tenge(offer.price)
    return _whole_tenge(offer.price - config.step)


def _expected_position(
    price: Decimal, competitors: tuple[CompetitorOffer, ...], own_rating: float | None
) -> int:
    ahead_of_us = sum(
        1
        for offer in competitors
        if offer.price < price or (offer.price == price and not _outrates(own_rating, offer.rating))
    )
    return ahead_of_us + 1


def _outrates(own: float | None, other: float | None) -> bool:
    """True when our rating is strictly higher. An unknown own rating never wins."""
    if own is None:
        return False
    return other is None or own > other


def _whole_tenge(value: Decimal) -> Decimal:
    # Rounding down keeps an undercut strictly below a fractional competitor price.
    return value.quantize(PRICE_QUANTUM, rounding=ROUND_FLOOR)
