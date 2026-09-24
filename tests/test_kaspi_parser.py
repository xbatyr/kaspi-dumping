import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from repricer.scraper import KaspiResponseError, Offer, parse_offer, parse_offers_page

# A trimmed real response (product 102298404, Almaty, 2026-09-18).
FIXTURE = Path(__file__).parent / "fixtures" / "kaspi_offers_page.json"


def raw_offer(**overrides: Any) -> dict[str, Any]:
    offer: dict[str, Any] = {
        "merchantId": "11271005",
        "merchantName": "Tehno Trade",
        "price": 361788.0,
        "merchantRating": 4.8,
        "merchantReviewsQuantity": 231,
        "kaspiDelivery": True,
        "deliveryDuration": "TILL_5_DAYS",
    }
    offer.update(overrides)
    return {key: value for key, value in offer.items() if value is not ...}


def test_parses_real_kaspi_response() -> None:
    page = parse_offers_page(json.loads(FIXTURE.read_text()))

    assert page.total == 20
    assert page.raw_count == 4
    assert page.offers == (
        Offer("11271005", "Tehno Trade", Decimal("361788.0"), 4.8, 231, True, "TILL_5_DAYS"),
        Offer("30463420", "MOOD PLACE", Decimal("361794.0"), None, 0, False, "TILL_7_DAYS"),
        Offer("12942011", "ProUnit", Decimal("362000.0"), 4.9, 272, False, "TOMORROW"),
        Offer("3932001", "Gadgetkz АВТОРИЗОВАННЫЙ МАГАЗИН", Decimal("362000.0"), 4.9, 146, True, "TOMORROW"),
    )


def test_empty_page_is_valid() -> None:
    page = parse_offers_page({"offers": [], "total": 0, "offersCount": 0})

    assert page.offers == ()
    assert (page.raw_count, page.total) == (0, 0)


def test_missing_optional_fields_become_unknown(warnings_logged: list[str]) -> None:
    offer = parse_offer({"merchantId": "Satel", "price": 365000.0})

    assert offer == Offer("Satel", None, Decimal("365000.0"), None, 0, False, None)
    assert warnings_logged == []


def test_zero_rating_means_no_rating(warnings_logged: list[str]) -> None:
    # Seen live: new stores come with "merchantRating": 0.0 and 0 reviews.
    offer = parse_offer(raw_offer(merchantRating=0.0, merchantReviewsQuantity=0))

    assert offer.rating is None
    assert warnings_logged == []


def test_numeric_merchant_id_and_price_string_are_accepted() -> None:
    offer = parse_offer(raw_offer(merchantId=11271005, price="361788"))

    assert offer.merchant_id == "11271005"
    assert offer.price == Decimal(361788)


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("merchantRating", "4.8", None),
        ("merchantRating", 7.5, None),
        ("merchantRating", -1, None),
        ("merchantRating", float("nan"), None),
        ("merchantRating", True, None),
        ("merchantReviewsQuantity", -3, 0),
        ("merchantReviewsQuantity", "231", 0),
        ("merchantReviewsQuantity", 231.5, 0),
        ("kaspiDelivery", "true", False),
        ("kaspiDelivery", 1, False),
        ("deliveryDuration", 5, None),
    ],
)
def test_malformed_optional_field_degrades_with_warning(
    field: str, value: object, expected: object, warnings_logged: list[str]
) -> None:
    offer = parse_offer(raw_offer(**{field: value}))

    attribute = {
        "merchantRating": "rating",
        "merchantReviewsQuantity": "reviews_count",
        "kaspiDelivery": "kaspi_delivery",
        "deliveryDuration": "delivery_duration",
    }[field]
    assert getattr(offer, attribute) == expected
    assert offer.price == Decimal("361788.0")
    assert len(warnings_logged) == 1 and field in warnings_logged[0]


def test_integral_float_review_count_is_accepted() -> None:
    assert parse_offer(raw_offer(merchantReviewsQuantity=231.0)).reviews_count == 231


@pytest.mark.parametrize(
    "overrides",
    [
        {"merchantId": ...},
        {"merchantId": None},
        {"merchantId": "   "},
        {"merchantId": True},
        {"merchantId": ["11271005"]},
        {"price": ...},
        {"price": None},
        {"price": 0},
        {"price": -100.0},
        {"price": "abc"},
        {"price": True},
        {"price": float("nan")},
        {"price": float("inf")},
        {"price": {"value": 1000}},
    ],
)
def test_offer_without_merchant_or_valid_price_is_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        parse_offer(raw_offer(**overrides))


def test_unusable_offers_are_skipped_and_logged(warnings_logged: list[str]) -> None:
    payload = {
        "offers": [
            raw_offer(merchantId="good-1"),
            raw_offer(merchantId="bad-price", price=0),
            "not an object",
            raw_offer(merchantId="good-2"),
        ],
        "total": 4,
    }

    page = parse_offers_page(payload)

    assert [offer.merchant_id for offer in page.offers] == ["good-1", "good-2"]
    assert page.raw_count == 4
    assert len(warnings_logged) == 2
    assert "bad-price" in warnings_logged[0]


def test_page_where_no_offer_parses_is_a_format_change() -> None:
    payload = {"offers": [{"seller": "x", "cost": 100}, {"seller": "y", "cost": 200}], "total": 2}

    with pytest.raises(KaspiResponseError, match="none of the 2 offers"):
        parse_offers_page(payload)


@pytest.mark.parametrize(
    "payload",
    [None, [], "offers", {"total": 3}, {"offers": None}, {"offers": {"0": {}}}],
    ids=["null", "list", "string", "no-offers-key", "offers-null", "offers-object"],
)
def test_unexpected_payload_shape_is_rejected(payload: object) -> None:
    with pytest.raises(KaspiResponseError):
        parse_offers_page(payload)


@pytest.mark.parametrize("total", [None, -1, "20", True, 20.5])
def test_invalid_total_is_ignored(total: object) -> None:
    assert parse_offers_page({"offers": [raw_offer()], "total": total}).total is None
