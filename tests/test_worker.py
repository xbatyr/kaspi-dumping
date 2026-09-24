import threading
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Any, cast

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from repricer.db import PriceHistory, Product, ProductAvailability, RepricerRule
from repricer.pricing import DecisionReason, PricingStrategy
from repricer.scraper import KaspiClient, KaspiHTTPError, KaspiTransportError, Offer, ProxyPool
from repricer.uploader import DatabaseCatalog, MerchantIdentity, SyncManager
from repricer.worker import (
    CycleReport,
    RepricingWorker,
    RuleSnapshot,
    WorkerSettings,
    group_by_city,
)

NS = "{kaspiShopping}"
MERCHANT = MerchantIdentity("30123456", "Ромашка")
ALMATY, ASTANA = "750000000", "710000000"
IPHONE, CASE = "102298404", "112233445"


class FakeKaspiClient:
    """Serves scripted offer lists, or raises, per (product card, city)."""

    def __init__(self, responses: Mapping[tuple[str, str], list[Offer] | Exception]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, str]] = []
        self._lock = threading.Lock()
        self.closed = False

    def get_product_offers(self, product_id: str, city_id: str) -> list[Offer]:
        with self._lock:
            self.calls.append((product_id, city_id))
        outcome = self._responses.get((product_id, city_id), [])
        if isinstance(outcome, Exception):
            raise outcome
        return list(outcome)

    def close(self) -> None:
        self.closed = True


class FakeStorage:
    def __init__(self) -> None:
        self.published: list[tuple[str, bytes]] = []

    def publish(self, filename: str, content: bytes) -> str:
        self.published.append((filename, content))
        return f"https://feeds.example.kz/{filename}"

    @property
    def last_feed(self) -> ET.Element:
        return ET.fromstring(self.published[-1][1])


def competitor(merchant_id: str, price: int, rating: float | None = 4.5) -> Offer:
    return Offer(
        merchant_id=merchant_id,
        merchant_name=f"Shop {merchant_id}",
        price=Decimal(price),
        rating=rating,
        reviews_count=10,
        kaspi_delivery=True,
        delivery_duration="TOMORROW",
    )


def make_product(
    session: Session,
    sku: str,
    kaspi_product_id: str,
    *,
    cities: dict[str, int | None] | None = None,
    active: bool = True,
    enabled: bool = True,
    strategy: PricingStrategy = PricingStrategy.BEAT_FIRST,
    min_price: int = 300000,
    max_price: int = 500000,
    base_price: int | None = None,
    target_position: int | None = None,
) -> Product:
    product = Product(
        merchant_id=MERCHANT.merchant_id,
        sku=sku,
        kaspi_product_id=kaspi_product_id,
        title=f"Model {sku}",
        brand="Apple",
        base_price=Decimal(base_price) if base_price is not None else None,
        is_active=active,
    )
    product.availabilities.append(ProductAvailability(store_id="PP1", stock_count=5))
    for city_id, price in (cities or {ALMATY: 362000}).items():
        product.rules.append(
            RepricerRule(
                city_id=city_id,
                strategy=strategy,
                min_price=Decimal(min_price),
                max_price=Decimal(max_price),
                current_price=Decimal(price) if price is not None else None,
                target_position=target_position,
                is_active=enabled,
            )
        )
    session.add(product)
    session.flush()
    return product


@pytest.fixture
def session_factory(session: Session) -> Callable[[], Session]:
    """Sessions for the worker that join the test's transaction, so its commits
    are still rolled back when the test ends."""
    connection = session.connection()

    def factory() -> Session:
        return Session(bind=connection, join_transaction_mode="create_savepoint")

    return factory


@pytest.fixture
def build_worker(session_factory: Callable[[], Session]) -> Any:
    def build(
        client: FakeKaspiClient,
        *,
        storage: FakeStorage | None = None,
        proxy_pool: ProxyPool | None = None,
        alerts: Any = None,
        price_updates: Any = None,
        **settings: Any,
    ) -> tuple[RepricingWorker, FakeStorage, FakeKaspiClient]:
        feed_storage = storage or FakeStorage()
        worker = RepricingWorker(
            settings=WorkerSettings(merchant=MERCHANT, concurrency=settings.pop("concurrency", 2), **settings),
            session_factory=session_factory,
            client_factory=lambda: cast(KaspiClient, client),
            sync_manager_factory=lambda session: SyncManager(
                catalog=DatabaseCatalog(session), storage=feed_storage
            ),
            proxy_pool=proxy_pool,
            alerts=alerts,
            price_updates=price_updates,
        )
        return worker, feed_storage, client

    return build


def prices_in_feed(feed: ET.Element, sku: str) -> dict[str, str]:
    offer = next(node for node in feed.iter(f"{NS}offer") if node.get("sku") == sku)
    return {node.get("cityId") or "": node.text or "" for node in offer.iter(f"{NS}cityprice")}


# --- Grouping -----------------------------------------------------------------


def snapshot(sku: str, city_id: str) -> RuleSnapshot:
    return RuleSnapshot(
        rule_id=1,
        sku=sku,
        kaspi_product_id=IPHONE,
        city_id=city_id,
        current_price=None,
        config=cast(Any, None),
    )


def test_rules_are_grouped_by_city() -> None:
    grouped = group_by_city(
        [snapshot("A", ALMATY), snapshot("B", ASTANA), snapshot("C", ALMATY)]
    )

    assert {city: [item.sku for item in rules] for city, rules in grouped.items()} == {
        ALMATY: ["A", "C"],
        ASTANA: ["B"],
    }


# --- A full cycle -------------------------------------------------------------


def test_cycle_prices_every_rule_and_publishes_the_feed(
    session: Session, build_worker: Any
) -> None:
    make_product(session, "IPH", IPHONE, cities={ALMATY: 362000, ASTANA: 366000})
    make_product(session, "CASE", CASE, cities={ALMATY: 4500}, min_price=2000, max_price=9000)
    client = FakeKaspiClient(
        {
            (IPHONE, ALMATY): [competitor("rival", 361000), competitor(MERCHANT.merchant_id, 362000)],
            (IPHONE, ASTANA): [competitor("rival", 366000)],
            (CASE, ALMATY): [competitor("rival", 4400)],
        }
    )
    worker, storage, _ = build_worker(client)

    with worker:
        report = worker.run_once()

    assert (report.evaluated, report.changed, report.skipped) == (3, 3, 0)
    assert set(client.calls) == {(IPHONE, ALMATY), (IPHONE, ASTANA), (CASE, ALMATY)}
    session.expire_all()
    prices = {
        (rule.product.sku, rule.city_id): rule.current_price
        for rule in session.scalars(select(RepricerRule))
    }
    # Beat First undercuts the cheapest competitor by one tenge.
    assert prices == {
        ("IPH", ALMATY): Decimal(360999),
        ("IPH", ASTANA): Decimal(365999),
        ("CASE", ALMATY): Decimal(4399),
    }
    assert prices_in_feed(storage.last_feed, "IPH") == {ALMATY: "360999", ASTANA: "365999"}
    assert session.scalar(select(func.count()).select_from(PriceHistory)) == 3


def test_our_own_offer_is_not_treated_as_a_competitor(session: Session, build_worker: Any) -> None:
    make_product(session, "IPH", IPHONE, cities={ALMATY: 362000})
    client = FakeKaspiClient(
        {(IPHONE, ALMATY): [competitor(MERCHANT.merchant_id, 362000), competitor("rival", 370000)]}
    )
    sink = FakeSink()
    worker, storage, _ = build_worker(client, price_updates=sink)

    with worker:
        first = worker.run_once()
        second = worker.run_once()

    session.expire_all()
    assert session.scalars(select(RepricerRule)).one().current_price == Decimal(362000)
    assert first.changed == second.changed == 0
    assert storage.published == []
    assert sink.messages == []


def test_pending_xml_price_in_first_place_does_not_spam(
    session: Session, build_worker: Any
) -> None:
    make_product(session, "IPH", IPHONE, cities={ALMATY: 362000})
    client = FakeKaspiClient(
        {(IPHONE, ALMATY): [competitor(MERCHANT.merchant_id, 390000), competitor("rival", 370000)]}
    )
    sink = FakeSink()
    worker, storage, _ = build_worker(client, price_updates=sink)

    with worker:
        first = worker.run_once()
        second = worker.run_once()

    assert first.changed == second.changed == 0
    assert session.scalars(select(RepricerRule)).one().current_price == Decimal(362000)
    assert storage.published == []
    assert sink.messages == []


def test_unchanged_price_is_reported_but_publishes_nothing(
    session: Session, build_worker: Any
) -> None:
    make_product(session, "IPH", IPHONE, cities={ALMATY: 369999})
    client = FakeKaspiClient({(IPHONE, ALMATY): [competitor("rival", 370000)]})
    worker, storage, _ = build_worker(client)

    with worker:
        report = worker.run_once()

    assert (report.evaluated, report.changed, report.unchanged) == (1, 0, 1)
    assert storage.published == []
    assert session.scalar(select(func.count()).select_from(PriceHistory)) == 0


def test_cycle_without_rules_does_nothing(session: Session, build_worker: Any) -> None:
    client = FakeKaspiClient({})
    worker, storage, _ = build_worker(client)

    with worker:
        report = worker.run_once()

    assert report == CycleReport(dry_run=False, duration=report.duration)
    assert client.calls == []
    assert storage.published == []


def test_disabled_rules_and_inactive_products_are_never_fetched(
    session: Session, build_worker: Any
) -> None:
    make_product(session, "OFF", IPHONE, enabled=False)
    make_product(session, "GONE", CASE, active=False)
    client = FakeKaspiClient({})
    worker, _, _ = build_worker(client)

    with worker:
        worker.run_once()

    assert client.calls == []


# --- Nothing resets a price ---------------------------------------------------


@pytest.mark.parametrize(
    ("outcome", "unreachable"),
    [
        ([], False),
        (KaspiHTTPError(404), False),
        (KaspiTransportError("every proxy timed out"), True),
        (RuntimeError("something nobody predicted"), False),
    ],
    ids=["no-offers", "http-error", "transport-error", "unexpected"],
)
def test_a_sku_that_cannot_be_priced_keeps_its_price(
    session: Session, build_worker: Any, outcome: Any, unreachable: bool
) -> None:
    make_product(session, "IPH", IPHONE, cities={ALMATY: 362000})
    make_product(session, "CASE", CASE, cities={ALMATY: 4500}, min_price=2000, max_price=9000)
    client = FakeKaspiClient(
        {(IPHONE, ALMATY): outcome, (CASE, ALMATY): [competitor("rival", 4400)]}
    )
    worker, storage, _ = build_worker(client)

    with worker:
        report = worker.run_once()

    assert (report.skipped, report.unreachable) == (1, 1 if unreachable else 0)
    session.expire_all()
    prices = {rule.product.sku: rule.current_price for rule in session.scalars(select(RepricerRule))}
    # An empty list would otherwise read as "no competitors" and jump to max_price.
    assert prices["IPH"] == Decimal(362000)
    # The rest of the cycle still runs.
    assert prices["CASE"] == Decimal(4399)
    assert prices_in_feed(storage.last_feed, "IPH") == {ALMATY: "362000"}


def test_history_is_written_only_for_prices_that_moved(
    session: Session, build_worker: Any
) -> None:
    make_product(session, "IPH", IPHONE, cities={ALMATY: 362000})
    client = FakeKaspiClient({(IPHONE, ALMATY): [competitor("rival", 361000)]})
    worker, _, _ = build_worker(client)

    with worker:
        worker.run_once()

    history = session.scalars(select(PriceHistory)).one()
    assert (history.old_price, history.new_price) == (Decimal(362000), Decimal(360999))
    assert history.competitor_top1_merchant_id == "rival"
    assert history.strategy_used is PricingStrategy.BEAT_FIRST


# --- Dry run ------------------------------------------------------------------


def test_dry_run_changes_nothing(session: Session, build_worker: Any) -> None:
    make_product(session, "IPH", IPHONE, cities={ALMATY: 362000})
    client = FakeKaspiClient({(IPHONE, ALMATY): [competitor("rival", 361000)]})
    worker, storage, _ = build_worker(client, dry_run=True)

    with worker:
        report = worker.run_once()

    assert (report.evaluated, report.changed, report.dry_run) == (1, 1, True)
    assert report.feed_url is None
    assert storage.published == []
    session.expire_all()
    assert session.scalars(select(RepricerRule)).one().current_price == Decimal(362000)
    assert session.scalar(select(func.count()).select_from(PriceHistory)) == 0


# --- Proxy alerts -------------------------------------------------------------


def test_all_proxies_benched_raises_a_critical_alert(
    session: Session, build_worker: Any, warnings_logged: list[str]
) -> None:
    make_product(session, "IPH", IPHONE)
    pool = ProxyPool(["http://10.0.0.1:8080"])
    pool.report_failure("http://10.0.0.1:8080")
    client = FakeKaspiClient({(IPHONE, ALMATY): KaspiTransportError("proxy dead")})
    worker, _, _ = build_worker(client, proxy_pool=pool)

    with worker:
        worker.run_once()

    assert any("all exit IPs are blocked or dead" in message for message in warnings_logged)


def test_widespread_unreachability_raises_a_critical_alert(
    session: Session, build_worker: Any, warnings_logged: list[str]
) -> None:
    make_product(session, "IPH", IPHONE)
    make_product(session, "CASE", CASE)
    client = FakeKaspiClient(
        {
            (IPHONE, ALMATY): KaspiTransportError("timed out"),
            (CASE, ALMATY): KaspiTransportError("timed out"),
        }
    )
    worker, _, _ = build_worker(client)

    with worker:
        report = worker.run_once()

    assert report.unreachable == 2
    assert any("could not reach Kaspi" in message for message in warnings_logged)


def test_a_few_failures_do_not_raise_an_alert(
    session: Session, build_worker: Any, warnings_logged: list[str]
) -> None:
    for index in range(5):
        make_product(session, f"SKU-{index}", f"10000000{index}")
    client = FakeKaspiClient(
        {("100000000", ALMATY): KaspiTransportError("timed out")}
        | {(f"10000000{index}", ALMATY): [competitor("rival", 370000)] for index in range(1, 5)}
    )
    worker, _, _ = build_worker(client, failure_alert_ratio=0.5)

    with worker:
        report = worker.run_once()

    assert report.unreachable == 1
    assert not any("could not reach Kaspi" in message for message in warnings_logged)


# --- Threads and the loop -----------------------------------------------------


def test_each_thread_keeps_one_client(session: Session, session_factory: Any) -> None:
    make_product(session, "IPH", IPHONE)
    make_product(session, "CASE", CASE)
    created: list[FakeKaspiClient] = []
    client = FakeKaspiClient({})

    def client_factory() -> KaspiClient:
        created.append(client)
        return cast(KaspiClient, client)

    worker = RepricingWorker(
        settings=WorkerSettings(merchant=MERCHANT, concurrency=1),
        session_factory=session_factory,
        client_factory=client_factory,
        sync_manager_factory=lambda s: SyncManager(catalog=DatabaseCatalog(s), storage=FakeStorage()),
    )
    with worker:
        worker.run_once()
        worker.run_once()

    # One thread, one client, reused across both cycles and closed at the end.
    assert len(created) == 1
    assert client.closed


def test_run_forever_stops_when_asked(session: Session, build_worker: Any) -> None:
    worker, _, _ = build_worker(FakeKaspiClient({}))
    stop = threading.Event()
    cycles = 0

    def run_once() -> CycleReport:
        nonlocal cycles
        cycles += 1
        stop.set()
        return CycleReport()

    with worker:
        worker.run_once = run_once
        worker.run_forever(interval=0.01, stop=stop)

    assert cycles == 1


def test_a_failing_cycle_does_not_kill_the_loop(session: Session, build_worker: Any) -> None:
    worker, _, _ = build_worker(FakeKaspiClient({}))
    stop = threading.Event()
    attempts = 0

    def run_once() -> CycleReport:
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            stop.set()
        raise RuntimeError("database is down")

    with worker:
        worker.run_once = run_once
        worker.run_forever(interval=0.01, stop=stop)

    assert attempts == 2


# --- Strategies that need no competitor data ---------------------------------


def test_fixed_price_rules_are_priced_without_any_request(
    session: Session, build_worker: Any
) -> None:
    make_product(
        session,
        "IPH",
        IPHONE,
        cities={ALMATY: 362000},
        strategy=PricingStrategy.FIXED_PRICE,
        base_price=380000,
    )
    client = FakeKaspiClient({})
    worker, storage, _ = build_worker(client)

    with worker:
        report = worker.run_once()

    assert client.calls == []  # holding a fixed price needs no competitors
    assert (report.evaluated, report.changed) == (1, 1)
    session.expire_all()
    assert session.scalars(select(RepricerRule)).one().current_price == Decimal(380000)
    assert prices_in_feed(storage.last_feed, "IPH") == {ALMATY: "380000"}


def test_manual_rules_are_never_touched(session: Session, build_worker: Any) -> None:
    make_product(session, "IPH", IPHONE, cities={ALMATY: 362000}, strategy=PricingStrategy.MANUAL)
    client = FakeKaspiClient({})
    worker, storage, _ = build_worker(client)

    with worker:
        report = worker.run_once()

    assert client.calls == []
    assert (report.evaluated, report.skipped) == (0, 1)
    assert storage.published == []
    session.expire_all()
    assert session.scalars(select(RepricerRule)).one().current_price == Decimal(362000)


def test_target_position_rules_are_priced_from_the_market(
    session: Session, build_worker: Any
) -> None:
    make_product(
        session,
        "IPH",
        IPHONE,
        cities={ALMATY: 400000},
        strategy=PricingStrategy.TARGET_POSITION,
        target_position=2,
    )
    client = FakeKaspiClient(
        {(IPHONE, ALMATY): [competitor("a", 360000), competitor("b", 370000)]}
    )
    worker, _, _ = build_worker(client)

    with worker:
        worker.run_once()

    session.expire_all()
    # Just ahead of whoever holds second place.
    assert session.scalars(select(RepricerRule)).one().current_price == Decimal(369999)


def test_a_misconfigured_rule_is_skipped_not_fatal(
    session: Session, build_worker: Any, warnings_logged: list[str]
) -> None:
    # fixed_price without a base price: the API refuses to create this, but a
    # product whose base price was cleared later would look exactly like it.
    make_product(session, "BROKEN", IPHONE, strategy=PricingStrategy.FIXED_PRICE)
    make_product(session, "FINE", CASE, cities={ALMATY: 4500}, min_price=2000, max_price=9000)
    client = FakeKaspiClient({(CASE, ALMATY): [competitor("rival", 4400)]})
    worker, _, _ = build_worker(client)

    with worker:
        report = worker.run_once()

    assert report.evaluated == 1
    assert any("misconfigured" in message for message in warnings_logged)
    session.expire_all()
    prices = {rule.product.sku: rule.current_price for rule in session.scalars(select(RepricerRule))}
    assert prices["FINE"] == Decimal(4399)


# --- Telegram alerts ----------------------------------------------------------


class FakeSink:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send(self, text: str) -> None:
        self.messages.append(text)


def test_price_updates_are_sent_once_after_a_real_change(session: Session, build_worker: Any) -> None:
    make_product(session, "IPH", IPHONE, cities={ALMATY: 362000})
    client = FakeKaspiClient({(IPHONE, ALMATY): [competitor("rival", 361000)]})
    sink = FakeSink()
    worker, _, _ = build_worker(client, price_updates=sink)

    with worker:
        worker.run_once()
        worker.run_once()

    assert len(sink.messages) == 1
    assert "IPH" in sink.messages[0]
    assert "362 000 ₸" in sink.messages[0]
    assert "360 999 ₸" in sink.messages[0]


def add_history(session: Session, product: Product, position: int, city_id: str = ALMATY) -> None:
    session.add(
        PriceHistory(
            product_id=product.id,
            city_id=city_id,
            new_price=Decimal(370112),
            strategy_used=PricingStrategy.BEAT_FIRST,
            reason=DecisionReason.STRATEGY_TARGET,
            expected_position=position,
            competitor_count=5,
        )
    )
    session.flush()


def test_stop_loss_raises_one_alert_and_then_keeps_quiet(
    session: Session, build_worker: Any
) -> None:
    # Every competitor is below our floor, so the engine pins us to min_price.
    make_product(session, "IPH", IPHONE, cities={ALMATY: 320000}, min_price=300000)
    client = FakeKaspiClient({(IPHONE, ALMATY): [competitor("dumper", 250000)]})
    sink = FakeSink()
    worker, _, _ = build_worker(client, alerts=sink)

    with worker:
        worker.run_once()
        worker.run_once()

    assert len(sink.messages) == 1
    assert "Достигнут стоп-лосс" in sink.messages[0]
    assert "IPH" in sink.messages[0] and "300 000 ₸" in sink.messages[0]


def test_losing_first_place_names_the_competitor(session: Session, build_worker: Any) -> None:
    product = make_product(
        session,
        "IPH",
        IPHONE,
        cities={ALMATY: 362000},
        strategy=PricingStrategy.FOLLOW_SECOND,
    )
    add_history(session, product, position=1)  # we were first at the last change
    client = FakeKaspiClient({(IPHONE, ALMATY): [competitor("rival", 361000)]})
    sink = FakeSink()
    worker, _, _ = build_worker(client, alerts=sink)

    with worker:
        worker.run_once()

    assert len(sink.messages) == 1
    assert "Потеряно первое место" in sink.messages[0]
    assert "Shop rival" in sink.messages[0]  # the shop name, not just its ID
    assert "361 000 ₸" in sink.messages[0]


def test_staying_first_raises_nothing(session: Session, build_worker: Any) -> None:
    product = make_product(session, "IPH", IPHONE, cities={ALMATY: 362000})
    add_history(session, product, position=1)
    client = FakeKaspiClient({(IPHONE, ALMATY): [competitor("rival", 370000)]})
    sink = FakeSink()
    worker, _, _ = build_worker(client, alerts=sink)

    with worker:
        worker.run_once()

    assert sink.messages == []


def test_a_dry_run_never_sends_alerts(session: Session, build_worker: Any) -> None:
    make_product(session, "IPH", IPHONE, cities={ALMATY: 320000}, min_price=300000)
    client = FakeKaspiClient({(IPHONE, ALMATY): [competitor("dumper", 250000)]})
    sink = FakeSink()
    worker, _, _ = build_worker(client, alerts=sink, dry_run=True)

    with worker:
        worker.run_once()

    assert sink.messages == []
