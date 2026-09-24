"""Kaspi city IDs for the city picker.

Every ID below was checked against Kaspi's own offer endpoint on 2026-09-21: a
valid city returns offers, an invalid one returns an empty list. Two candidates
did not pass and are deliberately absent, Turkestan (512210000) and Rudny
(393210000); add them once you can confirm their IDs in the merchant cabinet.

The IDs follow Kazakhstan's KATO codes. The list lives in the core package
because the API, the Telegram bot and the dashboard all need the same names.
"""

from __future__ import annotations

from typing import Final

KASPI_CITIES: Final[tuple[tuple[str, str], ...]] = (
    ("750000000", "Алматы"),
    ("710000000", "Астана"),
    ("511010000", "Шымкент"),
    ("151010000", "Актобе"),
    ("351010000", "Караганда"),
    ("231010000", "Атырау"),
    ("311010000", "Тараз"),
    ("551010000", "Павлодар"),
    ("631010000", "Усть-Каменогорск"),
    ("391010000", "Костанай"),
    ("431010000", "Кызылорда"),
    ("271010000", "Уральск"),
    ("591010000", "Петропавловск"),
    ("471010000", "Актау"),
    ("191010000", "Талдыкорган"),
    ("111010000", "Кокшетау"),
    ("632810000", "Семей"),
    ("352410000", "Темиртау"),
    ("552210000", "Экибастуз"),
)

CITY_NAMES: Final[dict[str, str]] = dict(KASPI_CITIES)

#: Where to look when nothing else says otherwise.
DEFAULT_CITY_ID: Final[str] = "750000000"
