import random
from decimal import Decimal

import pytest

from repricer.pricing import (
    CompetitorOffer,
    DecisionReason,
    PricingConfig,
    PricingEngine,
    PricingStrategy,
)

OWN = "own-store"
BEAT = PricingStrategy.BEAT_FIRST
MATCH = PricingStrategy.MATCH_FIRST
FOLLOW = PricingStrategy.FOLLOW_SECOND
TARGET = PricingStrategy.TARGET_POSITION
FIXED = PricingStrategy.FIXED_PRICE
MANUAL = PricingStrategy.MANUAL
#: The strategies that actually look at competitors.
COMPETITIVE = [BEAT, MATCH, FOLLOW, TARGET]

engine = PricingEngine()


def offer(merchant_id: str, price: int | str, rating: float | None = None) -> CompetitorOffer:
    return CompetitorOffer(merchant_id=merchant_id, price=Decimal(price), rating=rating)


def config(
    strategy: PricingStrategy = BEAT,
    min_price: int | str = 1000,
    max_price: int | str = 5000,
    step: int = 1,
    ignored: frozenset[str] = frozenset(),
    own_rating: float | None = None,
    target_position: int | None = None,
    base_price: int | str | None = None,
) -> PricingConfig:
    if target_position is None and strategy is TARGET:
        target_position = 1
    return PricingConfig(
        own_merchant_id=OWN,
        strategy=strategy,
        min_price=Decimal(min_price),
        max_price=Decimal(max_price),
        step=step,
        ignored_merchants=ignored,
        own_rating=own_rating,
        target_position=target_position,
        base_price=Decimal(base_price) if base_price is not None else None,
    )


# --- Beat First -------------------------------------------------------------


def test_beat_first_undercuts_leader_by_step() -> None:
    decision = engine.evaluate(config(), [offer("a", 2000), offer("b", 1500), offer("c", 1800)])

    assert decision.new_price == 1499
    assert decision.reason is DecisionReason.STRATEGY_TARGET
    assert decision.reference_offer == offer("b", 1500)
    assert decision.expected_position == 1


def test_beat_first_uses_configured_step() -> None:
    decision = engine.evaluate(config(step=10), [offer("a", 1500)])

    assert decision.new_price == 1490


def test_beat_first_raises_price_when_far_ahead_of_everyone() -> None:
    decision = engine.evaluate(config(), [offer("a", 3000)], current_price=Decimal(1200))

    assert decision.new_price == 2999
    assert decision.changed


def test_unchanged_price_is_reported_as_unchanged() -> None:
    decision = engine.evaluate(config(), [offer("a", 2000)], current_price=Decimal("1999.00"))

    assert decision.new_price == 1999
    assert not decision.changed


# --- Filtering --------------------------------------------------------------


def test_own_offer_is_never_treated_as_a_competitor() -> None:
    decision = engine.evaluate(config(), [offer(OWN, 1200), offer("a", 2000)])

    assert decision.new_price == 1999
    assert [o.merchant_id for o in decision.competitors] == ["a"]


def test_ignored_merchants_are_excluded() -> None:
    decision = engine.evaluate(
        config(ignored=frozenset({"sister"})), [offer("sister", 1100), offer("a", 2000)]
    )

    assert decision.new_price == 1999
    assert [o.merchant_id for o in decision.competitors] == ["a"]


def test_duplicate_listings_keep_the_cheapest_offer_per_store() -> None:
    decision = engine.evaluate(config(), [offer("a", 2500), offer("a", 2000), offer("b", 2200)])

    assert [(o.merchant_id, o.price) for o in decision.competitors] == [
        ("a", Decimal(2000)),
        ("b", Decimal(2200)),
    ]
    assert decision.new_price == 1999


@pytest.mark.parametrize("strategy", COMPETITIVE)
@pytest.mark.parametrize(
    "offers",
    [[], [offer(OWN, 1500)], [offer("sister", 1500)]],
    ids=["empty", "only-own", "only-ignored"],
)
def test_no_competitors_prices_at_max(
    strategy: PricingStrategy, offers: list[CompetitorOffer]
) -> None:
    decision = engine.evaluate(config(strategy, ignored=frozenset({"sister"})), offers)

    assert decision.new_price == 5000
    assert decision.reason is DecisionReason.NO_COMPETITORS
    assert decision.reference_offer is None
    assert decision.leader is None
    assert decision.expected_position == 1


def test_offer_order_does_not_matter() -> None:
    offers = [offer(f"m{i}", 1000 + 37 * i, rating=(i % 5) + 0.5) for i in range(30)]
    expected = engine.evaluate(config(MATCH, min_price=1500, own_rating=3.0), offers)

    shuffled = offers[:]
    random.Random(7).shuffle(shuffled)
    assert engine.evaluate(config(MATCH, min_price=1500, own_rating=3.0), shuffled) == expected


# --- Max price --------------------------------------------------------------


def test_target_above_max_is_capped() -> None:
    decision = engine.evaluate(config(max_price=3000), [offer("a", 5000)])

    assert decision.new_price == 3000
    assert decision.reason is DecisionReason.CAPPED_AT_MAX


# --- Match First ------------------------------------------------------------


def test_match_first_matches_leader_when_we_outrate_them() -> None:
    decision = engine.evaluate(config(MATCH, own_rating=4.9), [offer("a", 2000, rating=4.5)])

    assert decision.new_price == 2000
    assert decision.reason is DecisionReason.STRATEGY_TARGET
    assert decision.expected_position == 1


@pytest.mark.parametrize(
    ("own_rating", "leader_rating"),
    [(4.5, 4.5), (4.0, 4.5), (None, 4.5), (None, None)],
    ids=["equal", "lower", "own-unknown", "both-unknown"],
)
def test_match_first_undercuts_when_we_do_not_outrate_leader(
    own_rating: float | None, leader_rating: float | None
) -> None:
    decision = engine.evaluate(
        config(MATCH, own_rating=own_rating), [offer("a", 2000, rating=leader_rating)]
    )

    assert decision.new_price == 1999


def test_match_first_matches_an_unrated_leader() -> None:
    decision = engine.evaluate(config(MATCH, own_rating=3.0), [offer("a", 2000)])

    assert decision.new_price == 2000


def test_match_first_prefers_live_rating_from_own_offer() -> None:
    offers = [offer(OWN, 2100, rating=4.2), offer("a", 2000, rating=4.5)]

    decision = engine.evaluate(config(MATCH, own_rating=4.9), offers)

    assert decision.new_price == 1999


def test_match_first_falls_back_to_configured_rating_if_own_offer_is_unrated() -> None:
    offers = [offer(OWN, 2100), offer("a", 2000, rating=4.5)]

    decision = engine.evaluate(config(MATCH, own_rating=4.9), offers)

    assert decision.new_price == 2000


def test_match_first_compares_with_best_rated_store_at_leader_price() -> None:
    offers = [offer("weak", 2000, rating=3.0), offer("strong", 2000, rating=4.8)]

    decision = engine.evaluate(config(MATCH, own_rating=4.5), offers)

    assert decision.reference_offer == offer("strong", 2000, rating=4.8)
    assert decision.new_price == 1999


# --- Follow Second ----------------------------------------------------------


def test_follow_second_sits_just_above_leader() -> None:
    decision = engine.evaluate(config(FOLLOW), [offer("a", 2000), offer("b", 2500)])

    assert decision.new_price == 2001
    assert decision.reason is DecisionReason.STRATEGY_TARGET
    assert decision.expected_position == 2


def test_follow_second_is_capped_at_max() -> None:
    decision = engine.evaluate(config(FOLLOW, max_price=2000), [offer("a", 2000)])

    assert decision.new_price == 2000
    assert decision.reason is DecisionReason.CAPPED_AT_MAX


# --- Fallback: «Борьба за 2-20 место» ---------------------------------------


def test_fallback_undercuts_second_place_when_leader_is_below_min() -> None:
    offers = [offer("dumper", 800), offer("b", 1500), offer("c", 1700)]

    decision = engine.evaluate(config(), offers)

    assert decision.new_price == 1499
    assert decision.reason is DecisionReason.FALLBACK_POSITION
    assert decision.reference_offer == offer("b", 1500)
    assert decision.leader == offer("dumper", 800)
    assert decision.expected_position == 2


def test_fallback_triggers_when_undercut_lands_just_below_min() -> None:
    decision = engine.evaluate(config(min_price=1000), [offer("a", 1000), offer("b", 1300)])

    assert decision.new_price == 1299
    assert decision.reason is DecisionReason.FALLBACK_POSITION


def test_fallback_skips_positions_that_cannot_be_undercut() -> None:
    offers = [offer("a", 700), offer("b", 900), offer("c", 1000), offer("d", 1400)]

    decision = engine.evaluate(config(min_price=1000), offers)

    assert decision.new_price == 1399
    assert decision.reference_offer == offer("d", 1400)
    assert decision.expected_position == 4


def test_fallback_pins_to_min_when_no_position_is_affordable() -> None:
    offers = [offer("a", 700), offer("b", 900), offer("c", 1000)]

    decision = engine.evaluate(config(min_price=1000), offers)

    assert decision.new_price == 1000
    assert decision.reason is DecisionReason.PINNED_TO_MIN
    assert decision.reference_offer is None


def test_fallback_does_not_look_past_position_20() -> None:
    offers = [offer(f"cheap{i:02}", 500 + i) for i in range(20)] + [offer("21st", 3000)]

    decision = engine.evaluate(config(min_price=1000), offers)

    assert decision.new_price == 1000
    assert decision.reason is DecisionReason.PINNED_TO_MIN


def test_fallback_reaches_position_20_exactly() -> None:
    offers = [offer(f"cheap{i:02}", 500 + i) for i in range(19)] + [offer("20th", 3000)]

    decision = engine.evaluate(config(min_price=1000), offers)

    assert decision.new_price == 2999
    assert decision.expected_position == 20


def test_fallback_depth_is_configurable() -> None:
    offers = [offer("a", 500), offer("b", 600), offer("c", 2000)]

    decision = PricingEngine(fallback_max_position=2).evaluate(config(min_price=1000), offers)

    assert decision.reason is DecisionReason.PINNED_TO_MIN


def test_fallback_price_is_capped_at_max() -> None:
    decision = engine.evaluate(
        config(min_price=1000, max_price=1200), [offer("a", 800), offer("b", 1500)]
    )

    assert decision.new_price == 1200
    assert decision.reason is DecisionReason.CAPPED_AT_MAX
    assert decision.reference_offer == offer("b", 1500)


def test_follow_second_uses_fallback_when_leader_is_far_below_min() -> None:
    decision = engine.evaluate(config(FOLLOW), [offer("a", 500), offer("b", 1500)])

    assert decision.new_price == 1499
    assert decision.reason is DecisionReason.FALLBACK_POSITION


def test_match_first_fallback_matches_a_store_we_outrate() -> None:
    offers = [offer("dumper", 800), offer("b", 1000, rating=4.0), offer("c", 1500)]

    decision = engine.evaluate(config(MATCH, own_rating=4.8), offers)

    assert decision.new_price == 1000
    assert decision.reference_offer == offer("b", 1000, rating=4.0)
    assert decision.expected_position == 2


# --- Rounding and position --------------------------------------------------


def test_fractional_bounds_are_rounded_inwards() -> None:
    cfg = config(min_price="1000.40", max_price="2000.90")

    assert cfg.floor_price == 1001
    assert cfg.ceiling_price == 2000
    assert engine.evaluate(cfg, [offer("a", 500)]).new_price == 1001
    assert engine.evaluate(cfg, [offer("a", 9000)]).new_price == 2000


def test_undercut_of_fractional_competitor_price_rounds_down() -> None:
    decision = engine.evaluate(config(), [offer("a", "1500.50")])

    assert decision.new_price == 1499


def test_expected_position_puts_better_rated_store_ahead_on_tie() -> None:
    offers = [offer("a", 800), offer("b", 1000, rating=4.9), offer("c", 1000, rating=4.0)]

    decision = engine.evaluate(config(min_price=1000, own_rating=4.5), offers)

    assert decision.reason is DecisionReason.PINNED_TO_MIN
    assert decision.expected_position == 3  # behind a (cheaper) and b (better rated)


@pytest.mark.parametrize("strategy", COMPETITIVE)
def test_price_always_stays_within_bounds(strategy: PricingStrategy) -> None:
    rng = random.Random(f"bounds-{strategy}")
    for _ in range(2000):
        min_price = Decimal(rng.randint(1, 5000)) + Decimal(rng.randint(0, 99)) / 100
        cfg = PricingConfig(
            own_merchant_id=OWN,
            strategy=strategy,
            min_price=min_price,
            max_price=min_price + rng.randint(1, 5000),
            step=rng.randint(1, 50),
            ignored_merchants=frozenset({"m0", "m1"}),
            own_rating=rng.choice([None, 3.5, 4.8]),
            target_position=rng.randint(1, 20) if strategy is TARGET else None,
        )
        offers = [
            offer(
                rng.choice([OWN, *(f"m{i}" for i in range(30))]),
                rng.randint(1, 12000),
                rng.choice([None, round(rng.uniform(0, 5), 1)]),
            )
            for _ in range(rng.randint(0, 40))
        ]

        decision = engine.evaluate(cfg, offers)

        assert cfg.floor_price <= decision.new_price <= cfg.ceiling_price
        assert decision.new_price == decision.new_price.to_integral_value()
        assert {o.merchant_id for o in decision.competitors}.isdisjoint({OWN, "m0", "m1"})
        assert 1 <= decision.expected_position <= len(decision.competitors) + 1


# --- Validation -------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"min_price": 0}, ValueError),
        ({"min_price": 2000, "max_price": 1000}, ValueError),
        ({"min_price": "1000.20", "max_price": "1000.80"}, ValueError),
        ({"step": 0}, ValueError),
        ({"step": True}, ValueError),
        ({"own_rating": float("nan")}, ValueError),
    ],
)
def test_invalid_config_is_rejected(kwargs: dict[str, object], error: type[Exception]) -> None:
    params: dict[str, object] = {
        "own_merchant_id": OWN,
        "strategy": BEAT,
        "min_price": 1000,
        "max_price": 5000,
    } | kwargs
    with pytest.raises(error):
        PricingConfig(**params)  # type: ignore[arg-type]


def test_config_rejects_non_string_merchant_ids() -> None:
    with pytest.raises(TypeError):
        PricingConfig(
            own_merchant_id=123,  # type: ignore[arg-type]
            strategy=BEAT,
            min_price=Decimal(1000),
            max_price=Decimal(2000),
        )
    with pytest.raises(TypeError):
        config(ignored=frozenset({123}))  # type: ignore[arg-type]


def test_config_accepts_strategy_value_from_storage() -> None:
    cfg = PricingConfig(
        own_merchant_id=OWN,
        strategy="match_first",  # type: ignore[arg-type]
        min_price=Decimal(1000),
        max_price=Decimal(2000),
        ignored_merchants=["x", "x"],  # type: ignore[arg-type]
    )

    assert cfg.strategy is MATCH
    assert cfg.ignored_merchants == frozenset({"x"})


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"price": Decimal(0)}, ValueError),
        ({"price": Decimal(-5)}, ValueError),
        ({"price": Decimal("NaN")}, ValueError),
        ({"price": "abc"}, ValueError),
        ({"price": None}, TypeError),
        ({"merchant_id": ""}, ValueError),
        ({"rating": float("inf")}, ValueError),
    ],
)
def test_invalid_offer_is_rejected(kwargs: dict[str, object], error: type[Exception]) -> None:
    params: dict[str, object] = {"merchant_id": "a", "price": Decimal(100)} | kwargs
    with pytest.raises(error):
        CompetitorOffer(**params)  # type: ignore[arg-type]


def test_offer_price_from_scraped_float_is_exact() -> None:
    scraped = CompetitorOffer(merchant_id="a", price=12990.0)  # type: ignore[arg-type]

    assert scraped.price == Decimal("12990")


def test_engine_rejects_fallback_depth_below_two() -> None:
    with pytest.raises(ValueError):
        PricingEngine(fallback_max_position=1)


# --- Target position --------------------------------------------------------


def test_target_position_undercuts_whoever_holds_that_place() -> None:
    offers = [offer("a", 1500), offer("b", 1800), offer("c", 2000), offer("d", 2500)]

    decision = engine.evaluate(config(TARGET, target_position=3), offers)

    assert decision.new_price == 1999  # just ahead of the store in third place
    assert decision.reference_offer == offer("c", 2000)
    assert decision.expected_position == 3


def test_target_position_one_is_beat_first() -> None:
    offers = [offer("a", 1500), offer("b", 1800)]

    assert engine.evaluate(config(TARGET, target_position=1), offers).new_price == 1499


def test_target_position_deeper_than_the_market_takes_the_best_margin() -> None:
    decision = engine.evaluate(config(TARGET, target_position=5), [offer("a", 1500)])

    # Only one competitor, so any price we set already sits at position 2 or better.
    assert decision.new_price == 5000
    assert decision.reason is DecisionReason.STRATEGY_TARGET
    assert decision.reference_offer is None


def test_target_position_falls_back_to_a_place_further_down() -> None:
    offers = [offer("a", 800), offer("b", 900), offer("c", 1000), offer("d", 2500)]

    decision = engine.evaluate(config(TARGET, min_price=1500, target_position=2), offers)

    # Position 2 would need 899, below min_price, so it takes the next one it can.
    assert decision.new_price == 2499
    assert decision.reason is DecisionReason.FALLBACK_POSITION
    assert decision.reference_offer == offer("d", 2500)


def test_target_position_fallback_starts_below_the_target() -> None:
    offers = [offer("a", 2000), offer("b", 2200), offer("c", 2400)]

    decision = engine.evaluate(config(TARGET, min_price=2300, target_position=2), offers)

    # 2199 is under min_price, so the scan resumes at position 3, not position 2.
    assert decision.new_price == 2399
    assert decision.reference_offer == offer("c", 2400)


# --- Fixed price and manual -------------------------------------------------


def test_fixed_price_holds_the_base_price_whatever_competitors_do() -> None:
    decision = engine.evaluate(
        config(FIXED, base_price=3000), [offer("a", 1000), offer("b", 1100)]
    )

    assert decision.new_price == 3000
    assert decision.reason is DecisionReason.FIXED_PRICE
    assert decision.reference_offer is None


def test_fixed_price_needs_no_offers_at_all() -> None:
    assert engine.evaluate(config(FIXED, base_price=3000), []).new_price == 3000


def test_fixed_price_still_respects_the_bounds() -> None:
    assert engine.evaluate(config(FIXED, base_price=9000), []).new_price == 5000
    assert engine.evaluate(config(FIXED, base_price=500), []).new_price == 1000


def test_manual_rules_are_not_priced_by_the_engine() -> None:
    with pytest.raises(ValueError, match="priced by hand"):
        engine.evaluate(config(MANUAL), [offer("a", 2000)])


# --- Validation of the new settings -----------------------------------------


@pytest.mark.parametrize("position", [0, 21, -1, 2.5, True])
def test_target_position_outside_one_to_twenty_is_rejected(position: object) -> None:
    with pytest.raises(ValueError):
        config(BEAT, target_position=position)  # type: ignore[arg-type]


def test_target_position_strategy_needs_a_position() -> None:
    with pytest.raises(ValueError, match="needs a target_position"):
        PricingConfig(
            own_merchant_id=OWN,
            strategy=TARGET,
            min_price=Decimal(1000),
            max_price=Decimal(5000),
        )


def test_fixed_price_strategy_needs_a_base_price() -> None:
    with pytest.raises(ValueError, match="needs the product's base_price"):
        PricingConfig(
            own_merchant_id=OWN,
            strategy=FIXED,
            min_price=Decimal(1000),
            max_price=Decimal(5000),
        )


def test_base_price_must_be_positive() -> None:
    with pytest.raises(ValueError):
        config(FIXED, base_price=0)
