"""Shared fixtures.

The models use PostgreSQL-only types (ARRAY), so DB tests need a real server.
They are skipped unless TEST_DATABASE_URL points at a disposable database:

    TEST_DATABASE_URL=postgresql+psycopg://localhost/repricer_test pytest

The schema is created once per session and dropped afterwards; each test runs in
a transaction that is rolled back.
"""

import os
from collections.abc import Iterator

import pytest
from loguru import logger
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from repricer.db import Base


@pytest.fixture
def warnings_logged() -> Iterator[list[str]]:
    """Messages of loguru records at WARNING and above emitted during the test."""
    messages: list[str] = []
    handler_id = logger.add(lambda message: messages.append(message.record["message"]), level="WARNING")
    yield messages
    try:
        logger.remove(handler_id)
    except ValueError:
        pass  # a CLI run inside the test reconfigured logging and dropped every handler


@pytest.fixture(scope="session")
def db_engine() -> Iterator[Engine]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_engine(url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def session(db_engine: Engine) -> Iterator[Session]:
    with db_engine.connect() as connection:
        transaction = connection.begin()
        with Session(bind=connection, join_transaction_mode="create_savepoint") as db_session:
            yield db_session
        transaction.rollback()
