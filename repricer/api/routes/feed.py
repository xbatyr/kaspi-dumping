"""The price list Kaspi fetches.

Built from the database on every request rather than served from disk: the feed
then always matches the prices the repricer has actually applied, and there is
no shared filesystem between the API and the worker. Kaspi polls this URL about
once an hour, so the cost is a single query per hour.

The URL is public by necessity, because Kaspi fetches it without credentials.
Whoever finds it sees the catalogue and its prices, so give it an unguessable
path or restrict it to Kaspi's addresses at the reverse proxy.
"""

from __future__ import annotations

from hashlib import sha256

from fastapi import APIRouter, HTTPException, Request, Response, status
from loguru import logger

from repricer.api.deps import MerchantDep, SessionDep
from repricer.uploader import DatabaseCatalog, FeedOffer, build_feed, collect_feed_offers

router = APIRouter(tags=["feed"])


@router.get(
    "/feed/kaspi.xml",
    summary="Current price list in Kaspi's XML format",
    response_class=Response,
    responses={
        200: {"content": {"application/xml": {}}, "description": "The price list"},
        304: {"description": "Unchanged since the ETag the caller sent"},
        503: {"description": "Nothing publishable: serving an empty feed would clear the shop"},
    },
)
def kaspi_feed(request: Request, session: SessionDep, merchant: MerchantDep) -> Response:
    offers, excluded = collect_feed_offers(
        session, merchant.merchant_id, catalog=DatabaseCatalog(session)
    )
    if excluded:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "прайс неполный: проверьте товары, которые не попадают в выгрузку",
        )
    if not offers:
        logger.error(
            "merchant={}: feed requested but nothing is publishable ({} products left out)",
            merchant.merchant_id,
            len(excluded),
        )
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "no publishable offers: an empty feed would take the whole shop off Kaspi",
        )

    # The ETag covers the offers, not the rendered document, whose date attribute
    # changes on every request and would defeat caching.
    etag = _offers_etag(offers)
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})

    feed = build_feed(merchant, offers)
    return Response(
        content=feed,
        media_type="application/xml; charset=utf-8",
        headers={"ETag": etag, "Cache-Control": "no-cache"},
    )


def _offers_etag(offers: list[FeedOffer]) -> str:
    digest = sha256()
    for offer in offers:
        digest.update(repr(offer).encode())
    return f'"{digest.hexdigest()[:32]}"'
