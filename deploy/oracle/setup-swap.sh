#!/usr/bin/env bash
# Своп для VM с 1 ГБ RAM: без него сборка образов при автодеплое вытесняет
# работающие сервисы, и всё начинает тормозить или падает по памяти.
# Запускать один раз: sudo bash deploy/oracle/setup-swap.sh [размер, по умолчанию 4G]
set -euo pipefail

size="${1:-4G}"
swapfile=/swapfile

if swapon --show=NAME --noheadings | grep -qx "$swapfile"; then
  echo "Своп уже включён:"
else
  fallocate -l "$size" "$swapfile" || dd if=/dev/zero of="$swapfile" bs=1M count="$(( ${size%G} * 1024 ))"
  chmod 600 "$swapfile"
  mkswap "$swapfile"
  swapon "$swapfile"
  grep -q "^$swapfile " /etc/fstab || echo "$swapfile none swap sw 0 0" >> /etc/fstab
  echo "Своп $size включён и переживёт перезагрузку:"
fi

# Своп — запас на время сборки, а не рабочая память: пусть ядро держит
# сервисы в RAM, пока может.
sysctl -q vm.swappiness=10
echo "vm.swappiness=10" > /etc/sysctl.d/99-kaspi-repricer-swap.conf

swapon --show
free -h
