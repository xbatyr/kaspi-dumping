"""What the merchant is left with after a sale at a given price.

Four things come off a Kaspi sale:

* **Kaspi's commission** — a percentage of the sale price, agreed per category in
  the merchant contract (usually 8–15%). It has no sane default, so it is 0
  until the merchant fills theirs in, and the interface says so.
* **Tax** — a percentage of turnover, not of profit. Retail tax in Kazakhstan is
  3%, which is the default here.
* **Delivery** — a flat tenge amount per order, whatever the merchant pays on
  average to get the item to the buyer.
* **Purchase price** — what the item cost the merchant.

``profit = price − commission − tax − delivery − purchase price``

The reverse question matters more in practice: *how low can this price go before
the sale loses money?* That is ``break_even_price``, and it is what the stop-loss
(the rule's Min) should never be set below.

Pure module: no database, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

#: Retail tax on turnover in Kazakhstan.
DEFAULT_TAX_PERCENT = Decimal(3)
_HUNDRED = Decimal(100)
_TENGE = Decimal(1)
_CENTS = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class MarginInputs:
    """Everything the calculation needs; only the price is mandatory."""

    price: Decimal
    purchase_price: Decimal | None = None
    tax_percent: Decimal = DEFAULT_TAX_PERCENT
    #: Kaspi's cut, from the merchant's contract. 0 means "not filled in yet".
    commission_percent: Decimal = Decimal(0)
    #: Average fulfilment cost of one order, in tenge.
    delivery_cost: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        _require_money(self.price, "price", positive=True)
        if self.purchase_price is not None:
            _require_money(self.purchase_price, "purchase_price")
        _require_money(self.delivery_cost, "delivery_cost")
        _require_percent(self.tax_percent, "tax_percent")
        _require_percent(self.commission_percent, "commission_percent")


@dataclass(frozen=True, slots=True)
class MarginBreakdown:
    """The calculation, itemised, so the interface can show where money went."""

    price: Decimal
    commission: Decimal
    tax: Decimal
    delivery: Decimal
    purchase_price: Decimal
    profit: Decimal
    #: Profit as a share of the sale price, the usual meaning of "маржа".
    margin_percent: Decimal
    #: Profit as a share of the purchase price ("наценка"); None without a cost.
    markup_percent: Decimal | None
    #: The price at which profit would be exactly zero, rounded up to a whole
    #: tenge. None when commission and tax already eat the whole price.
    break_even_price: Decimal | None
    #: True when the cost side is only partly known, so profit is optimistic.
    estimated: bool


def calculate_margin(inputs: MarginInputs) -> MarginBreakdown:
    """Profit and margin for one sale at ``inputs.price``."""
    commission = _money(inputs.price * inputs.commission_percent / _HUNDRED)
    tax = _money(inputs.price * inputs.tax_percent / _HUNDRED)
    delivery = _money(inputs.delivery_cost)
    purchase = _money(inputs.purchase_price or Decimal(0))
    profit = _money(inputs.price - commission - tax - delivery - purchase)
    return MarginBreakdown(
        price=_money(inputs.price),
        commission=commission,
        tax=tax,
        delivery=delivery,
        purchase_price=purchase,
        profit=profit,
        margin_percent=_money(profit / inputs.price * _HUNDRED),
        markup_percent=_money(profit / purchase * _HUNDRED) if purchase > 0 else None,
        break_even_price=break_even_price(inputs),
        # Without a purchase price the "profit" is really just the gross margin.
        estimated=inputs.purchase_price is None,
    )


def break_even_price(inputs: MarginInputs) -> Decimal | None:
    """The lowest price that still does not lose money, in whole tenge.

    Commission and tax scale with the price, so this is not simply cost plus
    delivery: ``price × (1 − commission − tax) = purchase + delivery``.
    """
    variable_share = (
        Decimal(1) - (inputs.commission_percent + inputs.tax_percent) / _HUNDRED
    )
    if variable_share <= 0:
        # Commission and tax alone take the whole price: no price works.
        return None
    fixed = (inputs.purchase_price or Decimal(0)) + inputs.delivery_cost
    if fixed <= 0:
        return None
    return (fixed / variable_share).quantize(_TENGE, rounding=ROUND_CEILING)


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENTS, rounding=ROUND_HALF_UP)


def _require_money(value: Decimal, name: str, *, positive: bool = False) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be a Decimal, got {type(value).__name__}")
    if not value.is_finite():
        raise ValueError(f"{name} must be a finite number, got {value}")
    if value < 0 or (positive and value <= 0):
        raise ValueError(f"{name} must be {'positive' if positive else 'zero or more'}, got {value}")


def _require_percent(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be a Decimal, got {type(value).__name__}")
    if not value.is_finite() or not (Decimal(0) <= value <= _HUNDRED):
        raise ValueError(f"{name} must be between 0 and 100, got {value}")
