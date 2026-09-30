"""The Bot API client against a local stand-in for api.telegram.org."""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from repricer.telegram import api as telegram_api
from repricer.telegram.api import TelegramApi, TelegramError

TOKEN = "123456:secret-token"


class FakeBotApi:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.answers: dict[str, tuple[int, bytes]] = {}


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeBotApi]:
    fake = FakeBotApi()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            method = self.path.rsplit("/", 1)[-1]
            fake.requests.append((self.path, json.loads(self.rfile.read(length) or b"{}")))
            status, body = fake.answers.get(method, (200, b'{"ok": true, "result": true}'))
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: Any) -> None:
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(telegram_api, "TELEGRAM_API", f"http://127.0.0.1:{httpd.server_port}")
    yield fake
    httpd.shutdown()


def test_messages_go_out_as_html_with_their_buttons(server: FakeBotApi) -> None:
    api = TelegramApi(TOKEN)
    keyboard = {"inline_keyboard": [[{"text": "Да", "callback_data": "stop:yes"}]]}

    api.send_message(42, "<b>Привет</b>", reply_markup=keyboard)
    api.edit_message_text(42, 7, "Готово")
    api.answer_callback("99", "Ок")

    assert [(path, body) for path, body in server.requests] == [
        (f"/bot{TOKEN}/sendMessage", {
            "chat_id": 42, "text": "<b>Привет</b>", "parse_mode": "HTML",
            "disable_web_page_preview": True, "reply_markup": keyboard,
        }),
        (f"/bot{TOKEN}/editMessageText", {"chat_id": 42, "message_id": 7, "text": "Готово", "parse_mode": "HTML"}),
        (f"/bot{TOKEN}/answerCallbackQuery", {"callback_query_id": "99", "text": "Ок"}),
    ]


def test_updates_come_back_as_plain_dicts(server: FakeBotApi) -> None:
    server.answers["getUpdates"] = (200, json.dumps(
        {"ok": True, "result": [{"update_id": 5, "message": {"chat": {"id": 42}, "text": "/help"}}]}
    ).encode())

    updates = TelegramApi(TOKEN).get_updates(3, wait=0)

    assert updates == [{"update_id": 5, "message": {"chat": {"id": 42}, "text": "/help"}}]
    assert server.requests[0][1] == {"offset": 3, "timeout": 0, "allowed_updates": ["message", "callback_query"]}


def test_a_refusal_carries_telegrams_reason(server: FakeBotApi) -> None:
    server.answers["sendMessage"] = (400, b'{"ok": false, "description": "Bad Request: chat not found"}')

    with pytest.raises(TelegramError, match="chat not found"):
        TelegramApi(TOKEN).send_message(1, "x")


def test_a_transport_failure_never_reveals_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    # Nothing listens on port 9: the connection is refused.
    monkeypatch.setattr(telegram_api, "TELEGRAM_API", "http://127.0.0.1:9")

    with pytest.raises(TelegramError) as failure:
        TelegramApi(TOKEN, timeout=2).send_message(1, "x")

    assert "secret-token" not in str(failure.value)
