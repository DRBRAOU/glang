#!/bin/sh
# Prepares /data, runs database migrations, then starts the app as an unprivileged user.
set -eu

DATA_DIR="${GV_STORAGE_DIR:-/data}"
mkdir -p "$DATA_DIR/audio" "$DATA_DIR/imports" "$DATA_DIR/backups"
# Bind-mounted host folders may be owned by root; hand them to the app user.
if [ "$(id -u)" = "0" ]; then
  chown -R gondi:gondi "$DATA_DIR"
  chmod 0750 "$DATA_DIR"
  RUN_AS="setpriv --reuid=gondi --regid=gondi --init-groups"
else
  RUN_AS=""
fi

case "${1:-serve}" in
  serve)
    echo "[gondi-vaani] applying database migrations..."
    $RUN_AS alembic upgrade head
    echo "[gondi-vaani] starting web server"
    exec $RUN_AS uvicorn app.main:app \
      --host 0.0.0.0 --port 8000 \
      --workers "${GV_WORKERS:-2}" \
      --proxy-headers --forwarded-allow-ips "*" \
      --no-server-header
    ;;
  cli)
    shift
    exec $RUN_AS python -m app.cli "$@"
    ;;
  *)
    exec $RUN_AS "$@"
    ;;
esac
