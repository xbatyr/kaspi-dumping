"""Request and response models. These are the API contract and the OpenAPI schema.

Money is a Decimal of whole tenge everywhere, matching what Kaspi accepts and
what the engine produces.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, model_validator

from repricer.pricing import (
    DEFAULT_TAX_PERCENT,
    MAX_DISCOUNT_PERCENT,
    MAX_MARKUP_PERCENT,
    MAX_TARGET_POSITION,
    DecisionReason,
    MarginBreakdown,
    PricingStrategy,
)

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
#: Percent below the product's own price, e.g. 10 for "no lower than 90% of it".
DiscountPercent = Annotated[
    Decimal, Field(ge=0, le=MAX_DISCOUNT_PERCENT, max_digits=5, decimal_places=2, examples=["10"])
]
#: Percent above the product's own price.
MarkupPercent = Annotated[
    Decimal, Field(ge=0, le=MAX_MARKUP_PERCENT, max_digits=5, decimal_places=2, examples=["5"])
]
#: A share of the sale price: tax or Kaspi's commission.
RatePercent = Annotated[Decimal, Field(ge=0, le=100, max_digits=5, decimal_places=2)]
Category = Annotated[str, Field(min_length=1, max_length=128, examples=["Системные блоки"])]
#: What the catalogue can be ordered by. Margin first is how a merchant finds
#: the products that are losing money.
ProductSort = Literal[
    "sku", "title", "price_asc", "price_desc", "margin_asc", "margin_desc", "updated"
]
#: "На продаже" means switched on *and* in stock somewhere; see Product.on_sale.
SaleFilter = Literal["all", "on", "off"]
#: Category value standing for "products without a category".
NO_CATEGORY = "__none__"


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


class ProductRuleConfigurationIn(RuleFields):
    """One product's strategy, exclusions and enabled cities, saved together."""

    city_ids: list[CityId] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_cities(self) -> Self:
        if len(set(self.city_ids)) != len(self.city_ids):
            raise ValueError("city_ids must not contain duplicates")
        return self


class GlobalStrategyIn(BaseModel):
    strategy: PricingStrategy
    step: int = Field(default=1, ge=1)
    target_position: TargetPosition | None = None
    ignored_merchants: list[str] = Field(default_factory=list)
    city_ids: list[CityId] = Field(min_length=1)

    @model_validator(mode="after")
    def check(self) -> Self:
        if self.strategy in {PricingStrategy.MANUAL, PricingStrategy.FIXED_PRICE}:
            raise ValueError("выберите стратегию автоматического демпинга")
        if self.strategy is PricingStrategy.TARGET_POSITION and self.target_position is None:
            raise ValueError("укажите целевую позицию")
        if len(set(self.city_ids)) != len(self.city_ids):
            raise ValueError("города не должны повторяться")
        cleaned = [item.strip() for item in self.ignored_merchants]
        if any(not item or len(item) > 64 for item in cleaned):
            raise ValueError("неверный ID магазина в белом списке")
        self.ignored_merchants = list(dict.fromkeys(cleaned))
        return self


class GlobalStrategyOut(BaseModel):
    strategy: PricingStrategy | None
    step: int
    target_position: int | None
    ignored_merchants: list[str]
    city_ids: list[str]
    configured_products: int


class ProductPriceLimitsIn(BaseModel):
    """One product's price band, in tenge or as a share of its own price.

    Each side is set one way or the other: ``min_price`` **or** ``min_percent``.
    A percentage is stored as well as resolved, so that raising the product's own
    price later moves the limit with it instead of leaving a stale floor behind.
    """

    min_price: Money | None = None
    max_price: Money | None = None
    min_percent: DiscountPercent | None = None
    max_percent: MarkupPercent | None = None
    step: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def check(self) -> Self:
        if self.min_price is not None and self.min_percent is not None:
            raise ValueError("минимум задаётся либо в тенге, либо в процентах")
        if self.max_price is not None and self.max_percent is not None:
            raise ValueError("максимум задаётся либо в тенге, либо в процентах")
        if not any(
            value is not None
            for value in (self.min_price, self.max_price, self.min_percent, self.max_percent)
        ):
            raise ValueError("укажите минимальную или максимальную цену")
        if (
            self.min_price is not None
            and self.max_price is not None
            and self.max_price <= self.min_price
        ):
            raise ValueError("максимальная цена должна быть выше минимальной")
        return self


class MarginOut(BaseModel):
    """Where the money from one sale goes."""

    model_config = ConfigDict(from_attributes=True)

    price: MoneyOut
    commission: MoneyOut
    tax: MoneyOut
    delivery: MoneyOut
    purchase_price: MoneyOut
    profit: MoneyOut
    margin_percent: Decimal
    markup_percent: Decimal | None
    break_even_price: MoneyOut | None
    #: True when no purchase price is known, so the profit shown is optimistic.
    estimated: bool

    @classmethod
    def of(cls, breakdown: MarginBreakdown) -> MarginOut:
        return cls.model_validate(breakdown)


class ProductMarginsOut(BaseModel):
    """The same calculation at the three prices the catalogue shows."""

    current: MarginOut | None = None
    minimum: MarginOut | None = None
    maximum: MarginOut | None = None


class MarginPreviewIn(BaseModel):
    """Calculator input. Anything omitted falls back to the shop's settings."""

    model_config = ConfigDict(extra="forbid")

    price: Money
    purchase_price: Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)] | None = None
    tax_percent: RatePercent | None = None
    commission_percent: RatePercent | None = None
    delivery_cost: Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)] | None = None
    #: Takes the product's purchase price, commission and delivery as defaults.
    sku: Sku | None = None


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
    #: Set when the limit was entered as a share of the product's own price; the
    #: tenge figures above are then derived and follow the base price.
    min_percent: Decimal | None = None
    max_percent: Decimal | None = None
    step: int
    target_position: int | None
    ignored_merchants: list[str]
    is_active: bool
    #: Price the worker last applied in this city; null until the first run.
    current_price: MoneyOut | None
    last_evaluated_at: datetime | None
    market_snapshot: dict[str, str | int | None] | None = None
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
    purchase_price: MoneyOut | None = None
    category: str | None = None
    commission_percent: Decimal | None = None
    delivery_cost: MoneyOut | None = None
    auto_decrease: bool = True
    auto_increase: bool = False
    is_active: bool
    #: Switched on and in stock somewhere: what "на продаже" means in the filter.
    on_sale: bool = True
    rules: list[RuleOut]
    availabilities: list[AvailabilityIn] = Field(default_factory=list)
    #: Profit at the current, minimum and maximum price of the shown city.
    margins: ProductMarginsOut = Field(default_factory=ProductMarginsOut)


class CategoryOut(BaseModel):
    """One entry of the category menu above the catalogue."""

    #: Null for products that have no category yet.
    name: str | None
    products: int


class BulkToolsIn(BaseModel):
    """The catalogue-wide tools, as one atomic request.

    Every field is optional and independent, so the interface can offer them as
    separate switches and send whichever the merchant turned on.
    """

    model_config = ConfigDict(extra="forbid")

    #: Which products to touch; empty means every product of the shop.
    skus: list[Sku] = Field(default_factory=list, max_length=5_000)
    #: Set a floor this far below each product's own price and allow lowering.
    set_min_percent: DiscountPercent | None = None
    #: Set a ceiling this far above each product's own price and allow raising.
    set_max_percent: MarkupPercent | None = None
    #: Push today's price up to the ceiling, for when the competition has left.
    raise_to_max: bool = False
    #: Stop lowering prices of products that are not on sale right now.
    disable_decrease_when_off_sale: bool = False

    @model_validator(mode="after")
    def check_something_to_do(self) -> Self:
        if not any(
            (
                self.set_min_percent is not None,
                self.set_max_percent is not None,
                self.raise_to_max,
                self.disable_decrease_when_off_sale,
            )
        ):
            raise ValueError("выберите хотя бы одно действие")
        if len(set(self.skus)) != len(self.skus):
            raise ValueError("в списке есть повторяющиеся артикулы")
        return self


class BulkToolsOut(BaseModel):
    """What the tools actually changed, so the interface can report it."""

    products_seen: int
    limits_set: int = Field(description="Products whose floor or ceiling changed.")
    prices_raised: int = Field(description="Products whose current price moved up to the maximum.")
    decrease_disabled: int
    #: Products left untouched, with the reason: no card, no own price, and so on.
    skipped: dict[str, int] = Field(default_factory=dict)


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


class BulkRuleUpdateIn(BaseModel):
    """Change selected fields on existing rules in one atomic operation."""

    rule_ids: list[int] = Field(min_length=1, max_length=1000)
    strategy: PricingStrategy | None = None
    min_price: Money | None = None
    max_price: Money | None = None
    step: int | None = Field(default=None, ge=1)
    target_position: TargetPosition | None = None
    ignored_merchants: list[str] | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def check_changes(self) -> Self:
        if not (self.model_fields_set - {"rule_ids"}):
            raise ValueError("choose at least one setting to change")
        if "target_position" in self.model_fields_set and self.target_position is None:
            raise ValueError("target_position must be a number")
        if self.ignored_merchants is not None:
            cleaned = [merchant.strip() for merchant in self.ignored_merchants]
            if any(not merchant for merchant in cleaned):
                raise ValueError("ignored_merchants must not contain empty IDs")
            self.ignored_merchants = list(dict.fromkeys(cleaned))
        return self


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


class ProductManagementIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purchase_price: Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)] | None = None
    #: Empty string clears the category, as does null.
    category: Annotated[str, Field(max_length=128)] | None = None
    commission_percent: RatePercent | None = None
    delivery_cost: Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)] | None = None
    auto_decrease: bool | None = None
    auto_increase: bool | None = None
    #: The product's own switch: off keeps it out of the price list entirely.
    is_active: bool | None = None
    availabilities: list[AvailabilityIn] | None = None

    @model_validator(mode="after")
    def check_management(self) -> Self:
        nullable = {"purchase_price", "category", "commission_percent", "delivery_cost"}
        for name in self.model_fields_set - nullable:
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        if self.availabilities is not None:
            ids = [entry.store_id for entry in self.availabilities]
            if not ids or len(ids) != len(set(ids)):
                raise ValueError("укажите склады без повторяющихся ID")
        return self


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
        default="",
        pattern=r"^\d{0,64}$",
        examples=["102298404"],
        description="Digits from the product card URL. Empty until a Kaspi XML import is linked.",
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

    @model_validator(mode="after")
    def check_card_for_repricing(self) -> Self:
        if not self.kaspi_product_id and any(
            rule.strategy not in {PricingStrategy.MANUAL, PricingStrategy.FIXED_PRICE}
            for rule in self.rules
        ):
            raise ValueError("привяжите ID карточки Kaspi перед включением демпинга")
        return self


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sku: str
    title: str
    kaspi_product_id: str
    brand: str | None
    base_price: MoneyOut | None
    purchase_price: MoneyOut | None = None
    category: str | None = None
    commission_percent: Decimal | None = None
    delivery_cost: MoneyOut | None = None
    auto_decrease: bool = True
    auto_increase: bool = False
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


class XmlImportResult(BaseModel):
    total: int
    created: int
    updated: int
    unlinked: int
    inferred_cards: int
    preview: bool


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
    # Defaults of the margin calculator.
    tax_percent: Decimal
    commission_percent: Decimal
    delivery_cost: MoneyOut


class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    merchant_id: str = Field(
        default="", max_length=64, description="ID вашего магазина на Kaspi."
    )
    company: str = Field(default="", max_length=255)
    merchant_rating: float | None = Field(default=None, ge=0, le=5)
    proxies: list[str] = Field(default_factory=list)
    worker_enabled: bool = False
    interval_seconds: int = Field(default=300, ge=60, le=86_400)
    request_interval: float = Field(default=2, ge=0, le=60)
    telegram_chat_ids: list[str] = Field(default_factory=list)
    tax_percent: RatePercent = Field(
        default=DEFAULT_TAX_PERCENT, description="Налог с оборота; в Казахстане розничный 3%."
    )
    commission_percent: RatePercent = Field(
        default=Decimal(0), description="Комиссия Kaspi по договору, обычно 8–15%."
    )
    delivery_cost: Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)] = Decimal(0)

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
    worker_enabled: bool
    global_strategy_configured: bool


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
