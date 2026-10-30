#!/usr/bin/env bash
# Obtain / renew a Let's Encrypt certificate using the running nginx container (webroot).
# Usage: sudo ./scripts/letsencrypt.sh <domain> <email>
# Renew (cron, twice daily is fine):  sudo ./scripts/letsencrypt.sh <domain> <email> renew
set -euo pipefail
cd "$(dirname "$0")/.."
DOMAIN="${1:?domain required}"; EMAIL="${2:?email required}"; MODE="${3:-issue}"
mkdir -p deployment/letsencrypt deployment/certbot-www

if [ "$MODE" = "renew" ]; then
  docker run --rm -v "$PWD/deployment/letsencrypt:/etc/letsencrypt" -v "$PWD/deployment/certbot-www:/var/www/certbot" \
    certbot/certbot:v2.11.0 renew --webroot -w /var/www/certbot --quiet
else
  docker run --rm -v "$PWD/deployment/letsencrypt:/etc/letsencrypt" -v "$PWD/deployment/certbot-www:/var/www/certbot" \
    certbot/certbot:v2.11.0 certonly --webroot -w /var/www/certbot -d "$DOMAIN" \
    --email "$EMAIL" --agree-tos --no-eff-email --non-interactive
fi

cp -L "deployment/letsencrypt/live/$DOMAIN/fullchain.pem" deployment/certs/fullchain.pem
cp -L "deployment/letsencrypt/live/$DOMAIN/privkey.pem" deployment/certs/privkey.pem
chmod 600 deployment/certs/privkey.pem
docker compose exec nginx nginx -s reload
echo "Certificate for $DOMAIN installed."
