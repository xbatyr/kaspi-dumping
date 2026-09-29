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


def skus_of(body: dict[str, Any]) -> list[str]:
    return [item["sku"] for item in body["items"]]


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


def _raise_own_price(client: TestClient, price: str) -> None:
    client.put(
        "/api/products/A",
        json={"sku": "A", "title": "Товар", "kaspi_product_id": "102298404", "base_price": price},
    )


def test_a_floor_retyped_in_tenge_is_not_taken_back_by_the_old_percentage(
    client: TestClient, session: Session
) -> None:
    add_product(session, "A", base_price=100_000)
    client.put("/api/strategy/products/A/limits", json={"min_percent": "10", "max_percent": "5"})
    rule_id = client.get("/api/products/A").json()["rules"][0]["id"]

    client.post("/api/rules/bulk-update", json={"rule_ids": [rule_id], "min_price": "80000"})
    _raise_own_price(client, "120000")

    rule = client.get("/api/products/A").json()["rules"][0]
    # The floor the merchant typed stays; the ceiling still follows its percentage.
    assert (rule["min_price"], rule["min_percent"]) == ("80000", None)
    assert (rule["max_price"], rule["max_percent"]) == ("126000", "5.00")


def test_saving_the_shared_strategy_keeps_percent_limits(
    client: TestClient, session: Session
) -> None:
    add_product(session, "A", base_price=100_000)
    client.put("/api/strategy/products/A/limits", json={"min_percent": "10", "max_percent": "5"})

    client.put("/api/strategy", json={"strategy": "beat_first", "city_ids": [ALMATY]})
    _raise_own_price(client, "120000")

    rule = client.get("/api/products/A").json()["rules"][0]
    assert (rule["min_percent"], rule["max_percent"]) == ("10.00", "5.00")
    assert (rule["min_price"], rule["max_price"]) == ("108000", "126000")


def test_a_price_list_without_stock_or_price_columns_erases_nothing(
    client: TestClient, session: Session
) -> None:
    add_product(session, "A", base_price=100_000)

    client.post(
        "/api/products/import",
        json={"items": [{"sku": "A", "title": "Товар", "kaspi_product_id": "102298404"}]},
    )

    product = client.get("/api/products/A").json()
    assert product["base_price"] == "100000"
    assert product["brand"] == "Apple"
    assert [entry["store_id"] for entry in product["availabilities"]] == ["PP1"]


def test_an_own_price_with_tiyn_is_refused(client: TestClient, session: Session) -> None:
    response = client.post(
        "/api/products",
        json={"sku": "B", "title": "Товар", "base_price": "100.50"},
    )

    assert response.status_code == 422


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


def _market(product: Product, *, position: int, offers: int, leader: int) -> None:
    product.rules[0].market_snapshot = {
        "position": position, "offer_count": offers, "leader_price": str(leader),
    }


def test_grouped_filter_menu_like_algatop(client: TestClient, session: Session) -> None:
    first = add_product(session, "FIRST", purchase_price=50_000)
    _market(first, position=1, offers=3, leader=100_000)
    behind = add_product(session, "BEHIND")
    # The leader sells at 85 000, below our 90 000 floor: first place is out of reach.
    _market(behind, position=2, offers=2, leader=85_000)
    alone = add_product(session, "ALONE")
    _market(alone, position=1, offers=1, leader=100_000)
    alone.availabilities[0].preorder_days = 5
    manual = add_product(session, "MANUAL")
    manual.auto_decrease = False
    session.flush()

    def picked(value: str) -> list[str]:
        return sorted(skus(client.get("/api/rules", params={"filter": value})))

    assert picked("first_place") == ["ALONE", "FIRST"]
    assert picked("below_first") == ["BEHIND"]
    assert picked("min_short") == ["BEHIND"]
    assert picked("no_competitors") == ["ALONE"]
    assert picked("with_competitors") == ["BEHIND", "FIRST"]
    assert picked("with_cost") == ["FIRST"]
    assert picked("with_preorder") == ["ALONE"]
    assert picked("dumping_off") == ["MANUAL"]

    counts = client.get("/api/rules").json()["filter_counts"]
    assert (counts["all"], counts["on"], counts["first_place"], counts["no_cost"]) == (4, 4, 2, 3)
    assert counts["with_min"] == 4 and counts["without_min"] == 0


def test_filter_counts_follow_the_other_filters(client: TestClient, session: Session) -> None:
    add_product(session, "PC", category="Системные блоки", purchase_price=1)
    add_product(session, "CASE", category="Чехлы")

    body = client.get(
        "/api/rules", params={"category": "Системные блоки", "filter": "no_cost"}
    ).json()

    # The page is filtered; the menu counts are for the category, whatever is picked.
    assert body["items"] == []
    assert (body["filter_counts"]["all"], body["filter_counts"]["with_cost"]) == (1, 1)


# --- Import from AlgaTop -----------------------------------------------------


def _algatop_file(*rows: list[object]) -> bytes:
    from tests.test_algatop_import import HEADER, xlsx

    return xlsx(HEADER, *rows)


def _algatop_row(sku: str, price: int, low: int, high: int, **extra: object) -> list[object]:
    status = extra.get("status", "Опубликовано")
    return [sku, extra.get("card", 102298404), "Товар", "", status, None, "Алматы", price, 0,
            extra.get("cost", 0), 1, low, 0, high, extra.get("step", 2), 1, extra.get("stock", 3), 0]


def test_algatop_import_carries_prices_and_limits_over(client: TestClient, session: Session) -> None:
    add_product(session, "A", base_price=100_000)
    # The bot's own feed price, which AlgaTop's current price must replace.
    add_product(session, "B", base_price=100_000).rules[0].current_price = Decimal(90_000)
    session.flush()
    content = _algatop_file(
        _algatop_row("A", 99_990, 95_000, 105_000, cost=80_000, step=5),
        _algatop_row("B", 101_000, 96_000, 101_000, status="Снято с продажи"),
        _algatop_row("GHOST", 5_000, 4_000, 6_000, card=""),
    )

    preview = client.post("/api/products/import-algatop?preview=true", content=content,
                          headers={"Content-Type": "application/octet-stream"}).json()
    assert (preview["preview"], preview["updated"], preview["prices_changed"]) == (True, 2, 2)
    assert preview["not_found"] == ["GHOST"]
    assert client.get("/api/products/A").json()["rules"][0]["current_price"] == "100000"

    result = client.post("/api/products/import-algatop", content=content,
                         headers={"Content-Type": "application/octet-stream"}).json()

    assert (result["updated"], result["limits_changed"]) == (2, 2)
    a = client.get("/api/products/A").json()
    rule = a["rules"][0]
    assert (rule["current_price"], rule["min_price"], rule["max_price"], rule["step"]) == (
        "99990", "95000", "105000", 5,
    )
    assert (a["purchase_price"], a["auto_increase"], a["availabilities"][0]["stock_count"]) == (
        "80000", False, 3,
    )
    b = client.get("/api/products/B").json()
    assert (b["rules"][0]["current_price"], b["is_active"]) == ("101000", False)


def test_algatop_import_creates_missing_products_in_the_only_store(
    client: TestClient, session: Session
) -> None:
    add_product(session, "A")

    result = client.post("/api/products/import-algatop",
                         content=_algatop_file(_algatop_row("NEW", 50_000, 45_000, 55_000)),
                         headers={"Content-Type": "application/octet-stream"}).json()

    assert result["created"] == 1
    new = client.get("/api/products/NEW").json()
    assert [entry["store_id"] for entry in new["availabilities"]] == ["PP1"]
    assert new["rules"][0]["current_price"] == "50000"


def test_algatop_import_explains_an_unreadable_file(client: TestClient, session: Session) -> None:
    response = client.post("/api/products/import-algatop", content=b"not a table",
                           headers={"Content-Type": "application/octet-stream"})

    assert response.status_code == 422
    assert "Артикул" in response.json()["detail"]


# --- Nothing resumes a paused product behind the owner's back --------------

ASTANA = "710000000"


def _rule_states(client: TestClient, sku: str) -> dict[str, bool]:
    return {rule["city_id"]: rule["is_active"] for rule in client.get(f"/api/products/{sku}").json()["rules"]}


def test_saving_the_strategy_never_resumes_a_paused_product(
    client: TestClient, session: Session
) -> None:
    paused = add_product(session, "PAUSED")
    paused.rules[0].is_active = False
    add_product(session, "RUNNING")
    session.flush()

    client.put("/api/strategy", json={"strategy": "beat_first", "city_ids": [ALMATY]})
    assert _rule_states(client, "PAUSED") == {ALMATY: False}

    # A new city joins: the running product gets it, the paused one stays off.
    client.put("/api/strategy", json={"strategy": "beat_first", "city_ids": [ALMATY, ASTANA]})
    assert _rule_states(client, "PAUSED") == {ALMATY: False, ASTANA: False}
    assert _rule_states(client, "RUNNING") == {ALMATY: True, ASTANA: True}


def test_bulk_limits_do_not_resume_a_paused_product(client: TestClient, session: Session) -> None:
    client.put("/api/strategy", json={"strategy": "beat_first", "city_ids": [ALMATY]})
    paused = add_product(session, "PAUSED")
    paused.rules[0].is_active = False
    session.flush()

    client.post("/api/tools/bulk", json={"min_limit": {"mode": "percent", "value": "5"}})

    rule = client.get("/api/products/PAUSED").json()["rules"][0]
    assert (rule["min_price"], rule["is_active"]) == ("95000", False)


def test_reimporting_a_price_list_keeps_what_the_owner_switched_off(
    client: TestClient, session: Session
) -> None:
    product = add_product(session, "A")
    product.is_active = False
    product.rules[0].is_active = False
    session.flush()

    client.post("/api/products/import", json={"items": [{
        "sku": "A", "title": "Товар", "kaspi_product_id": "102298404",
        "rules": [{"city_id": ALMATY, "min_price": "91000", "max_price": "111000"}],
    }]})

    again = client.get("/api/products/A").json()
    assert again["is_active"] is False
    assert (again["rules"][0]["min_price"], again["rules"][0]["is_active"]) == ("91000", False)


def test_algatop_zero_stock_never_takes_a_product_off_sale(
    client: TestClient, session: Session
) -> None:
    # AlgaTop shows «Остаток: 0 шт.» for products it has on sale.
    add_product(session, "A", stock=4)

    client.post("/api/products/import-algatop",
                content=_algatop_file(_algatop_row("A", 100_000, 90_000, 110_000, stock=0)),
                headers={"Content-Type": "application/octet-stream"})

    product = client.get("/api/products/A").json()
    store = product["availabilities"][0]
    assert (store["available"], store["stock_count"]) == (True, 4)
    assert client.get("/api/rules").json()["sale_counts"]["on"] == 1


def test_algatop_preview_names_the_products_it_takes_off_sale(
    client: TestClient, session: Session
) -> None:
    add_product(session, "A")

    preview = client.post(
        "/api/products/import-algatop?preview=true",
        content=_algatop_file(_algatop_row("A", 100_000, 90_000, 110_000, status="Снято с продажи")),
        headers={"Content-Type": "application/octet-stream"},
    ).json()

    assert (preview["switched_off"], preview["switched_on"]) == (["A"], [])


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


def test_one_inverted_band_refuses_the_whole_selection(
    client: TestClient, session: Session
) -> None:
    add_product(session, "A", base_price=100_000)
    # B's ceiling (110 000, kept by "не менять") is below a floor 5% under 200 000.
    add_product(session, "B", base_price=200_000)

    response = client.post("/api/tools/bulk", json={"set_min_percent": "5"})

    assert response.status_code == 422
    assert "B (190000 > 110000)" in response.json()["detail"]
    items = {item["sku"]: item for item in client.get("/api/rules").json()["items"]}
    # A would have been fine on its own, and is still untouched.
    assert items["A"]["rules"][0]["min_price"] == "90000"
    assert items["A"]["rules"][0]["min_percent"] is None


def test_the_side_left_empty_is_not_overwritten(client: TestClient, session: Session) -> None:
    add_product(session, "A", base_price=100_000)

    client.post("/api/tools/bulk", json={"set_min_percent": "5"})

    rule = client.get("/api/rules").json()["items"][0]["rules"][0]
    assert (rule["min_price"], rule["max_price"]) == ("95000", "110000")
    assert rule["max_percent"] is None


def test_bulk_can_be_limited_to_products_on_sale(client: TestClient, session: Session) -> None:
    add_product(session, "ON")
    add_product(session, "OFF", stock=0)

    result = client.post("/api/tools/bulk", json={"only_on_sale": True, "set_min_percent": "5"}).json()

    assert (result["products_seen"], result["limits_set"]) == (2, 1)
    items = {item["sku"]: item for item in client.get("/api/rules").json()["items"]}
    assert items["ON"]["rules"][0]["min_price"] == "95000"
    assert items["OFF"]["rules"][0]["min_price"] == "90000"


def test_bulk_keeps_existing_floors_unless_asked_to_overwrite(
    client: TestClient, session: Session
) -> None:
    add_product(session, "BAND")  # 90 000 – 110 000 already
    fresh = add_product(session, "FRESH")
    fresh.rules[0].min_price = fresh.rules[0].max_price = Decimal(100_000)
    session.flush()

    result = client.post(
        "/api/tools/bulk", json={"set_min_percent": "5", "overwrite_min": False}
    ).json()

    assert result["limits_set"] == 1
    assert result["skipped"] == {"границы уже заданы": 1}
    items = {item["sku"]: item for item in client.get("/api/rules").json()["items"]}
    assert items["BAND"]["rules"][0]["min_price"] == "90000"
    assert items["BAND"]["auto_decrease"] is True
    assert items["FRESH"]["rules"][0]["min_price"] == "95000"


def test_selection_bar_takes_products_off_sale_in_one_request(
    client: TestClient, session: Session
) -> None:
    add_product(session, "A")
    add_product(session, "B")
    add_product(session, "C")

    result = client.post("/api/tools/sale", json={"skus": ["A", "B"], "is_active": False}).json()

    assert result == {"updated": 2}
    body = client.get("/api/rules", params={"sale": "on"}).json()
    assert skus_of(body) == ["C"]
    assert body["sale_counts"] == {"all": 3, "on": 1, "off": 2}


def test_bulk_limits_in_tenge_from_the_price_and_as_one_fixed_figure(
    client: TestClient, session: Session
) -> None:
    add_product(session, "A", base_price=100_000)
    add_product(session, "B", base_price=50_000)

    result = client.post("/api/tools/bulk", json={
        "min_limit": {"mode": "tenge_offset", "value": "2000"},
        "max_limit": {"mode": "fixed", "value": "150000"},
    }).json()

    assert result["limits_set"] == 2
    items = {item["sku"]: item["rules"][0] for item in client.get("/api/rules").json()["items"]}
    assert (items["A"]["min_price"], items["A"]["max_price"]) == ("98000", "150000")
    assert (items["B"]["min_price"], items["B"]["max_price"]) == ("48000", "150000")
    # Tenge figures do not follow the own price, so no percentage is kept.
    assert (items["A"]["min_percent"], items["A"]["max_percent"]) == (None, None)


def test_bulk_floor_from_cost_never_sells_at_a_loss(
    client: TestClient, session: Session, shop: Any
) -> None:
    add_product(session, "A", base_price=100_000, purchase_price=50_000)
    add_product(session, "NO-COST", base_price=100_000)

    result = client.post(
        "/api/tools/bulk", json={"min_limit": {"mode": "cost_markup", "value": "10"}}
    ).json()

    # Break-even: (50 000 cost + 1 500 delivery) / (1 − 12% − 3%) = 60 589 ₸; +10% → 66 648 ₸.
    assert result["skipped"] == {"нет себестоимости": 1}
    rule = client.get("/api/products/A").json()["rules"][0]
    assert (rule["min_price"], rule["max_price"]) == ("66648", "110000")


def test_bulk_preview_reports_every_change_and_writes_nothing(
    client: TestClient, session: Session
) -> None:
    add_product(session, "A", base_price=100_000)

    preview = client.post("/api/tools/bulk", json={
        "min_limit": {"mode": "percent", "value": "5"}, "step": 7, "auto_increase": True,
        "dry_run": True,
    }).json()

    assert preview["dry_run"] is True
    assert (preview["limits_set"], preview["steps_set"], preview["directions_set"]) == (1, 1, 1)
    assert preview["changes"] == [{
        "sku": "A", "title": "Товар",
        "min_before": "90000", "min_after": "95000", "max_before": "110000", "max_after": "110000",
    }]
    item = client.get("/api/rules").json()["items"][0]
    assert (item["rules"][0]["min_price"], item["rules"][0]["step"]) == ("90000", 1)
    assert item["auto_increase"] is False


def test_bulk_step_and_directions_without_touching_limits(
    client: TestClient, session: Session
) -> None:
    add_product(session, "A")

    result = client.post(
        "/api/tools/bulk", json={"step": 5, "auto_decrease": False, "auto_increase": True}
    ).json()

    assert (result["limits_set"], result["steps_set"], result["directions_set"]) == (0, 1, 1)
    item = client.get("/api/rules").json()["items"][0]
    assert (item["rules"][0]["step"], item["rules"][0]["min_price"]) == (5, "90000")
    assert (item["auto_decrease"], item["auto_increase"]) == (False, True)


def test_bulk_can_be_limited_to_one_category(client: TestClient, session: Session) -> None:
    add_product(session, "PC", category="Системные блоки")
    add_product(session, "CASE", category="Чехлы")

    client.post("/api/tools/bulk", json={
        "category": "Системные блоки", "min_limit": {"mode": "percent", "value": "5"},
    })

    items = {item["sku"]: item["rules"][0] for item in client.get("/api/rules").json()["items"]}
    assert (items["PC"]["min_price"], items["CASE"]["min_price"]) == ("95000", "90000")


@pytest.mark.parametrize(
    "limit",
    [
        {"mode": "percent", "value": "95"},
        {"mode": "fixed", "value": "0"},
        {"mode": "cost_markup", "value": "600"},
        {"mode": "guess", "value": "10"},
    ],
)
def test_bulk_refuses_a_limit_that_makes_no_sense(
    client: TestClient, session: Session, limit: dict[str, str]
) -> None:
    add_product(session, "A")

    assert client.post("/api/tools/bulk", json={"min_limit": limit}).status_code == 422


def test_bulk_selection_by_rule_takes_percentages_too(
    client: TestClient, session: Session
) -> None:
    picked = add_product(session, "A", base_price=100_000)
    add_product(session, "B", base_price=100_000)

    client.post("/api/tools/bulk", json={
        "rule_ids": [picked.rules[0].id],
        "min_limit": {"mode": "percent", "value": "5"},
        "max_limit": {"mode": "percent", "value": "20"},
    })

    items = {item["sku"]: item["rules"][0] for item in client.get("/api/rules").json()["items"]}
    assert (items["A"]["min_price"], items["A"]["max_price"]) == ("95000", "120000")
    assert (items["B"]["min_price"], items["B"]["max_price"]) == ("90000", "110000")


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
