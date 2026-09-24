"""The shop's own settings, filled in on the dashboard instead of in .env."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from loguru import logger

from repricer.api.deps import SessionDep
from repricer.api.schemas import SettingsIn, SettingsOut
from repricer.api.security import ApiKeyGuard
from repricer.db.models import ShopSettings
from repricer.db.settings_store import load_settings
from repricer.scraper import ProxyPool, mask_proxy

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[ApiKeyGuard])


@router.get("", summary="Current shop settings")
def get_settings(session: SessionDep) -> SettingsOut:
    return _out(load_settings(session))


@router.put("", summary="Save shop settings")
def update_settings(payload: SettingsIn, session: SessionDep) -> SettingsOut:
    if payload.proxies:
        try:
            ProxyPool(payload.proxies)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    settings = load_settings(session)
    settings.merchant_id = payload.merchant_id
    settings.company = payload.company
    settings.merchant_rating = payload.merchant_rating
    settings.proxies = list(payload.proxies)
    settings.worker_enabled = payload.worker_enabled
    settings.interval_seconds = payload.interval_seconds
    settings.request_interval = payload.request_interval
    settings.telegram_chat_ids = list(payload.telegram_chat_ids)
    session.commit()
    session.refresh(settings)
    logger.info(
        "Settings saved: merchant={} worker_enabled={} proxies={}",
        settings.merchant_id or "—",
        settings.worker_enabled,
        [mask_proxy(proxy) for proxy in settings.proxies],
    )
    return _out(settings)


def _out(settings: ShopSettings) -> SettingsOut:
    token = settings.telegram_bot_token
    return SettingsOut(
        merchant_id=settings.merchant_id,
        company=settings.company,
        merchant_rating=settings.merchant_rating,
        proxies=list(settings.proxies),
        worker_enabled=settings.worker_enabled,
        interval_seconds=settings.interval_seconds,
        request_interval=settings.request_interval,
        # Enough to recognise the token, not enough to use it.
        telegram_token_hint=f"…{token[-4:]}" if len(token) > 4 else "",
        telegram_configured=bool(token),
        telegram_chat_ids=list(settings.telegram_chat_ids),
        is_ready=settings.is_ready,
    )
