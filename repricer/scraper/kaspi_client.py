"""Kaspi.kz offer scraper: every seller's offer on a product card, for one city.

Uses the JSON endpoint the product page itself calls. Verified against the live
site on 2026-09-18:

* ``POST /yml/offer-view/offers/{product_id}`` with a JSON body; GET is 405.
* ``page`` is 0-based; ``limit`` 64 works, 100 is rejected with 400.
* ``sortOption: "PRICE"`` sorts cheapest first.
* An unknown product ID returns 200 with an empty list, so an empty result
  cannot be told apart from a product that nobody sells.

This module knows nothing about pricing or the database. It returns its own
``Offer`` type; mapping offers to the rule engine's input is the caller's job.
"""

from __future__ import annotations

import json
import math
import random
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import TypeVar
from urllib.parse import quote, urlsplit, urlunsplit

from curl_cffi.requests import BrowserTypeLiteral, Response, Session
from curl_cffi.requests.session import HttpMethod
from curl_cffi.requests.exceptions import (
    ChunkedEncodingError,
    IncompleteRead,
    ProxyError,
    Timeout,
)
from curl_cffi.requests.exceptions import ConnectionError as CurlConnectionError
from loguru import logger

KASPI_BASE_URL = "https://kaspi.kz"
MAX_PAGE_SIZE = 64

# Failures worth another attempt, possibly through another proxy. ConnectionError
# also covers DNS, TLS and connect-timeout errors.
_TRANSIENT_ERRORS = (Timeout, CurlConnectionError, ProxyError, ChunkedEncodingError, IncompleteRead)
_PROXY_SCHEMES = frozenset({"http", "https", "socks5", "socks5h"})

_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class ProductCard:
    """A product card from Kaspi's catalogue search: what the dashboard offers
    when the merchant types a product name instead of hunting for an ID."""

    kaspi_product_id: str
    title: str
    brand: str | None
    price: Decimal | None
    rating: float | None
    reviews_count: int
    link: str


@dataclass(frozen=True, slots=True)
class Offer:
    """One store's offer on a Kaspi product card, for one city."""

    merchant_id: str
    merchant_name: str | None
    price: Decimal
    #: Store rating, 1..5. None for stores that have no reviews yet.
    rating: float | None
    reviews_count: int
    kaspi_delivery: bool
    #: Kaspi's delivery bucket: EXPRESS, TODAY, TOMORROW, TILL_2_DAYS, ...
    delivery_duration: str | None


class KaspiError(Exception):
    """Base class for everything this module raises about talking to Kaspi."""


class KaspiTransportError(KaspiError):
    """Kaspi could not be reached (timeout, connection or proxy failure) within the retry budget."""


class KaspiHTTPError(KaspiError):
    """Kaspi answered with an error status: not retryable, or still failing after all retries."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class KaspiResponseError(KaspiError):
    """Kaspi answered, but not with the offers JSON this module understands."""


# --- Parsing -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OffersPage:
    offers: tuple[Offer, ...]
    #: Offers in the response before validation; drives pagination.
    raw_count: int
    #: Kaspi's count of all offers for the product in this city, if reported.
    total: int | None


def parse_offers_page(payload: object) -> OffersPage:
    """Validate one page of the offers response.

    Unusable offers (no merchant ID or no valid price) are skipped with a
    warning. If a non-empty page yields no usable offer at all, the response
    format has most likely changed, and that raises rather than passing an empty
    list downstream, where it would read as "no competitors".
    """
    if not isinstance(payload, dict):
        raise KaspiResponseError(f"expected a JSON object, got {type(payload).__name__}")
    raw_offers = payload.get("offers")
    if not isinstance(raw_offers, list):
        raise KaspiResponseError("response has no 'offers' list")

    offers: list[Offer] = []
    for index, raw in enumerate(raw_offers):
        try:
            offers.append(parse_offer(raw))
        except ValueError as exc:
            merchant = raw.get("merchantId") if isinstance(raw, dict) else None
            logger.warning("Skipping unusable offer #{} (merchant {!r}): {}", index, merchant, exc)
    if raw_offers and not offers:
        raise KaspiResponseError(
            f"none of the {len(raw_offers)} offers could be parsed; has the response format changed?"
        )

    total = payload.get("total")
    return OffersPage(tuple(offers), len(raw_offers), total if _is_count(total) else None)


def parse_offer(raw: object) -> Offer:
    """Build an Offer from one element of the response's ``offers`` list.

    Raises ValueError when the offer is unusable: no merchant ID or no valid
    price. Optional fields that are missing or malformed become "unknown" (no
    rating, 0 reviews, no Kaspi Delivery) rather than failing the whole offer.
    """
    if not isinstance(raw, dict):
        raise ValueError(f"expected an object, got {type(raw).__name__}")
    rating = _optional(raw, "merchantRating", _parse_rating)
    return Offer(
        merchant_id=_parse_merchant_id(raw.get("merchantId")),
        merchant_name=_optional(raw, "merchantName", _parse_text),
        price=_parse_price(raw.get("price")),
        # Kaspi sends either no rating or 0 for stores without reviews; real
        # ratings start at one star.
        rating=rating or None,
        reviews_count=_optional(raw, "merchantReviewsQuantity", _parse_count) or 0,
        kaspi_delivery=_optional(raw, "kaspiDelivery", _parse_bool) or False,
        delivery_duration=_optional(raw, "deliveryDuration", _parse_text),
    )


def parse_search_results(payload: object, *, limit: int = 12) -> list[ProductCard]:
    """Pick the few fields the dashboard needs out of a large search response."""
    if not isinstance(payload, dict):
        raise KaspiResponseError(f"expected a JSON object, got {type(payload).__name__}")
    data = payload.get("data")
    cards = data if isinstance(data, list) else []
    results: list[ProductCard] = []
    for raw in cards[:limit]:
        if not isinstance(raw, dict):
            continue
        try:
            kaspi_id = _parse_merchant_id(raw.get("id"))
        except ValueError:
            continue
        price = _optional(raw, "unitPrice", _parse_price)
        results.append(
            ProductCard(
                kaspi_product_id=kaspi_id,
                title=_optional(raw, "title", _parse_text) or kaspi_id,
                brand=_optional(raw, "brand", _parse_text),
                price=price,
                rating=_optional(raw, "rating", _parse_rating),
                reviews_count=_optional(raw, "reviewsQuantity", _parse_count) or 0,
                link=_optional(raw, "shopLink", _parse_text) or f"/p/-{kaspi_id}/",
            )
        )
    return results


def _optional(raw: Mapping[str, object], key: str, parse: Callable[[object], _T]) -> _T | None:
    value = raw.get(key)
    if value is None or value == "":
        return None
    try:
        return parse(value)
    except ValueError as exc:
        logger.warning(
            "Ignoring malformed {}={!r} (merchant {!r}): {}", key, value, raw.get("merchantId"), exc
        )
        return None


def _parse_merchant_id(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, str | int):
        raise ValueError(f"merchantId must be a string, got {value!r}")
    merchant_id = str(value).strip()
    if not merchant_id:
        raise ValueError("merchantId is empty")
    return merchant_id


def _parse_price(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise ValueError(f"price must be a number, got {value!r}")
    try:
        price = Decimal(str(value))
    except InvalidOperation:
        raise ValueError(f"price must be a number, got {value!r}") from None
    if not price.is_finite() or price <= 0:
        raise ValueError(f"price must be a positive number, got {value!r}")
    return price


def _parse_rating(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("expected a number")
    rating = float(value)
    if not 0 <= rating <= 5:  # also rejects NaN
        raise ValueError("expected a rating between 0 and 5")
    return rating


def _parse_count(value: object) -> int:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("expected a non-negative integer")
    return value


def _parse_bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise ValueError("expected true or false")
    return value


def _parse_text(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("expected a string")
    return value.strip()


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


# --- Retries and proxies ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    #: Total attempts per page, the first one included.
    max_attempts: int = 5
    base_delay: float = 1.0
    max_delay: float = 30.0
    retry_statuses: frozenset[int] = frozenset({403, 429, 500, 502, 503, 504})

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if not 0 <= self.base_delay <= self.max_delay:
            raise ValueError("delays must satisfy 0 <= base_delay <= max_delay")

    def delay(self, failed_attempts: int, rng: random.Random, retry_after: float | None = None) -> float:
        """Seconds to wait after ``failed_attempts`` consecutive failures.

        Exponential backoff with "equal jitter" (half fixed, half random), so
        workers that were blocked together do not retry in lockstep but never
        retry immediately either. A server's Retry-After is honoured up to
        ``max_delay``.
        """
        ceiling = min(self.max_delay, self.base_delay * 2.0 ** min(failed_attempts - 1, 32))
        delay = ceiling / 2 + rng.uniform(0, ceiling / 2)
        if retry_after is not None:
            delay = max(delay, min(retry_after, self.max_delay))
        return delay


class ProxyPool:
    """Round-robin over proxies, benching each one for ``cooldown`` seconds after it fails.

    Thread-safe, so one pool can be shared by every client in a worker process.
    """

    def __init__(
        self,
        proxies: Iterable[str],
        *,
        cooldown: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._proxies = list(dict.fromkeys(_validate_proxy(proxy) for proxy in proxies))
        if not self._proxies:
            raise ValueError("ProxyPool needs at least one proxy")
        self._cooldown = cooldown
        self._clock = clock
        self._benched_until: dict[str, float] = {}
        self._next = 0
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._proxies)

    def acquire(self) -> str:
        """The next healthy proxy or, if all are benched, the one that recovers first."""
        with self._lock:
            now = self._clock()
            count = len(self._proxies)
            for offset in range(count):
                index = (self._next + offset) % count
                proxy = self._proxies[index]
                if self._benched_until.get(proxy, 0.0) <= now:
                    self._next = (index + 1) % count
                    return proxy
            return min(self._proxies, key=lambda proxy: self._benched_until[proxy])

    def report_failure(self, proxy: str) -> None:
        with self._lock:
            self._benched_until[proxy] = self._clock() + self._cooldown

    def report_success(self, proxy: str) -> None:
        with self._lock:
            self._benched_until.pop(proxy, None)

    def healthy_count(self) -> int:
        """Proxies not currently benched. Zero means every exit IP is blocked or dead."""
        with self._lock:
            now = self._clock()
            return sum(1 for proxy in self._proxies if self._benched_until.get(proxy, 0.0) <= now)


class RateLimiter:
    """Keeps consecutive requests that share an exit IP at least ``min_interval`` apart.

    Keyed by proxy URL (or "direct"), so a pool of proxies is paced per IP rather
    than globally. Thread-safe: the slot is reserved under the lock and only the
    waiting happens outside it, so two threads can never take the same slot.
    """

    def __init__(
        self,
        min_interval: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if min_interval < 0:
            raise ValueError("min_interval must not be negative")
        self._min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._next_allowed: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, key: str) -> float:
        """Block until ``key`` may be used again; returns how long that took."""
        with self._lock:
            now = self._clock()
            start = max(now, self._next_allowed.get(key, now))
            self._next_allowed[key] = start + self._min_interval
        delay = start - now
        if delay > 0:
            self._sleep(delay)
        return delay


def mask_proxy(proxy: str | None) -> str:
    """A proxy URL that is safe to log: the password is replaced with ***."""
    if proxy is None:
        return "direct connection"
    parts = urlsplit(proxy)
    userinfo, has_userinfo, host = parts.netloc.rpartition("@")
    if not has_userinfo or ":" not in userinfo:
        return proxy
    user = userinfo.split(":", 1)[0]
    return urlunsplit(parts._replace(netloc=f"{user}:***@{host}"))


def _validate_proxy(proxy: str) -> str:
    proxy = proxy.strip()
    parts = urlsplit(proxy)
    try:
        port = parts.port
    except ValueError:
        port = None
    if parts.scheme not in _PROXY_SCHEMES or not parts.hostname or port is None:
        raise ValueError(
            f"invalid proxy {mask_proxy(proxy)!r}: expected scheme://[user:password@]host:port "
            f"with scheme one of {', '.join(sorted(_PROXY_SCHEMES))}"
        )
    return proxy


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None  # HTTP-date form; not worth supporting for this API
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


# --- Client ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Failure:
    """A failed attempt that is worth retrying."""

    error: KaspiError
    cause: BaseException | None = None
    retry_after: float | None = None


class KaspiClient:
    """Fetches offers from Kaspi.kz the way Chrome on the product page does.

    Requests carry the TLS/HTTP2 fingerprint of a current Chrome (curl_cffi) and
    go through the next healthy proxy when a pool is configured. 403, 429, 5xx,
    timeouts and connection errors are retried with exponential backoff, each
    retry through the next proxy.

    Use one client per thread; a ProxyPool can be shared between clients.
    """

    def __init__(
        self,
        *,
        proxy_pool: ProxyPool | None = None,
        retry_policy: RetryPolicy | None = None,
        #: Shared between clients to pace every request that leaves through the
        #: same exit IP, retries included.
        rate_limiter: RateLimiter | None = None,
        timeout: float = 15.0,
        page_size: int = MAX_PAGE_SIZE,
        max_pages: int = 5,
        impersonate: BrowserTypeLiteral = "chrome",
        base_url: str = KASPI_BASE_URL,
        session: Session[Response] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        if not 1 <= page_size <= MAX_PAGE_SIZE:
            raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")
        if max_pages < 1:
            raise ValueError("max_pages must be >= 1")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._proxy_pool = proxy_pool
        self._retry = retry_policy or RetryPolicy()
        self._rate_limiter = rate_limiter
        self._timeout = timeout
        self._page_size = page_size
        self._max_pages = max_pages
        self._base_url = base_url.rstrip("/")
        self._owns_session = session is None
        self._session = session if session is not None else Session(impersonate=impersonate)
        self._sleep = sleep
        self._rng = rng or random.Random()

    def __enter__(self) -> KaspiClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_session:
            self._session.close()

    def get_product_offers(self, product_id: str, city_id: str) -> list[Offer]:
        """Every offer on the product card in ``city_id``: one per store, cheapest first.

        Reads up to ``max_pages`` pages; beyond that only the most expensive tail
        is lost. Raises a KaspiError subclass instead of returning a partial list
        when Kaspi cannot be reached or answers with something unusable.
        """
        _check_numeric_id(product_id, "product_id")
        _check_numeric_id(city_id, "city_id")

        cheapest: dict[str, Offer] = {}
        for page in range(self._max_pages):
            result = parse_offers_page(self._fetch_page(product_id, city_id, page))
            for offer in result.offers:
                # Prices can move between page requests; keep each store's lowest.
                seen = cheapest.get(offer.merchant_id)
                if seen is None or offer.price < seen.price:
                    cheapest[offer.merchant_id] = offer
            fetched = page * self._page_size + result.raw_count
            if result.raw_count < self._page_size or (
                result.total is not None and fetched >= result.total
            ):
                break
        else:
            logger.info(
                "product={} city={}: stopped after {} pages; more expensive offers were not read",
                product_id,
                city_id,
                self._max_pages,
            )

        offers = sorted(cheapest.values(), key=lambda offer: offer.price)
        logger.debug("product={} city={}: {} offers", product_id, city_id, len(offers))
        return offers

    def search_products(self, text: str, city_id: str, *, limit: int = 12) -> list[ProductCard]:
        """Catalogue search, the same endpoint the website's search box uses.

        Lets the owner find a product by name and take its card ID from the
        result, instead of copying it out of a browser address bar.
        """
        _check_numeric_id(city_id, "city_id")
        query = text.strip()
        if not query:
            return []
        url = f"{self._base_url}/yml/product-view/pl/results"
        headers: dict[str, str | None] = {
            "Accept": "application/json, text/*",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": f"{self._base_url}/shop/search/?text={quote(query)}",
            "X-KS-City": city_id,
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-User": None,
            "Upgrade-Insecure-Requests": None,
        }
        full_url = f"{url}?text={quote(query)}&page=0&all=false"
        payload = self._get_json(full_url, headers, {"kaspi.city": city_id}, f"search={query!r}")
        return parse_search_results(payload, limit=limit)

    def _fetch_page(self, product_id: str, city_id: str, page: int) -> object:
        url = f"{self._base_url}/yml/offer-view/offers/{product_id}"
        # User-Agent and sec-ch-ua are deliberately not set here: the impersonated
        # Chrome profile sends ones that match its TLS/HTTP2 fingerprint, and a
        # hand-written UA would contradict that fingerprint.
        headers: dict[str, str | None] = {
            "Accept": "application/json, text/*",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Content-Type": "application/json; charset=UTF-8",
            "Origin": self._base_url,
            "Referer": f"{self._base_url}/shop/p/-{product_id}/?c={city_id}",
            "X-KS-City": city_id,
            # The Chrome profile defaults to top-level navigation headers, but this
            # is an XHR from the product page. None removes a default header.
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-User": None,
            "Upgrade-Insecure-Requests": None,
        }
        # The product page's own request body, minus its location-specific
        # zoneId, which does not change the offer list.
        body: dict[str, object] = {
            "cityId": city_id,
            "id": product_id,
            "merchantUID": [],
            "limit": self._page_size,
            "page": page,
            "sortOption": "PRICE",
            "highRating": None,
            "searchText": None,
            "isExcellentMerchant": False,
            "installationId": "-1",
        }
        label = f"product={product_id} city={city_id} page={page}"
        return self._post_json(url, body, headers, {"kaspi.city": city_id}, label)

    def _get_json(
        self,
        url: str,
        headers: dict[str, str | None],
        cookies: dict[str, str],
        label: str,
    ) -> object:
        """A GET with the same retries and proxy rotation as the offers call."""
        return self._post_json(url, None, headers, cookies, label, method="GET")

    def _post_json(
        self,
        url: str,
        body: dict[str, object] | None,
        headers: dict[str, str | None],
        cookies: dict[str, str],
        label: str,
        method: HttpMethod = "POST",
    ) -> object:
        pool = self._proxy_pool
        for attempt in range(1, self._retry.max_attempts + 1):
            proxy = pool.acquire() if pool is not None else None
            outcome = self._attempt(url, body, headers, cookies, proxy, method)
            if not isinstance(outcome, _Failure):
                if pool is not None and proxy is not None:
                    pool.report_success(proxy)
                return outcome

            if pool is not None and proxy is not None:
                pool.report_failure(proxy)
            if attempt == self._retry.max_attempts:
                raise outcome.error from outcome.cause
            delay = self._retry.delay(attempt, self._rng, outcome.retry_after)
            logger.warning(
                "{}: {} via {} (attempt {}/{}), retrying in {:.1f}s",
                label,
                outcome.error,
                mask_proxy(proxy),
                attempt,
                self._retry.max_attempts,
                delay,
            )
            self._sleep(delay)
        raise AssertionError("unreachable: max_attempts >= 1")

    def _attempt(
        self,
        url: str,
        body: dict[str, object] | None,
        headers: dict[str, str | None],
        cookies: dict[str, str],
        proxy: str | None,
        method: HttpMethod = "POST",
    ) -> object:
        """One round trip: the decoded JSON body, or a _Failure worth retrying.

        Raises for failures that another attempt would not fix.
        """
        if self._rate_limiter is not None:
            self._rate_limiter.wait(proxy if proxy is not None else "direct")
        try:
            response = self._session.request(
                method,
                url,
                json=body,
                headers=headers,
                cookies=cookies,
                proxy=proxy,
                timeout=self._timeout,
            )
        except _TRANSIENT_ERRORS as exc:
            return _Failure(KaspiTransportError(f"{type(exc).__name__}: {exc}"), cause=exc)

        status = response.status_code
        if status in self._retry.retry_statuses:
            retry_after = _parse_retry_after(response.headers.get("Retry-After"))
            return _Failure(KaspiHTTPError(status), retry_after=retry_after)
        if status != 200:
            raise KaspiHTTPError(status)
        try:
            return json.loads(response.content)
        except ValueError as exc:
            # Anti-bot interstitials arrive as 200 text/html; another proxy may get through.
            content_type = response.headers.get("Content-Type") or "no content type"
            return _Failure(KaspiResponseError(f"expected JSON, got {content_type}"), cause=exc)


def _check_numeric_id(value: str, name: str) -> None:
    # Kaspi product and city IDs are numeric. Checking also keeps arbitrary text
    # out of the URL path.
    if not isinstance(value, str) or not (value.isascii() and value.isdigit()):
        raise ValueError(f"{name} must be a string of digits, got {value!r}")
