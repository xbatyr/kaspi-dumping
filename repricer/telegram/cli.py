"""Entry point: ``repricer-bot``; one bot token from the environment or database."""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import typer
from loguru import logger

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from repricer.db.settings_store import settings_or_none
from repricer.telegram.bot import run_bot
from repricer.telegram.settings import TelegramSettings

app = typer.Typer(add_completion=False, help="Telegram bot for the Kaspi repricer.")


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

    asyncio.run(_serve(settings))


async def _serve(settings: TelegramSettings) -> None:
    """Run the bot, waiting for a configured token and merchant."""
    session_factory = sessionmaker(create_engine(settings.database_url, pool_pre_ping=True))
    announced = False
    while True:
        configured = _from_database(settings, session_factory)
        if configured is None:
            if not announced:
                logger.info(
                    "Жду настройки: нужен токен Telegram и ID магазина"
                )
                announced = True
            await asyncio.sleep(15)
            continue
        announced = False
        logger.info("Токен получен, запускаю бота")
        await run_bot(configured)


def _from_database(settings: TelegramSettings, session_factory: Any) -> TelegramSettings | None:
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


if __name__ == "__main__":
    app()
