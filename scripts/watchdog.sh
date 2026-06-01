#!/usr/bin/env bash
set -uo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

REPO_DIR="${KALSHIBOT_REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
BACKEND_URL="${KALSHIBOT_BACKEND_URL:-http://127.0.0.1:8000}"
MAX_SCAN_AGE_MIN="${KALSHIBOT_MAX_SCAN_AGE_MIN:-45}"
MAX_RUNNING_SCAN_MIN="${KALSHIBOT_MAX_RUNNING_SCAN_MIN:-20}"
SCAN_ERROR_RATE_THRESHOLD="${KALSHIBOT_SCAN_ERROR_RATE_THRESHOLD:-0.25}"
LOG_DIR="${KALSHIBOT_WATCHDOG_LOG_DIR:-$REPO_DIR/logs}"
DRY_RUN=0

if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=1
fi

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/watchdog.log"

log() {
  printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" | tee -a "$LOG_FILE"
}

run_cmd() {
  if [[ "$DRY_RUN" == "1" ]]; then
    log "DRY_RUN $*"
    return 0
  fi
  log "RUN $*"
  "$@" >>"$LOG_FILE" 2>&1
}

cd "$REPO_DIR" || {
  log "ERROR repo directory unavailable: $REPO_DIR"
  exit 1
}

docker_ready() {
  docker info >/dev/null 2>&1
}

ensure_docker() {
  if docker_ready; then
    return 0
  fi

  log "Docker daemon unavailable"
  if command -v colima >/dev/null 2>&1; then
    log "Attempting Colima start"
    run_cmd colima start
    sleep 8
  fi

  if docker_ready; then
    return 0
  fi

  log "ERROR Docker still unavailable; start Docker Desktop or Colima"
  return 1
}

compose_up() {
  run_cmd docker compose up -d
}

restart_backend() {
  run_cmd docker compose restart backend
  sleep 12
}

fetch_json() {
  curl -fsS --max-time 10 "$1"
}

json_field() {
  python3 -c 'import json,sys; data=json.load(sys.stdin); print(data.get(sys.argv[1], ""))' "$1"
}

health_ok() {
  local body status
  body="$(fetch_json "$BACKEND_URL/health" 2>/dev/null)" || return 1
  status="$(printf '%s' "$body" | json_field status 2>/dev/null)" || return 1
  [[ "$status" == "ok" ]]
}

scan_decision() {
  local payload="$1"
  SCAN_STATUS_JSON="$payload" python3 - "$MAX_SCAN_AGE_MIN" "$MAX_RUNNING_SCAN_MIN" "$SCAN_ERROR_RATE_THRESHOLD" <<'PY'
import os
import json
import sys
from datetime import datetime, timezone

max_age_min = int(sys.argv[1])
max_running_min = int(sys.argv[2])
error_rate_threshold = float(sys.argv[3])
try:
    data = json.loads(os.environ.get("SCAN_STATUS_JSON", ""))
except Exception:
    print("scan_unreadable")
    raise SystemExit(0)

def parse_time(value):
    if not value:
        return None
    raw = str(value).replace(" ", "T")
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    if "+" not in raw and raw.count("-") >= 2:
        raw += "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

now = datetime.now(timezone.utc)
status = data.get("status")
if status == "running":
    started = parse_time(data.get("started_at"))
    if started and (now - started).total_seconds() > max_running_min * 60:
        print("scan_stuck")
    else:
        print("scan_running")
    raise SystemExit(0)

completed = parse_time(data.get("completed_at"))
series_total = int(data.get("series_total") or 0)
series_errors = int(data.get("series_errors") or 0)
markets_processed = int(data.get("markets_processed") or 0)
error_rate = (series_errors / series_total) if series_total > 0 else 0.0

if not completed:
    print("scan_missing")
elif (now - completed).total_seconds() > max_age_min * 60:
    print("scan_stale")
elif series_errors > 0 and markets_processed == 0:
    print("scan_failed")
elif series_total > 0 and error_rate >= error_rate_threshold:
    # Lots of series errored even though some markets came back. Usually a
    # process-resident DNS cache (session 8) or virtiofs corruption
    # (session 9) — re-triggering the scan won't help, we need a restart.
    print("scan_high_error_rate")
else:
    print("scan_ok")
PY
}

trigger_scan() {
  if [[ "$DRY_RUN" == "1" ]]; then
    log "DRY_RUN POST $BACKEND_URL/api/scan/weather"
    return 0
  fi
  log "POST $BACKEND_URL/api/scan/weather"
  curl -fsS --max-time 10 -X POST "$BACKEND_URL/api/scan/weather" >>"$LOG_FILE" 2>&1 || return 1
}

run_auto_entry_if_safe() {
  local body paper_enabled live_enabled paper_ready
  body="$(fetch_json "$BACKEND_URL/api/auto-trade/status" 2>/dev/null)" || {
    log "Auto-entry status unavailable"
    return 1
  }
  paper_enabled="$(printf '%s' "$body" | json_field paper_auto_enabled 2>/dev/null)"
  live_enabled="$(printf '%s' "$body" | json_field live_auto_enabled 2>/dev/null)"
  paper_ready="$(printf '%s' "$body" | json_field paper_ready 2>/dev/null)"

  if [[ "$live_enabled" == "True" || "$live_enabled" == "true" ]]; then
    log "Live auto is enabled; watchdog will not poke auto-entry"
    return 0
  fi
  if [[ "$paper_enabled" != "True" && "$paper_enabled" != "true" ]]; then
    log "Paper auto disabled; nothing to poke"
    return 0
  fi
  if [[ "$paper_ready" != "True" && "$paper_ready" != "true" ]]; then
    log "Paper auto not ready; respecting backend blocker"
    return 0
  fi

  if [[ "$DRY_RUN" == "1" ]]; then
    log "DRY_RUN POST $BACKEND_URL/api/auto-trade/run"
    return 0
  fi
  log "POST $BACKEND_URL/api/auto-trade/run"
  curl -fsS --max-time 60 -X POST "$BACKEND_URL/api/auto-trade/run" \
    -H 'content-type: application/json' \
    -d '{}' >>"$LOG_FILE" 2>&1 || return 1
}

main() {
  log "Watchdog tick repo=$REPO_DIR dry_run=$DRY_RUN"

  ensure_docker || exit 1
  compose_up

  if ! health_ok; then
    log "Backend health failed; restarting backend"
    restart_backend
  fi

  if ! health_ok; then
    log "ERROR backend health still failed after restart"
    exit 1
  fi

  local scan_status decision poke_auto_entry
  poke_auto_entry=1
  scan_status="$(fetch_json "$BACKEND_URL/api/scan/status" 2>/dev/null)" || scan_status=""
  decision="$(scan_decision "$scan_status" 2>/dev/null || printf 'scan_unreadable')"
  log "Scan decision: $decision"

  case "$decision" in
    scan_stuck|scan_high_error_rate)
      log "Scan unhealthy ($decision); restarting backend"
      restart_backend
      poke_auto_entry=0
      ;;
    scan_missing|scan_stale|scan_failed|scan_unreadable)
      trigger_scan || log "Scan trigger failed"
      poke_auto_entry=0
      ;;
    scan_running)
      poke_auto_entry=0
      ;;
    scan_ok)
      ;;
    *)
      log "Unknown scan decision: $decision"
      poke_auto_entry=0
      ;;
  esac

  if [[ "$poke_auto_entry" == "1" ]]; then
    run_auto_entry_if_safe || log "Auto-entry poke failed"
  else
    log "Auto-entry poke skipped while scan state is $decision"
  fi
  log "Watchdog tick complete"
}

main "$@"
