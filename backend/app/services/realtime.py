"""Real-time Kalshi market data feed (WebSocket).

Subscribes to the Kalshi `ticker` channel for every market we care about
(open trades + pending alerts), keeps an in-memory quote cache, mirrors
fresh marks into the `markets` table (so existing position/alert endpoints
see live prices instead of 15-minute-old scan data), and writes throttled
rows into `price_snapshots`.

`price_snapshots` is the price-path store this bot never had: it enables
true closing-line value (the market price just before close, vs. the
settlement-restated "clv" column), stop-loss/take-profit backtesting, and
honest brain metrics. See docs/STRATEGY_RECOMMENDATIONS.md §5.

Design notes:
- Runs as an asyncio task inside the FastAPI event loop (started from
  lifespan). DB writes hop through asyncio.to_thread so the loop never
  blocks on SQLite.
- Auth reuses the RSA-PSS scheme from kalshi_client; the WS handshake signs
  ``timestamp + "GET" + "/trade-api/ws/v2"``.
- Reconnects with exponential backoff. Watchlist refreshes every 60s; a
  changed watchlist forces a clean resubscribe (reconnect) — watchlists only
  really change after scans, so this is cheap.
- Snapshot throttle (pure function, unit-tested): one row per ticker per
  SNAPSHOT_INTERVAL_S, unless the yes-mid moved >= SNAPSHOT_MOVE_THRESHOLD
  or the market is inside CLOSE_WINDOW_MINUTES of close (then the faster
  CLOSE_SNAPSHOT_INTERVAL_S applies) — that final window is what true CLV
  is computed from.
"""

import asyncio
import base64
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

WS_PATH = "/trade-api/ws/v2"
SNAPSHOT_INTERVAL_S = 30.0
CLOSE_SNAPSHOT_INTERVAL_S = 10.0
SNAPSHOT_MOVE_THRESHOLD = 0.02
CLOSE_WINDOW_MINUTES = 30.0
WATCHLIST_REFRESH_S = 60.0
RECONNECT_BASE_S = 2.0
RECONNECT_MAX_S = 120.0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ws_url_from_api_base(api_base: str) -> str:
    """https://host/trade-api/v2 -> wss://host/trade-api/ws/v2"""
    base = str(api_base or "").strip()
    host = base.split("://", 1)[-1].split("/", 1)[0]
    return f"wss://{host}{WS_PATH}"


def should_snapshot(
    last_snapshot_ts: Optional[float],
    last_snapshot_mid: Optional[float],
    new_mid: Optional[float],
    now_ts: float,
    minutes_to_close: Optional[float],
) -> bool:
    """Pure throttle decision — see module docstring."""
    if new_mid is None:
        return False
    if last_snapshot_ts is None:
        return True
    interval = SNAPSHOT_INTERVAL_S
    if minutes_to_close is not None and minutes_to_close <= CLOSE_WINDOW_MINUTES:
        interval = CLOSE_SNAPSHOT_INTERVAL_S
    if now_ts - last_snapshot_ts >= interval:
        return True
    if last_snapshot_mid is not None and abs(new_mid - last_snapshot_mid) >= SNAPSHOT_MOVE_THRESHOLD:
        return True
    return False


def close_mark_for(ticker: str, close_time: Optional[str] = None) -> Optional[dict]:
    """Last pre-close price snapshot for a ticker → the true-CLV reference.

    Returns {"yes_mid":…, "yes_bid":…, "yes_ask":…, "ts":…} or None when no
    snapshot exists (feed wasn't running — true CLV stays NULL, never faked).
    """
    from app.database import get_conn

    conn = get_conn()
    try:
        if close_time:
            normalized = str(close_time).replace("Z", "+00:00")
            row = conn.execute(
                """SELECT yes_bid, yes_ask, yes_mid, created_at FROM price_snapshots
                    WHERE market_ticker = ? AND created_at <= ?
                    ORDER BY created_at DESC LIMIT 1""",
                (ticker, normalized),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT yes_bid, yes_ask, yes_mid, created_at FROM price_snapshots
                    WHERE market_ticker = ?
                    ORDER BY created_at DESC LIMIT 1""",
                (ticker,),
            ).fetchone()
    finally:
        conn.close()
    if row is None or row["yes_mid"] is None:
        return None
    return {
        "yes_mid": row["yes_mid"],
        "yes_bid": row["yes_bid"],
        "yes_ask": row["yes_ask"],
        "ts": row["created_at"],
    }


def true_clv(direction: str, entry_yes_price: float, close_mark_yes: float) -> float:
    """Closing-line value in YES coords: positive = entry beat the close."""
    if (direction or "yes").lower() == "no":
        return round(entry_yes_price - close_mark_yes, 4)
    return round(close_mark_yes - entry_yes_price, 4)


class RealtimeFeed:
    def __init__(self):
        self.quotes: dict[str, dict] = {}          # ticker -> latest quote
        self._snap_ts: dict[str, float] = {}       # ticker -> last snapshot epoch
        self._snap_mid: dict[str, float] = {}      # ticker -> last snapshot mid
        self._close_times: dict[str, Optional[str]] = {}
        self._watchlist: set[str] = set()
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._subscribers: list[asyncio.Queue] = []
        self.status: dict = {
            "enabled": False,
            "connected": False,
            "subscribed_tickers": 0,
            "messages_received": 0,
            "snapshots_written": 0,
            "last_message_at": None,
            "last_error": None,
            "reconnects": 0,
        }

    # ---- lifecycle ----

    def start(self):
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self.status["enabled"] = True
        self._task = asyncio.get_event_loop().create_task(self._run(), name="kalshi-realtime-feed")

    async def stop(self):
        self.status["enabled"] = False
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    # ---- event bus (foundation for the SSE/UI workstream) ----

    def subscribe_events(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subscribers.append(q)
        return q

    def unsubscribe_events(self, q: asyncio.Queue):
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    def _publish(self, event: dict):
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Slow consumer: drop oldest to keep the feed moving.
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except Exception:
                    pass

    # ---- main loop ----

    async def _run(self):
        backoff = RECONNECT_BASE_S
        while not self._stop.is_set():
            try:
                watchlist = await asyncio.to_thread(self._load_watchlist)
                self._watchlist = watchlist
                if not watchlist:
                    self.status["connected"] = False
                    self.status["subscribed_tickers"] = 0
                    await asyncio.sleep(WATCHLIST_REFRESH_S)
                    continue
                await self._connect_and_consume(watchlist)
                backoff = RECONNECT_BASE_S
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.status["connected"] = False
                self.status["last_error"] = f"{exc.__class__.__name__}: {exc}"
                self.status["reconnects"] += 1
                logger.warning("Realtime feed error (%s); reconnecting in %.0fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(RECONNECT_MAX_S, backoff * 2)

    async def _connect_and_consume(self, watchlist: set[str]):
        import websockets

        from app import config as cfg
        settings = await asyncio.to_thread(cfg.load)
        from app.services import kalshi_client

        if not kalshi_client.credentials_configured(settings):
            self.status["last_error"] = "kalshi credentials not configured"
            await asyncio.sleep(WATCHLIST_REFRESH_S)
            return

        # The WS endpoint is not served on every REST host (external-api
        # 404s the upgrade); walk the same host fallback list as REST.
        last_exc = None
        ws_conn = None
        url = None
        for base in kalshi_client.kalshi_api_bases(settings):
            candidate = ws_url_from_api_base(base)
            headers = self._ws_auth_headers(settings)
            try:
                ws_conn = await websockets.connect(
                    candidate, additional_headers=headers, ping_interval=10, ping_timeout=20
                )
                url = candidate
                break
            except Exception as exc:
                last_exc = exc
                logger.info("Realtime WS connect failed on %s: %s", candidate, exc)
        if ws_conn is None:
            raise last_exc if last_exc else RuntimeError("no Kalshi WS host accepted the connection")

        async with ws_conn as ws:
            await ws.send(json.dumps({
                "id": 1,
                "cmd": "subscribe",
                "params": {"channels": ["ticker"], "market_tickers": sorted(watchlist)},
            }))
            self.status["connected"] = True
            self.status["subscribed_tickers"] = len(watchlist)
            self.status["last_error"] = None
            logger.info("Realtime feed connected to %s, %d tickers", url, len(watchlist))
            await asyncio.to_thread(self._load_close_times, watchlist)

            last_watchlist_check = time.monotonic()
            while not self._stop.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=WATCHLIST_REFRESH_S)
                except asyncio.TimeoutError:
                    raw = None
                if raw is not None:
                    await self._handle_message(raw)
                if time.monotonic() - last_watchlist_check >= WATCHLIST_REFRESH_S:
                    last_watchlist_check = time.monotonic()
                    fresh = await asyncio.to_thread(self._load_watchlist)
                    if fresh != watchlist:
                        logger.info("Realtime watchlist changed (%d -> %d); resubscribing",
                                    len(watchlist), len(fresh))
                        return  # clean reconnect with the new list

    async def _handle_message(self, raw):
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return
        if data.get("type") != "ticker":
            return
        msg = data.get("msg") or {}
        ticker = msg.get("market_ticker")
        if not ticker:
            return

        self.status["messages_received"] += 1
        self.status["last_message_at"] = _utcnow().isoformat()

        quote = self._quote_from_ws(msg)
        self.quotes[ticker] = quote
        self._publish({"type": "quote", "ticker": ticker, **quote})

        now_ts = time.time()
        mid = quote.get("yes_mid")
        if should_snapshot(
            self._snap_ts.get(ticker),
            self._snap_mid.get(ticker),
            mid,
            now_ts,
            self._minutes_to_close(ticker),
        ):
            self._snap_ts[ticker] = now_ts
            if mid is not None:
                self._snap_mid[ticker] = mid
            await asyncio.to_thread(self._persist, ticker, quote)
            self.status["snapshots_written"] += 1

    @staticmethod
    def _quote_from_ws(msg: dict) -> dict:
        """Live ticker schema (verified 2026-06-10 against the elections host):
        dollar-string fields like yes_bid_dollars="0.2900"; legacy integer-cent
        fields (yes_bid=29) kept as fallback."""
        def dollars(dollar_key, cents_key):
            v = msg.get(dollar_key)
            if v not in (None, ""):
                try:
                    return round(float(v), 4)
                except (TypeError, ValueError):
                    pass
            v = msg.get(cents_key)
            if v in (None, ""):
                return None
            try:
                return round(float(v) / 100.0, 4)
            except (TypeError, ValueError):
                return None

        def number(*keys):
            for key in keys:
                v = msg.get(key)
                if v in (None, ""):
                    continue
                try:
                    return float(v)
                except (TypeError, ValueError):
                    continue
            return None

        yes_bid = dollars("yes_bid_dollars", "yes_bid")
        yes_ask = dollars("yes_ask_dollars", "yes_ask")
        mid = None
        if yes_bid is not None and yes_ask is not None:
            mid = round((yes_bid + yes_ask) / 2.0, 4)
        elif yes_ask is not None:
            mid = yes_ask
        return {
            "yes_bid": yes_bid,
            "yes_ask": yes_ask,
            "yes_mid": mid,
            "last_price": dollars("price_dollars", "price"),
            "yes_bid_size": number("yes_bid_size_fp", "yes_bid_size"),
            "yes_ask_size": number("yes_ask_size_fp", "yes_ask_size"),
            "volume": number("volume_fp", "volume"),
            "open_interest": number("open_interest_fp", "open_interest"),
            "ts": msg.get("ts"),
            "received_at": _utcnow().isoformat(),
        }

    def _minutes_to_close(self, ticker: str) -> Optional[float]:
        close_time = self._close_times.get(ticker)
        if not close_time:
            return None
        try:
            normalized = str(close_time).replace("Z", "+00:00")
            dt = datetime.fromisoformat(normalized)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (dt - _utcnow()).total_seconds() / 60.0
        except Exception:
            return None

    # ---- sync helpers (run via to_thread) ----

    def _load_watchlist(self) -> set[str]:
        from app.database import get_conn

        conn = get_conn()
        try:
            open_trades = conn.execute(
                "SELECT DISTINCT market_ticker FROM trades WHERE status='open'"
            ).fetchall()
            pending_alerts = conn.execute(
                """SELECT DISTINCT market_ticker FROM alerts
                    WHERE status='pending'
                      AND updated_at >= datetime('now', '-4 hours')"""
            ).fetchall()
        finally:
            conn.close()
        return {r["market_ticker"] for r in open_trades} | {r["market_ticker"] for r in pending_alerts}

    def _load_close_times(self, tickers: set[str]):
        from app.database import get_conn

        if not tickers:
            return
        conn = get_conn()
        try:
            placeholders = ",".join("?" for _ in tickers)
            rows = conn.execute(
                f"SELECT ticker, close_time FROM markets WHERE ticker IN ({placeholders})",
                tuple(tickers),
            ).fetchall()
        finally:
            conn.close()
        self._close_times = {r["ticker"]: r["close_time"] for r in rows}

    def _persist(self, ticker: str, quote: dict):
        from app.database import get_conn

        conn = get_conn()
        try:
            conn.execute(
                """INSERT INTO price_snapshots
                     (market_ticker, yes_bid, yes_ask, yes_mid, last_price, source)
                   VALUES (?, ?, ?, ?, ?, 'ws')""",
                (ticker, quote.get("yes_bid"), quote.get("yes_ask"),
                 quote.get("yes_mid"), quote.get("last_price")),
            )
            # Mirror live marks into markets so existing endpoints see them.
            conn.execute(
                """UPDATE markets SET
                     market_price=COALESCE(?, market_price),
                     yes_bid=COALESCE(?, yes_bid),
                     yes_ask=COALESCE(?, yes_ask),
                     no_bid=COALESCE(?, no_bid),
                     no_ask=COALESCE(?, no_ask),
                     updated_at=datetime('now')
                   WHERE ticker=?""",
                (
                    quote.get("yes_mid"),
                    quote.get("yes_bid"),
                    quote.get("yes_ask"),
                    round(1.0 - quote["yes_ask"], 4) if quote.get("yes_ask") is not None else None,
                    round(1.0 - quote["yes_bid"], 4) if quote.get("yes_bid") is not None else None,
                    ticker,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _ws_auth_headers(settings: dict) -> dict:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        from app.services.kalshi_client import _load_private_key

        timestamp = str(int(time.time() * 1000))
        message = f"{timestamp}GET{WS_PATH}".encode("utf-8")
        private_key = _load_private_key(settings["kalshi_private_key_path"])
        signature = private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": settings["kalshi_key_id"],
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("utf-8"),
        }


feed = RealtimeFeed()
