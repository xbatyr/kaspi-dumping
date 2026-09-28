"""Fill product thumbnails from Kaspi's public catalogue search.

Search by the exact numeric card ID, then keep only the result whose ID matches.
No customer credentials are needed and the feed is never changed by this job.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import typer
from loguru import logger
from sqlalchemy import create_engine, or_, select
from sqlalchemy.orm import Session

from repricer.cities import DEFAULT_CITY_ID
from repricer.db.models import Product
from repricer.db.settings_store import settings_or_none
from repricer.scraper import KaspiClient, KaspiError, ProxyPool, RateLimiter, RetryPolicy

app = typer.Typer(add_completion=False)


def refresh_missing_images(session: Session, *, limit: int = 10,
                           client: KaspiClient | None = None) -> tuple[int, int]:
    """Check at most `limit` linked cards; failed/empty results cool down for a day."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=1)
    products = session.scalars(
        select(Product)
        .where(Product.kaspi_product_id != "", Product.image_url.is_(None),
               or_(Product.image_checked_at.is_(None), Product.image_checked_at < cutoff))
        .order_by(Product.image_checked_at.asc().nulls_first(), Product.id)
        .limit(limit)
    ).all()
    if not products:
        return 0, 0
    owns_client = client is None
    if client is None:
        shop = settings_or_none(session)
        pool = ProxyPool(shop.proxies) if shop and shop.proxies else None
        client = KaspiClient(proxy_pool=pool, rate_limiter=RateLimiter(1.0), timeout=8,
                             retry_policy=RetryPolicy(max_attempts=2, base_delay=0.5,
                                                      max_delay=2.0))
    found = 0
    try:
        for product in products:
            try:
                cards = client.search_products(product.kaspi_product_id, DEFAULT_CITY_ID, limit=5)
                match = next((card for card in cards if card.kaspi_product_id == product.kaspi_product_id), None)
                if match and match.image_url:
                    product.image_url = match.image_url
                    found += 1
            except KaspiError as exc:
                logger.warning("image sku={}: Kaspi unavailable: {}", product.sku, exc)
            product.image_checked_at = datetime.now(timezone.utc)
            session.commit()
    finally:
        if owns_client:
            client.close()
    return len(products), found


@app.command()
def backfill(limit: int = typer.Option(500, min=1, max=10_000)) -> None:
    """One time batch for existing products; future imports are handled by the worker."""
    database_url = os.environ["DATABASE_URL"]
    engine = create_engine(database_url, pool_pre_ping=True)
    with Session(engine) as session:
        checked, found = refresh_missing_images(session, limit=limit)
    logger.info("Checked {} Kaspi cards; saved {} images", checked, found)


if __name__ == "__main__":
    app()
