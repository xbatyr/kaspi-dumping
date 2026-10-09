import threading
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from repricer.db import PriceHistory, Product, ProductAvailability, RepricerRule
from repricer.db.settings_store import load_settings
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
        # These pricing fixtures model products that are listed on Kaspi. Old
        # abbreviated rival-only pages must include their own listing too.
        # Tests for actual withdrawal explicitly set include_own_listing=False.
        if settings.pop("include_own_listing", True):
            pages = dict(client._responses)
            with session_factory() as db:
                for rule, product in db.execute(select(RepricerRule, Product).join(
                    Product, Product.id == RepricerRule.product_id)):
                    key = (product.kaspi_product_id, rule.city_id)
                    page = pages.get(key)
                    if isinstance(page, list) and page and not any(o.merchant_id == MERCHANT.merchant_id for o in page):
                        pages[key] = [*page, competitor(MERCHANT.merchant_id, int(rule.current_price or product.base_price or rule.max_price))]
            client._responses = pages
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

@pytest.mark.parametrize("leader_id", [MERCHANT.merchant_id, "rival"])
def test_market_leader_name_matches_displayed_price(session: Session, build_worker: Any, leader_id: str) -> None:
    product = make_product(session, "LEADER", IPHONE)
    other_id = "rival" if leader_id == MERCHANT.merchant_id else MERCHANT.merchant_id
    client = FakeKaspiClient({(IPHONE, ALMATY): [
        competitor(other_id, 410000), competitor(leader_id, 360000),
    ]})
    worker, _, _ = build_worker(client)
    worker.run_once()
    session.expire_all()
    market = product.rules[0].market_snapshot
    assert market is not None
    assert market["leader_price"] == "360000"
    assert market["leader_merchant_id"] == leader_id
    assert market["leader_name"] == f"Shop {leader_id}"


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


def test_prices_from_what_kaspi_shows_and_then_holds_still(
    session: Session, build_worker: Any
) -> None:
    # The feed says 362 000, Kaspi still shows our 390 000 against a rival at
    # 370 000. Like AlgaTop, the bot works from the price shoppers see: 369 999
    # is all it takes to be first, so the deeper feed discount is not kept, and
    # while Kaspi catches up the next pass changes nothing.
    make_product(session, "IPH", IPHONE, cities={ALMATY: 362000})
    client = FakeKaspiClient(
        {(IPHONE, ALMATY): [competitor(MERCHANT.merchant_id, 390000), competitor("rival", 370000)]}
    )
    sink = FakeSink()
    worker, storage, _ = build_worker(client, price_updates=sink)

    with worker:
        first = worker.run_once()
        second = worker.run_once()

    assert (first.changed, second.changed) == (1, 0)
    session.expire_all()
    assert session.scalars(select(RepricerRule)).one().current_price == Decimal(369999)
    assert len(storage.published) == 1
    assert len(sink.messages) == 1


@pytest.mark.parametrize(
    ("rivals", "expected"),
    [
        # The competition left: the raised price is what the owner wanted.
        ([], 500000),
        # A rival is still there: the bot lowers from the raised price, not
        # from the old one Kaspi still shows.
        ([competitor("rival", 450000)], 500000),
    ],
)
def test_a_price_raised_by_hand_is_not_undone_before_kaspi_fetches_it(
    session: Session, build_worker: Any, rivals: list[Offer], expected: int
) -> None:
    from repricer.api.routes.tools import _raise_to_max

    product = make_product(session, "IPH", IPHONE, cities={ALMATY: 370000})
    ours = competitor(MERCHANT.merchant_id, 370000)
    worker, _, _ = build_worker(
        FakeKaspiClient({(IPHONE, ALMATY): [ours, competitor("rival", 362001)]})
    )
    with worker:
        worker.run_once()  # the bot's own change: 362 000
    session.expire_all()
    assert product.rules[0].current_price == Decimal(362000)

    # «Поднять до максимальных», while Kaspi still shows the old 370 000.
    _raise_to_max(product)
    session.flush()
    worker, _, _ = build_worker(FakeKaspiClient({(IPHONE, ALMATY): [ours, *rivals]}))
    with worker:
        worker.run_once()

    session.expire_all()
    assert product.rules[0].current_price == Decimal(expected)


@pytest.mark.parametrize(("ignore", "expected"), [(False, 369999), (True, 380000)])
def test_intercity_rivals_can_be_left_out_of_the_competition(
    session: Session, build_worker: Any, ignore: bool, expected: int
) -> None:
    # 164612291-like: the cheaper store ships from another city in five days,
    # we deliver tomorrow in the buyer's city.
    make_product(session, "IPH", IPHONE, cities={ALMATY: 380000})
    shop = load_settings(session)
    shop.ignore_intercity_rivals = ignore
    session.flush()
    ours = replace(competitor(MERCHANT.merchant_id, 380000), intercity=False)
    far = replace(competitor("far", 370000), intercity=True)
    worker, _, _ = build_worker(FakeKaspiClient({(IPHONE, ALMATY): [far, ours]}))

    with worker:
        worker.run_once()

    session.expire_all()
    assert session.scalars(select(RepricerRule)).one().current_price == Decimal(expected)


@pytest.mark.parametrize(("ignore", "own_days", "rival_days", "expected"), [
    (True, 0, 1, 342988), (False, 0, 1, 283999),
    (True, 1, 1, 283999), (True, 1, 0, 283999),
])
def test_local_but_slower_luxe_does_not_force_a_price_cut(
    session: Session, build_worker: Any, ignore: bool, own_days: int, rival_days: int, expected: int
) -> None:
    make_product(session, "100733126_212707688", IPHONE, cities={ASTANA: 342988}, min_price=255599)
    load_settings(session).ignore_intercity_rivals = ignore
    session.flush()
    ours = replace(competitor(MERCHANT.merchant_id, 342988), intercity=False, delivery_days=own_days)
    luxe = replace(competitor("LUXE", 284000), intercity=False, delivery_days=rival_days)
    worker, _, _ = build_worker(FakeKaspiClient({(IPHONE, ASTANA): [luxe, ours]}))
    with worker:
        worker.run_once()
    session.expire_all()
    assert session.scalars(select(RepricerRule)).one().current_price == Decimal(expected)


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
    assert "Не хватает минимальной цены" in sink.messages[0]
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


# --- Margin first: intercity stores, raising, test mode -----------------------


def card_from_the_screenshot() -> list[Offer]:
    """109841597 in Astana on 2026-09-30: we deliver today, most rivals in days."""
    def seller(merchant_id: str, price: int, intercity: bool | None) -> Offer:
        return replace(competitor(merchant_id, price), intercity=intercity)

    return [
        seller("cyber-bro", 455555, True),
        seller("moon", 462899, True),
        seller(MERCHANT.merchant_id, 462900, False),
        seller("luxe", 463000, False),
        seller("gstore", 499990, True),
    ]


def changed_at(session: Session, product: Product, price: int, ago: timedelta) -> None:
    session.add(
        PriceHistory(
            product_id=product.id,
            city_id=ALMATY,
            new_price=Decimal(price),
            strategy_used=PricingStrategy.BEAT_FIRST,
            reason=DecisionReason.STRATEGY_TARGET,
            expected_position=1,
            competitor_count=4,
            created_at=datetime.now(UTC) - ago,
        )
    )
    session.flush()


@pytest.mark.parametrize(("raise_on", "expected"), [(False, 462900), (True, 462999)])
def test_the_price_is_never_cut_under_a_store_that_ships_from_another_city(
    session: Session, build_worker: Any, raise_on: bool, expected: int
) -> None:
    # It went 462 897 -> 455 553 under a 3-star seller delivering in three days.
    product = make_product(session, "PC", IPHONE, cities={ALMATY: 462900},
                           min_price=400000, max_price=520000)
    product.auto_increase = raise_on
    session.flush()
    worker, _, _ = build_worker(FakeKaspiClient({(IPHONE, ALMATY): card_from_the_screenshot()}))

    with worker:
        worker.run_once()

    session.expire_all()
    # First among the local stores: kept, or raised to just under LUXE.
    assert product.rules[0].current_price == Decimal(expected)


@pytest.mark.parametrize(("stock", "available", "active"), [(0, True, True), (5, False, True), (5, True, False)])
def test_withdrawn_products_are_not_even_scraped(session: Session, build_worker: Any, stock: int, available: bool, active: bool) -> None:
    product = make_product(session, "WITHDRAWN", IPHONE, active=active)
    product.availabilities[0].stock_count = stock
    product.availabilities[0].available = available
    session.flush()
    worker, storage, client = build_worker(FakeKaspiClient({(IPHONE, ALMATY): [competitor("rival", 340000)]}))
    with worker:
        worker.run_once()
    assert client.calls == [] and storage.published == []


def test_missing_own_listing_is_never_repriced(session: Session, build_worker: Any) -> None:
    product = make_product(session, "WITHDRAWN_ON_KASPI", IPHONE)
    worker, storage, _ = build_worker(FakeKaspiClient({(IPHONE, ALMATY): [competitor("rival", 340000)]}), include_own_listing=False)
    with worker:
        report = worker.run_once()
    session.expire_all()
    assert report.skipped == 1 and storage.published == []
    assert product.rules[0].current_price == Decimal(362000)
    assert product.rules[0].market_snapshot is not None
    assert product.rules[0].market_snapshot["observed_price"] is None


@pytest.mark.parametrize(("rival_price", "current", "maximum", "expected"), [
    (100000, 110000, 160000, 120000), (85000, 110000, 160000, 110000),
    (90000, 100000, 160000, 108000), (100000, 110000, 115000, 115000),
    (100000, 125000, 160000, 125000), (85000, 110000, 105000, 105000),
])
def test_astana_foreign_city_premium_examples(session: Session, build_worker: Any, rival_price: int, current: int, maximum: int, expected: int) -> None:
    product = make_product(session, "PREMIUM", IPHONE, cities={ASTANA: current}, min_price=90000, max_price=maximum)
    product.auto_increase = True
    session.flush()
    ours = replace(competitor(MERCHANT.merchant_id, current), intercity=False)
    far = replace(competitor("far", rival_price), intercity=True)
    worker, _, _ = build_worker(FakeKaspiClient({(IPHONE, ASTANA): [far, ours]}))
    with worker:
        worker.run_once()
    session.expire_all()
    assert product.rules[0].current_price == Decimal(expected)


def test_foreign_premium_uses_the_owners_min_without_replacing_the_safe_price_floor(session: Session, build_worker: Any) -> None:
    product = make_product(session, "COST-PREMIUM", IPHONE, cities={ASTANA: 100000}, min_price=90000, max_price=150000)
    product.purchase_price = Decimal(93000)
    product.auto_increase = True
    session.flush()
    offers = [replace(competitor(MERCHANT.merchant_id, 100000), intercity=False),
              replace(competitor("far", 92000), intercity=True)]
    worker, _, _ = build_worker(FakeKaspiClient({(IPHONE, ASTANA): offers}))
    with worker:
        snapshots, _ = worker._load_rules(session)
        assert snapshots[0].config.floor_price == Decimal(95877)
        assert snapshots[0].configured_min_price == Decimal(90000)
        worker.run_once()
    session.expire_all()
    assert product.rules[0].current_price == Decimal(110400)


def test_changing_foreign_threshold_invalidates_an_old_approval_even_if_break_even_is_unchanged(session: Session, build_worker: Any) -> None:
    from repricer.db import PriceProposal

    product = make_product(session, "COST-PREMIUM", IPHONE, cities={ASTANA: 100000}, min_price=90000, max_price=150000)
    product.purchase_price = Decimal(93000)
    product.auto_increase = True
    shop = load_settings(session)
    shop.test_mode = shop.worker_enabled = True
    shop.telegram_chat_ids = ["42"]
    session.flush()
    offers = [replace(competitor(MERCHANT.merchant_id, 100000), intercity=False),
              replace(competitor("far", 92000), intercity=True)]
    worker, storage, _ = build_worker(FakeKaspiClient({(IPHONE, ASTANA): offers}), price_updates=FakeSink())
    with worker:
        worker.run_once()
        session.expire_all()
        proposal = session.scalar(select(PriceProposal))
        assert proposal is not None and proposal.proposed_price == Decimal(110400)
        product.rules[0].min_price = Decimal(93000)
        session.flush()
        snapshots, _ = worker._load_rules(session)
        assert snapshots[0].config.floor_price == Decimal(95877)
        assert "Настройки" in worker.review_proposal(proposal.id, True, 42)
    assert storage.published == []
    assert session.scalar(select(func.count()).select_from(PriceHistory)) == 0


def test_astana_local_rival_keeps_normal_dumping(session: Session, build_worker: Any) -> None:
    product = make_product(session, "LOCAL", IPHONE, cities={ASTANA: 110000}, min_price=90000, max_price=160000)
    product.auto_increase = True
    session.flush()
    offers = [replace(competitor(MERCHANT.merchant_id, 110000), intercity=False),
              replace(competitor("local", 105000), intercity=False),
              replace(competitor("far", 100000), intercity=True)]
    worker, _, _ = build_worker(FakeKaspiClient({(IPHONE, ASTANA): offers}))
    with worker:
        worker.run_once()
    session.expire_all()
    assert product.rules[0].current_price == Decimal(104999)


def test_foreign_premium_is_only_written_after_test_mode_confirmation(session: Session, build_worker: Any) -> None:
    from repricer.db import PriceProposal

    product = make_product(session, "PREMIUM", IPHONE, cities={ASTANA: 110000}, min_price=90000, max_price=160000)
    product.auto_increase = True
    shop = load_settings(session)
    shop.test_mode = shop.worker_enabled = True
    shop.telegram_chat_ids = ["42"]
    session.flush()
    offers = [replace(competitor(MERCHANT.merchant_id, 110000), intercity=False),
              replace(competitor("far", 100000), intercity=True)]
    sink = FakeSink()
    worker, storage, _ = build_worker(FakeKaspiClient({(IPHONE, ASTANA): offers}), price_updates=sink)
    with worker:
        worker.run_once()
        session.expire_all()
        proposal = session.scalar(select(PriceProposal))
        assert proposal is not None and proposal.proposed_price == Decimal(120000)
        assert "+20%" in proposal.message and "Shop far" in proposal.message
        assert "kaspi.kz/shop/p/" in proposal.message
        assert product.rules[0].current_price == Decimal(110000) and storage.published == []
        assert "Подтверждено" in worker.review_proposal(proposal.id, True, 42)
        assert worker.run_once().changed == 0  # Kaspi still shows the old price.
    session.expire_all()
    assert product.rules[0].current_price == product.rules[0].pending_price == Decimal(120000)
    assert len(storage.published) == 1


def test_pending_xml_waits_through_competitor_changes_and_restart(session: Session, build_worker: Any) -> None:
    product = make_product(session, "DELAY", IPHONE, cities={ALMATY: 100000}, min_price=80000, max_price=130000)
    ours = competitor(MERCHANT.merchant_id, 100000)
    client = FakeKaspiClient({(IPHONE, ALMATY): [ours, competitor("rival", 99999)]})
    worker, storage, _ = build_worker(client)
    with worker:
        worker.run_once()
    session.expire_all()
    rule = product.rules[0]
    assert rule.current_price == rule.pending_price == Decimal(99998)
    requested_at = rule.price_requested_at
    client._responses = {(IPHONE, ALMATY): [ours, competitor("rival", 99997)]}
    restarted, _, _ = build_worker(client, storage=storage)
    with restarted:
        for _ in range(3):
            assert restarted.run_once().changed == 0
        session.expire_all()
        assert rule.price_requested_at == requested_at
        assert len(storage.published) == 1
        # Only a real observed acknowledgement unlocks another target.
        client._responses = {(IPHONE, ALMATY): [competitor(MERCHANT.merchant_id, 99998), competitor("rival", 99997)]}
        assert restarted.run_once().changed == 1
    session.expire_all()
    assert rule.current_price == rule.pending_price == Decimal(99996)
    assert len(storage.published) == 2


def test_existing_xml_target_still_waits_for_kaspi_ack(session: Session, build_worker: Any) -> None:
    product = make_product(session, "IMPORTED", IPHONE, cities={ALMATY: 99998}, min_price=80000, max_price=130000)
    ours = competitor(MERCHANT.merchant_id, 100000)
    client = FakeKaspiClient({(IPHONE, ALMATY): [ours, competitor("rival", 99999)]})
    worker, storage, _ = build_worker(client)
    with worker:
        assert worker.run_once().changed == 0
        session.expire_all()
        assert product.rules[0].pending_price == Decimal(99998)
        published = len(storage.published)
        client._responses = {(IPHONE, ALMATY): [ours, competitor("rival", 99997)]}
        assert worker.run_once().changed == 0
        assert len(storage.published) == published
    session.expire_all()
    assert product.rules[0].current_price == Decimal(99998)
    assert session.scalar(select(func.count()).select_from(PriceHistory)) == 0


def test_kaspi_ack_clears_pending_without_republishing(session: Session, build_worker: Any) -> None:
    product = make_product(session, "ACK", IPHONE, cities={ALMATY: 100000}, min_price=80000, max_price=130000)
    product.rules[0].request_price(Decimal(99998))
    session.flush()
    worker, storage, _ = build_worker(FakeKaspiClient({(IPHONE, ALMATY): [competitor(MERCHANT.merchant_id, 99998), competitor("rival", 99999)]}))
    with worker:
        worker.run_once()
    session.expire_all()
    assert product.rules[0].pending_price is None
    assert product.rules[0].price_confirmed_at is not None
    assert storage.published == []


def test_our_delivery_unknown_still_counts_as_local(session: Session, build_worker: Any) -> None:
    product = make_product(session, "PC", IPHONE, cities={ALMATY: 462900},
                           min_price=400000, max_price=520000)
    offers = [replace(offer, intercity=None) if offer.merchant_id == MERCHANT.merchant_id else offer
              for offer in card_from_the_screenshot()]
    worker, _, _ = build_worker(FakeKaspiClient({(IPHONE, ALMATY): offers}))

    with worker:
        worker.run_once()

    session.expire_all()
    assert product.rules[0].current_price == Decimal(462900)


def test_first_with_no_local_competition_goes_to_the_maximum(
    session: Session, build_worker: Any
) -> None:
    product = make_product(session, "PC", IPHONE, cities={ALMATY: 462900},
                           min_price=400000, max_price=520000)
    product.auto_increase = True
    session.flush()
    offers = [offer for offer in card_from_the_screenshot() if offer.merchant_id != "luxe"]
    worker, _, _ = build_worker(FakeKaspiClient({(IPHONE, ALMATY): offers}))

    with worker:
        worker.run_once()

    session.expire_all()
    assert product.rules[0].current_price == Decimal(520000)


@pytest.mark.parametrize(("ago", "expected"), [(timedelta(minutes=30), 462900), (timedelta(hours=3), 462999)])
def test_a_price_goes_up_only_once_it_has_stood_for_two_hours(
    session: Session, build_worker: Any, ago: timedelta, expected: int
) -> None:
    product = make_product(session, "PC", IPHONE, cities={ALMATY: 462900},
                           min_price=400000, max_price=520000)
    product.auto_increase = True
    changed_at(session, product, 462900, ago)
    worker, _, _ = build_worker(FakeKaspiClient({(IPHONE, ALMATY): card_from_the_screenshot()}))

    with worker:
        worker.run_once()

    session.expire_all()
    assert product.rules[0].current_price == Decimal(expected)


def test_a_cut_is_never_held_back_by_the_cooldown(session: Session, build_worker: Any) -> None:
    product = make_product(session, "PC", IPHONE, cities={ALMATY: 462900},
                           min_price=400000, max_price=520000)
    product.auto_increase = True
    changed_at(session, product, 462900, timedelta(minutes=5))
    offers = [*card_from_the_screenshot(), replace(competitor("local", 460000), intercity=False)]
    worker, _, _ = build_worker(FakeKaspiClient({(IPHONE, ALMATY): offers}))

    with worker:
        worker.run_once()

    session.expire_all()
    assert product.rules[0].current_price == Decimal(459999)


def test_price_updates_say_why_the_price_moved(session: Session, build_worker: Any) -> None:
    make_product(session, "IPH", IPHONE, cities={ALMATY: 362000})
    sink = FakeSink()
    worker, _, _ = build_worker(
        FakeKaspiClient({(IPHONE, ALMATY): [competitor("rival", 361000)]}), price_updates=sink
    )

    with worker:
        worker.run_once()

    assert "на шаг дешевле: Shop rival (361 000 ₸)" in sink.messages[0]


def test_test_mode_announces_changes_once_and_changes_nothing(
    session: Session, build_worker: Any
) -> None:
    product = make_product(session, "IPH", IPHONE, cities={ALMATY: 362000})
    load_settings(session).test_mode = True
    session.flush()
    ours = competitor(MERCHANT.merchant_id, 362000)
    client = FakeKaspiClient({(IPHONE, ALMATY): [ours, competitor("rival", 361000)]})
    sink = FakeSink()
    worker, storage, _ = build_worker(client, price_updates=sink)

    with worker:
        first = worker.run_once()
        second = worker.run_once()
        client._responses = {(IPHONE, ALMATY): [ours, competitor("rival", 360000)]}
        worker.run_once()

    session.expire_all()
    rule = product.rules[0]
    assert rule.current_price == Decimal(362000)
    assert session.scalar(select(func.count()).select_from(PriceHistory)) == 0
    assert storage.published == []
    assert (first.test_mode, first.changed, second.changed) == (True, 1, 1)
    # The same wish is told once; a new one is told again.
    assert len(sink.messages) == 2
    assert "Хочу поменять" in sink.messages[0] and "362 000 ₸ → 360 999 ₸" in sink.messages[0]
    assert "359 999 ₸" in sink.messages[1]
    # The catalogue still sees the market.
    assert rule.market_snapshot is not None and rule.last_evaluated_at is not None


def test_group_delivery_receipts_survive_worker_restart(session: Session, build_worker: Any) -> None:
    from repricer.db import PriceProposal
    from repricer.telegram.alerts import ProposalNotice
    make_product(session, "GROUP", IPHONE, cities={ALMATY: 362000})
    load_settings(session).test_mode = True
    session.flush()
    calls = []
    class GroupSink(FakeSink):
        def send_proposals(self, notices: list[ProposalNotice], *, on_delivered: Any) -> dict[int, list[str]]:
            pending = [n for n in notices if "77" not in n.delivered_to]
            calls.append([n.id for n in pending])
            receipts = {n.id: ["77"] for n in pending}
            on_delivered(receipts)
            return receipts
    client = FakeKaspiClient({(IPHONE, ALMATY): [competitor(MERCHANT.merchant_id, 362000), competitor("rival", 361000)]})
    with build_worker(client, price_updates=GroupSink())[0] as worker:
        worker.run_once()
    with build_worker(client, price_updates=GroupSink())[0] as restarted:
        restarted.run_once()
    session.expire_all()
    proposal = session.scalar(select(PriceProposal))
    assert proposal is not None and proposal.delivered_to == ["77"]
    assert calls == [[proposal.id], []]
    assert session.scalar(select(func.count()).select_from(PriceHistory)) == 0


def stage_test_proposal(session: Session, build_worker: Any) -> tuple[Any, Any, Any, Any]:
    from repricer.db import PriceProposal
    product = make_product(session, "REVIEW", IPHONE, cities={ALMATY: 362000})
    shop = load_settings(session)
    shop.test_mode = shop.worker_enabled = True
    shop.telegram_chat_ids = ["42"]
    session.flush()
    client = FakeKaspiClient({(IPHONE, ALMATY): [
        competitor(MERCHANT.merchant_id, 362000), competitor("rival", 361000)]})
    sink = FakeSink()
    worker, storage, _ = build_worker(client, price_updates=sink)
    worker.run_once()
    proposal = session.scalar(select(PriceProposal))
    assert proposal is not None
    return worker, storage, client, proposal


def test_owner_can_apply_once_and_duplicate_click_cannot_write(session: Session, build_worker: Any) -> None:
    worker, storage, _, proposal = stage_test_proposal(session, build_worker)
    with worker:
        assert "Подтверждено" in worker.review_proposal(proposal.id, True, 42)
        assert "уже обработано" in worker.review_proposal(proposal.id, True, 42)
    session.expire_all()
    assert proposal.status == "applied"
    assert session.scalar(select(RepricerRule.current_price)) == Decimal(360999)
    assert session.scalar(select(func.count()).select_from(PriceHistory)) == 1
    assert len(storage.published) == 1


def test_rejection_survives_worker_restart(session: Session, build_worker: Any) -> None:
    from repricer.db import PriceProposal
    worker, storage, client, proposal = stage_test_proposal(session, build_worker)
    with worker:
        assert "Отклонено" in worker.review_proposal(proposal.id, False, 42)
    sink = FakeSink()
    restarted, _, _ = build_worker(client, price_updates=sink)
    with restarted:
        restarted.run_once()
    session.expire_all()
    assert proposal.status == "rejected"
    assert sink.messages == [] and storage.published == []
    assert session.scalar(select(func.count()).select_from(PriceProposal)) == 1


@pytest.mark.parametrize("change", ["price", "min", "pause", "product_pause", "zero_stock", "unavailable", "test_off", "bot_off", "expired", "owner_removed"])
def test_stale_or_unauthorized_proposal_never_changes_price(
    session: Session, build_worker: Any, change: str
) -> None:
    worker, storage, _, proposal = stage_test_proposal(session, build_worker)
    session.expire_all()
    rule = session.scalar(select(RepricerRule))
    assert rule is not None
    shop = load_settings(session)
    if change == "price":
        rule.current_price = Decimal(400000)
    elif change == "min":
        rule.min_price = Decimal(360500)
    elif change == "pause":
        rule.is_active = False
    elif change == "product_pause":
        rule.product.is_active = False
    elif change == "zero_stock":
        rule.product.availabilities[0].stock_count = 0
    elif change == "unavailable":
        rule.product.availabilities[0].available = False
    elif change == "test_off":
        shop.test_mode = False
    elif change == "bot_off":
        shop.worker_enabled = False
    elif change == "expired":
        proposal.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    elif change == "owner_removed":
        shop.telegram_chat_ids = []
    session.flush()
    with worker:
        answer = worker.review_proposal(proposal.id, True, 42)
    assert "Подтверждено" not in answer
    assert storage.published == []
    assert session.scalar(select(func.count()).select_from(PriceHistory)) == 0


def test_changed_market_does_not_apply_old_button(session: Session, build_worker: Any) -> None:
    worker, storage, client, proposal = stage_test_proposal(session, build_worker)
    client._responses = {(IPHONE, ALMATY): [competitor("rival", 350000), competitor(MERCHANT.merchant_id, 362000)]}
    with worker:
        assert "конкурентов изменились" in worker.review_proposal(proposal.id, True, 42)
    session.expire_all()
    assert proposal.status == "superseded" and storage.published == []


def test_a_subscriber_can_approve_once_but_cannot_after_unsubscribing(session: Session, build_worker: Any) -> None:
    from repricer.telegram.subscriptions import subscribe, unsubscribe
    worker, storage, _, proposal = stage_test_proposal(session, build_worker)
    subscribe(session, MERCHANT.merchant_id, 777)
    unsubscribe(session, MERCHANT.merchant_id, 777)
    with worker:
        assert "Подтверждено" not in worker.review_proposal(proposal.id, True, 777)
        subscribe(session, MERCHANT.merchant_id, 777)
        assert "Подтверждено" in worker.review_proposal(proposal.id, True, 777)
        assert "уже обработано" in worker.review_proposal(proposal.id, True, 42)
    assert len(storage.published) == 1


def test_unreachable_market_keeps_button_retryable(session: Session, build_worker: Any) -> None:
    worker, storage, client, proposal = stage_test_proposal(session, build_worker)
    client._responses = {(IPHONE, ALMATY): KaspiTransportError("offline")}
    with worker:
        assert "попробуйте" in worker.review_proposal(proposal.id, True, 42)
    session.expire_all()
    assert proposal.status == "pending" and storage.published == []


def test_known_purchase_price_guards_loss_and_impossible_max_is_skipped(session: Session, build_worker: Any) -> None:
    product = make_product(session, "COST", IPHONE, cities={ALMATY: 400000}, min_price=200000)
    product.purchase_price = Decimal(350000)
    session.flush()
    worker, _, _ = build_worker(FakeKaspiClient({(IPHONE, ALMATY): [
        competitor(MERCHANT.merchant_id, 400000), competitor("rival", 250000)]}))
    with worker:
        snapshots, _ = worker._load_rules(session)
        assert snapshots[0].config.min_price == Decimal(360825)
        worker.run_once()
        session.expire_all()
        assert product.rules[0].current_price == Decimal(400000)
        product.purchase_price = Decimal(600000)
        session.flush()
        snapshots, _ = worker._load_rules(session)
        assert snapshots == []


def test_manual_edit_during_scrape_is_not_overwritten(session: Session, build_worker: Any) -> None:
    product = make_product(session, "RACE", IPHONE, cities={ALMATY: 362000})
    client = FakeKaspiClient({(IPHONE, ALMATY): [competitor("rival", 350000)]})
    worker, storage, _ = build_worker(client)
    with worker:
        snapshots, _ = worker._load_rules(session)
        outcome = worker._price_one(snapshots[0])
        product.rules[0].current_price = Decimal(450000)
        session.flush()
        _, applied = worker._write([outcome])
    assert applied == set() and storage.published == []
    assert session.scalar(select(RepricerRule.current_price)) == Decimal(450000)


def test_mode_enabled_during_scrape_blocks_automatic_write(session: Session, build_worker: Any) -> None:
    make_product(session, "MODE", IPHONE)
    worker, storage, _ = build_worker(FakeKaspiClient({(IPHONE, ALMATY): [competitor("rival", 350000)]}))
    with worker:
        snapshots, _ = worker._load_rules(session)
        outcome = worker._price_one(snapshots[0])
        load_settings(session).test_mode = True
        session.flush()
        _, applied = worker._write([outcome])
    assert applied == set() and storage.published == []


def test_failed_delivery_retries_then_persists_after_restart(session: Session, build_worker: Any) -> None:
    from repricer.db import PriceProposal
    make_product(session, "RETRY", IPHONE)
    load_settings(session).test_mode = True
    session.flush()
    class DeliverySink:
        def __init__(self) -> None:
            self.calls = 0
        def send(self, text: str) -> bool:
            self.calls += 1
            return self.calls > 1
    sink = DeliverySink()
    client = FakeKaspiClient({(IPHONE, ALMATY): [competitor("rival", 350000)]})
    worker, _, _ = build_worker(client, price_updates=sink)
    with worker:
        worker.run_once()
        worker.run_once()
    restarted, _, _ = build_worker(client, price_updates=sink)
    with restarted:
        restarted.run_once()
    assert sink.calls == 2
    assert session.scalar(select(func.count()).select_from(PriceProposal)) == 1
