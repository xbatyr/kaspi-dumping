"""Nobody but the owner talks to this bot.

The bot can pause repricing and move price floors, so the allowlist is checked
on every update, including button presses. An empty allowlist denies everyone:
a misconfigured .env must not open the bot to the world.

Strangers get no reply at all. An error message would confirm that the token is
live and worth brute-forcing; the chat ID still lands in the log so the owner
can add themselves.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from loguru import logger


class OwnerOnly(BaseMiddleware):
    def __init__(self, allowed_chat_ids: frozenset[int]) -> None:
        self._allowed = allowed_chat_ids
        if not self._allowed:
            logger.error(
                "ALLOWED_TELEGRAM_CHAT_IDS is empty: the bot will ignore every message"
            )

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat_id = chat_id_of(event)
        if chat_id is None or chat_id not in self._allowed:
            logger.warning("Ignoring update from chat {}: not in the allowlist", chat_id)
            return None
        return await handler(event, data)


def chat_id_of(event: TelegramObject) -> int | None:
    if isinstance(event, Message):
        return event.chat.id
    if isinstance(event, CallbackQuery):
        return event.message.chat.id if event.message else None
    return None
