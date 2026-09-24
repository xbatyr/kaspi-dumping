"""Kaspi Price List XML feed.

Kaspi fetches the feed from an HTTP(S) URL roughly once an hour and imports it
as the merchant's assortment, so a feed carries the whole catalogue, not only
the offers whose price changed.

The format, from Kaspi's partner guide (checked 2026-09-21):

    <?xml version='1.0' encoding='utf-8'?>
    <kaspi_catalog date="2026-09-21T10:00:00+05:00" xmlns="kaspiShopping"
                   xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                   xsi:schemaLocation="kaspiShopping http://kaspi.kz/kaspishopping.xsd">
      <company>Company</company>
      <merchantid>MerchantID</merchantid>
      <offers>
        <offer sku="232130213">
          <model>iphone 5s white 32gb</model>
          <brand>Apple</brand>
          <availabilities>
            <availability available="yes" storeId="PP1" preOrder="3" stockCount="234"/>
          </availabilities>
          <cityprices>
            <cityprice cityId="750000000">193000</cityprice>
          </cityprices>
        </offer>
      </offers>
    </kaspi_catalog>

Names are case-sensitive, and the casing is not consistent: ``merchantid`` is
lower case, while ``storeId``, ``stockCount``, ``preOrder`` and ``cityId`` are
camel case. ``brand`` is optional, and each offer has either ``price`` or
``cityprices``.

This module is pure: it turns plain values into bytes and imports nothing from
the database, the scraper or the rule engine.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import MappingProxyType

KASPI_NAMESPACE = "kaspiShopping"
XSI_NAMESPACE = "http://www.w3.org/2001/XMLSchema-instance"
SCHEMA_LOCATION = f"{KASPI_NAMESPACE} http://kaspi.kz/kaspishopping.xsd"
#: Kazakhstan has been on a single UTC+5 offset since March 2024.
KASPI_TIMEZONE = timezone(timedelta(hours=5))


@dataclass(frozen=True, slots=True)
class MerchantIdentity:
    """The store a feed belongs to: ``<merchantid>`` and ``<company>``."""

    merchant_id: str
    company: str

    def __post_init__(self) -> None:
        _require_text(self.merchant_id, "merchant_id")
        _require_text(self.company, "company")


@dataclass(frozen=True, slots=True)
class Availability:
    """One pickup point's stock for an offer: ``<availability/>``."""

    store_id: str
    available: bool = True
    stock_count: int | None = None
    #: Days until a pre-ordered item ships; Kaspi's ``preOrder``.
    preorder_days: int | None = None

    def __post_init__(self) -> None:
        _require_text(self.store_id, "store_id")
        _require_count(self.stock_count, "stock_count")
        _require_count(self.preorder_days, "preorder_days")


@dataclass(frozen=True, slots=True)
class FeedOffer:
    """One ``<offer>``: a product, where it is in stock, and what it costs."""

    sku: str
    model: str
    brand: str
    availabilities: tuple[Availability, ...]
    #: Used when there are no city-specific prices.
    price: Decimal | None = None
    city_prices: Mapping[str, Decimal] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        _require_text(self.sku, "sku")
        _require_text(self.model, "model")
        if not isinstance(self.brand, str):
            raise TypeError("brand must be a string")
        if not self.availabilities:
            raise ValueError(f"offer {self.sku!r} needs at least one availability")
        if self.price is not None:
            _require_price(self.price, f"price of offer {self.sku!r}")
        city_prices = {}
        for city_id, price in sorted(self.city_prices.items()):
            _require_text(city_id, "city_id")
            _require_price(price, f"price of offer {self.sku!r} in city {city_id}")
            city_prices[city_id] = price
        if self.price is None and not city_prices:
            raise ValueError(f"offer {self.sku!r} has neither a price nor city prices")
        # Sorted and read-only, so the same catalogue always renders byte for byte
        # the same and callers cannot mutate an offer after it is validated.
        object.__setattr__(self, "city_prices", MappingProxyType(city_prices))


def build_feed(
    merchant: MerchantIdentity,
    offers: Iterable[FeedOffer],
    *,
    generated_at: datetime | None = None,
    indent: bool = True,
) -> bytes:
    """Render a complete price list.

    ``generated_at`` must be timezone-aware; it defaults to now in Kazakhstan.
    """
    root = ET.Element(
        "kaspi_catalog",
        {
            "date": _feed_date(generated_at),
            "xmlns": KASPI_NAMESPACE,
            "xmlns:xsi": XSI_NAMESPACE,
            "xsi:schemaLocation": SCHEMA_LOCATION,
        },
    )
    ET.SubElement(root, "company").text = merchant.company
    ET.SubElement(root, "merchantid").text = merchant.merchant_id
    offers_element = ET.SubElement(root, "offers")

    seen: set[str] = set()
    for offer in offers:
        if offer.sku in seen:
            raise ValueError(f"duplicate sku {offer.sku!r}: a feed carries one offer per sku")
        seen.add(offer.sku)
        _append_offer(offers_element, offer)
    if not seen:
        # Publishing an empty catalogue would take every offer off Kaspi.
        raise ValueError("refusing to build a feed with no offers")

    if indent:
        ET.indent(root)
    # ET.tostring is typed as returning Any once an encoding is given; with a
    # byte encoding it is bytes.
    document: bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    return document


def _append_offer(parent: ET.Element, offer: FeedOffer) -> None:
    # Kaspi's schema allows an absent brand and exactly one price representation.
    element = ET.SubElement(parent, "offer", {"sku": offer.sku})
    ET.SubElement(element, "model").text = offer.model
    if offer.brand.strip():
        ET.SubElement(element, "brand").text = offer.brand

    availabilities = ET.SubElement(element, "availabilities")
    for availability in offer.availabilities:
        attributes = {
            "available": "yes" if availability.available else "no",
            "storeId": availability.store_id,
        }
        if availability.preorder_days is not None:
            attributes["preOrder"] = str(availability.preorder_days)
        if availability.stock_count is not None:
            attributes["stockCount"] = str(availability.stock_count)
        ET.SubElement(availabilities, "availability", attributes)

    if offer.city_prices:
        city_prices = ET.SubElement(element, "cityprices")
        for city_id, price in offer.city_prices.items():
            ET.SubElement(city_prices, "cityprice", {"cityId": city_id}).text = _price_text(price)
    elif offer.price is not None:
        ET.SubElement(element, "price").text = _price_text(offer.price)


def _feed_date(generated_at: datetime | None) -> str:
    moment = generated_at if generated_at is not None else datetime.now(KASPI_TIMEZONE)
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise ValueError("generated_at must be timezone-aware")
    return moment.isoformat(timespec="seconds")


def _price_text(price: Decimal) -> str:
    return str(int(price))


def _require_text(value: str, name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string, got {type(value).__name__}")
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def _require_count(value: int | None, name: str) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer, got {type(value).__name__}")
    if value < 0:
        raise ValueError(f"{name} must not be negative, got {value}")


def _require_price(price: Decimal, name: str) -> None:
    if not isinstance(price, Decimal):
        raise TypeError(f"{name} must be a Decimal, got {type(price).__name__}")
    if not price.is_finite() or price <= 0:
        raise ValueError(f"{name} must be a positive number, got {price}")
    if price != price.to_integral_value():
        # Rounding here could push a price under the rule's min_price, so the
        # caller has to decide what a fractional price means.
        raise ValueError(f"{name} must be whole tenge, got {price}")
