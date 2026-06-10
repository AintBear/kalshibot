"""Append-only audit log for every consequential trading action.

Live order submits/cancels/re-quotes/exits, fills, reconciliation
mismatches, kill-switch flips, and loss-limit breaches all land here.
Rows are never updated or deleted.
"""
import json
import logging

logger = logging.getLogger(__name__)


def audit(action: str, *, ticker: str = None, trade_id: int = None,
          order_id: int = None, actor: str = "system", **details):
    """Write one audit row. Never raises — auditing must not break trading."""
    try:
        from app.database import get_conn

        conn = get_conn()
        try:
            conn.execute(
                """INSERT INTO audit_log (action, actor, market_ticker, trade_id, order_id, details)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (action, actor, ticker, trade_id, order_id,
                 json.dumps(details, default=str) if details else None),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.error("audit write failed for %s: %s", action, exc)


def recent(limit: int = 100) -> list:
    from app.database import get_conn

    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT id, action, actor, market_ticker, trade_id, order_id, details, created_at
                 FROM audit_log ORDER BY id DESC LIMIT ?""",
            (int(limit),),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("details"):
            try:
                d["details"] = json.loads(d["details"])
            except (TypeError, ValueError):
                pass
        out.append(d)
    return out
