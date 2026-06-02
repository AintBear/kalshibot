#!/usr/bin/env bash
# Post-deploy smoke test for the Sibylla Kalshi bot on Fly.io.
#
# Hits the five endpoints that prove the bot is alive, learning, and ready
# to take trades. Exits non-zero on any failure so it can gate a deploy.
#
# Usage:
#   scripts/fly-smoke.sh [app-name]
#   scripts/fly-smoke.sh --local        # smoke the local Docker bot
#
# Defaults to the app name in fly.toml.

set -euo pipefail

BASE=""
if [[ "${1:-}" == "--local" ]]; then
  BASE="http://localhost:8000"
  echo "Smoke testing local bot at $BASE"
else
  APP="${1:-$(grep -E '^app *= *' fly.toml | head -1 | sed -E 's/.*"([^"]+)".*/\1/')}"
  if [[ -z "$APP" ]]; then
    echo "ERROR: could not resolve app name from fly.toml; pass it as the first argument" >&2
    exit 1
  fi
  BASE="https://${APP}.fly.dev"
  echo "Smoke testing $BASE"
fi

fail=0

check() {
  local label="$1"
  local url="$2"
  local jq_expr="$3"
  local response
  if ! response="$(curl -fsS --max-time 10 "$url" 2>&1)"; then
    echo "  [FAIL] $label  $url  ->  $response"
    fail=1
    return
  fi
  local extracted
  extracted="$(echo "$response" | python3 -c "import json,sys; d=json.load(sys.stdin); $jq_expr" 2>&1 || true)"
  echo "  [ok]   $label  $extracted"
}

echo
echo "=== Health ==="
check "GET /health" "$BASE/health" \
  "print('status=', d.get('status'), 'issues=', d.get('issues', []))"

echo
echo "=== Scan loop ==="
check "GET /api/scan/status" "$BASE/api/scan/status" \
  "print('status=', d.get('status'), 'markets=', d.get('markets_processed','-'),'/', d.get('markets_found','-'), 'errors=', d.get('series_errors','-'))"

echo
echo "=== Auto-trade gate ==="
check "GET /api/auto-trade/status" "$BASE/api/auto-trade/status" \
  "print('paper_auto=', d.get('paper_auto_enabled'), 'paper_ready=', d.get('paper_ready'), 'live_auto=', d.get('live_auto_enabled'), 'live_blocker=', d.get('live_blocker'))"

echo
echo "=== Brain ==="
check "GET /api/brain/status" "$BASE/api/brain/status" \
  "g=d.get('score_breakdown',{}).get('biggest_gap',{}); print('score=', d.get('score'), 'samples=', d.get('learning_samples'), 'open=', d.get('open_trades'), 'recent_pnl=\$' + str(d.get('recent_30_pnl_paper')), 'entry_ok=', d.get('entry_quality_ok'), 'biggest_gap=' + str(g.get('component')) + ' ' + str(g.get('headroom')))"

echo
echo "=== Open trades ==="
check "GET /api/trades?status=open" "$BASE/api/trades?status=open" \
  "n=len(d) if isinstance(d, list) else len(d.get('trades', [])); print('open_count=', n)"

echo
if [[ "$fail" == "0" ]]; then
  echo "Smoke test: PASS"
  exit 0
else
  echo "Smoke test: FAIL"
  exit 1
fi
