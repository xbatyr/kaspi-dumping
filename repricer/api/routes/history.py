"""Price history for one product."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from repricer.api.deps import MerchantDep, SessionDep
from repricer.api.security import ApiKeyGuard
from repricer.api.schemas import HistoryEntryOut, HistoryOut
from repricer.db.models import PriceHistory, Product

router = APIRouter(prefix="/api/history", tags=["history"], dependencies=[ApiKeyGuard])


@router.get(
    "/{sku}",
    summary="Price changes of one product, newest first",
    responses={404: {"description": "Unknown SKU"}},
)
def get_history(
    sku: str,
    session: SessionDep,
    merchant: MerchantDep,
    city_id: Annotated[str | None, Query()] = None,
    since: Annotated[datetime | None, Query(description="Only changes after this moment.")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> HistoryOut:
    product_id = session.scalar(
        select(Product.id).where(Product.merchant_id == merchant.merchant_id, Product.sku == sku)
    )
    if product_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no product with sku {sku}")

    filters = [PriceHistory.product_id == product_id]
    if city_id is not None:
        filters.append(PriceHistory.city_id == city_id)
    if since is not None:
        filters.append(PriceHistory.created_at >= since)

    total = session.scalar(select(func.count()).select_from(PriceHistory).where(*filters)) or 0
    rows = session.scalars(
        select(PriceHistory)
        .where(*filters)
        # created_at comes from now(), so rows written in one transaction share it;
        # the id breaks those ties in insertion order.
        .order_by(PriceHistory.created_at.desc(), PriceHistory.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()

    items = [
        HistoryEntryOut(
            id=row.id,
            product_sku=sku,
            city_id=row.city_id,
            old_price=row.old_price,
            new_price=row.new_price,
            competitor_top1_price=row.competitor_top1_price,
            competitor_top1_merchant_id=row.competitor_top1_merchant_id,
            strategy_used=row.strategy_used,
            reason=row.reason,
            expected_position=row.expected_position,
            competitor_count=row.competitor_count,
            created_at=row.created_at,
        )
        for row in rows
    ]
    return HistoryOut(sku=sku, items=items, total=total, limit=limit, offset=offset)
