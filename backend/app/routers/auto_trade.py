"""
Auto-trade router: status, manual trigger, circuit breaker reset.
All endpoints are read-safe except POST /run and POST /reset-circuit-breaker.
"""
from fastapi import APIRouter

router = APIRouter()


@router.get("/auto-trade/status")
@router.get("/auto-entry/status")
def get_status():
    from app.services.auto_entry import get_auto_entry_status
    return get_auto_entry_status()


@router.post("/auto-trade/run")
def run_now():
    """Manually trigger one full automation pass: settle, backfill, rebuild, enter."""
    from app.services.auto_entry import run_automation_cycle
    result = run_automation_cycle()
    return result


@router.get("/auto-trade/readiness")
def live_readiness():
    """Explain live automation blockers and candidate sizing without placing orders."""
    from app.services.auto_entry import get_live_readiness_report
    return get_live_readiness_report()


@router.post("/auto-trade/reset-circuit-breaker")
def reset_circuit_breaker():
    """Reset the circuit breaker by setting consecutive_losses limit back to 0."""
    from app import config as cfg
    settings = cfg.load()
    settings["circuit_breaker_consecutive_losses"] = 0
    cfg.save(settings)
    return {"reset": True}


@router.get("/auto-trade/orders")
def list_live_orders():
    """List all non-paper orders with their current status."""
    from app.database import get_conn
    conn = get_conn()
    rows = conn.execute(
        """SELECT orders.id, orders.trade_id, orders.market_ticker, orders.side,
                  orders.price, orders.contracts, orders.status, orders.kalshi_order_id,
                  orders.created_at, orders.updated_at,
                  trades.entry_price AS actual_fill_price
             FROM orders
             LEFT JOIN trades ON trades.id = orders.trade_id
            WHERE orders.paper = 0
            ORDER BY orders.created_at DESC
            LIMIT 100"""
    ).fetchall()
    conn.close()
    return {"orders": [dict(r) for r in rows]}
