"""Risk & security layer: kill-switch, loss limits, pre-trade sanity checks.

Every control here fails CLOSED: if state can't be read, trading is blocked.
All flips and breaches land in the append-only audit log.

- Kill-switch (`kill_switch` setting): blocks ALL new entries (paper and
  live) and cancels working live entry orders. Position-REDUCING actions
  (exits, cancels) stay allowed — a kill-switch that traps you in positions
  is a liability, not a safety.
- Loss limits (`live_daily_loss_limit` / `live_weekly_loss_limit`, dollars):
  realized live P&L breaching either auto-reverts the bot to paper mode and
  trips the kill-switch. Re-arming live is a deliberate owner action.
- Pre-trade checks: price sanity, contract cap, total-exposure cap,
  duplicate-position guard, balance sufficiency. Run before every live
  submit; any violation aborts the order.

Default caps mirror the capped-pilot proposal in
docs/STRATEGY_RECOMMENDATIONS.md §6 and stay inert until paper_trading=false.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _load_settings() -> dict:
    from app import config as cfg
    return cfg.load()


def _save_settings(settings: dict):
    from app import config as cfg
    cfg.save(settings)


def kill_switch_active(settings: Optional[dict] = None) -> bool:
    try:
        settings = settings or _load_settings()
        return bool(settings.get("kill_switch", False))
    except Exception:
        # Fail closed: unreadable state means no trading.
        return True


def activate_kill_switch(reason: str, actor: str = "owner") -> dict:
    from app.services.audit import audit

    settings = _load_settings()
    settings["kill_switch"] = True
    _save_settings(settings)
    cancelled = cancel_all_working_live_entries(reason=f"kill_switch: {reason}")
    audit("kill_switch_activated", actor=actor, reason=reason,
          working_entries_cancelled=cancelled)
    logger.warning("KILL SWITCH ACTIVATED (%s): %s; %d working entries cancelled",
                   actor, reason, cancelled)
    return {"kill_switch": True, "working_entries_cancelled": cancelled}


def deactivate_kill_switch(actor: str = "owner") -> dict:
    from app.services.audit import audit

    settings = _load_settings()
    settings["kill_switch"] = False
    _save_settings(settings)
    audit("kill_switch_deactivated", actor=actor)
    logger.warning("Kill switch deactivated by %s", actor)
    return {"kill_switch": False}


def cancel_all_working_live_entries(reason: str) -> int:
    """Cancel every live entry order still working at Kalshi. Exits are left
    alone — they reduce risk."""
    from app.database import get_conn
    from app.services.audit import audit

    conn = get_conn()
    try:
        working = conn.execute(
            """SELECT id, trade_id, market_ticker, kalshi_order_id FROM orders
                WHERE paper=0 AND purpose='entry'
                  AND status IN ('submitted', 'pending_fill')
                  AND kalshi_order_id IS NOT NULL"""
        ).fetchall()
    finally:
        conn.close()

    if not working:
        return 0

    from app.services.order_manager import cancel_kalshi_order

    cancelled = 0
    for order in working:
        try:
            if cancel_kalshi_order(order["kalshi_order_id"]):
                conn = get_conn()
                conn.execute(
                    "UPDATE orders SET status='cancelled', updated_at=datetime('now') WHERE id=?",
                    (order["id"],),
                )
                conn.execute(
                    """UPDATE trades SET status='cancelled', exit_time=datetime('now')
                        WHERE id=? AND paper=0 AND status='open'""",
                    (order["trade_id"],),
                )
                conn.commit()
                conn.close()
                cancelled += 1
                audit("live_entry_cancelled", ticker=order["market_ticker"],
                      trade_id=order["trade_id"], order_id=order["id"], reason=reason)
        except Exception as exc:
            logger.error("Kill-switch cancel failed for order %s: %s", order["id"], exc)
    return cancelled


def live_realized_pnl(days: float) -> float:
    """Realized P&L on real-money trades closed in the trailing window."""
    from app.database import get_conn

    conn = get_conn()
    try:
        row = conn.execute(
            """SELECT COALESCE(SUM(pnl), 0.0) AS pnl FROM trades
                WHERE paper=0 AND status='closed' AND pnl IS NOT NULL
                  AND exit_time >= datetime('now', ?)""",
            (f"-{float(days) * 24:.1f} hours",),
        ).fetchone()
    finally:
        conn.close()
    return round(float(row["pnl"] or 0.0), 4)


def current_live_exposure() -> float:
    """Dollars at risk across open live positions (side cost x contracts)."""
    from app.database import get_conn

    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT direction, entry_price, contracts FROM trades
                WHERE paper=0 AND status='open'"""
        ).fetchall()
    finally:
        conn.close()
    total = 0.0
    for r in rows:
        entry_yes = float(r["entry_price"] or 0.0)
        side_cost = (1.0 - entry_yes) if (r["direction"] or "yes").lower() == "no" else entry_yes
        total += side_cost * int(r["contracts"] or 1)
    return round(total, 4)


def check_loss_limits() -> dict:
    """Enforce daily/weekly live loss limits. Breach -> revert to paper + kill.

    No-ops in paper mode (paper losses are learning, not money).
    """
    settings = _load_settings()
    if settings.get("paper_trading", True):
        return {"skipped": True, "reason": "paper mode"}

    daily_limit = float(settings.get("live_daily_loss_limit", 0) or 0)
    weekly_limit = float(settings.get("live_weekly_loss_limit", 0) or 0)
    result = {"daily_pnl": live_realized_pnl(1.0), "weekly_pnl": live_realized_pnl(7.0),
              "breached": None}

    breach = None
    if daily_limit > 0 and result["daily_pnl"] <= -daily_limit:
        breach = f"daily loss limit: {result['daily_pnl']:.2f} <= -{daily_limit:.2f}"
    elif weekly_limit > 0 and result["weekly_pnl"] <= -weekly_limit:
        breach = f"weekly loss limit: {result['weekly_pnl']:.2f} <= -{weekly_limit:.2f}"

    if breach:
        result["breached"] = breach
        revert_to_paper(breach)
    return result


def revert_to_paper(reason: str):
    """Loss-limit response: paper mode + kill switch, fully audited."""
    from app.services.audit import audit

    settings = _load_settings()
    settings["paper_trading"] = True
    settings["auto_trade_enabled"] = False
    _save_settings(settings)
    audit("loss_limit_revert_to_paper", actor="risk", reason=reason)
    logger.error("LOSS LIMIT BREACH — reverted to paper: %s", reason)
    activate_kill_switch(reason=f"loss limit breach: {reason}", actor="risk")


def pre_trade_checks(
    market_ticker: str,
    direction: str,
    yes_price: float,
    contracts: int,
    settings: Optional[dict] = None,
) -> list:
    """Sanity gauntlet before any live order. Returns violations (empty = go)."""
    settings = settings or _load_settings()
    violations = []

    if kill_switch_active(settings):
        violations.append("kill switch is active")

    direction = (direction or "yes").lower()
    side_cost = (1.0 - float(yes_price)) if direction == "no" else float(yes_price)

    if not (0.01 <= float(yes_price) <= 0.99):
        violations.append(f"price sanity: yes price {yes_price} outside [0.01, 0.99]")
    if side_cost <= 0.0 or side_cost >= 1.0:
        violations.append(f"price sanity: side cost {side_cost:.2f} invalid")

    max_contracts = int(settings.get("live_max_contracts_per_trade", 2) or 2)
    if int(contracts) < 1:
        violations.append("contracts must be >= 1")
    if int(contracts) > max_contracts:
        violations.append(f"contracts {contracts} > live cap {max_contracts}")

    # Duplicate guard: one live position per market, ever.
    from app.database import get_conn
    conn = get_conn()
    try:
        dup = conn.execute(
            "SELECT id FROM trades WHERE paper=0 AND status='open' AND market_ticker=?",
            (market_ticker,),
        ).fetchone()
    finally:
        conn.close()
    if dup:
        violations.append(f"duplicate guard: live position already open on {market_ticker}")

    # Total exposure cap.
    max_exposure = float(settings.get("live_max_total_exposure", 25.0) or 0)
    if max_exposure > 0:
        new_exposure = current_live_exposure() + side_cost * int(contracts)
        if new_exposure > max_exposure:
            violations.append(
                f"exposure cap: {new_exposure:.2f} > {max_exposure:.2f} with this order"
            )

    # Balance sufficiency (live Kalshi balance must cover the order).
    try:
        from app.services.kalshi_client import get_balance
        balance_info = get_balance(settings)
        balance = balance_info.get("balance")
        if balance is None:
            violations.append("balance check failed: no balance available")
        elif side_cost * int(contracts) > float(balance):
            violations.append(
                f"insufficient balance: need {side_cost * int(contracts):.2f}, have {balance:.2f}"
            )
    except Exception as exc:
        violations.append(f"balance check failed: {exc}")

    return violations


def risk_status() -> dict:
    settings = _load_settings()
    return {
        "kill_switch": bool(settings.get("kill_switch", False)),
        "paper_trading": bool(settings.get("paper_trading", True)),
        "auto_trade_enabled": bool(settings.get("auto_trade_enabled", False)),
        "live_daily_loss_limit": float(settings.get("live_daily_loss_limit", 0) or 0),
        "live_weekly_loss_limit": float(settings.get("live_weekly_loss_limit", 0) or 0),
        "live_max_total_exposure": float(settings.get("live_max_total_exposure", 0) or 0),
        "live_max_contracts_per_trade": int(settings.get("live_max_contracts_per_trade", 2) or 2),
        "daily_live_pnl": live_realized_pnl(1.0),
        "weekly_live_pnl": live_realized_pnl(7.0),
        "current_live_exposure": current_live_exposure(),
    }
