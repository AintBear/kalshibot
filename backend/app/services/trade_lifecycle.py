"""
Trade lifecycle: manages open trade state, stop-loss checks, and settlement.
market_resolved trigger: closes trades immediately when market status is closed/settled/finalized,
even if hours_to_expiry > 0. Fixes trades stuck open on early-resolving markets.
"""
import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)
_LIVE_PRICE_REFRESH_LOCK = threading.Lock()
_LAST_LIVE_PRICE_REFRESH = 0.0


def _get_conn():
    from app.database import get_conn
    return get_conn()


def check_and_close_trades():
    settle_expired_open_trades()
    conn = _get_conn()
    open_trades = conn.execute(
        "SELECT * FROM trades WHERE status = 'open'"
    ).fetchall()
    conn.close()

    for trade in open_trades:
        _check_trade(trade)

    backfill_settlements()


def settle_expired_open_trades() -> dict:
    """Fetch Kalshi settlement for open trades whose markets have closed."""
    conn = _get_conn()
    stale = conn.execute(
        """SELECT t.* FROM trades t
           LEFT JOIN markets m ON m.ticker = t.market_ticker
           WHERE t.status = 'open'
             AND (
               (m.close_time IS NOT NULL AND datetime(m.close_time) <= datetime('now'))
               OR m.ticker IS NULL
             )"""
    ).fetchall()
    conn.close()

    if not stale:
        return {"checked": 0, "settled": 0, "closed_unknown": 0, "errors": []}

    from app.services.kalshi_client import (
        get_market,
        settlement_exit_price_from_market,
        settlement_result_from_market,
    )
    checked = settled = closed_unknown = 0
    errors = []
    for trade in stale:
        checked += 1
        try:
            market = get_market(trade["market_ticker"])
            if not market:
                continue
            result = settlement_result_from_market(market)
            exit_price = settlement_exit_price_from_market(market)
            status = (market.get("status") or "").lower()
            if exit_price is not None:
                _close_trade(trade["id"], exit_price, "market_closed", settlement_result=result)
                settled += 1
            elif status in ("closed", "settled", "finalized"):
                _close_trade(trade["id"], market.get("last_price", trade["entry_price"]) or trade["entry_price"],
                             "market_closed", settlement_unknown=True)
                closed_unknown += 1
        except Exception as exc:
            errors.append({"trade_id": trade["id"], "ticker": trade["market_ticker"], "error": str(exc)})
            logger.warning("Settlement fetch failed for trade %d (%s): %s",
                           trade["id"], trade["market_ticker"], exc)
    return {"checked": checked, "settled": settled, "closed_unknown": closed_unknown, "errors": errors}



def check_live_prices(min_interval_seconds: int = 0) -> dict:
    """Poll Kalshi quotes for open paper trades and enforce SL/TP thresholds."""
    global _LAST_LIVE_PRICE_REFRESH
    now = time.time()
    if min_interval_seconds > 0 and now - _LAST_LIVE_PRICE_REFRESH < min_interval_seconds:
        return {
            "checked": 0,
            "closed": 0,
            "errors": [],
            "skipped": True,
            "reason": "recently refreshed",
        }
    if not _LIVE_PRICE_REFRESH_LOCK.acquire(blocking=False):
        return {
            "checked": 0,
            "closed": 0,
            "errors": [],
            "skipped": True,
            "reason": "refresh already running",
        }

    conn = _get_conn()
    try:
        trades = conn.execute(
            """SELECT * FROM trades
                WHERE status='open'
                  AND paper=1"""
        ).fetchall()
        conn.close()

        checked = closed = 0
        errors = []
        for trade in trades:
            checked += 1
            try:
                quote = _refresh_trade_quote(trade["market_ticker"])
                if not quote:
                    continue
                if quote.get("market_status") not in (None, "open", "active"):
                    continue
                yes_price = _exit_yes_price_for_trade(trade, quote)
                if yes_price is None:
                    continue
                action = _threshold_action(trade, float(yes_price))
                if not action:
                    continue
                from app.services.order_manager import close_order
                result = close_order(trade["id"], action["exit_price"], action["exit_reason"])
                if not result.get("error"):
                    closed += 1
            except Exception as exc:
                logger.warning("Live price monitor failed for trade %s: %s", trade["id"], exc)
                errors.append({"trade_id": trade["id"], "error": str(exc)})

        _LAST_LIVE_PRICE_REFRESH = time.time()
        return {"checked": checked, "closed": closed, "errors": errors}
    finally:
        try:
            conn.close()
        except Exception:
            pass
        _LIVE_PRICE_REFRESH_LOCK.release()


def check_stop_losses() -> dict:
    """Backward-compatible scheduler/test entrypoint for paper risk exits."""
    return check_live_prices()


def check_live_trade_exits() -> dict:
    """Enforce stop-loss / take-profit on REAL (paper=0) open positions.

    Unlike the paper path, hitting a threshold here submits an actual Kalshi
    sell order (stop-loss crosses for speed, take-profit rests passively).
    The trade closes only when the fill monitor confirms the sell executed.
    Inert in paper mode — there are no live open trades to check.
    """
    conn = _get_conn()
    try:
        trades = conn.execute(
            """SELECT * FROM trades
                WHERE status='open' AND paper=0
                  AND (stop_loss_price IS NOT NULL OR take_profit_price IS NOT NULL)"""
        ).fetchall()
    finally:
        conn.close()

    if not trades:
        return {"checked": 0, "exits_submitted": 0}

    from app.services.order_manager import submit_live_exit, _current_quote, _side_quote

    checked = exits = 0
    errors = []
    for trade in trades:
        checked += 1
        try:
            quote = _current_quote(trade["market_ticker"])
            if not quote:
                continue
            # Mark at the price we could actually exit at (side bid).
            direction = (trade["direction"] or "yes").lower()
            side_bid, _side_ask = _side_quote(quote, direction)
            if side_bid is None:
                continue
            yes_mark = round(1.0 - side_bid, 4) if direction == "no" else side_bid
            action = _threshold_action(trade, float(yes_mark))
            if not action:
                continue
            result = submit_live_exit(
                trade["id"],
                action["exit_reason"],
                cross=(action["exit_reason"] == "stop_loss"),
            )
            if result.get("error"):
                errors.append({"trade_id": trade["id"], "error": result["error"]})
            elif not result.get("skipped"):
                exits += 1
        except Exception as exc:
            logger.warning("Live exit check failed for trade %s: %s", trade["id"], exc)
            errors.append({"trade_id": trade["id"], "error": str(exc)})

    return {"checked": checked, "exits_submitted": exits, "errors": errors}


def _check_trade(trade):
    conn = _get_conn()
    market = conn.execute(
        "SELECT * FROM markets WHERE ticker = ?",
        (trade["market_ticker"],)
    ).fetchone()
    conn.close()

    if market is None:
        return

    market_status = (market["status"] or "").lower()

    # market_resolved trigger — close immediately on early-resolving markets
    if market_status in ("closed", "settled", "finalized"):
        exit_price = _resolve_exit_price(trade, market)
        result = (market["result"] or "").lower()
        settlement_unknown = result not in ("yes", "no")
        _close_trade(
            trade_id=trade["id"],
            exit_price=exit_price,
            exit_reason="market_resolved",
            settlement_unknown=settlement_unknown,
            settlement_result=None if settlement_unknown else result,
        )
        return

    if _market_past_close(market):
        # Use actual settlement result if available; otherwise CLV stays NULL
        result = (market["result"] or "").lower()
        if result in ("yes", "no"):
            exit_price = 1.0 if result == "yes" else 0.0
            _close_trade(trade["id"], exit_price, "market_closed", settlement_result=result)
        else:
            exit_price = market["market_price"] or trade["entry_price"]
            _close_trade(trade["id"], exit_price, "market_closed", settlement_unknown=True)
        return

    market_price = market["market_price"]
    if market_price is None:
        return

    action = _threshold_action(trade, float(market_price))
    if action:
        _close_trade(trade["id"], action["exit_price"], action["exit_reason"])
        return


def _refresh_trade_quote(ticker: str) -> Optional[dict]:
    from app.services.kalshi_client import (
        get_market,
        quote_from_market,
        settlement_exit_price_from_market,
        settlement_result_from_market,
    )

    market = get_market(ticker)
    if not market:
        return None
    quote = quote_from_market(market)
    result = settlement_result_from_market(market)
    quote["market_status"] = "open" if market.get("status") in ("open", "active") else market.get("status", "open")
    quote["settlement_result"] = result
    quote["settlement_exit_price"] = settlement_exit_price_from_market(market)
    if quote.get("market_price") is None and quote.get("settlement_exit_price") is None:
        return quote

    conn = _get_conn()
    conn.execute(
        """INSERT INTO markets
           (ticker, title, category, market_price, yes_bid, yes_ask, no_bid, no_ask,
            status, close_time, expiration_time, result, volume, open_interest, raw_json, updated_at)
           VALUES (?, ?, 'weather', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
           ON CONFLICT(ticker) DO UPDATE SET
             title=excluded.title,
             market_price=excluded.market_price,
             yes_bid=excluded.yes_bid,
             yes_ask=excluded.yes_ask,
             no_bid=excluded.no_bid,
             no_ask=excluded.no_ask,
             status=excluded.status,
             close_time=excluded.close_time,
             expiration_time=excluded.expiration_time,
             result=excluded.result,
             volume=excluded.volume,
             open_interest=excluded.open_interest,
             raw_json=excluded.raw_json,
             updated_at=excluded.updated_at""",
        (
            ticker,
            market.get("title") or market.get("subtitle") or ticker,
            quote.get("market_price"),
            quote.get("yes_bid"),
            quote.get("yes_ask"),
            quote.get("no_bid"),
            quote.get("no_ask"),
            quote["market_status"],
            market.get("close_time") or market.get("expiration_time"),
            market.get("expiration_time") or market.get("close_time"),
            result,
            int(float(quote.get("volume") or 0)),
            int(float(market.get("open_interest") or 0)),
            json.dumps(market),
        ),
    )
    conn.commit()
    conn.close()
    return quote


def close_open_paper_trades_at_kalshi(reason: str = "paper_reset") -> dict:
    """Close all open paper trades using Kalshi settlement when available,
    otherwise the current tradable quote.

    This is intentionally separate from settle_expired_open_trades(): the
    expired sweep only closes markets that are already resolved, while this
    operator reset clears an oversized paper book without deleting history.
    Prediction correctness is still populated only when Kalshi has a final
    YES/NO settlement.
    """
    conn = _get_conn()
    trades = conn.execute(
        """SELECT t.*, m.market_price AS local_market_price,
                  m.yes_bid AS local_yes_bid, m.yes_ask AS local_yes_ask,
                  m.result AS local_result
             FROM trades t
             LEFT JOIN markets m ON m.ticker = t.market_ticker
            WHERE t.status='open'
              AND t.paper=1"""
    ).fetchall()
    conn.close()

    if not trades:
        return {
            "checked": 0,
            "closed": 0,
            "settled": 0,
            "marked_to_market": 0,
            "entry_fallback": 0,
            "errors": [],
        }

    quote_cache: dict[str, Optional[dict]] = {}
    checked = closed = settled = marked_to_market = entry_fallback = 0
    errors = []
    for trade in trades:
        checked += 1
        ticker = trade["market_ticker"]
        try:
            if ticker not in quote_cache:
                quote_cache[ticker] = _refresh_trade_quote(ticker)
            quote = quote_cache.get(ticker) or {}

            exit_reason = reason
            exit_price = quote.get("settlement_exit_price")
            if exit_price is not None:
                exit_reason = "market_closed"
                settled += 1
            else:
                exit_price = _exit_yes_price_for_trade(trade, quote)
                if exit_price is not None:
                    marked_to_market += 1
                else:
                    local_quote = {
                        "market_price": trade["local_market_price"],
                        "yes_bid": trade["local_yes_bid"],
                        "yes_ask": trade["local_yes_ask"],
                    }
                    exit_price = _exit_yes_price_for_trade(trade, local_quote)
                    if exit_price is not None:
                        marked_to_market += 1
                    else:
                        exit_price = trade["entry_price"]
                        entry_fallback += 1

            _close_trade(trade["id"], float(exit_price), exit_reason, refresh_learning=False)
            closed += 1
        except Exception as exc:
            errors.append({"trade_id": trade["id"], "ticker": ticker, "error": str(exc)})
            logger.warning("Paper reset failed for trade %d (%s): %s", trade["id"], ticker, exc)

    if closed > 0:
        from app.services import adaptive_policy
        adaptive_policy.rebuild_snapshots()

    return {
        "checked": checked,
        "closed": closed,
        "settled": settled,
        "marked_to_market": marked_to_market,
        "entry_fallback": entry_fallback,
        "errors": errors,
    }


def _exit_yes_price_for_trade(trade, quote: dict) -> Optional[float]:
    direction = (trade["direction"] or "yes").lower()
    if direction == "no":
        yes_price = quote.get("yes_ask")
    else:
        yes_price = quote.get("yes_bid")
    if yes_price is None:
        yes_price = quote.get("market_price")
    if yes_price is None:
        yes_price = quote.get("yes_bid") if direction == "no" else quote.get("yes_ask")
    return yes_price


def _threshold_action(trade, yes_price: float) -> Optional[dict]:
    direction = (trade["direction"] or "yes").lower()
    stop_loss = trade["stop_loss_price"]
    take_profit = trade["take_profit_price"]

    if stop_loss is not None:
        stop_loss = float(stop_loss)
        if direction == "yes" and yes_price <= stop_loss:
            _log_stop_loss_trigger(trade, stop_loss, yes_price)
            return {"exit_price": stop_loss, "exit_reason": "stop_loss"}
        if direction == "no" and yes_price >= stop_loss:
            _log_stop_loss_trigger(trade, stop_loss, yes_price)
            return {"exit_price": stop_loss, "exit_reason": "stop_loss"}

    if take_profit is not None:
        take_profit = float(take_profit)
        if direction == "yes" and yes_price >= take_profit:
            return {"exit_price": take_profit, "exit_reason": "take_profit"}
        if direction == "no" and yes_price <= take_profit:
            return {"exit_price": take_profit, "exit_reason": "take_profit"}

    return None


def _log_stop_loss_trigger(trade, stop_price: float, current_price: float) -> None:
    try:
        from app.services import weather_model
        conn = _get_conn()
        market = conn.execute(
            "SELECT close_time FROM markets WHERE ticker=?",
            (trade["market_ticker"],),
        ).fetchone()
        conn.close()
        hours_remaining = None
        if market and market["close_time"]:
            try:
                normalized = str(market["close_time"]).replace("Z", "+00:00")
                close_dt = datetime.fromisoformat(normalized)
                if close_dt.tzinfo is None:
                    close_dt = close_dt.replace(tzinfo=timezone.utc)
                hours_remaining = max(0.0, (close_dt - datetime.now(timezone.utc)).total_seconds() / 3600.0)
            except Exception:
                hours_remaining = None
        logger.warning(
            "Stop-loss trigger trade_id=%s entry_price=%.4f stop_price=%.4f current_price=%.4f "
            "hours_remaining=%s city=%s market_type=%s",
            trade["id"],
            float(trade["entry_price"] or 0.0),
            float(stop_price),
            float(current_price),
            "%.2f" % hours_remaining if hours_remaining is not None else "unknown",
            weather_model._city_code_from_ticker(trade["market_ticker"]),
            weather_model._segment_from_ticker(trade["market_ticker"]),
        )
    except Exception as exc:
        logger.warning("Stop-loss trigger logging failed for trade %s: %s", trade["id"], exc)


def _resolve_exit_price(trade, market) -> float:
    result = (market["result"] or "").lower()
    if result == "yes":
        return 1.0
    if result == "no":
        return 0.0
    return market["market_price"] or trade["entry_price"]


def _market_past_close(market) -> bool:
    close_time = market["close_time"]
    if not close_time:
        return False
    try:
        normalized = str(close_time).replace("Z", "+00:00")
        return datetime.fromisoformat(normalized) <= datetime.now(timezone.utc)
    except Exception:
        return False


def _close_trade(
    trade_id: int,
    exit_price: float,
    exit_reason: str,
    settlement_unknown: bool = False,
    settlement_result: Optional[str] = None,
    refresh_learning: bool = True,
):
    conn = _get_conn()
    trade = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    if trade is None:
        conn.close()
        return

    entry_price = trade["entry_price"]
    direction = trade["direction"]

    # When market closed before settlement data is available, CLV and PnL are unknown.
    # Leaving them NULL keeps them out of avg_clv calculations until backfilled.
    if settlement_unknown:
        clv_at_close = None
        pnl = None
    else:
        clv_at_close = _directional_clv(direction, entry_price, exit_price)
        if direction == "yes":
            pnl = round((exit_price - entry_price) * trade["contracts"], 4)
        else:
            pnl = round((entry_price - exit_price) * trade["contracts"], 4)

    normalized_result = str(settlement_result or "").strip().lower()
    if settlement_unknown or normalized_result not in ("yes", "no"):
        normalized_result = None
        prediction_correct = None
        settlement_pnl = None
    else:
        prediction_correct = 1 if (
            (direction == "yes" and normalized_result == "yes")
            or (direction == "no" and normalized_result == "no")
        ) else 0
        settlement_pnl = pnl

    close_mark_yes, computed_true_clv = _true_clv_fields(conn, trade)

    conn.execute(
        """UPDATE trades SET
             status='closed',
             exit_price=?,
             exit_reason=?,
             clv=?,
             pnl=?,
             settlement_result=COALESCE(?, settlement_result),
             prediction_correct=COALESCE(?, prediction_correct),
             settlement_pnl=COALESCE(?, settlement_pnl),
             close_mark_yes=COALESCE(?, close_mark_yes),
             true_clv=COALESCE(?, true_clv),
             exit_time=datetime('now')
           WHERE id=?""",
        (
            exit_price,
            exit_reason,
            clv_at_close,
            pnl,
            normalized_result,
            prediction_correct,
            settlement_pnl,
            close_mark_yes,
            computed_true_clv,
            trade_id,
        ),
    )
    conn.commit()
    conn.close()

    logger.info("Trade %d closed via %s at %.4f (CLV: %s)", trade_id, exit_reason, exit_price,
                f"{clv_at_close:.4f}" if clv_at_close is not None else "pending settlement")

    if refresh_learning:
        from app.services import adaptive_policy
        adaptive_policy.rebuild_snapshots()


def _directional_clv(direction: str, entry_price: float, exit_price: float) -> float:
    if direction == "no":
        return round(entry_price - exit_price, 4)
    return round(exit_price - entry_price, 4)


def _true_clv_fields(conn, trade) -> tuple:
    """(close_mark_yes, true_clv) from the realtime price-path store.

    Returns (None, None) when no pre-close snapshot exists — true CLV is
    never fabricated from settlement values.
    """
    try:
        from app.services.realtime import close_mark_for, true_clv

        market = conn.execute(
            "SELECT close_time FROM markets WHERE ticker = ?",
            (trade["market_ticker"],),
        ).fetchone()
        close_time = market["close_time"] if market else None
        mark = close_mark_for(trade["market_ticker"], close_time)
        if mark is None or trade["entry_price"] is None:
            return None, None
        return mark["yes_mid"], true_clv(trade["direction"], float(trade["entry_price"]), float(mark["yes_mid"]))
    except Exception as exc:
        logger.debug("true CLV unavailable for trade %s: %s", trade["id"], exc)
        return None, None


def backfill_settlements() -> dict:
    """Fetch Kalshi settlement results for closed trades that have NULL pnl."""
    conn = _get_conn()
    pending = conn.execute(
        """SELECT * FROM trades
            WHERE status='closed'
              AND pnl IS NULL
              AND COALESCE(exit_reason, '') != 'paper_reset'"""
    ).fetchall()
    conn.close()

    from app.services.kalshi_client import get_market

    backfilled = 0
    skipped = 0
    errors = []
    for trade in pending:
        try:
            market = get_market(trade["market_ticker"])
            if not market:
                skipped += 1
                continue
            result = (market.get("result") or "").lower()
            if result not in ("yes", "no"):
                skipped += 1
                continue
            exit_price = 1.0 if result == "yes" else 0.0
            entry_price = float(trade["entry_price"])
            direction = (trade["direction"] or "yes").lower()
            contracts = int(trade["contracts"] or 1)
            clv = _directional_clv(direction, entry_price, exit_price)
            if direction == "yes":
                pnl = round((exit_price - entry_price) * contracts, 4)
            else:
                pnl = round((entry_price - exit_price) * contracts, 4)

            prediction_correct = 1 if (
                (direction == "yes" and result == "yes")
                or (direction == "no" and result == "no")
            ) else 0
            settlement_pnl = pnl

            conn2 = _get_conn()
            close_mark_yes, computed_true_clv = _true_clv_fields(conn2, trade)
            conn2.execute(
                """UPDATE trades SET exit_price=?, pnl=?, clv=?,
                     exit_reason=COALESCE(exit_reason, 'market_closed'),
                     settlement_result=?, prediction_correct=?, settlement_pnl=?,
                     close_mark_yes=COALESCE(?, close_mark_yes),
                     true_clv=COALESCE(?, true_clv)
                   WHERE id=?""",
                (exit_price, pnl, clv, result, prediction_correct, settlement_pnl,
                 close_mark_yes, computed_true_clv, trade["id"]),
            )
            conn2.commit()
            conn2.close()
            backfilled += 1
            logger.info("Backfilled trade %d (%s): result=%s pnl=%.4f clv=%.4f correct=%s",
                        trade["id"], trade["market_ticker"], result, pnl, clv, prediction_correct)
        except Exception as exc:
            errors.append({"trade_id": trade["id"], "error": str(exc)})
            logger.warning("Backfill failed for trade %d: %s", trade["id"], exc)

    cross_ref_result = backfill_settlement_cross_reference()

    if backfilled > 0 or int(cross_ref_result.get("cross_referenced") or 0) > 0:
        from app.services import adaptive_policy
        adaptive_policy.rebuild_snapshots()

    return {
        "backfilled": backfilled,
        "skipped": skipped,
        "errors": errors,
        "cross_reference": cross_ref_result,
    }


def backfill_settlement_cross_reference() -> dict:
    """For ALL closed trades (including SL/TP exits), check if market has settled
    and record whether the bot's prediction was correct. This is the critical
    feedback loop — without it, the bot never learns if its directional calls
    are right, only whether the trade P&L was positive."""
    conn = _get_conn()
    pending = conn.execute(
        """SELECT t.id, t.market_ticker, t.direction, t.entry_price, t.contracts,
                  t.exit_reason, t.pnl
             FROM trades t
             LEFT JOIN markets m ON m.ticker = t.market_ticker
            WHERE t.status IN ('closed', 'settled')
              AND t.settlement_result IS NULL
              AND COALESCE(t.exit_reason, '') != 'paper_reset'
              AND (
                    lower(coalesce(m.result, '')) IN ('yes', 'no')
                 OR (m.close_time IS NOT NULL AND datetime(m.close_time) <= datetime('now'))
                 OR m.ticker IS NULL
              )"""
    ).fetchall()
    conn.close()

    if not pending:
        return {"cross_referenced": 0, "skipped": 0}

    from app.services.kalshi_client import get_market

    cross_referenced = 0
    skipped = 0
    for trade in pending:
        try:
            market = get_market(trade["market_ticker"])
            if not market:
                skipped += 1
                continue
            result = (market.get("result") or "").lower()
            if result not in ("yes", "no"):
                skipped += 1
                continue

            direction = (trade["direction"] or "yes").lower()
            entry_price = float(trade["entry_price"])
            contracts = int(trade["contracts"] or 1)

            prediction_correct = 1 if (
                (direction == "yes" and result == "yes")
                or (direction == "no" and result == "no")
            ) else 0

            settlement_exit = 1.0 if result == "yes" else 0.0
            if direction == "yes":
                settlement_pnl = round((settlement_exit - entry_price) * contracts, 4)
            else:
                settlement_pnl = round((entry_price - settlement_exit) * contracts, 4)

            conn2 = _get_conn()
            conn2.execute(
                """UPDATE trades SET settlement_result=?, prediction_correct=?,
                     settlement_pnl=?
                   WHERE id=?""",
                (result, prediction_correct, settlement_pnl, trade["id"]),
            )
            conn2.commit()
            conn2.close()
            cross_referenced += 1
        except Exception as exc:
            logger.warning("Settlement cross-ref failed for trade %d: %s", trade["id"], exc)
            skipped += 1

    if cross_referenced > 0:
        logger.info("Cross-referenced %d trades with Kalshi settlement outcomes", cross_referenced)
    return {"cross_referenced": cross_referenced, "skipped": skipped}



def open_paper_trade(
    market_ticker: str,
    direction: str,
    entry_price: float,
    alert_id: Optional[int] = None,
    contracts: int = 1,
    stop_loss_price: Optional[float] = None,
    take_profit_price: Optional[float] = None,
) -> int:
    conn = _get_conn()
    cur = conn.execute(
        """INSERT INTO trades
           (market_ticker, alert_id, direction, entry_price, contracts,
            stop_loss_price, take_profit_price, paper, status, entry_time)
           VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'open', datetime('now'))""",
        (market_ticker, alert_id, direction, entry_price, contracts, stop_loss_price, take_profit_price),
    )
    trade_id = cur.lastrowid
    conn.commit()
    conn.close()
    logger.info("Opened paper trade %d for %s %s @ %.4f", trade_id, market_ticker, direction, entry_price)
    return trade_id
