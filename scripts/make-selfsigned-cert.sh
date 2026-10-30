#!/usr/bin/env bash
# Creates a self-signed certificate so HTTPS (and browser microphone recording) works
# immediately. Browsers will show a warning; replace it with Let's Encrypt or your
# university certificate for production.
set -euo pipefail
cd "$(dirname "$0")/.."
NAME="${1:-localhost}"
mkdir -p deployment/certs
openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
  -keyout deployment/certs/privkey.pem -out deployment/certs/fullchain.pem \
  -subj "/CN=${NAME}" -addext "subjectAltName=DNS:${NAME},DNS:localhost,IP:127.0.0.1"
chmod 600 deployment/certs/privkey.pem
echo "Self-signed certificate written to deployment/certs/ for ${NAME}"
