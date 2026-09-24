# Oracle Cloud: один магазин, один сервер

Этот стек рассчитан на VM.Standard.A1.Flex с Ubuntu Arm64 в домашнем регионе
Oracle. В Always Free сейчас доступны суммарно 2 OCPU и 12 ГБ RAM; при создании
нужно выбрать форму и диск с пометкой **Always Free Eligible**.

## Сеть и домен

1. VM получает публичный IPv4. В правилах сети OCI открываются TCP 80 и 443
   для всех, TCP 22 только для IP администратора. Порты 3000, 5432 и 8000 не
   открываются.
2. В DuckDNS создаётся `kaspi-repricer.duckdns.org` с IPv4 этой VM. Если имя
   занято, берётся другое и его записывают в `SITE_DOMAIN`.
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
sudo docker compose up -d --build
sudo docker compose ps
```

После запуска панель доступна по `https://kaspi-repricer.duckdns.org/`, а
прайс — по `https://kaspi-repricer.duckdns.org/feed/kaspi.xml`. Панель требует
логин и пароль из `.env`. Фид доступен Kaspi без пароля. Проверка:

```bash
curl -I https://kaspi-repricer.duckdns.org/feed/kaspi.xml
curl -I https://kaspi-repricer.duckdns.org/
```

Фид должен отвечать `200` только после импорта магазина и товаров; панель без
пароля должна отвечать `401`. Автоматический демпинг начнётся после настройки
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
