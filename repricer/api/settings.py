"""Configuration for the API process, from the environment or a .env file."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from repricer.settings import CoreSettings


class ApiSettings(CoreSettings):
    #: Statement pool size; Supabase's transaction pooler prefers small pools.
    pool_size: int = 5
    #: Every /api/* call must present this in X-API-Key. Without it the API
    #: refuses to serve: an open dashboard means anyone can move your prices.
    api_key: str = Field(default="", alias="repricer_api_key")


@lru_cache
def get_settings() -> ApiSettings:
    return ApiSettings()
