"""The cities a rule can be written for."""

from __future__ import annotations

from fastapi import APIRouter

from repricer.cities import KASPI_CITIES
from repricer.api.security import ApiKeyGuard
from repricer.api.schemas import CityOut

router = APIRouter(prefix="/api/cities", tags=["rules"], dependencies=[ApiKeyGuard])


@router.get("", summary="Cities the repricer knows about")
def list_cities() -> list[CityOut]:
    """Options for the city picker: Kaspi's cityId plus a human name."""
    return [CityOut(id=city_id, name=name) for city_id, name in KASPI_CITIES]
