#!/usr/bin/env bash
set -euo pipefail

cd /home/ubuntu/kaspi-dumping
git fetch --quiet origin main
remote_revision=$(git rev-parse origin/main)
deployed_file=/home/ubuntu/.kaspi-repricer-deployed
if [ -f "$deployed_file" ] && [ "$(cat "$deployed_file")" = "$remote_revision" ]; then
  exit 0
fi

test -z "$(git status --porcelain)"
git merge --ff-only origin/main
bash deploy/oracle/deploy.sh
printf '%s\n' "$remote_revision" > "$deployed_file.tmp"
mv "$deployed_file.tmp" "$deployed_file"
