"""Turning pricing decisions into prices Kaspi actually sees.

The price list feed is the whole assortment, so a price change means rebuilding
and republishing the entire catalogue. That is why the delta check comes first:
when no price actually moved there is nothing to write, nothing to build and
nothing to upload.

Ordering inside a sync is deliberate. Rows are updated and flushed, then the
feed is built and published, and only then does the caller commit. If
publishing fails, the caller rolls back and the database keeps the old prices,
so the next run tries again. The other order would be worse: a committed price
that never reached Kaspi looks like "already applied", and the delta check
would skip it forever.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from repricer.db.models import PriceHistory, Product, RepricerRule
from repricer.pricing import PricingDecision
from repricer.uploader.feed_builder import (
    KASPI_TIMEZONE,
    Availability,
    FeedOffer,
    MerchantIdentity,
    build_feed,
)
from repricer.uploader.storage import FeedStorage

DEFAULT_FILENAME_TEMPLATE = "kaspi-price-list-{merchant_id}.xml"


@dataclass(frozen=True, slots=True)
class CatalogItem:
    """Everything except the price that an offer needs in the feed."""

    sku: str
    model: str
    brand: str
    availabilities: tuple[Availability, ...]
    #: The merchant's own price, used as the feed's <price>. Without one the
    #: highest city price is used, which is the safe guess for a city we have no
    #: rule for.
    base_price: Decimal | None = None


@dataclass(frozen=True, slots=True)
class ExcludedOffer:
    """A product that cannot go into the feed, and why."""

    sku: str
    reason: str


@dataclass(frozen=True, slots=True)
class Catalog:
    items: tuple[CatalogItem, ...]
    excluded: tuple[ExcludedOffer, ...] = ()


class CatalogSource(Protocol):
    """Where product data (but never prices) comes from.

    Implement this over 1C, MoySklad or any other system that owns stock; prices
    always come from the repricer's own tables.
    """

    def load(self, merchant_id: str) -> Catalog: ...


@dataclass(frozen=True, slots=True)
class PriceUpdate:
    """One rule and what the engine decided for it."""

    rule: RepricerRule
    decision: PricingDecision


@dataclass(frozen=True, slots=True)
class SyncResult:
    #: Rules whose price actually moved and were written to the database.
    applied: int
    #: Rules the engine re-confirmed at their current price: no work needed.
    skipped_unchanged: int
    offers_published: int
    feed_url: str | None
    excluded: tuple[ExcludedOffer, ...] = ()

    @property
    def published(self) -> bool:
        return self.feed_url is not None


class DatabaseCatalog:
    """Catalogue from our own tables: products plus their pickup points."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def load(self, merchant_id: str) -> Catalog:
        statement = (
            select(Product)
            .where(Product.merchant_id == merchant_id, Product.is_active)
            .options(selectinload(Product.availabilities))
            .order_by(Product.sku)
        )
        items: list[CatalogItem] = []
        excluded: list[ExcludedOffer] = []
        for product in self._session.scalars(statement):
            brand = (product.brand or "").strip()
            in_stock = [entry for entry in product.availabilities if entry.store_id]
            if not brand:
                excluded.append(ExcludedOffer(product.sku, "no brand"))
            elif not in_stock:
                excluded.append(ExcludedOffer(product.sku, "no pickup point"))
            else:
                items.append(
                    CatalogItem(
                        sku=product.sku,
                        model=product.title,
                        brand=brand,
                        availabilities=tuple(
                            Availability(
                                store_id=entry.store_id,
                                available=entry.available,
                                stock_count=entry.stock_count,
                                preorder_days=entry.preorder_days,
                            )
                            for entry in in_stock
                        ),
                        base_price=product.base_price,
                    )
                )
        return Catalog(tuple(items), tuple(excluded))


class SyncManager:
    """Applies pricing decisions and republishes the price list feed."""

    def __init__(
        self,
        *,
        catalog: CatalogSource,
        storage: FeedStorage,
        filename_template: str = DEFAULT_FILENAME_TEMPLATE,
        # Kaspi's guide presents price and cityprices as alternatives but takes
        # both; the base price covers cities that have no rule of their own, and
        # the highest city price is the safe value for a city we know nothing about.
        include_base_price: bool = True,
        clock: Callable[[], datetime] = lambda: datetime.now(KASPI_TIMEZONE),
    ) -> None:
        self._catalog = catalog
        self._storage = storage
        self._filename_template = filename_template
        self._include_base_price = include_base_price
        self._clock = clock

    def sync(
        self,
        session: Session,
        merchant: MerchantIdentity,
        updates: Sequence[PriceUpdate],
        *,
        force: bool = False,
    ) -> SyncResult:
        """Write the decisions that changed a price, then republish the feed.

        The caller owns the transaction and commits once this returns. ``force``
        republishes even when no price moved, which is what a first run or a
        feed lost on the storage side needs.
        """
        applied, skipped = self._apply(session, updates)
        if applied == 0 and not force:
            logger.info(
                "merchant={}: {} rules re-confirmed at their current price, feed left alone",
                merchant.merchant_id,
                skipped,
            )
            return SyncResult(applied=0, skipped_unchanged=skipped, offers_published=0, feed_url=None)

        # Make the new prices visible to the queries the feed is built from.
        session.flush()
        offers, excluded = self._collect_offers(session, merchant.merchant_id)
        feed = build_feed(merchant, offers, generated_at=self._clock())
        filename = self._filename_template.format(merchant_id=merchant.merchant_id)
        feed_url = self._storage.publish(filename, feed)
        logger.info(
            "merchant={}: {} prices applied, {} unchanged, {} offers published to {}",
            merchant.merchant_id,
            applied,
            skipped,
            len(offers),
            feed_url,
        )
        return SyncResult(
            applied=applied,
            skipped_unchanged=skipped,
            offers_published=len(offers),
            feed_url=feed_url,
            excluded=excluded,
        )

    def _apply(self, session: Session, updates: Sequence[PriceUpdate]) -> tuple[int, int]:
        evaluated_at = self._clock()
        applied = skipped = 0
        for update in updates:
            rule, decision = update.rule, update.decision
            # The rule row, not decision.previous_price, is the source of truth:
            # the price may have moved between evaluation and sync.
            current_price = rule.current_price
            rule.last_evaluated_at = evaluated_at
            if current_price is not None and decision.new_price == current_price:
                skipped += 1
                continue
            history = PriceHistory.from_decision(
                product_id=rule.product_id, city_id=rule.city_id, decision=decision
            )
            history.old_price = current_price
            session.add(history)
            rule.current_price = decision.new_price
            applied += 1
        return applied, skipped

    def _collect_offers(
        self, session: Session, merchant_id: str
    ) -> tuple[list[FeedOffer], tuple[ExcludedOffer, ...]]:
        return collect_feed_offers(
            session,
            merchant_id,
            catalog=self._catalog,
            include_base_price=self._include_base_price,
        )



def collect_feed_offers(
    session: Session,
    merchant_id: str,
    *,
    catalog: CatalogSource,
    include_base_price: bool = True,
) -> tuple[list[FeedOffer], tuple[ExcludedOffer, ...]]:
    """Join the catalogue with the prices currently in the database.

    Shared by the sync manager, which publishes the feed after a repricing run,
    and by the API endpoint that serves the same feed to Kaspi on demand.
    """
    loaded = catalog.load(merchant_id)
    prices = current_prices(session, merchant_id)
    offers: list[FeedOffer] = []
    excluded = list(loaded.excluded)
    for item in loaded.items:
        city_prices = prices.get(item.sku)
        if not city_prices:
            excluded.append(ExcludedOffer(item.sku, "no price for any city"))
            continue
        base_price = item.base_price or max(city_prices.values())
        offers.append(
            FeedOffer(
                sku=item.sku,
                model=item.model,
                brand=item.brand,
                availabilities=item.availabilities,
                price=base_price if include_base_price else None,
                city_prices=city_prices,
            )
        )
    for offer in excluded:
        logger.warning(
            "merchant={}: offer {} left out of the feed: {}", merchant_id, offer.sku, offer.reason
        )
    return offers, tuple(excluded)


def current_prices(session: Session, merchant_id: str) -> dict[str, dict[str, Decimal]]:
    """The price each active SKU currently holds, per city."""
    statement = (
        select(Product.sku, RepricerRule.city_id, RepricerRule.current_price)
        .join(RepricerRule, RepricerRule.product_id == Product.id)
        .where(
            Product.merchant_id == merchant_id,
            Product.is_active,
            RepricerRule.is_active,
            RepricerRule.current_price.is_not(None),
        )
    )
    prices: dict[str, dict[str, Decimal]] = defaultdict(dict)
    for sku, city_id, price in session.execute(statement):
        if price is not None:  # guaranteed by the WHERE clause; keeps types honest
            prices[sku][city_id] = price
    return prices
