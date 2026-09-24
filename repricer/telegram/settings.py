"""Bot configuration, read from the environment or .env."""

from __future__ import annotations

from functools import lru_cache

from typing import Any

from pydantic import Field, field_validator

from repricer.settings import CoreSettings


class TelegramSettings(CoreSettings):
    bot_token: str = Field(default="", alias="telegram_bot_token")
    #: Comma separated chat IDs. Kept as text because a bare "123,456" is not
    #: JSON, which is what pydantic-settings expects for a list field.
    allowed_chat_ids_raw: str = Field(default="", alias="allowed_telegram_chat_ids")
    #: Where alerts and the daily summary go; defaults to the first allowed chat.
    alert_chat_id: int | None = Field(default=None, alias="telegram_alert_chat_id")
    #: Local time of the daily summary, HH:MM in Kazakhstan time.
    summary_at: str = Field(default="20:00", alias="telegram_summary_at")

    @field_validator("alert_chat_id", mode="before")
    @classmethod
    def blank_chat_id_means_unset(cls, value: Any) -> Any:
        # A distinct name from the parent's validator: same-named methods would
        # shadow it and quietly disable the rating check.
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def allowed_chat_ids(self) -> frozenset[int]:
        ids: set[int] = set()
        for chunk in self.allowed_chat_ids_raw.replace(";", ",").split(","):
            chunk = chunk.strip()
            if chunk:
                try:
                    ids.add(int(chunk))
                except ValueError:
                    continue
        return frozenset(ids)

    @property
    def target_chat_id(self) -> int | None:
        if self.alert_chat_id is not None:
            return self.alert_chat_id
        return min(self.allowed_chat_ids) if self.allowed_chat_ids else None


@lru_cache
def get_telegram_settings() -> TelegramSettings:
    return TelegramSettings()
