"""Settings every process shares: which database, which store."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from repricer.uploader import MerchantIdentity


class CoreSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(
        default="postgresql+psycopg://localhost/repricer",
        description="SQLAlchemy URL; Supabase works with the pooler URL.",
    )
    #: One process serves one store: there is no merchants table yet.
    merchant_id: str = Field(default="", alias="kaspi_merchant_id")
    company: str = Field(default="", alias="kaspi_company")
    #: Our store's rating on Kaspi. The engine prefers the live value from the
    #: product card; this is the fallback for when our offer is not listed there.
    merchant_rating: float | None = Field(default=None, alias="kaspi_merchant_rating")

    @field_validator("merchant_rating", mode="before")
    @classmethod
    def blank_rating_means_unset(cls, value: Any) -> Any:
        # A line like "KASPI_MERCHANT_RATING=" in .env is how people say "no
        # value"; without this it would crash the process on startup.
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def merchant(self) -> MerchantIdentity:
        return MerchantIdentity(self.merchant_id, self.company or self.merchant_id)
