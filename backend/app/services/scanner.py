"""
Weather market scanner: fetches Kalshi weather markets, scores them via weather_model,
evaluates via weather_brain, and upserts alerts for edge opportunities.
"""
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional, Tuple

import requests

from app.services import weather_model, weather_brain, position_sizing
from app.services.kalshi_client import (
    kalshi_api_bases,
    kalshi_request,
    quote_from_market,
    settlement_result_from_market,
)
from app.database import get_conn

logger = logging.getLogger(__name__)

MAX_SERIES_ERROR_DETAILS = 20

# Active daily weather series tickers on Kalshi (verified 2026-04)
WEATHER_SERIES = [
    "KXHIGHLAX", "KXHIGHNY", "KXHIGHCHI", "KXHIGHDEN", "KXHIGHMIA",
    "KXHIGHOU", "KXHIGHAUS", "KXHIGHTPHX", "KXHIGHTLV", "KXHIGHTSEA",
    "KXHIGHTDC", "KXHIGHTBOS", "KXHIGHTOKC", "KXHIGHTMIN", "KXHIGHTNOLA",
    "KXHIGHTATL", "KXHIGHTDAL", "KXHIGHTHOU", "KXHIGHTSATX", "KXHIGHTSFO",
    "KXHIGHPHIL", "KXDENHIGH",
    "KXLOWTNYC", "KXLOWTSEA", "KXLOWTLV", "KXLOWTCHI", "KXLOWLAX",
    "KXLOWTPHX", "KXLOWTDEN", "KXLOWTBOS", "KXLOWTMIA", "KXLOWTHOU",
    "KXLOWTAUS", "KXLOWTMIN", "KXLOWTPHIL", "KXLOWTDAL", "KXLOWTSATX",
    "KXLOWTSFO", "KXLOWTOKC", "KXLOWTATL", "KXLOWTDC", "KXLOWTNOLA",
    "KXLOWNYC", "KXLOWCHI", "KXLOWMIA", "KXLOWPHIL", "KXLOWDEN",
    "KXRAINNYCM", "KXRAINSFOM", "KXRAINCHIM", "KXRAINLAXM",
    "KXRAINSEA", "KXRAINSEAM", "KXRAINAUSM", "KXRAINDALM",
    "KXRAINHOUM", "KXRAINDENM", "KXRAINMIAM",
    "KXSNOWNYM", "KXSNOWNY", "KXSNOWNYC", "KXSNOWCHIM",
]

SERIES_FALLBACK_ALIASES = {
    "KXDENHIGH": ["KXHIGHDEN"],
}

_last_scan: dict = {
    "status": "never",
    "stage": "idle",
    "progress": 0,
    "started_at": None,
    "completed_at": None,
    "markets_found": 0,
    "markets_processed": 0,
    "alerts_created": 0,
    "error": None,
    "series_error_details": [],
}


def get_scan_status() -> dict:
    if _last_scan.get("status") == "running":
        return _public_scan_status(_last_scan)
    persisted = _load_scan_status()
    if persisted:
        if persisted.get("status") == "running" and _last_scan.get("status") == "never":
            persisted = _mark_interrupted_scan(persisted)
        _last_scan.update(persisted)
        return _public_scan_status(persisted)
    if _last_scan["status"] == "never":
        _recover_last_scan()
    return _public_scan_status(_last_scan)


def _set_scan_status(_persist: bool = True, **updates) -> None:
    _last_scan.update(updates)
    try:
        _last_scan["accuweather_cache"] = weather_model.accuweather_cache_status()
    except Exception:
        pass
    if _persist:
        _persist_scan_status()


def _load_scan_status() -> Optional[dict]:
    try:
        conn = get_conn()
        row = conn.execute("SELECT payload, updated_at FROM scan_status WHERE id=1").fetchone()
        conn.close()
        if not row or not row["payload"]:
            return None
        payload = json.loads(row["payload"])
        if isinstance(payload, dict):
            payload["_persisted_updated_at"] = row["updated_at"]
            return payload
    except Exception:
        return None
    return None


def _mark_interrupted_scan(persisted: dict) -> dict:
    interrupted = dict(persisted)
    interrupted.update({
        "status": "failed",
        "stage": "failed",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "error": "scan interrupted by backend restart",
        "interrupted": True,
    })
    _last_scan.update(interrupted)
    _persist_scan_status()
    return _public_scan_status(_last_scan)


def _persist_scan_status() -> None:
    try:
        payload = json.dumps(_public_scan_status(_last_scan))
        conn = get_conn()
        conn.execute(
            """INSERT INTO scan_status
                   (id, status, stage, progress, started_at, completed_at,
                    markets_found, markets_processed, alerts_created, payload, updated_at)
               VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(id) DO UPDATE SET
                    status=excluded.status,
                    stage=excluded.stage,
                    progress=excluded.progress,
                    started_at=excluded.started_at,
                    completed_at=excluded.completed_at,
                    markets_found=excluded.markets_found,
                    markets_processed=excluded.markets_processed,
                    alerts_created=excluded.alerts_created,
                    payload=excluded.payload,
                    updated_at=excluded.updated_at""",
            (
                _last_scan.get("status"),
                _last_scan.get("stage"),
                int(_last_scan.get("progress") or 0),
                _last_scan.get("started_at"),
                _last_scan.get("completed_at"),
                int(_last_scan.get("markets_found") or 0),
                int(_last_scan.get("markets_processed") or 0),
                int(_last_scan.get("alerts_created") or 0),
                payload,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("Failed to persist scan status: %s", exc)


def _recover_last_scan():
    """Derive scan status from DB when the in-memory state was lost on restart."""
    try:
        conn = get_conn()
        row = conn.execute(
            "SELECT COUNT(*) as cnt, MAX(updated_at) as latest FROM alerts WHERE status IN ('pending','paper_traded')"
        ).fetchone()
        conn.close()
        if row and row["cnt"] and row["cnt"] > 0:
            _set_scan_status(**{
                "status": "complete",
                "stage": "complete",
                "progress": 100,
                "completed_at": row["latest"],
                "markets_found": row["cnt"],
                "markets_processed": row["cnt"],
                "alerts_created": 0,
                "recovered_from_db": True,
            })
    except Exception:
        pass


def _public_scan_status(status: dict) -> dict:
    public = {k: v for k, v in dict(status).items() if not str(k).startswith("_")}
    if public.get("status") != "failed":
        public.pop("interrupted", None)
    return public


def scan_weather_markets(settings: Optional[dict] = None) -> dict:
    global _last_scan
    _set_scan_status(**{
        "status": "running",
        "stage": "fetching_markets",
        "progress": 5,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
        "markets_found": 0,
        "markets_processed": 0,
        "alerts_created": 0,
        "stale_expired": 0,
        "paper_trades_created": 0,
        "error": None,
        "interrupted": False,
        "recovered_from_db": False,
        "series_errors": 0,
        "series_error_details": [],
        "current_series": None,
        "series_processed": 0,
        "series_total": 0,
        "current_ticker": None,
    })

    try:
        if settings is None:
            from app import config as cfg
            settings = cfg.load()

        markets = _fetch_kalshi_weather_markets(settings)
        _set_scan_status(**{
            "markets_found": len(markets),
            "stage": "scoring_forecasts",
            "progress": 15 if markets else 80,
        })

        scored_markets = _score_markets_for_priority(markets)
        _set_scan_status(**{
            "stage": "creating_alerts",
            "progress": 25 if scored_markets else 80,
        })

        alerts_created = 0
        total = max(len(scored_markets), 1)
        for idx, item in enumerate(scored_markets, start=1):
            market = item["market"]
            _set_scan_status(**{
                "markets_processed": idx - 1,
                "current_ticker": market.get("ticker") or market.get("market_ticker"),
                "progress": min(88, 15 + round((idx - 1) / total * 70)),
            })
            try:
                created = _process_market(
                    market,
                    settings,
                    precomputed_result=item.get("result"),
                    precomputed_quote=item.get("quote"),
                )
                if created:
                    alerts_created += 1
            except Exception as e:
                logger.warning("Error processing market %s: %s", market.get("ticker", "?"), e)

        _set_scan_status(**{
            "markets_processed": len(markets),
            "stage": "creating_alerts",
            "progress": 90,
            "alerts_created": alerts_created,
        })
        stale_expired = _expire_unrefreshed_pending_alerts()
        paper_trades_created = _auto_execute_paper_candidates(settings)
        _set_scan_status(**{
            "status": "complete",
            "stage": "complete",
            "progress": 100,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "alerts_created": alerts_created,
            "stale_expired": stale_expired,
            "paper_trades_created": paper_trades_created,
            "current_ticker": None,
        })
    except Exception as e:
        logger.exception("Weather scan failed")
        _set_scan_status(**{
            "status": "failed",
            "stage": "failed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "error": str(e),
        })
    return _public_scan_status(_last_scan)


def _fetch_kalshi_weather_markets(settings: dict) -> list:
    headers = {"User-Agent": "sibylla-weather-bot/1.0"}

    all_markets = []
    series_errors = 0

    def _fetch_series(series: str) -> list:
        last_status = None
        last_attempts = []
        for attempt in range(3):
            try:
                r = kalshi_request(
                    "GET",
                    "/markets",
                    settings=settings,
                    params={"status": "open", "series_ticker": series, "limit": 50},
                    headers=headers,
                    timeout=(5, 15),
                )
            except Exception as exc:
                raise _series_fetch_error(series, exc) from exc
            last_status = r.status_code
            last_attempts = getattr(r, "kalshi_attempts", []) or []
            if r.status_code == 429:
                retry_after = r.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else 1.5 * (attempt + 1)
                except (TypeError, ValueError):
                    delay = 1.5 * (attempt + 1)
                delay = max(1.0, min(8.0, delay))
                logger.warning("Kalshi rate limited series %s; retrying in %.1fs", series, delay)
                time.sleep(delay)
                continue
            if not r.ok:
                raise _series_fetch_error(series, RuntimeError(f"HTTP {r.status_code}"), response=r)
            return r.json().get("markets", [])
        raise _series_fetch_error(
            series,
            RuntimeError(f"HTTP {last_status or 'unknown'} after retry"),
            attempts=last_attempts,
        )

    series_error_details = []
    for idx, series in enumerate(WEATHER_SERIES, start=1):
        _set_scan_status(_persist=(idx == 1 or idx % 5 == 0), **{
            "current_series": series,
            "series_processed": idx - 1,
            "series_total": len(WEATHER_SERIES),
        })
        try:
            markets = _fetch_series(series)
            all_markets.extend(markets)
        except Exception as e:
            detail = _series_error_detail(series, e)
            cached_markets = _cached_open_markets_for_series(series)
            if not cached_markets:
                for alias in SERIES_FALLBACK_ALIASES.get(series, []):
                    cached_markets = _cached_open_markets_for_series(alias)
                    if cached_markets:
                        detail["fallback_alias"] = alias
                        break
            if cached_markets:
                detail["fallback"] = "cached_open_markets"
                detail["fallback_markets"] = len(cached_markets)
                all_markets.extend(cached_markets)
            series_errors += 1
            series_error_details.append(detail)
            _set_scan_status(series_errors=series_errors, series_error_details=series_error_details[-MAX_SERIES_ERROR_DETAILS:])
            logger.warning(
                "Series fetch failed %s (%d/%d): %s attempts=%s fallback=%s",
                series,
                series_errors,
                len(WEATHER_SERIES),
                detail.get("error"),
                detail.get("attempts") or [],
                detail.get("fallback"),
            )
        time.sleep(0.05)

    if series_errors:
        logger.warning("Scan: %d/%d series had fetch errors", series_errors, len(WEATHER_SERIES))

    seen = set()
    unique = []
    for m in all_markets:
        t = m.get("ticker") or m.get("market_ticker", "")
        if t and t not in seen:
            seen.add(t)
            unique.append(m)
    _set_scan_status(series_errors=series_errors, series_error_details=series_error_details[-MAX_SERIES_ERROR_DETAILS:])
    return unique


def _cached_open_markets_for_series(series: str) -> list:
    try:
        conn = get_conn()
        rows = conn.execute(
            """SELECT ticker, title, market_price, yes_bid, yes_ask, no_bid, no_ask,
                      status, close_time, expiration_time, result, volume,
                      open_interest, raw_json
                 FROM markets
                WHERE category='weather'
                  AND lower(coalesce(status, '')) IN ('open', 'active')
                  AND (close_time IS NULL OR datetime(close_time) > datetime('now'))
                  AND (
                        ticker LIKE ?
                     OR json_extract(raw_json, '$.series_ticker') = ?
                  )
                ORDER BY close_time ASC
                LIMIT 50""",
            (f"{series}%", series),
        ).fetchall()
        conn.close()
    except Exception:
        return []

    markets = []
    for row in rows:
        try:
            market = json.loads(row["raw_json"] or "{}")
        except Exception:
            market = {}
        if not isinstance(market, dict):
            market = {}
        market.update({
            "ticker": market.get("ticker") or row["ticker"],
            "title": market.get("title") or row["title"],
            "market_price": row["market_price"],
            "yes_bid": row["yes_bid"],
            "yes_ask": row["yes_ask"],
            "no_bid": row["no_bid"],
            "no_ask": row["no_ask"],
            "status": market.get("status") or row["status"],
            "close_time": market.get("close_time") or row["close_time"],
            "expiration_time": market.get("expiration_time") or row["expiration_time"],
            "result": market.get("result") or row["result"],
            "volume": market.get("volume") or row["volume"],
            "open_interest": market.get("open_interest") or row["open_interest"],
            "series_ticker": market.get("series_ticker") or series,
        })
        markets.append(market)
    return markets


def diagnose_kalshi_series(series: str = "KXHIGHNY", limit: int = 1, settings: Optional[dict] = None) -> dict:
    """Fetch one Kalshi weather series from every configured base and return raw response diagnostics."""
    if settings is None:
        from app import config as cfg
        settings = cfg.load()
    series = str(series or "KXHIGHNY").strip().upper()
    limit = max(1, min(int(limit or 1), 50))
    params = {"status": "open", "series_ticker": series, "limit": limit}
    headers = {"User-Agent": "sibylla-weather-bot/1.0"}
    attempts = []
    for base in kalshi_api_bases(settings):
        url = f"{base}/markets"
        try:
            response = requests.get(url, params=params, headers=headers, timeout=15)
            body = response.text or ""
            attempt = {
                "base": base,
                "url": response.url,
                "status_code": response.status_code,
                "ok": response.ok,
                "headers": {
                    "content-type": response.headers.get("content-type"),
                    "x-kalshi-cache-hits": response.headers.get("x-kalshi-cache-hits"),
                },
                "raw_text": body[:12000],
            }
            try:
                payload = response.json()
                attempt["json"] = payload
                attempt["markets_returned"] = len(payload.get("markets") or []) if isinstance(payload, dict) else None
            except ValueError:
                attempt["json"] = None
                attempt["markets_returned"] = None
            attempts.append(attempt)
        except requests.RequestException as exc:
            attempts.append({
                "base": base,
                "url": url,
                "exception_type": exc.__class__.__name__,
                "exception": str(exc),
            })
    first_ok = next((attempt for attempt in attempts if attempt.get("ok")), None)
    return {
        "series_ticker": series,
        "limit": limit,
        "configured_bases": kalshi_api_bases(settings),
        "ok": bool(first_ok),
        "first_ok_status": first_ok.get("status_code") if first_ok else None,
        "first_ok_markets_returned": first_ok.get("markets_returned") if first_ok else 0,
        "attempts": attempts,
    }


def _series_fetch_error(series: str, exc: Exception, response=None, attempts: Optional[list] = None) -> RuntimeError:
    raw_attempts = attempts
    if raw_attempts is None and response is not None:
        raw_attempts = getattr(response, "kalshi_attempts", []) or []
    if not raw_attempts:
        raw_attempts = getattr(exc, "kalshi_attempts", []) or []
    error = RuntimeError(str(exc))
    error.series = series
    error.kalshi_attempts = _compact_attempts(raw_attempts or [])
    return error


def _series_error_detail(series: str, exc: Exception) -> dict:
    return {
        "series": series,
        "error": str(exc),
        "attempts": _compact_attempts(getattr(exc, "kalshi_attempts", []) or []),
    }


def _compact_attempts(attempts: list) -> list:
    compact = []
    for attempt in attempts:
        item = {
            "base": attempt.get("base"),
            "url": attempt.get("url"),
            "status_code": attempt.get("status_code"),
            "ok": attempt.get("ok"),
            "exception_type": attempt.get("exception_type"),
            "exception": attempt.get("exception"),
        }
        if attempt.get("body"):
            item["body"] = str(attempt.get("body"))[:400]
        compact.append({k: v for k, v in item.items() if v is not None})
    return compact


def _score_markets_for_priority(markets: list) -> list:
    scored = []
    total = max(len(markets), 1)
    for idx, market in enumerate(markets, start=1):
        ticker = market.get("ticker") or market.get("market_ticker", "")
        quote = quote_from_market(market) if ticker else {}
        result = None
        market_price = quote.get("market_price")
        if ticker and market_price is not None and market_price > 0:
            try:
                result = weather_model.score_market(ticker, market_price, market.get("close_time"), market=market)
            except Exception as exc:
                logger.warning("Priority scoring failed for %s: %s", ticker, exc)
        scored.append({"market": market, "quote": quote, "result": result})
        if idx % 25 == 0 or idx == total:
            _set_scan_status(
                markets_processed=idx,
                current_ticker=ticker,
                progress=min(24, 15 + round(idx / total * 9)),
            )

    return sorted(scored, key=_priority_sort_key)


def _priority_sort_key(item: dict) -> Tuple[int, float, float]:
    market = item.get("market") or {}
    result = item.get("result") or {}
    ticker = market.get("ticker") or market.get("market_ticker") or ""
    hours = result.get("hours_to_close")
    if hours is None:
        hours = weather_model._hours_to_close(ticker, market)
    try:
        hours_value = float(hours)
    except (TypeError, ValueError):
        hours_value = 999.0
    priority = result.get("time_priority") or _time_priority(hours_value)
    priority_rank = {"high": 0, "normal": 1, "low": 2}.get(priority, 1)
    conviction = abs(float(result.get("edge") or 0.0)) * float(result.get("confidence") or 0.0)
    return (priority_rank, hours_value, -conviction)


def _time_priority(hours: float) -> str:
    if hours < 4:
        return "high"
    if hours > 24:
        return "low"
    return "normal"



def _process_market(
    market: dict,
    settings: dict,
    precomputed_result: Optional[dict] = None,
    precomputed_quote: Optional[dict] = None,
) -> bool:
    ticker = market.get("ticker") or market.get("market_ticker", "")
    if not ticker:
        return False

    quote = precomputed_quote or quote_from_market(market)
    yes_bid = quote.get("yes_bid")
    yes_ask = quote.get("yes_ask")
    no_bid = quote.get("no_bid")
    no_ask = quote.get("no_ask")
    market_price = quote.get("market_price")
    if market_price is None or market_price <= 0:
        return False

    conn = get_conn()
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
            market_price,
            yes_bid,
            yes_ask,
            no_bid,
            no_ask,
            "open" if market.get("status") in ("open", "active") else market.get("status", "open"),
            market.get("close_time") or market.get("expiration_time"),
            market.get("expiration_time") or market.get("close_time"),
            settlement_result_from_market(market),
            int(float(quote.get("volume") or 0)),
            int(float(market.get("open_interest") or 0)),
            json.dumps(market),
        ),
    )
    conn.commit()
    conn.close()

    result = precomputed_result
    if result is None:
        result = weather_model.score_market(ticker, market_price, market.get("close_time"), market=market)
    if result is None:
        logger.warning("_process_market: %s -> result is None (precomputed was %s)", ticker, "set" if precomputed_result else "None")
        _expire_pending_alert(ticker, "unsupported_model_input")
        return False

    _record_model_output(ticker, result)

    min_edge = 0.06
    if abs(result["edge"]) < min_edge:
        _expire_pending_alert(ticker, "edge_below_threshold")
        return False

    direction = result["direction"]
    event_ticker = market.get("event_ticker") or ticker.rsplit("-", 1)[0]
    series_ticker = market.get("series_ticker") or re.sub(r'-\d{2}[A-Z]{3}\d{2}.*', '', event_ticker)
    event_has_open_trade = _has_open_trade_for_event(ticker, market)

    brain = weather_brain.evaluate_alert(
        ticker=ticker,
        edge=result["edge"],
        direction=result["direction"],
        market_price=market_price,
        model_prob=result["model_prob"],
        confidence=result["confidence"],
        segment=result["segment"],
        time_bucket=result["time_bucket"],
        phantom_risk={
            "level": result["phantom_risk_level"],
            "score": result["phantom_risk_score"],
            "flags": json.loads(result["phantom_risk_flags"]),
        },
        details={
            "yes_bid": yes_bid,
            "yes_ask": yes_ask,
            "spread": quote.get("spread"),
            "liquidity": quote.get("liquidity"),
            "volume_24h": quote.get("volume_24h"),
            "open_interest": market.get("open_interest"),
        },
    )

    details = {
        **result,
        "brain": brain,
        "analysis": result.get("analysis", ""),
        "market_title": market.get("title") or market.get("subtitle") or ticker,
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_bid": quote.get("no_bid"),
        "no_ask": quote.get("no_ask"),
        "spread": quote.get("spread"),
        "liquidity": quote.get("liquidity"),
        "volume_24h": quote.get("volume_24h"),
        "open_interest": market.get("open_interest"),
        "event_ticker": event_ticker,
        "event_has_open_trade": event_has_open_trade,
        "kalshi_url": f"https://kalshi.com/markets/{series_ticker}/{event_ticker}",
        "rules_primary": market.get("rules_primary"),
        "rules_secondary": market.get("rules_secondary"),
        "yes_sub_title": market.get("yes_sub_title"),
        "no_sub_title": market.get("no_sub_title"),
        "strike_type": market.get("strike_type"),
        "floor_strike": market.get("floor_strike"),
        "cap_strike": market.get("cap_strike"),
        "close_time": market.get("close_time"),
        "expiration_time": market.get("expiration_time"),
    }
    details["recommendation"] = position_sizing.recommend_alert(
        {
            **details,
            "market_ticker": ticker,
            "market_price": market_price,
            "model_prob": result["model_prob"],
            "direction": result["direction"],
            "brain_score": brain["score"],
            "brain_state": brain["state"],
            "phantom_risk_level": result["phantom_risk_level"],
            "details": {
                "brain": brain,
                "event_has_open_trade": event_has_open_trade,
                "hours_to_close": result.get("hours_to_close"),
                "time_priority": result.get("time_priority"),
                "yes_bid": yes_bid,
                "yes_ask": yes_ask,
                "no_bid": quote.get("no_bid"),
                "no_ask": quote.get("no_ask"),
                "spread": quote.get("spread"),
            },
        },
        settings,
    )

    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM alerts WHERE market_ticker = ? AND status = 'pending'",
        (ticker,)
    ).fetchone()

    if existing:
        conn.execute(
            """UPDATE alerts SET
                 edge=?, direction=?, market_price=?, model_prob=?,
                 confidence=?, brain_score=?, brain_state=?,
                 brain_auto_qualified=?, phantom_risk_level=?,
                 phantom_risk_score=?, phantom_risk_flags=?,
                 details=?, updated_at=datetime('now')
               WHERE id=?""",
            (
                result["edge"], result["direction"], market_price,
                result["model_prob"], result["confidence"],
                brain["score"], brain["state"],
                int(brain["auto_qualified"]),
                result["phantom_risk_level"],
                result["phantom_risk_score"],
                result["phantom_risk_flags"],
                json.dumps(details),
                existing["id"],
            ),
        )
        conn.commit()
        conn.close()
        return False

    conn.execute(
        """INSERT INTO alerts
           (market_ticker, status, edge, direction, market_price, model_prob,
            confidence, brain_score, brain_state, brain_auto_qualified,
            phantom_risk_level, phantom_risk_score, phantom_risk_flags,
            details, created_at, updated_at)
           VALUES (?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))""",
        (
            ticker, result["edge"], result["direction"], market_price,
            result["model_prob"], result["confidence"],
            brain["score"], brain["state"],
            int(brain["auto_qualified"]),
            result["phantom_risk_level"],
            result["phantom_risk_score"],
            result["phantom_risk_flags"],
            json.dumps(details),
        ),
    )
    conn.commit()
    conn.close()
    return True


def _expire_pending_alert(ticker: str, reason: str):
    conn = get_conn()
    conn.execute(
        """UPDATE alerts
              SET status='expired',
                  details=json_set(coalesce(details, '{}'), '$.expired_reason', ?),
                  updated_at=datetime('now')
            WHERE market_ticker=? AND status='pending'""",
        (reason, ticker),
    )
    conn.commit()
    conn.close()


def _has_open_trade_for_event(ticker: str, market: dict) -> bool:
    event_ticker = (market or {}).get("event_ticker") or ticker.rsplit("-", 1)[0]
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM trades WHERE status='open' AND market_ticker LIKE ? LIMIT 1",
        (f"{event_ticker}-%",),
    ).fetchone()
    conn.close()
    return row is not None


def _expire_unrefreshed_pending_alerts() -> int:
    from app import config as cfg
    settings = cfg.load()
    expiry_minutes = max(15, int(settings.get("stale_alert_expiry_minutes", 60) or 60))
    conn = get_conn()
    result = conn.execute(
        """UPDATE alerts
              SET status='expired',
                  details=json_set(coalesce(details, '{}'), '$.expired_reason', 'market_not_refreshed_or_unsupported'),
                  updated_at=datetime('now')
            WHERE status='pending'
              AND (
                NOT EXISTS (
                  SELECT 1 FROM markets
                   WHERE markets.ticker = alerts.market_ticker
                     AND datetime(markets.updated_at) >= datetime('now', ?)
                )
              )"""
        ,
        (f"-{expiry_minutes} minutes",),
    )
    expired = result.rowcount
    conn.commit()
    conn.close()
    return expired


def _auto_execute_paper_candidates(settings: dict) -> int:
    """
    In paper mode, let the adaptive auto-entry sampler pick the strongest
    learning trades after a scan while capping same-event concentration.
    """
    from app.services.auto_entry import auto_enter_qualifying_alerts
    result = auto_enter_qualifying_alerts(settings_override=settings)
    created = int(result.get("total_entered") or 0)
    if created:
        logger.info("Auto-created %d paper trades from scan candidates", created)
    return created


def _record_model_output(ticker: str, result: dict):
    conn = get_conn()
    forecast = result.get("forecast") or {}
    conn.execute(
        """INSERT INTO model_outputs
           (market_ticker, category, model_prob, edge, confidence, direction,
            forecast_data, phantom_risk_score, phantom_risk_flags,
            phantom_risk_level, raw_output, created_at)
           VALUES (?, 'weather', ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
        (
            ticker,
            result.get("model_prob"),
            result.get("edge"),
            result.get("confidence"),
            result.get("direction"),
            json.dumps(forecast),
            result.get("phantom_risk_score"),
            result.get("phantom_risk_flags"),
            result.get("phantom_risk_level"),
            json.dumps(result),
        ),
    )
    conn.execute(
        """INSERT INTO forecast_snapshots
           (market_ticker, snapshot_date, forecast_high, forecast_low,
            forecast_precip, model_prob, market_price_at_snapshot, created_at)
           VALUES (?, date('now'), ?, ?, ?, ?, ?, datetime('now'))""",
        (
            ticker,
            forecast.get("high"),
            forecast.get("low"),
            forecast.get("precip_pct"),
            result.get("model_prob"),
            result.get("market_price"),
        ),
    )
    conn.commit()
    conn.close()
