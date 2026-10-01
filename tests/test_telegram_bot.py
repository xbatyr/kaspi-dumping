import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
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
from repricer.telegram.api import TelegramError, Update
from repricer.telegram.auth import OwnerOnly
from repricer.telegram.alerts import BroadcastTelegramSink
from repricer.telegram.subscriptions import subscribe, subscriber_chat_ids, unsubscribe
from repricer.telegram.bot import RepricerBot, min_price_keyboard, seconds_until
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
    # Stays a tenge under the ceiling: Min = Max would leave the shared strategy
    # no room and it would stop repricing the product.
    assert change.after == Decimal(499999)  # would have been 519 750
    assert change.clamped


def test_a_floor_moved_by_hand_stops_following_a_percentage(session: Session) -> None:
    product = make_product(session)
    rule = product.rules[0]
    rule.min_percent = Decimal(10)
    session.flush()

    adjust_min_price(session, MERCHANT, rule.id, -5)

    session.expire_all()
    assert session.scalars(select(RepricerRule)).one().min_percent is None


def test_an_unknown_rule_changes_nothing(session: Session) -> None:
    assert adjust_min_price(session, MERCHANT, 999999, -5) is None


def test_another_merchants_rule_cannot_be_touched(session: Session) -> None:
    product = make_product(session, "THEIRS", merchant_id=OTHER)

    assert adjust_min_price(session, MERCHANT, product.rules[0].id, -5) is None
    session.expire_all()
    assert session.scalars(select(RepricerRule)).one().min_price == Decimal(300000)


def test_the_keyboard_encodes_the_rule_and_the_step() -> None:
    keyboard = min_price_keyboard(17)

    data = [button["callback_data"] for row in keyboard["inline_keyboard"] for button in row]
    assert data == ["min:17:-5", "min:17:-1", "min:17:1", "min:17:5"]
    # Telegram rejects callback data over 64 bytes.
    assert all(len(item.encode()) <= 64 for item in data)


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


def test_manual_imports_are_not_counted_as_working_bot_rules(session: Session) -> None:
    product = make_product(session)
    product.rules[0].strategy = PricingStrategy.MANUAL
    session.flush()

    report = status_report(session, MERCHANT)

    assert report.products == 1
    assert report.active_rules == 0
    assert "Демпинг ещё не настроен" in render_status(report)


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


def fake_message(chat_id: int, text: str = "/status") -> Update:
    return Update(chat_id=chat_id, text=text)


def fake_callback(chat_id: int, data: str = "stop:yes") -> Update:
    return Update(chat_id=chat_id, callback_id="1", callback_data=data, message_id=5)


def test_the_owner_gets_through() -> None:
    assert OwnerOnly(frozenset({42})).allows(fake_message(42))


def test_a_stranger_is_ignored_without_a_reply(warnings_logged: list[str]) -> None:
    assert not OwnerOnly(frozenset({42})).allows(fake_message(777))
    assert any("777" in message for message in warnings_logged)


def test_button_presses_are_checked_too() -> None:
    guard = OwnerOnly(frozenset({42}))

    assert not guard.allows(fake_callback(777))
    assert guard.allows(fake_callback(42))


def test_an_empty_allowlist_locks_everyone_out(warnings_logged: list[str]) -> None:
    assert not OwnerOnly(frozenset()).allows(fake_message(42))
    assert any("ALLOWED_TELEGRAM_CHAT_IDS is empty" in message for message in warnings_logged)


def test_anyone_can_subscribe_but_not_control_the_bot() -> None:
    guard = OwnerOnly(frozenset({42}))

    assert guard.allows(fake_message(777, "/start"))
    assert guard.allows(fake_message(777, "/unsubscribe@shop_bot"))
    assert not guard.allows(fake_message(777, "/stop_all"))


def test_subscriptions_are_persistent_and_scoped_to_merchant(session: Session) -> None:
    subscribe(session, MERCHANT, 42)
    subscribe(session, MERCHANT, 77)
    subscribe(session, MERCHANT, 42)
    subscribe(session, OTHER, 99)

    assert subscriber_chat_ids(session, MERCHANT) == [42, 77]
    unsubscribe(session, MERCHANT, 42)
    assert subscriber_chat_ids(session, MERCHANT) == [77]
    assert subscriber_chat_ids(session, OTHER) == [99]


def test_price_broadcast_reaches_every_subscriber(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    subscribe(session, MERCHANT, 42)
    subscribe(session, MERCHANT, 77)
    sent: list[tuple[int, str]] = []

    class FakeTelegramSink:
        def __init__(self, _token: str, chat_id: int) -> None:
            self.chat_id = chat_id

        def send(self, text: str) -> None:
            sent.append((self.chat_id, text))

    monkeypatch.setattr("repricer.telegram.alerts.TelegramSink", FakeTelegramSink)
    BroadcastTelegramSink("test-token", MERCHANT, lambda: session).send("Новая цена")

    assert sent == [(42, "Новая цена"), (77, "Новая цена")]


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


# --- The bot against a fake Telegram ------------------------------------------


class FakeTelegram:
    """Records what the bot sends and serves scripted getUpdates batches."""

    def __init__(self, batches: list[list[dict[str, Any]] | Exception] | None = None) -> None:
        self.sent: list[tuple[int, str, Any]] = []
        self.edited: list[tuple[int, int, str]] = []
        self.answered: list[tuple[str, str | None]] = []
        self.offsets: list[int] = []
        self.stop = threading.Event()
        self.fail_next_send = False
        self._batches = list(batches or [])

    def get_updates(self, offset: int, *, wait: int) -> list[dict[str, Any]]:
        self.offsets.append(offset)
        if len(self._batches) <= 1:
            self.stop.set()  # the last batch: the loop ends after it
        if not self._batches:
            return []
        batch = self._batches.pop(0)
        if isinstance(batch, Exception):
            raise batch
        return batch

    def send_message(self, chat_id: int, text: str, *, reply_markup: Any = None) -> None:
        if self.fail_next_send:
            self.fail_next_send = False
            raise TelegramError("sendMessage: Bad Request")
        self.sent.append((chat_id, text, reply_markup))

    def edit_message_text(self, chat_id: int, message_id: int, text: str) -> None:
        self.edited.append((chat_id, message_id, text))

    def answer_callback(self, callback_id: str, text: str | None = None) -> None:
        self.answered.append((callback_id, text))


class FakeKaspi:
    def __init__(self, offers: list[Offer] | Exception) -> None:
        self._offers = offers
        self.closed = False

    def get_product_offers(self, _product_id: str, _city_id: str) -> list[Offer]:
        if isinstance(self._offers, Exception):
            raise self._offers
        return self._offers

    def close(self) -> None:
        self.closed = True


OWNER = 42


@pytest.fixture
def session_factory(session: Session) -> Any:
    connection = session.connection()

    def factory() -> Session:
        return Session(bind=connection, join_transaction_mode="create_savepoint")

    return factory


def build_bot(session_factory: Any, api: FakeTelegram, kaspi: FakeKaspi | None = None) -> RepricerBot:
    from repricer.uploader import MerchantIdentity

    return RepricerBot(
        api,
        session_factory=session_factory,
        client_factory=lambda: kaspi or FakeKaspi([]),  # type: ignore[arg-type,return-value]
        merchant=MerchantIdentity(MERCHANT, "Ромашка"),
        allowed_chat_ids=frozenset({OWNER}),
        summary_chat_id=OWNER,
    )


def message_update(update_id: int, chat_id: int, text: str) -> dict[str, Any]:
    return {"update_id": update_id, "message": {"message_id": 1, "chat": {"id": chat_id}, "text": text}}


def test_updates_are_read_from_plain_json() -> None:
    command = Update.parse(message_update(1, 42, "  /sku@shop_bot   IPH 13 "))
    press = Update.parse({"update_id": 2, "callback_query": {
        "id": "77", "data": "stop:no", "message": {"message_id": 9, "chat": {"id": 42}},
    }})

    assert command is not None and command.command == ("/sku", "IPH 13")
    assert press == Update(chat_id=42, callback_id="77", callback_data="stop:no", message_id=9)
    assert Update(chat_id=42, text="привет").command == ("", "")
    assert Update.parse({"update_id": 3, "edited_message": {}}) is None


def test_price_buttons_use_current_owner_list(session: Session, session_factory: Any) -> None:
    from repricer.db.settings_store import load_settings
    shop = load_settings(session)
    shop.telegram_chat_ids = ["77"]
    session.flush()
    api = FakeTelegram()
    bot = build_bot(session_factory, api)
    called = []
    def review(proposal_id: int, approve: bool, chat_id: int) -> str:
        called.append((proposal_id, approve, chat_id))
        return "Done"
    bot._proposal_reviewer = review
    bot.handle(Update(chat_id=42, callback_id="unauthorized", callback_data="price:9:yes", message_id=5))
    assert called == []
    bot.handle(Update(chat_id=77, callback_id="owner", callback_data="price:9:yes", message_id=5))
    assert called == [(9, True, 77)]
    assert api.edited[-1] == (77, 5, "Done")


@pytest.mark.parametrize("data", ["price:x:yes", "price:0:yes", "price:9:maybe", "price:9:yes:extra"])
def test_malformed_price_buttons_do_not_write(session: Session, session_factory: Any, data: str) -> None:
    api = FakeTelegram()
    bot = build_bot(session_factory, api)
    bot.handle(Update(chat_id=42, callback_id="invalid", callback_data=data, message_id=5))
    assert api.edited == [] and api.answered


def test_a_stranger_can_subscribe_and_nothing_more(session: Session, session_factory: Any) -> None:
    api = FakeTelegram()
    bot = build_bot(session_factory, api)

    bot.handle(fake_message(777, "/start"))
    bot.handle(fake_message(777, "/stop_all"))
    bot.handle(fake_callback(777, "stop:yes"))

    assert [chat for chat, _text, _markup in api.sent] == [777]
    assert subscriber_chat_ids(session, MERCHANT) == [777]
    assert api.answered == []


def test_stop_all_asks_first_and_then_pauses_everything(session: Session, session_factory: Any) -> None:
    make_product(session)
    api = FakeTelegram()
    bot = build_bot(session_factory, api)

    bot.handle(fake_message(OWNER, "/stop_all"))
    bot.handle(fake_callback(OWNER, "stop:yes"))

    buttons = [button["callback_data"] for row in api.sent[0][2]["inline_keyboard"] for button in row]
    assert buttons == ["stop:yes", "stop:no"]
    assert api.answered == [("1", "Готово")]
    assert api.edited[0][:2] == (OWNER, 5) and "<b>1</b>" in api.edited[0][2]
    session.expire_all()
    assert session.scalars(select(RepricerRule)).one().is_active is False


def test_sku_shows_the_competition_with_floor_buttons(session: Session, session_factory: Any) -> None:
    product = make_product(session)
    api = FakeTelegram()
    kaspi = FakeKaspi([offer("rival", 369000), offer(MERCHANT, 370112)])
    bot = build_bot(session_factory, api, kaspi)

    bot.handle(fake_message(OWNER, f"/sku {product.sku}"))

    _chat, text, markup = api.sent[0]
    assert "369 000" in text and "👉" in text
    assert markup == min_price_keyboard(product.rules[0].id)
    assert kaspi.closed


def test_sku_escapes_what_was_typed_and_reports_kaspi_trouble(
    session: Session, session_factory: Any
) -> None:
    from repricer.scraper import KaspiTransportError

    product = make_product(session)
    api = FakeTelegram()
    bot = build_bot(session_factory, api, FakeKaspi(KaspiTransportError("timeout <proxy>")))

    bot.handle(fake_message(OWNER, "/sku <b>"))
    bot.handle(fake_message(OWNER, f"/sku {product.sku}"))

    assert api.sent[0][1] == "Товар <code>&lt;b&gt;</code> не найден."
    assert api.sent[1][1] == "Не удалось получить цены с Kaspi: timeout &lt;proxy&gt;"


def test_floor_buttons_move_the_floor_and_ignore_garbage(session: Session, session_factory: Any) -> None:
    product = make_product(session, min_price=300000)
    rule_id = product.rules[0].id
    api = FakeTelegram()
    bot = build_bot(session_factory, api)

    bot.handle(fake_callback(OWNER, f"min:{rule_id}:5"))
    bot.handle(fake_callback(OWNER, "min:x:y"))
    bot.handle(fake_callback(OWNER, "min:999999:5"))

    assert api.answered == [("1", "Готово"), ("1", None), ("1", "Правило не найдено")]
    assert "315 000" in api.sent[0][1] and api.sent[0][2] == min_price_keyboard(rule_id)
    session.expire_all()
    assert session.get(RepricerRule, rule_id).min_price == Decimal(315000)  # type: ignore[union-attr]


def test_polling_survives_a_failing_update_and_confirms_every_one(session_factory: Any) -> None:
    api = FakeTelegram([
        [message_update(10, OWNER, "/help"), message_update(11, OWNER, "/help"), {"update_id": 12}],
        TelegramError("getUpdates: Bad Gateway"),
    ])
    api.fail_next_send = True
    bot = build_bot(session_factory, api)

    bot.poll_forever(api.stop)

    # The first reply failed; the second update was still handled.
    assert len(api.sent) == 1 and "/status" in api.sent[0][1]
    assert api.offsets == [0, 13]


def test_the_daily_summary_goes_to_the_owner(session_factory: Any) -> None:
    api = FakeTelegram()

    build_bot(session_factory, api).send_daily_summary()

    assert api.sent[0][0] == OWNER and "Сводка за день" in api.sent[0][1]
