import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from repricer.uploader import (
    KASPI_TIMEZONE,
    Availability,
    FeedOffer,
    MerchantIdentity,
    build_feed,
)

NS = "{kaspiShopping}"
MERCHANT = MerchantIdentity("30123456", "Ромашка")
MOMENT = datetime(2026, 9, 21, 10, 0, tzinfo=KASPI_TIMEZONE)


def offer(**overrides: Any) -> FeedOffer:
    params: dict[str, Any] = {
        "sku": "SKU-1",
        "model": "Apple iPhone 17 256Gb",
        "brand": "Apple",
        "availabilities": (Availability("PP1", stock_count=5),),
        "price": Decimal(362000),
    }
    params.update(overrides)
    return FeedOffer(**params)


def build(*offers: FeedOffer, **kwargs: Any) -> bytes:
    return build_feed(MERCHANT, offers or (offer(),), generated_at=MOMENT, **kwargs)


def root_of(*offers: FeedOffer, **kwargs: Any) -> ET.Element:
    return ET.fromstring(build(*offers, **kwargs))


def first_offer(*offers: FeedOffer, **kwargs: Any) -> ET.Element:
    element = root_of(*offers, **kwargs).find(f"{NS}offers/{NS}offer")
    assert element is not None
    return element


# --- Document skeleton --------------------------------------------------------


def test_declares_the_kaspi_namespace_and_schema() -> None:
    document = build().decode()

    assert document.startswith("<?xml version='1.0' encoding='utf-8'?>")
    assert 'xmlns="kaspiShopping"' in document
    assert 'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"' in document
    assert 'xsi:schemaLocation="kaspiShopping http://kaspi.kz/kaspishopping.xsd"' in document


def test_root_and_its_children_are_in_the_kaspi_namespace() -> None:
    root = root_of()

    assert root.tag == f"{NS}kaspi_catalog"
    assert [child.tag for child in root] == [f"{NS}company", f"{NS}merchantid", f"{NS}offers"]


def test_company_and_merchantid_carry_the_store_identity() -> None:
    root = root_of()

    assert root.findtext(f"{NS}company") == "Ромашка"
    # merchantid is lower case in the Kaspi schema, unlike storeId or cityId.
    assert root.findtext(f"{NS}merchantid") == "30123456"


def test_date_is_iso_8601_with_offset() -> None:
    assert root_of().get("date") == "2026-09-21T10:00:00+05:00"


def test_generated_at_must_be_timezone_aware() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        build_feed(MERCHANT, [offer()], generated_at=datetime(2026, 9, 21, 10, 0))


def test_utc_timestamp_is_kept_as_utc() -> None:
    feed = build_feed(MERCHANT, [offer()], generated_at=datetime(2026, 9, 21, 5, 0, tzinfo=timezone.utc))

    assert ET.fromstring(feed).get("date") == "2026-09-21T05:00:00+00:00"


# --- Offers -------------------------------------------------------------------


def test_offer_elements_follow_the_schema_order() -> None:
    element = first_offer(offer(city_prices={"750000000": Decimal(362000)}))

    assert element.get("sku") == "SKU-1"
    assert [child.tag for child in element] == [
        f"{NS}model",
        f"{NS}brand",
        f"{NS}availabilities",
        f"{NS}price",
        f"{NS}cityprices",
    ]
    assert element.findtext(f"{NS}model") == "Apple iPhone 17 256Gb"
    assert element.findtext(f"{NS}brand") == "Apple"


def test_every_offer_is_written_in_order() -> None:
    root = root_of(offer(sku="A"), offer(sku="B"), offer(sku="C"))

    assert [element.get("sku") for element in root.iter(f"{NS}offer")] == ["A", "B", "C"]


def test_availability_attributes_use_kaspi_casing() -> None:
    element = first_offer(
        offer(availabilities=(Availability("PP1", available=True, stock_count=234, preorder_days=3),))
    )
    availability = element.find(f"{NS}availabilities/{NS}availability")

    assert availability is not None
    assert availability.attrib == {
        "available": "yes",
        "storeId": "PP1",
        "preOrder": "3",
        "stockCount": "234",
    }


def test_unavailable_store_without_counts_omits_optional_attributes() -> None:
    element = first_offer(offer(availabilities=(Availability("PP2", available=False),)))
    availability = element.find(f"{NS}availabilities/{NS}availability")

    assert availability is not None
    assert availability.attrib == {"available": "no", "storeId": "PP2"}


def test_all_pickup_points_are_listed() -> None:
    element = first_offer(
        offer(availabilities=(Availability("PP1", stock_count=1), Availability("PP2", stock_count=0)))
    )

    stores = [node.get("storeId") for node in element.iter(f"{NS}availability")]
    assert stores == ["PP1", "PP2"]


# --- Prices -------------------------------------------------------------------


def test_price_is_written_as_whole_tenge() -> None:
    assert first_offer(offer(price=Decimal("362000.00"))).findtext(f"{NS}price") == "362000"


def test_city_prices_use_camel_case_city_id_and_are_sorted() -> None:
    element = first_offer(
        offer(
            price=None,
            city_prices={"750000000": Decimal(362000), "710000000": Decimal(363000)},
        )
    )
    city_prices = element.find(f"{NS}cityprices")

    assert city_prices is not None
    assert [(node.get("cityId"), node.text) for node in city_prices] == [
        ("710000000", "363000"),
        ("750000000", "362000"),
    ]
    assert element.find(f"{NS}price") is None


def test_offer_without_city_prices_has_no_cityprices_block() -> None:
    assert first_offer(offer()).find(f"{NS}cityprices") is None


def test_same_catalogue_renders_byte_for_byte_the_same() -> None:
    prices = {"750000000": Decimal(1000), "710000000": Decimal(1100)}

    assert build(offer(city_prices=dict(prices))) == build(offer(city_prices=dict(reversed(list(prices.items())))))


# --- Encoding and text safety -------------------------------------------------


def test_special_characters_are_escaped() -> None:
    feed = build_feed(
        MerchantIdentity("30123456", 'ТОО "Ромашка" & Co'),
        [offer(model="Кабель <USB-C> & <HDMI>")],
        generated_at=MOMENT,
    )

    assert b"&amp;" in feed and b"&lt;USB-C&gt;" in feed
    root = ET.fromstring(feed)
    assert root.findtext(f"{NS}company") == 'ТОО "Ромашка" & Co'
    assert root.findtext(f"{NS}offers/{NS}offer/{NS}model") == "Кабель <USB-C> & <HDMI>"


def test_cyrillic_is_written_as_utf8_not_escapes() -> None:
    assert "Ромашка".encode() in build()


def test_indentation_can_be_turned_off_for_large_catalogues() -> None:
    compact = build(indent=False)

    # Only the newline that follows the XML declaration is left.
    assert compact.count(b"\n") == 1
    assert b">\n  <" not in compact
    assert len(compact) < len(build())
    assert ET.fromstring(compact).findtext(f"{NS}merchantid") == "30123456"


# --- Refusals -----------------------------------------------------------------


def test_duplicate_sku_is_refused() -> None:
    with pytest.raises(ValueError, match="duplicate sku"):
        build(offer(sku="SKU-1"), offer(sku="SKU-1"))


def test_empty_feed_is_refused() -> None:
    # Publishing an empty catalogue would pull every offer off Kaspi.
    with pytest.raises(ValueError, match="no offers"):
        build_feed(MERCHANT, [], generated_at=MOMENT)


@pytest.mark.parametrize(
    "overrides",
    [
        {"sku": ""},
        {"sku": "   "},
        {"model": ""},
        {"brand": ""},
        {"availabilities": ()},
        {"price": Decimal(0)},
        {"price": Decimal(-1)},
        {"price": Decimal("362000.50")},
        {"price": Decimal("NaN")},
        {"price": None, "city_prices": {}},
        {"price": None, "city_prices": {"750000000": Decimal("999.99")}},
        {"price": None, "city_prices": {"": Decimal(1000)}},
    ],
)
def test_unusable_offer_is_refused(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        offer(**overrides)


@pytest.mark.parametrize("price", [362000, "362000", 362000.0])
def test_price_must_be_a_decimal(price: object) -> None:
    with pytest.raises(TypeError):
        offer(price=price)


@pytest.mark.parametrize(
    "kwargs", [{"store_id": ""}, {"stock_count": -1}, {"preorder_days": -1}, {"stock_count": True}]
)
def test_unusable_availability_is_refused(kwargs: dict[str, Any]) -> None:
    with pytest.raises((ValueError, TypeError)):
        Availability(**{"store_id": "PP1", **kwargs})


@pytest.mark.parametrize("kwargs", [{"merchant_id": ""}, {"company": "  "}])
def test_unusable_merchant_identity_is_refused(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        MerchantIdentity(**{"merchant_id": "30123456", "company": "Ромашка", **kwargs})
