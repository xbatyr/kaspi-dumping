# Один образ на три процесса: API, воркер и Telegram-бот. Команду задаёт compose.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Зависимости отдельным слоем: правка кода не тянет переустановку.
COPY pyproject.toml ./
COPY repricer ./repricer
RUN pip install --no-cache-dir .

COPY alembic.ini ./
COPY alembic ./alembic

# Не root: если кто-то пролезет через парсер, прав у него не будет.
RUN useradd --create-home --uid 10001 repricer && mkdir -p /app/feeds && chown -R repricer /app
USER repricer

CMD ["uvicorn", "repricer.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
