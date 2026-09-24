"""Persistent opt-in for price notifications."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from repricer.db.models import TelegramSubscriber


def subscribe(session: Session, merchant_id: str, chat_id: int) -> None:
    if session.get(TelegramSubscriber, (merchant_id, chat_id)) is None:
        session.add(TelegramSubscriber(merchant_id=merchant_id, chat_id=chat_id))
        session.commit()


def unsubscribe(session: Session, merchant_id: str, chat_id: int) -> None:
    subscriber = session.get(TelegramSubscriber, (merchant_id, chat_id))
    if subscriber is not None:
        session.delete(subscriber)
        session.commit()


def subscriber_chat_ids(session: Session, merchant_id: str) -> list[int]:
    return list(session.scalars(
        select(TelegramSubscriber.chat_id)
        .where(TelegramSubscriber.merchant_id == merchant_id)
        .order_by(TelegramSubscriber.chat_id)
    ))
