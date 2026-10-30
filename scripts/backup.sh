#!/usr/bin/env bash
# Create a backup (database + all audio) and keep the newest N archives.
# Archives are written to ./data/backups/ on the host. Copy them off the server too!
# Cron example (02:30 every night):
#   30 2 * * * cd /opt/gondi-vaani && ./scripts/backup.sh >> /var/log/gondi-vaani-backup.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."
KEEP="${KEEP:-14}"
docker compose exec -T -u gondi app python -m app.cli backup
ls -1t data/backups/gondi-vaani-backup-*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f
echo "$(date -Is) backups kept: $(ls -1 data/backups/gondi-vaani-backup-*.tar.gz | wc -l)"
# Optional off-site copy (uncomment and adjust):
# rsync -a data/backups/ backup-user@backup-host:/srv/backups/gondi-vaani/
