import json
from fastapi import APIRouter, Query
from typing import Optional

router = APIRouter()


def _generate_trade_lesson(trade: dict) -> dict:
    """Generate a human-readable lesson explaining what the bot learned from a trade."""
    status = trade.get("status", "open")
    clv = trade.get("clv")
    pnl = trade.get("pnl")
    direction = str(trade.get("direction") or "yes")
    entry = float(trade.get("entry_price") or 0)
    exit_p = trade.get("exit_price")
    exit_reason = str(trade.get("exit_reason") or "")
    try:
        alert_details = json.loads(trade.get("alert_details") or "{}")
    except Exception:
        alert_details = {}
    brain = alert_details.get("brain") or {}
    learned = brain.get("learned") or {}

    if status == "open":
        unrealized = trade.get("unrealized_pnl")
        current = trade.get("current_price")
        return {
            "summary": (
                f"Trade is still open. Current side price is {round(current * 100, 1) if current is not None else 'unknown'}¢ "
                f"and unrealized P&L is {unrealized:+.2f}."
                if unrealized is not None else
                "Trade is still open — lesson pending after settlement."
            ),
            "outcome": "open",
            "clv_cents": None,
            "pnl_dollars": None,
            "application": "No policy update yet. The trade must close before it changes segment scores, sizing, or auto-entry gates.",
        }

    clv_cents = round(clv * 100, 1) if clv is not None else None
    pnl_dollars = round(pnl, 2) if pnl is not None else None
    parts = []
    outcome = "neutral"
    weather_note = _weather_note(trade, alert_details)
    if weather_note:
        parts.append(weather_note)
    application = []

    if exit_reason == "stop_loss":
        drop_pct = round(abs(entry - (exit_p or 0)) / entry * 100, 0) if entry > 0 else 0
        warnings = (brain.get("messages") or []) + (brain.get("cautions") or [])
        parts.append(
            f"Stop-loss triggered ({drop_pct:.0f}% drop from entry). "
            "This means the entry moved against the thesis before settlement."
        )
        outcome = "stopped"
        if pnl_dollars is not None:
            parts.append(f"Paper loss locked in: -${abs(pnl_dollars):.2f}.")
        if warnings:
            readable = ", ".join(str(w).replace("_", " ") for w in warnings[:3])
            parts.append(f"Entry warnings to learn from: {readable}.")
        if learned:
            stop_rate = float(learned.get("stop_loss_rate") or 0.0)
            recent = float(learned.get("recent_avg_clv") or 0.0) * 100
            parts.append(
                f"Segment context: stop-loss rate {stop_rate * 100:.0f}%, recent CLV {recent:+.1f}¢. "
                "Future sizing should stay smaller here until recent CLV improves."
            )
        application.append(
            "Applied next: lowers similar signal trust, adds stop-loss-rate pressure to sizing, and blocks auto sizing until recent CLV, good-entry rate, and paper P&L recover."
        )
    elif clv is not None:
        if clv >= 0.02:
            parts.append(
                f"Entry was {clv_cents:+.1f}¢ ahead of the final closing line. "
                "Model had genuine edge — this type of setup gets a higher score next time."
            )
            outcome = "win_clv"
            application.append(
                "Applied next: raises the segment's average CLV and can unlock larger sizing only if the recent hit rate and paper P&L also stay positive."
            )
        elif clv >= 0:
            parts.append(
                f"CLV was flat at {clv_cents:+.1f}¢. "
                "Entry was roughly in line with where the market settled — neutral for learning."
            )
            outcome = "flat_clv"
            application.append("Applied next: counts as neutral evidence, so it does not raise sizing by itself.")
        elif clv >= -0.10:
            parts.append(
                f"Entry was {abs(clv_cents):.0f}¢ behind the final closing price. "
                "Market moved slightly against the model before settlement."
            )
            outcome = "loss_clv"
            application.append("Applied next: reduces recent CLV and keeps similar signals in review mode.")
        else:
            parts.append(
                f"Entry was {abs(clv_cents):.0f}¢ behind the closing line. "
                "The crowd had significantly better information. "
                "This market segment now scores lower on similar future setups."
            )
            outcome = "bad_clv"
            application.append(
                "Applied next: pushes similar setups toward wait-only and blocks auto entry until the segment proves positive recent CLV again."
            )

        if pnl_dollars is not None:
            if pnl > 0:
                parts.append(f"Settled as a win: +${pnl_dollars:.2f}.")
            else:
                parts.append(
                    f"Settled as a loss: -${abs(pnl_dollars):.2f}. "
                    "Outcome variance is normal — CLV is what the brain tracks, not win/loss."
                )

    # Attach segment context from the linked alert
    segment_note = _segment_note(trade)
    if segment_note:
        parts.append(segment_note)

    return {
        "summary": " ".join(parts) if parts else "No lesson data available for this trade.",
        "outcome": outcome,
        "clv_cents": clv_cents,
        "pnl_dollars": pnl_dollars,
        "application": " ".join(application),
    }


def _segment_note(trade: dict) -> str:
    """Return a 1-sentence note about the segment's current state after this trade."""
    try:
        raw_details = trade.get("alert_details") or "{}"
        details = json.loads(raw_details)
        brain = details.get("brain") or {}
        learned = brain.get("learned") or {}
        seg = details.get("segment") or ""
        tc = int(learned.get("trade_count") or 0)
        avg_clv = float(learned.get("avg_clv") or 0.0)
        if tc >= 3 and seg:
            label = seg.replace("_", " ").title()
            avg_c = round(avg_clv * 100, 1)
            sign = "+" if avg_c >= 0 else ""
            return (
                f"The '{label}' segment now has {tc} trades with avg CLV {sign}{avg_c}¢."
            )
    except Exception:
        pass
    return ""


def _weather_note(trade: dict, details: dict) -> str:
    forecast = details.get("forecast") or {}
    current = details.get("current_conditions") or {}
    ticker = str(trade.get("market_ticker") or "").upper()
    direction = str(trade.get("direction") or "yes").upper()
    floor = details.get("floor_strike")
    cap = details.get("cap_strike")
    option = details.get("yes_sub_title") or details.get("no_sub_title")
    source = forecast.get("source") or current.get("source") or "weather model"

    try:
        if "RAIN" in ticker or "PRECIP" in ticker:
            precip = forecast.get("precip_pct")
            threshold = f"{floor:g} in." if isinstance(floor, (int, float)) else (option or "listed rain threshold")
            if precip is not None:
                return (
                    f"Weather read at entry: {source} showed {float(precip):.0f}% rain risk versus "
                    f"{threshold}; the bot chose {direction}."
                )
        if "LOW" in ticker:
            low = forecast.get("low")
            threshold = option or _strike_range(floor, cap, "°F")
            temp = current.get("temperature")
            if low is not None:
                current_text = f" Current observed temp was {float(temp):.0f}°F." if temp is not None else ""
                return (
                    f"Weather read at entry: {source} projected a low near {float(low):.0f}°F versus "
                    f"{threshold}; the bot chose {direction}.{current_text}"
                )
        if "HIGH" in ticker:
            high = forecast.get("high")
            threshold = option or _strike_range(floor, cap, "°F")
            temp = current.get("temperature")
            if high is not None:
                current_text = f" Current observed temp was {float(temp):.0f}°F." if temp is not None else ""
                return (
                    f"Weather read at entry: {source} projected a high near {float(high):.0f}°F versus "
                    f"{threshold}; the bot chose {direction}.{current_text}"
                )
    except Exception:
        return ""
    return ""


def _strike_range(floor, cap, unit: str) -> str:
    try:
        floor = float(floor) if floor is not None else None
    except (TypeError, ValueError):
        floor = None
    try:
        cap = float(cap) if cap is not None else None
    except (TypeError, ValueError):
        cap = None
    if floor is not None and cap is not None:
        return f"{floor:g}-{cap:g}{unit}"
    if floor is not None:
        return f"above {floor:g}{unit}"
    if cap is not None:
        return f"below {cap:g}{unit}"
    return "the listed threshold"


@router.get("/trades")
def list_trades(
    status: Optional[str] = Query(None),
    limit: int = Query(50),
    offset: int = Query(0),
    refresh: bool = Query(False),
):
    from app.database import get_conn
    if refresh and status == "open":
        try:
            from app.services.trade_lifecycle import check_live_prices
            check_live_prices(min_interval_seconds=60)
        except Exception:
            pass

    conn = get_conn()
    base_sql = """
        SELECT trades.*,
               markets.title AS market_title,
               markets.close_time,
               markets.market_price AS current_yes_price,
               markets.yes_bid,
               markets.yes_ask,
               markets.no_bid,
               markets.no_ask,
               alerts.brain_score,
               alerts.brain_state,
               alerts.details AS alert_details
          FROM trades
          LEFT JOIN markets ON markets.ticker = trades.market_ticker
          LEFT JOIN alerts ON alerts.id = trades.alert_id
    """
    if status:
        rows = conn.execute(
            base_sql + " WHERE trades.status=? ORDER BY trades.entry_time DESC LIMIT ? OFFSET ?",
            (status, limit, offset),
        ).fetchall()
    else:
        rows = conn.execute(
            base_sql + " ORDER BY trades.entry_time DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    conn.close()

    result = []
    for r in rows:
        t = dict(r)
        _attach_live_trade_marks(t)
        t["lesson"] = _generate_trade_lesson(t)
        t.pop("alert_details", None)
        result.append(t)

    agg_conn = get_conn()
    agg = agg_conn.execute(
        """SELECT COUNT(*) as total, ROUND(COALESCE(SUM(pnl),0),4) as total_pnl,
	              ROUND(AVG(clv)*100,2) as avg_clv_cents,
	              SUM(CASE WHEN prediction_correct=1 THEN 1 ELSE 0 END) as pred_correct,
	              SUM(CASE WHEN prediction_correct IS NOT NULL THEN 1 ELSE 0 END) as pred_total
	         FROM trades
	        WHERE paper=1
	          AND status IN ('closed','settled')
	          AND pnl IS NOT NULL
	          AND COALESCE(exit_reason, '') != 'paper_reset'"""
    ).fetchone()
    agg_conn.close()

    return {
        "trades": result,
        "aggregate": {
            "total_trades": agg["total"],
            "total_pnl": float(agg["total_pnl"] or 0),
            "avg_clv_cents": float(agg["avg_clv_cents"] or 0),
            "prediction_accuracy": round(int(agg["pred_correct"] or 0) / max(1, int(agg["pred_total"] or 1)) * 100, 1),
            "prediction_sample_count": int(agg["pred_total"] or 0),
        },
    }


def _attach_live_trade_marks(trade: dict) -> None:
    current_yes = trade.get("current_yes_price")
    if current_yes is None:
        return
    try:
        current_yes = float(current_yes)
        entry = float(trade.get("entry_price") or 0.0)
        contracts = int(trade.get("contracts") or 1)
    except (TypeError, ValueError):
        return
    if trade.get("direction") == "no":
        side_bid = _as_price(trade.get("no_bid"))
        side_ask = _as_price(trade.get("no_ask"))
        current_side = side_bid if side_bid is not None else 1.0 - current_yes
        try:
            current_side = float(current_side)
        except (TypeError, ValueError):
            current_side = 1.0 - current_yes
        entry_side = 1.0 - entry
    else:
        side_bid = _as_price(trade.get("yes_bid"))
        side_ask = _as_price(trade.get("yes_ask"))
        current_side = side_bid if side_bid is not None else current_yes
        try:
            current_side = float(current_side)
        except (TypeError, ValueError):
            current_side = current_yes
        entry_side = entry

    spread = None
    if side_bid is not None and side_ask is not None:
        spread = max(0.0, side_ask - side_bid)

    trade["entry_side_price"] = round(entry_side, 4)
    trade["current_price"] = round(current_side, 4)
    trade["mark_price_type"] = "exit_bid"
    trade["current_side_bid"] = round(side_bid, 4) if side_bid is not None else None
    trade["current_side_ask"] = round(side_ask, 4) if side_ask is not None else None
    trade["current_spread"] = round(spread, 4) if spread is not None else None
    trade["spread_mark_cost"] = round(spread * contracts, 4) if spread is not None else None
    trade["unrealized_pnl"] = round((current_side - entry_side) * contracts, 4)


def _as_price(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@router.post("/trades/backfill-settlements")
def backfill_settlements():
    from app.services.trade_lifecycle import backfill_settlements as _backfill
    return _backfill()


@router.post("/trades/sweep-settlements")
def sweep_settlements():
    """One-shot: settle all open trades on closed markets, backfill CLV/PnL,
    cross-reference predictions with Kalshi outcomes."""
    from app.services.trade_lifecycle import (
        settle_expired_open_trades,
        backfill_settlements as _backfill,
        backfill_settlement_cross_reference,
    )
    from app.services.adaptive_policy import rebuild_snapshots

    settle_result = settle_expired_open_trades()
    backfill_result = _backfill()
    cross_ref_result = backfill_settlement_cross_reference()
    rebuild_result = rebuild_snapshots()

    from app.database import get_conn
    conn = get_conn()
    stats = {}
    stats["open_trades"] = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE status='open' AND paper=1"
    ).fetchone()[0]
    stats["total_settled"] = conn.execute(
        """SELECT COUNT(*) FROM trades
            WHERE paper=1
              AND settlement_result IS NOT NULL
              AND COALESCE(exit_reason, '') != 'paper_reset'"""
    ).fetchone()[0]
    r = conn.execute(
        """SELECT AVG(prediction_correct), COUNT(*)
	     FROM trades
	    WHERE paper=1
	      AND prediction_correct IS NOT NULL
	      AND COALESCE(exit_reason, '') != 'paper_reset'
"""
    ).fetchone()
    stats["prediction_accuracy"] = round(float(r[0] or 0) * 100, 1) if r[0] else 0
    stats["prediction_sample_count"] = r[1]
    conn.close()

    return {
        "settle": settle_result,
        "backfill": backfill_result,
        "cross_reference": cross_ref_result,
        "segments_rebuilt": len(rebuild_result),
        "current_stats": stats,
    }


@router.post("/trades/reset-paper-trades")
def reset_paper_trades():
    """Operator reset: clear the current open paper book without deleting rows.

    Settled markets close at final Kalshi result. Still-open markets close at
    the current Kalshi quote, then settlement cross-reference is attempted for
    any market that already has a final YES/NO outcome.
    """
    from app.services.trade_lifecycle import (
        close_open_paper_trades_at_kalshi,
        backfill_settlements as _backfill,
        backfill_settlement_cross_reference,
    )
    from app.services.adaptive_policy import rebuild_snapshots

    reset_result = close_open_paper_trades_at_kalshi()
    backfill_result = _backfill()
    cross_ref_result = backfill_settlement_cross_reference()
    rebuild_result = rebuild_snapshots()

    from app.database import get_conn
    conn = get_conn()
    r = conn.execute(
        """SELECT AVG(prediction_correct), COUNT(*)
             FROM trades
	    WHERE paper=1
	      AND prediction_correct IS NOT NULL
	      AND COALESCE(exit_reason, '') != 'paper_reset'
"""
    ).fetchone()
    stats = {
        "open_trades": conn.execute(
            "SELECT COUNT(*) FROM trades WHERE status='open' AND paper=1"
        ).fetchone()[0],
        "total_closed": conn.execute(
            "SELECT COUNT(*) FROM trades WHERE status='closed' AND paper=1"
        ).fetchone()[0],
        "prediction_accuracy": round(float(r[0] or 0.0) * 100, 1) if r[1] else 0.0,
        "prediction_sample_count": r[1],
    }
    conn.close()

    return {
        "reset": reset_result,
        "backfill": backfill_result,
        "cross_reference": cross_ref_result,
        "segments_rebuilt": len(rebuild_result),
        "current_stats": stats,
    }


@router.post("/trades/{trade_id}/close")
def close_trade(trade_id: int, exit_price: float, exit_reason: str = "manual"):
    from app.services.order_manager import close_order
    result = close_order(trade_id, exit_price, exit_reason)
    return result
