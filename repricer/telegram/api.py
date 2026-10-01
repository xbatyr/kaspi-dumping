"""The few Bot API calls the bot makes, over plain HTTPS.

aiogram used to do this. It builds a pydantic model for every type in the Bot
API when imported, which cost about 160 MB of a 1 GB server for eight commands
and two kinds of button. Updates here stay plain JSON, and only the fields the
bot acts on are read.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from curl_cffi import requests as curl_requests

TELEGRAM_API = "https://api.telegram.org"


class TelegramError(Exception):
    """A Bot API call failed: no answer, or Telegram said no."""


@dataclass(frozen=True, slots=True)
class Update:
    """The part of a Telegram update the bot acts on: a message or a button press."""

    chat_id: int | None
    text: str = ""
    #: Set for a button press; answering it stops the spinner on the button.
    callback_id: str | None = None
    callback_data: str = ""
    #: The message a button belongs to, so its text can be replaced.
    message_id: int | None = None

    @classmethod
    def parse(cls, raw: Mapping[str, Any]) -> Update | None:
        """None for the kinds of update the bot does not handle."""
        message = raw.get("message")
        if isinstance(message, dict):
            text = message.get("text")
            return cls(chat_id=_chat_id(message), text=text if isinstance(text, str) else "")
        query = raw.get("callback_query")
        if isinstance(query, dict):
            source = query.get("message")
            source = source if isinstance(source, dict) else {}
            data = query.get("data")
            message_id = source.get("message_id")
            return cls(
                chat_id=_chat_id(source),
                callback_id=str(query.get("id", "")),
                callback_data=data if isinstance(data, str) else "",
                message_id=message_id if isinstance(message_id, int) else None,
            )
        return None

    @property
    def command(self) -> tuple[str, str]:
        """("/sku", "IPH13") for "/sku@shop_bot IPH13"; ("", "") for plain text."""
        parts = self.text.strip().split(maxsplit=1)
        if not parts or not parts[0].startswith("/"):
            return "", ""
        args = parts[1].strip() if len(parts) > 1 else ""
        return parts[0].split("@", 1)[0].lower(), args


def _chat_id(message: Mapping[str, Any]) -> int | None:
    chat = message.get("chat")
    chat_id = chat.get("id") if isinstance(chat, dict) else None
    return chat_id if isinstance(chat_id, int) and not isinstance(chat_id, bool) else None


class TelegramApi:
    """Synchronous Bot API client. Every message is sent as HTML."""

    def __init__(self, token: str, *, timeout: float = 15.0) -> None:
        self._token = token
        self._url = f"{TELEGRAM_API}/bot{token}"
        self._timeout = timeout
        self._session: curl_requests.Session[curl_requests.Response] = curl_requests.Session()

    def close(self) -> None:
        self._session.close()

    def call(self, method: str, params: Mapping[str, Any], *, timeout: float | None = None) -> Any:
        try:
            response = self._session.post(
                f"{self._url}/{method}", json=dict(params), timeout=timeout or self._timeout
            )
        except Exception as exc:  # noqa: BLE001 - every transport failure reads the same
            # The token is part of the URL; never let it reach a log line.
            raise TelegramError(f"{method}: {exc}".replace(self._token, "***")) from None
        try:
            body = json.loads(response.content)
        except ValueError:
            raise TelegramError(f"{method}: HTTP {response.status_code}, not JSON") from None
        if not isinstance(body, dict) or not body.get("ok"):
            description = body.get("description") if isinstance(body, dict) else None
            raise TelegramError(f"{method}: {description or f'HTTP {response.status_code}'}")
        return body.get("result")

    def get_updates(self, offset: int, *, wait: int) -> list[dict[str, Any]]:
        """Long polling: returns as soon as there is an update, or after ``wait`` seconds."""
        result = self.call(
            "getUpdates",
            {"offset": offset, "timeout": wait, "allowed_updates": ["message", "callback_query"]},
            timeout=wait + 10,
        )
        return [item for item in result if isinstance(item, dict)] if isinstance(result, list) else []

    def send_message(
        self, chat_id: int, text: str, *, reply_markup: Mapping[str, Any] | None = None
    ) -> None:
        params: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            params["reply_markup"] = reply_markup
        self.call("sendMessage", params)

    def edit_message_text(self, chat_id: int, message_id: int, text: str) -> None:
        self.call(
            "editMessageText",
            {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML",
             "reply_markup": {"inline_keyboard": []}},
        )

    def answer_callback(self, callback_id: str, text: str | None = None) -> None:
        params: dict[str, Any] = {"callback_query_id": callback_id}
        if text:
            params["text"] = text
        self.call("answerCallbackQuery", params)
