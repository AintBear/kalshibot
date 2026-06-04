#!/usr/bin/env bash
# Re-deploy Sibylla to Fly.io after `git push`. This is the day-2 command.
# For first-time setup, see DEPLOYMENT.md.

set -euo pipefail

APP="${1:-$(grep -E '^app *= *' fly.toml | head -1 | sed -E 's/.*"([^"]+)".*/\1/')}"

if ! command -v fly >/dev/null 2>&1; then
  echo "ERROR: fly CLI not on PATH. Install with: brew install flyctl" >&2
  exit 1
fi

echo "Deploying $APP..."
fly deploy --app "$APP"

echo
echo "Waiting for health to come up (machine may be cold-starting)..."
healthy=0
for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  status="$(curl -fsS --max-time 8 "https://${APP}.fly.dev/health" 2>&1 || true)"
  if [[ "$status" == *'"status":"ok"'* ]]; then
    echo "  health: OK (attempt $i)"
    healthy=1
    break
  fi
  echo "  attempt $i: $status"
  sleep 6
done
if [[ "$healthy" != "1" ]]; then
  echo "ERROR: health never returned ok after deploy" >&2
  echo "Hint: 'fly logs --app $APP' for backend logs" >&2
  exit 1
fi

echo
echo "Running full smoke test..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ! "$SCRIPT_DIR/fly-smoke.sh" "$APP"; then
  echo "ERROR: smoke test failed after deploy. Bot is up but not operating cleanly." >&2
  exit 1
fi

echo
echo "Deploy complete."
echo "  App:        https://${APP}.fly.dev"
echo "  Health:     https://${APP}.fly.dev/health"
echo "  Logs:       fly logs --app $APP"
echo "  SSH:        fly ssh console --app $APP"
