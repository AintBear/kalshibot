#!/usr/bin/env bash
# Upload Sibylla's secrets to a freshly-provisioned Fly.io app.
#
# Run this ONCE after `fly launch` + volume create. It does two things:
#   1. uploads config/settings.json into the sibylla_config volume
#   2. uploads config/kalshi_private_key.pem into the sibylla_config volume
#
# It does NOT store these as fly secrets — they live on the persistent
# volume mounted at /app/config, so we can rotate them later via SSH
# without redeploying.
#
# Usage:
#   scripts/fly-bootstrap-secrets.sh [app-name]
#
# Defaults to the app name in fly.toml.

set -euo pipefail

APP="${1:-$(grep -E '^app *= *' fly.toml | head -1 | sed -E 's/.*"([^"]+)".*/\1/')}"
SETTINGS="config/settings.json"
PEM="config/kalshi_private_key.pem"

if [[ -z "$APP" ]]; then
  echo "ERROR: could not resolve app name; pass it as the first argument" >&2
  exit 1
fi

if [[ ! -f "$SETTINGS" ]]; then
  echo "ERROR: $SETTINGS not found. Copy from config/settings.example.json and edit." >&2
  exit 1
fi

if [[ ! -f "$PEM" ]]; then
  echo "ERROR: $PEM not found. Place your Kalshi RSA private key there." >&2
  exit 1
fi

if ! command -v fly >/dev/null 2>&1; then
  echo "ERROR: fly CLI not on PATH. Install with: brew install flyctl" >&2
  exit 1
fi

# Ensure a machine exists so we can `fly ssh sftp shell`.
echo "Verifying the app has at least one machine..."
fly status --app "$APP" >/dev/null

echo "Uploading $SETTINGS -> /app/config/settings.json"
fly ssh sftp shell --app "$APP" <<EOF
put $SETTINGS /app/config/settings.json
EOF

echo "Uploading $PEM -> /app/config/kalshi_private_key.pem"
fly ssh sftp shell --app "$APP" <<EOF
put $PEM /app/config/kalshi_private_key.pem
EOF

echo
echo "Done. Verify with:"
echo "  fly ssh console --app $APP -C 'ls -la /app/config'"
echo "  curl https://${APP}.fly.dev/health"
