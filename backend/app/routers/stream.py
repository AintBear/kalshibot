"""Server-Sent Events: the live nervous system for the UI.

GET /api/stream emits:
  - quote:     realtime Kalshi ticks for watched markets (from the WS feed bus)
  - pulse:     every ~5s — open-position P&L marked at live quotes, brain
               score, scan stage, risk/kill state, feed health
  - narration: human-readable lines of what the bot is doing/thinking —
               new audit actions, scan completions, fresh alerts with their
               drivers/blockers, trades opened/closed

One endpoint, one EventSource in the frontend. Everything else stays REST.
"""
import asyncio
import json
import logging

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)
router = APIRouter()

PULSE_INTERVAL_S = 5.0
NARRATION_POLL_S = 5.0


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _positions_pulse() -> dict:
    """Open positions marked at the freshest quote we hold (WS cache first,
    markets table fallback), plus headline risk/brain/scan state."""
    from app.database import get_conn
    from app.services.realtime import feed

    conn = get_conn()
    try:
        trades = conn.execute(
            """SELECT t.id, t.market_ticker, t.direction, t.entry_price, t.contracts, t.paper,
                      m.yes_bid, m.yes_ask
                 FROM trades t LEFT JOIN markets m ON m.ticker = t.market_ticker
                WHERE t.status='open'"""
        ).fetchall()
        scan = conn.execute("SELECT status, stage, progress FROM scan_status WHERE id=1").fetchone()
    finally:
        conn.close()

    positions = []
    total_pnl = 0.0
    for t in trades:
        ticker = t["market_ticker"]
        live = feed.quotes.get(ticker) or {}
        yes_bid = live.get("yes_bid", t["yes_bid"])
        yes_ask = live.get("yes_ask", t["yes_ask"])
        direction = (t["direction"] or "yes").lower()
        # Mark at the price the position could actually exit at.
        exit_yes = yes_ask if direction == "no" else yes_bid
        pnl = None
        if exit_yes is not None and t["entry_price"] is not None:
            per = (t["entry_price"] - exit_yes) if direction == "no" else (exit_yes - t["entry_price"])
            pnl = round(per * (t["contracts"] or 1), 4)
            total_pnl += pnl
        positions.append({
            "trade_id": t["id"], "ticker": ticker, "direction": direction,
            "entry": t["entry_price"], "contracts": t["contracts"],
            "yes_bid": yes_bid, "yes_ask": yes_ask, "pnl": pnl,
            "paper": bool(t["paper"]), "live_quote": bool(live),
        })

    try:
        from app import config as cfg
        settings = cfg.load()
        kill = bool(settings.get("kill_switch", False))
        paper = bool(settings.get("paper_trading", True))
    except Exception:
        kill, paper = True, True

    return {
        "open_positions": len(positions),
        "open_pnl": round(total_pnl, 4),
        "positions": positions,
        "scan": dict(scan) if scan else None,
        "kill_switch": kill,
        "paper_trading": paper,
        "feed": {k: feed.status.get(k) for k in
                 ("connected", "subscribed_tickers", "messages_received", "last_message_at")},
    }


_AUDIT_NARRATION = {
    "live_entry_submitted": lambda d: f"Submitted live entry: {d.get('direction', '?').upper()} {d.get('ticker', '')} @ {d.get('price')} x{d.get('contracts')}",
    "live_entry_filled": lambda d: f"Live entry filled @ {d.get('fill_price_yes')}",
    "live_entry_requoted": lambda d: f"Re-quoted working order to {d.get('side_price')} ({d.get('reason', '')})",
    "live_entry_crossed": lambda d: f"Crossed the spread at {d.get('side_price')} — {d.get('reason', '')}",
    "live_entry_abandoned": lambda d: f"Abandoned entry: {d.get('reason', '')}",
    "live_entry_blocked": lambda d: f"BLOCKED live entry: {'; '.join(d.get('violations', []))}",
    "live_exit_submitted": lambda d: f"Exit order posted ({d.get('reason', '')}) @ {d.get('price_yes')}",
    "live_exit_filled": lambda d: f"Exit filled @ {d.get('exit_price_yes')}",
    "kill_switch_activated": lambda d: f"KILL SWITCH ON — {d.get('reason', '')}",
    "kill_switch_deactivated": lambda d: "Kill switch off — trading re-armed",
    "loss_limit_revert_to_paper": lambda d: f"LOSS LIMIT BREACH — reverted to paper ({d.get('reason', '')})",
    "reconcile_mismatch": lambda d: f"Position mismatch vs Kalshi: db={d.get('db_contracts')} kalshi={d.get('kalshi_contracts')}",
}


class _NarrationState:
    def __init__(self):
        from app.database import get_conn
        conn = get_conn()
        try:
            self.last_audit_id = (conn.execute("SELECT COALESCE(MAX(id),0) i FROM audit_log").fetchone() or {"i": 0})["i"]
            self.last_alert_id = (conn.execute("SELECT COALESCE(MAX(id),0) i FROM alerts").fetchone() or {"i": 0})["i"]
            self.last_trade_id = (conn.execute("SELECT COALESCE(MAX(id),0) i FROM trades").fetchone() or {"i": 0})["i"]
            row = conn.execute("SELECT status, completed_at FROM scan_status WHERE id=1").fetchone()
            self.last_scan_completed = row["completed_at"] if row else None
        finally:
            conn.close()


def _collect_narration(state: _NarrationState) -> list:
    from app.database import get_conn

    lines = []
    conn = get_conn()
    try:
        for r in conn.execute(
            "SELECT * FROM audit_log WHERE id > ? ORDER BY id LIMIT 50", (state.last_audit_id,)
        ).fetchall():
            state.last_audit_id = max(state.last_audit_id, r["id"])
            details = {}
            if r["details"]:
                try:
                    details = json.loads(r["details"])
                except (TypeError, ValueError):
                    pass
            details.setdefault("ticker", r["market_ticker"])
            render = _AUDIT_NARRATION.get(r["action"])
            text = render(details) if render else f"{r['action']} {r['market_ticker'] or ''}"
            lines.append({"kind": "audit", "at": r["created_at"], "text": text,
                          "ticker": r["market_ticker"]})

        scan = conn.execute(
            "SELECT status, completed_at, payload FROM scan_status WHERE id=1"
        ).fetchone()
        if scan and scan["completed_at"] and scan["completed_at"] != state.last_scan_completed:
            state.last_scan_completed = scan["completed_at"]
            payload = {}
            try:
                payload = json.loads(scan["payload"] or "{}")
            except (TypeError, ValueError):
                pass
            lines.append({
                "kind": "scan", "at": scan["completed_at"],
                "text": (f"Scan complete: {payload.get('markets_processed', '?')} markets, "
                         f"{payload.get('alerts_created', '?')} alerts, "
                         f"{payload.get('series_errors', 0)} errors, "
                         f"{payload.get('paper_trades_created', 0)} paper trades"),
            })

        for r in conn.execute(
            """SELECT id, market_ticker, direction, market_price, model_prob, details, created_at
                 FROM alerts WHERE id > ? ORDER BY id DESC LIMIT 8""",
            (state.last_alert_id,),
        ).fetchall():
            state.last_alert_id = max(state.last_alert_id, r["id"])
            d = {}
            try:
                d = json.loads(r["details"] or "{}")
            except (TypeError, ValueError):
                pass
            rec = d.get("recommendation") or {}
            blockers = rec.get("blockers") or []
            drivers = rec.get("drivers") or []
            why = blockers[0] if blockers else (drivers[0] if drivers else "")
            verdict = "pass" if blockers else "candidate"
            lines.append({
                "kind": "alert", "at": r["created_at"], "ticker": r["market_ticker"],
                "text": (f"{r['market_ticker']}: model {round((r['model_prob'] or 0) * 100)}% vs "
                         f"market {round((r['market_price'] or 0) * 100)}c — {verdict}"
                         + (f" ({why})" if why else "")),
            })

        for r in conn.execute(
            """SELECT id, market_ticker, direction, entry_price, contracts, paper, entry_time
                 FROM trades WHERE id > ? ORDER BY id LIMIT 10""",
            (state.last_trade_id,),
        ).fetchall():
            state.last_trade_id = max(state.last_trade_id, r["id"])
            mode = "paper" if r["paper"] else "LIVE"
            lines.append({
                "kind": "trade", "at": r["entry_time"], "ticker": r["market_ticker"],
                "text": (f"Entered {mode}: {(r['direction'] or '?').upper()} "
                         f"{r['market_ticker']} @ {r['entry_price']} x{r['contracts']}"),
            })
    finally:
        conn.close()
    return lines


@router.get("/stream")
async def stream():
    async def event_source():
        from app.services.realtime import feed

        queue = feed.subscribe_events()
        state = await asyncio.to_thread(_NarrationState)
        try:
            pulse = await asyncio.to_thread(_positions_pulse)
            yield _sse("pulse", pulse)
            last_pulse = asyncio.get_event_loop().time()
            last_narration = last_pulse

            while True:
                now = asyncio.get_event_loop().time()
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1.0)
                    yield _sse("quote", event)
                except asyncio.TimeoutError:
                    pass

                if now - last_pulse >= PULSE_INTERVAL_S:
                    last_pulse = now
                    pulse = await asyncio.to_thread(_positions_pulse)
                    yield _sse("pulse", pulse)

                if now - last_narration >= NARRATION_POLL_S:
                    last_narration = now
                    lines = await asyncio.to_thread(_collect_narration, state)
                    for line in lines:
                        yield _sse("narration", line)
        except asyncio.CancelledError:
            pass
        finally:
            feed.unsubscribe_events(queue)

    return StreamingResponse(event_source(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })
