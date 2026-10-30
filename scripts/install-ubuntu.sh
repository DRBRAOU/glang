#!/usr/bin/env bash
# One-time setup of Gondi Vaani on Ubuntu 22.04 / 24.04 with Docker.
# Run from the project folder:   sudo ./scripts/install-ubuntu.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if [ "$(id -u)" -ne 0 ]; then echo "Please run with sudo."; exit 1; fi

echo "==> Installing Docker Engine and Compose plugin (if missing)"
if ! command -v docker >/dev/null 2>&1; then
  apt-get update
  apt-get install -y ca-certificates curl gnupg openssl
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
fi
docker compose version

echo "==> Preparing configuration"
[ -f .env ] || cp .env.example .env
mkdir -p data deployment/certs deployment/certbot-www

if [ ! -f deployment/certs/fullchain.pem ]; then
  echo "==> No certificate found; creating a temporary self-signed one"
  ./scripts/make-selfsigned-cert.sh "${1:-$(hostname -f)}"
fi

echo "==> Opening firewall ports 80/443 (if ufw is active)"
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  ufw allow 80/tcp; ufw allow 443/tcp
fi

echo "==> Building and starting containers"
docker compose up -d --build

echo
echo "Done. Next steps:"
echo "  1. Create the first administrator:"
echo "       sudo ./scripts/gv create-admin --username admin --name \"Your Name\""
echo "  2. (Optional) add starter categories:   sudo ./scripts/gv seed-categories"
echo "  3. For a real certificate:   sudo ./scripts/letsencrypt.sh gondivaani.example.ac.in you@example.ac.in"
echo "  4. Schedule nightly backups: see README (Backups)."
echo "Learner page:  https://<server>/      Editorial console:  https://<server>/admin"
