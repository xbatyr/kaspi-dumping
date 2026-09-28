import json
import random
import re
import threading
import time
from collections.abc import Iterator
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast

import pytest
from curl_cffi.requests import Response, Session
from curl_cffi.requests.exceptions import ConnectionError as CurlConnectionError
from curl_cffi.requests.exceptions import InvalidURL, ProxyError, Timeout

from repricer.scraper import (
    KaspiClient,
    KaspiHTTPError,
    KaspiResponseError,
    KaspiTransportError,
    ProxyPool,
    RateLimiter,
    RetryPolicy,
    mask_proxy,
)

FIXTURE = Path(__file__).parent / "fixtures" / "kaspi_offers_page.json"
PRODUCT, ALMATY, ASTANA = "102298404", "750000000", "710000000"
P1, P2, P3 = "http://10.0.0.1:8080", "http://10.0.0.2:8080", "http://10.0.0.3:8080"


class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        payload: object = None,
        *,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.content = content if content is not None else json.dumps(payload).encode()
        self.headers = headers or {}


class FakeSession:
    """Stands in for curl_cffi's Session: replays scripted outcomes, records every call."""

    def __init__(self, *outcomes: FakeResponse | Exception) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        return self.request("POST", url, **kwargs)

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def close(self) -> None:
        pass


def ok(*offers: tuple[str, int], total: int | None = None) -> FakeResponse:
    raw = [{"merchantId": merchant, "price": float(price)} for merchant, price in offers]
    return FakeResponse(200, {"offers": raw, "total": total})


def status(code: int, **headers: str) -> FakeResponse:
    return FakeResponse(code, {"error": code}, headers={k.replace("_", "-"): v for k, v in headers.items()})


def make_client(session: FakeSession, **kwargs: Any) -> tuple[KaspiClient, list[float]]:
    sleeps: list[float] = []
    client = KaspiClient(
        session=cast("Session[Response]", session), sleep=sleeps.append, rng=random.Random(0), **kwargs
    )
    return client, sleeps


# --- Request shape ------------------------------------------------------------


def test_request_carries_city_headers_cookie_and_body() -> None:
    session = FakeSession(ok(("a", 1000)))
    client, _ = make_client(session)

    client.get_product_offers(PRODUCT, ASTANA)

    (call,) = session.calls
    assert call["url"] == f"https://kaspi.kz/yml/offer-view/offers/{PRODUCT}"
    assert call["cookies"] == {"kaspi.city": ASTANA}
    assert call["headers"]["X-KS-City"] == ASTANA
    assert call["headers"]["Referer"] == f"https://kaspi.kz/shop/p/-{PRODUCT}/?c={ASTANA}"
    assert call["headers"]["Accept"] == "application/json, text/*"
    assert "User-Agent" not in call["headers"]  # comes from the Chrome profile
    assert call["json"] == {
        "cityId": ASTANA,
        "id": PRODUCT,
        "merchantUID": [],
        "limit": 64,
        "page": 0,
        "sortOption": "PRICE",
        "highRating": None,
        "searchText": None,
        "isExcellentMerchant": False,
        "installationId": "-1",
    }
    assert call["proxy"] is None
    assert call["timeout"] == 15.0


@pytest.mark.parametrize("bad_id", ["", "abc", "../1", "12 3", "١٢٣", "-5"])
def test_non_numeric_ids_are_rejected_before_any_request(bad_id: str) -> None:
    session = FakeSession()
    client, _ = make_client(session)

    with pytest.raises(ValueError):
        client.get_product_offers(bad_id, ALMATY)
    with pytest.raises(ValueError):
        client.get_product_offers(PRODUCT, bad_id)
    assert session.calls == []


@pytest.mark.parametrize(
    "kwargs", [{"page_size": 0}, {"page_size": 65}, {"max_pages": 0}, {"timeout": 0}]
)
def test_invalid_client_settings_are_rejected(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        make_client(FakeSession(), **kwargs)


# --- Pagination ---------------------------------------------------------------


def test_reads_pages_until_total_is_reached() -> None:
    session = FakeSession(
        ok(("a", 100), ("b", 200), ("c", 300), total=5),
        ok(("d", 400), ("e", 500), total=5),
    )
    client, _ = make_client(session, page_size=3)

    offers = client.get_product_offers(PRODUCT, ALMATY)

    assert [call["json"]["page"] for call in session.calls] == [0, 1]
    assert [offer.merchant_id for offer in offers] == ["a", "b", "c", "d", "e"]


def test_short_page_ends_pagination_when_total_is_missing() -> None:
    session = FakeSession(ok(("a", 100), ("b", 200)), ok(("c", 300)))
    client, _ = make_client(session, page_size=2)

    assert len(client.get_product_offers(PRODUCT, ALMATY)) == 3
    assert len(session.calls) == 2


def test_full_page_matching_total_needs_no_extra_request() -> None:
    session = FakeSession(ok(("a", 100), ("b", 200), total=2))
    client, _ = make_client(session, page_size=2)

    client.get_product_offers(PRODUCT, ALMATY)

    assert len(session.calls) == 1


def test_max_pages_caps_the_number_of_requests() -> None:
    session = FakeSession(ok(("a", 1), ("b", 2), total=10), ok(("c", 3), ("d", 4), total=10))
    client, _ = make_client(session, page_size=2, max_pages=2)

    assert len(client.get_product_offers(PRODUCT, ALMATY)) == 4
    assert len(session.calls) == 2


def test_store_seen_on_two_pages_keeps_its_cheapest_offer() -> None:
    # A price moved between the two page requests, so "b" shows up twice.
    session = FakeSession(ok(("a", 100), ("b", 300), total=4), ok(("b", 250), ("c", 400), total=4))
    client, _ = make_client(session, page_size=2)

    offers = client.get_product_offers(PRODUCT, ALMATY)

    assert [(offer.merchant_id, offer.price) for offer in offers] == [
        ("a", Decimal(100)),
        ("b", Decimal(250)),
        ("c", Decimal(400)),
    ]


def test_empty_offer_list_is_returned_as_is() -> None:
    client, _ = make_client(FakeSession(ok(total=0)))

    assert client.get_product_offers(PRODUCT, ALMATY) == []


# --- Retries ------------------------------------------------------------------


@pytest.mark.parametrize("code", [403, 429, 500, 502, 503, 504])
def test_retryable_status_is_retried(code: int) -> None:
    session = FakeSession(status(code), ok(("a", 1000)))
    client, sleeps = make_client(session)

    offers = client.get_product_offers(PRODUCT, ALMATY)

    assert [offer.merchant_id for offer in offers] == ["a"]
    assert len(session.calls) == 2
    assert len(sleeps) == 1 and 0.5 <= sleeps[0] <= 1.0


@pytest.mark.parametrize(
    "error",
    [Timeout("Operation timed out"), CurlConnectionError("Connection refused"), ProxyError("Proxy CONNECT aborted")],
    ids=["timeout", "connection", "proxy"],
)
def test_transport_error_is_retried(error: Exception) -> None:
    session = FakeSession(error, ok(("a", 1000)))
    client, sleeps = make_client(session)

    assert len(client.get_product_offers(PRODUCT, ALMATY)) == 1
    assert len(sleeps) == 1


def test_backoff_doubles_between_attempts() -> None:
    session = FakeSession(status(503), status(503), status(503), ok(("a", 1000)))
    client, sleeps = make_client(session)

    client.get_product_offers(PRODUCT, ALMATY)

    assert len(sleeps) == 3
    for failed_attempts, slept in enumerate(sleeps, start=1):
        ceiling = 2.0 ** (failed_attempts - 1)
        assert ceiling / 2 <= slept <= ceiling


def test_retry_after_header_is_honoured() -> None:
    session = FakeSession(status(429, Retry_After="7"), ok(("a", 1000)))
    client, sleeps = make_client(session)

    client.get_product_offers(PRODUCT, ALMATY)

    assert sleeps == [7.0]


def test_retry_after_is_capped_at_max_delay() -> None:
    session = FakeSession(status(429, Retry_After="600"), ok(("a", 1000)))
    client, sleeps = make_client(session, retry_policy=RetryPolicy(max_delay=30))

    client.get_product_offers(PRODUCT, ALMATY)

    assert sleeps == [30.0]


def test_gives_up_with_the_last_status_after_max_attempts() -> None:
    session = FakeSession(status(429), status(429), status(403))
    client, sleeps = make_client(session, retry_policy=RetryPolicy(max_attempts=3))

    with pytest.raises(KaspiHTTPError) as excinfo:
        client.get_product_offers(PRODUCT, ALMATY)

    assert excinfo.value.status_code == 403
    assert len(session.calls) == 3
    assert len(sleeps) == 2


def test_persistent_timeout_raises_transport_error_with_cause() -> None:
    session = FakeSession(*(Timeout("Operation timed out") for _ in range(3)))
    client, _ = make_client(session, retry_policy=RetryPolicy(max_attempts=3))

    with pytest.raises(KaspiTransportError) as excinfo:
        client.get_product_offers(PRODUCT, ALMATY)

    assert isinstance(excinfo.value.__cause__, Timeout)


@pytest.mark.parametrize("code", [400, 401, 404])
def test_other_error_status_fails_fast(code: int) -> None:
    session = FakeSession(status(code))
    client, sleeps = make_client(session)

    with pytest.raises(KaspiHTTPError) as excinfo:
        client.get_product_offers(PRODUCT, ALMATY)

    assert excinfo.value.status_code == code
    assert (len(session.calls), sleeps) == (1, [])


def test_html_instead_of_json_is_retried_as_a_possible_bot_check() -> None:
    challenge = FakeResponse(200, content=b"<html>verify you are human</html>", headers={"Content-Type": "text/html"})
    session = FakeSession(challenge, ok(("a", 1000)))
    client, _ = make_client(session)

    assert len(client.get_product_offers(PRODUCT, ALMATY)) == 1


def test_persistent_html_raises_response_error() -> None:
    challenge = FakeResponse(200, content=b"<html></html>", headers={"Content-Type": "text/html"})
    session = FakeSession(challenge, challenge)
    client, _ = make_client(session, retry_policy=RetryPolicy(max_attempts=2))

    with pytest.raises(KaspiResponseError, match="text/html"):
        client.get_product_offers(PRODUCT, ALMATY)


def test_programming_errors_are_not_retried() -> None:
    session = FakeSession(InvalidURL("bad url"))
    client, sleeps = make_client(session)

    with pytest.raises(InvalidURL):
        client.get_product_offers(PRODUCT, ALMATY)
    assert sleeps == []


def test_failure_on_a_later_page_raises_instead_of_returning_a_partial_list() -> None:
    session = FakeSession(ok(("a", 100), ("b", 200), total=4), status(404))
    client, _ = make_client(session, page_size=2)

    with pytest.raises(KaspiHTTPError):
        client.get_product_offers(PRODUCT, ALMATY)


# --- Proxy rotation -----------------------------------------------------------


def test_blocked_proxy_is_swapped_and_benched() -> None:
    session = FakeSession(status(403), ok(("a", 1000)), ok(("a", 1000)))
    client, _ = make_client(session, proxy_pool=ProxyPool([P1, P2]))

    client.get_product_offers(PRODUCT, ALMATY)
    client.get_product_offers(PRODUCT, ALMATY)

    # P1 was blocked, so the retry and the next request both avoid it.
    assert [call["proxy"] for call in session.calls] == [P1, P2, P2]


def test_retry_log_never_contains_proxy_password(warnings_logged: list[str]) -> None:
    proxy = "http://user:s3cret@10.0.0.9:8080"
    session = FakeSession(Timeout("Operation timed out"), ok(("a", 1000)))
    client, _ = make_client(session, proxy_pool=ProxyPool([proxy]))

    client.get_product_offers(PRODUCT, ALMATY)

    assert len(warnings_logged) == 1
    assert "user:***@10.0.0.9:8080" in warnings_logged[0]
    assert "s3cret" not in warnings_logged[0]


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_pool_rotates_round_robin() -> None:
    pool = ProxyPool([P1, P2, P3])

    assert [pool.acquire() for _ in range(4)] == [P1, P2, P3, P1]


def test_pool_skips_benched_proxy_until_cooldown_ends() -> None:
    clock = FakeClock()
    pool = ProxyPool([P1, P2], cooldown=10, clock=clock)
    pool.report_failure(P1)

    assert [pool.acquire() for _ in range(3)] == [P2, P2, P2]
    clock.now = 10
    assert pool.acquire() == P1


def test_pool_with_every_proxy_benched_returns_the_first_to_recover() -> None:
    clock = FakeClock()
    pool = ProxyPool([P1, P2], cooldown=10, clock=clock)
    pool.report_failure(P2)
    clock.now = 1
    pool.report_failure(P1)

    assert pool.acquire() == P2


def test_pool_success_lifts_the_bench() -> None:
    pool = ProxyPool([P1, P2], cooldown=10, clock=FakeClock())
    pool.report_failure(P1)
    pool.report_success(P1)

    assert [pool.acquire() for _ in range(2)] == [P1, P2]


def test_pool_drops_duplicates() -> None:
    assert len(ProxyPool([P1, P1, f" {P1} ", P2])) == 2


@pytest.mark.parametrize(
    "proxy",
    ["", "10.0.0.1:8080", "ftp://10.0.0.1:21", "http://10.0.0.1", "http://:8080", "http://host:notaport"],
)
def test_pool_rejects_malformed_proxy(proxy: str) -> None:
    with pytest.raises(ValueError):
        ProxyPool([proxy])


def test_pool_error_does_not_leak_password() -> None:
    with pytest.raises(ValueError) as excinfo:
        ProxyPool(["ftp://user:s3cret@10.0.0.1:21"])

    assert "s3cret" not in str(excinfo.value)


def test_pool_needs_a_proxy() -> None:
    with pytest.raises(ValueError):
        ProxyPool([])


@pytest.mark.parametrize(
    ("proxy", "masked"),
    [
        ("http://user:pass@10.0.0.1:8080", "http://user:***@10.0.0.1:8080"),
        ("https://user:p@ss@proxy.kz:443", "https://user:***@proxy.kz:443"),
        ("http://10.0.0.1:8080", "http://10.0.0.1:8080"),
        ("socks5://onlyuser@10.0.0.1:1080", "socks5://onlyuser@10.0.0.1:1080"),
        (None, "direct connection"),
    ],
)
def test_mask_proxy(proxy: str | None, masked: str) -> None:
    assert mask_proxy(proxy) == masked


# --- Rate limiting ------------------------------------------------------------


class FakeSleeper:
    """A clock that only moves when something sleeps on it."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def test_first_request_through_an_ip_is_not_delayed() -> None:
    timing = FakeSleeper()
    limiter = RateLimiter(2.0, clock=timing.clock, sleep=timing.sleep)

    assert limiter.wait(P1) == 0
    assert timing.slept == []


def test_requests_through_one_ip_are_spaced_out() -> None:
    timing = FakeSleeper()
    limiter = RateLimiter(2.0, clock=timing.clock, sleep=timing.sleep)

    limiter.wait(P1)
    assert limiter.wait(P1) == 2.0
    assert limiter.wait(P1) == 2.0
    assert timing.slept == [2.0, 2.0]


def test_each_ip_is_paced_on_its_own() -> None:
    timing = FakeSleeper()
    limiter = RateLimiter(2.0, clock=timing.clock, sleep=timing.sleep)

    limiter.wait(P1)
    assert limiter.wait(P2) == 0
    assert limiter.wait("direct") == 0


def test_waiting_long_enough_needs_no_delay() -> None:
    timing = FakeSleeper()
    limiter = RateLimiter(2.0, clock=timing.clock, sleep=timing.sleep)

    limiter.wait(P1)
    timing.now += 5
    assert limiter.wait(P1) == 0


def test_rate_limiter_rejects_a_negative_interval() -> None:
    with pytest.raises(ValueError):
        RateLimiter(-1)


def test_client_paces_requests_per_proxy() -> None:
    timing = FakeSleeper()
    limiter = RateLimiter(3.0, clock=timing.clock, sleep=timing.sleep)
    session = FakeSession(ok(("a", 1000)), status(429), ok(("a", 1000)))
    client, _ = make_client(session, proxy_pool=ProxyPool([P1]), rate_limiter=limiter)

    client.get_product_offers(PRODUCT, ALMATY)
    client.get_product_offers(PRODUCT, ALMATY)

    # The retry counts too: it is another request through the same IP.
    assert timing.slept == [3.0, 3.0]


def test_client_without_proxies_paces_the_direct_connection() -> None:
    timing = FakeSleeper()
    limiter = RateLimiter(3.0, clock=timing.clock, sleep=timing.sleep)
    session = FakeSession(ok(("a", 1000)), ok(("a", 1000)))
    client, _ = make_client(session, rate_limiter=limiter)

    client.get_product_offers(PRODUCT, ALMATY)
    client.get_product_offers(PRODUCT, ALMATY)

    assert timing.slept == [3.0]


def test_pool_reports_how_many_proxies_are_healthy() -> None:
    clock = FakeClock()
    pool = ProxyPool([P1, P2], cooldown=10, clock=clock)

    assert pool.healthy_count() == 2
    pool.report_failure(P1)
    assert pool.healthy_count() == 1
    pool.report_failure(P2)
    assert pool.healthy_count() == 0
    clock.now = 10
    assert pool.healthy_count() == 2


# --- Retry policy -------------------------------------------------------------


def test_backoff_is_bounded_by_max_delay() -> None:
    policy = RetryPolicy(base_delay=1, max_delay=8)
    rng = random.Random(1)

    for failed_attempts in range(1, 40):
        ceiling = min(8.0, 2.0 ** (failed_attempts - 1))
        assert ceiling / 2 <= policy.delay(failed_attempts, rng) <= ceiling


@pytest.mark.parametrize(
    "kwargs", [{"max_attempts": 0}, {"base_delay": -1}, {"base_delay": 10, "max_delay": 5}]
)
def test_invalid_retry_policy_is_rejected(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        RetryPolicy(**kwargs)


# --- On the wire: real curl_cffi against a local server ------------------------


class KaspiStub:
    """A local HTTP server standing in for kaspi.kz; records what actually arrives."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.delay = 0.0
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                stub.requests.append(
                    {
                        "path": self.path,
                        "headers": {name.lower(): value for name, value in self.headers.items()},
                        "body": json.loads(self.rfile.read(length)),
                    }
                )
                time.sleep(stub.delay)
                payload = FIXTURE.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args: Any) -> None:
                pass

        class QuietServer(ThreadingHTTPServer):
            def handle_error(self, request: Any, client_address: Any) -> None:
                pass  # the client hanging up during the timeout test is expected

        self.server = QuietServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def kaspi_stub() -> Iterator[KaspiStub]:
    stub = KaspiStub()
    yield stub
    stub.close()


def test_what_actually_goes_over_the_wire(kaspi_stub: KaspiStub) -> None:
    with KaspiClient(base_url=kaspi_stub.url) as client:
        offers = client.get_product_offers(PRODUCT, ALMATY)

    assert len(offers) == 4
    (request,) = kaspi_stub.requests
    headers = request["headers"]
    assert request["path"] == f"/yml/offer-view/offers/{PRODUCT}"
    assert headers["x-ks-city"] == ALMATY
    assert headers["referer"] == f"{kaspi_stub.url}/shop/p/-{PRODUCT}/?c={ALMATY}"
    assert headers["origin"] == kaspi_stub.url
    assert headers["accept"] == "application/json, text/*"
    assert headers["content-type"] == "application/json; charset=UTF-8"
    assert f"kaspi.city={ALMATY}" in headers["cookie"]
    # XHR, not a page navigation.
    assert (headers["sec-fetch-mode"], headers["sec-fetch-dest"]) == ("cors", "empty")
    assert "sec-fetch-user" not in headers
    assert "upgrade-insecure-requests" not in headers
    # The User-Agent comes from the impersonated Chrome and agrees with sec-ch-ua.
    ua_version = re.search(r"Chrome/(\d+)\.", headers["user-agent"])
    assert ua_version is not None
    assert f'"Google Chrome";v="{ua_version.group(1)}"' in headers["sec-ch-ua"]
    assert request["body"]["cityId"] == ALMATY
    assert request["body"]["id"] == PRODUCT
    assert (request["body"]["page"], request["body"]["limit"]) == (0, 64)


def test_real_curl_timeout_is_retried_then_reported(kaspi_stub: KaspiStub) -> None:
    kaspi_stub.delay = 1.0
    policy = RetryPolicy(max_attempts=2, base_delay=0, max_delay=0)

    with KaspiClient(base_url=kaspi_stub.url, timeout=0.2, retry_policy=policy) as client:
        with pytest.raises(KaspiTransportError) as excinfo:
            client.get_product_offers(PRODUCT, ALMATY)

    assert isinstance(excinfo.value.__cause__, Timeout)
    assert len(kaspi_stub.requests) == 2


# --- Catalogue search ---------------------------------------------------------


SEARCH_RESPONSE = {
    "data": [
        {
            "id": "102298404",
            "title": "Apple iPhone 13 128Gb NanoSIM+eSIM черный",
            "brand": "Apple",
            "unitPrice": 354975,
            "rating": 4.9,
            "reviewsQuantity": 8348,
            "shopLink": "/p/apple-iphone-13-128gb-102298404/?c=750000000",
            "previewImages": [{"small": "https://resources.cdn-kaspi.kz/img/m/p/example.jpg?format=preview-small"}],
        },
        {"id": "112233445", "title": "Чехол", "unitPrice": 4900.0},
        {"nonsense": True},
    ]
}


def test_search_returns_cards_with_their_ids() -> None:
    session = FakeSession(FakeResponse(200, SEARCH_RESPONSE))
    client, _ = make_client(session)

    cards = client.search_products("iphone 13", ALMATY)

    assert [card.kaspi_product_id for card in cards] == ["102298404", "112233445"]
    first = cards[0]
    assert first.title.startswith("Apple iPhone 13")
    assert (first.brand, first.price, first.rating) == ("Apple", Decimal(354975), 4.9)
    assert first.link.endswith("102298404/?c=750000000")
    assert first.image_url == "https://resources.cdn-kaspi.kz/img/m/p/example.jpg?format=preview-small"
    assert cards[1].image_url is None


def test_search_uses_a_get_with_the_query_in_the_url() -> None:
    session = FakeSession(FakeResponse(200, SEARCH_RESPONSE))
    client, _ = make_client(session)

    client.search_products("iphone 13", ALMATY)

    (call,) = session.calls
    assert "text=iphone%2013" in call["url"]
    assert call["json"] is None  # a GET carries no body
    assert call["headers"]["X-KS-City"] == ALMATY


def test_an_unusable_card_is_skipped_not_fatal() -> None:
    session = FakeSession(FakeResponse(200, {"data": [{"id": ""}, {"id": "1", "title": "Ок"}]}))
    client, _ = make_client(session)

    assert [card.kaspi_product_id for card in client.search_products("x", ALMATY)] == ["1"]


def test_an_empty_query_never_reaches_kaspi() -> None:
    session = FakeSession()
    client, _ = make_client(session)

    assert client.search_products("   ", ALMATY) == []
    assert session.calls == []


def test_search_failures_are_retried_like_everything_else() -> None:
    session = FakeSession(status(429), FakeResponse(200, SEARCH_RESPONSE))
    client, sleeps = make_client(session)

    assert len(client.search_products("iphone", ALMATY)) == 2
    assert len(sleeps) == 1
