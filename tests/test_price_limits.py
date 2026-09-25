from decimal import Decimal

import pytest

from repricer.pricing import (
    PercentLimits,
    PriceLimits,
    limits_from_percent,
    percent_from_limits,
    recalculated_limits,
)


def percent(down: str, up: str = "0") -> PercentLimits:
    return PercentLimits(min_percent=Decimal(down), max_percent=Decimal(up))


# --- Percentages into tenge ---------------------------------------------------


def test_the_example_from_the_brief() -> None:
    # 100 000 with a 10% floor becomes 90 000.
    limits = limits_from_percent(Decimal(100_000), percent("10"))

    assert limits.min_price == Decimal(90_000)


def test_ceiling_is_a_markup_above_the_base_price() -> None:
    limits = limits_from_percent(Decimal(100_000), percent("10", "5"))

    assert (limits.min_price, limits.max_price) == (Decimal(90_000), Decimal(105_000))


def test_zero_percent_pins_both_limits_to_the_base_price() -> None:
    assert limits_from_percent(Decimal(362_000), percent("0")) == PriceLimits(
        Decimal(362_000), Decimal(362_000)
    )


def test_floor_rounds_up_and_ceiling_rounds_down() -> None:
    # 9999 − 7% = 9299.07 and 9999 + 7% = 10698.93: rounding may only narrow
    # the band, never widen it past what the merchant asked for.
    limits = limits_from_percent(Decimal(9_999), percent("7", "7"))

    assert (limits.min_price, limits.max_price) == (Decimal(9_300), Decimal(10_698))


def test_floor_never_drops_below_one_tenge() -> None:
    assert limits_from_percent(Decimal(5), percent("90")).min_price == Decimal(1)


@pytest.mark.parametrize("base", [Decimal(0), Decimal(-100), Decimal("NaN")])
def test_base_price_must_be_a_positive_number(base: Decimal) -> None:
    with pytest.raises(ValueError):
        limits_from_percent(base, percent("10"))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"min_percent": Decimal(-1)},
        {"min_percent": Decimal(91)},
        {"max_percent": Decimal(-1)},
        {"max_percent": Decimal(501)},
        {"min_percent": Decimal("NaN")},
    ],
)
def test_percentages_outside_the_accepted_range_are_refused(kwargs: dict[str, Decimal]) -> None:
    with pytest.raises(ValueError):
        PercentLimits(**{"min_percent": Decimal(10), "max_percent": Decimal(10), **kwargs})


def test_percentages_must_be_decimals() -> None:
    with pytest.raises(TypeError):
        PercentLimits(min_percent=10, max_percent=Decimal(10))  # type: ignore[arg-type]


# --- Tenge back into percentages ----------------------------------------------


def test_absolute_limits_are_shown_as_percentages() -> None:
    shown = percent_from_limits(Decimal(100_000), PriceLimits(Decimal(90_000), Decimal(105_000)))

    assert (shown.min_percent, shown.max_percent) == (Decimal("10.00"), Decimal("5.00"))


def test_limits_set_long_ago_are_clamped_into_the_accepted_range() -> None:
    # A 1 ₸ floor is a 99.999% discount, which the merchant could not type in.
    shown = percent_from_limits(Decimal(100_000), PriceLimits(Decimal(1), Decimal(100_000)))

    assert shown.min_percent == Decimal("90.00")
    assert shown.max_percent == Decimal("0.00")


# --- Recalculation when the base price changes --------------------------------


def test_limits_follow_the_new_base_price() -> None:
    # The merchant raised their own price from 100 000 to 120 000.
    updated = recalculated_limits(
        Decimal(120_000), percent("10", "5"), PriceLimits(Decimal(90_000), Decimal(105_000))
    )

    assert updated == PriceLimits(Decimal(108_000), Decimal(126_000))


def test_limits_set_in_tenge_are_left_alone() -> None:
    current = PriceLimits(Decimal(90_000), Decimal(105_000))

    assert recalculated_limits(Decimal(120_000), None, current) == current


@pytest.mark.parametrize("base", [None, Decimal(0)])
def test_a_product_without_a_base_price_keeps_its_limits(base: Decimal | None) -> None:
    # Guessing here would silently move a stop-loss.
    current = PriceLimits(Decimal(90_000), Decimal(105_000))

    assert recalculated_limits(base, percent("10", "5"), current) == current


def test_recalculation_is_idempotent() -> None:
    once = recalculated_limits(
        Decimal(100_000), percent("10", "5"), PriceLimits(Decimal(1), Decimal(2))
    )
    twice = recalculated_limits(Decimal(100_000), percent("10", "5"), once)

    assert once == twice


def test_the_floor_does_not_walk_down_when_the_repriced_price_falls() -> None:
    # The anchor is the merchant's own price, so repeated repricing cycles
    # cannot ratchet the stop-loss towards zero.
    base, limits = Decimal(100_000), PriceLimits(Decimal(90_000), Decimal(105_000))
    for _cycle in range(10):
        limits = recalculated_limits(base, percent("10", "5"), limits)

    assert limits.min_price == Decimal(90_000)
