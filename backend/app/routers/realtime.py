from fastapi import APIRouter

router = APIRouter()


@router.get("/realtime/status")
def realtime_status():
    from app.database import get_conn
    from app.services.realtime import feed

    conn = get_conn()
    try:
        row = conn.execute(
            """SELECT COUNT(*) AS n,
                      COUNT(DISTINCT market_ticker) AS tickers,
                      MAX(created_at) AS latest
                 FROM price_snapshots"""
        ).fetchone()
        true_clv_row = conn.execute(
            "SELECT COUNT(*) AS n FROM trades WHERE true_clv IS NOT NULL"
        ).fetchone()
    finally:
        conn.close()

    return {
        **feed.status,
        "watchlist_size": len(feed._watchlist),
        "cached_quotes": len(feed.quotes),
        "snapshot_rows": row["n"],
        "snapshot_tickers": row["tickers"],
        "latest_snapshot_at": row["latest"],
        "trades_with_true_clv": true_clv_row["n"],
    }


@router.get("/realtime/quotes")
def realtime_quotes():
    from app.services.realtime import feed

    return {"quotes": feed.quotes, "count": len(feed.quotes)}
