"""The worker service: settings in the database drive everything."""

import threading
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from repricer.db import Product, ProductAvailability, RepricerRule, ShopSettings
from repricer.db.settings_store import load_settings
from repricer.pricing import PricingStrategy
from repricer.service import IDLE_SECONDS, RuntimeConfig, WorkerService
from repricer.telegram.alerts import LoggingSink, TelegramSink
from repricer.worker import CycleReport

ALMATY = "750000000"


@pytest.fixture
def session_factory(session: Session) -> Callable[[], Session]:
    connection = session.connection()

    def factory() -> Session:
        return Session(bind=connection, join_transaction_mode="create_savepoint")

    return factory


def configure(
    session: Session,
    *,
    merchant_id: str = "30123456",
    company: str = "ТОО Ромашка",
    enabled: bool = True,
    **fields: Any,
) -> ShopSettings:
    settings = load_settings(session)
    settings.merchant_id = merchant_id
    settings.company = company
    settings.worker_enabled = enabled
    settings.global_strategy = PricingStrategy.BEAT_FIRST
    settings.global_city_ids = [ALMATY]
    for name, value in fields.items():
        setattr(settings, name, value)
    session.flush()
    return settings


class FakeWorker:
    def __init__(self) -> None:
        self.cycles = 0
        self.closed = False

    def run_once(self) -> CycleReport:
        self.cycles += 1
        return CycleReport(evaluated=1)

    def close(self) -> None:
        self.closed = True


def service_with_fake(
    session_factory: Callable[[], Session], tmp_path: Path
) -> tuple[WorkerService, list[RuntimeConfig], list[FakeWorker]]:
    seen: list[RuntimeConfig] = []
    built: list[FakeWorker] = []

    def factory(config: RuntimeConfig) -> Any:
        seen.append(config)
        worker = FakeWorker()
        built.append(worker)
        return worker

    service = WorkerService(
        session_factory=session_factory,
        feed_dir=tmp_path / "feeds",
        worker_factory=factory,
    )
    return service, seen, built


# --- Deciding whether to run --------------------------------------------------


def test_an_unconfigured_shop_does_not_scrape(
    session: Session, session_factory: Any, tmp_path: Path
) -> None:
    service, seen, _ = service_with_fake(session_factory, tmp_path)

    assert service.run_once() is None
    assert seen == []


def test_the_switch_in_the_dashboard_stops_the_bot(
    session: Session, session_factory: Any, tmp_path: Path
) -> None:
    configure(session, enabled=False)
    service, seen, _ = service_with_fake(session_factory, tmp_path)

    assert service.run_once() is None
    assert seen == []


def test_bot_waits_for_shared_strategy(
    session: Session, session_factory: Any, tmp_path: Path
) -> None:
    settings = configure(session)
    settings.global_strategy = None
    session.flush()
    service, seen, _ = service_with_fake(session_factory, tmp_path)

    assert service.run_once() is None
    assert seen == []


def test_a_configured_shop_runs_a_cycle(
    session: Session, session_factory: Any, tmp_path: Path
) -> None:
    configure(session)
    service, seen, built = service_with_fake(session_factory, tmp_path)

    report = service.run_once()

    assert report is not None
    assert built[0].cycles == 1
    assert seen[0].merchant.merchant_id == "30123456"


def test_the_settings_row_appears_by_itself(session: Session, session_factory: Any) -> None:
    with session_factory() as fresh:
        settings = load_settings(fresh)
        settings_id = settings.id
        fresh.commit()

    assert settings_id == 1
    assert session.scalars(select(ShopSettings)).one().worker_enabled is False


# --- Reacting to changes ------------------------------------------------------


def test_the_worker_is_reused_while_nothing_changes(
    session: Session, session_factory: Any, tmp_path: Path
) -> None:
    configure(session)
    service, _, built = service_with_fake(session_factory, tmp_path)

    service.run_once()
    service.run_once()

    assert len(built) == 1
    assert built[0].cycles == 2


def test_changed_settings_rebuild_the_worker(
    session: Session, session_factory: Any, tmp_path: Path
) -> None:
    configure(session)
    service, seen, built = service_with_fake(session_factory, tmp_path)
    service.run_once()

    configure(session, proxies=["http://10.0.0.1:8080"])
    service.run_once()

    # The old worker held clients pointing at the old proxies, so it is dropped.
    assert len(built) == 2
    assert built[0].closed
    assert seen[1].proxies == ("http://10.0.0.1:8080",)


def test_switching_off_releases_the_worker(
    session: Session, session_factory: Any, tmp_path: Path
) -> None:
    configure(session)
    service, _, built = service_with_fake(session_factory, tmp_path)
    service.run_once()

    configure(session, enabled=False)
    assert service.run_once() is None
    assert built[0].closed


# --- What the worker is built with --------------------------------------------


def test_alerts_go_to_telegram_once_a_token_is_saved(session: Session) -> None:
    configure(session, telegram_bot_token="123:ABC", telegram_chat_ids=["42"])
    config = RuntimeConfig.from_settings(load_settings(session))

    assert config is not None
    assert isinstance(WorkerService._alert_sink(config), TelegramSink)


def test_without_a_token_alerts_only_reach_the_log(session: Session) -> None:
    configure(session)
    config = RuntimeConfig.from_settings(load_settings(session))

    assert config is not None
    assert isinstance(WorkerService._alert_sink(config), LoggingSink)


def test_a_half_filled_shop_is_not_ready(session: Session) -> None:
    settings = configure(session, company="")

    assert RuntimeConfig.from_settings(settings) is None


def test_the_rating_reaches_the_engine(
    session: Session, session_factory: Any, tmp_path: Path
) -> None:
    configure(session, merchant_rating=4.7)
    service, seen, _ = service_with_fake(session_factory, tmp_path)

    service.run_once()

    assert seen[0].own_rating == 4.7


# --- The loop -----------------------------------------------------------------


def test_the_loop_sleeps_for_the_configured_interval(
    session: Session, session_factory: Any, tmp_path: Path
) -> None:
    configure(session, interval_seconds=600)
    service, _, _ = service_with_fake(session_factory, tmp_path)
    waits: list[float] = []
    stop = _StopAfter(waits, times=1)

    service.run_forever(stop)  # type: ignore[arg-type]

    assert waits == [600]


def test_an_idle_loop_checks_back_sooner(
    session: Session, session_factory: Any, tmp_path: Path
) -> None:
    service, _, _ = service_with_fake(session_factory, tmp_path)
    waits: list[float] = []

    service.run_forever(_StopAfter(waits, times=1))  # type: ignore[arg-type]

    # Nothing configured yet, so it comes back quickly to notice when it is.
    assert waits == [IDLE_SECONDS]


def test_a_failing_cycle_does_not_end_the_loop(
    session: Session, session_factory: Any, tmp_path: Path
) -> None:
    configure(session)
    service, _, _ = service_with_fake(session_factory, tmp_path)
    waits: list[float] = []

    def explode() -> CycleReport:
        raise RuntimeError("Kaspi недоступен")

    service.run_once = explode  # type: ignore[method-assign]
    service.run_forever(_StopAfter(waits, times=2))  # type: ignore[arg-type]

    assert len(waits) == 2


class _StopAfter:
    """A stop event that lets the loop run a fixed number of times."""

    def __init__(self, waits: list[float], times: int) -> None:
        self._waits = waits
        self._left = times

    def wait(self, timeout: float) -> bool:
        self._waits.append(timeout)
        self._left -= 1
        return self._left <= 0

    def is_set(self) -> bool:
        return self._left <= 0

    def set(self) -> None:
        self._left = 0


# --- End to end with the real worker ------------------------------------------


def test_a_real_cycle_prices_a_product(
    session: Session, session_factory: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(session, merchant_id="11271005", company="ТОО Техно")
    product = Product(
        merchant_id="11271005",
        sku="IPH13-128",
        kaspi_product_id="102298404",
        title="iPhone 13",
        brand="Apple",
    )
    product.availabilities.append(ProductAvailability(store_id="PP1", stock_count=2))
    product.rules.append(
        RepricerRule(
            city_id=ALMATY,
            strategy=PricingStrategy.BEAT_FIRST,
            min_price=Decimal(300000),
            max_price=Decimal(500000),
            current_price=Decimal(400000),
        )
    )
    session.add(product)
    session.flush()

    from repricer.scraper import Offer

    class FakeClient:
        def get_product_offers(self, product_id: str, city_id: str) -> list[Offer]:
            return [
                Offer("rival", "Конкурент", Decimal(390000), 4.5, 10, True, "TOMORROW"),
            ]

        def close(self) -> None:
            pass

    service = WorkerService(session_factory=session_factory, feed_dir=tmp_path / "feeds")
    monkeypatch.setattr(
        "repricer.service.KaspiClient", lambda **kwargs: FakeClient()
    )

    report = service.run_once()
    service.close()

    assert report is not None and report.changed == 1
    session.expire_all()
    assert session.scalars(select(RepricerRule)).one().current_price == Decimal(389999)
    assert (tmp_path / "feeds" / "kaspi-price-list-11271005.xml").exists()
