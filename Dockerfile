# Один образ на три процесса: API, воркер и Telegram-бот. Команду задаёт compose.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Зависимости отдельным слоем: правка кода не тянет переустановку. Для этого
# сначала ставится пустой пакет с теми же зависимостями, а код — вторым слоем
# без них. Иначе любая правка заново качала и ставила все библиотеки, а на VM
# с 1 ГБ сборка образа — самое тяжёлое место деплоя.
COPY pyproject.toml ./
RUN mkdir repricer && touch repricer/__init__.py \
    && pip install --no-cache-dir . \
    && pip uninstall --yes kaspi-repricer \
    && rm -rf repricer build
COPY repricer ./repricer
RUN pip install --no-cache-dir --no-deps .

COPY alembic.ini ./
COPY alembic ./alembic

# Не root: если кто-то пролезет через парсер, прав у него не будет.
RUN useradd --create-home --uid 10001 repricer && mkdir -p /app/feeds && chown -R repricer /app
USER repricer

CMD ["uvicorn", "repricer.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
