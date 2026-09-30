"""Only the owner can control pricing; anyone may subscribe to notifications.

The bot can pause repricing and move price floors, so the allowlist is checked
on every update, including button presses. An empty allowlist denies everyone:
a misconfigured .env must not open the bot to the world.

Public /start and /unsubscribe are allowed so viewers can opt in and out.
"""

from __future__ import annotations

from loguru import logger

from repricer.telegram.api import Update

PUBLIC_COMMANDS = frozenset({"/start", "/unsubscribe"})


class OwnerOnly:
    def __init__(self, allowed_chat_ids: frozenset[int]) -> None:
        self._allowed = allowed_chat_ids
        if not self._allowed:
            logger.error(
                "ALLOWED_TELEGRAM_CHAT_IDS is empty: the bot will ignore every message"
            )

    def allows(self, update: Update) -> bool:
        if update.callback_id is None and update.command[0] in PUBLIC_COMMANDS:
            return True
        if update.chat_id is None or update.chat_id not in self._allowed:
            logger.warning("Ignoring update from chat {}: not in the allowlist", update.chat_id)
            return False
        return True
