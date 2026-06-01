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
echo "Verifying health..."
for i in 1 2 3 4 5 6 7 8 9 10; do
  status="$(curl -fsS --max-time 8 "https://${APP}.fly.dev/health" 2>&1 || true)"
  if [[ "$status" == *'"status":"ok"'* ]]; then
    echo "  health: OK"
    break
  fi
  echo "  attempt $i: $status"
  sleep 6
done

echo
echo "Quick endpoint sweep:"
echo "  /api/auto-trade/status: $(curl -fsS --max-time 5 "https://${APP}.fly.dev/api/auto-trade/status" | python3 -c "import json,sys; d=json.load(sys.stdin); print('paper_ready=', d.get('paper_ready'), 'live_ready=', d.get('live_ready'), 'brain=', d.get('brain_score'))")"
echo "  /api/brain/status.score_breakdown.biggest_gap: $(curl -fsS --max-time 5 "https://${APP}.fly.dev/api/brain/status" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('score_breakdown',{}).get('biggest_gap'))")"
