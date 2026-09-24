"""The aiogram side: handlers, keyboards, polling.

Deliberately thin. Every handler parses the update, calls a function from
``actions`` or ``reports`` and sends what comes back, so the logic can be tested
without Telegram.

The database and the scraper are synchronous, so their calls go through
``asyncio.to_thread`` and never block the event loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from loguru import logger
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

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
from repricer.telegram.auth import OwnerOnly
from repricer.cities import DEFAULT_CITY_ID
from repricer.telegram.reports import (
    daily_summary,
    render_status,
    render_summary,
    status_report,
)
from repricer.telegram.settings import TelegramSettings
from repricer.telegram.subscriptions import subscribe, unsubscribe
from repricer.uploader import KASPI_TIMEZONE, MerchantIdentity

router = Router(name="repricer")

HELP = (
    "🤖 <b>Kaspi Repricer</b>\n\n"
    "/status — что сейчас с ценами и позициями\n"
    "/sku &lt;SKU или ID карточки&gt; — цены конкурентов и правка мин. цены\n"
    "/summary — сводка за сегодня\n"
    "/stop_all — поставить на паузу все правила\n"
    "/resume_all — снять с паузы"
)


@router.message(CommandStart())
async def start(
    message: Message, session_factory: Callable[[], Session], merchant: MerchantIdentity
) -> None:
    def save() -> None:
        with session_factory() as session:
            subscribe(session, merchant.merchant_id, message.chat.id)

    await asyncio.to_thread(save)
    await message.answer("Вы подписаны на изменения цен. Отключить уведомления: /unsubscribe")


@router.message(Command("unsubscribe"))
async def stop_subscription(
    message: Message, session_factory: Callable[[], Session], merchant: MerchantIdentity
) -> None:
    def remove() -> None:
        with session_factory() as session:
            unsubscribe(session, merchant.merchant_id, message.chat.id)

    await asyncio.to_thread(remove)
    await message.answer("Уведомления о ценах отключены. Вернуться: /start")


@router.message(Command("help"))
async def help_command(message: Message) -> None:
    await message.answer(HELP)


@router.message(Command("status"))
async def status(message: Message, session_factory: Callable[[], Session], merchant: MerchantIdentity) -> None:
    def query() -> str:
        with session_factory() as session:
            return render_status(status_report(session, merchant.merchant_id))

    await message.answer(await asyncio.to_thread(query))


@router.message(Command("summary"))
async def summary(message: Message, session_factory: Callable[[], Session], merchant: MerchantIdentity) -> None:
    def query() -> str:
        with session_factory() as session:
            return render_summary(
                daily_summary(session, merchant.merchant_id, since=start_of_today())
            )

    await message.answer(await asyncio.to_thread(query))


@router.message(Command("stop_all"))
async def stop_all(message: Message) -> None:
    # Pausing the whole store is one tap away from a fat finger, so it asks first.
    await message.answer(
        "⛔️ Поставить на паузу <b>все</b> правила репрайсера?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Да, стоп", callback_data="stop:yes"),
                    InlineKeyboardButton(text="Отмена", callback_data="stop:no"),
                ]
            ]
        ),
    )


@router.callback_query(F.data == "stop:no")
async def stop_cancelled(callback: CallbackQuery) -> None:
    await callback.answer("Отменено")
    if isinstance(callback.message, Message):
        await callback.message.edit_text("Отменено, ничего не изменилось.")


@router.callback_query(F.data == "stop:yes")
async def stop_confirmed(
    callback: CallbackQuery, session_factory: Callable[[], Session], merchant: MerchantIdentity
) -> None:
    def run() -> int:
        with session_factory() as session:
            return pause_all(session, merchant.merchant_id)

    paused = await asyncio.to_thread(run)
    await callback.answer("Готово")
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            f"⛔️ Репрайсер остановлен: правил на паузе — <b>{paused}</b>.\n"
            "Вернуть в работу: /resume_all"
        )


@router.message(Command("resume_all"))
async def resume(message: Message, session_factory: Callable[[], Session], merchant: MerchantIdentity) -> None:
    def run() -> int:
        with session_factory() as session:
            return resume_all(session, merchant.merchant_id)

    resumed = await asyncio.to_thread(run)
    await message.answer(f"▶️ Снято с паузы правил: <b>{resumed}</b>.")


@router.message(Command("sku"))
async def sku(
    message: Message,
    command: CommandObject,
    session_factory: Callable[[], Session],
    merchant: MerchantIdentity,
    client_factory: Callable[[], KaspiClient],
) -> None:
    needle = (command.args or "").strip()
    if not needle:
        await message.answer("Укажите SKU или ID карточки: <code>/sku IPH13-128</code>")
        return

    def load() -> tuple[str, str, str, list[int]] | None:
        with session_factory() as session:
            product = find_product(session, merchant.merchant_id, needle)
            if product is None:
                return None
            rules = sorted(product.rules, key=lambda rule: rule.city_id)
            city_id = rules[0].city_id if rules else DEFAULT_CITY_ID
            return product.sku, product.kaspi_product_id, city_id, [rule.id for rule in rules]

    found = await asyncio.to_thread(load)
    if found is None:
        await message.answer(f"Товар <code>{needle}</code> не найден.")
        return
    _sku, kaspi_product_id, city_id, rule_ids = found

    try:
        client = client_factory()
        offers = await asyncio.to_thread(client.get_product_offers, kaspi_product_id, city_id)
    except (KaspiError, ValueError) as exc:
        await message.answer(f"Не удалось получить цены с Kaspi: {exc}")
        return

    def render() -> str:
        with session_factory() as session:
            product = find_product(session, merchant.merchant_id, needle)
            if product is None:
                return "Товар исчез из базы, пока мы ходили в Kaspi."
            rule = next((item for item in product.rules if item.city_id == city_id), None)
            return render_offers(
                product, rule, offers, merchant.merchant_id, city_id=city_id
            )

    await message.answer(
        await asyncio.to_thread(render),
        reply_markup=min_price_keyboard(rule_ids[0]) if rule_ids else None,
    )


@router.callback_query(F.data.startswith("min:"))
async def change_min_price(
    callback: CallbackQuery, session_factory: Callable[[], Session], merchant: MerchantIdentity
) -> None:
    _, raw_rule_id, raw_percent = (callback.data or "").split(":")

    def run() -> str | None:
        with session_factory() as session:
            change = adjust_min_price(
                session, merchant.merchant_id, int(raw_rule_id), int(raw_percent)
            )
            return render_min_price_change(change) if change else None

    answer = await asyncio.to_thread(run)
    await callback.answer("Готово" if answer else "Правило не найдено")
    if answer and isinstance(callback.message, Message):
        await callback.message.answer(answer, reply_markup=min_price_keyboard(int(raw_rule_id)))


def min_price_keyboard(rule_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"мин {percent:+d}%", callback_data=f"min:{rule_id}:{percent}"
                )
                for percent in MIN_PRICE_STEPS
            ]
        ]
    )


def start_of_today() -> datetime:
    now = datetime.now(KASPI_TIMEZONE)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def build_dispatcher(
    settings: TelegramSettings,
    *,
    session_factory: Callable[[], Session],
    client_factory: Callable[[], KaspiClient],
) -> Dispatcher:
    """Wires the handlers, the allowlist and the shared objects they need."""
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    guard = OwnerOnly(settings.allowed_chat_ids)
    dispatcher.message.middleware(guard)
    dispatcher.callback_query.middleware(guard)
    dispatcher.workflow_data.update(
        session_factory=session_factory,
        client_factory=client_factory,
        merchant=settings.merchant,
    )
    return dispatcher


async def daily_summary_loop(
    bot: Bot,
    settings: TelegramSettings,
    session_factory: Callable[[], Session],
) -> None:
    """Sends the summary once a day at the configured local time."""
    chat_id = settings.target_chat_id
    if chat_id is None:
        logger.warning("No chat to send the daily summary to; the loop is idle")
        return
    while True:
        await asyncio.sleep(seconds_until(settings.summary_at))

        def query() -> str:
            with session_factory() as session:
                return render_summary(
                    daily_summary(session, settings.merchant.merchant_id, since=start_of_today())
                )

        try:
            await bot.send_message(chat_id, await asyncio.to_thread(query))
        except Exception:  # noqa: BLE001 - a missed summary must not kill the bot
            logger.exception("Could not send the daily summary")


def seconds_until(clock_time: str) -> float:
    """Seconds from now until the next HH:MM in Kazakhstan time."""
    hour, _, minute = clock_time.partition(":")
    now = datetime.now(KASPI_TIMEZONE)
    target = now.replace(hour=int(hour), minute=int(minute or 0), second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def run_bot(settings: TelegramSettings) -> None:
    if not settings.bot_token:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set")
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    session_factory = sessionmaker(engine)
    limiter = RateLimiter(1.0)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = build_dispatcher(
        settings,
        session_factory=session_factory,
        client_factory=lambda: KaspiClient(rate_limiter=limiter),
    )
    summary_task = asyncio.create_task(daily_summary_loop(bot, settings, session_factory))
    logger.info(
        "Bot started for merchant {}, {} chat(s) allowed",
        settings.merchant.merchant_id,
        len(settings.allowed_chat_ids),
    )
    try:
        await dispatcher.start_polling(bot)
    finally:
        summary_task.cancel()
        with suppress(asyncio.CancelledError):
            await summary_task
        await bot.session.close()
