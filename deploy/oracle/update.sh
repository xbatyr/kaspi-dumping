#!/usr/bin/env bash
set -euo pipefail

# Everything is in one function, called on the last line: bash reads a script
# as it runs, and `git merge` below may rewrite this very file mid-run. A
# function is parsed whole before it starts, so the old version finishes cleanly.
main() {
  cd /home/ubuntu/kaspi-dumping
  git fetch --quiet origin main
  remote_revision=$(git rev-parse origin/main)
  deployed_file=/home/ubuntu/.kaspi-repricer-deployed
  if [ -f "$deployed_file" ] && [ "$(cat "$deployed_file")" = "$remote_revision" ]; then
    return 0
  fi

  # A deploy that failed (on a 1 GB VM, usually the image build running out
  # of memory) is retried once an hour, not on every 5-minute tick: rebuilding
  # the same commit over and over is what keeps a small server swapping.
  failed_file=/home/ubuntu/.kaspi-repricer-failed
  retry_after=3600
  if [ -f "$failed_file" ]; then
    read -r failed_revision failed_at < "$failed_file" || true
    if [ "${failed_revision:-}" = "$remote_revision" ] \
      && [ $(( $(date +%s) - ${failed_at:-0} )) -lt "$retry_after" ]; then
      return 0
    fi
  fi

  test -z "$(git status --porcelain)"
  git merge --ff-only origin/main
  if ! bash deploy/oracle/deploy.sh; then
    printf '%s %s\n' "$remote_revision" "$(date +%s)" > "$failed_file"
    echo "Деплой $remote_revision не удался, следующая попытка через час" >&2
    return 1
  fi
  rm -f "$failed_file"
  printf '%s\n' "$remote_revision" > "$deployed_file.tmp"
  mv "$deployed_file.tmp" "$deployed_file"
}

main "$@"; exit $?
