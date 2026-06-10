"""Per-city forecast skill: learned error distributions for the weather model.

Root-cause finding (2026-06-10 audit, scripts/analysis/ws1_*.py +
sigma analysis): measured near-close forecast error is sigma ~2.2F for
HIGH series and ~4.0F for LOW series, while the model ran a global
base_sigma of 9.0/8.0 — 2-4x too wide. That single constant produced the
"raw 0.10 settles YES 0.35" miscalibration: a bracket near the forecast
has ~35% true probability at sigma 2.2 but the model called it ~9% at
sigma 9. Per-city spread is real and large (Vegas highs sigma 0.74 vs DC
lows 6.9), so one global number can never be right.

This module learns (series, kind) -> (bias, error_std) from settled YES
bracket markets (the bracket that settled YES pins the actual temperature
within 1F) joined against the forecast stored in each event's latest
alert. Safeguards mirror the slice-calibration ones:

  - apply only when a series has >= MIN_SAMPLES settled events;
  - blend toward the global per-kind sigma below RAMP_FULL samples;
  - clamp the result to [SIGMA_FLOOR, legacy base] — the learned sigma can
    sharpen the model but never make it wilder than the legacy constant;
  - if the global sample itself is thin (< GLOBAL_MIN), change nothing.

Also backfills `forecast_snapshots.actual_high/actual_low/resolved` from
the same settled-bracket truth so the proper lead-time-aware dataset
accumulates going forward (the table had 11k rows and zero resolved).
"""
import json
import logging
import math
import re
import time
from collections import defaultdict
from typing import Optional

logger = logging.getLogger(__name__)

MIN_SAMPLES = 8
RAMP_FULL = 20
GLOBAL_MIN = 30
SIGMA_FLOOR = 1.0
LEGACY_BASE = {"high": 9.0, "low": 8.0}

_BRACKET_RE = re.compile(r"^(KX[A-Z]+)-(\d\d[A-Z]{3}\d\d)-B([\d.]+)$")
_EVENT_RE = re.compile(r"^(KX[A-Z]+)-(\d\d[A-Z]{3}\d\d)-")

_CACHE: dict = {"ts": 0.0, "rows": {}, "globals": {}}
_CACHE_TTL = 600


def _get_conn():
    from app.database import get_conn
    return get_conn()


def _series_kind(series: str) -> str:
    return "low" if "LOW" in (series or "").upper() else "high"


def _settled_actuals(conn) -> dict:
    """(series, eventdate) -> actual temp (YES bracket midpoint)."""
    actual = {}
    rows = conn.execute(
        """SELECT market_ticker AS t FROM trades
            WHERE settlement_result='yes' AND market_ticker LIKE '%-B%'
           UNION
           SELECT ticker AS t FROM markets WHERE result='yes' AND ticker LIKE '%-B%'"""
    ).fetchall()
    for r in rows:
        m = _BRACKET_RE.match(r["t"] or "")
        if m:
            actual[(m.group(1), m.group(2))] = float(m.group(3))
    return actual


def rebuild_city_skill() -> dict:
    """Recompute the city_forecast_skill table from settled evidence."""
    conn = _get_conn()
    try:
        actual = _settled_actuals(conn)
        if not actual:
            return {"updated": 0, "reason": "no settled YES brackets"}

        # Latest alert forecast per settled event.
        fc = {}
        rows = conn.execute(
            "SELECT market_ticker, details, updated_at FROM alerts WHERE details LIKE '%forecast%'"
        ).fetchall()
        for r in rows:
            m = _EVENT_RE.match(r["market_ticker"] or "")
            if not m:
                continue
            key = (m.group(1), m.group(2))
            if key not in actual:
                continue
            if key in fc and (fc[key][1] or "") >= (r["updated_at"] or ""):
                continue
            try:
                d = json.loads(r["details"] or "{}")
            except (TypeError, ValueError):
                continue
            f = d.get("forecast") or {}
            fc[key] = (f, r["updated_at"])

        errors = defaultdict(list)
        for (series, date), (f, _ts) in fc.items():
            kind = _series_kind(series)
            pred = f.get(kind)
            if pred is None:
                continue
            errors[(series, kind)].append(float(pred) - actual[(series, date)])

        updated = 0
        for (series, kind), vals in errors.items():
            n = len(vals)
            if n < 2:
                continue
            mean = sum(vals) / n
            sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / (n - 1))
            conn.execute(
                """INSERT INTO city_forecast_skill (series, kind, sample_count, bias, error_std, updated_at)
                   VALUES (?, ?, ?, ?, ?, datetime('now'))
                   ON CONFLICT(series, kind) DO UPDATE SET
                     sample_count=excluded.sample_count,
                     bias=excluded.bias,
                     error_std=excluded.error_std,
                     updated_at=excluded.updated_at""",
                (series, kind, n, round(mean, 4), round(sd, 4)),
            )
            updated += 1
        conn.commit()
        _CACHE["ts"] = 0.0  # invalidate lookup cache
        return {"updated": updated, "events_with_actuals": len(actual)}
    finally:
        conn.close()


def _load_cache():
    now = time.time()
    if now - _CACHE["ts"] < _CACHE_TTL:
        return
    conn = _get_conn()
    try:
        rows = conn.execute("SELECT * FROM city_forecast_skill").fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()
    table = {}
    by_kind = defaultdict(list)
    for r in rows:
        table[(r["series"], r["kind"])] = {
            "n": int(r["sample_count"] or 0),
            "bias": float(r["bias"] or 0.0),
            "sd": float(r["error_std"] or 0.0),
        }
        by_kind[r["kind"]].append((int(r["sample_count"] or 0), float(r["error_std"] or 0.0)))
    glob = {}
    for kind, pairs in by_kind.items():
        total = sum(n for n, _sd in pairs)
        if total >= GLOBAL_MIN:
            # Sample-weighted pooled sd (approximation: weighted mean of sds).
            glob[kind] = {"n": total, "sd": sum(n * sd for n, sd in pairs) / total}
    _CACHE.update({"ts": now, "rows": table, "globals": glob})


def base_sigma_for(ticker: str, kind: str) -> float:
    """Learned base sigma for a market, falling back to the legacy constant.

    Never returns a value wider than the legacy base — learning can only
    sharpen the model, not inflate uncertainty beyond the historical default.
    """
    legacy = LEGACY_BASE.get(kind, 9.0)
    try:
        m = _EVENT_RE.match((ticker or "").upper())
        series = m.group(1) if m else (ticker or "").upper().split("-")[0]
        _load_cache()
        glob = _CACHE["globals"].get(kind)
        if not glob:
            return legacy
        row = _CACHE["rows"].get((series, kind))
        if row and row["n"] >= MIN_SAMPLES and row["sd"] > 0:
            w = min(1.0, row["n"] / float(RAMP_FULL))
            sigma = w * row["sd"] + (1.0 - w) * glob["sd"]
        else:
            sigma = glob["sd"]
        return round(min(legacy, max(SIGMA_FLOOR, sigma)), 2)
    except Exception as exc:
        logger.debug("base_sigma_for fallback for %s/%s: %s", ticker, kind, exc)
        return legacy


def backfill_forecast_actuals() -> dict:
    """Resolve forecast_snapshots rows whose event has a settled YES bracket."""
    conn = _get_conn()
    try:
        actual = _settled_actuals(conn)
        if not actual:
            return {"resolved": 0}
        pending = conn.execute(
            "SELECT id, market_ticker FROM forecast_snapshots WHERE resolved=0 OR resolved IS NULL"
        ).fetchall()
        resolved = 0
        for r in pending:
            m = _EVENT_RE.match(r["market_ticker"] or "")
            if not m:
                continue
            key = (m.group(1), m.group(2))
            temp = actual.get(key)
            if temp is None:
                continue
            kind = _series_kind(m.group(1))
            column = "actual_low" if kind == "low" else "actual_high"
            conn.execute(
                f"UPDATE forecast_snapshots SET {column}=?, resolved=1 WHERE id=?",
                (temp, r["id"]),
            )
            resolved += 1
        conn.commit()
        return {"resolved": resolved, "events_with_actuals": len(actual)}
    finally:
        conn.close()
