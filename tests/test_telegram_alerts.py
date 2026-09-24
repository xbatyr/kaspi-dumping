from decimal import Decimal
from typing import Any

import pytest

from repricer.telegram.alerts import (
    Alert,
    AlertKind,
    AlertThrottle,
    LoggingSink,
    TelegramSink,
)
from repricer.telegram.formatting import render_alert

ALMATY = "750000000"


def alert(kind: AlertKind = AlertKind.STOP_LOSS, sku: str = "IPH13-128", **overrides: Any) -> Alert:
    params: dict[str, Any] = {
        "kind": kind,
        "sku": sku,
        "city_id": ALMATY,
        "price": Decimal(330000),
        "competitor_name": "Kazphone",
        "competitor_price": Decimal(325000),
        "position": 4,
    }
    params.update(overrides)
    return Alert(**params)


# --- Throttling ---------------------------------------------------------------


def test_a_new_condition_is_reported() -> None:
    throttle = AlertThrottle()

    assert throttle.fresh([alert()], resolved=set()) == [alert()]


def test_the_same_condition_stays_quiet_next_cycle() -> None:
    throttle = AlertThrottle()
    throttle.fresh([alert()], resolved=set())

    # The dumper is still there, but the owner has already been told.
    assert throttle.fresh([alert()], resolved=set()) == []


def test_a_condition_that_cleared_can_fire_again() -> None:
    throttle = AlertThrottle()
    throttle.fresh([alert()], resolved=set())

    throttle.fresh([], resolved={alert().key})
    assert throttle.fresh([alert()], resolved=set()) == [alert()]


def test_the_two_kinds_are_tracked_apart() -> None:
    throttle = AlertThrottle()
    throttle.fresh([alert(AlertKind.STOP_LOSS)], resolved=set())

    lost = alert(AlertKind.LOST_FIRST_PLACE)
    assert throttle.fresh([lost], resolved=set()) == [lost]


def test_each_city_is_tracked_apart() -> None:
    throttle = AlertThrottle()
    throttle.fresh([alert()], resolved=set())

    astana = alert(city_id="710000000")
    assert throttle.fresh([astana], resolved=set()) == [astana]


# --- Wording ------------------------------------------------------------------


def test_stop_loss_message_names_the_floor_and_the_competitor() -> None:
    text = render_alert(alert())

    assert "Достигнут стоп-лосс" in text
    assert "IPH13-128" in text
    assert "Алматы" in text
    assert "330 000 ₸" in text
    assert "Kazphone" in text
    assert "325 000 ₸" in text


def test_lost_first_place_message_names_who_took_it() -> None:
    text = render_alert(
        alert(AlertKind.LOST_FIRST_PLACE, price=Decimal(370112), competitor_price=Decimal(369999), position=2)
    )

    assert "Потеряно первое место" in text
    assert "Kazphone" in text
    assert "369 999 ₸" in text
    assert "№2" in text


def test_shop_names_cannot_break_the_markup() -> None:
    text = render_alert(alert(competitor_name="<b>Мага</b> & сыновья"))

    assert "&lt;b&gt;Мага&lt;/b&gt; &amp; сыновья" in text


def test_a_nameless_competitor_is_still_described() -> None:
    text = render_alert(
        alert(AlertKind.LOST_FIRST_PLACE, competitor_name=None, competitor_price=Decimal(369999))
    )

    assert "Нас подвинул конкурент" in text
    assert "369 999 ₸" in text


def test_a_stop_loss_without_competitor_data_omits_that_line() -> None:
    text = render_alert(alert(competitor_name=None, competitor_price=None))

    assert "Достигнут стоп-лосс" in text
    assert "330 000 ₸" in text
    assert "Ниже нас" not in text


# --- Sending ------------------------------------------------------------------


class FakePost:
    def __init__(self, status_code: int = 200, content: bytes = b"{}") -> None:
        self.status_code = status_code
        self.content = content
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url: str, **kwargs: Any) -> "FakePost":
        self.calls.append({"url": url, **kwargs})
        return self


def test_sink_posts_to_the_bot_api(monkeypatch: pytest.MonkeyPatch) -> None:
    post = FakePost()
    monkeypatch.setattr("repricer.telegram.alerts.curl_requests.post", post)

    TelegramSink("123:TOKEN", 42).send("привет")

    (call,) = post.calls
    assert call["url"] == "https://api.telegram.org/bot123:TOKEN/sendMessage"
    assert call["json"] == {
        "chat_id": 42,
        "text": "привет",
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }


def test_a_network_failure_does_not_stop_repricing(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("telegram unreachable")

    monkeypatch.setattr("repricer.telegram.alerts.curl_requests.post", explode)

    TelegramSink("123:TOKEN", 42).send("привет")  # must not raise


def test_a_refusal_from_telegram_is_only_logged(
    monkeypatch: pytest.MonkeyPatch, warnings_logged: list[str]
) -> None:
    monkeypatch.setattr(
        "repricer.telegram.alerts.curl_requests.post",
        FakePost(status_code=403, content=b'{"description": "bot was blocked by the user"}'),
    )

    TelegramSink("123:TOKEN", 42).send("привет")

    assert any("bot was blocked" in message for message in warnings_logged)


def test_the_logging_sink_keeps_the_alert_visible(warnings_logged: list[str]) -> None:
    LoggingSink().send("🛑 стоп-лосс\nIPH13-128")

    assert any("IPH13-128" in message for message in warnings_logged)
