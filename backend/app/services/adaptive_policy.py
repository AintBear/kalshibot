"""
Adaptive policy: segment-level entry-quality gates.

CLV is still the primary signal, but a segment is not policy-ready unless
positive CLV rate, recent CLV, and paper P&L also agree. A high average CLV from
a few large settlement wins should not greenlight automation while most entries
or the paper equity curve are still weak.
"""
import json
import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)

_CONFIDENCE_Z = 1.64


def _get_conn():
    from app.database import get_conn
    return get_conn()


def segment_keys_from_details(details: dict, direction: Optional[str] = None) -> list[str]:
    """Canonical segment key builder — used by position_sizing and auto_entry."""
    keys: list[str] = []
    brain = details.get("brain") or {}
    if brain.get("segment"):
        keys.append(str(brain["segment"]))
    ctx = details.get("analysis_context") or {}
    if ctx.get("segment_key"):
        keys.append(str(ctx["segment_key"]))
    segment = details.get("segment")
    bucket = details.get("time_bucket")
    base_key = None
    if segment and bucket:
        base_key = f"{segment}:{bucket}"
    elif segment:
        base_key = f"{segment}:all"
    if base_key:
        direction = (direction or details.get("direction") or "").lower()
        if direction in ("yes", "no"):
            keys.insert(0, f"{direction}:{base_key}")
        keys.append(base_key)
    deduped: list[str] = []
    for key in keys:
        if key and key not in deduped:
            deduped.append(key)
    return deduped


def lookup_adjustment(segment_key: str) -> dict:
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM adaptive_segments WHERE segment_key = ?",
        (segment_key,)
    ).fetchone()
    conn.close()

    if row is None:
        agg = _get_aggregate_snapshot()
        return {
            "segment_key": segment_key,
            "auto_eligible": False,
            "avg_clv": agg.get("avg_clv", 0.0),
            "avg_pnl": agg.get("avg_pnl", 0.0),
            "trade_count": 0,
            "fallback": True,
        }

    return {
        "segment_key": segment_key,
        "auto_eligible": bool(row["auto_eligible"]),
        "avg_clv": row["avg_clv"],
        "avg_pnl": row["avg_pnl"],
        "positive_clv_rate": _row_value(row, "positive_clv_rate", 0.0),
        "recent_avg_clv": _row_value(row, "recent_avg_clv", 0.0),
        "recent_positive_clv_rate": _row_value(row, "recent_positive_clv_rate", 0.0),
        "trade_count": row["trade_count"],
        "fallback": False,
    }


def describe_context(segment_key: str) -> dict:
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM adaptive_segments WHERE segment_key = ?",
        (segment_key,)
    ).fetchone()
    conn.close()

    if row is None:
        agg = _get_aggregate_snapshot()
        return {
            "segment_key": segment_key,
            "auto_eligible": False,
            "avg_clv": agg.get("avg_clv", 0.0),
            "avg_pnl": agg.get("avg_pnl", 0.0),
            "trade_count": 0,
            "learning_ready": False,
            "fallback": True,
        }

    auto_eligible = bool(row["auto_eligible"])
    return {
        "segment_key": segment_key,
        "auto_eligible": auto_eligible,
        "avg_clv": row["avg_clv"],
        "avg_pnl": row["avg_pnl"],
        "positive_clv_rate": _row_value(row, "positive_clv_rate", 0.0),
        "recent_avg_clv": _row_value(row, "recent_avg_clv", 0.0),
        "recent_positive_clv_rate": _row_value(row, "recent_positive_clv_rate", 0.0),
        "trade_count": row["trade_count"],
        "learning_ready": row["trade_count"] > 0,
        "fallback": False,
        "details": json.loads(row["details"]) if row["details"] else {},
    }


def get_segment_learning(segment_key: str) -> dict:
    """Return learned outcome stats for one segment with aggregate fallback."""
    ctx = describe_context(segment_key)
    details = ctx.get("details") or {}
    return {
        "segment_key": segment_key,
        "fallback": bool(ctx.get("fallback")),
        "auto_eligible": bool(ctx.get("auto_eligible")),
        "trade_count": int(ctx.get("trade_count") or 0),
        "avg_clv": float(ctx.get("avg_clv") or 0.0),
        "avg_pnl": float(ctx.get("avg_pnl") or 0.0),
        "positive_clv_rate": float(ctx.get("positive_clv_rate") or details.get("positive_clv_rate") or 0.0),
        "recent_avg_clv": float(ctx.get("recent_avg_clv") or details.get("recent_avg_clv") or 0.0),
        "recent_positive_clv_rate": float(
            ctx.get("recent_positive_clv_rate") or details.get("recent_positive_clv_rate") or 0.0
        ),
        "prediction_accuracy": float(details.get("prediction_accuracy") or 0.0),
        "prediction_sample_count": int(details.get("prediction_sample_count") or 0),
        "prediction_correct_count": int(details.get("prediction_correct_count") or 0),
        "stop_loss_rate": float(details.get("stop_loss_rate") or 0.0),
        "settlement_win_rate": float(details.get("settlement_win_rate") or 0.0),
        "lessons": details.get("lessons") or [],
    }


def _get_aggregate_snapshot() -> dict:
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM adaptive_segments WHERE segment_key = 'weather_all:all'"
    ).fetchone()
    conn.close()
    if row:
        return {"avg_clv": row["avg_clv"], "avg_pnl": row["avg_pnl"]}
    return {"avg_clv": 0.0, "avg_pnl": 0.0}


def get_all_segments() -> list:
    _ensure_segments_table()
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM adaptive_segments ORDER BY trade_count DESC").fetchall()
    conn.close()
    return [
        {
            "segment_key": r["segment_key"],
            "auto_eligible": bool(r["auto_eligible"]),
            "avg_clv": r["avg_clv"],
            "avg_pnl": r["avg_pnl"],
            "positive_clv_rate": _row_value(r, "positive_clv_rate", 0.0),
            "recent_avg_clv": _row_value(r, "recent_avg_clv", 0.0),
            "recent_positive_clv_rate": _row_value(r, "recent_positive_clv_rate", 0.0),
            "trade_count": r["trade_count"],
            "details": json.loads(r["details"]) if r["details"] else {},
        }
        for r in rows
    ]


def rebuild_snapshots() -> dict:
    _ensure_segments_table()
    conn = _get_conn()

    all_trades = conn.execute(
        """SELECT t.market_ticker, t.clv, t.pnl, t.exit_reason, t.exit_time,
                  t.prediction_correct,
                  a.details
           FROM trades t
           LEFT JOIN alerts a ON t.alert_id = a.id
           WHERE t.status IN ('closed','settled')
             AND COALESCE(t.exit_reason, '') NOT IN ('paper_reset', 'bulk_cleanup')
             AND (
                   t.prediction_correct IS NOT NULL
                OR t.clv IS NOT NULL
             )"""
    ).fetchall()

    segments: dict[str, list] = {}
    for row in all_trades:
        details = {}
        try:
            details = json.loads(row["details"] or "{}")
        except Exception:
            pass
        seg = details.get("segment", "weather_all")
        bucket = details.get("time_bucket", "all")
        direction = details.get("direction", "unknown")
        seg_key = f"{seg}:{bucket}"
        dir_seg_key = f"{direction}:{seg}:{bucket}"
        segments.setdefault("weather_all:all", []).append(row)
        if seg_key != "weather_all:all":
            segments.setdefault(seg_key, []).append(row)
        if direction in ("yes", "no"):
            segments.setdefault(dir_seg_key, []).append(row)

    results = {}
    conn.execute("DELETE FROM adaptive_segments")
    for seg_key, trades in segments.items():
        snap = _build_snapshot(seg_key, trades)
        conn.execute(
            """INSERT INTO adaptive_segments
               (segment_key, auto_eligible, avg_clv, avg_pnl,
                positive_clv_rate, recent_avg_clv, recent_positive_clv_rate,
                trade_count, details, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(segment_key) DO UPDATE SET
                 auto_eligible=excluded.auto_eligible,
                 avg_clv=excluded.avg_clv,
                 avg_pnl=excluded.avg_pnl,
                 positive_clv_rate=excluded.positive_clv_rate,
                 recent_avg_clv=excluded.recent_avg_clv,
                 recent_positive_clv_rate=excluded.recent_positive_clv_rate,
                 trade_count=excluded.trade_count,
                 details=excluded.details,
                 updated_at=excluded.updated_at""",
            (
                seg_key,
                int(snap["auto_eligible"]),
                snap["avg_clv"],
                snap["avg_pnl"],
                snap["details"].get("positive_clv_rate", 0.0),
                snap["details"].get("recent_avg_clv", 0.0),
                snap["details"].get("recent_positive_clv_rate", 0.0),
                snap["trade_count"],
                json.dumps(snap["details"]),
            ),
        )
        results[seg_key] = snap

    conn.commit()
    conn.close()
    return results


def _build_snapshot(segment_key: str, trades: list) -> dict:
    n = len(trades)
    if n == 0:
        return {"auto_eligible": False, "avg_clv": 0.0, "avg_pnl": 0.0, "trade_count": 0, "details": {}}

    clv_vals = [r["clv"] for r in trades if r["clv"] is not None]
    pnl_vals = [r["pnl"] for r in trades if r["pnl"] is not None]
    stop_loss_exits = sum(1 for r in trades if (r["exit_reason"] or "").lower() == "stop_loss")
    stop_loss_rate = round(stop_loss_exits / n, 4)
    settlement_trades = [r for r in trades if (r["exit_reason"] or "").lower() != "stop_loss"]
    settlement_wins = sum(1 for r in settlement_trades if (r["pnl"] or 0.0) > 0)
    settlement_total = len(settlement_trades)
    settlement_win_rate = round(settlement_wins / settlement_total, 4) if settlement_total > 0 else 0.0
    prediction_vals = [
        int(r["prediction_correct"])
        for r in trades
        if r["prediction_correct"] is not None
    ]
    prediction_sample_count = len(prediction_vals)
    prediction_correct_count = sum(prediction_vals)
    prediction_accuracy = (
        round(prediction_correct_count / prediction_sample_count, 4)
        if prediction_sample_count > 0 else 0.0
    )

    avg_clv = round(sum(clv_vals) / len(clv_vals), 4) if clv_vals else 0.0
    avg_pnl = round(sum(pnl_vals) / len(pnl_vals), 4) if pnl_vals else 0.0
    positive_clv_rate = round(sum(1 for v in clv_vals if v > 0) / len(clv_vals), 4) if clv_vals else 0.0
    clv_lower_bound = _mean_lower_bound(clv_vals, default_range=1.0)
    pnl_lower_bound = _mean_lower_bound(pnl_vals, default_range=1.0)
    prediction_lower_bound = _wilson_bound(prediction_correct_count, prediction_sample_count, upper=False)
    prediction_upper_bound = _wilson_bound(prediction_correct_count, prediction_sample_count, upper=True)
    positive_clv_lower_bound = _wilson_bound(
        sum(1 for v in clv_vals if v > 0),
        len(clv_vals),
        upper=False,
    )
    recent = sorted(
        [r for r in trades if r["clv"] is not None],
        key=lambda r: r["exit_time"] or "",
        reverse=True,
    )[:20]
    recent_clv_vals = [r["clv"] for r in recent if r["clv"] is not None]
    recent_avg_clv = round(sum(recent_clv_vals) / len(recent_clv_vals), 4) if recent_clv_vals else 0.0
    recent_positive_clv_rate = (
        round(sum(1 for v in recent_clv_vals if v > 0) / len(recent_clv_vals), 4)
        if recent_clv_vals else 0.0
    )

    clv_auto_eligible = clv_lower_bound > 0 and pnl_lower_bound >= 0 and positive_clv_lower_bound > 0.50
    prediction_bad = prediction_sample_count > 0 and prediction_upper_bound <= 0.50
    prediction_auto_eligible = prediction_sample_count > 0 and prediction_lower_bound > 0.50
    prediction_paper_eligible = prediction_sample_count > 0 and prediction_upper_bound > 0.50
    auto_eligible = (clv_auto_eligible or prediction_auto_eligible) and not prediction_bad
    paper_auto_eligible = not prediction_bad and (
        prediction_paper_eligible
        or clv_lower_bound > 0
        or (avg_clv > 0 and recent_avg_clv >= 0)
    )

    details = {
        "stop_loss_exits": stop_loss_exits,
        "stop_loss_rate": stop_loss_rate,
        "settlement_win_rate": settlement_win_rate,
        "settlement_total": settlement_total,
        "prediction_accuracy": prediction_accuracy,
        "prediction_sample_count": prediction_sample_count,
        "prediction_correct_count": prediction_correct_count,
        "prediction_lower_bound": prediction_lower_bound,
        "prediction_upper_bound": prediction_upper_bound,
        "positive_clv_rate": positive_clv_rate,
        "positive_clv_lower_bound": positive_clv_lower_bound,
        "recent_avg_clv": recent_avg_clv,
        "recent_positive_clv_rate": recent_positive_clv_rate,
        "clv_lower_bound": clv_lower_bound,
        "pnl_lower_bound": pnl_lower_bound,
        "policy_ready_checks": {
            "has_settlement_evidence": n > 0,
            "prediction_confidently_profitable": prediction_auto_eligible,
            "prediction_accuracy_not_bad": not prediction_bad,
            "clv_lower_bound_positive": clv_lower_bound > 0,
            "pnl_lower_bound_non_negative": pnl_lower_bound >= 0,
            "good_entry_lower_bound_above_break_even": positive_clv_lower_bound > 0.50,
            "recent_avg_clv_non_negative": recent_avg_clv >= 0,
        },
        "paper_auto_eligible": paper_auto_eligible,
    }
    stop_diag = _stop_loss_diagnostics(trades)
    if stop_diag:
        details["stop_loss_diagnostics"] = stop_diag

    lessons = _loss_lessons(stop_loss_rate, settlement_win_rate, avg_clv)
    if prediction_bad:
        lessons.append(
            f"Prediction confidence interval is below break-even "
            f"({prediction_accuracy * 100:.0f}% raw on {prediction_sample_count} outcomes); skip live sizing here."
        )
    elif prediction_auto_eligible:
        lessons.append(
            f"Prediction confidence interval clears break-even "
            f"({prediction_accuracy * 100:.0f}% raw on {prediction_sample_count} outcomes); this segment has directional edge."
        )
    if lessons:
        details["lessons"] = lessons

    return {
        "auto_eligible": auto_eligible,
        "avg_clv": avg_clv,
        "avg_pnl": avg_pnl,
        "trade_count": n,
        "details": details,
    }


def _mean_lower_bound(values: list, default_range: float = 1.0) -> float:
    if not values:
        return 0.0
    n = len(values)
    mean = sum(values) / n
    if n > 1:
        variance = sum((v - mean) ** 2 for v in values) / (n - 1)
        std = math.sqrt(max(0.0, variance))
    else:
        std = default_range
    margin = _CONFIDENCE_Z * max(std, 0.01) / math.sqrt(n)
    return round(mean - margin, 4)


def _wilson_bound(successes: int, total: int, upper: bool = False) -> float:
    if total <= 0:
        return 0.0
    p = successes / total
    z = _CONFIDENCE_Z
    denom = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    value = (centre + margin if upper else centre - margin) / denom
    return round(max(0.0, min(1.0, value)), 4)


def _stop_loss_diagnostics(trades: list) -> dict:
    stopped = [r for r in trades if (r["exit_reason"] or "").lower() == "stop_loss"]
    if not stopped:
        return {}

    warning_counts = {}
    edges = []
    confidences = []
    spreads = []
    for row in stopped:
        try:
            details = json.loads(row["details"] or "{}")
        except Exception:
            details = {}
        brain = details.get("brain") or {}
        for key in (brain.get("messages") or []) + (brain.get("cautions") or []):
            warning_counts[key] = warning_counts.get(key, 0) + 1
        for key, bucket in (
            ("edge", edges),
            ("confidence", confidences),
            ("spread", spreads),
        ):
            try:
                if details.get(key) is not None:
                    bucket.append(float(details.get(key)))
            except (TypeError, ValueError):
                pass

    top_warnings = sorted(warning_counts.items(), key=lambda kv: kv[1], reverse=True)[:4]
    avg_edge = round(sum(edges) / len(edges), 4) if edges else None
    avg_conf = round(sum(confidences) / len(confidences), 4) if confidences else None
    avg_spread = round(sum(spreads) / len(spreads), 4) if spreads else None

    lesson = "Stop-loss exits are showing adverse price movement before settlement."
    adjustment = "Keep similar future paper trades at one contract until recent entry quality turns positive."
    if top_warnings:
        readable = ", ".join(_readable_warning(k) for k, _ in top_warnings[:2])
        lesson = f"Most stopped trades carried these warnings at entry: {readable}."
    if avg_conf is not None and avg_conf < 0.5:
        adjustment = "Require cleaner forecast confidence or better price before sizing up this segment."
    if avg_spread is not None and avg_spread >= 0.08:
        adjustment = "Wide spreads are likely hurting entries; prefer tighter quotes or one-contract paper trades."

    return {
        "sample_count": len(stopped),
        "avg_entry_edge": avg_edge,
        "avg_confidence": avg_conf,
        "avg_spread": avg_spread,
        "top_warnings": [{"warning": key, "count": count} for key, count in top_warnings],
        "lesson": lesson,
        "adjustment": adjustment,
    }


def _readable_warning(key: str) -> str:
    labels = {
        "low_positive_clv_rate": "low good-entry rate",
        "recent_clv_positive": "recent entries improving",
        "recent_clv_negative": "recent entries weakening",
        "segment_flat_clv": "flat similar-trade edge",
        "segment_negative_clv": "negative similar-trade edge",
        "segment_positive_clv": "positive similar-trade edge",
    }
    return labels.get(str(key), str(key).replace("_", " "))


def _loss_lessons(stop_loss_rate: float, settlement_win_rate: float, avg_clv: float) -> list:
    lessons = []
    if stop_loss_rate >= 0.35:
        lessons.append(
            f"Stop-loss dominates exits ({round(stop_loss_rate * 100)}%). "
            "Consider wider stop or longer hold time. Entry quality means whether the price improves after entry, not only win/loss."
        )
    if settlement_win_rate < 0.40 and avg_clv > 0:
        lessons.append("Settlement win rate is low but entry prices are improving afterward, so timing is the issue.")
    return lessons


def _ensure_segments_table():
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS adaptive_segments (
            segment_key TEXT PRIMARY KEY,
            auto_eligible INTEGER DEFAULT 0,
            avg_clv REAL DEFAULT 0.0,
            avg_pnl REAL DEFAULT 0.0,
            positive_clv_rate REAL DEFAULT 0.0,
            recent_avg_clv REAL DEFAULT 0.0,
            recent_positive_clv_rate REAL DEFAULT 0.0,
            trade_count INTEGER DEFAULT 0,
            details TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    _ensure_column(conn, "positive_clv_rate", "REAL DEFAULT 0.0")
    _ensure_column(conn, "recent_avg_clv", "REAL DEFAULT 0.0")
    _ensure_column(conn, "recent_positive_clv_rate", "REAL DEFAULT 0.0")
    conn.commit()
    conn.close()


def _ensure_column(conn, column: str, ddl: str):
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(adaptive_segments)").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE adaptive_segments ADD COLUMN {column} {ddl}")


def _row_value(row, key: str, default=None):
    try:
        return row[key]
    except (KeyError, IndexError):
        return default
