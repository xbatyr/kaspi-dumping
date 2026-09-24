"""Only the owner can control pricing; anyone may subscribe to notifications.

The bot can pause repricing and move price floors, so the allowlist is checked
on every update, including button presses. An empty allowlist denies everyone:
a misconfigured .env must not open the bot to the world.

Public /start and /unsubscribe are allowed so viewers can opt in and out.
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
        if isinstance(event, Message) and event.text:
            command = event.text.split(maxsplit=1)[0].split("@", 1)[0].lower()
            if command in {"/start", "/unsubscribe"}:
                return await handler(event, data)
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
