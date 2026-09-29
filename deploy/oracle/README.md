# Oracle Cloud: один магазин, один сервер

Стек работает на Always Free VM в домашнем регионе Oracle. Предпочтительна
VM.Standard.A1.Flex (до 2 OCPU и 12 ГБ RAM суммарно). Когда A1 недоступна из-за
нехватки мощности, можно использовать VM.Standard.E2.1.Micro с Ubuntu x86_64.
У Micro только 1 ГБ RAM: добавьте 4 ГБ swap до сборки и собирайте образы
последовательно (`docker compose build api`, затем `docker compose build web`).
После первой сборки запускайте контейнеры с `--no-build`. Выбирайте форму и
диск с пометкой **Always Free Eligible**.

### Если на Micro не хватает памяти

В работе стек занимает около 700–800 МБ: API и воркер по ~100 МБ, бот и
панель по ~80–90 МБ, Postgres до 100 МБ, остальное — Docker и система. Сборка
образов при автодеплое добавляет ещё 500–800 МБ, и без свопа сервер начинает
тормозить или убивает процессы.

1. Проверить память и своп: `free -h`, `swapon --show`,
   `sudo docker stats --no-stream`.
2. Включить своп 4 ГБ, один раз: `sudo bash deploy/oracle/setup-swap.sh`.
   Скрипт ставит `vm.swappiness=10`: своп служит запасом на время сборки, а
   сервисы остаются в RAM.
3. В `docker-compose.yml` уже поджаты Postgres (`shared_buffers=32MB`, без JIT),
   Python-процессы (`MALLOC_ARENA_MAX=2`) и куча Node.js панели (192 МБ).
   Новые настройки базы применяются при пересоздании контейнера:
   `sudo docker compose up -d db`.

Если и со свопом тесно, переходите на A1.Flex (бесплатно, 12 ГБ) или на VPS
с 4 ГБ RAM.

## Сеть и домен

1. VM получает публичный IPv4. В правилах сети OCI открываются TCP 80 и 443
   для всех, TCP 22 только для IP администратора. Порты 3000, 5432 и 8000 не
   открываются.
2. В DuckDNS создаётся `kaspi-repricer.duckdns.org` с IPv4 этой VM. Если имя
   занято, берётся другое и его записывают в `SITE_DOMAIN`. При временной
   недоступности DuckDNS можно начать с имени вида
   `kaspi-repricer.132-145-102-181.sslip.io`, подставив IP своей VM.
3. Caddy автоматически получает и обновляет HTTPS-сертификат после появления
   DNS-записи и доступа к портам 80/443.

## Подготовка Ubuntu

Установить Docker Engine и Compose plugin по [официальной инструкции для
Ubuntu](https://docs.docker.com/engine/install/ubuntu/). Для Arm64 подходят
официальные пакеты Docker. Включить службу: `sudo systemctl enable --now docker`.

```bash
git clone https://github.com/xbatyr/kaspi-dumping.git
cd kaspi-dumping
umask 077
cp .env.example .env
```

В `.env` задать `POSTGRES_PASSWORD` (случайная длинная строка только из букв и
цифр), `REPRICER_API_KEY`, `DASHBOARD_PASSWORD`, `SITE_DOMAIN` и
`TELEGRAM_BOT_TOKEN`. Секреты не записывают в Git и не пересылают в чате.

```bash
chmod 600 .env
sudo docker compose config --quiet
sudo docker compose build api
sudo docker compose build web
sudo docker compose up -d --no-build
sudo docker compose ps
```

После запуска панель доступна по `https://kaspi-repricer.duckdns.org/`, а
прайс — по `https://kaspi-repricer.duckdns.org/feed/kaspi.xml`. Также работает
ссылка с именем `kaspi-price-list-{merchantid}.xml`, которую пишет воркер.
Панель требует
логин и пароль из `.env`. Фид доступен Kaspi без пароля. Проверка:

```bash
curl -I https://kaspi-repricer.duckdns.org/feed/kaspi.xml
curl -I https://kaspi-repricer.duckdns.org/
```

Фид должен отвечать `200` только после импорта магазина и товаров; панель без
пароля должна перенаправлять на `/login` (`307`). Автоматический демпинг начнётся после настройки
общей стратегии, индивидуальных границ и загрузки этой HTTPS-ссылки в Kaspi.

## Перенос уже настроенного магазина

До финального экспорта остановить локальные процессы воркера и Telegram-бота,
чтобы не было двух одновременных циклов и двух polling-процессов. Экспортировать
локальную PostgreSQL 18 через `pg_dump --format=custom --no-owner --no-acl`.
Дамп содержит секреты и данные магазина: хранить его вне репозитория и
передавать на VM только по SCP. На VM остановить `api`, `worker`, `bot`, `web`,
восстановить дамп в контейнер `db` через `pg_restore --clean --if-exists
--no-owner --no-acl`, выполнить `docker compose run --rm migrate` и вновь
запустить сервисы. После проверки данных и HTTPS удалить временный дамп.

Не включать демпинг до проверки, что фид содержит весь ассортимент и ссылка
добавлена в кабинет Kaspi. У Oracle Always Free возможен отзыв простаивающей
VM; базу стоит регулярно экспортировать за пределы сервера.

## Автоматическое обновление с GitHub

На VM включён systemd-таймер `kaspi-repricer-update.timer`: каждые пять минут
он проверяет ветку `main` на GitHub. Если появился новый коммит, он запускает
`deploy/oracle/update.sh`. Скрипт подтягивает коммит и вызывает
`deploy/oracle/deploy.sh`: делает резервную копию БД, последовательно собирает
образы, применяет миграции, перезапускает сервисы и проверяет HTTPS и XML.
Успешный коммит записывается отдельно; после сбоя следующий запуск повторит
попытку. Для таймера не нужны GitHub токен и открытый SSH для внешних runner.
Состояние: `systemctl status kaspi-repricer-update.timer` и
`journalctl -u kaspi-repricer-update.service -n 100`.

Изображения из Kaspi подгружаются фоновым воркером небольшими пачками; для
первичного заполнения каталога можно выполнить
`sudo docker compose exec -T api python -m repricer.images --limit 500`.
