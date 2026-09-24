"""Request and response models. These are the API contract and the OpenAPI schema.

Money is a Decimal of whole tenge everywhere, matching what Kaspi accepts and
what the engine produces.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, model_validator

from repricer.pricing import MAX_TARGET_POSITION, DecisionReason, PricingStrategy

CityId = Annotated[
    str,
    Field(
        pattern=r"^\d{1,16}$",
        examples=["750000000"],
        description="Kaspi cityId: 750000000 is Almaty, 710000000 is Astana.",
    ),
]
Sku = Annotated[str, Field(min_length=1, max_length=128, examples=["IPH17-256"])]
Money = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2, examples=["362000"])]
#: Whether a price came from Python or from a NUMERIC(12, 2) column decides how
#: many trailing zeros a Decimal carries. Normalising on the way out keeps the
#: JSON stable: "362000", never "362000.00" one call and "362000" the next.
MoneyOut = Annotated[
    Decimal,
    PlainSerializer(lambda value: format(value.normalize(), "f"), return_type=str, when_used="json"),
]
TargetPosition = Annotated[int, Field(ge=1, le=MAX_TARGET_POSITION)]


class RuleFields(BaseModel):
    """The settings of one rule, shared by create and update."""

    strategy: PricingStrategy
    min_price: Money = Field(description="Stop-loss floor; no strategy may price below it.")
    max_price: Money
    step: int = Field(default=1, ge=1, description="Undercut or follow step, in whole tenge.")
    target_position: TargetPosition | None = Field(
        default=None, description="Place to fight for; required by the target_position strategy."
    )
    ignored_merchants: list[str] = Field(
        default_factory=list,
        description="Merchant IDs never treated as competitors: partner or sister stores.",
    )
    is_active: bool = True

    @model_validator(mode="after")
    def check_consistency(self) -> Self:
        if self.max_price < self.min_price:
            raise ValueError("max_price must not be below min_price")
        if self.strategy is PricingStrategy.TARGET_POSITION and self.target_position is None:
            raise ValueError("the target_position strategy needs a target_position")
        cleaned = [merchant.strip() for merchant in self.ignored_merchants]
        if any(not merchant for merchant in cleaned):
            raise ValueError("ignored_merchants must not contain empty IDs")
        self.ignored_merchants = list(dict.fromkeys(cleaned))
        return self


class RuleCreate(RuleFields):
    product_sku: Sku
    city_id: CityId


class RuleUpdate(RuleFields):
    """A full replacement of a rule's settings; the product and city stay put."""


class RuleStatusOut(BaseModel):
    """What the repricer worked out for this rule the last time it changed the price."""

    model_config = ConfigDict(from_attributes=True)

    #: The price the engine computed, which is also what was pushed to Kaspi.
    computed_price: MoneyOut
    #: Place we expected to land in with that price: the badge in the dashboard.
    expected_position: int
    competitor_top1_price: MoneyOut | None
    competitor_top1_merchant_id: str | None
    strategy_used: PricingStrategy
    reason: DecisionReason
    changed_at: datetime


class RuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    city_id: str
    strategy: PricingStrategy
    min_price: MoneyOut
    max_price: MoneyOut
    step: int
    target_position: int | None
    ignored_merchants: list[str]
    is_active: bool
    #: Price the worker last applied in this city; null until the first run.
    current_price: MoneyOut | None
    last_evaluated_at: datetime | None
    #: Null while the price has never moved, which is also true right after the
    #: rule is created.
    last_change: RuleStatusOut | None = None


class ProductRulesOut(BaseModel):
    """A product with its per-city rules and their current status."""

    model_config = ConfigDict(from_attributes=True)

    sku: str
    title: str
    kaspi_product_id: str
    brand: str | None
    base_price: MoneyOut | None
    is_active: bool
    rules: list[RuleOut]


class RuleListOut(BaseModel):
    items: list[ProductRulesOut]
    total: int = Field(description="Products matching the filters, ignoring limit and offset.")
    limit: int
    offset: int


class BulkToggleIn(BaseModel):
    """Switch the repricer on or off for many rules at once."""

    is_active: bool
    rule_ids: list[int] = Field(default_factory=list)
    product_skus: list[Sku] = Field(
        default_factory=list, description="Every rule of these products, optionally one city."
    )
    city_id: CityId | None = Field(default=None, description="Only with product_skus.")

    @model_validator(mode="after")
    def check_target(self) -> Self:
        if bool(self.rule_ids) == bool(self.product_skus):
            raise ValueError("pass either rule_ids or product_skus, not both and not neither")
        if self.city_id is not None and not self.product_skus:
            raise ValueError("city_id only narrows product_skus")
        return self


class BulkToggleOut(BaseModel):
    updated: int
    rule_ids: list[int]


class HistoryEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_sku: str
    city_id: str
    old_price: MoneyOut | None
    new_price: MoneyOut
    competitor_top1_price: MoneyOut | None
    competitor_top1_merchant_id: str | None
    strategy_used: PricingStrategy
    reason: DecisionReason
    expected_position: int
    competitor_count: int
    created_at: datetime


class HistoryOut(BaseModel):
    sku: str
    items: list[HistoryEntryOut]
    total: int
    limit: int
    offset: int


class AvailabilityIn(BaseModel):
    """Stock at one pickup point; the feed cannot be built without at least one."""

    store_id: str = Field(min_length=1, max_length=64, examples=["PP1"])
    available: bool = True
    stock_count: int | None = Field(default=None, ge=0)
    preorder_days: int | None = Field(default=None, ge=0, le=30)


class RuleInline(BaseModel):
    """Settings for one city, sent together with the product it belongs to."""

    city_id: CityId
    strategy: PricingStrategy = PricingStrategy.BEAT_FIRST
    min_price: Money
    max_price: Money
    step: int = Field(default=1, ge=1)
    target_position: TargetPosition | None = None
    is_active: bool = True

    @model_validator(mode="after")
    def check_consistency(self) -> Self:
        if self.max_price < self.min_price:
            raise ValueError("max_price must not be below min_price")
        if self.strategy is PricingStrategy.TARGET_POSITION and self.target_position is None:
            raise ValueError("the target_position strategy needs a target_position")
        return self


class ProductIn(BaseModel):
    """A product as the merchant knows it, plus the Kaspi card it lives on."""

    sku: Sku
    title: str = Field(min_length=1, max_length=512)
    kaspi_product_id: str = Field(
        pattern=r"^\d{1,64}$",
        examples=["102298404"],
        description="Digits from kaspi.kz/shop/p/<slug>-<ID>/ — what the scraper watches.",
    )
    brand: str | None = Field(default=None, max_length=128)
    base_price: Money | None = Field(
        default=None, description="Feed <price> for cities without a rule."
    )
    is_active: bool = True
    availabilities: list[AvailabilityIn] = Field(default_factory=list)
    #: Per-city settings created or updated along with the product. Cities left
    #: out keep whatever they had: an import never silently drops a rule.
    rules: list[RuleInline] = Field(default_factory=list)


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sku: str
    title: str
    kaspi_product_id: str
    brand: str | None
    base_price: MoneyOut | None
    is_active: bool
    availabilities: list[AvailabilityIn]
    rules: list[RuleOut] = Field(default_factory=list)
    #: Filled in when something keeps the product out of the price list.
    feed_blocker: str | None = None


class ImportIn(BaseModel):
    items: list[ProductIn] = Field(min_length=1, max_length=1000)


class ImportError(BaseModel):
    sku: str
    reason: str


class ImportResult(BaseModel):
    created: int
    updated: int
    errors: list[ImportError] = Field(default_factory=list)


class SettingsOut(BaseModel):
    """What the settings screen shows. The Telegram token comes back masked."""

    merchant_id: str
    company: str
    merchant_rating: float | None
    proxies: list[str]
    worker_enabled: bool
    interval_seconds: int
    request_interval: float
    telegram_token_hint: str
    telegram_configured: bool
    telegram_chat_ids: list[str]
    is_ready: bool


class SettingsIn(BaseModel):
    merchant_id: str = Field(
        default="", max_length=64, description="ID вашего магазина на Kaspi."
    )
    company: str = Field(default="", max_length=255)
    merchant_rating: float | None = Field(default=None, ge=0, le=5)
    proxies: list[str] = Field(default_factory=list)
    worker_enabled: bool = False
    interval_seconds: int = Field(default=300, ge=60, le=86_400)
    request_interval: float = Field(default=2, ge=0, le=60)
    #: None keeps the stored token, "" clears it, anything else replaces it.
    telegram_bot_token: str | None = None
    telegram_chat_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def tidy(self) -> Self:
        self.merchant_id = self.merchant_id.strip()
        self.company = self.company.strip()
        self.proxies = [item.strip() for item in self.proxies if item.strip()]
        self.telegram_chat_ids = [item.strip() for item in self.telegram_chat_ids if item.strip()]
        for chat_id in self.telegram_chat_ids:
            if not chat_id.lstrip("-").isdigit():
                raise ValueError(f"chat id должен быть числом, получено «{chat_id}»")
        if self.worker_enabled and not (self.merchant_id and self.company):
            raise ValueError("перед запуском бота заполните ID магазина и название компании")
        return self


class StatusOut(BaseModel):
    """The one screen that answers "готово ли к работе?"."""

    products_total: int
    products_ready: int
    #: Reason -> how many products it keeps out of the price list.
    blockers: dict[str, int]
    rules_active: int
    rules_paused: int
    first_place: int
    #: When the worker last looked at any rule; null means it never ran.
    last_run_at: datetime | None
    changes_today: int
    feed_ready: bool


class ProductCardOut(BaseModel):
    """A card found in Kaspi's catalogue, offered to the merchant to pick from."""

    kaspi_product_id: str
    title: str
    brand: str | None
    price: MoneyOut | None
    rating: float | None
    reviews_count: int
    link: str


class CityOut(BaseModel):
    id: str
    name: str


class ErrorOut(BaseModel):
    detail: str
