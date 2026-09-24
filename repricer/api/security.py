"""Who may call the API.

Everything under /api/* needs the key from REPRICER_API_KEY, passed as
``X-API-Key``. The feed and the health probe stay open: Kaspi fetches the feed
without credentials, and a probe with a secret is not a probe.

With no key configured the API refuses to answer at all. Failing closed is the
only safe default here: an open endpoint means a stranger can move every price
in the shop.
"""

from __future__ import annotations

from secrets import compare_digest
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from repricer.api.settings import ApiSettings, get_settings

API_KEY_HEADER = "X-API-Key"


def require_api_key(
    settings: Annotated[ApiSettings, Depends(get_settings)],
    x_api_key: Annotated[str | None, Header(alias=API_KEY_HEADER)] = None,
) -> None:
    if not settings.api_key:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "REPRICER_API_KEY is not set: the API stays closed until it is",
        )
    if x_api_key is None or not compare_digest(x_api_key, settings.api_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing API key")


#: Attach to a router to close it.
ApiKeyGuard = Depends(require_api_key)
