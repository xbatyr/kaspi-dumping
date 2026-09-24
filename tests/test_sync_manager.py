import xml.etree.ElementTree as ET
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from repricer.db import PriceHistory, Product, ProductAvailability, RepricerRule
from repricer.pricing import DecisionReason, PricingDecision, PricingStrategy
from repricer.uploader import (
    Availability,
    Catalog,
    CatalogItem,
    ExcludedOffer,
    MerchantIdentity,
    PriceUpdate,
    SyncManager,
)

NS = "{kaspiShopping}"
MERCHANT = MerchantIdentity("30123456", "Ромашка")
OTHER_MERCHANT = "99999999"
ALMATY, ASTANA = "750000000", "710000000"


class FakeStorage:
    def __init__(self) -> None:
        self.published: list[tuple[str, bytes]] = []

    def publish(self, filename: str, content: bytes) -> str:
        self.published.append((filename, content))
        return f"https://feeds.example.kz/{filename}"

    @property
    def last_feed(self) -> ET.Element:
        return ET.fromstring(self.published[-1][1])


class BrokenStorage:
    def publish(self, filename: str, content: bytes) -> str:
        raise OSError("bucket unreachable")


class StaticCatalog:
    """A CatalogSource standing in for an external system such as 1C."""

    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog

    def load(self, merchant_id: str) -> Catalog:
        return self.catalog


def make_product(
    session: Session,
    sku: str,
    *,
    merchant_id: str = MERCHANT.merchant_id,
    brand: str | None = "Apple",
    stores: bool = True,
    active: bool = True,
    prices: dict[str, int | None] | None = None,
    enabled: bool = True,
) -> Product:
    product = Product(
        merchant_id=merchant_id,
        sku=sku,
        kaspi_product_id="102298404",
        title=f"Model {sku}",
        brand=brand,
        is_active=active,
    )
    if stores:
        product.availabilities.append(ProductAvailability(store_id="PP1", stock_count=5))
    for city_id, price in (prices or {ALMATY: 362000}).items():
        product.rules.append(
            RepricerRule(
                city_id=city_id,
                strategy=PricingStrategy.BEAT_FIRST,
                min_price=Decimal(300000),
                max_price=Decimal(500000),
                current_price=Decimal(price) if price is not None else None,
                is_active=enabled,
            )
        )
    session.add(product)
    session.flush()
    return product


def decision(new_price: int, previous: int | None = None) -> PricingDecision:
    return PricingDecision(
        new_price=Decimal(new_price),
        previous_price=Decimal(previous) if previous is not None else None,
        strategy=PricingStrategy.BEAT_FIRST,
        reason=DecisionReason.STRATEGY_TARGET,
        expected_position=1,
        reference_offer=None,
        competitors=(),
    )


def rule_of(product: Product, city_id: str = ALMATY) -> RepricerRule:
    return next(rule for rule in product.rules if rule.city_id == city_id)


def city_prices_in(feed: ET.Element, sku: str) -> dict[str, str]:
    offer = next(node for node in feed.iter(f"{NS}offer") if node.get("sku") == sku)
    return {
        node.get("cityId") or "": node.text or "" for node in offer.iter(f"{NS}cityprice")
    }


def skus_in(feed: ET.Element) -> list[str]:
    return [node.get("sku") or "" for node in feed.iter(f"{NS}offer")]


@pytest.fixture
def manager_factory(session: Session) -> Any:
    from repricer.uploader import DatabaseCatalog

    def factory(storage: Any = None, **kwargs: Any) -> tuple[SyncManager, Any]:
        feed_storage = FakeStorage() if storage is None else storage
        catalog = kwargs.pop("catalog", None) or DatabaseCatalog(session)
        return SyncManager(catalog=catalog, storage=feed_storage, **kwargs), feed_storage

    return factory


# --- Delta ---------------------------------------------------------------------


def test_unchanged_price_publishes_nothing(session: Session, manager_factory: Any) -> None:
    product = make_product(session, "SKU-1", prices={ALMATY: 362000})
    manager, storage = manager_factory()
    rule = rule_of(product)

    result = manager.sync(session, MERCHANT, [PriceUpdate(rule, decision(362000))])

    assert (result.applied, result.skipped_unchanged, result.feed_url) == (0, 1, None)
    assert not result.published
    assert storage.published == []
    assert rule.current_price == Decimal(362000)
    assert session.scalar(select(func.count()).select_from(PriceHistory)) == 0
    # The rule was still looked at, and that is worth recording.
    assert rule.last_evaluated_at is not None


def test_changed_price_is_written_and_published(session: Session, manager_factory: Any) -> None:
    product = make_product(session, "SKU-1", prices={ALMATY: 362000})
    manager, storage = manager_factory()
    rule = rule_of(product)

    result = manager.sync(session, MERCHANT, [PriceUpdate(rule, decision(361999, previous=362000))])

    assert (result.applied, result.skipped_unchanged, result.offers_published) == (1, 0, 1)
    assert result.feed_url == "https://feeds.example.kz/kaspi-price-list-30123456.xml"
    assert rule.current_price == Decimal(361999)
    assert city_prices_in(storage.last_feed, "SKU-1") == {ALMATY: "361999"}


def test_history_row_records_the_price_from_the_database(session: Session, manager_factory: Any) -> None:
    product = make_product(session, "SKU-1", prices={ALMATY: 362000})
    manager, _ = manager_factory()

    # The decision was computed against a price that has since moved on.
    manager.sync(session, MERCHANT, [PriceUpdate(rule_of(product), decision(361999, previous=370000))])

    history = session.scalars(select(PriceHistory)).one()
    assert (history.old_price, history.new_price) == (Decimal(362000), Decimal(361999))
    assert history.city_id == ALMATY
    assert history.strategy_used is PricingStrategy.BEAT_FIRST


def test_a_rule_without_a_price_yet_counts_as_a_change(session: Session, manager_factory: Any) -> None:
    product = make_product(session, "SKU-1", prices={ALMATY: None})
    manager, storage = manager_factory()

    result = manager.sync(session, MERCHANT, [PriceUpdate(rule_of(product), decision(362000))])

    assert result.applied == 1
    assert session.scalars(select(PriceHistory)).one().old_price is None
    assert city_prices_in(storage.last_feed, "SKU-1") == {ALMATY: "362000"}


def test_force_republishes_when_nothing_changed(session: Session, manager_factory: Any) -> None:
    product = make_product(session, "SKU-1", prices={ALMATY: 362000})
    manager, storage = manager_factory()

    result = manager.sync(session, MERCHANT, [PriceUpdate(rule_of(product), decision(362000))], force=True)

    assert (result.applied, result.skipped_unchanged, result.offers_published) == (0, 1, 1)
    assert len(storage.published) == 1


def test_sync_without_updates_does_nothing(session: Session, manager_factory: Any) -> None:
    make_product(session, "SKU-1")
    manager, storage = manager_factory()

    assert manager.sync(session, MERCHANT, []).feed_url is None
    assert storage.published == []


# --- What lands in the feed ----------------------------------------------------


def test_feed_carries_the_whole_catalogue_not_only_the_changed_offer(
    session: Session, manager_factory: Any
) -> None:
    changed = make_product(session, "SKU-1", prices={ALMATY: 362000})
    make_product(session, "SKU-2", prices={ALMATY: 150000})
    manager, storage = manager_factory()

    manager.sync(session, MERCHANT, [PriceUpdate(rule_of(changed), decision(361999))])

    # Kaspi imports the feed as the whole assortment, so leaving SKU-2 out would
    # take it off sale.
    assert skus_in(storage.last_feed) == ["SKU-1", "SKU-2"]


def test_another_merchants_products_stay_out(session: Session, manager_factory: Any) -> None:
    product = make_product(session, "SKU-1")
    make_product(session, "SKU-OTHER", merchant_id=OTHER_MERCHANT)
    manager, storage = manager_factory()

    manager.sync(session, MERCHANT, [PriceUpdate(rule_of(product), decision(361999))])

    assert skus_in(storage.last_feed) == ["SKU-1"]


def test_city_prices_and_base_price_cover_every_enabled_city(
    session: Session, manager_factory: Any
) -> None:
    product = make_product(session, "SKU-1", prices={ALMATY: 362000, ASTANA: 365000})
    manager, storage = manager_factory()

    manager.sync(session, MERCHANT, [PriceUpdate(rule_of(product), decision(361999))])

    offer = next(iter(storage.last_feed.iter(f"{NS}offer")))
    assert city_prices_in(storage.last_feed, "SKU-1") == {ALMATY: "361999", ASTANA: "365000"}
    # The base price covers cities with no rule, so it takes the highest.
    assert offer.findtext(f"{NS}price") == "365000"


def test_base_price_can_be_left_out(session: Session, manager_factory: Any) -> None:
    product = make_product(session, "SKU-1", prices={ALMATY: 362000})
    manager, storage = manager_factory(include_base_price=False)

    manager.sync(session, MERCHANT, [PriceUpdate(rule_of(product), decision(361999))])

    offer = next(iter(storage.last_feed.iter(f"{NS}offer")))
    assert offer.find(f"{NS}price") is None
    assert city_prices_in(storage.last_feed, "SKU-1") == {ALMATY: "361999"}


def test_availabilities_come_from_the_database(session: Session, manager_factory: Any) -> None:
    product = make_product(session, "SKU-1")
    product.availabilities.append(
        ProductAvailability(store_id="PP2", available=False, preorder_days=3)
    )
    manager, storage = manager_factory()

    manager.sync(session, MERCHANT, [PriceUpdate(rule_of(product), decision(361999))])

    stores = [
        (node.get("storeId"), node.get("available"), node.get("stockCount"), node.get("preOrder"))
        for node in storage.last_feed.iter(f"{NS}availability")
    ]
    assert sorted(stores) == [("PP1", "yes", "5", None), ("PP2", "no", None, "3")]


def test_feed_name_follows_the_template(session: Session, manager_factory: Any) -> None:
    product = make_product(session, "SKU-1")
    manager, storage = manager_factory(filename_template="{merchant_id}-feed.xml")

    manager.sync(session, MERCHANT, [PriceUpdate(rule_of(product), decision(361999))])

    assert storage.published[0][0] == "30123456-feed.xml"


# --- Products that cannot be published ----------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"brand": None}, "no brand"),
        ({"stores": False}, "no pickup point"),
        ({"prices": {ALMATY: None}}, "no price for any city"),
        ({"enabled": False}, "no price for any city"),
    ],
)
def test_incomplete_product_is_left_out_with_a_reason(
    session: Session, manager_factory: Any, kwargs: dict[str, Any], reason: str
) -> None:
    changed = make_product(session, "SKU-1", prices={ALMATY: 362000})
    make_product(session, "SKU-BAD", **kwargs)
    manager, storage = manager_factory()

    result = manager.sync(session, MERCHANT, [PriceUpdate(rule_of(changed), decision(361999))])

    assert ExcludedOffer("SKU-BAD", reason) in result.excluded
    assert skus_in(storage.last_feed) == ["SKU-1"]


def test_inactive_product_is_left_out(session: Session, manager_factory: Any) -> None:
    changed = make_product(session, "SKU-1")
    make_product(session, "SKU-OLD", active=False)
    manager, storage = manager_factory()

    manager.sync(session, MERCHANT, [PriceUpdate(rule_of(changed), decision(361999))])

    assert skus_in(storage.last_feed) == ["SKU-1"]


def test_catalogue_with_nothing_publishable_refuses_to_publish(
    session: Session, manager_factory: Any
) -> None:
    product = make_product(session, "SKU-1", brand=None)
    manager, storage = manager_factory()

    with pytest.raises(ValueError, match="no offers"):
        manager.sync(session, MERCHANT, [PriceUpdate(rule_of(product), decision(361999))])
    assert storage.published == []


# --- External catalogue and failures ------------------------------------------


def test_catalogue_can_come_from_an_external_system(session: Session, manager_factory: Any) -> None:
    product = make_product(session, "SKU-1", brand=None, stores=False)
    external = StaticCatalog(
        Catalog(
            items=(
                CatalogItem(
                    sku="SKU-1",
                    model="iPhone 17 256Gb",
                    brand="Apple",
                    availabilities=(Availability("WAREHOUSE-1", stock_count=12),),
                ),
            ),
            excluded=(ExcludedOffer("SKU-9", "archived in 1C"),),
        )
    )
    manager, storage = manager_factory(catalog=external)

    result = manager.sync(session, MERCHANT, [PriceUpdate(rule_of(product), decision(361999))])

    offer = next(iter(storage.last_feed.iter(f"{NS}offer")))
    assert offer.findtext(f"{NS}brand") == "Apple"
    assert offer.find(f"{NS}availabilities/{NS}availability") is not None
    assert result.excluded == (ExcludedOffer("SKU-9", "archived in 1C"),)


def test_a_failed_upload_leaves_the_old_price_in_the_database(
    session: Session, manager_factory: Any
) -> None:
    product = make_product(session, "SKU-1", prices={ALMATY: 362000})
    session.commit()  # the state the sync starts from, so the rollback below keeps it
    manager, _ = manager_factory(BrokenStorage())
    rule = rule_of(product)

    with pytest.raises(OSError, match="bucket unreachable"):
        manager.sync(session, MERCHANT, [PriceUpdate(rule, decision(361999))])

    # The caller's transaction is still open, so rolling back keeps the database
    # and Kaspi in agreement, and the next run retries the change.
    session.rollback()
    assert session.scalars(select(RepricerRule)).one().current_price == Decimal(362000)
    assert session.scalar(select(func.count()).select_from(PriceHistory)) == 0
