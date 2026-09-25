"""The catalogue screen: filters, categories, sorting, margins and bulk tools."""

from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from repricer.api.app import create_app
from repricer.api.deps import get_merchant, get_session
from repricer.api.settings import ApiSettings, get_settings
from repricer.db import Product, ProductAvailability, RepricerRule
from repricer.db.settings_store import load_settings
from repricer.pricing import PricingStrategy
from repricer.uploader import MerchantIdentity

MERCHANT = MerchantIdentity("30123456", "Ромашка")
ALMATY = "750000000"
API_KEY = "test-key"


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_merchant] = lambda: MERCHANT
    app.dependency_overrides[get_settings] = lambda: ApiSettings(
        **{"_env_file": None, "repricer_api_key": API_KEY}
    )
    with TestClient(app, headers={"X-API-Key": API_KEY}) as test_client:
        yield test_client


@pytest.fixture
def shop(session: Session) -> Any:
    """A shop with a Kaspi contract filled in, so margins are not guesses."""
    settings = load_settings(session)
    settings.merchant_id = MERCHANT.merchant_id
    settings.company = MERCHANT.company
    settings.commission_percent = Decimal(12)
    settings.delivery_cost = Decimal(1_500)
    session.flush()
    return settings


def add_product(
    session: Session,
    sku: str,
    *,
    title: str = "Товар",
    base_price: int | None = 100_000,
    purchase_price: int | None = None,
    category: str | None = None,
    active: bool = True,
    stock: int | None = 5,
    available: bool = True,
    card: str = "102298404",
    rule: bool = True,
) -> Product:
    product = Product(
        merchant_id=MERCHANT.merchant_id,
        sku=sku,
        kaspi_product_id=card,
        title=title,
        brand="Apple",
        base_price=Decimal(base_price) if base_price is not None else None,
        purchase_price=Decimal(purchase_price) if purchase_price is not None else None,
        category=category,
        is_active=active,
    )
    product.availabilities.append(
        ProductAvailability(store_id="PP1", stock_count=stock, available=available)
    )
    if rule:
        product.rules.append(
            RepricerRule(
                city_id=ALMATY,
                strategy=PricingStrategy.BEAT_FIRST,
                min_price=Decimal(90_000),
                max_price=Decimal(110_000),
                current_price=Decimal(base_price or 100_000),
            )
        )
    session.add(product)
    session.flush()
    return product


def skus(response: Any) -> list[str]:
    return [item["sku"] for item in response.json()["items"]]


# --- Filters ------------------------------------------------------------------


def test_on_sale_means_switched_on_and_in_stock(client: TestClient, session: Session) -> None:
    add_product(session, "ON")
    add_product(session, "OFF-SWITCH", active=False)
    add_product(session, "OFF-STOCK", stock=0)
    add_product(session, "OFF-STORE", available=False)

    assert skus(client.get("/api/rules", params={"sale": "on"})) == ["ON"]
    assert skus(client.get("/api/rules", params={"sale": "off"})) == [
        "OFF-STOCK",
        "OFF-STORE",
        "OFF-SWITCH",
    ]


def test_a_product_with_unlimited_stock_counts_as_on_sale(
    client: TestClient, session: Session
) -> None:
    # stockCount is optional in the Kaspi feed: no number means "available".
    add_product(session, "NO-COUNT", stock=None)

    assert skus(client.get("/api/rules", params={"sale": "on"})) == ["NO-COUNT"]


def test_filters_narrow_each_other(client: TestClient, session: Session) -> None:
    add_product(session, "A", category="Ноутбуки")
    add_product(session, "B", category="Ноутбуки", active=False)
    add_product(session, "C", category="Телефоны")

    response = client.get("/api/rules", params={"category": "Ноутбуки", "sale": "on"})

    assert skus(response) == ["A"]
    assert response.json()["total"] == 1


def test_products_without_a_category_have_their_own_filter(
    client: TestClient, session: Session
) -> None:
    add_product(session, "A", category="Ноутбуки")
    add_product(session, "B")

    assert skus(client.get("/api/rules", params={"category": "__none__"})) == ["B"]


def test_search_still_works_with_the_new_filters(client: TestClient, session: Session) -> None:
    add_product(session, "A", title="Ноутбук Apple")
    add_product(session, "B", title="Телефон Apple")

    assert skus(client.get("/api/rules", params={"search": "ноутбук"})) == ["A"]


# --- The category menu --------------------------------------------------------


def test_category_menu_counts_products_and_puts_the_uncategorised_last(
    client: TestClient, session: Session
) -> None:
    add_product(session, "A", category="Ноутбуки")
    add_product(session, "B", category="Ноутбуки")
    add_product(session, "C", category="Телефоны")
    add_product(session, "D")

    assert client.get("/api/rules/categories").json() == [
        {"name": "Ноутбуки", "products": 2},
        {"name": "Телефоны", "products": 1},
        {"name": None, "products": 1},
    ]


def test_a_category_is_set_and_cleared_from_the_product_card(
    client: TestClient, session: Session
) -> None:
    add_product(session, "A")

    client.patch("/api/products/A/management", json={"category": "Системные блоки"})
    assert client.get("/api/products/A").json()["category"] == "Системные блоки"

    client.patch("/api/products/A/management", json={"category": ""})
    assert client.get("/api/products/A").json()["category"] is None


# --- Sorting ------------------------------------------------------------------


def test_sorting_by_price(client: TestClient, session: Session) -> None:
    add_product(session, "MID", base_price=100_000)
    add_product(session, "LOW", base_price=50_000)
    add_product(session, "HIGH", base_price=200_000)

    assert skus(client.get("/api/rules", params={"sort": "price_asc"})) == ["LOW", "MID", "HIGH"]
    assert skus(client.get("/api/rules", params={"sort": "price_desc"})) == ["HIGH", "MID", "LOW"]


def test_sorting_by_margin_puts_the_loss_makers_first(
    client: TestClient, session: Session, shop: Any
) -> None:
    add_product(session, "GOOD", base_price=100_000, purchase_price=50_000)
    add_product(session, "LOSS", base_price=100_000, purchase_price=95_000)

    assert skus(client.get("/api/rules", params={"sort": "margin_asc"})) == ["LOSS", "GOOD"]
    assert skus(client.get("/api/rules", params={"sort": "margin_desc"})) == ["GOOD", "LOSS"]


def test_products_without_the_numbers_to_sort_by_come_last(
    client: TestClient, session: Session, shop: Any
) -> None:
    add_product(session, "PRICED", base_price=100_000, purchase_price=50_000)
    add_product(session, "UNKNOWN", base_price=None, rule=False)

    assert skus(client.get("/api/rules", params={"sort": "margin_asc"}))[-1] == "UNKNOWN"
    assert skus(client.get("/api/rules", params={"sort": "price_asc"}))[-1] == "UNKNOWN"


# --- Margins in the list ------------------------------------------------------


def test_the_list_carries_the_margin_at_the_current_and_limit_prices(
    client: TestClient, session: Session, shop: Any
) -> None:
    add_product(session, "A", base_price=100_000, purchase_price=70_000)

    margins = client.get("/api/rules").json()["items"][0]["margins"]

    # 100 000 − 12% commission − 3% tax − 1 500 delivery − 70 000 cost.
    assert margins["current"]["profit"] == "13500"
    assert margins["current"]["estimated"] is False
    assert margins["minimum"]["profit"] == "5000"  # at the 90 000 floor
    assert margins["maximum"]["profit"] == "22000"  # at the 110 000 ceiling


def test_a_margin_without_a_purchase_price_is_marked_as_an_estimate(
    client: TestClient, session: Session, shop: Any
) -> None:
    add_product(session, "A", base_price=100_000)

    assert client.get("/api/rules").json()["items"][0]["margins"]["current"]["estimated"] is True


def test_the_margin_calculator_answers_for_any_price(
    client: TestClient, session: Session, shop: Any
) -> None:
    add_product(session, "A", purchase_price=70_000)

    result = client.post("/api/tools/margin", json={"price": "120000", "sku": "A"}).json()

    assert result["commission"] == "14400"
    assert result["tax"] == "3600"
    assert result["profit"] == "30500"
    assert result["break_even_price"] == "84118"


def test_the_calculator_accepts_figures_that_are_not_saved_anywhere(
    client: TestClient, session: Session, shop: Any
) -> None:
    result = client.post(
        "/api/tools/margin",
        json={"price": "10000", "purchase_price": "5000", "commission_percent": "0",
              "delivery_cost": "0"},
    ).json()

    assert result["profit"] == "4700"  # only the 3% retail tax


# --- Price limits in percent --------------------------------------------------


def test_limits_can_be_set_as_a_share_of_the_product_price(
    client: TestClient, session: Session
) -> None:
    add_product(session, "A", base_price=100_000)

    response = client.put(
        "/api/strategy/products/A/limits", json={"min_percent": "10", "max_percent": "5"}
    )

    assert response.status_code == 200
    rule = response.json()[0]
    assert (rule["min_price"], rule["max_price"]) == ("90000", "105000")
    assert (rule["min_percent"], rule["max_percent"]) == ("10.00", "5.00")


def test_limits_in_percent_follow_the_product_price(client: TestClient, session: Session) -> None:
    add_product(session, "A", base_price=100_000)
    client.put("/api/strategy/products/A/limits", json={"min_percent": "10", "max_percent": "5"})

    client.put(
        "/api/products/A",
        json={
            "sku": "A",
            "title": "Товар",
            "kaspi_product_id": "102298404",
            "base_price": "120000",
            "availabilities": [{"store_id": "PP1", "stock_count": 5}],
        },
    )

    rule = client.get("/api/products/A").json()["rules"][0]
    assert (rule["min_price"], rule["max_price"]) == ("108000", "126000")


def test_limits_given_in_tenge_stay_where_they_were_put(
    client: TestClient, session: Session
) -> None:
    add_product(session, "A", base_price=100_000)
    client.put(
        "/api/strategy/products/A/limits", json={"min_price": "80000", "max_price": "130000"}
    )

    client.put(
        "/api/products/A",
        json={
            "sku": "A",
            "title": "Товар",
            "kaspi_product_id": "102298404",
            "base_price": "120000",
            "availabilities": [{"store_id": "PP1", "stock_count": 5}],
        },
    )

    rule = client.get("/api/products/A").json()["rules"][0]
    assert (rule["min_price"], rule["max_price"]) == ("80000", "130000")
    assert rule["min_percent"] is None


def test_one_side_in_percent_and_one_in_tenge(client: TestClient, session: Session) -> None:
    add_product(session, "A", base_price=100_000)

    client.put("/api/strategy/products/A/limits", json={"min_percent": "10"})
    rule = client.put(
        "/api/strategy/products/A/limits", json={"max_price": "150000"}
    ).json()[0]

    assert (rule["min_price"], rule["max_price"]) == ("90000", "150000")
    assert (rule["min_percent"], rule["max_percent"]) == ("10.00", None)


def test_a_percent_limit_needs_a_price_to_be_a_percent_of(
    client: TestClient, session: Session
) -> None:
    add_product(session, "A", base_price=None, rule=False)

    response = client.put("/api/strategy/products/A/limits", json={"min_percent": "10"})

    assert response.status_code == 422
    assert "цены" in response.json()["detail"]


@pytest.mark.parametrize(
    "payload",
    [
        {"min_price": "90000", "min_percent": "10"},
        {"min_percent": "95"},
        {"max_percent": "600"},
        {},
    ],
)
def test_contradictory_or_impossible_limits_are_refused(
    client: TestClient, session: Session, payload: dict[str, str]
) -> None:
    add_product(session, "A", base_price=100_000)

    assert client.put("/api/strategy/products/A/limits", json=payload).status_code == 422


# --- Bulk tools ---------------------------------------------------------------


def test_bulk_floor_sets_limits_and_allows_lowering(client: TestClient, session: Session) -> None:
    add_product(session, "A", base_price=100_000)
    add_product(session, "B", base_price=50_000)

    result = client.post("/api/tools/bulk", json={"set_min_percent": "10"}).json()

    assert (result["products_seen"], result["limits_set"]) == (2, 2)
    items = {item["sku"]: item for item in client.get("/api/rules").json()["items"]}
    assert items["A"]["rules"][0]["min_price"] == "90000"
    assert items["B"]["rules"][0]["min_price"] == "45000"
    assert items["A"]["auto_decrease"] is True


def test_bulk_ceiling_sets_limits_and_allows_raising(client: TestClient, session: Session) -> None:
    add_product(session, "A", base_price=100_000)

    client.post("/api/tools/bulk", json={"set_max_percent": "20"})

    item = client.get("/api/rules").json()["items"][0]
    assert item["rules"][0]["max_price"] == "120000"
    assert item["auto_increase"] is True


def test_bulk_tools_touch_only_the_selected_products(
    client: TestClient, session: Session
) -> None:
    add_product(session, "A", base_price=100_000)
    add_product(session, "B", base_price=100_000)

    client.post("/api/tools/bulk", json={"skus": ["A"], "set_min_percent": "10"})

    items = {item["sku"]: item for item in client.get("/api/rules").json()["items"]}
    assert items["A"]["rules"][0]["min_price"] == "90000"
    assert items["A"]["rules"][0]["min_percent"] == "10.00"
    # B keeps the limits it was created with and never learned a percentage.
    assert items["B"]["rules"][0]["min_percent"] is None


def test_bulk_raise_puts_todays_price_up_to_the_ceiling(
    client: TestClient, session: Session
) -> None:
    product = add_product(session, "A", base_price=100_000)
    product.rules[0].current_price = Decimal(92_000)
    session.flush()

    result = client.post("/api/tools/bulk", json={"raise_to_max": True}).json()

    assert result["prices_raised"] == 1
    assert client.get("/api/rules").json()["items"][0]["rules"][0]["current_price"] == "110000"


def test_bulk_stops_lowering_prices_of_products_that_are_not_on_sale(
    client: TestClient, session: Session
) -> None:
    add_product(session, "ON")
    add_product(session, "OFF", stock=0)

    result = client.post(
        "/api/tools/bulk", json={"disable_decrease_when_off_sale": True}
    ).json()

    assert result["decrease_disabled"] == 1
    items = {item["sku"]: item for item in client.get("/api/rules").json()["items"]}
    assert items["OFF"]["auto_decrease"] is False
    assert items["ON"]["auto_decrease"] is True


def test_products_the_tools_cannot_touch_are_reported_not_skipped_silently(
    client: TestClient, session: Session
) -> None:
    add_product(session, "NO-CARD", card="", rule=False)
    add_product(session, "NO-PRICE", base_price=None, rule=False)

    result = client.post("/api/tools/bulk", json={"set_min_percent": "10"}).json()

    assert result["limits_set"] == 0
    assert result["skipped"] == {"нет карточки Kaspi": 1, "нет своей цены": 1}


def test_a_request_that_asks_for_nothing_is_refused(client: TestClient, session: Session) -> None:
    add_product(session, "A")

    assert client.post("/api/tools/bulk", json={}).status_code == 422
