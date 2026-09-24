from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable

from repricer.db import Base, PriceHistory, Product, RepricerRule
from repricer.pricing import CompetitorOffer, DecisionReason, PricingEngine, PricingStrategy

ALMATY = "750000000"
OWN = "own-store"


def make_product(sku: str = "SKU-1") -> Product:
    return Product(merchant_id=OWN, sku=sku, kaspi_product_id="100950123", title="iPhone 17 256GB")


def make_rule(product: Product, **overrides: object) -> RepricerRule:
    params: dict[str, object] = {
        "product": product,
        "city_id": ALMATY,
        "strategy": PricingStrategy.BEAT_FIRST,
        "min_price": Decimal("450000.00"),
        "max_price": Decimal("520000.00"),
    } | overrides
    return RepricerRule(**params)


# --- No database needed -----------------------------------------------------


def test_schema_compiles_for_postgres() -> None:
    dialect = postgresql.dialect()  # type: ignore[no-untyped-call]
    ddl = {
        table.name: str(CreateTable(table).compile(dialect=dialect))
        for table in Base.metadata.sorted_tables
    }

    assert set(ddl) == {
        "products",
        "repricer_rules",
        "price_history",
        "product_availabilities",
        "shop_settings",
    }
    # One shop per deployment, enforced by the database rather than by hope.
    assert "CONSTRAINT ck_shop_settings_single_row CHECK (id = 1)" in ddl["shop_settings"]
    assert "brand VARCHAR(128)" in ddl["products"]
    stores = ddl["product_availabilities"]
    assert "CONSTRAINT uq_product_availabilities_product_id_store_id UNIQUE (product_id, store_id)" in stores
    assert "CONSTRAINT ck_product_availabilities_stock_count_not_negative" in stores
    rules = ddl["repricer_rules"]
    assert "ignored_merchants VARCHAR(64)[] DEFAULT '{}' NOT NULL" in rules
    assert "CONSTRAINT ck_repricer_rules_max_price_not_below_min CHECK (max_price >= min_price)" in rules
    assert "CONSTRAINT uq_repricer_rules_product_id_city_id UNIQUE (product_id, city_id)" in rules
    # Enum values, not member names, are what gets stored and checked.
    assert "CONSTRAINT ck_repricer_rules_pricing_strategy CHECK" in rules
    assert "'beat_first'" in rules and "'BEAT_FIRST'" not in rules
    assert "CONSTRAINT ck_price_history_decision_reason CHECK" in ddl["price_history"]
    assert "product_id BIGINT NOT NULL" in ddl["price_history"]


def test_unsaved_rule_has_the_same_defaults_as_the_database() -> None:
    rule = make_rule(make_product())

    assert (rule.step, rule.ignored_merchants, rule.is_active) == (1, [], True)


def test_rule_to_pricing_config() -> None:
    rule = make_rule(make_product(), step=5, ignored_merchants=["sister", "partner"])

    cfg = rule.to_pricing_config(own_merchant_id=OWN, own_rating=4.7)

    assert cfg.own_merchant_id == OWN
    assert cfg.strategy is PricingStrategy.BEAT_FIRST
    assert (cfg.min_price, cfg.max_price, cfg.step) == (Decimal("450000.00"), Decimal("520000.00"), 5)
    assert cfg.ignored_merchants == frozenset({"sister", "partner"})
    assert cfg.own_rating == 4.7


def test_price_history_from_decision() -> None:
    decision = PricingEngine().evaluate(
        make_rule(make_product()).to_pricing_config(own_merchant_id=OWN),
        [
            CompetitorOffer("dumper", Decimal(400000)),
            CompetitorOffer("b", Decimal(480000)),
        ],
        current_price=Decimal(500000),
    )

    row = PriceHistory.from_decision(product_id=7, city_id=ALMATY, decision=decision)

    assert (row.product_id, row.city_id) == (7, ALMATY)
    assert (row.old_price, row.new_price) == (Decimal(500000), Decimal(479999))
    assert row.reason is DecisionReason.FALLBACK_POSITION
    assert (row.competitor_top1_merchant_id, row.competitor_top1_price) == ("dumper", Decimal(400000))
    assert (row.reference_merchant_id, row.reference_price) == ("b", Decimal(480000))
    assert (row.competitor_count, row.expected_position) == (2, 2)


# --- PostgreSQL -------------------------------------------------------------


def test_product_and_rule_round_trip(session: Session) -> None:
    product = make_product()
    make_rule(product, ignored_merchants=["sister"])
    session.add(product)
    session.commit()
    session.expire_all()

    rule = session.scalars(select(RepricerRule)).one()

    assert rule.strategy is PricingStrategy.BEAT_FIRST
    assert rule.min_price == Decimal("450000.00")
    assert rule.step == 1
    assert rule.is_active is True
    assert rule.ignored_merchants == ["sister"]
    assert rule.current_price is None
    assert rule.product.merchant_id == OWN
    assert rule.created_at.tzinfo is not None


def test_ignored_merchants_in_place_mutation_is_persisted(session: Session) -> None:
    rule = make_rule(make_product())
    session.add(rule)
    session.commit()

    rule.ignored_merchants.append("partner")
    session.commit()
    session.expire_all()

    assert rule.ignored_merchants == ["partner"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"min_price": Decimal(0)},
        {"max_price": Decimal("449999.99")},
        {"step": 0},
    ],
    ids=["min-not-positive", "max-below-min", "step-zero"],
)
def test_rule_check_constraints(session: Session, overrides: dict[str, object]) -> None:
    session.add(make_rule(make_product(), **overrides))

    with pytest.raises(IntegrityError):
        session.flush()


def test_one_rule_per_product_and_city(session: Session) -> None:
    product = make_product()
    make_rule(product)
    make_rule(product)
    session.add(product)

    with pytest.raises(IntegrityError):
        session.flush()


def test_unknown_strategy_is_rejected_by_database(session: Session) -> None:
    product = make_product()
    session.add(product)
    session.flush()

    with pytest.raises(IntegrityError):
        session.execute(
            text(
                "INSERT INTO repricer_rules (product_id, city_id, strategy, min_price, max_price) "
                "VALUES (:product_id, :city, 'undercut_everyone', 1, 2)"
            ),
            {"product_id": product.id, "city": ALMATY},
        )


def test_evaluate_and_record_price_change(session: Session) -> None:
    product = make_product()
    rule = make_rule(product, strategy=PricingStrategy.MATCH_FIRST, current_price=Decimal(515000))
    session.add(product)
    session.flush()

    decision = PricingEngine().evaluate(
        rule.to_pricing_config(own_merchant_id=product.merchant_id),
        [
            CompetitorOffer(OWN, Decimal(515000), rating=4.9),
            CompetitorOffer("a", Decimal(505000), rating=4.6),
            CompetitorOffer("b", Decimal(509990), rating=5.0),
        ],
        current_price=rule.current_price,
    )
    assert decision.changed
    product.price_history.add(
        PriceHistory.from_decision(product_id=product.id, city_id=rule.city_id, decision=decision)
    )
    rule.current_price = decision.new_price
    rule.last_evaluated_at = datetime.now(UTC)
    session.commit()
    session.expire_all()

    history = session.scalars(product.price_history.select()).one()
    assert history.new_price == Decimal(505000)  # matched: we out-rate the leader
    assert history.old_price == Decimal(515000)
    assert history.strategy_used is PricingStrategy.MATCH_FIRST
    assert history.reason is DecisionReason.STRATEGY_TARGET
    assert history.created_at is not None
    assert rule.current_price == Decimal(505000)
    assert rule.last_evaluated_at is not None


def test_deleting_product_cascades_to_rules_and_history(session: Session) -> None:
    product = make_product()
    make_rule(product)
    session.add(product)
    session.flush()
    product.price_history.add(
        PriceHistory(
            product_id=product.id,
            city_id=ALMATY,
            new_price=Decimal(1000),
            strategy_used=PricingStrategy.BEAT_FIRST,
            reason=DecisionReason.NO_COMPETITORS,
            expected_position=1,
            competitor_count=0,
        )
    )
    session.flush()

    session.execute(delete(Product).where(Product.id == product.id))

    assert session.scalar(select(func.count()).select_from(RepricerRule)) == 0
    assert session.scalar(select(func.count()).select_from(PriceHistory)) == 0
