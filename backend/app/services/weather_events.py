"""
NWS active weather event awareness for city-specific confidence boosts.

Uses the free api.weather.gov active-alerts endpoint and keeps a short in-memory
cache so scans do not repeatedly fetch the same alert feed.
"""
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

NWS_ACTIVE_ALERTS_URL = "https://api.weather.gov/alerts/active"
EVENT_CACHE_TTL_SECONDS = 30 * 60

CITY_STATE = {
    "NYC": "NY",
    "CHI": "IL",
    "LAX": "CA",
    "MIA": "FL",
    "DAL": "TX",
    "ATL": "GA",
    "SEA": "WA",
    "DEN": "CO",
    "BOS": "MA",
    "PHX": "AZ",
    "SFO": "CA",
    "HOU": "TX",
    "PHIL": "PA",
    "MIN": "MN",
    "AUS": "TX",
    "LV": "NV",
    "DC": "DC",
    "OKC": "OK",
    "NOLA": "LA",
    "SATX": "TX",
}

_EVENT_CACHE: Dict[str, object] = {
    "refreshed_at": 0.0,
    "refreshed_at_iso": None,
    "cities": {},
    "errors": [],
}


def refresh_weather_events(force: bool = False) -> dict:
    now = time.time()
    if not force and now - float(_EVENT_CACHE.get("refreshed_at") or 0.0) < EVENT_CACHE_TTL_SECONDS:
        return _cache_payload()

    cities: Dict[str, List[dict]] = {}
    errors: List[dict] = []

    for city, meta in _city_points().items():
        try:
            events = _fetch_city_events(city, meta)
            if events:
                cities[city] = events
        except Exception as exc:
            logger.warning("NWS active-alert fetch failed for %s: %s", city, exc)
            errors.append({"city": city, "error": str(exc)})

    _EVENT_CACHE.update({
        "refreshed_at": now,
        "refreshed_at_iso": datetime.now(timezone.utc).isoformat(),
        "cities": cities,
        "errors": errors,
    })
    return _cache_payload()


def event_context_for_city(city_code: str) -> dict:
    data = refresh_weather_events(force=False)
    events = list((data.get("cities") or {}).get(city_code, []))
    bonus = 0.0
    for event in events:
        bonus = max(bonus, float(event.get("confidence_bonus") or 0.0))
    return {"events": events, "confidence_bonus": bonus}


def _cache_payload() -> dict:
    return {
        "status": "ok",
        "refreshed_at": _EVENT_CACHE.get("refreshed_at_iso"),
        "ttl_seconds": EVENT_CACHE_TTL_SECONDS,
        "cities": dict(_EVENT_CACHE.get("cities") or {}),
        "errors": list(_EVENT_CACHE.get("errors") or []),
    }


def _city_points() -> Dict[str, dict]:
    from app.services import weather_model

    points = {}
    for city, station in weather_model.SETTLEMENT_STATIONS.items():
        coords = station.get("coords")
        if not coords:
            continue
        points[city] = {
            "state": CITY_STATE.get(city),
            "lat": coords[0],
            "lon": coords[1],
            "station": station.get("station"),
            "station_name": station.get("name"),
        }
    return points


def _fetch_city_events(city: str, meta: dict) -> List[dict]:
    user_agent = _nws_user_agent()
    response = requests.get(
        NWS_ACTIVE_ALERTS_URL,
        params={"point": "%.4f,%.4f" % (meta["lat"], meta["lon"])},
        headers={"User-Agent": user_agent},
        timeout=10,
    )
    response.raise_for_status()

    events = []
    for feature in response.json().get("features") or []:
        props = feature.get("properties") or {}
        event = _event_from_properties(city, meta, props)
        if event:
            events.append(event)
    return events


def _event_from_properties(city: str, meta: dict, props: dict) -> Optional[dict]:
    event_type = props.get("event") or ""
    headline = props.get("headline") or event_type
    if not event_type and not headline:
        return None
    severity = props.get("severity") or "Unknown"
    bonus = _severity_to_confidence_bonus(event_type, severity, headline)
    return {
        "city_code": city,
        "state": meta.get("state"),
        "station": meta.get("station"),
        "station_name": meta.get("station_name"),
        "event": event_type,
        "severity": severity,
        "confidence_bonus": bonus,
        "onset": props.get("onset"),
        "expires": props.get("expires"),
        "headline": headline,
        "description": props.get("description"),
        "area_desc": props.get("areaDesc"),
    }


def _severity_to_confidence_bonus(event_type: str, severity: str, headline: str = "") -> float:
    text = " ".join([event_type or "", severity or "", headline or ""]).lower()
    is_warning = "warning" in text
    is_watch_or_advisory = "watch" in text or "advisory" in text
    is_heat_cold = any(word in text for word in ("heat", "cold", "freeze", "wind chill", "winter storm", "ice storm"))

    if is_warning and is_heat_cold and ("extreme" in text or "severe" in text or "excessive" in text):
        return 0.20
    if is_warning or "severe" in text:
        return 0.15
    if is_watch_or_advisory:
        return 0.08
    return 0.0


def _nws_user_agent() -> str:
    try:
        from app import config as cfg
        return cfg.load().get("nws_user_agent") or "sibylla-weather-bot/1.0 contact@sibylla.local"
    except Exception:
        return "sibylla-weather-bot/1.0 contact@sibylla.local"
