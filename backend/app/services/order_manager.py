"""
Order manager: paper and live order execution with CLV tracking.
clv_at_close = round(exit_price - entry_price, 4) stored in BOTH paper and live close paths.
"""
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _get_conn():
    from app.database import get_conn
    return get_conn()


def _get_settings():
    from app import config as cfg
    return cfg.load()


def place_order(
    market_ticker: str,
    direction: str,
    entry_price: float,
    alert_id: Optional[int] = None,
    contracts: int = 1,
    stop_loss_pct: Optional[float] = None,
    take_profit_pct: Optional[float] = None,
    stop_loss_price: Optional[float] = None,
    take_profit_price: Optional[float] = None,
    fill_context: Optional[dict] = None,
) -> dict:
    settings = _get_settings()
    is_paper = settings.get("paper_trading", True)

    from app.services.risk import kill_switch_active
    if kill_switch_active(settings):
        raise RuntimeError("kill switch is active: no new entries (paper or live)")

    # Freeze the entry-time fill context (recommendation dict works directly).
    # Alert recommendations get recomputed every scan, so this is the only
    # durable record of what the trade actually saw at entry.
    fill_context = fill_context or {}
    fill_model = fill_context.get("fill_model")
    entry_side_bid = fill_context.get("side_bid")
    entry_side_ask = fill_context.get("side_ask")

    if stop_loss_price is not None:
        stop_loss_price = round(float(stop_loss_price), 4)
    elif stop_loss_pct is not None:
        if direction == "no":
            stop_loss_price = round(entry_price + ((1 - entry_price) * stop_loss_pct), 4)
        else:
            stop_loss_price = round(entry_price * (1 - stop_loss_pct), 4)

    if take_profit_price is not None:
        take_profit_price = round(float(take_profit_price), 4)
    elif take_profit_pct is not None:
        if direction == "no":
            take_profit_price = round(entry_price - ((1 - entry_price) * take_profit_pct), 4)
        else:
            take_profit_price = round(entry_price * (1 + take_profit_pct), 4)
        take_profit_price = min(max(0.01, take_profit_price), 0.99)

    if is_paper:
        return _paper_place(market_ticker, direction, entry_price, alert_id, contracts,
                            stop_loss_price, take_profit_price,
                            fill_model, entry_side_bid, entry_side_ask)
    return _live_place(market_ticker, direction, entry_price, alert_id, contracts,
                       stop_loss_price, take_profit_price,
                       fill_model, entry_side_bid, entry_side_ask)


def recommendation_exit_args(
    recommendation: dict,
    stop_loss_pct: Optional[float] = None,
    take_profit_pct: Optional[float] = None,
    allow_default_pct: bool = False,
) -> dict:
    """Build place_order exit args without overriding deliberate no-exit recs."""
    stop_loss_price = recommendation.get("stop_loss_price")
    take_profit_price = recommendation.get("take_profit_price")
    return {
        "stop_loss_price": stop_loss_price,
        "take_profit_price": take_profit_price,
        "stop_loss_pct": stop_loss_pct if (allow_default_pct or stop_loss_price is not None) else None,
        "take_profit_pct": take_profit_pct if (allow_default_pct or take_profit_price is not None) else None,
    }


def _paper_place(
    market_ticker: str,
    direction: str,
    entry_price: float,
    alert_id: Optional[int],
    contracts: int,
    stop_loss_price: Optional[float],
    take_profit_price: Optional[float] = None,
    fill_model: Optional[str] = None,
    entry_side_bid: Optional[float] = None,
    entry_side_ask: Optional[float] = None,
) -> dict:
    from app.services.trade_lifecycle import open_paper_trade
    trade_id = open_paper_trade(
        market_ticker=market_ticker,
        direction=direction,
        entry_price=entry_price,
        alert_id=alert_id,
        contracts=contracts,
        stop_loss_price=stop_loss_price,
        take_profit_price=take_profit_price,
        fill_model=fill_model,
        entry_side_bid=entry_side_bid,
        entry_side_ask=entry_side_ask,
    )
    conn = _get_conn()
    cur = conn.execute(
        """INSERT INTO orders
           (trade_id, market_ticker, side, price, contracts, status, order_type, paper, created_at)
           VALUES (?, ?, ?, ?, ?, 'filled', 'limit', 1, datetime('now'))""",
        (trade_id, market_ticker, direction, entry_price, contracts),
    )
    order_id = cur.lastrowid
    conn.commit()
    conn.close()
    return {"trade_id": trade_id, "order_id": order_id, "paper": True, "status": "filled"}


def _live_place(
    market_ticker: str,
    direction: str,
    entry_price: float,
    alert_id: Optional[int],
    contracts: int,
    stop_loss_price: Optional[float],
    take_profit_price: Optional[float] = None,
    fill_model: Optional[str] = None,
    entry_side_bid: Optional[float] = None,
    entry_side_ask: Optional[float] = None,
) -> dict:
    # Risk gauntlet: every live order passes pre-trade checks or dies here.
    from app.services.risk import pre_trade_checks
    from app.services.audit import audit as _audit

    violations = pre_trade_checks(market_ticker, direction, entry_price, contracts)
    if violations:
        _audit("live_entry_blocked", ticker=market_ticker, violations=violations,
               price=entry_price, contracts=contracts, direction=direction)
        raise RuntimeError(f"pre-trade checks failed: {'; '.join(violations)}")

    logger.info("LIVE order: %s %s @ %.4f x%d", direction, market_ticker, entry_price, contracts)
    conn = _get_conn()
    trade_cur = conn.execute(
        """INSERT INTO trades
           (market_ticker, alert_id, direction, entry_price, contracts,
            stop_loss_price, take_profit_price, fill_model, entry_side_bid,
            entry_side_ask, paper, status, entry_time)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'open', datetime('now'))""",
        (market_ticker, alert_id, direction, entry_price, contracts, stop_loss_price,
         take_profit_price, fill_model, entry_side_bid, entry_side_ask),
    )
    trade_id = trade_cur.lastrowid
    conn.commit()
    conn.close()

    client_order_id = make_client_order_id(trade_id, "entry", 0)
    try:
        kalshi_order_id = _submit_to_kalshi(
            market_ticker, direction, entry_price, contracts, client_order_id=client_order_id
        )
    except Exception:
        # Trade row exists without a Kalshi order: mark it cancelled so the
        # book never carries a phantom live position.
        conn = _get_conn()
        conn.execute(
            "UPDATE trades SET status='cancelled', exit_time=datetime('now') WHERE id=?",
            (trade_id,),
        )
        conn.commit()
        conn.close()
        from app.services.audit import audit
        audit("live_entry_submit_failed", ticker=market_ticker, trade_id=trade_id,
              client_order_id=client_order_id)
        raise

    conn = _get_conn()
    order_cur = conn.execute(
        """INSERT INTO orders
           (trade_id, market_ticker, side, price, contracts, status, order_type,
            kalshi_order_id, client_order_id, purpose, paper, created_at)
           VALUES (?, ?, ?, ?, ?, 'submitted', 'limit', ?, ?, 'entry', 0, datetime('now'))""",
        (trade_id, market_ticker, direction, entry_price, contracts, kalshi_order_id, client_order_id),
    )
    order_id = order_cur.lastrowid
    conn.commit()
    conn.close()
    from app.services.audit import audit
    audit("live_entry_submitted", ticker=market_ticker, trade_id=trade_id, order_id=order_id,
          kalshi_order_id=kalshi_order_id, price=entry_price, contracts=contracts,
          direction=direction, client_order_id=client_order_id)
    return {"trade_id": trade_id, "order_id": order_id, "paper": False, "kalshi_order_id": kalshi_order_id}


def close_order(trade_id: int, exit_price: float, exit_reason: str = "manual"):
    conn = _get_conn()
    trade = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    if trade is None:
        conn.close()
        return {"error": "trade not found"}

    is_paper = bool(trade["paper"])
    if is_paper:
        return _paper_close(trade, exit_price, exit_reason, conn)
    return _live_close(trade, exit_price, exit_reason, conn)


def _paper_close(trade, exit_price: float, exit_reason: str, conn) -> dict:
    entry_price = trade["entry_price"]
    direction = trade["direction"]
    clv_at_close = _directional_clv(direction, entry_price, exit_price)

    if direction == "yes":
        pnl = round((exit_price - entry_price) * trade["contracts"], 4)
    else:
        pnl = round((entry_price - exit_price) * trade["contracts"], 4)

    conn.execute(
        """UPDATE trades SET
             status='closed', exit_price=?, exit_reason=?,
             clv=?, pnl=?, exit_time=datetime('now')
           WHERE id=?""",
        (exit_price, exit_reason, clv_at_close, pnl, trade["id"]),
    )
    conn.execute(
        """UPDATE orders SET status='closed', updated_at=datetime('now')
           WHERE trade_id=? AND status IN ('pending', 'submitted', 'pending_fill')""",
        (trade["id"],),
    )
    conn.commit()
    conn.close()

    from app.services import adaptive_policy
    adaptive_policy.rebuild_snapshots()

    return {"trade_id": trade["id"], "clv": clv_at_close, "pnl": pnl, "paper": True}


def _live_close(trade, exit_price: float, exit_reason: str, conn) -> dict:
    entry_price = trade["entry_price"]
    direction = trade["direction"]
    clv_at_close = _directional_clv(direction, entry_price, exit_price)

    if direction == "yes":
        pnl = round((exit_price - entry_price) * trade["contracts"], 4)
    else:
        pnl = round((entry_price - exit_price) * trade["contracts"], 4)

    conn.execute(
        """UPDATE trades SET
             status='closed', exit_price=?, exit_reason=?,
             clv=?, pnl=?, exit_time=datetime('now')
           WHERE id=?""",
        (exit_price, exit_reason, clv_at_close, pnl, trade["id"]),
    )
    conn.execute(
        """UPDATE orders SET status='closed', updated_at=datetime('now')
           WHERE trade_id=? AND status IN ('pending', 'submitted', 'pending_fill')""",
        (trade["id"],),
    )
    conn.commit()
    conn.close()

    from app.services import adaptive_policy
    adaptive_policy.rebuild_snapshots()

    return {"trade_id": trade["id"], "clv": clv_at_close, "pnl": pnl, "paper": False}


def make_client_order_id(trade_id: int, purpose: str, seq: int) -> str:
    """Deterministic idempotency key: a crashed-and-restarted submit reuses the
    same id, and Kalshi rejects the duplicate instead of double-filling."""
    return f"sib-{int(trade_id)}-{purpose}-{int(seq)}"


def _submit_to_kalshi(
    ticker: str,
    direction: str,
    price: float,
    contracts: int,
    client_order_id: Optional[str] = None,
    action: str = "buy",
) -> Optional[str]:
    import json as _json
    import uuid as _uuid
    from app.services.kalshi_client import kalshi_request

    settings = _get_settings()
    if not settings.get("kalshi_key_id") or not settings.get("kalshi_private_key_path"):
        raise RuntimeError("Kalshi credentials not configured")

    yes_price = max(0.01, min(0.99, float(price)))
    side = "no" if direction == "no" else "yes"

    body = {
        "ticker": ticker,
        "action": action,
        "type": "limit",
        "side": side,
        "count": contracts,
        "client_order_id": client_order_id or str(_uuid.uuid4()),
    }
    if side == "no":
        body["no_price"] = max(1, min(99, int(round((1.0 - yes_price) * 100))))
    else:
        body["yes_price"] = max(1, min(99, int(round(yes_price * 100))))
    body_str = _json.dumps(body, separators=(",", ":"))
    path = "/portfolio/orders"
    r = kalshi_request(
        "POST",
        path,
        settings=settings,
        signed=True,
        headers={"Content-Type": "application/json"},
        data=body_str,
        timeout=15,
    )
    if not r.ok:
        logger.error("Kalshi order failed %s %s %s: HTTP %d %s",
                     direction, ticker, price, r.status_code, r.text[:200])
        raise RuntimeError(f"Kalshi order HTTP {r.status_code}: {r.text[:120]}")
    order_id = (r.json().get("order") or {}).get("order_id")
    if not order_id:
        logger.error("Kalshi order accepted but no order_id returned: %s", r.text[:200])
        raise RuntimeError("Kalshi order response missing order_id")
    return order_id


def monitor_live_orders() -> dict:
    """Poll Kalshi for fill status on any submitted live orders. Call from scheduler."""
    from app.services.kalshi_client import kalshi_request

    settings = _get_settings()
    if not settings.get("kalshi_key_id") or not settings.get("kalshi_private_key_path"):
        return {"skipped": True, "reason": "credentials not configured"}

    conn = _get_conn()
    pending = conn.execute(
        """SELECT orders.id AS order_id, orders.kalshi_order_id, orders.trade_id,
                  orders.purpose, orders.exit_reason,
                  trades.entry_price, trades.direction, trades.contracts
             FROM orders
             JOIN trades ON trades.id = orders.trade_id
            WHERE orders.paper = 0
              AND orders.status IN ('submitted', 'pending_fill')
              AND orders.kalshi_order_id IS NOT NULL"""
    ).fetchall()
    conn.close()

    if not pending:
        return {"checked": 0}

    from app.services.audit import audit

    checked = filled = 0
    for row in pending:
        checked += 1
        kid = row["kalshi_order_id"]
        path = f"/portfolio/orders/{kid}"
        try:
            r = kalshi_request("GET", path, settings=settings, signed=True, timeout=10)
            if not r.ok:
                continue
            order = r.json().get("order", {})
            status = (order.get("status") or "").lower()
            filled_count = _filled_count(order)
            avg_price = _average_fill_price(order, row["direction"])
            purpose = (row["purpose"] or "entry").lower()

            if status in ("filled", "partially_filled", "executed") and filled_count > 0:
                new_status = "filled" if status == "filled" else "partial"
                conn2 = _get_conn()
                conn2.execute(
                    "UPDATE orders SET status=?, updated_at=datetime('now') WHERE id=?",
                    (new_status, row["order_id"]),
                )
                if purpose == "exit":
                    conn2.commit()
                    conn2.close()
                    # The position was actually sold on Kalshi — now close the
                    # trade at the real fill price.
                    exit_yes = avg_price if avg_price is not None else row["entry_price"]
                    close_order(row["trade_id"], float(exit_yes),
                                row["exit_reason"] or "live_exit")
                    audit("live_exit_filled", ticker=None, trade_id=row["trade_id"],
                          order_id=row["order_id"], kalshi_order_id=kid,
                          exit_price_yes=exit_yes, filled=filled_count)
                else:
                    if avg_price:
                        conn2.execute(
                            "UPDATE trades SET entry_price=? WHERE id=? AND paper=0",
                            (avg_price, row["trade_id"]),
                        )
                    conn2.commit()
                    conn2.close()
                    audit("live_entry_filled", trade_id=row["trade_id"],
                          order_id=row["order_id"], kalshi_order_id=kid,
                          fill_price_yes=avg_price, filled=filled_count)
                filled += 1
            elif status in ("cancelled", "expired", "rejected"):
                conn2 = _get_conn()
                conn2.execute(
                    "UPDATE orders SET status=?, updated_at=datetime('now') WHERE id=?",
                    (status, row["order_id"]),
                )
                if purpose == "entry":
                    # Unfilled entry died: no position, cancel the trade shell.
                    conn2.execute(
                        """UPDATE trades SET status='cancelled', exit_time=datetime('now')
                            WHERE id=? AND paper=0 AND status='open'""",
                        (row["trade_id"],),
                    )
                conn2.commit()
                conn2.close()
                audit("live_order_dead", trade_id=row["trade_id"], order_id=row["order_id"],
                      kalshi_order_id=kid, kalshi_status=status, purpose=purpose)
        except Exception as exc:
            logger.warning("Order monitor error for %s: %s", kid, exc)

    return {"checked": checked, "filled": filled}


def _filled_count(order: dict) -> int:
    for key in ("count_filled", "fill_count", "fill_count_fp"):
        value = order.get(key)
        if value in (None, ""):
            continue
        try:
            return int(float(value))
        except (TypeError, ValueError):
            continue
    return 0


def _average_fill_price(order: dict, direction: str) -> Optional[float]:
    keys = ("avg_price", "average_fill_price", "yes_price", "yes_price_dollars")
    if (direction or "yes").lower() == "no":
        keys = ("avg_price", "average_fill_price", "no_price", "no_price_dollars")
    for key in keys:
        value = order.get(key)
        if value in (None, ""):
            continue
        try:
            price = float(value)
        except (TypeError, ValueError):
            continue
        if price > 1:
            price = price / 100.0
        if (direction or "yes").lower() == "no":
            return round(1.0 - price, 4)
        return round(price, 4)
    return None


def _directional_clv(direction: str, entry_price: float, exit_price: float) -> float:
    """
    exit_price is the YES-side market/settlement price.
    For NO entries, favorable movement is a lower YES price.
    """
    if direction == "no":
        return round(entry_price - exit_price, 4)
    return round(exit_price - entry_price, 4)


# --------------------------------------------------------------------------
# Desk-grade execution: work the bid, cancel/re-post on quote moves, cross
# near expiry, exit live positions with real sell orders, reconcile vs Kalshi.
# All of this is inert while paper_trading=true (no live orders exist).
# --------------------------------------------------------------------------

def cancel_kalshi_order(kalshi_order_id: str, settings: Optional[dict] = None) -> bool:
    from app.services.kalshi_client import kalshi_request

    settings = settings or _get_settings()
    r = kalshi_request("DELETE", f"/portfolio/orders/{kalshi_order_id}",
                       settings=settings, signed=True, timeout=15)
    if r.status_code == 404:
        # Already gone (filled or cancelled server-side) — treat as success;
        # the fill monitor reconciles the truth.
        return True
    return bool(r.ok)


def _side_quote(quote: dict, direction: str) -> tuple:
    """(side_bid, side_ask) in side coords from a YES-coord quote."""
    yes_bid = quote.get("yes_bid")
    yes_ask = quote.get("yes_ask")
    if direction == "no":
        side_bid = round(1.0 - yes_ask, 4) if yes_ask is not None else None
        side_ask = round(1.0 - yes_bid, 4) if yes_bid is not None else None
        return side_bid, side_ask
    return yes_bid, yes_ask


def _current_quote(ticker: str) -> Optional[dict]:
    """Realtime feed cache first (sub-second fresh), REST refresh fallback."""
    try:
        from app.services.realtime import feed
        q = feed.quotes.get(ticker)
        if q and q.get("yes_bid") is not None and q.get("yes_ask") is not None:
            return q
    except Exception:
        pass
    try:
        from app.services.kalshi_client import refresh_market_in_db
        return refresh_market_in_db(ticker)
    except Exception:
        return None


def _minutes_to_close_from_markets(ticker: str) -> Optional[float]:
    from datetime import datetime, timezone

    conn = _get_conn()
    try:
        row = conn.execute("SELECT close_time FROM markets WHERE ticker=?", (ticker,)).fetchone()
    finally:
        conn.close()
    if not row or not row["close_time"]:
        return None
    try:
        normalized = str(row["close_time"]).replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (dt - datetime.now(timezone.utc)).total_seconds() / 60.0
    except Exception:
        return None


def requote_decision(
    resting_side_price: float,
    side_bid: Optional[float],
    side_ask: Optional[float],
    minutes_to_close: Optional[float],
    requote_count: int,
    max_chase_side_price: float,
    cross_minutes: float,
    max_requotes: int,
) -> dict:
    """Pure re-quote decision for a working passive entry order.

    Returns {"action": "hold" | "requote" | "cross" | "abandon", "price": side_price}.
    - cross: market closes soon — take the ask before the order dies unfilled
      (only if the ask is within the chase cap; otherwise abandon).
    - requote: we've been outbid — re-post at bid+1c, never above the chase
      cap and never crossing the ask.
    - abandon: re-quote budget exhausted or the market ran away beyond the cap.
    """
    if side_bid is None or side_ask is None:
        return {"action": "hold", "reason": "no quote"}

    if minutes_to_close is not None and minutes_to_close <= cross_minutes:
        if side_ask <= max_chase_side_price:
            return {"action": "cross", "price": side_ask, "reason": f"{minutes_to_close:.0f}m to close"}
        return {"action": "abandon", "reason": "cross price beyond chase cap near close"}

    if resting_side_price >= side_bid:
        return {"action": "hold", "reason": "still at or above bid"}

    if requote_count >= max_requotes:
        return {"action": "abandon", "reason": f"requote budget exhausted ({requote_count})"}

    candidate = round(min(side_bid + 0.01, side_ask - 0.01, max_chase_side_price), 4)
    if candidate <= resting_side_price:
        return {"action": "hold", "reason": "chase cap reached"}
    return {"action": "requote", "price": candidate, "reason": "outbid"}


def manage_working_orders() -> dict:
    """Work all live entry orders: re-post when outbid, cross near close.

    Driven by the order-monitor scheduler job. No-ops in paper mode.
    """
    settings = _get_settings()
    if settings.get("paper_trading", True):
        return {"skipped": True, "reason": "paper mode"}
    if not settings.get("live_requote_enabled", True):
        return {"skipped": True, "reason": "live_requote_enabled=false"}
    from app.services.risk import kill_switch_active, cancel_all_working_live_entries
    if kill_switch_active(settings):
        cancelled = cancel_all_working_live_entries(reason="kill switch active")
        return {"skipped": True, "reason": "kill switch active", "cancelled": cancelled}

    from app.services.audit import audit

    max_chase_cents = float(settings.get("live_max_chase_cents", 3) or 3)
    cross_minutes = float(settings.get("live_cross_minutes_to_close", 45) or 45)
    max_requotes = int(settings.get("live_max_requotes_per_order", 10) or 10)

    conn = _get_conn()
    working = conn.execute(
        """SELECT orders.*, trades.direction AS trade_direction
             FROM orders JOIN trades ON trades.id = orders.trade_id
            WHERE orders.paper = 0
              AND orders.purpose = 'entry'
              AND orders.status IN ('submitted', 'pending_fill')
              AND orders.kalshi_order_id IS NOT NULL"""
    ).fetchall()
    conn.close()

    if not working:
        return {"checked": 0}

    results = {"checked": 0, "requoted": 0, "crossed": 0, "abandoned": 0, "held": 0}
    for order in working:
        results["checked"] += 1
        ticker = order["market_ticker"]
        direction = order["trade_direction"] or order["side"]
        quote = _current_quote(ticker)
        if not quote:
            results["held"] += 1
            continue
        side_bid, side_ask = _side_quote(quote, direction)

        # Order price is stored in YES coords; convert to side coords.
        resting_yes = float(order["price"])
        resting_side = round(1.0 - resting_yes, 4) if direction == "no" else resting_yes
        max_chase = round(resting_side + max_chase_cents / 100.0, 4)

        decision = requote_decision(
            resting_side_price=resting_side,
            side_bid=side_bid,
            side_ask=side_ask,
            minutes_to_close=_minutes_to_close_from_markets(ticker),
            requote_count=int(order["requote_count"] or 0),
            max_chase_side_price=max_chase,
            cross_minutes=cross_minutes,
            max_requotes=max_requotes,
        )

        if decision["action"] == "hold":
            results["held"] += 1
            continue

        try:
            cancelled = cancel_kalshi_order(order["kalshi_order_id"], settings)
        except Exception as exc:
            logger.warning("Cancel failed for %s: %s", order["kalshi_order_id"], exc)
            audit("live_cancel_failed", ticker=ticker, trade_id=order["trade_id"],
                  order_id=order["id"], kalshi_order_id=order["kalshi_order_id"], error=str(exc))
            continue
        if not cancelled:
            audit("live_cancel_rejected", ticker=ticker, trade_id=order["trade_id"],
                  order_id=order["id"], kalshi_order_id=order["kalshi_order_id"])
            continue

        if decision["action"] == "abandon":
            conn = _get_conn()
            conn.execute(
                "UPDATE orders SET status='cancelled', updated_at=datetime('now') WHERE id=?",
                (order["id"],),
            )
            conn.execute(
                "UPDATE trades SET status='cancelled', exit_time=datetime('now') WHERE id=? AND paper=0 AND status='open'",
                (order["trade_id"],),
            )
            conn.commit()
            conn.close()
            audit("live_entry_abandoned", ticker=ticker, trade_id=order["trade_id"],
                  order_id=order["id"], reason=decision["reason"])
            results["abandoned"] += 1
            continue

        # requote or cross: submit replacement at the new side price.
        new_side_price = float(decision["price"])
        new_yes_price = round(1.0 - new_side_price, 4) if direction == "no" else new_side_price
        seq = int(order["requote_count"] or 0) + 1
        client_order_id = make_client_order_id(order["trade_id"], "entry", seq)
        try:
            kalshi_order_id = _submit_to_kalshi(
                ticker, direction, new_yes_price, int(order["contracts"] or 1),
                client_order_id=client_order_id,
            )
        except Exception as exc:
            logger.error("Re-post failed for trade %s: %s", order["trade_id"], exc)
            audit("live_requote_submit_failed", ticker=ticker, trade_id=order["trade_id"],
                  order_id=order["id"], error=str(exc), price=new_yes_price)
            conn = _get_conn()
            conn.execute(
                "UPDATE orders SET status='cancelled', updated_at=datetime('now') WHERE id=?",
                (order["id"],),
            )
            conn.execute(
                "UPDATE trades SET status='cancelled', exit_time=datetime('now') WHERE id=? AND paper=0 AND status='open'",
                (order["trade_id"],),
            )
            conn.commit()
            conn.close()
            continue

        conn = _get_conn()
        conn.execute(
            """UPDATE orders SET status='replaced', updated_at=datetime('now') WHERE id=?""",
            (order["id"],),
        )
        conn.execute(
            """INSERT INTO orders
               (trade_id, market_ticker, side, price, contracts, status, order_type,
                kalshi_order_id, client_order_id, purpose, requote_count, paper, created_at)
               VALUES (?, ?, ?, ?, ?, 'submitted', ?, ?, ?, 'entry', ?, 0, datetime('now'))""",
            (order["trade_id"], ticker, direction, new_yes_price, order["contracts"],
             "cross" if decision["action"] == "cross" else "limit",
             kalshi_order_id, client_order_id, seq),
        )
        # Keep the trade's working entry price current so marks are honest.
        conn.execute(
            "UPDATE trades SET entry_price=? WHERE id=? AND paper=0",
            (new_yes_price, order["trade_id"]),
        )
        conn.commit()
        conn.close()
        audit("live_entry_requoted" if decision["action"] == "requote" else "live_entry_crossed",
              ticker=ticker, trade_id=order["trade_id"], order_id=order["id"],
              new_price_yes=new_yes_price, side_price=new_side_price,
              kalshi_order_id=kalshi_order_id, seq=seq, reason=decision["reason"])
        results["requoted" if decision["action"] == "requote" else "crossed"] += 1

    return results


def submit_live_exit(trade_id: int, exit_reason: str, cross: bool = False) -> dict:
    """Sell a live position with a real Kalshi order.

    Passive by default (post at side bid); cross=True takes the opposite side
    immediately (stop-loss exits should cross — speed beats price there).
    The trade row stays open until the fill monitor confirms the sell filled.
    """
    settings = _get_settings()
    from app.services.audit import audit

    conn = _get_conn()
    trade = conn.execute(
        "SELECT * FROM trades WHERE id=? AND paper=0 AND status='open'", (trade_id,)
    ).fetchone()
    existing_exit = conn.execute(
        """SELECT id FROM orders WHERE trade_id=? AND purpose='exit'
            AND status IN ('submitted', 'pending_fill')""",
        (trade_id,),
    ).fetchone()
    conn.close()
    if trade is None:
        return {"error": "live open trade not found"}
    if existing_exit:
        return {"skipped": True, "reason": "exit order already working", "order_id": existing_exit["id"]}

    direction = (trade["direction"] or "yes").lower()
    quote = _current_quote(trade["market_ticker"])
    if not quote:
        audit("live_exit_no_quote", ticker=trade["market_ticker"], trade_id=trade_id, reason=exit_reason)
        return {"error": "no quote available for exit"}
    side_bid, side_ask = _side_quote(quote, direction)
    if side_bid is None or side_ask is None:
        return {"error": "no usable quote for exit"}

    if cross:
        # Immediate exit: sell into the resting buyers at the side bid.
        side_price = side_bid
    else:
        # Passive exit: undercut the offer by 1c for queue priority, but never
        # cross down into the bid.
        side_price = max(side_bid + 0.01, side_ask - 0.01)
    side_price = max(0.01, min(0.99, round(side_price, 4)))
    yes_price = round(1.0 - side_price, 4) if direction == "no" else round(side_price, 4)

    client_order_id = make_client_order_id(trade_id, "exit", 0)
    kalshi_order_id = _submit_to_kalshi(
        trade["market_ticker"], direction, yes_price, int(trade["contracts"] or 1),
        client_order_id=client_order_id, action="sell",
    )

    conn = _get_conn()
    cur = conn.execute(
        """INSERT INTO orders
           (trade_id, market_ticker, side, price, contracts, status, order_type,
            kalshi_order_id, client_order_id, purpose, exit_reason, paper, created_at)
           VALUES (?, ?, ?, ?, ?, 'submitted', ?, ?, ?, 'exit', ?, 0, datetime('now'))""",
        (trade_id, trade["market_ticker"], direction, yes_price, trade["contracts"],
         "cross" if cross else "limit", kalshi_order_id, client_order_id, exit_reason),
    )
    order_id = cur.lastrowid
    conn.commit()
    conn.close()
    audit("live_exit_submitted", ticker=trade["market_ticker"], trade_id=trade_id,
          order_id=order_id, kalshi_order_id=kalshi_order_id, price_yes=yes_price,
          reason=exit_reason, cross=cross)
    return {"trade_id": trade_id, "order_id": order_id, "kalshi_order_id": kalshi_order_id,
            "price_yes": yes_price}


def reconcile_with_kalshi() -> dict:
    """Compare DB live positions against Kalshi portfolio truth.

    Read-only: mismatches are audited and reported, never auto-mutated —
    a human (or an explicit repair command) resolves them.
    """
    from app.services.kalshi_client import kalshi_request
    from app.services.audit import audit

    settings = _get_settings()
    conn = _get_conn()
    db_positions = conn.execute(
        """SELECT market_ticker, direction, SUM(contracts) AS contracts
             FROM trades WHERE paper=0 AND status='open'
            GROUP BY market_ticker, direction"""
    ).fetchall()
    conn.close()

    if not db_positions:
        return {"db_positions": 0, "mismatches": []}

    try:
        r = kalshi_request("GET", "/portfolio/positions", settings=settings, signed=True, timeout=15)
        if not r.ok:
            return {"error": f"positions HTTP {r.status_code}"}
        payload = r.json()
    except Exception as exc:
        return {"error": str(exc)}

    kalshi_by_ticker = {}
    for pos in payload.get("market_positions", []) or []:
        ticker = pos.get("ticker")
        if not ticker:
            continue
        kalshi_by_ticker[ticker] = pos

    mismatches = []
    for row in db_positions:
        ticker = row["market_ticker"]
        direction = (row["direction"] or "yes").lower()
        db_count = int(row["contracts"] or 0)
        pos = kalshi_by_ticker.get(ticker)
        kalshi_count = 0
        if pos is not None:
            raw = pos.get("position")
            try:
                raw = int(float(raw or 0))
            except (TypeError, ValueError):
                raw = 0
            # Kalshi position sign: positive = long YES, negative = long NO.
            kalshi_count = raw if direction == "yes" else -raw
        if kalshi_count != db_count:
            mismatch = {"ticker": ticker, "direction": direction,
                        "db_contracts": db_count, "kalshi_contracts": kalshi_count}
            mismatches.append(mismatch)
            audit("reconcile_mismatch", ticker=ticker, **{k: v for k, v in mismatch.items() if k != "ticker"})

    return {"db_positions": len(db_positions), "mismatches": mismatches}
