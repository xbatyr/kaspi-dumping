#!/usr/bin/env python3
"""Manual check: fetch and print every offer for one Kaspi product in one city.

Needs the project installed (``pip install -e .``). Examples:

    python scripts/check_product.py 102298404
    python scripts/check_product.py 102298404 --city 710000000 --own-merchant 30123456
    python scripts/check_product.py 102298404 --proxy http://user:pass@1.2.3.4:8080 -v
"""

from __future__ import annotations

import argparse
import sys
import time
from decimal import Decimal

from loguru import logger

from repricer.scraper import KaspiClient, KaspiError, Offer, ProxyPool, mask_proxy

CITY_NAMES = {"750000000": "Almaty", "710000000": "Astana"}
ROW = "{:>3}  {:<34}  {:>12}  {:>6}  {:>7}  {:^6}  {}"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _configure_logging(args.verbose)

    try:
        pool = ProxyPool(args.proxy) if args.proxy else None
    except ValueError as exc:
        logger.error("{}", exc)
        return 2

    city = f"{args.city} ({CITY_NAMES[args.city]})" if args.city in CITY_NAMES else args.city
    route = ", ".join(mask_proxy(proxy) for proxy in args.proxy) if args.proxy else "direct connection"
    logger.opt(colors=True).info(
        "Product <cyan>{}</cyan>, city <cyan>{}</cyan>, via {}", args.product_id, city, route
    )

    started = time.perf_counter()
    try:
        with KaspiClient(proxy_pool=pool, max_pages=args.max_pages) as client:
            offers = client.get_product_offers(args.product_id, args.city)
    except ValueError as exc:
        logger.error("{}", exc)
        return 2
    except KaspiError as exc:
        logger.error("Kaspi request failed: {}: {}", type(exc).__name__, exc)
        return 1
    elapsed = time.perf_counter() - started

    if not offers:
        logger.warning(
            "No offers. Kaspi answers the same way for a product nobody sells and for an "
            "unknown product ID, so double-check the ID ({:.2f}s)",
            elapsed,
        )
        return 0

    logger.success("{} offers in {:.2f}s", len(offers), elapsed)
    _log_table(offers, args.own_merchant)
    _log_summary(offers, args.own_merchant)
    return 0


def _log_table(offers: list[Offer], own_merchant: str | None) -> None:
    log = logger.opt(colors=True)
    log.info("<bold>" + ROW + "</bold>", "#", "Merchant", "Price", "Rating", "Reviews", "Kaspi", "Delivery")
    for position, offer in enumerate(offers, start=1):
        if offer.merchant_id == own_merchant:
            color = "yellow"
        elif position == 1:
            color = "green"
        else:
            color = "white"
        log.info(
            f"<{color}>{ROW}</{color}>",
            position,
            _merchant_label(offer, 34),
            _tenge(offer.price),
            f"{offer.rating:.1f}" if offer.rating is not None else "—",
            offer.reviews_count,
            "✓" if offer.kaspi_delivery else "·",
            offer.delivery_duration or "—",
        )


def _log_summary(offers: list[Offer], own_merchant: str | None) -> None:
    cheapest, dearest = offers[0].price, offers[-1].price
    logger.info("Price range {} – {} (spread {})", _tenge(cheapest), _tenge(dearest), _tenge(dearest - cheapest))
    if own_merchant is None:
        return
    position = next((i for i, offer in enumerate(offers, start=1) if offer.merchant_id == own_merchant), None)
    if position is None:
        logger.warning(
            "Merchant {} has no offer here: check the ID and that the offer is active in this city",
            own_merchant,
        )
    else:
        gap = offers[position - 1].price - cheapest
        logger.opt(colors=True).info(
            "Your offer: position <yellow>{}</yellow> of {}, {} above the cheapest",
            position,
            len(offers),
            _tenge(gap),
        )


def _tenge(amount: Decimal) -> str:
    return f"{amount:,.0f} ₸".replace(",", " ")


def _merchant_label(offer: Offer, width: int) -> str:
    # Shorten the name, never the ID: the ID is what goes into ignored_merchants.
    merchant_id = f" [{offer.merchant_id}]"
    name = offer.merchant_name or "?"
    room = max(width - len(merchant_id), 1)
    if len(name) > room:
        name = name[: room - 1] + "…"
    return name + merchant_id


def _configure_logging(verbose: bool) -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level="DEBUG" if verbose else "INFO",
        format="<dim>{time:HH:mm:ss}</dim> <level>{level: <8}</level> {message}",
    )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Print every offer for one Kaspi product.")
    parser.add_argument("product_id", help="numeric ID from kaspi.kz/shop/p/<slug>-<ID>/")
    parser.add_argument("--city", default="750000000", help="Kaspi cityId (default: 750000000, Almaty)")
    parser.add_argument(
        "--proxy",
        action="append",
        default=[],
        metavar="URL",
        help="scheme://[user:password@]host:port; repeat to rotate through several",
    )
    parser.add_argument("--own-merchant", metavar="ID", help="highlight this merchant and report its position")
    parser.add_argument("--max-pages", type=int, default=5, help="pages of 64 offers to read (default: 5)")
    parser.add_argument("-v", "--verbose", action="store_true", help="show debug logs")
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
