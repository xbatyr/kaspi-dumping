"""Entry point: ``repricer-bot``; one bot token from the environment or database."""

from __future__ import annotations

import signal
import sys
import threading
from collections.abc import Callable
from types import FrameType

import typer
from loguru import logger
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from repricer.db.settings_store import settings_or_none
from repricer.telegram.bot import run_bot
from repricer.telegram.settings import TelegramSettings

app = typer.Typer(add_completion=False, help="Telegram bot for the Kaspi repricer.")

#: How long an update being handled may take to finish on shutdown.
SHUTDOWN_GRACE_SECONDS = 5.0


@app.command()
def run(
    bot_token: str = typer.Option("", envvar="TELEGRAM_BOT_TOKEN", help="Token from @BotFather."),
    allowed_chat_ids: str = typer.Option(
        "",
        envvar="ALLOWED_TELEGRAM_CHAT_IDS",
        help="Comma separated chat IDs allowed to use the bot.",
    ),
    database_url: str = typer.Option("", envvar="DATABASE_URL"),
    merchant_id: str = typer.Option("", envvar="KASPI_MERCHANT_ID"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Start polling Telegram."""
    logger.remove()
    logger.add(
        sys.stderr,
        level="DEBUG" if verbose else "INFO",
        format="<dim>{time:HH:mm:ss}</dim> <level>{level: <8}</level> {message}",
    )

    overrides = {
        key: value
        for key, value in {
            "telegram_bot_token": bot_token,
            "allowed_telegram_chat_ids": allowed_chat_ids,
            "database_url": database_url,
            "kaspi_merchant_id": merchant_id,
        }.items()
        if value
    }
    settings = TelegramSettings(**overrides)  # type: ignore[arg-type]
    if not settings.database_url:
        raise typer.BadParameter("нужен адрес базы", param_hint="--database-url")

    stop = threading.Event()
    _install_signal_handlers(stop)
    # Polling runs in its own thread so a stop signal is acted on at once rather
    # than after the current long poll: the main thread only waits for the
    # signal, then gives an update in progress a moment to finish.
    poller = threading.Thread(target=_serve, args=(settings, stop), name="telegram", daemon=True)
    poller.start()
    while poller.is_alive():
        if stop.wait(1.0):
            poller.join(SHUTDOWN_GRACE_SECONDS)
            break
    logger.info("Бот остановлен")


def _serve(settings: TelegramSettings, stop: threading.Event) -> None:
    """Run the bot, waiting for a configured token and merchant."""
    session_factory = sessionmaker(create_engine(settings.database_url, pool_pre_ping=True))
    announced = False
    while not stop.is_set():
        try:
            configured = _from_database(settings, session_factory)
        except Exception:  # noqa: BLE001 - a database still starting must not end the bot
            logger.exception("Не удалось прочитать настройки из базы")
            configured = None
        if configured is None:
            if not announced:
                logger.info("Жду настройки: нужен токен Telegram и ID магазина")
                announced = True
            stop.wait(15)
            continue
        announced = False
        logger.info("Токен получен, запускаю бота")
        run_bot(configured, stop, session_factory=session_factory)


def _from_database(
    settings: TelegramSettings, session_factory: Callable[[], Session]
) -> TelegramSettings | None:
    """Settings with the fixed token and owner chats, or None while empty."""
    with session_factory() as session:
        shop = settings_or_none(session)
    token = settings.bot_token or (shop.telegram_bot_token if shop else "")
    chats = ",".join(shop.telegram_chat_ids) if shop and shop.telegram_chat_ids else settings.allowed_chat_ids_raw
    merchant = (shop.merchant_id if shop else "") or settings.merchant_id
    if not token or not merchant:
        return None
    return settings.model_copy(
        update={
            "bot_token": token,
            "allowed_chat_ids_raw": chats,
            "merchant_id": merchant,
            "company": (shop.company if shop else "") or settings.company,
        }
    )


def _install_signal_handlers(stop: threading.Event) -> None:
    def handle(signum: int, _frame: FrameType | None) -> None:
        logger.info("{} получен, останавливаю бота", signal.Signals(signum).name)
        stop.set()

    for received in (signal.SIGINT, signal.SIGTERM):
        signal.signal(received, handle)


if __name__ == "__main__":
    app()
