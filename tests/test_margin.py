from decimal import Decimal

import pytest

from repricer.pricing import MarginInputs, break_even_price, calculate_margin


def inputs(**overrides: object) -> MarginInputs:
    params: dict[str, object] = {
        "price": Decimal(100_000),
        "purchase_price": Decimal(70_000),
        "commission_percent": Decimal(12),
        "delivery_cost": Decimal(1_500),
    }
    params.update(overrides)
    return MarginInputs(**params)  # type: ignore[arg-type]


# --- The breakdown ------------------------------------------------------------


def test_every_deduction_is_itemised() -> None:
    result = calculate_margin(inputs())

    assert result.commission == Decimal(12_000)
    assert result.tax == Decimal(3_000)  # 3% of turnover, the default
    assert result.delivery == Decimal(1_500)
    assert result.purchase_price == Decimal(70_000)
    assert result.profit == Decimal(13_500)


def test_margin_is_profit_over_the_sale_price() -> None:
    assert calculate_margin(inputs()).margin_percent == Decimal("13.50")


def test_markup_is_profit_over_the_purchase_price() -> None:
    # 13 500 on a 70 000 purchase.
    assert calculate_margin(inputs()).markup_percent == Decimal("19.29")


def test_tax_defaults_to_three_percent() -> None:
    result = calculate_margin(MarginInputs(price=Decimal(10_000)))

    assert result.tax == Decimal(300)


def test_commission_is_zero_until_the_merchant_fills_theirs_in() -> None:
    assert calculate_margin(MarginInputs(price=Decimal(10_000))).commission == Decimal(0)


def test_selling_below_cost_gives_a_negative_profit() -> None:
    result = calculate_margin(inputs(price=Decimal(75_000)))

    assert result.profit < 0
    assert result.margin_percent < 0


def test_a_product_without_a_purchase_price_is_marked_as_an_estimate() -> None:
    result = calculate_margin(inputs(purchase_price=None))

    assert result.estimated is True
    assert result.markup_percent is None
    # Nothing is invented for the missing cost, so the figure is gross margin.
    assert result.profit == Decimal(83_500)


def test_a_known_purchase_price_is_not_an_estimate() -> None:
    assert calculate_margin(inputs()).estimated is False


def test_amounts_are_kept_to_the_tiyn() -> None:
    result = calculate_margin(inputs(price=Decimal("9999.99"), purchase_price=Decimal("5000.55")))

    assert result.commission == Decimal("1200.00")
    assert result.tax == Decimal("300.00")


# --- Break-even ---------------------------------------------------------------


def test_break_even_covers_commission_and_tax_on_itself() -> None:
    # 71 500 of cost against 85% of the price left after 12% + 3%.
    price = break_even_price(inputs())

    assert price == Decimal(84_118)
    assert calculate_margin(inputs(price=price)).profit >= 0


def test_one_tenge_below_break_even_already_loses_money() -> None:
    price = break_even_price(inputs())
    assert price is not None

    assert calculate_margin(inputs(price=price - 1)).profit < 0


def test_break_even_without_commission_is_cost_plus_delivery_plus_tax() -> None:
    assert break_even_price(
        MarginInputs(price=Decimal(1), purchase_price=Decimal(9_700), delivery_cost=Decimal(0))
    ) == Decimal(10_000)  # 9700 / 0.97: the tax is paid on the selling price


def test_no_break_even_without_any_cost() -> None:
    # Nothing to cover: every price is profitable, so there is no threshold.
    assert break_even_price(MarginInputs(price=Decimal(1_000))) is None


def test_no_break_even_when_commission_and_tax_take_the_whole_price() -> None:
    assert (
        break_even_price(
            inputs(commission_percent=Decimal(97), tax_percent=Decimal(3))
        )
        is None
    )


def test_break_even_is_exposed_on_the_breakdown() -> None:
    assert calculate_margin(inputs()).break_even_price == break_even_price(inputs())


# --- Refusals -----------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"price": Decimal(0)},
        {"price": Decimal(-1)},
        {"price": Decimal("NaN")},
        {"purchase_price": Decimal(-1)},
        {"delivery_cost": Decimal(-1)},
        {"tax_percent": Decimal(-1)},
        {"tax_percent": Decimal(101)},
        {"commission_percent": Decimal(-1)},
        {"commission_percent": Decimal(101)},
    ],
)
def test_unusable_input_is_refused(overrides: dict[str, Decimal]) -> None:
    with pytest.raises(ValueError):
        inputs(**overrides)


@pytest.mark.parametrize("price", [100_000, "100000", 100_000.0])
def test_money_must_be_a_decimal(price: object) -> None:
    with pytest.raises(TypeError):
        inputs(price=price)
