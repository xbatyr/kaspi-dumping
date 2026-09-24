"""Alerts the worker raises while repricing.

Two things are worth waking the owner for:

* the stop-loss is reached -- a competitor went under our floor and the bot has
  nowhere left to go;
* first place was lost -- somebody undercut us, and the owner wants to know who
  and by how much.

Both conditions persist for as long as the competitor keeps its price, so they
are reported on the *transition* into the condition, not on every cycle. Without
that, a single dumper would send an alert every few minutes all night.

The sink is a plain synchronous HTTP call to the Bot API: the worker is
synchronous and has no event loop to hand to aiogram.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from collections.abc import Callable
from typing import Protocol

from curl_cffi import requests as curl_requests
from loguru import logger
from sqlalchemy.orm import Session

from repricer.telegram.subscriptions import subscriber_chat_ids

TELEGRAM_API = "https://api.telegram.org"


class AlertKind(StrEnum):
    STOP_LOSS = "stop_loss"
    LOST_FIRST_PLACE = "lost_first_place"


@dataclass(frozen=True, slots=True)
class Alert:
    kind: AlertKind
    sku: str
    city_id: str
    price: Decimal
    #: Who is in first place now, when we know: the cheapest competitor.
    competitor_name: str | None = None
    competitor_price: Decimal | None = None
    position: int | None = None
    min_price: Decimal | None = None

    @property
    def key(self) -> tuple[AlertKind, str, str]:
        """Identifies the condition, so a repeat of the same one stays quiet."""
        return self.kind, self.sku, self.city_id


class AlertSink(Protocol):
    def send(self, text: str) -> None: ...


class AlertThrottle:
    """Remembers which conditions are already reported.

    In memory only: after a restart the current conditions are announced once
    more, which is the safer way round for a tool that guards prices.
    """

    def __init__(self) -> None:
        self._active: set[tuple[AlertKind, str, str]] = set()

    def fresh(self, alerts: list[Alert], resolved: set[tuple[AlertKind, str, str]]) -> list[Alert]:
        """Alerts whose condition has just appeared; clears the ones that ended."""
        self._active -= resolved
        new = [alert for alert in alerts if alert.key not in self._active]
        self._active |= {alert.key for alert in new}
        return new


class TelegramSink:
    """Posts to the Bot API over plain HTTP, no event loop involved."""

    def __init__(self, token: str, chat_id: int, *, timeout: float = 10.0) -> None:
        self._url = f"{TELEGRAM_API}/bot{token}/sendMessage"
        self._chat_id = chat_id
        self._timeout = timeout

    def send(self, text: str) -> None:
        try:
            response = curl_requests.post(
                self._url,
                json={
                    "chat_id": self._chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=self._timeout,
            )
        except Exception as exc:  # noqa: BLE001 - a failed alert must not stop repricing
            logger.warning("Telegram alert not delivered: {}", exc)
            return
        if response.status_code != 200:
            body = json.loads(response.content or b"{}").get("description", response.status_code)
            logger.warning("Telegram refused the alert: {}", body)


class BroadcastTelegramSink:
    """Send the same committed price update to every /start subscriber."""

    def __init__(
        self, token: str, merchant_id: str, session_factory: Callable[[], Session]
    ) -> None:
        self._token = token
        self._merchant_id = merchant_id
        self._session_factory = session_factory

    def send(self, text: str) -> None:
        try:
            with self._session_factory() as session:
                chat_ids = subscriber_chat_ids(session, self._merchant_id)
        except Exception as exc:  # noqa: BLE001 - notification failure must not fail a price cycle
            logger.warning("Could not load Telegram subscribers: {}", exc)
            return
        for chat_id in chat_ids:
            TelegramSink(self._token, chat_id).send(text)


class LoggingSink:
    """Fallback when no bot token is configured: the alert still shows up."""

    def send(self, text: str) -> None:
        logger.warning("ALERT (no Telegram configured): {}", text.replace("\n", " | "))
