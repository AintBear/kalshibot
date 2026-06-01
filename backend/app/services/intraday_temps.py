"""Intraday temperature observations from Open-Meteo.

The bot was historically forecast-blind: it knew tomorrow's predicted high
but did not check what the actual temperature had already done today. This
module fills that gap by fetching the hourly observed temperatures for a
city's calendar day and exposing the running high-so-far / low-so-far /
current temp.

The weather model uses these to:
- skip trades against already-resolved brackets (high already over ceiling,
  low already under floor)
- tighten probabilities late in the day when the observation is near-final

Open-Meteo serves this free with no key and no auth.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import requests

from app import config as cfg

logger = logging.getLogger(__name__)


OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# (lat_rounded, lon_rounded, target_date_iso) -> (cached_payload, fetched_at_epoch)
_CACHE: dict[tuple, tuple[dict, float]] = {}
_CACHE_LOCK = threading.Lock()


def _settings() -> dict:
    try:
        return cfg.load()
    except Exception:
        return {}


def _cache_seconds() -> int:
    try:
        return int(_settings().get("intraday_temps_cache_seconds") or 600)
    except Exception:
        return 600


def _enabled() -> bool:
    return bool(_settings().get("intraday_temps_enabled", True))


def _coord_key(lat: float, lon: float) -> tuple[float, float]:
    return (round(float(lat), 3), round(float(lon), 3))


def _cache_get(lat: float, lon: float, target_date: str) -> Optional[dict]:
    key = (*_coord_key(lat, lon), target_date)
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if not entry:
            return None
        payload, ts = entry
        if (time.time() - ts) > _cache_seconds():
            _CACHE.pop(key, None)
            return None
        # Return a shallow copy so callers can't mutate the cached dict.
        return dict(payload)


def _cache_set(lat: float, lon: float, target_date: str, payload: dict) -> None:
    key = (*_coord_key(lat, lon), target_date)
    with _CACHE_LOCK:
        _CACHE[key] = (dict(payload), time.time())


def clear_cache() -> None:
    """Test hook."""
    with _CACHE_LOCK:
        _CACHE.clear()


def _empty(reason: str) -> dict:
    return {
        "available": False,
        "observed_high": None,
        "observed_low": None,
        "current_temp": None,
        "local_hour": None,
        "latest_obs_iso": None,
        "samples": 0,
        "reason": reason,
    }


def get_observed_extremes(lat: float, lon: float, target_date: str) -> dict:
    """Return today's observed high/low/current for the given coords.

    target_date is an ISO calendar date string (e.g. "2026-06-02") in the
    city's local timezone. Open-Meteo returns hourly observed temps; we
    take the max/min over the city-local hours of target_date that have
    already elapsed.

    Returns a dict with ``available=False`` when the API fails, the call
    is disabled, or no usable hourly data exists yet for target_date.
    """
    if not _enabled():
        return _empty("disabled")

    if lat is None or lon is None or not target_date:
        return _empty("missing_inputs")

    cached = _cache_get(lat, lon, target_date)
    if cached is not None:
        return cached

    params = {
        "latitude": float(lat),
        "longitude": float(lon),
        "hourly": "temperature_2m",
        "current": "temperature_2m",
        "temperature_unit": "fahrenheit",
        "timezone": "auto",
        "past_days": 1,
        "forecast_days": 1,
    }

    try:
        resp = requests.get(OPEN_METEO_URL, params=params, timeout=8)
        resp.raise_for_status()
        data = resp.json() or {}
    except Exception as exc:
        logger.warning("intraday_temps: open-meteo fetch failed lat=%s lon=%s: %s", lat, lon, exc)
        result = _empty(f"fetch_error:{type(exc).__name__}")
        _cache_set(lat, lon, target_date, result)
        return result

    hourly = data.get("hourly") or {}
    times: list[str] = hourly.get("time") or []
    temps = hourly.get("temperature_2m") or []
    if len(times) != len(temps) or not times:
        result = _empty("no_hourly_data")
        _cache_set(lat, lon, target_date, result)
        return result

    # Parse the city's local time so we slice the right calendar day.
    # Open-Meteo emits hourly timestamps as naive "YYYY-MM-DDTHH:MM" in the
    # city's local timezone when timezone=auto is set, plus a current block.
    current = data.get("current") or {}
    current_local = current.get("time")  # e.g. "2026-06-01T18:00"
    current_temp = current.get("temperature_2m")

    try:
        local_now = datetime.fromisoformat(current_local) if current_local else None
    except Exception:
        local_now = None

    if local_now is None:
        # Best-effort: assume the last hourly entry corresponds to "now".
        try:
            local_now = datetime.fromisoformat(times[-1])
        except Exception:
            result = _empty("unparseable_time")
            _cache_set(lat, lon, target_date, result)
            return result

    observed_temps: list[float] = []
    latest_obs_iso: Optional[str] = None
    for ts, temp in zip(times, temps):
        if temp is None:
            continue
        if not ts.startswith(target_date):
            continue
        try:
            ts_dt = datetime.fromisoformat(ts)
        except Exception:
            continue
        # Only count hours that have already happened in the city's local time.
        if ts_dt > local_now:
            continue
        observed_temps.append(float(temp))
        latest_obs_iso = ts

    if not observed_temps:
        result = _empty("no_elapsed_hours_yet")
        _cache_set(lat, lon, target_date, result)
        return result

    result = {
        "available": True,
        "observed_high": round(max(observed_temps), 2),
        "observed_low": round(min(observed_temps), 2),
        "current_temp": round(float(current_temp), 2) if current_temp is not None else None,
        "local_hour": local_now.hour,
        "latest_obs_iso": latest_obs_iso,
        "samples": len(observed_temps),
        "reason": None,
    }
    _cache_set(lat, lon, target_date, result)
    return result
