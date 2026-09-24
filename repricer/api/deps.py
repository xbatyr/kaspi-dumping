"""Shared dependencies: the database session and the merchant the process serves.

The session is synchronous, like the rest of the project. FastAPI runs
synchronous endpoints in a thread pool, so a blocking query does not hold up the
event loop.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from repricer.api.settings import ApiSettings, get_settings
from repricer.db.settings_store import settings_or_none
from repricer.uploader import MerchantIdentity


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    settings = get_settings()
    engine = create_engine(settings.database_url, pool_pre_ping=True, pool_size=settings.pool_size)
    return sessionmaker(engine)


def get_session() -> Iterator[Session]:
    with get_session_factory()() as session:
        yield session


def get_merchant(
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[ApiSettings, Depends(get_settings)],
) -> MerchantIdentity:
    """Which shop this request is about.

    The dashboard writes it into the database, so the owner can fill it in
    without touching the environment. The environment still wins on a fresh
    install where nothing has been saved yet.
    """
    shop = settings_or_none(session)
    if shop is not None and shop.merchant_id.strip():
        return MerchantIdentity(shop.merchant_id.strip(), shop.company.strip() or shop.merchant_id.strip())
    if settings.merchant_id.strip():
        return settings.merchant
    raise HTTPException(
        status.HTTP_409_CONFLICT,
        "Магазин не настроен: укажите ID магазина и название компании в настройках",
    )


SessionDep = Annotated[Session, Depends(get_session)]
MerchantDep = Annotated[MerchantIdentity, Depends(get_merchant)]
