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
) -> dict:
    settings = _get_settings()
    is_paper = settings.get("paper_trading", True)

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
        return _paper_place(market_ticker, direction, entry_price, alert_id, contracts, stop_loss_price, take_profit_price)
    return _live_place(market_ticker, direction, entry_price, alert_id, contracts, stop_loss_price, take_profit_price)


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
) -> dict:
    logger.info("LIVE order: %s %s @ %.4f x%d", direction, market_ticker, entry_price, contracts)
    kalshi_order_id = _submit_to_kalshi(market_ticker, direction, entry_price, contracts)
    conn = _get_conn()
    trade_cur = conn.execute(
        """INSERT INTO trades
           (market_ticker, alert_id, direction, entry_price, contracts,
            stop_loss_price, take_profit_price, paper, status, entry_time)
           VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'open', datetime('now'))""",
        (market_ticker, alert_id, direction, entry_price, contracts, stop_loss_price, take_profit_price),
    )
    trade_id = trade_cur.lastrowid
    order_cur = conn.execute(
        """INSERT INTO orders
           (trade_id, market_ticker, side, price, contracts, status, order_type,
            kalshi_order_id, paper, created_at)
           VALUES (?, ?, ?, ?, ?, 'submitted', 'limit', ?, 0, datetime('now'))""",
        (trade_id, market_ticker, direction, entry_price, contracts, kalshi_order_id),
    )
    order_id = order_cur.lastrowid
    conn.commit()
    conn.close()
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
           WHERE trade_id=?""",
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
           WHERE trade_id=?""",
        (trade["id"],),
    )
    conn.commit()
    conn.close()

    from app.services import adaptive_policy
    adaptive_policy.rebuild_snapshots()

    return {"trade_id": trade["id"], "clv": clv_at_close, "pnl": pnl, "paper": False}


def _submit_to_kalshi(ticker: str, direction: str, price: float, contracts: int) -> Optional[str]:
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
        "action": "buy",
        "type": "limit",
        "side": side,
        "count": contracts,
        "client_order_id": str(_uuid.uuid4()),
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

            conn2 = _get_conn()
            if status in ("filled", "partially_filled", "executed") and filled_count > 0:
                actual_price = avg_price
                upd = {"status": "filled" if status == "filled" else "partial"}
                if actual_price:
                    conn2.execute(
                        "UPDATE trades SET entry_price=? WHERE id=? AND paper=0",
                        (actual_price, row["trade_id"]),
                    )
                conn2.execute(
                    "UPDATE orders SET status=?, updated_at=datetime('now') WHERE id=?",
                    (upd["status"], row["order_id"]),
                )
                filled += 1
            elif status in ("cancelled", "expired", "rejected"):
                conn2.execute(
                    "UPDATE orders SET status=?, updated_at=datetime('now') WHERE id=?",
                    (status, row["order_id"]),
                )
                conn2.execute(
                    "UPDATE trades SET status='cancelled', exit_time=datetime('now') WHERE id=? AND paper=0",
                    (row["trade_id"],),
                )
            conn2.commit()
            conn2.close()
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
