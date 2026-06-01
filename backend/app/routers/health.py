from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()


SCAN_MAX_AGE_SECONDS = 45 * 60          # last completed scan older than this -> degraded
SCAN_RUNNING_MAX_SECONDS = 20 * 60      # a "running" scan older than this is stuck
SCAN_ERROR_RATE_THRESHOLD = 0.25        # series_errors / series_total above this -> degraded


def _parse_ts(value) -> Optional[datetime]:
    if not value:
        return None
    raw = str(value).replace(" ", "T")
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _check_scan() -> Optional[str]:
    """Return an issue tag if the scanner is unhealthy, else None.

    The scanner is the bot's only useful job — if it has not run cleanly in a
    long time, /health must not report "ok" or the launchd watchdog and
    Docker's curl healthcheck will both miss outages (see CLAUDE.md sessions
    8 and 9).
    """
    try:
        from app import config as cfg
        if not bool(cfg.load().get("automation_enabled", False)):
            return None
    except Exception:
        return "config_unavailable"

    try:
        from app.services.scanner import get_scan_status  # local import to dodge circulars
    except Exception:
        return None

    try:
        status = get_scan_status() or {}
    except Exception:
        return "scan_status_unavailable"

    now = datetime.now(timezone.utc)
    state = status.get("status")

    if state == "running":
        started = _parse_ts(status.get("started_at"))
        if started and (now - started).total_seconds() > SCAN_RUNNING_MAX_SECONDS:
            return "scan_stuck"
        return None

    completed = _parse_ts(status.get("completed_at"))
    if not completed:
        return "scan_never_completed"
    if (now - completed).total_seconds() > SCAN_MAX_AGE_SECONDS:
        return "scan_stale"

    series_total = int(status.get("series_total") or 0)
    series_errors = int(status.get("series_errors") or 0)
    markets_processed = int(status.get("markets_processed") or 0)

    if series_errors > 0 and markets_processed == 0:
        return "scan_failed"

    # Lots of series errored even though some markets came back — usually a
    # process-resident DNS cache or virtiofs issue (sessions 8 + 9). Treat as
    # degraded so the watchdog restarts the backend instead of re-triggering
    # the scan against the same broken resolver.
    if series_total > 0 and (series_errors / series_total) >= SCAN_ERROR_RATE_THRESHOLD:
        return "scan_high_error_rate"

    return None


@router.get("/health")
def health():
    issues = []

    try:
        from app.database import get_conn
        conn = get_conn()
        conn.execute("SELECT 1")
        conn.close()
    except Exception:
        issues.append("database_unavailable")

    scan_issue = _check_scan()
    if scan_issue:
        issues.append(scan_issue)

    body = {
        "status": "degraded" if issues else "ok",
        "issues": issues,
    }
    status_code = 503 if issues else 200
    return JSONResponse(content=body, status_code=status_code)
