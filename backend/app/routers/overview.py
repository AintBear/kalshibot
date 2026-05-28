import time
import threading
from fastapi import APIRouter

router = APIRouter()

_balance_cache = {"data": None, "ts": 0.0, "refreshing": False}
_balance_lock = threading.Lock()


@router.get("/overview")
def overview():
    from app.database import get_conn
    conn = get_conn()

    markets_total = conn.execute("SELECT COUNT(*) FROM markets WHERE status IN ('open','active')").fetchone()[0]
    alerts_pending = conn.execute("SELECT COUNT(*) FROM alerts WHERE status='pending'").fetchone()[0]
    alerts_active = conn.execute(
        "SELECT COUNT(*) FROM alerts WHERE status IN ('pending','paper_traded')"
    ).fetchone()[0]
    trades_open = conn.execute("SELECT COUNT(*) FROM trades WHERE status='open'").fetchone()[0]
    trades_closed = conn.execute("SELECT COUNT(*) FROM trades WHERE status='closed'").fetchone()[0]
    model_outputs = conn.execute("SELECT COUNT(*) FROM model_outputs WHERE category='weather'").fetchone()[0]
    forecast_snapshots = conn.execute("SELECT COUNT(*) FROM forecast_snapshots").fetchone()[0]

    pnl_row = conn.execute(
        "SELECT SUM(pnl) FROM trades WHERE status='closed' AND paper=1"
    ).fetchone()
    total_pnl = round(pnl_row[0] or 0.0, 4)
    learning_pnl_row = conn.execute(
        """SELECT SUM(pnl) FROM trades
            WHERE status='closed'
              AND paper=1
              AND COALESCE(exit_reason, '') != 'paper_reset'"""
    ).fetchone()
    learning_pnl = round(learning_pnl_row[0] or 0.0, 4)
    reset_pnl_row = conn.execute(
        "SELECT SUM(pnl) FROM trades WHERE status='closed' AND paper=1 AND exit_reason='paper_reset'"
    ).fetchone()
    reset_pnl = round(reset_pnl_row[0] or 0.0, 4)

    unrealized_row = conn.execute(
        """SELECT SUM(
             CASE
               WHEN trades.direction='no' THEN (trades.entry_price - markets.market_price) * trades.contracts
               ELSE (markets.market_price - trades.entry_price) * trades.contracts
             END
           )
           FROM trades
           JOIN markets ON markets.ticker = trades.market_ticker
          WHERE trades.status='open'
            AND trades.paper=1
            AND markets.market_price IS NOT NULL"""
    ).fetchone()
    unrealized_pnl = round(unrealized_row[0] or 0.0, 4)

    clv_row = conn.execute(
        """SELECT AVG(clv) FROM trades
            WHERE status='closed'
              AND paper=1
              AND clv IS NOT NULL
              AND COALESCE(exit_reason, '') != 'paper_reset'"""
    ).fetchone()
    avg_clv = round((clv_row[0] or 0.0) * 100, 2)

    conn.close()

    from app.services.scanner import get_scan_status
    scan = get_scan_status()
    from app import config as cfg
    settings = cfg.load()
    paper_start = float(settings.get("paper_starting_balance", 500.0) or 500.0)
    live_balance = _cached_live_balance(settings)

    paper_equity = round(paper_start + total_pnl + unrealized_pnl, 2)
    kalshi_bal = live_balance.get("balance") if live_balance.get("connected") else None
    kalshi_port = live_balance.get("portfolio_value") if live_balance.get("connected") else None

    try:
        snap_conn = get_conn()
        last_snap = snap_conn.execute(
            "SELECT recorded_at FROM balance_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
        should_record = True
        if last_snap:
            from datetime import datetime, timezone
            last_ts = datetime.fromisoformat(last_snap["recorded_at"].replace("Z", "+00:00"))
            if (datetime.now(timezone.utc) - last_ts).total_seconds() < 300:
                should_record = False
        if should_record:
            snap_conn.execute(
                """INSERT INTO balance_snapshots (kalshi_balance, kalshi_portfolio, paper_equity, paper_pnl)
                   VALUES (?, ?, ?, ?)""",
                (kalshi_bal, kalshi_port, paper_equity, total_pnl),
            )
            snap_conn.commit()
        snap_conn.close()
    except Exception:
        pass

    return {
        "scope": "weather_only",
        "markets_open": markets_total,
        "alerts_pending": alerts_pending,
        "alerts_active": alerts_active,
        "trades_open": trades_open,
        "trades_closed": trades_closed,
        "total_pnl_paper": total_pnl,
        "learning_pnl_paper": learning_pnl,
        "reset_pnl_paper": reset_pnl,
        "unrealized_pnl_paper": unrealized_pnl,
        "total_equity_paper": paper_equity,
        "paper_starting_balance": paper_start,
        "paper_balance": paper_equity,
        "realized_paper_balance": round(paper_start + total_pnl, 2),
        "avg_clv_cents": avg_clv,
        "model_outputs": model_outputs,
        "forecast_snapshots": forecast_snapshots,
        "kalshi_key_configured": bool(settings.get("kalshi_key_id")),
        "noaa_token_configured": bool(settings.get("noaa_token")),
        "accuweather_key_configured": bool(settings.get("accuweather_api_key")),
        "kalshi_balance": live_balance,
        "last_scan": scan,
    }


@router.get("/overview/balance-history")
def balance_history():
    from app.database import get_conn
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM balance_snapshots ORDER BY id DESC LIMIT 200"
    ).fetchall()
    conn.close()
    return {"snapshots": [dict(r) for r in reversed(rows)]}


def _cached_live_balance(settings: dict) -> dict:
    now = time.time()
    with _balance_lock:
        data = _balance_cache["data"]
        fresh = data is not None and now - _balance_cache["ts"] < 60
        refreshing = _balance_cache["refreshing"]
        if fresh:
            return {**data, "cache_age_seconds": round(now - _balance_cache["ts"], 1)}
        if not refreshing:
            _balance_cache["refreshing"] = True
            threading.Thread(target=_refresh_live_balance, args=(dict(settings),), daemon=True).start()
        if data is not None:
            return {**data, "stale": True, "refreshing": True, "cache_age_seconds": round(now - _balance_cache["ts"], 1)}

    return {
        "configured": bool(settings.get("kalshi_key_id")),
        "connected": False,
        "balance": None,
        "portfolio_value": None,
        "updated_ts": None,
        "refreshing": True,
        "error": None,
    }


def _refresh_live_balance(settings: dict) -> None:
    try:
        from app.services.kalshi_client import get_balance
        data = get_balance(settings)
    except Exception as exc:
        data = {
            "configured": bool(settings.get("kalshi_key_id")),
            "connected": False,
            "balance": None,
            "portfolio_value": None,
            "updated_ts": None,
            "error": str(exc),
        }
    with _balance_lock:
        _balance_cache.update({"data": data, "ts": time.time(), "refreshing": False})
