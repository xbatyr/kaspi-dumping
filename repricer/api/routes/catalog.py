"""Finding products on Kaspi without leaving the dashboard.

Asking the owner for the numeric ID of every card is the slowest part of setting
this up. Here they type a product name, Kaspi's own search answers, and the card
ID comes along for the ride.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from loguru import logger

from repricer.api.schemas import ProductCardOut
from repricer.api.security import ApiKeyGuard
from repricer.cities import DEFAULT_CITY_ID
from repricer.db.settings_store import settings_or_none
from repricer.api.deps import SessionDep
from repricer.scraper import KaspiClient, KaspiError, ProxyPool, RateLimiter

router = APIRouter(prefix="/api/kaspi", tags=["products"], dependencies=[ApiKeyGuard])


@router.get(
    "/search",
    summary="Find a product card on Kaspi",
    responses={502: {"description": "Kaspi did not answer"}},
)
def search_kaspi(
    session: SessionDep,
    text: Annotated[str, Query(min_length=2, max_length=200, description="Название товара.")],
    city_id: Annotated[str, Query(pattern=r"^\d{1,16}$")] = DEFAULT_CITY_ID,
    limit: Annotated[int, Query(ge=1, le=24)] = 12,
) -> list[ProductCardOut]:
    shop = settings_or_none(session)
    # Search goes through the same proxies as the scraper, so one search does not
    # burn the IP the repricer depends on.
    pool = ProxyPool(shop.proxies) if shop and shop.proxies else None
    with KaspiClient(proxy_pool=pool, rate_limiter=RateLimiter(0.5)) as client:
        try:
            found = client.search_products(text, city_id, limit=limit)
        except KaspiError as exc:
            logger.warning("Kaspi search for {!r} failed: {}", text, exc)
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, f"Kaspi не ответил на поиск: {exc}"
            ) from exc

    return [
        ProductCardOut(
            kaspi_product_id=card.kaspi_product_id,
            title=card.title,
            brand=card.brand,
            price=card.price,
            rating=card.rating,
            reviews_count=card.reviews_count,
            link=card.link if card.link.startswith("http") else f"https://kaspi.kz/shop{card.link}",
        )
        for card in found
    ]
