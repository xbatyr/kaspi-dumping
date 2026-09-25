"""The background pipeline: rules in, competitor prices out, feed republished.

One cycle:

  1. Load every enabled rule for the merchant in a single query and group it by city.
  2. Fetch the product cards through a pool of threads, paced per exit IP.
  3. Price each SKU with the rule engine and log the step.
  4. Hand the decisions to the sync manager, which writes the history, updates the
     prices and republishes the price list feed.

Threads rather than asyncio: the scraper's session, SQLAlchemy's Session and the
engine are all synchronous and the work is I/O bound, so a thread pool buys the
same concurrency without a second, asynchronous copy of the scraper.

ORM objects never cross a thread boundary. The main thread turns rules into
plain snapshots, the threads return plain decisions, and the database is touched
again only to write the results, so a slow cycle never holds a transaction open
while scraping.

Whatever goes wrong with a SKU -- an unreachable proxy, a changed response, an
empty offer list -- that SKU keeps the price it already has. Prices only ever
move on a decision the engine actually made.
"""

from __future__ import annotations

import threading
import time
from html import escape
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from decimal import Decimal

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from repricer.db.models import Product, RepricerRule
from repricer.db.queries import latest_changes
from repricer.db.settings_store import settings_or_none
from repricer.pricing import (
    CompetitorOffer,
    DecisionReason,
    PricingConfig,
    PricingDecision,
    PricingEngine,
    PricingStrategy,
)
from repricer.scraper import KaspiClient, KaspiError, KaspiTransportError, ProxyPool
from repricer.telegram.alerts import Alert, AlertKind, AlertSink, AlertThrottle
from repricer.telegram.formatting import city as city_name, tenge
from repricer.telegram.formatting import render_alert
from repricer.uploader import MerchantIdentity, PriceUpdate, SyncManager


@dataclass(frozen=True, slots=True)
class WorkerSettings:
    merchant: MerchantIdentity
    #: Product cards fetched at the same time.
    concurrency: int = 4
    #: Price everything and log it, but write nothing and publish nothing.
    dry_run: bool = False
    #: Share of a cycle's SKUs that may fail to reach Kaspi before this counts as
    #: a proxy outage rather than bad luck.
    failure_alert_ratio: float = 0.25
    #: Our store's rating, used by MATCH_FIRST when our own offer is missing from
    #: the card and its live rating cannot be read.
    own_rating: float | None = None

    def __post_init__(self) -> None:
        if self.concurrency < 1:
            raise ValueError("concurrency must be >= 1")
        if not 0 < self.failure_alert_ratio <= 1:
            raise ValueError("failure_alert_ratio must be between 0 (exclusive) and 1")


@dataclass(frozen=True, slots=True)
class RuleSnapshot:
    """A rule detached from the session, safe to hand to a worker thread."""

    rule_id: int
    sku: str
    kaspi_product_id: str
    city_id: str
    current_price: Decimal | None
    config: PricingConfig
    #: Where we stood after the last price change; None when there is no history.
    last_position: int | None = None


@dataclass(frozen=True, slots=True)
class TaskOutcome:
    snapshot: RuleSnapshot
    decision: PricingDecision | None = None
    #: Why no price was computed; the current price then stays untouched.
    skipped: str | None = None
    #: The reason was a failure to reach Kaspi, which points at the proxies.
    unreachable: bool = False
    #: Shop name of the cheapest competitor, for alerts; the engine only keeps IDs.
    leader_name: str | None = None
    market_snapshot: dict[str, str | int | None] | None = None


@dataclass(frozen=True, slots=True)
class CycleReport:
    evaluated: int = 0
    changed: int = 0
    unchanged: int = 0
    skipped: int = 0
    unreachable: int = 0
    feed_url: str | None = None
    dry_run: bool = False
    duration: float = 0.0


def group_by_city(snapshots: Iterable[RuleSnapshot]) -> dict[str, list[RuleSnapshot]]:
    """Group rules by city, which is the unit Kaspi prices in and we log in."""
    grouped: dict[str, list[RuleSnapshot]] = defaultdict(list)
    for snapshot in snapshots:
        grouped[snapshot.city_id].append(snapshot)
    return dict(grouped)


class RepricingWorker:
    """Runs repricing cycles: scrape, price, publish."""

    def __init__(
        self,
        *,
        settings: WorkerSettings,
        session_factory: Callable[[], Session],
        client_factory: Callable[[], KaspiClient],
        sync_manager_factory: Callable[[Session], SyncManager],
        engine: PricingEngine | None = None,
        proxy_pool: ProxyPool | None = None,
        alerts: AlertSink | None = None,
        price_updates: AlertSink | None = None,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._client_factory = client_factory
        self._sync_manager_factory = sync_manager_factory
        self._engine = engine or PricingEngine()
        self._proxy_pool = proxy_pool
        self._alerts = alerts
        self._price_updates = price_updates
        self._throttle = AlertThrottle()
        self._executor: ThreadPoolExecutor | None = None
        self._local = threading.local()
        self._clients: list[KaspiClient] = []
        self._clients_lock = threading.Lock()

    def __enter__(self) -> RepricingWorker:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=True)
            self._executor = None
        with self._clients_lock:
            clients, self._clients = self._clients, []
        for client in clients:
            client.close()

    # --- One cycle ------------------------------------------------------------

    def run_once(self) -> CycleReport:
        started = time.monotonic()
        merchant_id = self._settings.merchant.merchant_id
        with self._session_factory() as session:
            snapshots = self._load_rules(session)
        if not snapshots:
            logger.info("merchant={}: no enabled rules, nothing to do", merchant_id)
            return CycleReport(dry_run=self._settings.dry_run, duration=time.monotonic() - started)

        by_city = group_by_city(snapshots)
        logger.info(
            "merchant={}: cycle over {} rules in {} cities ({}){}",
            merchant_id,
            len(snapshots),
            len(by_city),
            ", ".join(f"{city}: {len(rules)}" for city, rules in sorted(by_city.items())),
            " [dry run]" if self._settings.dry_run else "",
        )

        outcomes = self._price_all(by_city)
        decided = [outcome for outcome in outcomes if outcome.decision is not None]
        unreachable = sum(1 for outcome in outcomes if outcome.unreachable)
        changed = sum(
            1
            for outcome in decided
            if outcome.decision is not None
            and outcome.decision.new_price != outcome.snapshot.current_price
        )
        self._alert_on_proxy_trouble(unreachable, len(outcomes))
        feed_url = None
        if self._settings.dry_run:
            logger.info(
                "merchant={}: dry run, {} of {} priced SKUs would change; nothing written",
                merchant_id,
                changed,
                len(decided),
            )
        elif decided:
            feed_url, applied_ids = self._write(decided)
            changed = len(applied_ids)
            self._notify(outcomes)
            self._notify_price_changes(outcomes, applied_ids)

        report = CycleReport(
            evaluated=len(decided),
            changed=changed,
            unchanged=len(decided) - changed,
            skipped=len(outcomes) - len(decided),
            unreachable=unreachable,
            feed_url=feed_url,
            dry_run=self._settings.dry_run,
            duration=time.monotonic() - started,
        )
        logger.info(
            "merchant={}: cycle done in {:.1f}s: {} priced, {} changed, {} left alone{}",
            merchant_id,
            report.duration,
            report.evaluated,
            report.changed,
            report.skipped,
            f", feed at {feed_url}" if feed_url else "",
        )
        return report

    def run_forever(self, interval: float, stop: threading.Event | None = None) -> None:
        """Run cycles every ``interval`` seconds until ``stop`` is set."""
        if interval <= 0:
            raise ValueError("interval must be positive")
        stop = stop if stop is not None else threading.Event()
        while True:
            started = time.monotonic()
            try:
                self.run_once()
            except Exception:
                # A cycle can fail on anything from a database blip to a full
                # disk. The worker logs it and tries again rather than dying.
                logger.exception("Cycle failed")
            elapsed = time.monotonic() - started
            if elapsed > interval:
                logger.warning(
                    "Cycle took {:.0f}s, longer than the {:.0f}s interval; starting the next one now",
                    elapsed,
                    interval,
                )
            if stop.wait(max(0.0, interval - elapsed)):
                logger.info("Stop requested, worker finished")
                return

    # --- Steps ----------------------------------------------------------------

    def _load_rules(self, session: Session) -> list[RuleSnapshot]:
        shop = settings_or_none(session)
        shared = shop if shop is not None and shop.global_strategy is not None else None
        statement = (
            select(RepricerRule, Product)
            .join(Product, RepricerRule.product_id == Product.id)
            .where(
                Product.merchant_id == self._settings.merchant.merchant_id,
                Product.is_active,
                RepricerRule.is_active,
            )
            .order_by(RepricerRule.city_id, Product.sku)
        )
        rows = session.execute(statement).all()
        # One query for every rule's last known place, so alerts can tell a fresh
        # loss of first place from one we already reported.
        changes = latest_changes(session, [product.id for _rule, product in rows])

        snapshots: list[RuleSnapshot] = []
        for rule, product in rows:
            if shared is not None and (
                rule.city_id not in shared.global_city_ids
                or rule.strategy is PricingStrategy.MANUAL
                or rule.max_price <= rule.min_price
            ):
                continue
            if not product.kaspi_product_id and rule.strategy not in {
                PricingStrategy.MANUAL, PricingStrategy.FIXED_PRICE
            }:
                logger.warning("sku={} city={}: link a Kaspi card before automatic repricing", product.sku, rule.city_id)
                continue
            try:
                if shared is not None:
                    assert shared.global_strategy is not None
                    config = PricingConfig(
                        own_merchant_id=product.merchant_id,
                        own_rating=self._settings.own_rating,
                        strategy=shared.global_strategy,
                        min_price=rule.min_price,
                        max_price=rule.max_price,
                        step=rule.step,
                        target_position=shared.global_target_position,
                        ignored_merchants=frozenset(shared.global_ignored_merchants),
                        base_price=product.base_price,
                    )
                else:
                    config = rule.to_pricing_config(
                        own_merchant_id=product.merchant_id,
                        own_rating=self._settings.own_rating,
                        base_price=product.base_price,
                    )
                config = replace(config,
                    auto_decrease=product.auto_decrease,
                    auto_increase=product.auto_increase,
                    raise_when_first=product.auto_increase)
            except ValueError as exc:
                # A rule the API could not have saved, or one whose product lost
                # its base price. Skipping it beats failing the whole cycle.
                logger.warning(
                    "sku={} city={}: rule is misconfigured and was skipped ({})",
                    product.sku,
                    rule.city_id,
                    exc,
                )
                continue
            snapshots.append(
                RuleSnapshot(
                    rule_id=rule.id,
                    sku=product.sku,
                    kaspi_product_id=product.kaspi_product_id,
                    city_id=rule.city_id,
                    current_price=rule.current_price,
                    config=config,
                    last_position=(
                        change.expected_position
                        if (change := changes.get((rule.product_id, rule.city_id)))
                        else None
                    ),
                )
            )
        return snapshots

    def _price_all(self, by_city: dict[str, list[RuleSnapshot]]) -> list[TaskOutcome]:
        futures: dict[Future[TaskOutcome], RuleSnapshot] = {}
        executor = self._pool()
        for _city_id, snapshots in sorted(by_city.items()):
            for snapshot in snapshots:
                futures[executor.submit(self._price_one, snapshot)] = snapshot
        outcomes: list[TaskOutcome] = []
        for future in as_completed(futures):
            outcome = future.result()  # _price_one handles its own failures
            self._log_step(outcome)
            outcomes.append(outcome)
        return outcomes

    def _price_one(self, snapshot: RuleSnapshot) -> TaskOutcome:
        try:
            strategy = snapshot.config.strategy
            if strategy is PricingStrategy.MANUAL:
                return TaskOutcome(snapshot, skipped="manual rule, priced by hand")
            if strategy is PricingStrategy.FIXED_PRICE:
                # Holds the product's base price, so there is nothing to scrape.
                return TaskOutcome(
                    snapshot,
                    decision=self._engine.evaluate(
                        snapshot.config, (), current_price=snapshot.current_price
                    ),
                )
            offers = self._client().get_product_offers(snapshot.kaspi_product_id, snapshot.city_id)
            if not offers:
                # Kaspi answers with an empty list both for a product nobody
                # sells and for an unknown product ID. Pricing against "no
                # competitors" would jump to max_price, so nothing is changed.
                return TaskOutcome(snapshot, skipped="Kaspi returned no offers")
            own_merchant_id = snapshot.config.own_merchant_id
            if not any(offer.merchant_id == own_merchant_id for offer in offers):
                logger.warning(
                    "sku={} city={}: our own offer is not on the card; pricing against competitors only",
                    snapshot.sku,
                    snapshot.city_id,
                )
            competitors = [
                CompetitorOffer(offer.merchant_id, offer.price, offer.rating) for offer in offers
            ]
            decision = self._engine.evaluate(
                snapshot.config, competitors, current_price=snapshot.current_price
            )
            leader = decision.leader
            names = {offer.merchant_id: offer.merchant_name for offer in offers}
            ranked = sorted({offer.merchant_id: offer for offer in reversed(offers)}.values(),
                            key=lambda offer: (offer.price, -(offer.rating or 0)))
            own_index = next((index for index, offer in enumerate(ranked)
                              if offer.merchant_id == own_merchant_id), None)
            return TaskOutcome(
                snapshot,
                decision=decision,
                leader_name=names.get(leader.merchant_id) if leader else None,
                market_snapshot={
                    "position": own_index + 1 if own_index is not None else None,
                    "offer_count": len(ranked),
                    "observed_price": str(ranked[own_index].price) if own_index is not None else None,
                    "leader_price": str(ranked[0].price),
                    "leader_merchant_id": ranked[0].merchant_id,
                    "leader_name": ranked[0].merchant_name or ranked[0].merchant_id,
                    "expected_position": decision.expected_position,
                },
            )
        except KaspiTransportError as exc:
            return TaskOutcome(snapshot, skipped=f"Kaspi unreachable ({exc})", unreachable=True)
        except KaspiError as exc:
            return TaskOutcome(snapshot, skipped=f"{type(exc).__name__}: {exc}")
        except Exception as exc:
            # One broken SKU must not take the whole cycle down.
            logger.exception("sku={} city={}: unexpected failure", snapshot.sku, snapshot.city_id)
            return TaskOutcome(snapshot, skipped=f"unexpected {type(exc).__name__}: {exc}")

    def _write(self, decided: Sequence[TaskOutcome]) -> tuple[str | None, set[int]]:
        decisions = {
            outcome.snapshot.rule_id: outcome.decision
            for outcome in decided
            if outcome.decision is not None
        }
        with self._session_factory() as session:
            rules = session.scalars(
                select(RepricerRule).where(RepricerRule.id.in_(decisions))
            ).all()
            if len(rules) != len(decisions):
                logger.warning(
                    "{} rules disappeared between loading and writing; their decisions are dropped",
                    len(decisions) - len(rules),
                )
            snapshots = {outcome.snapshot.rule_id: outcome.market_snapshot for outcome in decided}
            for rule in rules:
                rule.market_snapshot = snapshots.get(rule.id)
            updates = [PriceUpdate(rule, decisions[rule.id]) for rule in rules]
            applied_ids = {
                update.rule.id for update in updates
                if update.rule.current_price != update.decision.new_price
            }
            # sync() publishes the feed before we commit, so a failed upload
            # leaves the old prices in the database for the next cycle to retry.
            result = self._sync_manager_factory(session).sync(
                session, self._settings.merchant, updates
            )
            session.commit()
            if len(applied_ids) != result.applied:
                logger.warning("Applied-price count changed during sync; suppressing uncertain notifications")
                applied_ids.clear()
            return result.feed_url, applied_ids

    def _notify_price_changes(self, outcomes: Sequence[TaskOutcome], applied_ids: set[int]) -> None:
        if self._price_updates is None or not applied_ids:
            return
        lines = [
            f"<b>{escape(outcome.snapshot.sku)}</b> · {escape(city_name(outcome.snapshot.city_id))}: "
            f"{tenge(outcome.snapshot.current_price)} → {tenge(outcome.decision.new_price)}"
            for outcome in outcomes
            if outcome.snapshot.rule_id in applied_ids and outcome.decision is not None
        ]
        header = "💰 <b>Цены в прайсе обновлены</b>\n"
        chunk = header
        for line in lines:
            if len(chunk) + len(line) + 1 > 3500:
                self._price_updates.send(chunk)
                chunk = header
            chunk += line + "\n"
        if chunk != header:
            self._price_updates.send(chunk)

    def _log_step(self, outcome: TaskOutcome) -> None:
        snapshot = outcome.snapshot
        decision = outcome.decision
        if decision is None:
            logger.warning(
                "sku={} city={}: price left at {} ({})",
                snapshot.sku,
                snapshot.city_id,
                _money(snapshot.current_price),
                outcome.skipped,
            )
            return
        leader = decision.leader
        leader_text = (
            f"{_money(leader.price)} by {leader.merchant_id} (rating {leader.rating})"
            if leader is not None
            else "no competitors"
        )
        logger.info(
            "sku={} city={}: {} -> {} | top-1 {} | {}/{} -> position {}",
            snapshot.sku,
            snapshot.city_id,
            _money(snapshot.current_price),
            _money(decision.new_price),
            leader_text,
            decision.strategy.value,
            decision.reason.value,
            decision.expected_position,
        )

    def _notify(self, outcomes: Sequence[TaskOutcome]) -> None:
        """Turn this cycle's decisions into alerts worth a Telegram message.

        Both conditions last as long as the competitor keeps its price, so they
        are reported when they appear and forgotten when they clear.
        """
        if self._alerts is None:
            return
        raised: list[Alert] = []
        resolved: set[tuple[AlertKind, str, str]] = set()

        for outcome in outcomes:
            decision, snapshot = outcome.decision, outcome.snapshot
            if decision is None:
                continue
            leader = decision.leader
            common = {
                "sku": snapshot.sku,
                "city_id": snapshot.city_id,
                "price": decision.new_price,
                "competitor_name": outcome.leader_name,
                "competitor_price": leader.price if leader else None,
                "position": decision.expected_position,
            }

            if decision.reason is DecisionReason.PINNED_TO_MIN:
                raised.append(
                    Alert(
                        kind=AlertKind.STOP_LOSS,
                        min_price=snapshot.config.min_price,
                        **common,  # type: ignore[arg-type]
                    )
                )
            else:
                resolved.add((AlertKind.STOP_LOSS, snapshot.sku, snapshot.city_id))

            if decision.expected_position == 1:
                resolved.add((AlertKind.LOST_FIRST_PLACE, snapshot.sku, snapshot.city_id))
            elif snapshot.last_position == 1:
                # We held first place at the last change and do not now.
                raised.append(Alert(kind=AlertKind.LOST_FIRST_PLACE, **common))  # type: ignore[arg-type]

        for alert in self._throttle.fresh(raised, resolved):
            logger.info("Alert {} for sku={} city={}", alert.kind.value, alert.sku, alert.city_id)
            self._alerts.send(render_alert(alert))

    def _alert_on_proxy_trouble(self, unreachable: int, total: int) -> None:
        pool = self._proxy_pool
        if pool is not None and pool.healthy_count() == 0:
            logger.critical(
                "Every one of the {} proxies is benched: all exit IPs are blocked or dead, "
                "repricing is stalled until they recover",
                len(pool),
            )
        elif total and unreachable / total >= self._settings.failure_alert_ratio:
            logger.critical(
                "{} of {} SKUs ({:.0%}) could not reach Kaspi; check the proxies and the request interval",
                unreachable,
                total,
                unreachable / total,
            )

    # --- Threads --------------------------------------------------------------

    def _pool(self) -> ThreadPoolExecutor:
        # Kept between cycles so each thread reuses its client, and with it the
        # open connections to Kaspi.
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=self._settings.concurrency, thread_name_prefix="repricer"
            )
        return self._executor

    def _client(self) -> KaspiClient:
        client: KaspiClient | None = getattr(self._local, "client", None)
        if client is None:
            client = self._client_factory()
            self._local.client = client
            with self._clients_lock:
                self._clients.append(client)
        return client


def _money(amount: Decimal | None) -> str:
    return "unset" if amount is None else f"{amount:,.0f} ₸".replace(",", " ")
