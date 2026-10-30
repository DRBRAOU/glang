#!/usr/bin/env bash
# Restore database + audio from a backup archive.
# Usage: sudo ./scripts/restore.sh data/backups/gondi-vaani-backup-YYYYMMDD-HHMMSS.tar.gz
# The current state is saved first as a new "safety" backup in data/backups/.
set -euo pipefail
cd "$(dirname "$0")/.."
ARCHIVE="${1:?path to backup archive required}"
case "$ARCHIVE" in
  data/backups/*) INSIDE="/data/backups/$(basename "$ARCHIVE")" ;;
  *) cp "$ARCHIVE" data/backups/ && INSIDE="/data/backups/$(basename "$ARCHIVE")" ;;
esac
read -r -p "This replaces the current database. Continue? [y/N] " ok
[ "$ok" = "y" ] || { echo "Cancelled."; exit 1; }
docker compose stop app
docker compose run --rm --no-deps app cli restore "$INSIDE" --yes
docker compose start app
echo "Restore complete."
