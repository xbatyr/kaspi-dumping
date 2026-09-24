"""What /status and the daily summary show.

Plain synchronous queries: the handlers run them in a worker thread so the bot's
event loop stays free.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from html import escape

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from repricer.db.models import PriceHistory, Product, RepricerRule
from repricer.db.queries import latest_changes
from repricer.db.settings_store import settings_or_none
from repricer.pricing import PricingStrategy
from repricer.telegram.formatting import STRATEGY_NAMES, city, position, tenge


@dataclass(frozen=True, slots=True)
class StatusLine:
    sku: str
    title: str
    city_id: str
    strategy: str
    current_price: Decimal | None
    place: int | None
    is_active: bool


@dataclass(frozen=True, slots=True)
class StatusReport:
    products: int
    active_rules: int
    paused_rules: int
    first_place: int
    lines: list[StatusLine]
    shown_of: int
    worker_enabled: bool = False


@dataclass(frozen=True, slots=True)
class DailySummary:
    since: datetime
    changes: int
    products_changed: int
    stop_loss_hits: int
    first_place: int
    tracked_rules: int


def status_report(session: Session, merchant_id: str, *, limit: int = 10) -> StatusReport:
    rows = session.execute(
        select(RepricerRule, Product)
        .join(Product, RepricerRule.product_id == Product.id)
        .where(Product.merchant_id == merchant_id, Product.is_active)
        .order_by(Product.sku, RepricerRule.city_id)
    ).all()
    changes = latest_changes(session, [product.id for _rule, product in rows])

    lines: list[StatusLine] = []
    active = paused = first = 0
    for rule, product in rows:
        if rule.strategy is PricingStrategy.MANUAL:
            continue
        change = changes.get((rule.product_id, rule.city_id))
        place = change.expected_position if change else None
        if rule.is_active:
            active += 1
            if place == 1:
                first += 1
        else:
            paused += 1
        lines.append(
            StatusLine(
                sku=product.sku,
                title=product.title,
                city_id=rule.city_id,
                strategy=rule.strategy.value,
                current_price=rule.current_price,
                place=place,
                is_active=rule.is_active,
            )
        )

    # Worst places first: those are the ones worth looking at.
    lines.sort(key=lambda line: (-(line.place or 0), line.sku))
    return StatusReport(
        products=len({product.sku for _rule, product in rows}),
        active_rules=active,
        paused_rules=paused,
        first_place=first,
        lines=lines[:limit],
        shown_of=len(lines),
        worker_enabled=bool((shop := settings_or_none(session)) and shop.worker_enabled),
    )


def daily_summary(session: Session, merchant_id: str, since: datetime) -> DailySummary:
    history = (
        select(PriceHistory)
        .join(Product, PriceHistory.product_id == Product.id)
        .where(Product.merchant_id == merchant_id, PriceHistory.created_at >= since)
        .subquery()
    )
    changes = session.scalar(select(func.count()).select_from(history)) or 0
    products_changed = (
        session.scalar(select(func.count(func.distinct(history.c.product_id)))) or 0
    )
    stop_loss_hits = (
        session.scalar(
            select(func.count()).select_from(history).where(history.c.reason == "pinned_to_min")
        )
        or 0
    )

    rules = session.execute(
        select(RepricerRule, Product)
        .join(Product, RepricerRule.product_id == Product.id)
        .where(Product.merchant_id == merchant_id, Product.is_active, RepricerRule.is_active,
               RepricerRule.strategy != PricingStrategy.MANUAL)
    ).all()
    latest = latest_changes(session, [product.id for _rule, product in rules])
    first_place = sum(
        1
        for rule, _product in rules
        if (change := latest.get((rule.product_id, rule.city_id))) is not None
        and change.expected_position == 1
    )
    return DailySummary(
        since=since,
        changes=changes,
        products_changed=products_changed,
        stop_loss_hits=stop_loss_hits,
        first_place=first_place,
        tracked_rules=len(rules),
    )


def render_status(report: StatusReport) -> str:
    if report.shown_of == 0:
        if report.products:
            return f"В каталоге {report.products} товаров. Демпинг ещё не настроен: задайте общую стратегию и Min/Max для товаров."
        return "Товаров пока нет: добавьте их в базу, и они появятся здесь."
    head = [
        "📊 <b>Статус репрайсера</b>",
        "Бот включён" if report.worker_enabled else "Бот выключен в настройках",
        f"Товаров: <b>{report.products}</b> · правил активно: <b>{report.active_rules}</b>"
        + (f" · на паузе: {report.paused_rules}" if report.paused_rules else ""),
        f"На первом месте: <b>{report.first_place}</b> из {report.active_rules}",
        "",
    ]
    body = [
        f"{'▶️' if line.is_active else '⏸'} <b>{escape(line.sku)}</b> · {escape(city(line.city_id))}\n"
        f"    {tenge(line.current_price)} · {position(line.place)} · "
        f"{escape(STRATEGY_NAMES.get(line.strategy, line.strategy))}"
        for line in report.lines
    ]
    tail = (
        [f"\n<i>Показаны {len(report.lines)} из {report.shown_of}.</i>"]
        if report.shown_of > len(report.lines)
        else []
    )
    return "\n".join(head + body + tail)


def render_summary(summary: DailySummary) -> str:
    lines = [
        "🗒 <b>Сводка за день</b>",
        f"Цены менялись: <b>{summary.changes}</b> раз "
        f"(товаров: {summary.products_changed})",
        f"На первом месте сейчас: <b>{summary.first_place}</b> из {summary.tracked_rules}",
    ]
    if summary.stop_loss_hits:
        lines.append(f"Упирались в стоп-лосс: <b>{summary.stop_loss_hits}</b> раз")
    if summary.changes == 0:
        lines.append("<i>Ни одна цена не изменилась: рынок стоял или бот на паузе.</i>")
    return "\n".join(lines)
