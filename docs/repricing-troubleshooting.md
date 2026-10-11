# Почему цены не меняются

## Как проходит изменение

`WorkerService` читает настройки магазина из PostgreSQL. `RepricingWorker`
выбирает включённые правила для доступных товаров, получает предложения Kaspi
для каждого города и передаёт их в `PricingEngine`. `SyncManager` сохраняет
новую цену и историю. API собирает публичный XML из базы при каждом запросе.
Kaspi должен забрать этот XML и применить цены; доступность сайта сама по себе
не подтверждает работу воркера или загрузку прайса в Kaspi.

В тестовом режиме вместо записи создаётся предложение в `price_proposals`.
Подтверждение в Telegram повторно проверяет рынок, настройки и права владельца.
После записи `pending_price` блокирует следующий пересчёт, пока фактическая
цена магазина на Kaspi не совпадёт с ожидаемой. Ожидание сохраняется после
перезапуска и не снимается по времени.

## Проверка на сервере

Из каталога проекта:

```bash
sudo docker compose ps
sudo docker compose logs --tail=200 worker
sudo docker compose logs --tail=100 migrate
sudo docker compose exec -T api alembic current
sudo docker compose exec -T api alembic heads
sudo docker compose exec -T db psql -U repricer -d repricer -c \
  'SELECT worker_enabled, test_mode, global_strategy, global_city_ids FROM shop_settings'
sudo docker compose exec -T db psql -U repricer -d repricer -c \
  'SELECT p.sku, r.city_id, r.current_price, r.pending_price, r.price_requested_at, r.last_evaluated_at, r.market_snapshot FROM repricer_rules r JOIN products p ON p.id = r.product_id WHERE r.is_active ORDER BY r.price_requested_at NULLS LAST LIMIT 30'
curl --fail --silent --show-error https://YOUR_DOMAIN/feed/kaspi.xml
```

Фид проверяется **GET-запросом**. `curl -I` делает HEAD, который этот маршрут
не поддерживает: ответ 405 не означает, что GET-фид сломан.

Воркер должен быть включён, а общая стратегия и города — сохранены. Товару
нужны карточка Kaspi, включённое правило, доступный склад, положительный или
неуказанный остаток и Max выше Min. Дополнительные причины пропуска:

- Магазин отсутствует среди предложений в выбранном городе.
- Безубыточная цена по закупке, налогу, комиссии и доставке выше Max.
- Автоснижение/автоповышение выключено либо действует задержка повышения.
- Включён тестовый режим, но предложение ещё не подтверждено.
- `pending_price` не совпадает с `market_snapshot.observed_price`: Kaspi ещё
  не применил XML. Проверьте источник автоматической загрузки и ошибки его
  обработки в кабинете продавца, а также другие программы, меняющие цены.
- Запросы к Kaspi завершаются ошибками: смотрите прокси и логи воркера.

Не сбрасывайте `pending_price` вслепую: это может вернуть гонку между XML и
старой ценой витрины. В логах каждого пропуска теперь видны SKU, город,
причина, ожидаемая цена XML и наблюдаемая цена Kaspi.

## Локальный запуск

Установите зависимости и примените миграции перед запуском. Команды `repricer`
и `alembic` читают `.env` из текущего каталога; переменные окружения имеют
приоритет. Telegram запускается отдельной командой `repricer-bot`.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/alembic upgrade head
.venv/bin/repricer --once --dry-run
```

Проверяйте тесты только на отдельной одноразовой базе: фикстуры удаляют таблицы.
Если сборка панели после переноса страниц жалуется на старые пути в
`.next/dev/types`, остановите локальный Next, перенесите старый `.next` из
проекта и соберите панель заново.

## Перенос сервера

Сохраните PostgreSQL через `pg_dump -Fc`, серверный `.env`, домен и настройки
автоматической загрузки Kaspi. Не запускайте два воркера и два Telegram polling
процесса одновременно. Восстановите базу, примените миграции и проверьте XML
до включения воркера. Скрипты в `deploy/oracle` сейчас привязаны к
`/home/ubuntu/kaspi-dumping` и домену `kaspi-repricer.duckdns.org`; для другого
хоста нужно адаптировать пути, проверки домена и systemd-службы.
