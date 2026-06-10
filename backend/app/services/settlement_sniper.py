"""Settlement sniper: trade markets that are already mathematically decided.

The cleanest information edge in weather markets: once the day's observed
high has exceeded a bracket's cap, the final daily high can only go higher
— YES is impossible, the market settles NO with certainty. Same for lows
that have already undercut a bracket's floor (the final low can only go
lower). If the market still prices the dead side at a real premium, that
premium is nearly riskless.

Only MATHEMATICAL certainties fire (monotonicity of running max/min).
"The high probably won't reach the bracket" is a forecast, not a fact,
and is explicitly out of scope.

Safety posture:
  - Decisions require the observation to clear the strike by a margin
    (sniper_margin_f, default 1.5F) to absorb grid-vs-station skew and
    pending NWS revisions. Observation = max/min of Open-Meteo hourly
    extremes and the NWS settlement-station current temp.
  - Entries are 1-contract and tagged learning_mode='sniper' in the alert
    details, so they are held out of strategy learning exactly like
    explore trades until the slice proves itself.
  - Paper-only unless sniper_live_enabled (default false, owner decision).
  - Capped open count (sniper_max_open), one position per market, every
    entry audited.

Runs from the order-monitor job (1-minute cadence), so mispricings get
caught between 15-minute scans — this is what the realtime feed is for.
"""
import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_BRACKET_RE = re.compile(r"-B([\d.]+)$")
_THRESHOLD_RE = re.compile(r"-T([\d.]+)$")
_EVENT_DATE_RE = re.compile(r"-(\d\d)([A-Z]{3})(\d\d)-")
_MONTHS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
           "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}


def _get_conn():
    from app.database import get_conn
    return get_conn()


def event_date_from_ticker(ticker: str) -> Optional[str]:
    m = _EVENT_DATE_RE.search((ticker or "").upper())
    if not m:
        return None
    yy, mon, dd = m.groups()
    month = _MONTHS.get(mon)
    if not month:
        return None
    return f"20{yy}-{month:02d}-{int(dd):02d}"


def strikes_from_ticker(ticker: str, market_raw: Optional[dict] = None) -> Optional[dict]:
    """{'kind': 'bracket'|'threshold', 'floor': x, 'cap': y} in YES terms."""
    tu = (ticker or "").upper()
    raw = market_raw or {}
    m = _BRACKET_RE.search(tu)
    if m:
        mid = float(m.group(1))
        floor = raw.get("floor_strike")
        cap = raw.get("cap_strike")
        if floor is None or cap is None:
            floor, cap = mid - 1.0, mid + 1.0  # standard 2F bracket around the half-degree mid
        return {"kind": "bracket", "floor": float(floor), "cap": float(cap)}
    m = _THRESHOLD_RE.search(tu)
    if m:
        strike = float(m.group(1))
        stype = (raw.get("strike_type") or "greater").lower()
        return {"kind": "threshold", "strike": strike, "strike_type": stype}
    return None


def decide_market(
    temp_kind: str,
    strikes: dict,
    observed_high: Optional[float],
    observed_low: Optional[float],
    margin: float,
) -> Optional[str]:
    """Return 'yes' / 'no' when the market is mathematically decided, else None.

    Monotonicity facts only: the day's final high >= observed high so far;
    the day's final low <= observed low so far.
    """
    if temp_kind == "high":
        obs = observed_high
        if obs is None:
            return None
        if strikes["kind"] == "bracket":
            if obs >= strikes["cap"] + margin:
                return "no"          # high already above the bracket: YES impossible
            return None              # high could still rise INTO or past the bracket
        stype = strikes.get("strike_type", "greater")
        if stype == "greater":
            if obs > strikes["strike"] + margin:
                return "yes"         # threshold already exceeded: YES certain
            return None
        if stype == "less":
            if obs > strikes["strike"] + margin:
                return "no"          # high already above 'less-than' strike
            return None
        return None

    if temp_kind == "low":
        obs = observed_low
        if obs is None:
            return None
        if strikes["kind"] == "bracket":
            if obs <= strikes["floor"] - margin:
                return "no"          # low already below the bracket: YES impossible
            return None
        stype = strikes.get("strike_type", "greater")
        if stype == "greater":
            if obs < strikes["strike"] - margin:
                return "no"          # low already below 'greater-than' strike: YES impossible
            return None
        if stype == "less":
            if obs < strikes["strike"] - margin:
                return "yes"
            return None
    return None


def _observed_for_ticker(ticker: str) -> dict:
    """Best observation for the settlement city: Open-Meteo hourly extremes,
    sharpened by the NWS settlement-station current temp when available."""
    from app.services import weather_model
    from app.services.intraday_temps import get_observed_extremes

    code = weather_model._city_code_from_ticker(ticker)
    if not code:
        return {"available": False}
    station_info = weather_model.settlement_station_info_for_ticker(ticker) or {}
    lat, lon = station_info.get("coords") or weather_model.CITY_COORDS.get(code, (None, None))
    if lat is None:
        return {"available": False}
    target_date = event_date_from_ticker(ticker)
    if not target_date:
        return {"available": False}

    obs = get_observed_extremes(lat, lon, target_date)
    high = obs.get("observed_high") if obs.get("available") else None
    low = obs.get("observed_low") if obs.get("available") else None

    # Station current temp is settlement-grade; running extremes only ever
    # tighten with more information (max for highs, min for lows). Blend it
    # ONLY when the city-local calendar day still IS the event day — a
    # next-day temp would fabricate a false certainty.
    try:
        if obs.get("local_date") == target_date:
            current = weather_model.current_conditions_for_ticker(ticker)
            station_temp = current.get("temperature")
            if station_temp is not None:
                high = station_temp if high is None else max(high, station_temp)
                low = station_temp if low is None else min(low, station_temp)
    except Exception:
        pass

    return {
        "available": high is not None or low is not None,
        "observed_high": high,
        "observed_low": low,
        "local_hour": obs.get("local_hour"),
        "target_date": target_date,
    }


def run_sniper_scan() -> dict:
    """Find decided-but-mispriced markets among those closing soon; enter paper."""
    from app import config as cfg

    settings = cfg.load()
    if not settings.get("sniper_enabled", True):
        return {"skipped": True, "reason": "sniper disabled"}
    from app.services.risk import kill_switch_active
    if kill_switch_active(settings):
        return {"skipped": True, "reason": "kill switch active"}
    if not settings.get("paper_trading", True) and not settings.get("sniper_live_enabled", False):
        return {"skipped": True, "reason": "live mode but sniper_live_enabled=false"}

    margin = float(settings.get("sniper_margin_f", 1.5) or 1.5)
    min_edge = float(settings.get("sniper_min_edge_cents", 5) or 5) / 100.0
    max_open = int(settings.get("sniper_max_open", 20) or 20)

    conn = _get_conn()
    try:
        open_snipes = conn.execute(
            """SELECT COUNT(*) c FROM trades t JOIN alerts a ON a.id = t.alert_id
                WHERE t.status='open'
                  AND json_extract(a.details, '$.learning_mode') = 'sniper'"""
        ).fetchone()["c"]
        open_tickers = {r["market_ticker"] for r in conn.execute(
            "SELECT DISTINCT market_ticker FROM trades WHERE status='open'"
        ).fetchall()}
        candidates = conn.execute(
            """SELECT ticker, yes_bid, yes_ask, raw_json, close_time FROM markets
                WHERE status IN ('open', 'active')
                  AND (ticker LIKE 'KXHIGH%' OR ticker LIKE 'KXLOW%')
                  AND close_time IS NOT NULL
                  AND datetime(close_time) > datetime('now')
                  AND datetime(close_time) <= datetime('now', '+16 hours')"""
        ).fetchall()
    finally:
        conn.close()

    if open_snipes >= max_open:
        return {"checked": 0, "skipped": True, "reason": f"sniper book at cap {open_snipes}/{max_open}"}

    from app.services.realtime import feed

    checked = entered = 0
    opportunities = []
    obs_cache: dict = {}
    for mkt in candidates:
        ticker = mkt["ticker"]
        if ticker in open_tickers:
            continue
        raw = {}
        try:
            raw = json.loads(mkt["raw_json"] or "{}")
        except (TypeError, ValueError):
            pass
        strikes = strikes_from_ticker(ticker, raw)
        if not strikes:
            continue
        temp_kind = "low" if "LOW" in ticker.upper() else "high"

        # One observation fetch per (city, date) — cached within this scan.
        cache_key = (ticker.upper().split("-")[0], event_date_from_ticker(ticker))
        if cache_key not in obs_cache:
            obs_cache[cache_key] = _observed_for_ticker(ticker)
        obs = obs_cache[cache_key]
        if not obs.get("available"):
            continue
        checked += 1

        verdict = decide_market(temp_kind, strikes, obs.get("observed_high"),
                                obs.get("observed_low"), margin)
        if verdict is None:
            continue

        # Freshest quote: WS feed first, scan marks fallback.
        live = feed.quotes.get(ticker) or {}
        yes_bid = live.get("yes_bid", mkt["yes_bid"])
        yes_ask = live.get("yes_ask", mkt["yes_ask"])
        if verdict == "no":
            # NO will settle worth 1. Buy NO at no_ask = 1 - yes_bid; profit = yes_bid.
            if yes_bid is None or yes_bid < min_edge:
                continue
            direction, entry_yes, edge = "no", float(yes_bid), float(yes_bid)
        else:
            # YES will settle worth 1. Buy YES at yes_ask; profit = 1 - yes_ask.
            if yes_ask is None or (1.0 - yes_ask) < min_edge:
                continue
            direction, entry_yes, edge = "yes", float(yes_ask), round(1.0 - float(yes_ask), 4)

        opportunity = {
            "ticker": ticker, "verdict": verdict, "direction": direction,
            "edge": round(edge, 4), "entry_yes": entry_yes,
            "observed_high": obs.get("observed_high"), "observed_low": obs.get("observed_low"),
            "strikes": strikes, "margin": margin,
        }
        opportunities.append(opportunity)

        if open_snipes + entered >= max_open:
            continue
        try:
            entered += _enter_snipe(opportunity)
        except Exception as exc:
            logger.error("Sniper entry failed for %s: %s", ticker, exc)

    if opportunities:
        logger.info("Sniper: %d decided+mispriced of %d observable (%d entered)",
                    len(opportunities), checked, entered)
    return {"checked": checked, "opportunities": opportunities, "entered": entered}


def _enter_snipe(opp: dict) -> int:
    """Open a 1-contract paper trade for a decided market, with its own alert."""
    from app.services.audit import audit
    from app.services.order_manager import place_order

    conn = _get_conn()
    try:
        cur = conn.execute(
            """INSERT INTO alerts (market_ticker, status, direction, market_price,
                                   model_prob, confidence, details)
               VALUES (?, 'paper_traded', ?, ?, ?, 0.99, ?)""",
            (
                opp["ticker"], opp["direction"], opp["entry_yes"],
                0.0 if opp["verdict"] == "no" else 1.0,
                json.dumps({
                    "learning_mode": "sniper",
                    "sniper": {
                        "verdict": opp["verdict"],
                        "observed_high": opp["observed_high"],
                        "observed_low": opp["observed_low"],
                        "strikes": opp["strikes"],
                        "margin": opp["margin"],
                        "edge": opp["edge"],
                    },
                }),
            ),
        )
        alert_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()

    result = place_order(
        market_ticker=opp["ticker"],
        direction=opp["direction"],
        entry_price=opp["entry_yes"],
        alert_id=alert_id,
        contracts=1,
        fill_context={"fill_model": "sniper_touch"},
    )
    audit("sniper_entry", ticker=opp["ticker"], trade_id=result.get("trade_id"),
          verdict=opp["verdict"], edge=opp["edge"],
          observed_high=opp["observed_high"], observed_low=opp["observed_low"])
    return 1
