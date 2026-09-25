"""Products, per-city repricing rules and the price change log."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
    true,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy import false
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import Mapped, WriteOnlyMapped, mapped_column, relationship

from repricer.db.base import Base
from repricer.pricing import (
    DEFAULT_TAX_PERCENT,
    DecisionReason,
    MarginInputs,
    PercentLimits,
    PriceLimits,
    PricingConfig,
    PricingDecision,
    PricingStrategy,
    recalculated_limits,
)


def _str_enum(enum_cls: type[StrEnum], name: str) -> SAEnum:
    # VARCHAR + CHECK rather than a native PG enum: adding a strategy is then a
    # constraint swap in a migration instead of ALTER TYPE. Values ("beat_first"),
    # not member names, are stored.
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class Product(TimestampMixin, Base):
    """Our store's listing of one Kaspi product card."""

    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("merchant_id", "sku"),
        CheckConstraint(
            "purchase_price IS NULL OR purchase_price >= 0", name="purchase_price_not_negative"
        ),
        CheckConstraint(
            "commission_percent IS NULL OR commission_percent BETWEEN 0 AND 100",
            name="commission_percent_in_range",
        ),
        CheckConstraint(
            "delivery_cost IS NULL OR delivery_cost >= 0", name="delivery_cost_not_negative"
        ),
        # The catalogue is always filtered by shop first, then narrowed by the
        # category menu or the "on sale" switch.
        Index("ix_products_merchant_category", "merchant_id", "category"),
        Index("ix_products_merchant_active", "merchant_id", "is_active"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    #: Our Kaspi merchant ID (the store that owns this listing).
    merchant_id: Mapped[str] = mapped_column(String(64))
    #: Merchant SKU, as used in the Kaspi XML price list.
    sku: Mapped[str] = mapped_column(String(128))
    #: Product card ID, from kaspi.kz/shop/p/<slug>-<id>/.
    kaspi_product_id: Mapped[str] = mapped_column(String(64), index=True)
    #: Goes into the price list feed as <model>.
    title: Mapped[str] = mapped_column(String(512))
    #: Mandatory in the feed as <brand>; nullable because products are often
    #: imported before the brand is known, and such products are left out of the
    #: feed rather than breaking it.
    brand: Mapped[str | None] = mapped_column(String(128))
    #: The merchant's own price: the feed's <price> for cities without a rule,
    #: and what FIXED_PRICE rules hold.
    base_price: Mapped[Decimal | None]
    purchase_price: Mapped[Decimal | None]
    #: Free-form group the merchant sorts and filters by ("Системные блоки").
    #: Taken from the Kaspi card when it is known, editable by hand.
    category: Mapped[str | None] = mapped_column(String(128))
    #: Kaspi's cut for this product, when it differs from the shop default.
    commission_percent: Mapped[Decimal | None]
    #: Average fulfilment cost of one order of this product, in tenge.
    delivery_cost: Mapped[Decimal | None]
    auto_decrease: Mapped[bool] = mapped_column(server_default=true(), default=True)
    auto_increase: Mapped[bool] = mapped_column(server_default=false(), default=False)
    is_active: Mapped[bool] = mapped_column(server_default=true())

    rules: Mapped[list[RepricerRule]] = relationship(
        back_populates="product", cascade="all, delete-orphan", passive_deletes=True
    )
    availabilities: Mapped[list[ProductAvailability]] = relationship(
        back_populates="product", cascade="all, delete-orphan", passive_deletes=True
    )
    # Write-only: history grows without bound, so it is never loaded wholesale.
    # Query it with ``session.scalars(product.price_history.select()...)``.
    price_history: WriteOnlyMapped[PriceHistory] = relationship(
        back_populates="product", passive_deletes=True
    )

    def refresh_percent_limits(self) -> list[RepricerRule]:
        """Bring percent-based limits back in line with the base price.

        Every write path that touches ``base_price`` calls this, so a merchant
        who raises their own price does not have to re-enter a stop-loss. Rules
        set in tenge are left exactly as they are; the list returned is only the
        rules whose limits actually moved.
        """
        return [rule for rule in self.rules if rule.refresh_limits(self.base_price)]

    def on_sale(self) -> bool:
        """What the catalogue filter calls "на продаже".

        Being switched on is not enough: Kaspi only shows an offer that some
        pickup point actually has, so a product with no stock anywhere reads as
        withdrawn to the merchant and must read the same way here.
        """
        return self.is_active and any(
            entry.available and (entry.stock_count is None or entry.stock_count > 0)
            for entry in self.availabilities
        )


class ShopSettings(TimestampMixin, Base):
    """Everything the owner configures about their shop, in one row.

    These used to live in environment variables, which meant a deploy for every
    change. Keeping them here lets the merchant fill them in on the dashboard and
    lets the worker pick them up on its next cycle.

    One shop per deployment for now, hence the single row.
    """

    __tablename__ = "shop_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="single_row"),
        CheckConstraint("interval_seconds >= 60", name="interval_not_too_fast"),
        CheckConstraint(
            "merchant_rating IS NULL OR merchant_rating BETWEEN 0 AND 5",
            name="rating_in_range",
        ),
        CheckConstraint("tax_percent BETWEEN 0 AND 100", name="tax_percent_in_range"),
        CheckConstraint(
            "commission_percent BETWEEN 0 AND 100", name="commission_percent_in_range"
        ),
        CheckConstraint("delivery_cost >= 0", name="delivery_cost_not_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False, default=1)
    #: Our store's ID on Kaspi: what tells the engine which offer is ours.
    merchant_id: Mapped[str] = mapped_column(String(64), server_default="")
    #: Goes into the price list as <company>.
    company: Mapped[str] = mapped_column(String(255), server_default="")
    #: Our rating, for the match-first strategy when our offer is off the card.
    merchant_rating: Mapped[float | None]
    #: Kazakhstani proxies for the scraper; without them Kaspi blocks the IP.
    proxies: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(ARRAY(String(255))), server_default=text("'{}'")
    )
    #: The master switch. Off means the worker looks but never changes a price.
    worker_enabled: Mapped[bool] = mapped_column(server_default=false())
    #: Seconds between cycles. Kaspi refreshes the feed hourly anyway.
    interval_seconds: Mapped[int] = mapped_column(server_default=text("300"))
    #: Minimum seconds between two requests through the same proxy.
    request_interval: Mapped[float] = mapped_column(server_default=text("2"))
    #: Stored as typed in. Anyone with access to this database can read it, so
    #: keep the database private.
    telegram_bot_token: Mapped[str] = mapped_column(String(128), server_default="")
    telegram_chat_ids: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(ARRAY(String(32))), server_default=text("'{}'")
    )
    global_strategy: Mapped[PricingStrategy | None] = mapped_column(
        _str_enum(PricingStrategy, "pricing_strategy"), nullable=True
    )
    global_step: Mapped[int] = mapped_column(server_default=text("1"))
    global_target_position: Mapped[int | None]
    global_ignored_merchants: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(ARRAY(String(64))), server_default=text("'{}'")
    )
    global_city_ids: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(ARRAY(String(16))), server_default=text("'{}'")
    )
    # Defaults of the margin calculator. A product may override the last two.
    #: Retail tax on turnover; 3% in Kazakhstan.
    tax_percent: Mapped[Decimal] = mapped_column(server_default=text("3"))
    #: Kaspi's commission from the merchant contract (usually 8–15%). Zero means
    #: "not filled in yet", and the interface says the margin is then overstated.
    commission_percent: Mapped[Decimal] = mapped_column(server_default=text("0"))
    #: Average fulfilment cost of one order, in tenge.
    delivery_cost: Mapped[Decimal] = mapped_column(server_default=text("0"))

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("id", 1)
        kwargs.setdefault("merchant_id", "")
        kwargs.setdefault("company", "")
        kwargs.setdefault("proxies", [])
        kwargs.setdefault("worker_enabled", False)
        kwargs.setdefault("interval_seconds", 300)
        kwargs.setdefault("request_interval", 2.0)
        kwargs.setdefault("telegram_bot_token", "")
        kwargs.setdefault("telegram_chat_ids", [])
        kwargs.setdefault("global_step", 1)
        kwargs.setdefault("global_ignored_merchants", [])
        kwargs.setdefault("global_city_ids", [])
        kwargs.setdefault("tax_percent", DEFAULT_TAX_PERCENT)
        kwargs.setdefault("commission_percent", Decimal(0))
        kwargs.setdefault("delivery_cost", Decimal(0))
        super().__init__(**kwargs)

    def margin_inputs(self, price: Decimal, product: Product) -> MarginInputs:
        """Calculator inputs for one product at one price.

        The product's own commission and delivery win over the shop defaults:
        a heavy item or a category with a different contract is the normal case,
        not the exception.
        """
        return MarginInputs(
            price=price,
            purchase_price=product.purchase_price,
            tax_percent=self.tax_percent,
            commission_percent=(
                product.commission_percent
                if product.commission_percent is not None
                else self.commission_percent
            ),
            delivery_cost=(
                product.delivery_cost if product.delivery_cost is not None else self.delivery_cost
            ),
        )

    @property
    def is_ready(self) -> bool:
        """Enough filled in to actually reprice."""
        return bool(self.merchant_id.strip() and self.company.strip())


class TelegramSubscriber(Base):
    """A chat that opted in by sending /start to this shop's bot."""

    __tablename__ = "telegram_subscribers"

    merchant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    subscribed_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ProductAvailability(TimestampMixin, Base):
    """Stock of one product at one of the merchant's pickup points.

    Feeds the mandatory <availabilities> block of the price list. Kaspi
    identifies a pickup point by the storeId from the merchant cabinet.
    """

    __tablename__ = "product_availabilities"
    __table_args__ = (
        UniqueConstraint("product_id", "store_id"),
        CheckConstraint("stock_count IS NULL OR stock_count >= 0", name="stock_count_not_negative"),
        CheckConstraint(
            "preorder_days IS NULL OR preorder_days >= 0", name="preorder_days_not_negative"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    product_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("products.id", ondelete="CASCADE")
    )
    #: Pickup point ID from the Kaspi merchant cabinet, e.g. "PP1".
    store_id: Mapped[str] = mapped_column(String(64))
    available: Mapped[bool] = mapped_column(server_default=true())
    stock_count: Mapped[int | None]
    #: Days until a pre-ordered item ships; Kaspi's preOrder attribute.
    preorder_days: Mapped[int | None]

    product: Mapped[Product] = relationship(back_populates="availabilities")

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("available", True)
        super().__init__(**kwargs)


class RepricerRule(TimestampMixin, Base):
    """Repricing settings for one product in one Kaspi city."""

    __tablename__ = "repricer_rules"
    __table_args__ = (
        UniqueConstraint("product_id", "city_id"),
        CheckConstraint("min_price > 0", name="min_price_positive"),
        CheckConstraint("max_price >= min_price", name="max_price_not_below_min"),
        CheckConstraint("step >= 1", name="step_positive"),
        CheckConstraint(
            "target_position IS NULL OR target_position BETWEEN 1 AND 20",
            name="target_position_in_range",
        ),
        CheckConstraint(
            "min_percent IS NULL OR min_percent BETWEEN 0 AND 90", name="min_percent_in_range"
        ),
        CheckConstraint(
            "max_percent IS NULL OR max_percent BETWEEN 0 AND 500", name="max_percent_in_range"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    product_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("products.id", ondelete="CASCADE")
    )
    #: Kaspi cityId, e.g. "750000000" (Almaty), "710000000" (Astana).
    city_id: Mapped[str] = mapped_column(String(16))
    strategy: Mapped[PricingStrategy] = mapped_column(_str_enum(PricingStrategy, "pricing_strategy"))
    #: Stop-loss floor: cost + Kaspi commission + tax + fulfilment.
    min_price: Mapped[Decimal]
    max_price: Mapped[Decimal]
    # Limits the merchant set as a share of the product's own price. They are a
    # recipe, not the answer: min_price/max_price above stay what the engine
    # reads, and are recomputed from the base price whenever it changes.
    #: Percent below the base price, e.g. 10 for "no lower than 90% of my price".
    min_percent: Mapped[Decimal | None]
    #: Percent above the base price.
    max_percent: Mapped[Decimal | None]
    step: Mapped[int] = mapped_column(server_default=text("1"))
    ignored_merchants: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(ARRAY(String(64))), server_default=text("'{}'")
    )
    #: Which place to fight for under the target_position strategy.
    target_position: Mapped[int | None]
    is_active: Mapped[bool] = mapped_column(server_default=true())
    # State kept on the rule by the repricing worker, so that scheduling a pass
    # never needs to scan price_history.
    current_price: Mapped[Decimal | None]
    last_evaluated_at: Mapped[datetime | None]
    market_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    product: Mapped[Product] = relationship(back_populates="rules")

    def __init__(self, **kwargs: Any) -> None:
        # Mirror the server defaults at construction time: they only apply on
        # INSERT, and an unsaved rule must still convert to a PricingConfig.
        kwargs.setdefault("step", 1)
        kwargs.setdefault("ignored_merchants", [])
        kwargs.setdefault("is_active", True)
        super().__init__(**kwargs)

    @property
    def percent_limits(self) -> PercentLimits | None:
        """The percentages this rule was set with, if any."""
        if self.min_percent is None and self.max_percent is None:
            return None
        return PercentLimits(min_percent=self.min_percent, max_percent=self.max_percent)

    def refresh_limits(self, base_price: Decimal | None) -> bool:
        """Recompute the tenge limits from the percentages; True if they moved."""
        updated = recalculated_limits(
            base_price, self.percent_limits, PriceLimits(self.min_price, self.max_price)
        )
        if (updated.min_price, updated.max_price) == (self.min_price, self.max_price):
            return False
        self.min_price = updated.min_price
        self.max_price = updated.max_price
        return True

    def to_pricing_config(
        self,
        *,
        own_merchant_id: str,
        own_rating: float | None = None,
        base_price: Decimal | None = None,
    ) -> PricingConfig:
        """Build the engine's input.

        The product's fields are passed in, not read through ``self.product``, so
        the call never triggers a lazy load (which fails under an async session).
        """
        return PricingConfig(
            own_merchant_id=own_merchant_id,
            strategy=self.strategy,
            min_price=self.min_price,
            max_price=self.max_price,
            step=self.step,
            ignored_merchants=frozenset(self.ignored_merchants),
            own_rating=own_rating,
            target_position=self.target_position,
            base_price=base_price,
        )


class PriceHistory(Base):
    """Append-only log of price changes: one row per change, not per evaluation.

    Strategy and market data are snapshots taken at decision time, because the
    rule may be edited or deleted later.
    """

    __tablename__ = "price_history"
    __table_args__ = (
        Index("ix_price_history_product_city_created", "product_id", "city_id", "created_at"),
        CheckConstraint("new_price > 0", name="new_price_positive"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    product_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("products.id", ondelete="CASCADE")
    )
    city_id: Mapped[str] = mapped_column(String(16))
    old_price: Mapped[Decimal | None]
    new_price: Mapped[Decimal]
    strategy_used: Mapped[PricingStrategy] = mapped_column(
        _str_enum(PricingStrategy, "pricing_strategy")
    )
    reason: Mapped[DecisionReason] = mapped_column(_str_enum(DecisionReason, "decision_reason"))
    expected_position: Mapped[int]
    competitor_count: Mapped[int]
    #: Whoever held first place when the decision was made.
    competitor_top1_merchant_id: Mapped[str | None] = mapped_column(String(64))
    competitor_top1_price: Mapped[Decimal | None]
    #: The competitor the price was derived from (see PricingDecision.reference_offer).
    reference_merchant_id: Mapped[str | None] = mapped_column(String(64))
    reference_price: Mapped[Decimal | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    product: Mapped[Product] = relationship(back_populates="price_history")

    @classmethod
    def from_decision(
        cls, *, product_id: int, city_id: str, decision: PricingDecision
    ) -> PriceHistory:
        leader, reference = decision.leader, decision.reference_offer
        return cls(
            product_id=product_id,
            city_id=city_id,
            old_price=decision.previous_price,
            new_price=decision.new_price,
            strategy_used=decision.strategy,
            reason=decision.reason,
            expected_position=decision.expected_position,
            competitor_count=len(decision.competitors),
            competitor_top1_merchant_id=leader.merchant_id if leader else None,
            competitor_top1_price=leader.price if leader else None,
            reference_merchant_id=reference.merchant_id if reference else None,
            reference_price=reference.price if reference else None,
        )
