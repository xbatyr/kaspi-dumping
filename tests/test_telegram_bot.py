import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.orm import Session

from repricer.db import PriceHistory, Product, ProductAvailability, RepricerRule
from repricer.pricing import DecisionReason, PricingStrategy
from repricer.scraper import Offer
from repricer.telegram.actions import (
    adjust_min_price,
    find_product,
    pause_all,
    render_offers,
    resume_all,
)
from repricer.telegram.auth import OwnerOnly
from repricer.telegram.bot import min_price_keyboard, seconds_until
from repricer.telegram.reports import (
    daily_summary,
    render_status,
    render_summary,
    status_report,
)
from repricer.telegram.settings import TelegramSettings

def telegram_settings(**overrides: Any) -> TelegramSettings:
    """Settings for a test; ``_env_file`` keeps the local .env out of the way."""
    return TelegramSettings(**{"_env_file": None, **overrides})


MERCHANT = "30123456"
OTHER = "99999999"
ALMATY, ASTANA = "750000000", "710000000"


def make_product(
    session: Session,
    sku: str = "IPH13-128",
    *,
    merchant_id: str = MERCHANT,
    cities: dict[str, int | None] | None = None,
    is_active: bool = True,
    min_price: int = 300000,
    max_price: int = 500000,
) -> Product:
    product = Product(
        merchant_id=merchant_id,
        sku=sku,
        kaspi_product_id=f"1022984{abs(hash(sku)) % 100:02d}",
        title=f"Товар {sku}",
        brand="Apple",
        is_active=True,
    )
    product.availabilities.append(ProductAvailability(store_id="PP1", stock_count=3))
    for city_id, price in (cities or {ALMATY: 370112}).items():
        product.rules.append(
            RepricerRule(
                city_id=city_id,
                strategy=PricingStrategy.BEAT_FIRST,
                min_price=Decimal(min_price),
                max_price=Decimal(max_price),
                current_price=Decimal(price) if price is not None else None,
                is_active=is_active,
            )
        )
    session.add(product)
    session.flush()
    return product


def add_history(
    session: Session,
    product: Product,
    *,
    position: int = 1,
    city_id: str = ALMATY,
    reason: DecisionReason = DecisionReason.STRATEGY_TARGET,
    when: datetime | None = None,
) -> None:
    row = PriceHistory(
        product_id=product.id,
        city_id=city_id,
        old_price=Decimal(372000),
        new_price=Decimal(370112),
        strategy_used=PricingStrategy.BEAT_FIRST,
        reason=reason,
        expected_position=position,
        competitor_count=9,
        competitor_top1_merchant_id="Kazphone",
        competitor_top1_price=Decimal(370113),
    )
    if when is not None:
        row.created_at = when
    session.add(row)
    session.flush()


def offer(merchant_id: str, price: int, name: str | None = None) -> Offer:
    return Offer(
        merchant_id=merchant_id,
        merchant_name=name or f"Shop {merchant_id}",
        price=Decimal(price),
        rating=4.8,
        reviews_count=120,
        kaspi_delivery=True,
        delivery_duration="TOMORROW",
    )


# --- /stop_all and /resume_all ------------------------------------------------


def test_stop_all_pauses_every_rule_of_the_store(session: Session) -> None:
    make_product(session, "A", cities={ALMATY: 1000, ASTANA: 1100})
    make_product(session, "B", cities={ALMATY: 2000})

    assert pause_all(session, MERCHANT) == 3
    session.expire_all()
    assert all(not rule.is_active for rule in session.scalars(select(RepricerRule)))


def test_stop_all_leaves_other_merchants_alone(session: Session) -> None:
    make_product(session, "MINE")
    make_product(session, "THEIRS", merchant_id=OTHER)

    assert pause_all(session, MERCHANT) == 1
    session.expire_all()
    theirs = session.scalars(
        select(RepricerRule).join(Product).where(Product.merchant_id == OTHER)
    ).one()
    assert theirs.is_active


def test_stop_all_counts_only_what_it_changed(session: Session) -> None:
    make_product(session, "A", is_active=False)

    assert pause_all(session, MERCHANT) == 0


def test_resume_all_brings_them_back(session: Session) -> None:
    make_product(session, "A", cities={ALMATY: 1000, ASTANA: 1100})
    pause_all(session, MERCHANT)

    assert resume_all(session, MERCHANT) == 2
    session.expire_all()
    assert all(rule.is_active for rule in session.scalars(select(RepricerRule)))


# --- /sku ---------------------------------------------------------------------


def test_a_product_is_found_by_sku_or_by_card_id(session: Session) -> None:
    product = make_product(session)

    assert find_product(session, MERCHANT, product.sku) is not None
    assert find_product(session, MERCHANT, product.kaspi_product_id) is not None
    assert find_product(session, MERCHANT, " " + product.sku + " ") is not None
    assert find_product(session, MERCHANT, "NOPE") is None
    assert find_product(session, MERCHANT, "") is None


def test_another_merchants_product_is_not_found(session: Session) -> None:
    product = make_product(session, "THEIRS", merchant_id=OTHER)

    assert find_product(session, MERCHANT, product.sku) is None


def test_offers_are_rendered_with_our_own_offer_marked(session: Session) -> None:
    product = make_product(session)
    rule = product.rules[0]
    offers = [offer("rival", 370000, "Kazphone"), offer(MERCHANT, 370112, "Наш магазин")]

    text = render_offers(product, rule, offers, MERCHANT, city_id=ALMATY)

    assert "Kazphone" in text and "370 000 ₸" in text
    assert "👉" in text  # our own line is marked
    assert "Алматы" in text
    assert "мин 300 000 ₸" in text


def test_a_missing_own_offer_is_called_out(session: Session) -> None:
    product = make_product(session)

    text = render_offers(product, product.rules[0], [offer("rival", 370000)], MERCHANT, city_id=ALMATY)

    assert "Нашего предложения на карточке нет" in text


def test_a_product_without_a_rule_for_the_city_says_so(session: Session) -> None:
    product = make_product(session)

    text = render_offers(product, None, [offer("rival", 1)], MERCHANT, city_id=ASTANA)

    assert "Правила для этого города нет" in text


def test_an_empty_offer_list_is_reported(session: Session) -> None:
    product = make_product(session)

    text = render_offers(product, product.rules[0], [], MERCHANT, city_id=ALMATY)

    assert "не вернул ни одного предложения" in text


# --- min_price buttons --------------------------------------------------------


def test_the_buttons_move_the_floor_by_a_percentage(session: Session) -> None:
    product = make_product(session, min_price=300000)
    rule_id = product.rules[0].id

    change = adjust_min_price(session, MERCHANT, rule_id, -5)

    assert change is not None
    assert (change.before, change.after) == (Decimal(300000), Decimal(285000))
    assert not change.clamped
    session.expire_all()
    assert session.scalars(select(RepricerRule)).one().min_price == Decimal(285000)


def test_the_floor_never_passes_the_ceiling(session: Session) -> None:
    product = make_product(session, min_price=495000, max_price=500000)

    change = adjust_min_price(session, MERCHANT, product.rules[0].id, 5)

    assert change is not None
    assert change.after == Decimal(500000)  # would have been 519 750
    assert change.clamped


def test_an_unknown_rule_changes_nothing(session: Session) -> None:
    assert adjust_min_price(session, MERCHANT, 999999, -5) is None


def test_another_merchants_rule_cannot_be_touched(session: Session) -> None:
    product = make_product(session, "THEIRS", merchant_id=OTHER)

    assert adjust_min_price(session, MERCHANT, product.rules[0].id, -5) is None
    session.expire_all()
    assert session.scalars(select(RepricerRule)).one().min_price == Decimal(300000)


def test_the_keyboard_encodes_the_rule_and_the_step() -> None:
    keyboard = min_price_keyboard(17)

    data = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert data == ["min:17:-5", "min:17:-1", "min:17:1", "min:17:5"]
    # Telegram rejects callback data over 64 bytes.
    assert all(len(item or "") <= 64 for item in data)


# --- /status and the daily summary -------------------------------------------


def test_status_counts_rules_and_first_places(session: Session) -> None:
    first = make_product(session, "FIRST")
    add_history(session, first, position=1)
    second = make_product(session, "SECOND")
    add_history(session, second, position=4)
    make_product(session, "PAUSED", is_active=False)

    report = status_report(session, MERCHANT)

    assert (report.products, report.active_rules, report.paused_rules) == (3, 2, 1)
    assert report.first_place == 1
    # The worst place comes first: that is the one to look at.
    assert report.lines[0].sku == "SECOND"


def test_status_text_mentions_the_products(session: Session) -> None:
    product = make_product(session)
    add_history(session, product, position=1)

    text = render_status(status_report(session, MERCHANT))

    assert "IPH13-128" in text
    assert "370 112 ₸" in text
    assert "№1" in text
    assert "Алматы" in text


def test_status_without_products_says_so(session: Session) -> None:
    assert "Товаров пока нет" in render_status(status_report(session, MERCHANT))


def test_the_summary_counts_todays_changes(session: Session) -> None:
    product = make_product(session)
    add_history(session, product, position=1)
    add_history(session, product, position=2, reason=DecisionReason.PINNED_TO_MIN)
    add_history(session, product, position=1, when=datetime.now(UTC) - timedelta(days=2))

    summary = daily_summary(session, MERCHANT, since=datetime.now(UTC) - timedelta(hours=12))

    assert summary.changes == 2  # the two-day-old row is out of the window
    assert summary.products_changed == 1
    assert summary.stop_loss_hits == 1
    assert summary.tracked_rules == 1


def test_the_summary_reports_first_places(session: Session) -> None:
    winner = make_product(session, "WIN")
    add_history(session, winner, position=1)
    loser = make_product(session, "LOSE")
    add_history(session, loser, position=6)

    summary = daily_summary(session, MERCHANT, since=datetime.now(UTC) - timedelta(hours=12))

    assert (summary.first_place, summary.tracked_rules) == (1, 2)
    text = render_summary(summary)
    assert "Цены менялись" in text and "1</b> из 2" in text


def test_a_quiet_day_is_explained(session: Session) -> None:
    make_product(session)

    text = render_summary(daily_summary(session, MERCHANT, since=datetime.now(UTC)))

    assert "Ни одна цена не изменилась" in text


# --- Access control -----------------------------------------------------------


def fake_message(chat_id: int) -> Message:
    return Message.model_validate(
        {
            "message_id": 1,
            "date": datetime.now(UTC),
            "chat": {"id": chat_id, "type": "private"},
            "text": "/status",
        }
    )


def fake_callback(chat_id: int) -> CallbackQuery:
    return CallbackQuery.model_validate(
        {
            "id": "1",
            "from": {"id": chat_id, "is_bot": False, "first_name": "Кто-то"},
            "chat_instance": "1",
            "data": "stop:yes",
            "message": {
                "message_id": 1,
                "date": datetime.now(UTC),
                "chat": {"id": chat_id, "type": "private"},
            },
        }
    )


async def handler(_event: Any, _data: dict[str, Any]) -> str:
    return "handled"


def test_the_owner_gets_through() -> None:
    guard = OwnerOnly(frozenset({42}))
    result = asyncio.run(guard(handler, fake_message(42), {}))

    assert result == "handled"


def test_a_stranger_is_ignored_without_a_reply(warnings_logged: list[str]) -> None:
    guard = OwnerOnly(frozenset({42}))
    result = asyncio.run(guard(handler, fake_message(777), {}))

    assert result is None
    assert any("777" in message for message in warnings_logged)


def test_button_presses_are_checked_too() -> None:
    guard = OwnerOnly(frozenset({42}))

    assert asyncio.run(guard(handler, fake_callback(777), {})) is None
    assert asyncio.run(guard(handler, fake_callback(42), {})) == "handled"


def test_an_empty_allowlist_locks_everyone_out(warnings_logged: list[str]) -> None:
    guard = OwnerOnly(frozenset())

    assert asyncio.run(guard(handler, fake_message(42), {})) is None
    assert any("ALLOWED_TELEGRAM_CHAT_IDS is empty" in message for message in warnings_logged)


# --- Settings -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("42", {42}),
        ("42,77", {42, 77}),
        (" 42 , 77 ", {42, 77}),
        ("42;77", {42, 77}),
        ("42,,77", {42, 77}),
        ("", set()),
        ("не число", set()),
    ],
)
def test_the_allowlist_is_parsed_from_plain_text(raw: str, expected: set[int]) -> None:
    settings = telegram_settings(allowed_telegram_chat_ids=raw)

    assert settings.allowed_chat_ids == expected


def test_alerts_go_to_the_first_allowed_chat_by_default() -> None:
    settings = telegram_settings(allowed_telegram_chat_ids="77,42")

    assert settings.target_chat_id == 42


def test_an_explicit_alert_chat_wins() -> None:
    settings = telegram_settings(allowed_telegram_chat_ids="77,42", telegram_alert_chat_id=100
    )

    assert settings.target_chat_id == 100


def test_the_summary_is_scheduled_for_the_next_occurrence() -> None:
    seconds = seconds_until("23:59")

    assert 0 < seconds <= 24 * 3600


def test_blank_values_in_env_mean_unset() -> None:
    # "KASPI_MERCHANT_RATING=" and "TELEGRAM_ALERT_CHAT_ID=" are how people leave
    # a setting empty in .env; neither may crash the process.
    settings = telegram_settings(kaspi_merchant_rating="",
        telegram_alert_chat_id="",
        allowed_telegram_chat_ids="42",
    )

    assert settings.merchant_rating is None
    assert settings.alert_chat_id is None
    assert settings.target_chat_id == 42
