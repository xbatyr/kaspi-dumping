"""The Telegram side: commands, buttons, long polling.

Deliberately thin. Every handler parses the update, calls a function from
``actions`` or ``reports`` and sends what comes back, so the logic can be tested
without Telegram. Synchronous like the database and the scraper: one owner
sends a few commands a day, and a thread per update would buy nothing.
"""

from __future__ import annotations

import threading
import os
from pathlib import Path
from collections.abc import Callable
from datetime import datetime, timedelta
from html import escape
from typing import Any, Protocol

from loguru import logger
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from repricer.cities import DEFAULT_CITY_ID
from repricer.scraper import KaspiClient, KaspiError, RateLimiter
from repricer.telegram.actions import (
    MIN_PRICE_STEPS,
    adjust_min_price,
    find_product,
    pause_all,
    render_min_price_change,
    render_offers,
    resume_all,
)
from repricer.telegram.api import TelegramApi, TelegramError, Update
from repricer.telegram.auth import OwnerOnly
from repricer.telegram.reports import (
    daily_summary,
    render_status,
    render_summary,
    status_report,
)
from repricer.telegram.settings import TelegramSettings
from repricer.telegram.subscriptions import subscribe, unsubscribe
from repricer.uploader import KASPI_TIMEZONE, MerchantIdentity

HELP = (
    "🤖 <b>Kaspi Repricer</b>\n\n"
    "/status — что сейчас с ценами и позициями\n"
    "/sku &lt;SKU или ID карточки&gt; — цены конкурентов и правка мин. цены\n"
    "/summary — сводка за сегодня\n"
    "/stop_all — поставить на паузу все правила\n"
    "/resume_all — снять с паузы"
)
#: How long one getUpdates call waits for something to happen.
POLL_SECONDS = 20
#: Longest pause between retries while Telegram cannot be reached.
MAX_BACKOFF_SECONDS = 60.0

Keyboard = dict[str, list[list[dict[str, str]]]]


class BotApi(Protocol):
    """What the bot needs from Telegram; TelegramApi, or a fake in tests."""

    def get_updates(self, offset: int, *, wait: int) -> list[dict[str, Any]]: ...

    def send_message(
        self, chat_id: int, text: str, *, reply_markup: Keyboard | None = None
    ) -> None: ...

    def edit_message_text(self, chat_id: int, message_id: int, text: str) -> None: ...

    def answer_callback(self, callback_id: str, text: str | None = None) -> None: ...


def min_price_keyboard(rule_id: int) -> Keyboard:
    return {
        "inline_keyboard": [
            [
                {"text": f"мин {percent:+d}%", "callback_data": f"min:{rule_id}:{percent}"}
                for percent in MIN_PRICE_STEPS
            ]
        ]
    }


STOP_KEYBOARD: Keyboard = {
    "inline_keyboard": [
        [
            {"text": "Да, стоп", "callback_data": "stop:yes"},
            {"text": "Отмена", "callback_data": "stop:no"},
        ]
    ]
}


class RepricerBot:
    """Turns updates into actions and replies."""

    def __init__(
        self,
        api: BotApi,
        *,
        session_factory: Callable[[], Session],
        client_factory: Callable[[], KaspiClient],
        merchant: MerchantIdentity,
        allowed_chat_ids: frozenset[int],
        summary_chat_id: int | None = None,
        summary_at: str = "20:00",
        proposal_reviewer: Callable[[int, bool, int], str] | None = None,
    ) -> None:
        self._api = api
        self._session_factory = session_factory
        self._client_factory = client_factory
        self._merchant = merchant
        self._guard = OwnerOnly(allowed_chat_ids)
        self._summary_chat_id = summary_chat_id
        self._summary_at = summary_at
        self._proposal_reviewer = proposal_reviewer
        self._commands: dict[str, Callable[[int, str], None]] = {
            "/start": self._start,
            "/unsubscribe": self._unsubscribe,
            "/help": self._help,
            "/status": self._status,
            "/summary": self._summary,
            "/stop_all": self._stop_all,
            "/resume_all": self._resume_all,
            "/sku": self._sku,
        }

    # --- Updates --------------------------------------------------------------

    def handle(self, update: Update) -> None:
        # Approval authorization is checked against current DB settings, not a
        # list captured when this long-running process was started.
        if update.chat_id is not None and update.callback_id and update.callback_data.startswith(("price:", "prices:")):
            self._price_button(update, update.chat_id)
            return
        if update.chat_id is None or not self._guard.allows(update):
            return
        if update.callback_id is not None:
            self._button(update, update.chat_id)
            return
        command, args = update.command
        handler = self._commands.get(command)
        if handler is not None:
            handler(update.chat_id, args)

    def poll_forever(self, stop: threading.Event) -> None:
        """Long-poll Telegram until ``stop`` is set, sending the daily summary on time."""
        if self._summary_chat_id is None:
            logger.warning("No chat to send the daily summary to; it is not sent")
        next_summary = _next_occurrence(self._summary_at)
        offset = 0
        backoff = 1.0
        while not stop.is_set():
            try:
                updates = self._api.get_updates(offset, wait=POLL_SECONDS)
            except TelegramError as exc:
                logger.warning("Telegram unreachable ({}); retrying in {:.0f}s", exc, backoff)
                if stop.wait(backoff):
                    return
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
                continue
            backoff = 1.0
            for raw in updates:
                update_id = raw.get("update_id")
                if isinstance(update_id, int):
                    # Confirmed with the next call, so a failing update is not
                    # retried forever.
                    offset = max(offset, update_id + 1)
                update = Update.parse(raw)
                if update is None:
                    continue
                try:
                    self.handle(update)
                except Exception:  # noqa: BLE001 - one bad update must not stop the bot
                    logger.exception("Telegram update {} failed", update_id)
            if datetime.now(KASPI_TIMEZONE) >= next_summary:
                self.send_daily_summary()
                next_summary = _next_occurrence(self._summary_at)

    def send_daily_summary(self) -> None:
        if self._summary_chat_id is None:
            return
        try:
            with self._session_factory() as session:
                text = render_summary(
                    daily_summary(session, self._merchant.merchant_id, since=start_of_today())
                )
            self._api.send_message(self._summary_chat_id, text)
        except Exception:  # noqa: BLE001 - a missed summary must not kill the bot
            logger.exception("Could not send the daily summary")

    # --- Commands -------------------------------------------------------------

    def _start(self, chat_id: int, _args: str) -> None:
        with self._session_factory() as session:
            subscribe(session, self._merchant.merchant_id, chat_id)
        self._api.send_message(chat_id, "Вы подписаны на изменения цен. Отключить уведомления: /unsubscribe")

    def _unsubscribe(self, chat_id: int, _args: str) -> None:
        with self._session_factory() as session:
            unsubscribe(session, self._merchant.merchant_id, chat_id)
        self._api.send_message(chat_id, "Уведомления о ценах отключены. Вернуться: /start")

    def _help(self, chat_id: int, _args: str) -> None:
        self._api.send_message(chat_id, HELP)

    def _status(self, chat_id: int, _args: str) -> None:
        with self._session_factory() as session:
            text = render_status(status_report(session, self._merchant.merchant_id))
        self._api.send_message(chat_id, text)

    def _summary(self, chat_id: int, _args: str) -> None:
        with self._session_factory() as session:
            text = render_summary(
                daily_summary(session, self._merchant.merchant_id, since=start_of_today())
            )
        self._api.send_message(chat_id, text)

    def _stop_all(self, chat_id: int, _args: str) -> None:
        # Pausing the whole store is one tap away from a fat finger, so it asks first.
        self._api.send_message(
            chat_id, "⛔️ Поставить на паузу <b>все</b> правила репрайсера?", reply_markup=STOP_KEYBOARD
        )

    def _resume_all(self, chat_id: int, _args: str) -> None:
        with self._session_factory() as session:
            resumed = resume_all(session, self._merchant.merchant_id)
        self._api.send_message(chat_id, f"▶️ Снято с паузы правил: <b>{resumed}</b>.")

    def _sku(self, chat_id: int, needle: str) -> None:
        if not needle:
            self._api.send_message(chat_id, "Укажите SKU или ID карточки: <code>/sku IPH13-128</code>")
            return
        with self._session_factory() as session:
            product = find_product(session, self._merchant.merchant_id, needle)
            if product is None:
                self._api.send_message(chat_id, f"Товар <code>{escape(needle)}</code> не найден.")
                return
            rules = sorted(product.rules, key=lambda rule: rule.city_id)
            city_id = rules[0].city_id if rules else DEFAULT_CITY_ID
            kaspi_product_id, rule_ids = product.kaspi_product_id, [rule.id for rule in rules]

        # Outside the session: a slow Kaspi must not hold a database connection.
        try:
            client = self._client_factory()
            try:
                offers = client.get_product_offers(kaspi_product_id, city_id)
            finally:
                client.close()
        except (KaspiError, ValueError) as exc:
            self._api.send_message(chat_id, f"Не удалось получить цены с Kaspi: {escape(str(exc))}")
            return

        with self._session_factory() as session:
            product = find_product(session, self._merchant.merchant_id, needle)
            if product is None:
                text = "Товар исчез из базы, пока мы ходили в Kaspi."
            else:
                rule = next((item for item in product.rules if item.city_id == city_id), None)
                text = render_offers(
                    product, rule, offers, self._merchant.merchant_id, city_id=city_id
                )
        self._api.send_message(
            chat_id, text, reply_markup=min_price_keyboard(rule_ids[0]) if rule_ids else None
        )

    # --- Buttons --------------------------------------------------------------

    def _price_button(self, update: Update, chat_id: int) -> None:
        assert update.callback_id is not None
        try:
            _, raw_id, choice = update.callback_data.split(":")
            proposal_id = int(raw_id)
            if proposal_id < 1 or choice not in {"yes", "no"}:
                raise ValueError("invalid proposal callback")
        except ValueError:
            self._api.answer_callback(update.callback_id, "Некорректное предложение")
            return
        with self._session_factory() as session:
            from repricer.db.settings_store import settings_or_none
            shop = settings_or_none(session)
            from repricer.telegram.subscriptions import can_review_prices
            authorized = shop is not None and can_review_prices(
                session, self._merchant.merchant_id, str(chat_id), shop.telegram_chat_ids)
        if not authorized:
            self._api.answer_callback(update.callback_id, "Сначала подпишитесь на бота: /start")
            return
        self._api.answer_callback(update.callback_id, "Проверяем актуальную цену…")
        if self._proposal_reviewer is None:
            self._api.send_message(chat_id, "Подтверждение сейчас недоступно. Цена не изменена.")
            return
        answer = self._proposal_reviewer(proposal_id, choice == "yes", chat_id)
        # Failed market fetches are retryable: keep the original buttons.
        if answer.startswith("Не удалось проверить") or update.callback_data.startswith("prices:"):
            self._api.send_message(chat_id, answer)
        else:
            self._replace(update, chat_id, answer)

    def _button(self, update: Update, chat_id: int) -> None:
        assert update.callback_id is not None
        data = update.callback_data
        if data == "stop:no":
            self._api.answer_callback(update.callback_id, "Отменено")
            self._replace(update, chat_id, "Отменено, ничего не изменилось.")
        elif data == "stop:yes":
            with self._session_factory() as session:
                paused = pause_all(session, self._merchant.merchant_id)
            self._api.answer_callback(update.callback_id, "Готово")
            self._replace(
                update,
                chat_id,
                f"⛔️ Репрайсер остановлен: правил на паузе — <b>{paused}</b>.\n"
                "Вернуть в работу: /resume_all",
            )
        elif data.startswith("min:"):
            self._change_min_price(update, chat_id)
        else:
            # Stops the spinner on a button this version no longer knows.
            self._api.answer_callback(update.callback_id)

    def _change_min_price(self, update: Update, chat_id: int) -> None:
        assert update.callback_id is not None
        try:
            _, raw_rule_id, raw_percent = update.callback_data.split(":")
            rule_id, percent = int(raw_rule_id), int(raw_percent)
        except ValueError:
            self._api.answer_callback(update.callback_id)
            return
        with self._session_factory() as session:
            change = adjust_min_price(session, self._merchant.merchant_id, rule_id, percent)
            answer = render_min_price_change(change) if change else None
        self._api.answer_callback(update.callback_id, "Готово" if answer else "Правило не найдено")
        if answer:
            self._api.send_message(chat_id, answer, reply_markup=min_price_keyboard(rule_id))

    def _replace(self, update: Update, chat_id: int, text: str) -> None:
        if update.message_id is not None:
            self._api.edit_message_text(chat_id, update.message_id, text)


def start_of_today() -> datetime:
    now = datetime.now(KASPI_TIMEZONE)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def seconds_until(clock_time: str) -> float:
    """Seconds from now until the next HH:MM in Kazakhstan time."""
    return (_next_occurrence(clock_time) - datetime.now(KASPI_TIMEZONE)).total_seconds()


def _next_occurrence(clock_time: str) -> datetime:
    hour, _, minute = clock_time.partition(":")
    now = datetime.now(KASPI_TIMEZONE)
    target = now.replace(hour=int(hour), minute=int(minute or 0), second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def run_bot(
    settings: TelegramSettings,
    stop: threading.Event,
    *,
    session_factory: Callable[[], Session] | None = None,
) -> None:
    """Poll Telegram for this shop until ``stop`` is set."""
    if not settings.bot_token:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set")
    if session_factory is None:
        session_factory = sessionmaker(create_engine(settings.database_url, pool_pre_ping=True))
    limiter = RateLimiter(1.0)
    api = TelegramApi(settings.bot_token)
    def review(proposal_id: int, approve: bool, chat_id: int) -> str:
        from repricer.db.settings_store import load_settings
        from repricer.service import RuntimeConfig, WorkerService
        assert session_factory is not None
        with session_factory() as session:
            config = RuntimeConfig.from_settings(load_settings(session))
        if config is None:
            return "Магазин не настроен. Цена не изменена."
        service = WorkerService(session_factory=session_factory,
            feed_dir=Path(os.getenv("FEED_DIR", "/tmp/repricer-feeds")),
            feed_base_url=os.getenv("FEED_BASE_URL"))
        with service._build_worker(config) as worker:
            return worker.review_proposal(proposal_id, approve, chat_id)

    bot = RepricerBot(
        api,
        session_factory=session_factory,
        client_factory=lambda: KaspiClient(rate_limiter=limiter),
        merchant=settings.merchant,
        allowed_chat_ids=settings.allowed_chat_ids,
        summary_chat_id=settings.target_chat_id,
        summary_at=settings.summary_at,
        proposal_reviewer=review,
    )
    logger.info(
        "Bot started for merchant {}, {} chat(s) allowed",
        settings.merchant.merchant_id,
        len(settings.allowed_chat_ids),
    )
    try:
        bot.poll_forever(stop)
    finally:
        api.close()
