"""Command line entry point for the repricing worker.

    repricer                      # крутится по настройкам из панели
    repricer --once --dry-run     # один проход, ничего не меняя

Everything about the shop -- merchant, proxies, interval, Telegram -- is filled
in on the dashboard and read from the database on every cycle. The only thing
this process needs from the environment is where the database is.
"""

from __future__ import annotations

import signal
import sys
import threading
from pathlib import Path
from types import FrameType

import typer
from loguru import logger
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from repricer.service import WorkerService

app = typer.Typer(add_completion=False, help="Kaspi repricer: следит за конкурентами и ставит цены.")


@app.command()
def run(
    database_url: str = typer.Option(
        ..., envvar="DATABASE_URL", help="postgresql+psycopg://user@host/db"
    ),
    once: bool = typer.Option(False, "--once", help="Один цикл и выход."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Посчитать и показать в логе, но ничего не записывать."
    ),
    concurrency: int = typer.Option(
        4, "--concurrency", min=1, help="Сколько карточек тянуть одновременно."
    ),
    feed_dir: Path = typer.Option(
        Path("feeds"), envvar="FEED_DIR", help="Куда класть копию прайс-листа."
    ),
    feed_base_url: str = typer.Option(
        "", envvar="FEED_BASE_URL", help="Публичный адрес этой папки, если раздаёте файлом."
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Подробные логи."),
) -> None:
    """Запустить репрайсер."""
    _configure_logging(verbose)
    session_factory = sessionmaker(create_engine(database_url, pool_pre_ping=True))

    with WorkerService(
        session_factory=session_factory,
        feed_dir=feed_dir,
        feed_base_url=feed_base_url or None,
        concurrency=concurrency,
        dry_run=dry_run,
    ) as service:
        if once:
            service.run_once()
            return
        stop = threading.Event()
        _install_signal_handlers(stop)
        service.run_forever(stop)


def _install_signal_handlers(stop: threading.Event) -> None:
    def handle(signum: int, _frame: FrameType | None) -> None:
        # The current cycle finishes first, so we never stop mid-upload.
        logger.info("{} получен, останавливаемся после текущего цикла", signal.Signals(signum).name)
        stop.set()

    for received in (signal.SIGINT, signal.SIGTERM):
        signal.signal(received, handle)


def _configure_logging(verbose: bool) -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level="DEBUG" if verbose else "INFO",
        format="<dim>{time:HH:mm:ss}</dim> <level>{level: <8}</level> {message}",
    )


if __name__ == "__main__":
    app()
