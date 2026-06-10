import json
import re
import sqlite3
import threading
from datetime import datetime, timezone
from fastapi import APIRouter, Body, HTTPException, Query
from typing import Optional

router = APIRouter()
_QUOTE_REFRESH_LOCK = threading.Lock()


@router.get("/alerts")
def list_alerts(
    status: Optional[str] = Query(None),
    limit: int = Query(50),
    offset: int = Query(0),
    refresh: bool = Query(False),
    context: bool = Query(False),
):
    from app.database import get_conn
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    conn = get_conn()
    base_sql = """
        SELECT alerts.*,
               markets.title AS market_title,
               markets.status AS market_status,
               markets.close_time,
               markets.expiration_time,
               markets.yes_bid,
               markets.yes_ask,
               markets.no_bid,
               markets.no_ask,
               markets.updated_at AS market_updated_at,
               markets.raw_json AS market_raw_json
          FROM alerts
          LEFT JOIN markets ON markets.ticker = alerts.market_ticker
    """
    if status:
        if status == "active":
            rows = conn.execute(
                base_sql + """
                 WHERE alerts.status IN ('pending', 'active', 'paper_traded')
                   AND markets.status IN ('open', 'active')
                   AND (markets.close_time IS NULL OR datetime(markets.close_time) > datetime('now'))
                 ORDER BY alerts.updated_at DESC LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                base_sql + " WHERE alerts.status=? ORDER BY alerts.created_at DESC LIMIT ? OFFSET ?",
                (status, limit, offset),
            ).fetchall()
    else:
        rows = conn.execute(
            base_sql + " ORDER BY alerts.created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    if refresh:
        scan_running = False
        try:
            from app.services.scanner import get_scan_status
            scan_running = get_scan_status().get("status") == "running"
        except Exception:
            scan_running = False

        if not scan_running and _QUOTE_REFRESH_LOCK.acquire(blocking=False):
            try:
                try:
                    _refresh_alert_quotes(conn, rows[: min(len(rows), 40)])
                except sqlite3.OperationalError:
                    conn.rollback()
                ids = [r["id"] for r in rows]
                if ids:
                    placeholders = ",".join("?" for _ in ids)
                    rows = conn.execute(
                        base_sql + f" WHERE alerts.id IN ({placeholders}) ORDER BY alerts.updated_at DESC",
                        ids,
                    ).fetchall()
            finally:
                _QUOTE_REFRESH_LOCK.release()
    open_event_tickers = _open_event_tickers(conn, rows)
    conn.close()
    results = []
    for r in rows:
        results.append(_alert_dict(r, include_context=context, open_event_tickers=open_event_tickers))
    return {"alerts": results, "total": len(results)}


@router.get("/alerts/{alert_id}")
def get_alert(alert_id: int):
    from app.database import get_conn
    conn = get_conn()
    row = conn.execute(
        """SELECT alerts.*,
                  markets.title AS market_title,
                  markets.status AS market_status,
                  markets.close_time,
                  markets.expiration_time,
                  markets.yes_bid,
                  markets.yes_ask,
                  markets.no_bid,
                  markets.no_ask,
                  markets.updated_at AS market_updated_at,
                  markets.raw_json AS market_raw_json
             FROM alerts
             LEFT JOIN markets ON markets.ticker = alerts.market_ticker
            WHERE alerts.id=?""",
        (alert_id,),
    ).fetchone()
    conn.close()
    if row is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Alert not found")
    from app.database import get_conn
    conn = get_conn()
    open_event_tickers = _open_event_tickers(conn, [row])
    conn.close()
    return _alert_dict(row, include_context=True, open_event_tickers=open_event_tickers, recalculate_sizing=True)


def _alert_dict(row, include_context: bool = False, open_event_tickers: Optional[set] = None, recalculate_sizing: bool = False):
    d = dict(row)
    try:
        d["details"] = json.loads(d.get("details") or "{}")
    except Exception:
        d["details"] = {}

    raw = d.pop("market_raw_json", None)
    try:
        market_raw = json.loads(raw or "{}")
    except Exception:
        market_raw = {}

    event_ticker = _event_ticker_from(d, d["details"], market_raw)
    d["event_ticker"] = event_ticker
    event_open = bool(d["details"].get("event_has_open_trade"))
    if open_event_tickers is not None:
        event_open = event_ticker in open_event_tickers
    d["event_has_open_trade"] = event_open
    d["details"]["event_has_open_trade"] = event_open
    series_ticker = market_raw.get("series_ticker") or re.sub(r'-\d{2}[A-Z]{3}\d{2}.*', '', event_ticker)
    d["kalshi_url"] = f"https://kalshi.com/markets/{series_ticker}/{event_ticker}"
    d["rules_primary"] = d["details"].get("rules_primary") or market_raw.get("rules_primary")
    d["rules_secondary"] = d["details"].get("rules_secondary") or market_raw.get("rules_secondary")
    d["yes_sub_title"] = d["details"].get("yes_sub_title") or market_raw.get("yes_sub_title")
    d["no_sub_title"] = d["details"].get("no_sub_title") or market_raw.get("no_sub_title")
    d["strike_type"] = d["details"].get("strike_type") or market_raw.get("strike_type")
    d["floor_strike"] = d["details"].get("floor_strike") or market_raw.get("floor_strike")
    d["cap_strike"] = d["details"].get("cap_strike") or market_raw.get("cap_strike")
    d["yes_bid"] = d.get("yes_bid") if d.get("yes_bid") is not None else d["details"].get("yes_bid")
    d["yes_ask"] = d.get("yes_ask") if d.get("yes_ask") is not None else d["details"].get("yes_ask")
    d["no_bid"] = d.get("no_bid") if d.get("no_bid") is not None else d["details"].get("no_bid")
    d["no_ask"] = d.get("no_ask") if d.get("no_ask") is not None else d["details"].get("no_ask")
    d["spread"] = d["details"].get("spread")
    d["liquidity"] = d["details"].get("liquidity")
    d["volume_24h"] = d["details"].get("volume_24h")
    d["current_conditions"] = d["details"].get("current_conditions") or {}
    d["settlement_station"] = d["details"].get("settlement_station") or d["current_conditions"].get("settlement_station")
    d["details"]["settlement_station"] = d["settlement_station"]
    d["forecast_sources"] = d["details"].get("forecast_sources") or (d["details"].get("forecast") or {}).get("forecast_sources") or []
    d["active_weather_events"] = d["details"].get("active_weather_events") or []
    d["hours_to_close"] = d["details"].get("hours_to_close")
    d["time_priority"] = d["details"].get("time_priority")
    rec = d["details"].get("recommendation") or {}
    duplicate_blocker = "event already has an open paper trade"
    if (
        not isinstance(rec, dict)
        or "contracts" not in rec
        or "blockers" not in rec
        or (not event_open and duplicate_blocker in (rec.get("blockers") or []))
        or rec.get("time_priority") != d["details"].get("time_priority")
    ):
        recalculate_sizing = True

    if recalculate_sizing:
        from app import config as cfg
        from app.services.position_sizing import recommend_alert
        d["details"]["recommendation"] = recommend_alert(d, cfg.load())
    d["recommendation"] = d["details"].get("recommendation") or {}
    if include_context:
        _attach_analysis_context(d)
    return d


def _event_ticker_from(row: dict, details: dict, market_raw: dict) -> str:
    return market_raw.get("event_ticker") or details.get("event_ticker") or row["market_ticker"].rsplit("-", 1)[0]


def _open_event_tickers(conn, rows) -> set:
    event_tickers = set()
    for row in rows:
        item = dict(row)
        try:
            details = json.loads(item.get("details") or "{}")
        except Exception:
            details = {}
        try:
            market_raw = json.loads(item.get("market_raw_json") or "{}")
        except Exception:
            market_raw = {}
        event_tickers.add(_event_ticker_from(item, details, market_raw))

    if not event_tickers:
        return set()

    open_rows = conn.execute(
        "SELECT market_ticker FROM trades WHERE status='open'"
    ).fetchall()
    open_trade_events = {dict(r)["market_ticker"].rsplit("-", 1)[0] for r in open_rows}
    return event_tickers.intersection(open_trade_events)


def _attach_analysis_context(alert: dict) -> None:
    try:
        from app.database import get_conn
        from app.services import adaptive_policy

        details = alert.get("details") or {}
        brain = details.get("brain") or {}
        segment_key = (
            brain.get("segment")
            or details.get("segment_key")
            or f"{details.get('segment') or 'weather_all'}:{details.get('time_bucket') or 'all'}"
        )
        learning = adaptive_policy.get_segment_learning(segment_key)

        conn = get_conn()
        latest_snapshot = conn.execute(
            """SELECT snapshot_date, forecast_high, forecast_low, forecast_precip,
                      actual_high, actual_low, actual_precip, resolved,
                      model_prob, market_price_at_snapshot, created_at
                 FROM forecast_snapshots
                WHERE market_ticker=?
                ORDER BY created_at DESC, id DESC
                LIMIT 1""",
            (alert["market_ticker"],),
        ).fetchone()
        latest_resolved = conn.execute(
            """SELECT snapshot_date, forecast_high, forecast_low, forecast_precip,
                      actual_high, actual_low, actual_precip, resolved,
                      model_prob, market_price_at_snapshot, created_at
                 FROM forecast_snapshots
                WHERE market_ticker=?
                  AND resolved=1
                ORDER BY created_at DESC, id DESC
                LIMIT 1""",
            (alert["market_ticker"],),
        ).fetchone()
        resolution_count = conn.execute(
            """SELECT COUNT(*)
                 FROM forecast_snapshots
                WHERE market_ticker=?
                  AND resolved=1""",
            (alert["market_ticker"],),
        ).fetchone()[0]
        event_prefix = alert.get("event_ticker") or alert["market_ticker"].rsplit("-", 1)[0]
        event_rows = conn.execute(
            """SELECT alerts.id, alerts.market_ticker, alerts.direction, alerts.market_price,
                      alerts.model_prob, alerts.edge, alerts.details,
                      markets.title AS market_title
                 FROM alerts
                 LEFT JOIN markets ON markets.ticker = alerts.market_ticker
                WHERE alerts.market_ticker LIKE ?
                ORDER BY alerts.updated_at DESC
                LIMIT 40""",
            (f"{event_prefix}-%",),
        ).fetchall()
        trade = conn.execute(
            """SELECT id, direction, entry_price, contracts, stop_loss_price,
                      take_profit_price, status, entry_time
                 FROM trades
                WHERE alert_id=?
                ORDER BY entry_time DESC, id DESC
                LIMIT 1""",
            (alert["id"],),
        ).fetchone()
        if trade is None:
            trade = conn.execute(
                """SELECT id, direction, entry_price, contracts, stop_loss_price,
                          take_profit_price, status, entry_time
                     FROM trades
                    WHERE market_ticker=?
                    ORDER BY entry_time DESC, id DESC
                    LIMIT 1""",
                (alert["market_ticker"],),
            ).fetchone()
        conn.close()

        context = {
            "segment_key": segment_key,
            "segment_learning": learning,
            "latest_snapshot": dict(latest_snapshot) if latest_snapshot else None,
            "latest_resolved_snapshot": dict(latest_resolved) if latest_resolved else None,
            "resolved_snapshot_count": resolution_count,
            "event_best_option": _best_event_option(event_rows),
        }
        details["analysis_context"] = context
        if trade:
            alert["paper_trade"] = dict(trade)
        else:
            alert["paper_trade"] = None
        alert["details"] = details
    except Exception:
        alert.setdefault("details", {}).setdefault("analysis_context", {})


def _best_event_option(rows) -> Optional[dict]:
    best = None
    for row in rows:
        item = dict(row)
        try:
            details = json.loads(item.get("details") or "{}")
        except Exception:
            details = {}
        rec = details.get("recommendation") or {}
        direction = item.get("direction") or "yes"
        market_price = float(item.get("market_price") or 0.0)
        model_prob = float(item.get("model_prob") or 0.0)
        entry = 1.0 - market_price if direction == "no" else market_price
        side_prob = 1.0 - model_prob if direction == "no" else model_prob
        side_edge = side_prob - entry
        ev = float(rec.get("expected_value_per_contract") if rec.get("expected_value_per_contract") is not None else side_edge)
        if best is None or ev > best["expected_value_per_contract"]:
            best = {
                "alert_id": item.get("id"),
                "market_ticker": item.get("market_ticker"),
                "market_title": item.get("market_title"),
                "direction": direction,
                "entry_price": round(entry, 4),
                "model_side_probability": round(side_prob, 4),
                "side_edge": round(side_edge, 4),
                "expected_value_per_contract": round(ev, 4),
                "contracts": int(rec.get("contracts") or 0),
            }
    return best


def _refresh_alert_quotes(conn, rows, force: bool = False) -> set[int]:
    from app import config as cfg
    from app.services import weather_brain, weather_model, position_sizing
    from app.services.kalshi_client import get_market, quote_from_market, settlement_result_from_market

    settings = cfg.load()
    refreshed_ids: set[int] = set()
    for row in rows:
        alert = dict(row)
        if alert.get("status") not in ("pending", "paper_traded"):
            continue
        if not force and _fresh_enough(alert.get("market_updated_at"), seconds=25):
            continue
        ticker = alert["market_ticker"]
        market = get_market(ticker)
        if not market:
            continue
        quote = quote_from_market(market)
        if quote.get("market_price") is None:
            continue
        conn.execute(
            """UPDATE markets SET
                 title=?, market_price=?, yes_bid=?, yes_ask=?, no_bid=?, no_ask=?,
                 status=?, close_time=?, expiration_time=?, result=?, raw_json=?, updated_at=datetime('now')
               WHERE ticker=?""",
            (
                market.get("title") or market.get("subtitle") or ticker,
                quote.get("market_price"),
                quote.get("yes_bid"),
                quote.get("yes_ask"),
                quote.get("no_bid"),
                quote.get("no_ask"),
                "open" if market.get("status") in ("open", "active") else market.get("status", "open"),
                market.get("close_time") or market.get("expiration_time"),
                market.get("expiration_time") or market.get("close_time"),
                settlement_result_from_market(market),
                json.dumps(market),
                ticker,
            ),
        )
        try:
            details = json.loads(alert.get("details") or "{}")
        except Exception:
            details = {}
        event_ticker = market.get("event_ticker") or details.get("event_ticker") or ticker.rsplit("-", 1)[0]
        event_has_open_trade = conn.execute(
            "SELECT id FROM trades WHERE status='open' AND market_ticker LIKE ? LIMIT 1",
            (f"{event_ticker}-%",),
        ).fetchone() is not None

        market_price = float(quote["market_price"])
        model_prob = float(alert.get("model_prob") or details.get("model_prob") or 0.0)
        edge = round(model_prob - market_price, 4)
        direction = "yes" if edge > 0 else "no"
        phantom = weather_model._phantom_risk_assessment(
            edge,
            model_prob,
            market_price,
            float(alert.get("confidence") or details.get("confidence") or 0.0),
        )
        brain = weather_brain.evaluate_alert(
            ticker=ticker,
            edge=edge,
            direction=direction,
            market_price=market_price,
            model_prob=model_prob,
            confidence=float(alert.get("confidence") or details.get("confidence") or 0.0),
            segment=details.get("segment") or "weather_all",
            time_bucket=details.get("time_bucket") or "all",
            phantom_risk=phantom,
            details=details,
        )

        details.update({
            "market_price": market_price,
            "edge": edge,
            "direction": direction,
            "brain": brain,
            "yes_bid": quote.get("yes_bid"),
            "yes_ask": quote.get("yes_ask"),
            "no_bid": quote.get("no_bid"),
            "no_ask": quote.get("no_ask"),
            "spread": quote.get("spread"),
            "liquidity": quote.get("liquidity"),
            "volume_24h": quote.get("volume_24h"),
            "event_ticker": event_ticker,
            "event_has_open_trade": event_has_open_trade,
            "live_quote_at": market.get("last_price_time") or market.get("updated_time"),
            "phantom_risk_score": phantom["score"],
            "phantom_risk_flags": json.dumps(phantom["flags"]),
            "phantom_risk_level": phantom["level"],
        })
        try:
            details["current_conditions"] = weather_model.current_conditions_for_ticker(ticker)
        except Exception:
            pass
        details["recommendation"] = position_sizing.recommend_alert(
            {
                **alert,
                "market_price": market_price,
                "model_prob": model_prob,
                "edge": edge,
                "direction": direction,
                "brain_score": brain["score"],
                "brain_state": brain["state"],
                "phantom_risk_level": phantom["level"],
                "details": details,
            },
            settings,
        )

        conn.execute(
            """UPDATE alerts SET
                 edge=?, direction=?, market_price=?, brain_score=?, brain_state=?,
                 brain_auto_qualified=?, phantom_risk_level=?, phantom_risk_score=?,
                 phantom_risk_flags=?, details=?, updated_at=datetime('now')
               WHERE id=?""",
            (
                edge,
                direction,
                market_price,
                brain["score"],
                brain["state"],
                int(brain["auto_qualified"]),
                phantom["level"],
                phantom["score"],
                json.dumps(phantom["flags"]),
                json.dumps(details),
                alert["id"],
            ),
        )
        refreshed_ids.add(int(alert["id"]))
    conn.commit()
    return refreshed_ids


def _fresh_enough(value, seconds: int) -> bool:
    if not value:
        return False
    try:
        raw = str(value)
        normalized = raw.replace(" ", "T")
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        elif "+" not in normalized and normalized.count("-") >= 2:
            normalized = normalized + "+00:00"
        ts = datetime.fromisoformat(normalized)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds() < seconds
    except Exception:
        return False


@router.post("/alerts/{alert_id}/expire")
def expire_alert(alert_id: int):
    from app.database import get_conn
    conn = get_conn()
    conn.execute(
        "UPDATE alerts SET status='expired', updated_at=datetime('now') WHERE id=?",
        (alert_id,),
    )
    conn.commit()
    conn.close()
    return {"status": "expired", "alert_id": alert_id}


@router.post("/alerts/{alert_id}/skip")
def skip_alert(alert_id: int):
    from app.database import get_conn
    conn = get_conn()
    conn.execute(
        """UPDATE alerts
              SET status='skipped',
                  details=json_set(coalesce(details, '{}'), '$.skipped_at', datetime('now')),
                  updated_at=datetime('now')
            WHERE id=?""",
        (alert_id,),
    )
    conn.commit()
    conn.close()
    return {"status": "skipped", "alert_id": alert_id}


@router.post("/alerts/{alert_id}/deny")
def deny_alert(alert_id: int):
    from app.database import get_conn
    conn = get_conn()
    conn.execute(
        """UPDATE alerts
              SET status='expired',
                  details=json_set(coalesce(details, '{}'), '$.expired_reason', 'manual_deny'),
                  updated_at=datetime('now')
            WHERE id=?""",
        (alert_id,),
    )
    conn.commit()
    conn.close()
    return {"status": "expired", "alert_id": alert_id, "reason": "manual_deny"}


@router.post("/alerts/{alert_id}/paper-trade")
def paper_trade_alert(alert_id: int, payload: Optional[dict] = Body(default=None)):
    from app import config as cfg
    from app.database import get_conn
    from app.services.order_manager import place_order

    settings = cfg.load()
    if not settings.get("paper_trading", True):
        raise HTTPException(status_code=400, detail="Paper trading is disabled in settings")

    payload = payload or {}
    explicit_exit_pct = "stop_loss_pct" in payload or "take_profit_pct" in payload
    stop_loss_pct = payload.get("stop_loss_pct")
    if stop_loss_pct is None:
        stop_loss_pct = settings.get("stop_loss_pct", 0.50)
    take_profit_pct = payload.get("take_profit_pct")
    if take_profit_pct is None:
        take_profit_pct = settings.get("take_profit_pct", 0.50)

    conn = get_conn()
    row = _load_paper_trade_alert(conn, alert_id)
    if row is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Alert not found")
    requested_direction = (payload.get("direction") or row["direction"] or "").lower()

    _QUOTE_REFRESH_LOCK.acquire()
    try:
        refreshed_ids = _refresh_alert_quotes(conn, [row], force=True)
    finally:
        _QUOTE_REFRESH_LOCK.release()
    if alert_id not in refreshed_ids:
        conn.close()
        raise HTTPException(
            status_code=409,
            detail="Could not refresh the live Kalshi quote for this paper trade. Try again in a moment.",
        )

    row = _load_paper_trade_alert(conn, alert_id)
    if row is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Alert not found after quote refresh")

    alert = dict(row)
    try:
        details = json.loads(alert.get("details") or "{}")
    except Exception:
        details = {}
    alert["yes_bid"] = alert.get("live_yes_bid")
    alert["yes_ask"] = alert.get("live_yes_ask")
    alert["no_bid"] = alert.get("live_no_bid")
    alert["no_ask"] = alert.get("live_no_ask")

    refreshed_direction = (alert.get("direction") or "").lower()
    if requested_direction in ("yes", "no") and refreshed_direction != requested_direction:
        conn.close()
        raise HTTPException(
            status_code=409,
            detail=f"Live Kalshi refresh changed this signal from {requested_direction.upper()} to {refreshed_direction.upper()}; refresh the page before paper trading it.",
        )

    market_status = (alert.get("market_status") or "").lower()
    if market_status not in ("open", "active"):
        conn.close()
        raise HTTPException(status_code=400, detail=f"Market is {market_status or 'not open'}, not tradable")
    if not _fresh_enough(alert.get("market_updated_at"), seconds=20) or not _side_entry_quote_available(alert, details):
        conn.close()
        raise HTTPException(
            status_code=409,
            detail="Live Kalshi bid/ask is not fresh enough to paper-fill this trade.",
        )

    from app.services.position_sizing import recommend_alert
    recommendation = recommend_alert({**alert, "details": details}, settings)
    learning_override = bool(payload.get("learning_override"))
    recommended_contracts = int(recommendation.get("contracts") or 0)
    requested_contracts = payload.get("contracts")
    contracts = int(requested_contracts) if requested_contracts is not None else recommended_contracts
    max_contracts = max(1, min(5, int(settings.get("max_contracts_per_trade") or 5)))
    if learning_override:
        _validate_learning_override(alert, recommendation)
        contracts = 1
    elif recommended_contracts < 1:
        raise HTTPException(
            status_code=400,
            detail="Sizing is wait-only. Use a 1-contract paper trade only when edge and expected value are positive.",
        )
    contracts = max(1, min(max_contracts, contracts))

    if alert["status"] != "pending":
        conn.close()
        raise HTTPException(status_code=400, detail=f"Alert is {alert['status']}, not pending")

    try:
        raw = json.loads(alert.get("market_raw_json") or "{}")
    except Exception:
        raw = {}
    event_ticker = raw.get("event_ticker") or alert["market_ticker"].rsplit("-", 1)[0]

    conn.close()

    from app.services.order_manager import recommendation_exit_args
    result = place_order(
        market_ticker=alert["market_ticker"],
        direction=alert["direction"],
        entry_price=_paper_entry_yes_price(alert, details, recommendation),
        alert_id=alert_id,
        contracts=contracts,
        fill_context=recommendation,
        **recommendation_exit_args(
            recommendation,
            float(stop_loss_pct),
            float(take_profit_pct),
            allow_default_pct=explicit_exit_pct,
        ),
    )

    conn = get_conn()
    conn.execute(
        """UPDATE alerts
              SET status='paper_traded',
                  details=json_set(coalesce(details, '{}'), '$.paper_trade_action', 'manual_opened'),
                  updated_at=datetime('now')
            WHERE id=?""",
        (alert_id,),
    )
    conn.commit()
    conn.close()

    return {"status": "paper_traded", "alert_id": alert_id, **result}


def _load_paper_trade_alert(conn, alert_id: int):
    return conn.execute(
        """SELECT alerts.*,
                  markets.status AS market_status,
                  markets.close_time AS market_close_time,
                  markets.updated_at AS market_updated_at,
                  markets.raw_json AS market_raw_json,
                  markets.yes_bid AS live_yes_bid,
                  markets.yes_ask AS live_yes_ask,
                  markets.no_bid AS live_no_bid,
                  markets.no_ask AS live_no_ask
             FROM alerts
             LEFT JOIN markets ON markets.ticker = alerts.market_ticker
            WHERE alerts.id=?""",
        (alert_id,),
    ).fetchone()


def _validate_learning_override(alert: dict, recommendation: dict) -> None:
    side_edge = _first_number(recommendation.get("side_edge"))
    expected_value = _first_number(recommendation.get("expected_value_per_contract"))
    phantom = (alert.get("phantom_risk_level") or "none").lower()
    blockers = []
    rec_blockers = [str(b) for b in (recommendation.get("blockers") or []) if b]
    if rec_blockers:
        blockers.extend(rec_blockers)
    if side_edge is None or side_edge <= 0:
        blockers.append("no positive edge")
    if expected_value is None or expected_value <= 0:
        blockers.append("negative expected value")
    if phantom == "high":
        blockers.append("high forecast disagreement risk")
    if blockers:
        raise HTTPException(
            status_code=400,
            detail="Paper trade blocked: " + "; ".join(blockers),
        )


def _first_number(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _paper_entry_yes_price(alert: dict, details: dict, recommendation: dict) -> float:
    """Return the YES-coordinate entry price for the side being bought.

    The trades table stores prices in YES coordinates because settlement,
    stop-loss, and CLV calculations are all expressed against the YES line. A
    BUY NO paper fill should therefore use 1 - NO ask, not the YES midpoint.
    """
    direction = (alert.get("direction") or "yes").lower()
    rec_yes = recommendation.get("limit_price_yes")
    if rec_yes is not None:
        return round(_bounded_price(rec_yes), 4)

    if direction == "no":
        no_ask = _first_price(alert.get("live_no_ask"), alert.get("no_ask"), details.get("no_ask"))
        if no_ask is not None:
            return round(_bounded_price(1.0 - no_ask), 4)
        yes_bid = _first_price(alert.get("live_yes_bid"), alert.get("yes_bid"), details.get("yes_bid"))
        if yes_bid is not None:
            return round(_bounded_price(yes_bid), 4)
    else:
        yes_ask = _first_price(alert.get("live_yes_ask"), alert.get("yes_ask"), details.get("yes_ask"))
        if yes_ask is not None:
            return round(_bounded_price(yes_ask), 4)
        no_bid = _first_price(alert.get("live_no_bid"), alert.get("no_bid"), details.get("no_bid"))
        if no_bid is not None:
            return round(_bounded_price(1.0 - no_bid), 4)

    return round(_bounded_price(alert.get("market_price")), 4)


def _side_entry_quote_available(alert: dict, details: dict) -> bool:
    direction = (alert.get("direction") or "yes").lower()
    if direction == "no":
        return (
            _first_price(alert.get("live_no_ask"), alert.get("no_ask"), details.get("no_ask")) is not None
            or _first_price(alert.get("live_yes_bid"), alert.get("yes_bid"), details.get("yes_bid")) is not None
        )
    return (
        _first_price(alert.get("live_yes_ask"), alert.get("yes_ask"), details.get("yes_ask")) is not None
        or _first_price(alert.get("live_no_bid"), alert.get("no_bid"), details.get("no_bid")) is not None
    )


def _first_price(*values) -> Optional[float]:
    for value in values:
        if value is None:
            continue
        try:
            return _bounded_price(value)
        except (TypeError, ValueError):
            continue
    return None


def _bounded_price(value) -> float:
    return max(0.01, min(0.99, float(value)))


@router.post("/alerts/cleanup")
def cleanup_stale_data():
    """
    Run the same status-only alert cleanup and bounded history retention used by
    the scheduler. Alert rows are not deleted.
    Returns counts of what was cleaned.
    """
    from app.services.scheduler import (
        _cleanup_old_model_history,
        _expire_closed_market_alerts,
        _expire_stale_open_trades,
    )

    return {
        "expired_closed_market_alerts": _expire_closed_market_alerts(),
        "deleted_model_history": _cleanup_old_model_history(),
        "expired_stale_open_trades": _expire_stale_open_trades(),
    }
