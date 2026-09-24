"""The long-running worker process, driven by the settings in the database.

The owner fills the dashboard in and switches the bot on; this loop notices on
its next pass. Nothing here needs a restart or a deploy: the merchant, the
proxies, the interval and the Telegram token are all read from the database at
the start of every cycle.

When the shop is not configured, or the switch is off, the loop idles quietly
instead of exiting, so a container can stay up from the first minute of a fresh
install until long after it is set up.
"""

from __future__ import annotations

import threading
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from sqlalchemy.orm import Session

from repricer.db.models import ShopSettings
from repricer.db.settings_store import load_settings
from repricer.scraper import KaspiClient, ProxyPool, RateLimiter
from repricer.telegram.alerts import AlertSink, BroadcastTelegramSink, LoggingSink, TelegramSink
from repricer.uploader import DatabaseCatalog, LocalFeedStorage, MerchantIdentity, SyncManager
from repricer.worker import CycleReport, RepricingWorker, WorkerSettings

#: How long to wait before looking at the settings again while idle.
IDLE_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """The settings row, frozen into plain values the worker can be built from."""

    merchant: MerchantIdentity
    own_rating: float | None
    proxies: tuple[str, ...]
    request_interval: float
    interval_seconds: int
    telegram_token: str
    telegram_chat_id: int | None

    @classmethod
    def from_settings(cls, shop: ShopSettings) -> RuntimeConfig | None:
        """None when the shop has not been filled in yet."""
        if not shop.is_ready:
            return None
        chat_ids = [chat for chat in shop.telegram_chat_ids if chat.lstrip("-").isdigit()]
        return cls(
            merchant=MerchantIdentity(shop.merchant_id.strip(), shop.company.strip()),
            own_rating=shop.merchant_rating,
            proxies=tuple(shop.proxies),
            request_interval=shop.request_interval,
            interval_seconds=shop.interval_seconds,
            telegram_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip() or shop.telegram_bot_token.strip(),
            telegram_chat_id=int(chat_ids[0]) if chat_ids else None,
        )

    @property
    def rebuild_key(self) -> tuple[object, ...]:
        """What must change for the worker to be rebuilt rather than reused."""
        return (
            self.merchant.merchant_id,
            self.merchant.company,
            self.own_rating,
            self.proxies,
            self.request_interval,
            self.telegram_token,
            self.telegram_chat_id,
        )


class WorkerService:
    """Keeps one worker alive and in step with the settings."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        feed_dir: Path,
        feed_base_url: str | None = None,
        concurrency: int = 4,
        dry_run: bool = False,
        worker_factory: Callable[[RuntimeConfig], RepricingWorker] | None = None,
        sleep: Callable[[float], bool] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._feed_dir = feed_dir
        self._feed_base_url = feed_base_url
        self._concurrency = concurrency
        self._dry_run = dry_run
        self._worker_factory = worker_factory or self._build_worker
        self._sleep = sleep
        self._worker: RepricingWorker | None = None
        self._built_from: tuple[object, ...] | None = None
        self._idle_reason: str | None = None

    def close(self) -> None:
        if self._worker is not None:
            self._worker.close()
            self._worker = None
            self._built_from = None

    def __enter__(self) -> WorkerService:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def read_config(self) -> tuple[RuntimeConfig | None, bool, bool]:
        """Current settings, master switch and shared strategy readiness."""
        with self._session_factory() as session:
            shop = load_settings(session)
            session.commit()
            return (
                RuntimeConfig.from_settings(shop),
                shop.worker_enabled,
                bool(shop.global_strategy and shop.global_city_ids),
            )

    def run_once(self) -> CycleReport | None:
        """One cycle, or None when the settings say not to run."""
        config, enabled, strategy_ready = self.read_config()
        if config is None:
            self._idle("Магазин не настроен: заполните ID магазина и название компании в панели")
            return None
        if not enabled:
            self._idle(f"merchant={config.merchant.merchant_id}: бот выключен в панели")
            return None
        if not strategy_ready:
            self._idle(f"merchant={config.merchant.merchant_id}: общая стратегия и города не настроены")
            return None

        self._idle_reason = None
        if self._built_from != config.rebuild_key:
            # Proxies, the merchant or the alert chat changed: start clean rather
            # than keep clients pointing at the old settings.
            self.close()
            self._worker = self._worker_factory(config)
            self._built_from = config.rebuild_key
            logger.info(
                "merchant={}: настройки применены, прокси: {}",
                config.merchant.merchant_id,
                len(config.proxies) or "нет",
            )
        assert self._worker is not None
        return self._worker.run_once()

    def run_forever(self, stop: threading.Event | None = None) -> None:
        stop = stop if stop is not None else threading.Event()
        logger.info("Сервис запущен: настройки читаются из базы, ждём включения в панели")
        while True:
            config, enabled, strategy_ready = self.read_config()
            try:
                self.run_once()
            except Exception:
                # A cycle can fail on anything: a database blip, a full disk, a
                # changed Kaspi response. The service sleeps and tries again.
                logger.exception("Цикл упал")
            wait = config.interval_seconds if (config and enabled and strategy_ready) else IDLE_SECONDS
            if stop.wait(wait):
                logger.info("Остановка по сигналу")
                return

    def _idle(self, reason: str) -> None:
        if reason != self._idle_reason:
            logger.info(reason)
            self._idle_reason = reason
        # Nothing to talk to Kaspi with; drop any client we were holding.
        self.close()

    def _build_worker(self, config: RuntimeConfig) -> RepricingWorker:
        pool = ProxyPool(config.proxies) if config.proxies else None
        if pool is None:
            logger.warning(
                "Прокси не заданы: все запросы идут с IP этого сервера, Kaspi рано или поздно его ограничит"
            )
        limiter = RateLimiter(config.request_interval)
        storage = LocalFeedStorage(self._feed_dir, base_url=self._feed_base_url or None)

        def make_sync_manager(session: Session) -> SyncManager:
            return SyncManager(catalog=DatabaseCatalog(session), storage=storage)

        return RepricingWorker(
            settings=WorkerSettings(
                merchant=config.merchant,
                concurrency=self._concurrency,
                dry_run=self._dry_run,
                own_rating=config.own_rating,
            ),
            session_factory=self._session_factory,
            client_factory=lambda: KaspiClient(proxy_pool=pool, rate_limiter=limiter),
            sync_manager_factory=make_sync_manager,
            proxy_pool=pool,
            alerts=self._alert_sink(config),
            price_updates=(
                BroadcastTelegramSink(config.telegram_token, config.merchant.merchant_id, self._session_factory)
                if config.telegram_token else None
            ),
        )

    @staticmethod
    def _alert_sink(config: RuntimeConfig) -> AlertSink:
        if config.telegram_token and config.telegram_chat_id is not None:
            return TelegramSink(config.telegram_token, config.telegram_chat_id)
        return LoggingSink()
