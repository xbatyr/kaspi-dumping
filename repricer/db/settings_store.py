"""Reading and creating the shop's settings row."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from repricer.db.models import ShopSettings

SETTINGS_ID = 1


def load_settings(session: Session) -> ShopSettings:
    """The settings row, created empty on first use.

    Every process asks for this, so a fresh install must not require anyone to
    insert a row by hand.
    """
    settings = session.get(ShopSettings, SETTINGS_ID)
    if settings is None:
        settings = ShopSettings()
        session.add(settings)
        session.flush()
    return settings


def settings_or_none(session: Session) -> ShopSettings | None:
    """The row if it exists, without creating it: for read-only paths."""
    return session.scalar(select(ShopSettings).where(ShopSettings.id == SETTINGS_ID))
