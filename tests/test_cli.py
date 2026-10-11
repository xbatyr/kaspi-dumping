from collections.abc import Iterator
import os
from pathlib import Path

import pytest
from loguru import logger
from sqlalchemy import Engine
from typer.testing import CliRunner

from repricer.cli import app
from repricer.cli import main

runner = CliRunner()


@pytest.mark.parametrize("existing", [None, "postgresql://server/production"])
def test_entry_point_reads_dotenv_before_resolving_cli_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, existing: str | None
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    if existing is not None:
        monkeypatch.setenv("DATABASE_URL", existing)
    (tmp_path / ".env").write_text("DATABASE_URL=postgresql://localhost/local\n")
    resolved: list[str | None] = []
    monkeypatch.setattr("repricer.cli.app", lambda: resolved.append(os.getenv("DATABASE_URL")))

    main()

    assert resolved == [existing or "postgresql://localhost/local"]


@pytest.fixture(autouse=True)
def drop_cli_log_handlers() -> Iterator[None]:
    yield
    # The CLI logs to stderr, which pytest closes after the test; leaving that
    # sink attached would break logging in every later test.
    logger.remove()


def test_help_lists_the_flags() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for flag in ("--dry-run", "--once", "--concurrency", "--feed-dir"):
        assert flag in result.output


def test_the_database_is_the_only_thing_it_insists_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    result = runner.invoke(app, ["--once"])

    assert result.exit_code == 2
    assert "database" in result.output.lower()


def test_one_cycle_against_an_unconfigured_shop_just_idles(
    db_engine: Engine, tmp_path: pytest.TempPathFactory
) -> None:
    url = db_engine.url.render_as_string(hide_password=False)

    result = runner.invoke(app, ["--database-url", url, "--once", "--dry-run"])

    # Nothing is set up yet, so the run must say so and exit cleanly rather than
    # crash or start scraping.
    assert result.exit_code == 0, result.output
