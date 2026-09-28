#!/usr/bin/env bash
set -euo pipefail

cd /home/ubuntu/kaspi-dumping
sudo docker compose config --quiet
mkdir -p /home/ubuntu/backups
backup="/home/ubuntu/backups/before-deploy-$(date -u +%Y%m%dT%H%M%SZ).dump"
sudo docker compose exec -T db pg_dump -U repricer -Fc repricer > "$backup"
chmod 600 "$backup"

# A Micro VM has only 1 GB of RAM, so build the images one after the other.
sudo docker compose build api
sudo docker compose build web
sudo docker compose run --rm migrate
sudo docker compose up -d --no-build api worker bot web

for attempt in $(seq 1 30); do
  status=$(curl -sS -o /dev/null -w '%{http_code}' https://kaspi-repricer.duckdns.org/ || true)
  if [ "$status" = 401 ]; then
    break
  fi
  sleep 3
done
test "$status" = 401
test "$(curl -sS -o /dev/null -w '%{http_code}' https://kaspi-repricer.duckdns.org/feed/kaspi.xml)" = 200
sudo docker compose ps
