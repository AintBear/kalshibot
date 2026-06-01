"""
Weather model: scores Kalshi weather markets against NOAA/NWS forecast data.
"""
import json
import logging
import math
import re
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

NWS_POINTS_URL = "https://api.weather.gov/points/{lat},{lon}"
NWS_FORECAST_URL = "https://api.weather.gov/gridpoints/{office}/{gx},{gy}/forecast"
ACCUWEATHER_BASE = "https://dataservice.accuweather.com"
OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
NOAA_CDO_DATASETS_URL = "https://www.ncdc.noaa.gov/cdo-web/api/v2/datasets"
_FORECAST_CACHE: dict[tuple[float, float], Optional[dict]] = {}
_CURRENT_CACHE: dict[str, dict] = {}
_OPEN_METEO_CACHE: dict[tuple[float, float], dict] = {}
_ACCU_LOCATION_CACHE: dict[tuple[float, float], dict] = {}
_ACCU_FORECAST_CACHE: dict[str, dict] = {}
_ACCU_CURRENT_CACHE: dict[str, dict] = {}
_ACCU_BACKOFF_UNTIL = 0.0
_ACCU_RATE_LIMIT_STREAK = 0
_ACCU_LAST_RATE_LIMIT_AT = 0.0
_ACCU_LAST_CACHE_EVENT: dict = {}
_ACCU_FRESH_SECONDS = 1800
_ACCU_MAX_STALE_SECONDS = 4 * 3600
_ACCU_BACKOFF_BASE_SECONDS = 60
_ACCU_BACKOFF_MAX_SECONDS = 3600
_NOAA_CDO_HEALTH_CACHE: dict = {"checked_at": 0.0, "status": "not_configured"}
_NOAA_CDO_HEALTH_TTL_SECONDS = 900

MONTHS = {
    "JAN": "01", "FEB": "02", "MAR": "03", "APR": "04",
    "MAY": "05", "JUN": "06", "JUL": "07", "AUG": "08",
    "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12",
}

CITY_COORDS = {
    "NYC": (40.7128, -74.0060),
    "CHI": (41.8781, -87.6298),
    "LAX": (34.0522, -118.2437),
    "MIA": (25.7617, -80.1918),
    "DAL": (32.7767, -96.7970),
    "ATL": (33.7490, -84.3880),
    "SEA": (47.6062, -122.3321),
    "DEN": (39.7392, -104.9903),
    "BOS": (42.3656, -71.0096),
    "PHX": (33.4484, -112.0740),
    "SFO": (37.6213, -122.3790),
    "HOU": (29.9844, -95.3414),
    "PHIL": (39.8733, -75.2268),
    "MIN": (44.8848, -93.2223),
    "AUS": (30.1975, -97.6664),
    "LV": (36.0840, -115.1537),
    "DC": (38.9072, -77.0369),
    "OKC": (35.3931, -97.6007),
    "NOLA": (29.9934, -90.2580),
    "SATX": (29.5337, -98.4698),
}

# Kalshi settles weather markets from NWS Climatological Report (Daily) CLI
# products. The fields below are station/rules mappings verified against live
# Kalshi market rules on 2026-05-08 where API rules exposed CLI/source text.
SETTLEMENT_STATIONS = {
    "NYC":  {"station": "KNYC", "cli": "CLINY",  "name": "Central Park, New York", "coords": (40.7794, -73.9692)},
    "CHI":  {"station": "KMDW", "cli": "CLIMDW", "name": "Chicago Midway, IL", "coords": (41.7868, -87.7522)},
    "LAX":  {"station": "KLAX", "cli": "CLILAX", "name": "Los Angeles Airport, CA", "coords": (33.9416, -118.4085)},
    "MIA":  {"station": "KMIA", "cli": "CLIMIA", "name": "Miami International Airport", "coords": (25.7959, -80.2870)},
    "DAL":  {"station": "KDFW", "cli": "CLIDFW", "name": "Dallas/Fort Worth, TX", "coords": (32.8998, -97.0403)},
    "ATL":  {"station": "KATL", "cli": "CLIATL", "name": "Atlanta, GA", "coords": (33.6407, -84.4277)},
    "SEA":  {"station": "KSEA", "cli": "CLISEA", "name": "Seattle-Tacoma, WA", "coords": (47.4502, -122.3088)},
    "DEN":  {"station": "KDEN", "cli": "CLIDEN", "name": "Denver, CO", "coords": (39.8561, -104.6737)},
    "BOS":  {"station": "KBOS", "cli": "CLIBOS", "name": "Boston (Logan Airport), MA", "coords": (42.3656, -71.0096)},
    "PHX":  {"station": "KPHX", "cli": "CLIPHX", "name": "Phoenix, AZ", "coords": (33.4342, -112.0116)},
    "SFO":  {"station": "KSFO", "cli": "CLISFO", "name": "San Francisco Airport", "coords": (37.6213, -122.3790)},
    "HOU":  {"station": "KHOU", "cli": "CLIHOU", "name": "Houston-Hobby, TX", "coords": (29.6454, -95.2789)},
    "PHIL": {"station": "KPHL", "cli": "CLIPHL", "name": "Philadelphia International Airport", "coords": (39.8733, -75.2268)},
    "MIN":  {"station": "KMSP", "cli": "CLIMSP", "name": "Minneapolis/St Paul, MN", "coords": (44.8848, -93.2223)},
    "AUS":  {"station": "KAUS", "cli": "CLIAUS", "name": "Austin Bergstrom", "coords": (30.1975, -97.6664)},
    "LV":   {"station": "KLAS", "cli": "CLILAS", "name": "Las Vegas, NV", "coords": (36.0840, -115.1537)},
    "DC":   {"station": "KDCA", "cli": "CLIDCA", "name": "Washington-National", "coords": (38.8521, -77.0377)},
    "OKC":  {"station": "KOKC", "cli": "CLIOKC", "name": "Oklahoma City Will Rogers Airport", "coords": (35.3931, -97.6007)},
    "NOLA": {"station": "KMSY", "cli": "CLIMSY", "name": "New Orleans, LA", "coords": (29.9934, -90.2580)},
    "SATX": {"station": "KSAT", "cli": "CLISAT", "name": "San Antonio", "coords": (29.5337, -98.4698)},
}

KALSHI_SETTLEMENT_STATIONS = {
    city: data["station"] for city, data in SETTLEMENT_STATIONS.items()
}

SETTLEMENT_STATION_COORDS = {
    data["station"]: data["coords"] for data in SETTLEMENT_STATIONS.values()
}

CLI_TO_SETTLEMENT_STATION = {
    data["cli"]: data for data in SETTLEMENT_STATIONS.values() if data.get("cli")
}

RULE_LOCATION_TO_CITY = {
    "central park": "NYC",
    "new york city": "NYC",
    "chicago midway": "CHI",
    "los angeles airport": "LAX",
    "miami international": "MIA",
    "dallas/fort worth": "DAL",
    "atlanta": "ATL",
    "seattle-tacoma": "SEA",
    "denver": "DEN",
    "logan": "BOS",
    "boston": "BOS",
    "phoenix": "PHX",
    "san francisco airport": "SFO",
    "houston-hobby": "HOU",
    "philadelphia international": "PHIL",
    "minneapolis/st paul": "MIN",
    "austin bergstrom": "AUS",
    "las vegas": "LV",
    "washington-national": "DC",
    "washington dc": "DC",
    "oklahoma city will rogers": "OKC",
    "new orleans": "NOLA",
    "san antonio": "SATX",
}

SERIES_CITY = {
    "KXHIGHNY": "NYC", "KXLOWTNYC": "NYC", "KXLOWNYC": "NYC",
    "KXRAINNYCM": "NYC", "KXSNOWNYM": "NYC", "KXSNOWNY": "NYC", "KXSNOWNYC": "NYC",
    "KXHIGHCHI": "CHI", "KXLOWTCHI": "CHI", "KXLOWCHI": "CHI", "KXRAINCHIM": "CHI", "KXSNOWCHIM": "CHI",
    "KXHIGHLAX": "LAX", "KXLOWLAX": "LAX", "KXRAINLAXM": "LAX",
    "KXHIGHMIA": "MIA", "KXLOWTMIA": "MIA", "KXLOWMIA": "MIA", "KXRAINMIAM": "MIA",
    "KXHIGHTDAL": "DAL", "KXLOWTDAL": "DAL", "KXRAINDALM": "DAL",
    "KXHIGHTATL": "ATL", "KXLOWTATL": "ATL",
    "KXHIGHTSEA": "SEA", "KXLOWTSEA": "SEA", "KXRAINSEA": "SEA", "KXRAINSEAM": "SEA",
    "KXHIGHDEN": "DEN", "KXDENHIGH": "DEN", "KXLOWTDEN": "DEN", "KXLOWDEN": "DEN", "KXRAINDENM": "DEN",
    "KXHIGHTBOS": "BOS", "KXLOWTBOS": "BOS",
    "KXHIGHTPHX": "PHX", "KXLOWTPHX": "PHX",
    "KXHIGHTSFO": "SFO", "KXLOWTSFO": "SFO", "KXRAINSFOM": "SFO",
    "KXHIGHOU": "HOU", "KXHIGHTHOU": "HOU", "KXLOWTHOU": "HOU", "KXRAINHOUM": "HOU",
    "KXHIGHAUS": "AUS", "KXLOWTAUS": "AUS", "KXRAINAUSM": "AUS",
    "KXHIGHTLV": "LV", "KXLOWTLV": "LV",
    "KXHIGHTDC": "DC", "KXLOWTDC": "DC",
    "KXHIGHTOKC": "OKC", "KXLOWTOKC": "OKC",
    "KXHIGHTMIN": "MIN", "KXLOWTMIN": "MIN",
    "KXHIGHTNOLA": "NOLA", "KXLOWTNOLA": "NOLA",
    "KXHIGHTSATX": "SATX", "KXLOWTSATX": "SATX",
    "KXHIGHPHIL": "PHIL", "KXLOWTPHIL": "PHIL", "KXLOWPHIL": "PHIL",
}


_FORECAST_CACHE_TS: dict = {}
_FORECAST_CACHE_TTL = 600


def _fetch_nws_forecast(lat: float, lon: float) -> Optional[dict]:
    cache_key = (round(lat, 4), round(lon, 4))
    if cache_key in _FORECAST_CACHE:
        cached_at = _FORECAST_CACHE_TS.get(cache_key, 0)
        age = time.time() - cached_at
        if _FORECAST_CACHE[cache_key] is not None and age < _FORECAST_CACHE_TTL:
            return _FORECAST_CACHE[cache_key]
        if _FORECAST_CACHE[cache_key] is None and age < 120:
            return None

    try:
        user_agent = _nws_user_agent()
        r = requests.get(
            NWS_POINTS_URL.format(lat=lat, lon=lon),
            headers={"User-Agent": user_agent},
            timeout=10,
        )
        r.raise_for_status()
        props = r.json()["properties"]
        office = props["gridId"]
        gx = props["gridX"]
        gy = props["gridY"]

        r2 = requests.get(
            NWS_FORECAST_URL.format(office=office, gx=gx, gy=gy),
            headers={"User-Agent": user_agent},
            timeout=10,
        )
        r2.raise_for_status()
        data = r2.json()
        _FORECAST_CACHE[cache_key] = data
        _FORECAST_CACHE_TS[cache_key] = time.time()
        return data
    except Exception as e:
        logger.warning("NWS fetch failed: %s", e)
        _FORECAST_CACHE[cache_key] = None
        _FORECAST_CACHE_TS[cache_key] = time.time()
        return None


def _nws_user_agent() -> str:
    try:
        from app import config as cfg
        return cfg.load().get("nws_user_agent") or "sibylla-weather-bot/1.0 contact@sibylla.local"
    except Exception:
        return "sibylla-weather-bot/1.0 contact@sibylla.local"


def _accuweather_api_key() -> str:
    try:
        from app import config as cfg
        return cfg.load().get("accuweather_api_key") or ""
    except Exception:
        return ""


def _noaa_cdo_token() -> str:
    try:
        from app import config as cfg
        return cfg.load().get("noaa_token") or ""
    except Exception:
        return ""


def noaa_cdo_status(force: bool = False) -> dict:
    token = _noaa_cdo_token()
    if not token:
        return {"status": "not_configured", "configured": False}

    now = time.time()
    cached_at = float(_NOAA_CDO_HEALTH_CACHE.get("checked_at") or 0.0)
    if not force and now - cached_at < _NOAA_CDO_HEALTH_TTL_SECONDS:
        return {
            "status": _NOAA_CDO_HEALTH_CACHE.get("status", "error"),
            "configured": True,
            "checked_at": _NOAA_CDO_HEALTH_CACHE.get("checked_at_iso"),
            "error": _NOAA_CDO_HEALTH_CACHE.get("error"),
        }

    status = "error"
    error = None
    try:
        response = requests.get(NOAA_CDO_DATASETS_URL, headers={"token": token}, timeout=8)
        status = "configured" if response.ok else "error"
        if not response.ok:
            error = f"HTTP {response.status_code}"
    except Exception as exc:
        error = _safe_request_error(exc)

    checked_at_iso = datetime.now(timezone.utc).isoformat()
    _NOAA_CDO_HEALTH_CACHE.update({
        "checked_at": now,
        "checked_at_iso": checked_at_iso,
        "status": status,
        "error": error,
    })
    return {
        "status": status,
        "configured": True,
        "checked_at": checked_at_iso,
        "error": error,
    }


def _accuweather_headers(api_key: str) -> dict:
    return {
        "User-Agent": "sibylla-weather-bot/1.0",
        "Authorization": f"Bearer {api_key}",
    }


def _accuweather_params(api_key: str, **extra) -> dict:
    # AccuWeather deployments vary between bearer auth and apikey query auth.
    # Sending both keeps this compatible with the documented and legacy forms.
    return {"apikey": api_key, **extra}


def _accuweather_available() -> bool:
    return time.time() >= _ACCU_BACKOFF_UNTIL


def _retry_after_seconds(response, default: Optional[int] = None) -> Optional[int]:
    headers = getattr(response, "headers", {}) or {}
    value = headers.get("Retry-After") if response is not None else None
    try:
        if value is not None:
            return max(1, int(float(value)))
    except (TypeError, ValueError):
        pass
    return default


def _mark_accuweather_rate_limited(seconds: Optional[int] = None) -> int:
    global _ACCU_BACKOFF_UNTIL, _ACCU_RATE_LIMIT_STREAK, _ACCU_LAST_RATE_LIMIT_AT
    _ACCU_RATE_LIMIT_STREAK += 1
    if seconds is None:
        seconds = min(
            _ACCU_BACKOFF_MAX_SECONDS,
            _ACCU_BACKOFF_BASE_SECONDS * (2 ** max(0, _ACCU_RATE_LIMIT_STREAK - 1)),
        )
    else:
        seconds = min(_ACCU_BACKOFF_MAX_SECONDS, max(1, int(seconds)))
    now = time.time()
    _ACCU_LAST_RATE_LIMIT_AT = now
    _ACCU_BACKOFF_UNTIL = max(_ACCU_BACKOFF_UNTIL, now + seconds)
    return seconds


def _mark_accuweather_unavailable(response=None, reason: str = "unavailable", default_seconds: int = 900) -> int:
    global _ACCU_BACKOFF_UNTIL, _ACCU_LAST_CACHE_EVENT
    seconds = _retry_after_seconds(response, default_seconds) or default_seconds
    seconds = min(_ACCU_BACKOFF_MAX_SECONDS, max(1, int(seconds)))
    now = time.time()
    _ACCU_BACKOFF_UNTIL = max(_ACCU_BACKOFF_UNTIL, now + seconds)
    _ACCU_LAST_CACHE_EVENT = {
        "kind": "source_backoff",
        "reason": reason,
        "http_status": getattr(response, "status_code", None),
        "served_at": datetime.now(timezone.utc).isoformat(),
        "backoff_seconds": seconds,
    }
    return seconds


def _mark_accuweather_success() -> None:
    global _ACCU_RATE_LIMIT_STREAK
    _ACCU_RATE_LIMIT_STREAK = 0


def _cache_age_seconds(entry: Optional[dict]) -> Optional[int]:
    if not entry or not entry.get("data") or not entry.get("_ts"):
        return None
    return int(max(0, time.time() - float(entry["_ts"])))


def _cached_accuweather_forecast(location_key: str, max_age: int, reason: str) -> Optional[dict]:
    cached = _ACCU_FORECAST_CACHE.get(location_key)
    age = _cache_age_seconds(cached)
    if age is None or age > max_age:
        return None
    global _ACCU_LAST_CACHE_EVENT
    _ACCU_LAST_CACHE_EVENT = {
        "kind": "forecast",
        "location_key": location_key,
        "reason": reason,
        "cache_age_seconds": age,
        "served_at": datetime.now(timezone.utc).isoformat(),
    }
    logger.warning("AccuWeather using cached forecast for %s; age=%ss reason=%s", location_key, age, reason)
    return cached.get("data")


def accuweather_cache_status() -> dict:
    now = time.time()
    forecast_entries = [v for v in _ACCU_FORECAST_CACHE.values() if v.get("data")]
    current_entries = [v for v in _ACCU_CURRENT_CACHE.values() if v.get("data")]
    forecast_ages = [int(now - float(v.get("_ts", now))) for v in forecast_entries if v.get("_ts")]
    current_ages = [int(now - float(v.get("_ts", now))) for v in current_entries if v.get("_ts")]
    return {
        "configured": bool(_accuweather_api_key()),
        "status": _accuweather_operational_status(forecast_ages, current_ages),
        "available": _accuweather_available(),
        "backoff_until": (
            datetime.fromtimestamp(_ACCU_BACKOFF_UNTIL, tz=timezone.utc).isoformat()
            if _ACCU_BACKOFF_UNTIL > now else None
        ),
        "rate_limit_streak": _ACCU_RATE_LIMIT_STREAK,
        "last_rate_limit_at": (
            datetime.fromtimestamp(_ACCU_LAST_RATE_LIMIT_AT, tz=timezone.utc).isoformat()
            if _ACCU_LAST_RATE_LIMIT_AT else None
        ),
        "forecast_cache_entries": len(forecast_entries),
        "current_cache_entries": len(current_entries),
        "fresh_forecast_entries": sum(1 for age in forecast_ages if age <= _ACCU_FRESH_SECONDS),
        "stale_usable_forecast_entries": sum(
            1 for age in forecast_ages if _ACCU_FRESH_SECONDS < age <= _ACCU_MAX_STALE_SECONDS
        ),
        "oldest_usable_forecast_age_seconds": (
            max([age for age in forecast_ages if age <= _ACCU_MAX_STALE_SECONDS])
            if any(age <= _ACCU_MAX_STALE_SECONDS for age in forecast_ages) else None
        ),
        "youngest_forecast_age_seconds": min(forecast_ages) if forecast_ages else None,
        "youngest_current_age_seconds": min(current_ages) if current_ages else None,
        "last_cache_event": dict(_ACCU_LAST_CACHE_EVENT) if _ACCU_LAST_CACHE_EVENT else None,
    }


def _accuweather_operational_status(forecast_ages: List[int], current_ages: List[int]) -> str:
    if not _accuweather_api_key():
        return "not_configured"
    now = time.time()
    if _ACCU_BACKOFF_UNTIL > now:
        return "backoff_until_%s" % datetime.fromtimestamp(_ACCU_BACKOFF_UNTIL, tz=timezone.utc).isoformat()
    if any(age <= _ACCU_FRESH_SECONDS for age in forecast_ages):
        return "live"
    if any(age <= _ACCU_MAX_STALE_SECONDS for age in forecast_ages + current_ages):
        return "cached"
    return "down"


def _safe_request_error(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status:
        return f"HTTP {status}"
    return exc.__class__.__name__


def _fetch_accuweather_location_key(lat: float, lon: float) -> Optional[str]:
    cache_key = (round(lat, 4), round(lon, 4))
    cached = _ACCU_LOCATION_CACHE.get(cache_key)
    if cached and cached.get("key") and time.time() - cached.get("_ts", 0) < 86400:
        return cached.get("key")

    api_key = _accuweather_api_key()
    if not api_key or not _accuweather_available():
        return None

    try:
        response = requests.get(
            f"{ACCUWEATHER_BASE}/locations/v1/cities/geoposition/search",
            params=_accuweather_params(api_key, q=f"{lat},{lon}"),
            headers=_accuweather_headers(api_key),
            timeout=10,
        )
        if response.status_code == 429:
            delay = _mark_accuweather_rate_limited(_retry_after_seconds(response))
            logger.warning("AccuWeather rate limited; backing off supplemental source for %d seconds", delay)
            if not cached:
                _ACCU_LOCATION_CACHE[cache_key] = {"key": None, "_ts": time.time()}
            return None
        if response.status_code in (401, 403):
            delay = _mark_accuweather_unavailable(response, "auth_or_permission_failed")
            logger.warning(
                "AccuWeather location lookup returned HTTP %s; backing off supplemental source for %d seconds",
                response.status_code,
                delay,
            )
            if not cached:
                _ACCU_LOCATION_CACHE[cache_key] = {"key": None, "_ts": time.time()}
            return None
        response.raise_for_status()
        key = response.json().get("Key")
        if key:
            _ACCU_LOCATION_CACHE[cache_key] = {"key": key, "_ts": time.time()}
            _mark_accuweather_success()
        return key
    except Exception as exc:
        logger.warning("AccuWeather location lookup failed: %s", _safe_request_error(exc))
        _ACCU_LOCATION_CACHE[cache_key] = {"key": None, "_ts": time.time()}
        return None


def _fetch_accuweather_forecast(lat: float, lon: float) -> Optional[dict]:
    location_key = _fetch_accuweather_location_key(lat, lon)
    if not location_key:
        return None

    cached = _ACCU_FORECAST_CACHE.get(location_key)
    if cached and cached.get("data") and time.time() - cached.get("_ts", 0) < _ACCU_FRESH_SECONDS:
        return cached.get("data")

    api_key = _accuweather_api_key()
    if not api_key:
        return _cached_accuweather_forecast(location_key, _ACCU_MAX_STALE_SECONDS, "api_key_missing")
    if not _accuweather_available():
        return _cached_accuweather_forecast(location_key, _ACCU_MAX_STALE_SECONDS, "backoff_active")
    try:
        response = requests.get(
            f"{ACCUWEATHER_BASE}/forecasts/v1/daily/5day/{location_key}",
            params=_accuweather_params(api_key, details="true", metric="false"),
            headers=_accuweather_headers(api_key),
            timeout=10,
        )
        if response.status_code == 429:
            delay = _mark_accuweather_rate_limited(_retry_after_seconds(response))
            logger.warning("AccuWeather rate limited; backing off supplemental source for %d seconds", delay)
            stale = _cached_accuweather_forecast(location_key, _ACCU_MAX_STALE_SECONDS, "rate_limited")
            if stale is not None:
                return stale
            if not cached:
                _ACCU_FORECAST_CACHE[location_key] = {"data": None, "_ts": time.time(), "last_error": "HTTP 429"}
            return None
        if response.status_code in (401, 403):
            delay = _mark_accuweather_unavailable(response, "auth_or_permission_failed")
            logger.warning(
                "AccuWeather forecast returned HTTP %s; backing off supplemental source for %d seconds",
                response.status_code,
                delay,
            )
            stale = _cached_accuweather_forecast(location_key, _ACCU_MAX_STALE_SECONDS, "auth_or_permission_failed")
            if stale is not None:
                return stale
            if not cached:
                _ACCU_FORECAST_CACHE[location_key] = {"data": None, "_ts": time.time(), "last_error": f"HTTP {response.status_code}"}
            return None
        response.raise_for_status()
        data = response.json()
        _ACCU_FORECAST_CACHE[location_key] = {"data": data, "_ts": time.time(), "last_error": None}
        _mark_accuweather_success()
        return data
    except Exception as exc:
        logger.warning("AccuWeather forecast fetch failed: %s", _safe_request_error(exc))
        stale = _cached_accuweather_forecast(location_key, _ACCU_MAX_STALE_SECONDS, "fetch_error")
        if stale is not None:
            return stale
        if not cached:
            _ACCU_FORECAST_CACHE[location_key] = {"data": None, "_ts": time.time(), "last_error": _safe_request_error(exc)}
        return None


def _extract_accuweather_forecast(forecast_data: dict, target_date: str) -> dict:
    result = {"high": None, "low": None, "precip_pct": None}
    if not forecast_data:
        return result

    for day in forecast_data.get("DailyForecasts") or []:
        if str(day.get("Date") or "")[:10] != target_date:
            continue

        temp = day.get("Temperature") or {}
        maximum = temp.get("Maximum") or {}
        minimum = temp.get("Minimum") or {}
        result["high"] = _to_float(maximum.get("Value"))
        result["low"] = _to_float(minimum.get("Value"))

        precip_candidates = []
        for half in ("Day", "Night"):
            data = day.get(half) or {}
            for key in ("PrecipitationProbability", "RainProbability", "SnowProbability", "ThunderstormProbability"):
                value = _to_float(data.get(key))
                if value is not None:
                    precip_candidates.append(value)
        if precip_candidates:
            result["precip_pct"] = round(max(precip_candidates), 1)
        break
    return result


def _fetch_open_meteo_forecast(lat: float, lon: float) -> Optional[dict]:
    cache_key = (round(lat, 4), round(lon, 4))
    cached = _OPEN_METEO_CACHE.get(cache_key)
    if cached and cached.get("data") and time.time() - cached.get("_ts", 0) < 1800:
        return cached.get("data")

    try:
        response = requests.get(
            OPEN_METEO_FORECAST_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "temperature_unit": "fahrenheit",
                "timezone": "auto",
                "forecast_days": 3,
            },
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        _OPEN_METEO_CACHE[cache_key] = {"data": data, "_ts": time.time()}
        return data
    except Exception as exc:
        logger.warning("Open-Meteo fetch failed: %s", exc)
        _OPEN_METEO_CACHE[cache_key] = {"data": None, "_ts": time.time()}
        return None


def _extract_open_meteo_forecast(forecast_data: dict, target_date: str) -> dict:
    result = {"high": None, "low": None, "precip_pct": None}
    if not forecast_data:
        return result
    daily = forecast_data.get("daily") or {}
    dates = daily.get("time") or []
    highs = daily.get("temperature_2m_max") or []
    lows = daily.get("temperature_2m_min") or []
    precips = daily.get("precipitation_probability_max") or []
    for i, date_str in enumerate(dates):
        if date_str == target_date:
            if i < len(highs) and highs[i] is not None:
                result["high"] = round(float(highs[i]), 1)
            if i < len(lows) and lows[i] is not None:
                result["low"] = round(float(lows[i]), 1)
            if i < len(precips) and precips[i] is not None:
                result["precip_pct"] = round(float(precips[i]), 1)
            break
    return result


def _merge_forecasts(nws: dict, accuweather: Optional[dict], open_meteo: Optional[dict] = None) -> dict:
    nws = nws or {"high": None, "low": None, "precip_pct": None}
    accuweather = accuweather or {"high": None, "low": None, "precip_pct": None}
    open_meteo = open_meteo or {"high": None, "low": None, "precip_pct": None}
    result = {"high": None, "low": None, "precip_pct": None}
    source_disagreement = 0.0
    precip_source_disagreement = 0.0
    sources = []
    forecast_sources = []

    if any(nws.get(k) is not None for k in ("high", "low", "precip_pct")):
        sources.append("NWS")
        forecast_sources.append("nws_free")
    if any(accuweather.get(k) is not None for k in ("high", "low", "precip_pct")):
        sources.append("AccuWeather")
        forecast_sources.append("accuweather")
    if any(open_meteo.get(k) is not None for k in ("high", "low", "precip_pct")):
        sources.append("Open-Meteo")
        forecast_sources.append("open_meteo")

    for key in ("high", "low", "precip_pct"):
        nws_val = _to_float(nws.get(key))
        accu_val = _to_float(accuweather.get(key))
        om_val = _to_float(open_meteo.get(key))
        weighted_vals = []
        all_vals = []
        if nws_val is not None:
            weighted_vals.append((nws_val, 0.60))
            all_vals.append(nws_val)
        if accu_val is not None:
            weighted_vals.append((accu_val, 0.40))
            all_vals.append(accu_val)
        if om_val is not None:
            weighted_vals.append((om_val, 0.40))
            all_vals.append(om_val)
        if weighted_vals:
            total_weight = sum(w for _, w in weighted_vals)
            result[key] = round(sum(v * w for v, w in weighted_vals) / total_weight, 1)
            if len(all_vals) >= 2:
                spread = max(all_vals) - min(all_vals)
                if key == "precip_pct":
                    precip_source_disagreement = max(precip_source_disagreement, spread)
                else:
                    source_disagreement = max(source_disagreement, spread)

    result["source"] = "+".join(sources) if sources else "none"
    result["sources"] = sources
    result["forecast_sources"] = forecast_sources
    if len(forecast_sources) == 1:
        result["source_penalty"] = "single_source"
    result["source_disagreement"] = round(source_disagreement, 1)
    result["precip_source_disagreement"] = round(precip_source_disagreement, 1)
    result["nws"] = nws
    if "AccuWeather" in sources:
        result["accuweather"] = accuweather
    if "Open-Meteo" in sources:
        result["open_meteo"] = open_meteo
    return result


def _extract_temp_forecast(forecast_data: dict, target_date: str) -> dict:
    result = {"high": None, "low": None, "precip_pct": None}
    if not forecast_data:
        return result
    periods = forecast_data.get("properties", {}).get("periods", [])
    for period in periods:
        start = period.get("startTime", "")[:10]
        if start == target_date:
            temp = period.get("temperature")
            unit = period.get("temperatureUnit", "F")
            if unit == "C" and temp is not None:
                temp = round(temp * 9 / 5 + 32, 1)
            is_day = period.get("isDaytime", True)
            if is_day and result["high"] is None:
                result["high"] = temp
            elif not is_day and result["low"] is None:
                result["low"] = temp
            if result["precip_pct"] is None:
                prob = period.get("probabilityOfPrecipitation", {})
                result["precip_pct"] = prob.get("value") if isinstance(prob, dict) else None
    return result


def _market_to_coords(ticker: str) -> tuple[Optional[float], Optional[float]]:
    code = _city_code_from_ticker(ticker)
    if code:
        return CITY_COORDS[code]
    return None, None


def settlement_station_info_for_ticker(ticker: str, market: Optional[dict] = None) -> Optional[dict]:
    code = _city_code_from_ticker(ticker)
    if not code:
        return None
    from_rules = _settlement_station_from_market_rules(code, market)
    if from_rules:
        return from_rules
    return SETTLEMENT_STATIONS.get(code)


def settlement_station_for_ticker(ticker: str, market: Optional[dict] = None) -> Optional[str]:
    info = settlement_station_info_for_ticker(ticker, market)
    return info.get("station") if info else None


def _market_to_forecast_coords(
    ticker: str,
    market: Optional[dict] = None,
) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    info = settlement_station_info_for_ticker(ticker, market)
    station = info.get("station") if info else None
    coords = info.get("coords") if info else None
    if station and coords:
        lat, lon = coords
        return lat, lon, station
    if station and station in SETTLEMENT_STATION_COORDS:
        lat, lon = SETTLEMENT_STATION_COORDS[station]
        return lat, lon, station
    code = _city_code_from_ticker(ticker)
    if code:
        logger.warning("No settlement station coordinates for %s (%s); using city center", ticker, code)
    lat, lon = _market_to_coords(ticker)
    return lat, lon, station


def _settlement_station_from_market_rules(city_code: str, market: Optional[dict]) -> Optional[dict]:
    if not market:
        return None
    text = " ".join(
        str((market or {}).get(k) or "")
        for k in ("rules_primary", "rules_secondary")
    )
    if not text.strip():
        return None

    cli_match = re.search(r"\b(CLI[A-Z0-9]+)\b", text.upper())
    if cli_match:
        info = CLI_TO_SETTLEMENT_STATION.get(cli_match.group(1))
        if info:
            return info

    lowered = text.lower()
    for needle, code in RULE_LOCATION_TO_CITY.items():
        if needle in lowered:
            return SETTLEMENT_STATIONS.get(code)
    return SETTLEMENT_STATIONS.get(city_code)


def _city_code_from_ticker(ticker: str) -> Optional[str]:
    series = ticker.upper().split("-")[0]
    code = SERIES_CITY.get(series)
    if code:
        return code

    # Conservative fallback for newly added Kalshi city series.
    upper = ticker.upper()
    for code in CITY_COORDS:
        if code in upper:
            return code
    return None


def current_conditions_for_ticker(ticker: str) -> dict:
    code = _city_code_from_ticker(ticker)
    if not code:
        return {"city_code": None, "temperature": None, "source": "NWS", "error": "unknown_city"}
    cached = _CURRENT_CACHE.get(code)
    if cached and time.time() - cached.get("_ts", 0) < 300:
        return {k: v for k, v in cached.items() if k != "_ts"}

    station_info = settlement_station_info_for_ticker(ticker) or {}
    settlement_station = station_info.get("station")
    lat, lon = station_info.get("coords") or CITY_COORDS[code]
    result = {
        "city_code": code,
        "temperature": None,
        "source": "NWS",
        "settlement_station": settlement_station,
        "settlement_station_name": station_info.get("name"),
    }
    try:
        user_agent = _nws_user_agent()
        station_id = settlement_station
        if not station_id:
            points = requests.get(
                NWS_POINTS_URL.format(lat=lat, lon=lon),
                headers={"User-Agent": user_agent},
                timeout=8,
            )
            points.raise_for_status()
            stations_url = points.json()["properties"].get("observationStations")
            stations = requests.get(stations_url, headers={"User-Agent": user_agent}, timeout=8)
            stations.raise_for_status()
            features = stations.json().get("features") or []
            if not features:
                raise RuntimeError("no stations")
            station_id = features[0]["properties"]["stationIdentifier"]
        obs = requests.get(
            f"https://api.weather.gov/stations/{station_id}/observations/latest",
            params={"require_qc": "false"},
            headers={"User-Agent": user_agent},
            timeout=8,
        )
        obs.raise_for_status()
        props = obs.json().get("properties", {})
        temp_c = (props.get("temperature") or {}).get("value")
        temp_f = round(temp_c * 9 / 5 + 32, 1) if temp_c is not None else None
        result = {
            "city_code": code,
            "station": station_id,
            "settlement_station": settlement_station,
            "settlement_station_name": station_info.get("name"),
            "temperature": temp_f,
            "observed_at": props.get("timestamp"),
            "source": "NWS",
            "text": props.get("textDescription"),
        }
    except Exception as exc:
        result = {
            "city_code": code,
            "temperature": None,
            "source": "NWS",
            "settlement_station": settlement_station,
            "settlement_station_name": station_info.get("name"),
            "error": str(exc),
        }

    accuweather = _fetch_accuweather_current(lat, lon)
    if accuweather:
        result["accuweather_temperature"] = accuweather.get("temperature")
        result["accuweather_text"] = accuweather.get("text")
        result["accuweather_observed_at"] = accuweather.get("observed_at")
        if result.get("temperature") is None and accuweather.get("temperature") is not None:
            result["temperature"] = accuweather.get("temperature")
            result["text"] = accuweather.get("text")
            result["source"] = "AccuWeather"
        elif result.get("temperature") is not None:
            result["source"] = "NWS+AccuWeather"

    _CURRENT_CACHE[code] = {**result, "_ts": time.time()}
    return result


def _fetch_accuweather_current(lat: float, lon: float) -> Optional[dict]:
    location_key = _fetch_accuweather_location_key(lat, lon)
    if not location_key:
        return None

    cached = _ACCU_CURRENT_CACHE.get(location_key)
    if cached and cached.get("data") and time.time() - cached.get("_ts", 0) < 300:
        return cached.get("data")

    api_key = _accuweather_api_key()
    if not api_key or not _accuweather_available():
        return cached.get("data") if cached and _cache_age_seconds(cached) is not None and _cache_age_seconds(cached) <= 1800 else None
    try:
        response = requests.get(
            f"{ACCUWEATHER_BASE}/currentconditions/v1/{location_key}",
            params=_accuweather_params(api_key, details="true"),
            headers=_accuweather_headers(api_key),
            timeout=10,
        )
        if response.status_code == 429:
            delay = _mark_accuweather_rate_limited(_retry_after_seconds(response))
            logger.warning("AccuWeather rate limited; backing off supplemental source for %d seconds", delay)
            return cached.get("data") if cached and _cache_age_seconds(cached) is not None and _cache_age_seconds(cached) <= 1800 else None
        if response.status_code in (401, 403):
            delay = _mark_accuweather_unavailable(response, "auth_or_permission_failed")
            logger.warning(
                "AccuWeather current conditions returned HTTP %s; backing off supplemental source for %d seconds",
                response.status_code,
                delay,
            )
            return cached.get("data") if cached and _cache_age_seconds(cached) is not None and _cache_age_seconds(cached) <= 1800 else None
        response.raise_for_status()
        rows = response.json() or []
        row = rows[0] if rows else {}
        imperial = ((row.get("Temperature") or {}).get("Imperial") or {})
        data = {
            "temperature": _to_float(imperial.get("Value")),
            "text": row.get("WeatherText"),
            "observed_at": row.get("LocalObservationDateTime"),
            "source": "AccuWeather",
        }
        _ACCU_CURRENT_CACHE[location_key] = {"data": data, "_ts": time.time(), "last_error": None}
        _mark_accuweather_success()
        return data
    except Exception as exc:
        logger.warning("AccuWeather current conditions fetch failed: %s", _safe_request_error(exc))
        if cached and _cache_age_seconds(cached) is not None and _cache_age_seconds(cached) <= 1800:
            return cached.get("data")
        if not cached:
            _ACCU_CURRENT_CACHE[location_key] = {"data": None, "_ts": time.time(), "last_error": _safe_request_error(exc)}
        return None


def _target_date_from_ticker(ticker: str, fallback: Optional[str] = None) -> str:
    match = re.search(r"-(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})", ticker.upper())
    if match:
        yy, mon, day = match.groups()
        return f"20{yy}-{MONTHS[mon]}-{day}"
    return (fallback or datetime.now(timezone.utc).isoformat())[:10]


def _segment_from_ticker(ticker: str) -> str:
    t = ticker.upper()
    if "RAIN" in t or "PRECIP" in t or "SNOW" in t:
        return "precipitation"
    if "HIGH" in t:
        return "high_bracket"
    if "LOW" in t:
        return "low_bracket"
    return "weather_all"


def _time_bucket_from_ticker(ticker: str) -> str:
    t = ticker.upper()
    if "NEXT" in t or "TMW" in t or "TOMORROW" in t:
        return "next_day"
    if "WEEK" in t or "WK" in t:
        return "outer_day"
    return "same_day"


def _phantom_risk_assessment(edge: float, model_prob: float, market_price: float, confidence: float) -> dict:
    """
    Phantom risk for abs(edge) >= 0.25 — catches large disagreements between
    model and market before they masquerade as sure-thing edges.
    Returns score, flags, level. Does NOT block alerts.
    """
    if abs(edge) < 0.25:
        return {"score": 0.0, "flags": [], "level": "none"}

    flags = []
    score = 0.0

    if confidence < 0.40:
        flags.append("low_confidence")
        score += 40.0

    if model_prob > 0.88 or model_prob < 0.12:
        flags.append("extreme_model_prob")
        score += 35.0

    # Market is already fairly certain in the other direction
    if market_price > 0.75 or market_price < 0.25:
        flags.append("market_leaning_hard")
        score += 20.0

    if market_price > 0.85 or market_price < 0.15:
        flags.append("thin_market")
        score += 25.0

    if (edge > 0 and market_price < 0.05) or (edge < 0 and market_price > 0.95):
        flags.append("against_near_certain_market")
        score += 25.0

    if score >= 60:
        level = "high"
    elif score >= 30:
        level = "medium"
    else:
        level = "low"

    return {"score": score, "flags": flags, "level": level}


def score_market(
    ticker: str,
    market_price: float,
    close_time: Optional[str] = None,
    market: Optional[dict] = None,
) -> Optional[dict]:
    lat, lon, forecast_station = _market_to_forecast_coords(ticker, market)
    target_date = _target_date_from_ticker(ticker, close_time)
    segment = _segment_from_ticker(ticker)
    time_bucket = _time_bucket_from_ticker(ticker)
    city_code = _city_code_from_ticker(ticker)
    station_info = settlement_station_info_for_ticker(ticker, market) or {}
    settlement_station = forecast_station or station_info.get("station")

    forecast_data = None
    accuweather_data = None
    open_meteo_data = None
    forecast_temps = {"high": None, "low": None, "precip_pct": None}
    accuweather_temps = {"high": None, "low": None, "precip_pct": None}
    open_meteo_temps = {"high": None, "low": None, "precip_pct": None}

    if lat and lon:
        forecast_data = _fetch_nws_forecast(lat, lon)
        if forecast_data:
            forecast_temps = _extract_temp_forecast(forecast_data, target_date)
        accuweather_data = _fetch_accuweather_forecast(lat, lon)
        if accuweather_data:
            accuweather_temps = _extract_accuweather_forecast(accuweather_data, target_date)
        open_meteo_data = _fetch_open_meteo_forecast(lat, lon)
        if open_meteo_data:
            open_meteo_temps = _extract_open_meteo_forecast(open_meteo_data, target_date)

    forecast = _merge_forecasts(forecast_temps, accuweather_temps, open_meteo_temps)

    observed = None
    if lat and lon and target_date and segment != "precipitation":
        try:
            from app.services import intraday_temps  # local import to avoid cycles
            observed = intraday_temps.get_observed_extremes(lat, lon, target_date)
        except Exception as exc:
            logger.warning("intraday_temps lookup failed for %s: %s", ticker, exc)
            observed = None

    raw_forecast_prob = _estimate_model_prob(ticker, market_price, forecast, segment, market)
    model_prob = _estimate_model_prob(
        ticker, market_price, forecast, segment, market, observed=observed
    )
    if model_prob is None:
        return None
    raw_model_prob = model_prob
    model_prob, calibration = _apply_calibration(model_prob, ticker, segment)
    model_prob = _isotonic_calibrate(model_prob)
    model_prob = _market_anchor(model_prob, market_price)

    hours = _hours_to_close(ticker, market)
    event_context = _weather_event_context(city_code)
    confidence = _estimate_confidence(
        forecast,
        segment,
        model_prob,
        hours_to_close=hours,
        event_bonus=event_context.get("confidence_bonus", 0.0),
    )
    edge = round(model_prob - market_price, 4)
    direction = "yes" if edge > 0 else "no"

    phantom = _phantom_risk_assessment(edge, model_prob, market_price, confidence)

    result = {
        "ticker": ticker,
        "market_price": market_price,
        "model_prob": round(model_prob, 4),
        "edge": edge,
        "direction": direction,
        "confidence": round(confidence, 4),
        "segment": segment,
        "time_bucket": time_bucket,
        "hours_to_close": round(hours, 1),
        "time_priority": _time_priority(hours),
        "city_code": city_code,
        "settlement_station": settlement_station,
        "settlement_station_name": station_info.get("name"),
        "settlement_cli_product": station_info.get("cli"),
        "forecast_station": forecast_station,
        "forecast_coordinates_source": "settlement_station" if forecast_station else "city_center",
        "forecast_sources": forecast.get("forecast_sources") or _normalized_forecast_sources(forecast),
        "forecast": forecast,
        "active_weather_events": event_context.get("events", []),
        "weather_event_confidence_bonus": round(float(event_context.get("confidence_bonus") or 0.0), 4),
        "current_conditions": current_conditions_for_ticker(ticker),
        "phantom_risk_score": phantom["score"],
        "phantom_risk_flags": json.dumps(phantom["flags"]),
        "phantom_risk_level": phantom["level"],
        "raw_model_prob": round(raw_model_prob, 4),
        "raw_forecast_prob": round(raw_forecast_prob, 4) if raw_forecast_prob is not None else None,
        "intraday_observation": observed,
        "calibration": calibration,
        "scored_at": datetime.now(timezone.utc).isoformat(),
    }
    result["analysis"] = generate_analysis(ticker, result, market)
    return result


def _hours_to_close(ticker: str, market: Optional[dict] = None) -> float:
    close_time = (market or {}).get("close_time") or (market or {}).get("expiration_time")
    if close_time:
        try:
            normalized = str(close_time).replace("Z", "+00:00")
            close_dt = datetime.fromisoformat(normalized)
            delta = (close_dt - datetime.now(timezone.utc)).total_seconds() / 3600.0
            return max(0.0, delta)
        except Exception:
            pass
    target = _target_date_from_ticker(ticker)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if target == today:
        return 6.0
    return 24.0


def _time_priority(hours: float) -> str:
    try:
        hours = float(hours)
    except (TypeError, ValueError):
        return "normal"
    if hours < 4:
        return "high"
    if hours > 24:
        return "low"
    return "normal"


def _normalized_forecast_sources(forecast: dict) -> List[str]:
    existing = forecast.get("forecast_sources")
    if existing:
        return list(existing)
    names = []
    for source in forecast.get("sources") or []:
        normalized = str(source).strip().lower().replace(" ", "_")
        if normalized == "nws":
            normalized = "nws_free"
        elif normalized == "accuweather":
            normalized = "accuweather"
        if normalized:
            names.append(normalized)
    return names


def _weather_event_context(city_code: Optional[str]) -> dict:
    if not city_code:
        return {"events": [], "confidence_bonus": 0.0}
    try:
        from app.services import weather_events
        return weather_events.event_context_for_city(city_code)
    except Exception as exc:
        logger.debug("Weather event context unavailable for %s: %s", city_code, exc)
        return {"events": [], "confidence_bonus": 0.0}


def _adaptive_sigma(base_sigma: float, hours: float, forecast: dict) -> float:
    """Scale sigma by time-to-close, starting from the market-type base sigma."""
    if hours <= 4:
        time_scale = 0.70
    elif hours <= 8:
        time_scale = 0.80
    elif hours <= 16:
        time_scale = 0.90
    elif hours <= 24:
        time_scale = 1.0
    else:
        time_scale = 1.10

    sigma = base_sigma * time_scale

    disagreement = float(forecast.get("source_disagreement") or 0.0)
    if disagreement >= 5.0:
        sigma += 0.75
    elif disagreement >= 3.0:
        sigma += 0.35

    return round(max(sigma, 2.0), 2)


def _market_anchor(model_prob: float, market_price: float) -> float:
    """Pull model probability toward market price at extremes.

    Markets priced under 5c or over 95c are almost always right —
    the model's Gaussian tails wildly overestimate these events.
    """
    if market_price <= 0.05:
        return round(min(model_prob, 0.08), 4)
    if market_price >= 0.95:
        return round(max(model_prob, 0.92), 4)

    disagreement = abs(model_prob - market_price)
    if disagreement < 0.30:
        return model_prob

    if market_price > 0.85 or market_price < 0.15:
        anchor_weight = 0.40
    elif market_price > 0.75 or market_price < 0.25:
        anchor_weight = 0.25
    else:
        return model_prob

    blended = model_prob * (1.0 - anchor_weight) + market_price * anchor_weight
    return round(min(max(blended, 0.01), 0.99), 4)


_IDENTITY_ISOTONIC_KNOTS = [
    (0.00, 0.00),
    (1.00, 1.00),
]
_ISOTONIC_KNOTS = list(_IDENTITY_ISOTONIC_KNOTS)
_MIN_ISOTONIC_SAMPLES = 500
_MIN_ISOTONIC_BUCKETS = 5
_MAX_ISOTONIC_BUCKET_SHARE = 0.80


def _isotonic_calibrate(model_prob: float) -> float:
    """Piecewise-linear lookup from raw model probability to empirical settlement
    rate, built from ~700 settled trades. Corrects the model's systematic
    overconfidence at both extremes."""
    p = max(0.0, min(1.0, model_prob))
    for i in range(1, len(_ISOTONIC_KNOTS)):
        x0, y0 = _ISOTONIC_KNOTS[i - 1]
        x1, y1 = _ISOTONIC_KNOTS[i]
        if p <= x1:
            t = (p - x0) / (x1 - x0) if x1 > x0 else 0.0
            return round(max(0.01, min(0.99, y0 + t * (y1 - y0))), 4)
    return round(max(0.01, min(0.99, _ISOTONIC_KNOTS[-1][1])), 4)


def rebuild_isotonic_calibration() -> dict:
    """Recompute isotonic knots from settled trade outcomes using raw (pre-calibration)
    model probabilities to avoid circular calibration."""
    try:
        from app.database import get_conn
        conn = get_conn()
        rows = conn.execute(
            """SELECT
                 ROUND(CAST(json_extract(a.details, '$.raw_model_prob') AS REAL) * 10) / 10.0 as bucket,
                 COUNT(*) as n,
                 AVG(CAST(json_extract(a.details, '$.raw_model_prob') AS REAL)) as avg_raw_prob,
                 AVG(CASE WHEN t.settlement_result='yes' THEN 1.0 ELSE 0.0 END) as actual_rate
               FROM trades t
               JOIN alerts a ON a.id = t.alert_id
               WHERE t.status='closed'
                 AND t.exit_reason='market_closed'
                 AND json_extract(a.details, '$.raw_model_prob') IS NOT NULL
               GROUP BY bucket
               HAVING n >= 3
               ORDER BY bucket"""
        ).fetchall()
        conn.close()
    except Exception as exc:
        logger.warning("Isotonic calibration rebuild failed: %s", exc)
        return {"updated": False, "error": str(exc)}

    global _ISOTONIC_KNOTS
    coverage = _isotonic_coverage(rows)
    if not coverage["usable"]:
        _ISOTONIC_KNOTS = list(_IDENTITY_ISOTONIC_KNOTS)
        return {"updated": False, **coverage}

    new_knots = _pava_isotonic_knots(rows)
    if len(new_knots) < 3:
        _ISOTONIC_KNOTS = list(_IDENTITY_ISOTONIC_KNOTS)
        return {"updated": False, "reason": "insufficient_monotonic_knots", **coverage}

    _ISOTONIC_KNOTS = new_knots
    logger.info("Isotonic calibration rebuilt with %d knots", len(new_knots))
    return {"updated": True, "knots": len(new_knots), **coverage}


def _isotonic_coverage(rows) -> dict:
    total = sum(int(r["n"] or 0) for r in rows)
    buckets = len(rows)
    max_bucket = max((int(r["n"] or 0) for r in rows), default=0)
    max_bucket_share = round(max_bucket / total, 4) if total else 0.0
    if total < _MIN_ISOTONIC_SAMPLES:
        return {
            "usable": False,
            "reason": "insufficient_total_samples",
            "samples": total,
            "buckets": buckets,
            "max_bucket_share": max_bucket_share,
        }
    if buckets < _MIN_ISOTONIC_BUCKETS:
        return {
            "usable": False,
            "reason": "insufficient_bucket_coverage",
            "samples": total,
            "buckets": buckets,
            "max_bucket_share": max_bucket_share,
        }
    if max_bucket_share > _MAX_ISOTONIC_BUCKET_SHARE:
        return {
            "usable": False,
            "reason": "concentrated_bucket_coverage",
            "samples": total,
            "buckets": buckets,
            "max_bucket_share": max_bucket_share,
        }
    return {
        "usable": True,
        "samples": total,
        "buckets": buckets,
        "max_bucket_share": max_bucket_share,
    }


def _pava_isotonic_knots(rows) -> list[tuple[float, float]]:
    blocks = []
    for row in rows:
        n = int(row["n"] or 0)
        if n <= 0:
            continue
        blocks.append({
            "n": n,
            "x_sum": float(row["avg_raw_prob"] if row["avg_raw_prob"] is not None else row["bucket"]) * n,
            "y_sum": float(row["actual_rate"] or 0.0) * n,
        })
        while len(blocks) >= 2:
            prev = blocks[-2]
            cur = blocks[-1]
            if prev["y_sum"] / prev["n"] <= cur["y_sum"] / cur["n"]:
                break
            merged = {
                "n": prev["n"] + cur["n"],
                "x_sum": prev["x_sum"] + cur["x_sum"],
                "y_sum": prev["y_sum"] + cur["y_sum"],
            }
            blocks[-2:] = [merged]

    interior = [
        (
            round(block["x_sum"] / block["n"], 4),
            round(max(0.01, min(0.99, block["y_sum"] / block["n"])), 4),
        )
        for block in blocks
    ]
    knots = [(0.0, 0.0)]
    for x, y in interior:
        if 0.0 < x < 1.0 and (not knots or x > knots[-1][0]):
            knots.append((x, y))
    knots.append((1.0, 1.0))
    return knots


def _estimate_model_prob(
    ticker: str,
    market_price: float,
    forecast: dict,
    segment: str,
    market: Optional[dict] = None,
    observed: Optional[dict] = None,
) -> Optional[float]:
    t = ticker.upper()

    if segment == "precipitation":
        precip_pct = forecast.get("precip_pct")
        if precip_pct is not None:
            if _requires_accumulation_model(ticker, market):
                return None
            base = precip_pct / 100.0
            return round(min(max(base * 0.90 + 0.05, 0.05), 0.95), 4)
        return None

    high = forecast.get("high")
    low = forecast.get("low")

    if high is None and low is None:
        return None

    hours = _hours_to_close(ticker, market)

    if "HIGH" in t and high is not None:
        sigma = _adaptive_sigma(9.0, hours, forecast)
        return _temp_market_prob(high, ticker, market, sigma=sigma, observed=observed)

    if "LOW" in t and low is not None:
        sigma = _adaptive_sigma(8.0, hours, forecast)
        return _temp_market_prob(low, ticker, market, sigma=sigma, observed=observed)

    return None


def _apply_calibration(model_prob: float, ticker: str, segment: str) -> Tuple[float, dict]:
    # DISABLED: City-level calibration biases are computed from 5-8 samples and
    # are massively noisy (+0.62 for LV). They flip 59 tradeable NO alerts to YES
    # (which is blocked at 0% accuracy), causing the bot to miss opportunities.
    # Re-enable once we have 50+ samples per city/segment with raw_model_prob.
    return model_prob, {"applied": False, "reason": "disabled_noisy_biases"}


def update_model_calibration() -> dict:
    """Recompute city x market-type probability bias from settled paper outcomes."""
    try:
        from app.database import get_conn
        conn = get_conn()
        rows = conn.execute(
            """SELECT t.market_ticker, t.exit_price,
                      a.model_prob AS alert_model_prob,
                      a.details AS alert_details
                 FROM trades t
                 LEFT JOIN alerts a ON a.id = t.alert_id
                WHERE t.paper=1
                  AND t.status IN ('closed','settled')
                  AND COALESCE(t.exit_reason, '') NOT IN ('paper_reset', 'bulk_cleanup')
                  AND t.clv IS NOT NULL
                  AND t.exit_price IS NOT NULL
                  AND t.exit_price IN (0.0, 1.0)
"""
        ).fetchall()
    except Exception as exc:
        logger.warning("Model calibration read failed: %s", exc)
        return {"updated": 0, "error": str(exc)}

    groups = {}
    for row in rows:
        try:
            details = json.loads(row["alert_details"] or "{}")
        except Exception:
            details = {}
        ticker = row["market_ticker"]
        city = details.get("city_code") or _city_code_from_ticker(ticker)
        market_type = details.get("segment") or _segment_from_ticker(ticker)
        model_prob = _to_float(row["alert_model_prob"])
        if model_prob is None:
            model_prob = _to_float(details.get("raw_model_prob"))
        if model_prob is None:
            model_prob = _to_float(details.get("model_prob"))
        exit_price = _to_float(row["exit_price"])
        if not city or not market_type or model_prob is None or exit_price not in (0.0, 1.0):
            continue
        actual = 1.0 if exit_price >= 0.5 else 0.0
        groups.setdefault((city, market_type), []).append((model_prob, actual))

    updated = 0
    skipped = 0
    conn.execute("DELETE FROM model_calibration")
    for (city, market_type), samples in groups.items():
        sample_count = len(samples)
        if sample_count < 5:
            skipped += 1
            continue
        avg_model_prob = round(sum(model for model, _actual in samples) / sample_count, 4)
        avg_settlement_rate = round(sum(actual for _model, actual in samples) / sample_count, 4)
        bias = round(avg_settlement_rate - avg_model_prob, 4)
        conn.execute(
            """INSERT INTO model_calibration
                   (city, market_type, sample_count, calibration_bias,
                    avg_model_prob, avg_settlement_rate, last_updated)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(city, market_type) DO UPDATE SET
                   sample_count=excluded.sample_count,
                   calibration_bias=excluded.calibration_bias,
                   avg_model_prob=excluded.avg_model_prob,
                   avg_settlement_rate=excluded.avg_settlement_rate,
                   last_updated=excluded.last_updated""",
            (city, market_type, sample_count, bias, avg_model_prob, avg_settlement_rate),
        )
        updated += 1

    conn.commit()
    conn.close()
    return {"updated": updated, "skipped_segments": skipped, "segments_seen": len(groups)}


def _temp_market_prob(
    forecast: float,
    ticker: str,
    market: Optional[dict],
    sigma: float,
    observed: Optional[dict] = None,
) -> Optional[float]:
    # Hard cap: never claim more than 92% certainty on a single-day settlement.
    # Station-vs-gridpoint variance and same-day surprises mean true confidence
    # almost never exceeds this — values above look fabricated and inflate edges.
    MAX_PROB = 0.92
    MIN_PROB = 1.0 - MAX_PROB

    strike_type, floor, cap = _extract_strike(market, ticker)
    if strike_type == "between" and floor is not None and cap is not None:
        lower = float(floor) - 0.5
        upper = float(cap) + 0.5
        raw = _normal_cdf(upper, forecast, sigma) - _normal_cdf(lower, forecast, sigma)
        prob = round(max(0.0, min(MAX_PROB, raw)), 4)
        return _apply_intraday_observation(
            prob, ticker, "between", lower, upper, observed, MIN_PROB, MAX_PROB
        )
    if strike_type == "greater" and floor is not None:
        raw = 1.0 - _normal_cdf(float(floor) + 0.5, forecast, sigma)
        prob = round(max(MIN_PROB, min(MAX_PROB, raw)), 4)
        return _apply_intraday_observation(
            prob, ticker, "greater", float(floor), None, observed, MIN_PROB, MAX_PROB
        )
    if strike_type == "less" and cap is not None:
        raw = _normal_cdf(float(cap) - 0.5, forecast, sigma)
        prob = round(max(MIN_PROB, min(MAX_PROB, raw)), 4)
        return _apply_intraday_observation(
            prob, ticker, "less", None, float(cap), observed, MIN_PROB, MAX_PROB
        )

    threshold = _extract_threshold(ticker, default=forecast)
    prob = _temp_exceed_prob(forecast, threshold, sigma=sigma)
    return round(max(MIN_PROB, min(MAX_PROB, prob)), 4)


# Time-of-day weighting for intraday observation confidence. Highs typically
# peak between 14:00 and 17:00 local; lows typically set between 04:00 and
# 07:00 local. After those windows the observed extreme is essentially final.
_HIGH_CONFIDENT_HOUR = 17  # after 5pm local, observed_high is near-final
_HIGH_PARTIAL_HOUR = 14    # 2-5pm: observation is meaningful but climb may continue
_LOW_CONFIDENT_HOUR = 10   # after 10am local, observed_low is essentially final


def _apply_intraday_observation(
    prob: float,
    ticker: str,
    strike_type: str,
    floor: Optional[float],
    cap: Optional[float],
    observed: Optional[dict],
    min_prob: float,
    max_prob: float,
) -> float:
    """Sharpen the forecast probability with the city's observed extremes.

    The bot was previously forecast-blind — it would happily bet against a
    HIGH bracket the city had already exceeded by hours of trading. This
    function lets observations override the forecast when they make the
    market resolution near-certain. Forecast-only mode is preserved when
    ``observed`` is missing or unavailable.
    """
    if not observed or not observed.get("available"):
        return prob

    upper = (ticker or "").upper()
    is_high = "HIGH" in upper
    is_low = "LOW" in upper
    observed_high = observed.get("observed_high")
    observed_low = observed.get("observed_low")
    hour = observed.get("local_hour")

    if is_high and observed_high is not None:
        # HIGH market: today's actual high cannot fall below observed_high.
        if strike_type == "between" and cap is not None:
            if observed_high > float(cap):
                # Bracket ceiling already exceeded → YES is impossible.
                return round(min_prob, 4)
            if hour is not None and hour >= _HIGH_CONFIDENT_HOUR:
                if floor is not None and observed_high < float(floor):
                    # Late in the day and still below the bracket floor.
                    # Unlikely to climb into the bracket overnight.
                    return round(min_prob, 4)
                if floor is not None and float(floor) <= observed_high <= float(cap):
                    # Late and already inside bracket → very likely to stay.
                    return round(max(prob, max_prob * 0.9), 4)
        elif strike_type == "greater" and floor is not None:
            if observed_high > float(floor):
                # High already cleared the strike → YES near-certain.
                return round(max_prob, 4)
            if hour is not None and hour >= _HIGH_CONFIDENT_HOUR and observed_high < float(floor):
                return round(min_prob, 4)
        elif strike_type == "less" and cap is not None:
            if observed_high > float(cap):
                return round(min_prob, 4)

    if is_low and observed_low is not None:
        # LOW market: today's actual low cannot rise above observed_low.
        if strike_type == "between" and floor is not None:
            if observed_low < float(floor):
                # Already below bracket floor → final low will be ≤ observed,
                # which is below floor, so bracket cannot contain it.
                return round(min_prob, 4)
            if hour is not None and hour >= _LOW_CONFIDENT_HOUR:
                if cap is not None and observed_low > float(cap):
                    # Late morning and still above bracket ceiling.
                    return round(min_prob, 4)
                if cap is not None and float(floor) <= observed_low <= float(cap):
                    return round(max(prob, max_prob * 0.9), 4)
        elif strike_type == "less" and cap is not None:
            if observed_low < float(cap):
                # LOW < strike, already cleared → YES near-certain.
                return round(max_prob, 4)
            if hour is not None and hour >= _LOW_CONFIDENT_HOUR and observed_low > float(cap):
                return round(min_prob, 4)
        elif strike_type == "greater" and floor is not None:
            if observed_low < float(floor):
                return round(min_prob, 4)

    return prob


def _extract_strike(market: Optional[dict], ticker: str) -> tuple[Optional[str], Optional[float], Optional[float]]:
    if market:
        strike_type = market.get("strike_type")
        floor = _to_float(market.get("floor_strike"))
        cap = _to_float(market.get("cap_strike"))
        if strike_type in ("between", "greater", "less"):
            return strike_type, floor, cap

    match = re.search(r"-B(\d+(?:\.\d+)?)$", ticker.upper())
    if match:
        mid = float(match.group(1))
        return "between", mid - 0.5, mid + 0.5

    match = re.search(r"-T(\d+(?:\.\d+)?)$", ticker.upper())
    if match:
        threshold = float(match.group(1))
        if "LOW" in ticker.upper():
            return "less", None, threshold
        return "greater", threshold, None

    return None, None, None


def _to_float(value) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _requires_accumulation_model(ticker: str, market: Optional[dict] = None) -> bool:
    upper = ticker.upper()
    text = " ".join(
        str((market or {}).get(k) or "")
        for k in ("title", "rules_primary", "yes_sub_title")
    ).lower()
    monthly_rain_series = upper.split("-")[0].startswith("KXRAIN") and upper.split("-")[0].endswith("M")
    return monthly_rain_series or "total precipitation" in text or " inches" in text



def _normal_cdf(value: float, mean: float, sigma: float) -> float:
    z = (value - mean) / sigma
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def _extract_threshold(ticker: str, default: float) -> float:
    nums = re.findall(r"\d+", ticker)
    candidates = [int(n) for n in nums if 10 <= int(n) <= 130]
    return float(candidates[-1]) if candidates else default


def _temp_exceed_prob(forecast: float, threshold: float, sigma: float = 4.0) -> float:
    z = (forecast - threshold) / sigma
    return round(0.5 * (1 + math.erf(z / math.sqrt(2))), 4)


def _estimate_confidence(
    forecast: dict,
    segment: str,
    model_prob: float = 0.5,
    hours_to_close: float = 24.0,
    event_bonus: float = 0.0,
) -> float:
    if segment == "precipitation":
        base = 0.55 if forecast.get("precip_pct") is not None else 0.30
    else:
        has_high = forecast.get("high") is not None
        has_low = forecast.get("low") is not None
        if has_high and has_low:
            base = 0.72
        elif has_high or has_low:
            base = 0.55
        else:
            return 0.30

    forecast_sources = _normalized_forecast_sources(forecast)
    num_sources = len(forecast_sources)
    if num_sources >= 2:
        base += 0.06
    elif "forecast_sources" in forecast and num_sources == 1:
        base -= 0.15

    if hours_to_close <= 6:
        base += 0.08
    elif hours_to_close <= 12:
        base += 0.04

    margin = abs(model_prob - 0.5)
    scale = 0.5 + margin
    if segment == "precipitation":
        disagreement = float(forecast.get("precip_source_disagreement") or 0.0)
        if disagreement >= 35.0:
            base -= 0.15
        elif disagreement >= 20.0:
            base -= 0.08
    else:
        disagreement = float(forecast.get("source_disagreement") or 0.0)
        if disagreement >= 5.0:
            base -= 0.15
        elif disagreement >= 3.0:
            base -= 0.08
    base += max(0.0, min(0.20, float(event_bonus or 0.0)))
    return round(min(max(base, 0.20) * scale, 0.95), 4)


def generate_analysis(ticker: str, result: dict, market: Optional[dict] = None) -> str:
    """Produce a human-readable explanation of why the model likes or dislikes this market."""
    forecast = result.get("forecast") or {}
    city = result.get("city_code") or "?"
    direction = result.get("direction", "yes")
    edge = result.get("edge", 0)
    confidence = result.get("confidence", 0)
    model_prob = result.get("model_prob", 0)
    market_price = result.get("market_price", 0)
    segment = result.get("segment", "")
    high = forecast.get("high")
    low = forecast.get("low")
    precip = forecast.get("precip_pct")
    sources = forecast.get("sources") or []
    disagreement = forecast.get("source_disagreement") or 0
    precip_disagree = forecast.get("precip_source_disagreement") or 0

    lines = []

    title = (market or {}).get("title") or ticker
    lines.append(f"**{title}**")

    source_str = " + ".join(sources) if sources else "forecast"
    if "high_bracket" in segment and high is not None:
        lines.append(f"{source_str} projects a high of {high:.0f}°F for {city}.")
    elif "low_bracket" in segment and low is not None:
        lines.append(f"{source_str} projects a low of {low:.0f}°F for {city}.")
    elif "precipitation" in segment and precip is not None:
        lines.append(f"{source_str} shows {precip:.0f}% chance of precipitation for {city}.")

    side_word = "YES" if direction == "yes" else "NO"
    lines.append(
        f"Model estimates {model_prob:.0%} true probability → {side_word} at "
        f"{market_price:.0%} market price = {abs(edge)*100:+.1f}¢ edge."
    )

    reasons = []
    if len(sources) >= 2 and disagreement <= 2.0:
        reasons.append("NWS and AccuWeather agree closely")
    elif len(sources) >= 2 and disagreement > 4.0:
        reasons.append(f"sources disagree by {disagreement:.1f}°F — lower conviction")
    if confidence >= 0.65:
        reasons.append("high forecast confidence")
    elif confidence < 0.40:
        reasons.append("low confidence — keep paper size at one contract")

    phantom_level = result.get("phantom_risk_level", "none")
    if phantom_level == "high":
        reasons.append("phantom risk is HIGH — the apparent edge may be stale data vs live pricing")
    elif phantom_level == "medium":
        reasons.append("moderate phantom risk — proceed with smaller size")

    if reasons:
        lines.append("Drivers: " + "; ".join(reasons) + ".")

    current = result.get("current_conditions") or {}
    obs_temp = current.get("temperature")
    settlement = current.get("settlement_station") or KALSHI_SETTLEMENT_STATIONS.get(city)
    if obs_temp is not None:
        station_label = current.get("station", city)
        lines.append(f"Current observed: {obs_temp:.0f}°F at {station_label}.")
    if settlement:
        lines.append(f"Settlement station: {settlement}. Kalshi settles on the NWS daily climate report from that station.")

    return " ".join(lines)
