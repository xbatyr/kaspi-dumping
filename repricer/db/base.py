from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, MetaData, Numeric
from sqlalchemy.orm import DeclarativeBase

# Deterministic constraint names, so Alembic autogenerate yields stable migrations.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map: dict[Any, Any] = {
        Decimal: Numeric(12, 2),  # tenge; up to 9 999 999 999.99
        datetime: DateTime(timezone=True),
    }
