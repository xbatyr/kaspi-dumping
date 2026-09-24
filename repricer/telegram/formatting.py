"""Rendering of bot messages. Telegram HTML, so every value is escaped."""

from __future__ import annotations

from decimal import Decimal
from html import escape

from repricer.cities import CITY_NAMES
from repricer.telegram.alerts import Alert, AlertKind

STRATEGY_NAMES = {
    "beat_first": "Стать первым",
    "match_first": "Цена первого места",
    "follow_second": "Прижиматься к первому",
    "target_position": "Борьба за место",
    "fixed_price": "Фиксированная цена",
    "manual": "Вручную",
}


def tenge(amount: Decimal | None) -> str:
    if amount is None:
        return "—"
    return f"{amount:,.0f} ₸".replace(",", " ")


def city(city_id: str) -> str:
    return CITY_NAMES.get(city_id, city_id)


def position(place: int | None) -> str:
    if place is None:
        return "—"
    return f"№{place}" + (" 🥇" if place == 1 else "")


def render_alert(alert: Alert) -> str:
    head = f"<b>{escape(alert.sku)}</b> · {escape(city(alert.city_id))}"
    if alert.kind is AlertKind.STOP_LOSS:
        lines = [
            "🛑 <b>Достигнут стоп-лосс</b>",
            head,
            f"Цена упёрлась в минимум: <b>{tenge(alert.price)}</b>",
        ]
        if alert.competitor_price is not None:
            who = escape(alert.competitor_name or "конкурент")
            lines.append(f"Ниже нас: {who} — {tenge(alert.competitor_price)}")
        lines.append(f"Наше место: {position(alert.position)}")
        lines.append("<i>Опускаться дальше бот не будет.</i>")
        return "\n".join(lines)

    who = escape(alert.competitor_name or "конкурент")
    return "\n".join(
        [
            "⚠️ <b>Потеряно первое место</b>",
            head,
            f"Нас подвинул {who}: <b>{tenge(alert.competitor_price)}</b>",
            f"Наша цена: {tenge(alert.price)} ({position(alert.position)})",
        ]
    )
