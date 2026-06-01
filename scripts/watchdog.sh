#!/usr/bin/env bash
set -uo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

REPO_DIR="${KALSHIBOT_REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
BACKEND_URL="${KALSHIBOT_BACKEND_URL:-http://127.0.0.1:8000}"
MAX_SCAN_AGE_MIN="${KALSHIBOT_MAX_SCAN_AGE_MIN:-45}"
MAX_RUNNING_SCAN_MIN="${KALSHIBOT_MAX_RUNNING_SCAN_MIN:-20}"
SCAN_ERROR_RATE_THRESHOLD="${KALSHIBOT_SCAN_ERROR_RATE_THRESHOLD:-0.25}"
SCAN_RESTART_COOLDOWN_MIN="${KALSHIBOT_SCAN_RESTART_COOLDOWN_MIN:-20}"
LOG_DIR="${KALSHIBOT_WATCHDOG_LOG_DIR:-$REPO_DIR/logs}"
DRY_RUN=0

if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=1
fi

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/watchdog.log"
SCAN_RESTART_MARKER_FILE="$LOG_DIR/watchdog.scan_restart"

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
  [[ "$(health_decision)" == "health_ok" ]]
}

health_decision() {
  local body
  body="$(curl -sS --max-time 10 "$BACKEND_URL/health" 2>/dev/null)" || {
    printf 'backend_unreachable'
    return 0
  }
  HEALTH_JSON="$body" python3 - <<'PY'
import json
import os

try:
    data = json.loads(os.environ.get("HEALTH_JSON", ""))
except Exception:
    print("health_unreadable")
    raise SystemExit(0)

status = data.get("status")
issues = [str(issue) for issue in (data.get("issues") or [])]
if status == "ok" and not issues:
    print("health_ok")
elif issues and all(issue.startswith("scan_") for issue in issues):
    # Scan degradation is handled below by scan_decision(), which can decide
    # whether to restart, trigger a fresh scan, or wait out an active scan.
    print("scan_degraded")
else:
    print("backend_degraded")
PY
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

scan_marker() {
  local payload="$1"
  SCAN_STATUS_JSON="$payload" python3 - <<'PY'
import json
import os

try:
    data = json.loads(os.environ.get("SCAN_STATUS_JSON", ""))
except Exception:
    raise SystemExit(0)

marker = data.get("completed_at") or data.get("started_at") or data.get("_persisted_updated_at") or ""
print(str(marker))
PY
}

scan_restart_allowed() {
  local decision="$1"
  local marker="$2"
  local now last_decision last_marker last_epoch

  if [[ -z "$marker" || ! -f "$SCAN_RESTART_MARKER_FILE" ]]; then
    return 0
  fi

  IFS=$'\t' read -r last_decision last_marker last_epoch <"$SCAN_RESTART_MARKER_FILE" || return 0
  now="$(date +%s)"

  if [[ "$last_decision" == "$decision" && "$last_marker" == "$marker" ]]; then
    return 1
  fi
  if [[ "$last_epoch" =~ ^[0-9]+$ ]] && (( now - last_epoch < SCAN_RESTART_COOLDOWN_MIN * 60 )); then
    return 1
  fi
  return 0
}

record_scan_restart() {
  local decision="$1"
  local marker="$2"
  printf '%s\t%s\t%s\n' "$decision" "$marker" "$(date +%s)" >"$SCAN_RESTART_MARKER_FILE"
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

  local health_state
  health_state="$(health_decision)"
  case "$health_state" in
    health_ok)
      ;;
    scan_degraded)
      log "Backend reachable but scan health is degraded; deferring to scan decision"
      ;;
    backend_unreachable|backend_degraded|health_unreadable)
      log "Backend health failed ($health_state); restarting backend"
      restart_backend
      health_state="$(health_decision)"
      if [[ "$health_state" != "health_ok" && "$health_state" != "scan_degraded" ]]; then
        log "ERROR backend health still failed after restart: $health_state"
        exit 1
      fi
      ;;
    *)
      log "Unknown health decision: $health_state; restarting backend"
      restart_backend
      ;;
  esac

  local scan_status decision poke_auto_entry marker
  poke_auto_entry=1
  scan_status="$(fetch_json "$BACKEND_URL/api/scan/status" 2>/dev/null)" || scan_status=""
  decision="$(scan_decision "$scan_status" 2>/dev/null || printf 'scan_unreadable')"
  log "Scan decision: $decision"

  case "$decision" in
    scan_stuck|scan_high_error_rate)
      marker="$(scan_marker "$scan_status" 2>/dev/null || true)"
      if scan_restart_allowed "$decision" "$marker"; then
        log "Scan unhealthy ($decision); restarting backend"
        restart_backend
        record_scan_restart "$decision" "$marker"
      else
        log "Scan unhealthy ($decision) but restart suppressed for marker=$marker cooldown=${SCAN_RESTART_COOLDOWN_MIN}m"
      fi
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
