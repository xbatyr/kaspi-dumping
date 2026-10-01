"""Alerts the worker raises while repricing.

Two things are worth waking the owner for:

* the stop-loss is reached -- a competitor went under our floor and the bot has
  nowhere left to go;
* first place was lost -- somebody undercut us, and the owner wants to know who
  and by how much.

Both conditions persist for as long as the competitor keeps its price, so they
are reported on the *transition* into the condition, not on every cycle. Without
that, a single dumper would send an alert every few minutes all night.

The sink is a plain synchronous HTTP call to the Bot API, like everything the
bot sends: the worker is synchronous too.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from collections.abc import Callable
from typing import Protocol
from typing import Any

from curl_cffi import requests as curl_requests
from loguru import logger
from sqlalchemy.orm import Session

from repricer.telegram.subscriptions import subscriber_chat_ids
from repricer.db.settings_store import settings_or_none

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
    def send(self, text: str) -> bool | None: ...


@dataclass(frozen=True, slots=True)
class ProposalNotice:
    id: int
    message: str
    delivered_to: frozenset[str]


def proposal_packets(notices: list[ProposalNotice]) -> list[list[ProposalNotice]]:
    """Five individually reviewable products, safely below Telegram's limit."""
    packets: list[list[ProposalNotice]] = []
    current: list[ProposalNotice] = []
    length = 300
    for notice in notices:
        size = len(_proposal_body(notice)) + 20
        if current and (len(current) == 5 or length + size > 3500):
            packets.append(current)
            current, length = [], 300
        current.append(notice)
        length += size
    if current:
        packets.append(current)
    return packets


def _proposal_body(notice: ProposalNotice) -> str:
    # Existing proposals keep their stored legacy text for audit and approval.
    return notice.message.split("\n", 1)[-1].split("\n\n", 1)[0]


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
        self._token = token
        self._url = f"{TELEGRAM_API}/bot{token}/sendMessage"
        self._chat_id = chat_id
        self._timeout = timeout

    def send(self, text: str, *, reply_markup: dict[str, Any] | None = None) -> bool:
        payload: dict[str, Any] = {
            "chat_id": self._chat_id, "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        try:
            response = curl_requests.post(
                self._url,
                json=payload,
                timeout=self._timeout,
            )
        except Exception as exc:  # noqa: BLE001 - a failed alert must not stop repricing
            logger.warning("Telegram alert not delivered: {}", str(exc).replace(self._token, "***"))
            return False
        try:
            body = json.loads(response.content or b"{}")
        except ValueError:
            logger.warning("Telegram returned non-JSON HTTP {}", response.status_code)
            return False
        if response.status_code != 200 or not isinstance(body, dict) or body.get("ok") is not True:
            description = body.get("description", response.status_code) if isinstance(body, dict) else response.status_code
            logger.warning("Telegram refused the alert: {}", str(description).replace(self._token, "***"))
            return False
        return True


class BroadcastTelegramSink:
    """Send the same committed price update to every /start subscriber."""

    def __init__(
        self, token: str, merchant_id: str, session_factory: Callable[[], Session]
    ) -> None:
        self._token = token
        self._merchant_id = merchant_id
        self._session_factory = session_factory
        self._last_proposal_sent: dict[int, float] = {}

    def send(self, text: str) -> None:
        try:
            with self._session_factory() as session:
                chat_ids = subscriber_chat_ids(session, self._merchant_id)
        except Exception as exc:  # noqa: BLE001 - notification failure must not fail a price cycle
            logger.warning("Could not load Telegram subscribers: {}", exc)
            return
        for chat_id in chat_ids:
            TelegramSink(self._token, chat_id).send(text)

    def send_proposal(self, text: str, proposal_id: int, delivered: set[str]) -> list[str]:
        with self._session_factory() as session:
            shop = settings_or_none(session)
            owners = {int(c) for c in (shop.telegram_chat_ids if shop else []) if c.lstrip("-").isdigit()}
            recipients = set(subscriber_chat_ids(session, self._merchant_id)) | owners
        keyboard = {"inline_keyboard": [[
            {"text": "✅ Подтвердить", "callback_data": f"price:{proposal_id}:yes"},
            {"text": "❌ Отклонить", "callback_data": f"price:{proposal_id}:no"},
        ]]}
        sent: list[str] = []
        for chat_id in sorted(recipients):
            if str(chat_id) in delivered:
                continue
            pause = 1.05 - (time.monotonic() - self._last_proposal_sent.get(chat_id, 0))
            if pause > 0:
                time.sleep(pause)
            if TelegramSink(self._token, chat_id).send(text, reply_markup=keyboard):
                sent.append(str(chat_id))
                self._last_proposal_sent[chat_id] = time.monotonic()
        return sent

    def send_proposals(self, notices: list[ProposalNotice], *,
                       on_delivered: Callable[[dict[int, list[str]]], None] | None = None) -> dict[int, list[str]]:
        with self._session_factory() as session:
            shop = settings_or_none(session)
            owners = {int(c) for c in (shop.telegram_chat_ids if shop else []) if c.lstrip("-").isdigit()}
            recipients = set(subscriber_chat_ids(session, self._merchant_id)) | owners
        receipts: dict[int, list[str]] = {}
        for chat_id in sorted(recipients):
            pending = [n for n in notices if str(chat_id) not in n.delivered_to]
            for packet in proposal_packets(pending):
                text = "🧪 <b>Тестовый режим. Предлагаю цены:</b>\n\n"
                keyboard: dict[str, Any] = {"inline_keyboard": []}
                for number, notice in enumerate(packet, 1):
                    text += f"<b>{number}.</b> {_proposal_body(notice)}\n\n"
                    keyboard["inline_keyboard"].append([
                        {"text": f"✅ {number}. Да", "callback_data": f"prices:{notice.id}:yes"},
                        {"text": f"❌ {number}. Нет", "callback_data": f"prices:{notice.id}:no"},
                    ])
                text += "Выберите «Да» или «Нет» для каждого товара.\nБез подтверждения XML не изменится.\nСрок: 1 час. Перед записью проверим Kaspi."
                pause = 1.05 - (time.monotonic() - self._last_proposal_sent.get(chat_id, 0))
                if pause > 0:
                    time.sleep(pause)
                if TelegramSink(self._token, chat_id).send(text, reply_markup=keyboard):
                    packet_receipts = {notice.id: [str(chat_id)] for notice in packet}
                    if on_delivered is not None:
                        on_delivered(packet_receipts)
                    for notice in packet:
                        receipts.setdefault(notice.id, []).append(str(chat_id))
                self._last_proposal_sent[chat_id] = time.monotonic()
        return receipts


class LoggingSink:
    """Fallback when no bot token is configured: the alert still shows up."""

    def send(self, text: str) -> None:
        logger.warning("ALERT (no Telegram configured): {}", text.replace("\n", " | "))
