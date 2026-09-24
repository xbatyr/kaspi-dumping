"""FastAPI application: the dashboard's backend and Kaspi's feed URL.

    uvicorn repricer.api.app:app --reload

Swagger UI lives at /docs, ReDoc at /redoc and the OpenAPI document at
/openapi.json.
"""

from __future__ import annotations

from fastapi import FastAPI

from repricer.api.routes import (
    catalog,
    cities,
    feed,
    history,
    products,
    rules,
    settings,
    status,
)

DESCRIPTION = """
Backend of the Kaspi.kz repricer.

* **products** - the catalogue: SKUs, their Kaspi cards, brands and stock.
* **rules** - per-city repricing settings and current prices.
* **history** - what the repricer changed, when, and which competitor it reacted to.
* **feed** - the price list Kaspi polls; point the merchant cabinet at it.

One process serves one merchant, configured through `KASPI_MERCHANT_ID`.
Every `/api/*` call needs the `X-API-Key` header; the feed does not, because
Kaspi fetches it without credentials.
"""

TAGS = [
    {"name": "settings", "description": "Shop details the owner fills in once."},
    {"name": "status", "description": "Is everything set up, and what did the bot do today."},
    {"name": "products", "description": "The catalogue the repricer works on."},
    {"name": "rules", "description": "Read and edit repricing rules."},
    {"name": "history", "description": "Price changes over time."},
    {"name": "feed", "description": "The XML price list for the Kaspi merchant cabinet."},
]


def create_app() -> FastAPI:
    app = FastAPI(
        title="Kaspi Repricer API",
        version="0.1.0",
        description=DESCRIPTION,
        openapi_tags=TAGS,
    )
    app.include_router(settings.router)
    app.include_router(status.router)
    app.include_router(products.router)
    app.include_router(catalog.router)
    app.include_router(rules.router)
    app.include_router(cities.router)
    app.include_router(history.router)
    app.include_router(feed.router)

    @app.get("/health", tags=["feed"], summary="Liveness probe")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
