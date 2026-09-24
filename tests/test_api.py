import xml.etree.ElementTree as ET
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from repricer.api.app import create_app
from repricer.api.deps import get_merchant, get_session
from repricer.api.settings import ApiSettings, get_settings
from repricer.db import PriceHistory, Product, ProductAvailability, RepricerRule, ShopSettings
from repricer.pricing import DecisionReason, PricingStrategy
from repricer.uploader import MerchantIdentity

NS = "{kaspiShopping}"
MERCHANT = MerchantIdentity("30123456", "Ромашка")
OTHER_MERCHANT = "99999999"
ALMATY, ASTANA = "750000000", "710000000"


API_KEY = "test-key"


def api_settings(**overrides: Any) -> ApiSettings:
    """Settings for a test, deliberately ignoring the developer's own .env.

    ``_env_file`` is a pydantic-settings argument its type stubs do not declare,
    hence the dict.
    """
    return ApiSettings(**{"_env_file": None, **overrides})


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    """The API wired to the test's transaction, so nothing it writes survives."""
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_merchant] = lambda: MERCHANT
    app.dependency_overrides[get_settings] = lambda: api_settings(repricer_api_key=API_KEY)
    with TestClient(app, headers={"X-API-Key": API_KEY}) as test_client:
        yield test_client


def make_product(
    session: Session,
    sku: str = "IPH-256",
    *,
    merchant_id: str = MERCHANT.merchant_id,
    title: str = "Apple iPhone 17 256Gb",
    brand: str | None = "Apple",
    base_price: int | None = None,
    stores: bool = True,
    active: bool = True,
    cities: dict[str, int | None] | None = None,
) -> Product:
    product = Product(
        merchant_id=merchant_id,
        sku=sku,
        kaspi_product_id="102298404",
        title=title,
        brand=brand,
        base_price=Decimal(base_price) if base_price is not None else None,
        is_active=active,
    )
    if stores:
        product.availabilities.append(ProductAvailability(store_id="PP1", stock_count=5))
    for city_id, price in (cities or {}).items():
        product.rules.append(
            RepricerRule(
                city_id=city_id,
                strategy=PricingStrategy.BEAT_FIRST,
                min_price=Decimal(300000),
                max_price=Decimal(500000),
                current_price=Decimal(price) if price is not None else None,
            )
        )
    session.add(product)
    session.flush()
    return product


def rule_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "product_sku": "IPH-256",
        "city_id": ALMATY,
        "strategy": "beat_first",
        "min_price": "300000",
        "max_price": "500000",
    }
    payload.update(overrides)
    return payload


# --- GET /api/rules -----------------------------------------------------------


def test_lists_products_with_their_rules_and_status(session: Session, client: TestClient) -> None:
    make_product(session, "IPH-256", base_price=400000, cities={ALMATY: 362000, ASTANA: 366000})
    make_product(session, "CASE-17", title="Чехол", cities={ALMATY: 4500})

    body = client.get("/api/rules").json()

    assert body["total"] == 2
    assert [item["sku"] for item in body["items"]] == ["CASE-17", "IPH-256"]
    phone = body["items"][1]
    assert phone["title"] == "Apple iPhone 17 256Gb"
    assert phone["base_price"] == "400000"
    assert [rule["city_id"] for rule in phone["rules"]] == [ASTANA, ALMATY]
    almaty = phone["rules"][1]
    assert (almaty["strategy"], almaty["current_price"]) == ("beat_first", "362000")
    assert almaty["is_active"] is True
    assert almaty["last_evaluated_at"] is None


def test_another_merchants_products_are_invisible(session: Session, client: TestClient) -> None:
    make_product(session, "MINE", cities={ALMATY: 1000})
    make_product(session, "THEIRS", merchant_id=OTHER_MERCHANT, cities={ALMATY: 1000})

    body = client.get("/api/rules").json()

    assert [item["sku"] for item in body["items"]] == ["MINE"]


def test_city_filter_narrows_products_and_their_rules(session: Session, client: TestClient) -> None:
    make_product(session, "BOTH", cities={ALMATY: 1000, ASTANA: 1100})
    make_product(session, "ALMATY-ONLY", cities={ALMATY: 900})

    body = client.get("/api/rules", params={"city_id": ASTANA}).json()

    assert [item["sku"] for item in body["items"]] == ["BOTH"]
    assert [rule["city_id"] for rule in body["items"][0]["rules"]] == [ASTANA]


def test_search_matches_sku_or_title(session: Session, client: TestClient) -> None:
    make_product(session, "IPH-256", cities={ALMATY: 1000})
    make_product(session, "CASE-17", title="Чехол для iPhone", cities={ALMATY: 500})

    assert client.get("/api/rules", params={"search": "iph"}).json()["total"] == 2
    assert client.get("/api/rules", params={"search": "CASE"}).json()["total"] == 1


def test_list_is_paginated(session: Session, client: TestClient) -> None:
    for index in range(5):
        make_product(session, f"SKU-{index}", cities={ALMATY: 1000})

    body = client.get("/api/rules", params={"limit": 2, "offset": 2}).json()

    assert (body["total"], body["limit"], body["offset"]) == (5, 2, 2)
    assert [item["sku"] for item in body["items"]] == ["SKU-2", "SKU-3"]


# --- POST /api/rules ----------------------------------------------------------


def test_creates_a_rule(session: Session, client: TestClient) -> None:
    make_product(session)

    response = client.post("/api/rules", json=rule_payload(step=10, ignored_merchants=["sister"]))

    assert response.status_code == 201
    created = response.json()
    assert (created["city_id"], created["strategy"], created["step"]) == (ALMATY, "beat_first", 10)
    assert created["ignored_merchants"] == ["sister"]
    rule = session.scalars(select(RepricerRule)).one()
    assert rule.city_id == ALMATY
    assert rule.min_price == Decimal(300000)


def test_creating_a_rule_for_an_unknown_sku_is_404(session: Session, client: TestClient) -> None:
    response = client.post("/api/rules", json=rule_payload(product_sku="NOPE"))

    assert response.status_code == 404
    assert "NOPE" in response.json()["detail"]


def test_two_rules_for_one_city_is_409(session: Session, client: TestClient) -> None:
    make_product(session, cities={ALMATY: 1000})

    response = client.post("/api/rules", json=rule_payload())

    assert response.status_code == 409


def test_target_position_strategy_requires_a_position(session: Session, client: TestClient) -> None:
    make_product(session)

    response = client.post("/api/rules", json=rule_payload(strategy="target_position"))

    assert response.status_code == 422
    assert "target_position" in response.text


def test_fixed_price_strategy_requires_a_base_price(session: Session, client: TestClient) -> None:
    make_product(session, base_price=None)

    response = client.post("/api/rules", json=rule_payload(strategy="fixed_price"))

    assert response.status_code == 422
    assert "base_price" in response.json()["detail"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"min_price": "0"},
        {"min_price": "-10"},
        {"max_price": "1000", "min_price": "2000"},
        {"step": 0},
        {"strategy": "undercut_everyone"},
        {"city_id": "almaty"},
        {"target_position": 21},
        {"target_position": 0},
        {"ignored_merchants": ["  "]},
    ],
)
def test_invalid_rule_payload_is_rejected(
    session: Session, client: TestClient, overrides: dict[str, Any]
) -> None:
    make_product(session)

    assert client.post("/api/rules", json=rule_payload(**overrides)).status_code == 422
    assert session.scalars(select(RepricerRule)).all() == []


def test_duplicate_ignored_merchants_are_collapsed(session: Session, client: TestClient) -> None:
    make_product(session)

    created = client.post(
        "/api/rules", json=rule_payload(ignored_merchants=["sister", "sister", " sister "])
    ).json()

    assert created["ignored_merchants"] == ["sister"]


# --- PUT /api/rules/{id} ------------------------------------------------------


def test_updates_a_rule(session: Session, client: TestClient) -> None:
    product = make_product(session, cities={ALMATY: 362000})
    rule_id = product.rules[0].id

    response = client.put(
        f"/api/rules/{rule_id}",
        json={
            "strategy": "target_position",
            "target_position": 3,
            "min_price": "310000",
            "max_price": "480000",
            "step": 5,
            "ignored_merchants": ["partner"],
            "is_active": False,
        },
    )

    assert response.status_code == 200
    session.expire_all()
    rule = session.get(RepricerRule, rule_id)
    assert rule is not None
    assert rule.strategy is PricingStrategy.TARGET_POSITION
    assert (rule.target_position, rule.step, rule.is_active) == (3, 5, False)
    assert rule.min_price == Decimal(310000)
    # The price the worker applied is state, not settings: an edit leaves it alone.
    assert rule.current_price == Decimal(362000)


def test_updating_an_unknown_rule_is_404(session: Session, client: TestClient) -> None:
    assert client.put("/api/rules/999", json=rule_payload()).status_code == 404


def test_cannot_update_another_merchants_rule(session: Session, client: TestClient) -> None:
    other = make_product(session, "THEIRS", merchant_id=OTHER_MERCHANT, cities={ALMATY: 1000})

    response = client.put(
        f"/api/rules/{other.rules[0].id}",
        json={"strategy": "beat_first", "min_price": "1", "max_price": "2"},
    )

    assert response.status_code == 404


# --- POST /api/rules/bulk-toggle ---------------------------------------------


def test_bulk_toggle_by_sku(session: Session, client: TestClient) -> None:
    product = make_product(session, cities={ALMATY: 1000, ASTANA: 1100})

    body = client.post(
        "/api/rules/bulk-toggle", json={"is_active": False, "product_skus": [product.sku]}
    ).json()

    assert body["updated"] == 2
    session.expire_all()
    assert [rule.is_active for rule in session.scalars(select(RepricerRule))] == [False, False]


def test_bulk_toggle_can_narrow_to_one_city(session: Session, client: TestClient) -> None:
    product = make_product(session, cities={ALMATY: 1000, ASTANA: 1100})

    body = client.post(
        "/api/rules/bulk-toggle",
        json={"is_active": False, "product_skus": [product.sku], "city_id": ASTANA},
    ).json()

    assert body["updated"] == 1
    session.expire_all()
    switched_off = session.scalars(select(RepricerRule).where(~RepricerRule.is_active)).one()
    assert switched_off.city_id == ASTANA


def test_bulk_toggle_by_rule_ids_counts_only_real_changes(
    session: Session, client: TestClient
) -> None:
    product = make_product(session, cities={ALMATY: 1000, ASTANA: 1100})
    ids = [rule.id for rule in product.rules]
    product.rules[0].is_active = False
    session.flush()

    body = client.post("/api/rules/bulk-toggle", json={"is_active": False, "rule_ids": ids}).json()

    assert body["updated"] == 1
    assert body["rule_ids"] == [ids[1]]


@pytest.mark.parametrize(
    "payload",
    [
        {"is_active": True},
        {"is_active": True, "rule_ids": [1], "product_skus": ["IPH-256"]},
        {"is_active": True, "rule_ids": [1], "city_id": ALMATY},
    ],
    ids=["neither", "both", "city-without-skus"],
)
def test_bulk_toggle_rejects_an_ambiguous_target(client: TestClient, payload: dict[str, Any]) -> None:
    assert client.post("/api/rules/bulk-toggle", json=payload).status_code == 422


# --- GET /api/history/{sku} ---------------------------------------------------


def add_history(session: Session, product: Product, new_price: int, city_id: str = ALMATY) -> None:
    session.add(
        PriceHistory(
            product_id=product.id,
            city_id=city_id,
            old_price=Decimal(new_price + 1),
            new_price=Decimal(new_price),
            strategy_used=PricingStrategy.BEAT_FIRST,
            reason=DecisionReason.STRATEGY_TARGET,
            expected_position=1,
            competitor_count=3,
            competitor_top1_merchant_id="rival",
            competitor_top1_price=Decimal(new_price + 1),
        )
    )
    session.flush()


def test_history_returns_changes_newest_first(session: Session, client: TestClient) -> None:
    product = make_product(session, cities={ALMATY: 1000})
    add_history(session, product, 362000)
    add_history(session, product, 361000)

    body = client.get(f"/api/history/{product.sku}").json()

    assert body["sku"] == product.sku
    assert body["total"] == 2
    first = body["items"][0]
    assert first["new_price"] == "361000"
    assert first["old_price"] == "361001"
    assert first["competitor_top1_price"] == "361001"
    assert first["competitor_top1_merchant_id"] == "rival"
    assert (first["strategy_used"], first["reason"]) == ("beat_first", "strategy_target")


def test_history_can_be_filtered_by_city(session: Session, client: TestClient) -> None:
    product = make_product(session, cities={ALMATY: 1000})
    add_history(session, product, 362000, city_id=ALMATY)
    add_history(session, product, 366000, city_id=ASTANA)

    body = client.get(f"/api/history/{product.sku}", params={"city_id": ASTANA}).json()

    assert body["total"] == 1
    assert body["items"][0]["city_id"] == ASTANA


def test_history_of_an_unknown_sku_is_404(client: TestClient) -> None:
    assert client.get("/api/history/NOPE").status_code == 404


def test_history_is_empty_for_a_product_that_never_moved(
    session: Session, client: TestClient
) -> None:
    product = make_product(session, cities={ALMATY: 1000})

    body = client.get(f"/api/history/{product.sku}").json()

    assert (body["total"], body["items"]) == (0, [])


# --- GET /feed/kaspi.xml ------------------------------------------------------


def test_feed_serves_the_current_prices(session: Session, client: TestClient) -> None:
    make_product(session, "IPH-256", base_price=400000, cities={ALMATY: 362000, ASTANA: 366000})

    response = client.get("/feed/kaspi.xml")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    root = ET.fromstring(response.content)
    assert root.findtext(f"{NS}merchantid") == MERCHANT.merchant_id
    offer = root.find(f"{NS}offers/{NS}offer")
    assert offer is not None
    assert offer.get("sku") == "IPH-256"
    # The product's own base price wins over the highest city price.
    assert offer.findtext(f"{NS}price") == "400000"
    assert {node.get("cityId"): node.text for node in offer.iter(f"{NS}cityprice")} == {
        ALMATY: "362000",
        ASTANA: "366000",
    }


def test_feed_repeats_the_same_etag_and_answers_304(session: Session, client: TestClient) -> None:
    make_product(session, cities={ALMATY: 362000})

    first = client.get("/feed/kaspi.xml")
    etag = first.headers["etag"]

    # The document's date changes every request, so the ETag must be built from
    # the offers instead, or Kaspi would re-download an unchanged catalogue.
    assert client.get("/feed/kaspi.xml").headers["etag"] == etag
    unchanged = client.get("/feed/kaspi.xml", headers={"If-None-Match": etag})
    assert unchanged.status_code == 304
    assert unchanged.content == b""


def test_feed_etag_changes_when_a_price_does(session: Session, client: TestClient) -> None:
    product = make_product(session, cities={ALMATY: 362000})
    etag = client.get("/feed/kaspi.xml").headers["etag"]

    product.rules[0].current_price = Decimal(361000)
    session.flush()

    assert client.get("/feed/kaspi.xml").headers["etag"] != etag


def test_feed_leaves_out_products_it_cannot_publish(session: Session, client: TestClient) -> None:
    make_product(session, "GOOD", cities={ALMATY: 362000})
    make_product(session, "NO-BRAND", brand=None, cities={ALMATY: 1000})
    make_product(session, "NO-STORE", stores=False, cities={ALMATY: 1000})
    make_product(session, "NO-PRICE", cities={ALMATY: None})
    make_product(session, "INACTIVE", active=False, cities={ALMATY: 1000})

    root = ET.fromstring(client.get("/feed/kaspi.xml").content)

    assert [node.get("sku") for node in root.iter(f"{NS}offer")] == ["GOOD"]


def test_feed_refuses_to_serve_an_empty_catalogue(session: Session, client: TestClient) -> None:
    make_product(session, "NO-BRAND", brand=None, cities={ALMATY: 1000})

    response = client.get("/feed/kaspi.xml")

    # An empty feed would take the whole shop off Kaspi, so it is never served.
    assert response.status_code == 503
    assert "empty feed" in response.json()["detail"]


# --- Documentation ------------------------------------------------------------


def test_openapi_documents_every_endpoint(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()

    assert schema["info"]["title"] == "Kaspi Repricer API"
    assert set(schema["paths"]) >= {
        "/api/rules",
        "/api/rules/{rule_id}",
        "/api/rules/bulk-toggle",
        "/api/history/{sku}",
        "/feed/kaspi.xml",
    }
    strategies = schema["components"]["schemas"]["PricingStrategy"]["enum"]
    assert set(strategies) == {
        "beat_first",
        "match_first",
        "follow_second",
        "target_position",
        "fixed_price",
        "manual",
    }


def test_swagger_ui_is_served(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200
    assert client.get("/health").json() == {"status": "ok"}


# --- Status for the dashboard -------------------------------------------------


def test_rules_carry_the_last_decision_for_the_badge(session: Session, client: TestClient) -> None:
    product = make_product(session, cities={ALMATY: 370112})
    add_history(session, product, 372000)
    add_history(session, product, 370112)  # the newer one

    rule = client.get("/api/rules").json()["items"][0]["rules"][0]

    assert rule["current_price"] == "370112"
    change = rule["last_change"]
    assert change["computed_price"] == "370112"
    assert change["expected_position"] == 1
    assert change["competitor_top1_price"] == "370113"
    assert change["competitor_top1_merchant_id"] == "rival"


def test_a_rule_that_never_moved_has_no_last_change(session: Session, client: TestClient) -> None:
    make_product(session, cities={ALMATY: 370112})

    assert client.get("/api/rules").json()["items"][0]["rules"][0]["last_change"] is None


def test_last_change_is_per_city(session: Session, client: TestClient) -> None:
    product = make_product(session, cities={ALMATY: 1000, ASTANA: 2000})
    add_history(session, product, 1000, city_id=ALMATY)
    add_history(session, product, 2000, city_id=ASTANA)

    rules = {rule["city_id"]: rule for rule in client.get("/api/rules").json()["items"][0]["rules"]}

    assert rules[ALMATY]["last_change"]["computed_price"] == "1000"
    assert rules[ASTANA]["last_change"]["computed_price"] == "2000"


def test_cities_are_served_for_the_picker(client: TestClient) -> None:
    cities = client.get("/api/cities").json()

    assert {"id": "750000000", "name": "Алматы"} in cities
    assert {"id": "710000000", "name": "Астана"} in cities
    assert {"id": "511010000", "name": "Шымкент"} in cities
    assert len(cities) >= 15


# --- The catalogue ------------------------------------------------------------


def product_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "sku": "IPH13-128",
        "title": "Apple iPhone 13 128Gb",
        "kaspi_product_id": "102298404",
        "brand": "Apple",
        "base_price": "399000",
        "availabilities": [{"store_id": "PP1", "stock_count": 4}],
    }
    payload.update(overrides)
    return payload


def test_a_product_can_be_added(session: Session, client: TestClient) -> None:
    response = client.post("/api/products", json=product_payload())

    assert response.status_code == 201
    created = response.json()
    assert created["sku"] == "IPH13-128"
    assert created["base_price"] == "399000"
    assert created["availabilities"] == [
        {"store_id": "PP1", "available": True, "stock_count": 4, "preorder_days": None}
    ]
    stored = session.scalars(select(Product)).one()
    assert stored.kaspi_product_id == "102298404"
    assert len(stored.availabilities) == 1


def test_the_same_sku_twice_is_409(session: Session, client: TestClient) -> None:
    client.post("/api/products", json=product_payload())

    assert client.post("/api/products", json=product_payload()).status_code == 409


@pytest.mark.parametrize(
    "overrides",
    [
        {"sku": ""},
        {"title": ""},
        {"kaspi_product_id": "abc"},
        {"kaspi_product_id": ""},
        {"base_price": "0"},
        {"availabilities": [{"store_id": "", "stock_count": 1}]},
        {"availabilities": [{"store_id": "PP1", "stock_count": -1}]},
    ],
)
def test_an_unusable_product_is_rejected(
    session: Session, client: TestClient, overrides: dict[str, Any]
) -> None:
    assert client.post("/api/products", json=product_payload(**overrides)).status_code == 422
    assert session.scalars(select(Product)).all() == []


def test_a_product_can_be_edited(session: Session, client: TestClient) -> None:
    client.post("/api/products", json=product_payload())

    response = client.put(
        "/api/products/IPH13-128",
        json=product_payload(
            title="Apple iPhone 13 128Gb Midnight",
            availabilities=[
                {"store_id": "PP1", "stock_count": 2},
                {"store_id": "PP2", "available": False, "preorder_days": 3},
            ],
        ),
    )

    assert response.status_code == 200
    session.expire_all()
    stored = session.scalars(select(Product)).one()
    assert stored.title == "Apple iPhone 13 128Gb Midnight"
    # The payload is the whole truth about stock: PP1 changed, PP2 appeared.
    assert {entry.store_id for entry in stored.availabilities} == {"PP1", "PP2"}


def test_a_product_cannot_be_renamed_through_put(session: Session, client: TestClient) -> None:
    client.post("/api/products", json=product_payload())

    response = client.put("/api/products/IPH13-128", json=product_payload(sku="OTHER"))

    assert response.status_code == 422


def test_import_adds_and_updates_in_one_go(session: Session, client: TestClient) -> None:
    client.post("/api/products", json=product_payload())

    response = client.post(
        "/api/products/import",
        json={
            "items": [
                product_payload(title="Обновлённое название"),
                product_payload(sku="CASE-13", title="Чехол", kaspi_product_id="112233445"),
            ]
        },
    )

    assert response.json() == {"created": 1, "updated": 1, "errors": []}
    session.expire_all()
    assert {product.sku for product in session.scalars(select(Product))} == {
        "IPH13-128",
        "CASE-13",
    }


def test_import_reports_a_duplicate_row_without_losing_the_rest(
    session: Session, client: TestClient
) -> None:
    response = client.post(
        "/api/products/import",
        json={
            "items": [
                product_payload(),
                product_payload(title="дубль"),
                product_payload(sku="CASE-13", kaspi_product_id="112233445"),
            ]
        },
    )

    body = response.json()
    assert (body["created"], body["updated"]) == (2, 0)
    assert body["errors"] == [{"sku": "IPH13-128", "reason": "дубль SKU в одной загрузке"}]


def test_the_list_explains_why_a_product_is_not_in_the_feed(
    session: Session, client: TestClient
) -> None:
    client.post("/api/products", json=product_payload(sku="NO-BRAND", brand=None))
    client.post("/api/products", json=product_payload(sku="NO-STOCK", availabilities=[]))
    client.post("/api/products", json=product_payload(sku="FINE"))

    blockers = {item["sku"]: item["feed_blocker"] for item in client.get("/api/products").json()}

    assert blockers["NO-BRAND"] == "не указан бренд"
    assert blockers["NO-STOCK"] == "нет складов с остатком"
    assert blockers["FINE"] == "нет правил по городам"


def test_only_blocked_narrows_the_list(session: Session, client: TestClient) -> None:
    make_product(session, "READY", cities={ALMATY: 1000})
    client.post("/api/products", json=product_payload(sku="NO-BRAND", brand=None))

    blocked = client.get("/api/products", params={"only_blocked": True}).json()

    assert [item["sku"] for item in blocked] == ["NO-BRAND"]


def test_another_merchants_product_is_not_visible(session: Session, client: TestClient) -> None:
    make_product(session, "THEIRS", merchant_id=OTHER_MERCHANT)

    assert client.get("/api/products").json() == []
    assert client.get("/api/products/THEIRS").status_code == 404


def test_a_rule_can_be_created_right_after_the_product(session: Session, client: TestClient) -> None:
    client.post("/api/products", json=product_payload())

    created = client.post(
        "/api/rules",
        json={
            "product_sku": "IPH13-128",
            "city_id": ALMATY,
            "strategy": "beat_first",
            "min_price": "330000",
            "max_price": "420000",
        },
    )

    assert created.status_code == 201


# --- The key ------------------------------------------------------------------


def unguarded_client(session: Session, api_key: str = API_KEY) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_merchant] = lambda: MERCHANT
    app.dependency_overrides[get_settings] = lambda: api_settings(repricer_api_key=api_key)
    return TestClient(app)


@pytest.mark.parametrize(
    "path", ["/api/rules", "/api/products", "/api/cities", "/api/history/IPH13-128"]
)
def test_no_key_means_no_data(session: Session, path: str) -> None:
    assert unguarded_client(session).get(path).status_code == 401


def test_a_wrong_key_is_refused(session: Session) -> None:
    client = unguarded_client(session)

    assert client.get("/api/rules", headers={"X-API-Key": "guess"}).status_code == 401


def test_writing_also_needs_the_key(session: Session) -> None:
    make_product(session, cities={ALMATY: 1000})

    response = unguarded_client(session).post(
        "/api/rules/bulk-toggle", json={"is_active": False, "product_skus": ["IPH-256"]}
    )

    assert response.status_code == 401
    session.expire_all()
    assert session.scalars(select(RepricerRule)).one().is_active is True


def test_without_a_configured_key_the_api_stays_shut(session: Session) -> None:
    response = unguarded_client(session, api_key="").get("/api/rules", headers={"X-API-Key": "x"})

    # Failing closed: an unconfigured deployment must not be an open one.
    assert response.status_code == 503
    assert "REPRICER_API_KEY" in response.json()["detail"]


def test_kaspi_can_still_fetch_the_feed_without_a_key(session: Session, client: TestClient) -> None:
    make_product(session, cities={ALMATY: 362000})

    response = unguarded_client(session).get("/feed/kaspi.xml")

    assert response.status_code == 200
    assert b"kaspi_catalog" in response.content


def test_the_health_probe_stays_open(session: Session) -> None:
    assert unguarded_client(session).get("/health").status_code == 200


# --- Catalogue and settings in one import -------------------------------------


def test_import_can_bring_rules_with_the_products(session: Session, client: TestClient) -> None:
    response = client.post(
        "/api/products/import",
        json={
            "items": [
                product_payload(
                    rules=[
                        {
                            "city_id": ALMATY,
                            "strategy": "beat_first",
                            "min_price": "330000",
                            "max_price": "420000",
                            "step": 5,
                        },
                        {
                            "city_id": ASTANA,
                            "strategy": "target_position",
                            "target_position": 3,
                            "min_price": "335000",
                            "max_price": "430000",
                        },
                    ]
                )
            ]
        },
    )

    assert response.json()["created"] == 1
    rules = {rule.city_id: rule for rule in session.scalars(select(RepricerRule))}
    assert rules[ALMATY].min_price == Decimal(330000)
    assert rules[ALMATY].step == 5
    assert rules[ASTANA].strategy is PricingStrategy.TARGET_POSITION
    assert rules[ASTANA].target_position == 3


def test_a_second_import_updates_the_rule_it_mentions(session: Session, client: TestClient) -> None:
    payload = product_payload(
        rules=[{"city_id": ALMATY, "min_price": "330000", "max_price": "420000"}]
    )
    client.post("/api/products/import", json={"items": [payload]})

    client.post(
        "/api/products/import",
        json={
            "items": [
                product_payload(
                    rules=[{"city_id": ALMATY, "min_price": "340000", "max_price": "420000"}]
                )
            ]
        },
    )

    session.expire_all()
    assert session.scalars(select(RepricerRule)).one().min_price == Decimal(340000)


def test_an_import_of_one_city_leaves_the_others_alone(session: Session, client: TestClient) -> None:
    client.post(
        "/api/products/import",
        json={
            "items": [
                product_payload(
                    rules=[
                        {"city_id": ALMATY, "min_price": "330000", "max_price": "420000"},
                        {"city_id": ASTANA, "min_price": "335000", "max_price": "430000"},
                    ]
                )
            ]
        },
    )

    client.post(
        "/api/products/import",
        json={
            "items": [
                product_payload(
                    rules=[{"city_id": ALMATY, "min_price": "331000", "max_price": "420000"}]
                )
            ]
        },
    )

    session.expire_all()
    # Astana was not mentioned the second time and must keep working.
    astana = session.scalars(
        select(RepricerRule).where(RepricerRule.city_id == ASTANA)
    ).one()
    assert astana.is_active and astana.min_price == Decimal(335000)


@pytest.mark.parametrize(
    "rule",
    [
        {"city_id": ALMATY, "min_price": "420000", "max_price": "330000"},
        {"city_id": ALMATY, "min_price": "0", "max_price": "420000"},
        {"city_id": "алматы", "min_price": "330000", "max_price": "420000"},
        {"city_id": ALMATY, "strategy": "target_position", "min_price": "1", "max_price": "2"},
    ],
)
def test_a_bad_rule_stops_the_whole_row(
    session: Session, client: TestClient, rule: dict[str, Any]
) -> None:
    response = client.post("/api/products/import", json={"items": [product_payload(rules=[rule])]})

    assert response.status_code == 422
    assert session.scalars(select(Product)).all() == []


# --- The status screen --------------------------------------------------------


def test_status_counts_what_is_ready_and_what_blocks(session: Session, client: TestClient) -> None:
    make_product(session, "READY", cities={ALMATY: 362000})
    make_product(session, "NO-BRAND", brand=None, cities={ALMATY: 1000})
    make_product(session, "NO-RULES")

    body = client.get("/api/status").json()

    assert body["products_total"] == 3
    assert body["products_ready"] == 1
    assert body["blockers"] == {"не указан бренд": 1, "нет правил по городам": 1}
    assert body["feed_ready"] is True
    assert body["rules_active"] == 2


def test_status_reports_the_bot_has_never_run(session: Session, client: TestClient) -> None:
    make_product(session, cities={ALMATY: None})

    body = client.get("/api/status").json()

    assert body["last_run_at"] is None
    assert body["changes_today"] == 0
    assert body["first_place"] == 0


def test_status_shows_first_places_and_todays_work(session: Session, client: TestClient) -> None:
    product = make_product(session, cities={ALMATY: 362000})
    add_history(session, product, 362000)
    product.rules[0].last_evaluated_at = datetime.now(UTC)
    session.flush()

    body = client.get("/api/status").json()

    assert body["first_place"] == 1
    assert body["changes_today"] == 1
    assert body["last_run_at"] is not None


def test_an_empty_shop_is_not_feed_ready(session: Session, client: TestClient) -> None:
    body = client.get("/api/status").json()

    assert body == {
        "products_total": 0,
        "products_ready": 0,
        "blockers": {},
        "rules_active": 0,
        "rules_paused": 0,
        "first_place": 0,
        "last_run_at": None,
        "changes_today": 0,
        "feed_ready": False,
    }


# --- Settings the owner fills in on the dashboard -----------------------------


def settings_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "merchant_id": "30123456",
        "company": 'ТОО "Ромашка"',
        "merchant_rating": 4.8,
        "proxies": ["http://user:pass@10.0.0.1:8080"],
        "worker_enabled": True,
        "interval_seconds": 300,
        "request_interval": 2,
        "telegram_chat_ids": ["42"],
    }
    payload.update(overrides)
    return payload


def test_settings_start_empty_and_not_ready(client: TestClient) -> None:
    body = client.get("/api/settings").json()

    assert body["merchant_id"] == ""
    assert body["is_ready"] is False
    assert body["worker_enabled"] is False
    assert body["telegram_configured"] is False


def test_settings_are_saved_and_read_back(session: Session, client: TestClient) -> None:
    saved = client.put("/api/settings", json=settings_payload()).json()

    assert saved["merchant_id"] == "30123456"
    assert saved["is_ready"] is True
    assert saved["worker_enabled"] is True
    assert saved["proxies"] == ["http://user:pass@10.0.0.1:8080"]
    stored = session.scalars(select(ShopSettings)).one()
    assert stored.merchant_rating == 4.8
    assert stored.telegram_chat_ids == ["42"]


def test_the_telegram_token_never_comes_back_in_full(client: TestClient) -> None:
    client.put("/api/settings", json=settings_payload(telegram_bot_token="123456:SECRETTOKEN"))

    body = client.get("/api/settings").json()

    assert body["telegram_configured"] is True
    assert body["telegram_token_hint"] == "…OKEN"
    assert "SECRETTOKEN" not in str(body)


def test_saving_other_fields_keeps_the_token(session: Session, client: TestClient) -> None:
    client.put("/api/settings", json=settings_payload(telegram_bot_token="123456:SECRET"))

    client.put("/api/settings", json=settings_payload(company="ТОО Другое"))

    session.expire_all()
    assert session.scalars(select(ShopSettings)).one().telegram_bot_token == "123456:SECRET"


def test_an_empty_token_clears_it(session: Session, client: TestClient) -> None:
    client.put("/api/settings", json=settings_payload(telegram_bot_token="123456:SECRET"))

    client.put("/api/settings", json=settings_payload(telegram_bot_token=""))

    session.expire_all()
    assert session.scalars(select(ShopSettings)).one().telegram_bot_token == ""


@pytest.mark.parametrize(
    "overrides",
    [
        {"worker_enabled": True, "merchant_id": ""},
        {"worker_enabled": True, "company": ""},
        {"interval_seconds": 30},
        {"merchant_rating": 7},
        {"telegram_chat_ids": ["не число"]},
        {"proxies": ["не-прокси"]},
    ],
)
def test_bad_settings_are_refused(client: TestClient, overrides: dict[str, Any]) -> None:
    assert client.put("/api/settings", json=settings_payload(**overrides)).status_code == 422


def test_the_worker_switch_can_be_turned_off_without_other_changes(
    session: Session, client: TestClient
) -> None:
    client.put("/api/settings", json=settings_payload())

    client.put("/api/settings", json=settings_payload(worker_enabled=False))

    session.expire_all()
    assert session.scalars(select(ShopSettings)).one().worker_enabled is False
