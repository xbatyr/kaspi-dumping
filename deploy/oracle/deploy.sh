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

wait_http() {
  local path=$1 expected=$2 actual
  for attempt in $(seq 1 20); do
    actual=$(curl -sS --connect-timeout 5 --max-time 20 -o /dev/null -w '%{http_code}' \
      "https://kaspi-repricer.duckdns.org$path" || true)
    if [ "$actual" = "$expected" ]; then
      printf '%s: HTTP %s\n' "$path" "$actual"
      return 0
    fi
    sleep 3
  done
  printf 'Health check %s: expected %s, got %s\n' "$path" "$expected" "$actual" >&2
  return 1
}
# Every endpoint needs time to warm on a small VM, not just the redirect.
wait_http / 307
wait_http /login 200
wait_http /api/status 401
wait_http /feed/kaspi.xml 200
sudo docker compose ps
