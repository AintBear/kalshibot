"""
Weather brain: evaluates alerts against adaptive policy, applies trust score adjustments.
Phantom risk penalises trust score (-15 high, -6 medium) but does NOT block alerts.
"""
import json
import logging
from typing import Optional

from app.services import adaptive_policy

logger = logging.getLogger(__name__)

BASE_SCORE = 100


def evaluate_alert(
    ticker: str,
    edge: float,
    direction: str,
    market_price: float,
    model_prob: float,
    confidence: float,
    segment: str,
    time_bucket: str,
    phantom_risk: Optional[dict] = None,
    details: Optional[dict] = None,
) -> dict:
    side_entry, side_prob, side_edge, max_gain, max_loss = _side_terms(direction, market_price, model_prob)
    expected_value = round((side_prob * max_gain) - ((1 - side_prob) * max_loss), 4)

    score = 45
    messages = []
    cautions = []

    edge_points = min(24, max(0.0, side_edge) * 140)
    score += edge_points

    confidence_points = max(0, min(14, confidence * 14))
    score += confidence_points

    if confidence < 0.40:
        score -= 16
        messages.append("low_confidence_model")
    elif confidence < 0.60:
        score -= 7
        messages.append("moderate_confidence")

    if side_edge < 0.03:
        score -= 22
        messages.append("thin_edge")
    elif side_edge < 0.06:
        score -= 9
        messages.append("modest_edge")

    if max_gain < 0.04:
        score -= 24
        messages.append("tiny_payout")
        cautions.append("tiny_payout")
    elif max_gain < 0.10:
        score -= 10
        messages.append("small_payout")

    if side_entry < 0.03 or side_entry > 0.97:
        score -= 10
        messages.append("extreme_quote")

    details = details or {}
    spread = details.get("spread")
    if spread is not None:
        try:
            spread = float(spread)
            if spread >= 0.15:
                score -= 10
                cautions.append("wide_spread")
                messages.append("wide_spread")
            elif spread >= 0.08:
                score -= 5
                messages.append("moderate_spread")
        except (TypeError, ValueError):
            pass

    phantom_risk = phantom_risk or {}
    phantom_level = phantom_risk.get("level", "none")
    if phantom_level == "high":
        score -= 30
        cautions.append("phantom_risk_high")
        messages.append("phantom_risk_high")
    elif phantom_level == "medium":
        score -= 16
        cautions.append("phantom_risk_medium")
        messages.append("phantom_risk_medium")
    elif phantom_level == "low":
        score -= 5
        messages.append("phantom_risk_low")

    segment_key = f"{segment}:{time_bucket}"
    adj = adaptive_policy.lookup_adjustment(segment_key)
    auto_eligible = adj.get("auto_eligible", False)
    learned = adaptive_policy.get_segment_learning(segment_key)
    learned_avg_pnl = float(learned.get("avg_pnl") or 0.0)
    prediction_count = int(learned.get("prediction_sample_count") or 0)
    prediction_accuracy = float(learned.get("prediction_accuracy") or 0.0)
    prediction_bad = prediction_count >= 10 and prediction_accuracy < 0.40
    prediction_good = prediction_count >= 10 and prediction_accuracy >= 0.55

    if learned["trade_count"] >= 5:
        score += 5
        if learned["avg_clv"] >= 0.020:
            score += 10
            messages.append("segment_positive_clv")
        elif learned["avg_clv"] >= 0:
            score += 4
            messages.append("segment_flat_clv")
        else:
            score -= 12
            cautions.append("segment_negative_clv")
            messages.append("segment_negative_clv")

        if learned["recent_avg_clv"] < 0:
            score -= 6
            cautions.append("recent_clv_negative")
            messages.append("recent_clv_negative")
        elif learned["recent_avg_clv"] > 0:
            score += 5
            messages.append("recent_clv_positive")

        if learned["positive_clv_rate"] < 0.35:
            score -= 7
            cautions.append("low_positive_clv_rate")
            messages.append("low_positive_clv_rate")
        elif learned["positive_clv_rate"] >= 0.50:
            score += 6

        if learned["positive_clv_rate"] < 0.50:
            cautions.append("positive_clv_rate_below_live_gate")
            messages.append("positive_clv_rate_below_live_gate")
        if learned_avg_pnl < 0:
            score -= 6
            cautions.append("segment_paper_pnl_negative")
            messages.append("segment_paper_pnl_negative")

    if prediction_count >= 10:
        if prediction_accuracy >= 0.60:
            score += 14
            messages.append("segment_prediction_accuracy_strong")
        elif prediction_accuracy >= 0.55:
            score += 8
            messages.append("segment_prediction_accuracy_positive")
        elif prediction_bad:
            score -= 24
            cautions.append("segment_prediction_accuracy_low")
            messages.append("segment_prediction_accuracy_low")

    if auto_eligible:
        score += 8

    score = int(round(max(0, min(100, score))))
    if learned["trade_count"] >= 10:
        if prediction_bad:
            score = min(score, 44)
        elif not prediction_good and (learned["positive_clv_rate"] < 0.50 or learned["recent_avg_clv"] < 0):
            score = min(score, 64)
        if not prediction_good and learned_avg_pnl < 0:
            score = min(score, 59)

    brain_state = _score_to_state(score)
    quality_gate = (
        (
            learned["positive_clv_rate"] >= 0.50
            and learned["recent_avg_clv"] >= 0.0
            and learned_avg_pnl >= 0.0
        )
        or prediction_good
    )
    auto_qualified = (
        auto_eligible
        and score >= 82
        and side_edge >= 0.06
        and phantom_level != "high"
        and not prediction_bad
        and quality_gate
    )

    return {
        "score": score,
        "state": brain_state,
        "auto_qualified": auto_qualified,
        "auto_eligible": auto_eligible,
        "segment": segment_key,
        "messages": messages,
        "cautions": cautions,
        "phantom_risk": {
            "level": phantom_level,
            "score": phantom_risk.get("score", 0.0),
            "flags": phantom_risk.get("flags", []),
        },
        "adjustment": adj,
        "learned": learned,
        "market_read": {
            "entry_price": round(side_entry, 4),
            "model_side_probability": round(side_prob, 4),
            "side_edge": round(side_edge, 4),
            "max_gain": round(max_gain, 4),
            "max_loss": round(max_loss, 4),
            "expected_value": expected_value,
        },
        "components": {
            "base": 45,
            "edge_points": round(edge_points, 1),
            "confidence_points": round(confidence_points, 1),
            "learned_avg_clv": learned["avg_clv"],
            "learned_positive_clv_rate": learned["positive_clv_rate"],
            "learned_recent_clv": learned["recent_avg_clv"],
            "learned_avg_pnl": learned_avg_pnl,
            "learned_prediction_accuracy": prediction_accuracy,
            "learned_prediction_samples": prediction_count,
        },
    }


def _score_to_state(score: int) -> str:
    if score >= 82:
        return "paper_ready"
    if score >= 65:
        return "watch"
    if score >= 45:
        return "caution"
    return "skip"


def _side_terms(direction: str, market_price: float, model_prob: float) -> tuple[float, float, float, float, float]:
    yes_price = max(0.0, min(1.0, float(market_price or 0.0)))
    yes_prob = max(0.0, min(1.0, float(model_prob or 0.0)))
    if direction == "no":
        entry = 1.0 - yes_price
        prob = 1.0 - yes_prob
    else:
        entry = yes_price
        prob = yes_prob
    edge = prob - entry
    return entry, prob, edge, max(0.0, 1.0 - entry), max(0.0, entry)


def get_brain_status() -> dict:
    from app.database import get_conn
    conn = get_conn()

    total_trades = conn.execute("SELECT COUNT(*) FROM trades WHERE paper=1").fetchone()[0]
    open_trades = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE paper=1 AND status='open'"
    ).fetchone()[0]
    closed_trades = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE paper=1 AND status IN ('closed','settled')"
    ).fetchone()[0]
    _exclude = "AND COALESCE(exit_reason, '') NOT IN ('paper_reset', 'bulk_cleanup')"
    _strategy = """AND direction = 'no'
              AND entry_price >= 0.20 AND entry_price < 0.40
              AND market_ticker NOT GLOB '*-T[0-9]*'"""
    learning_samples = conn.execute(
        f"""SELECT COUNT(*) FROM trades
            WHERE paper=1
              AND clv IS NOT NULL
              AND status IN ('closed','settled')
              {_exclude}
              {_strategy}
"""
    ).fetchone()[0]
    pending_settlement = conn.execute(
        f"""SELECT COUNT(*) FROM trades
            WHERE paper=1
              AND clv IS NULL
              AND status IN ('closed','settled')
              {_exclude}"""
    ).fetchone()[0]
    excluded_reset_trades = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE paper=1 AND status IN ('closed','settled') AND exit_reason IN ('paper_reset', 'bulk_cleanup')"
    ).fetchone()[0]

    clv_row = conn.execute(
        f"""SELECT AVG(clv) FROM trades
            WHERE paper=1
              AND clv IS NOT NULL
              AND status IN ('closed','settled')
              {_exclude}
              {_strategy}
"""
    ).fetchone()
    avg_clv = round((clv_row[0] or 0.0) * 100, 2)

    pnl_row = conn.execute(
        f"""SELECT COALESCE(SUM(pnl), 0.0), AVG(pnl)
            FROM trades
            WHERE paper=1
              AND pnl IS NOT NULL
              AND status IN ('closed','settled')
              {_exclude}
              {_strategy}
"""
    ).fetchone()
    realized_pnl = round(float(pnl_row[0] or 0.0), 2)
    avg_pnl = round(float(pnl_row[1] or 0.0), 4)

    recent_row = conn.execute(
        f"""SELECT AVG(clv) FROM (
            SELECT clv FROM trades
             WHERE paper=1
               AND clv IS NOT NULL
               AND status IN ('closed','settled')
               {_exclude}
               {_strategy}
            ORDER BY exit_time DESC LIMIT 30
        )"""
    ).fetchone()
    recent_clv = round((recent_row[0] or 0.0) * 100, 2)

    recent_pnl_row = conn.execute(
        f"""SELECT COALESCE(SUM(pnl), 0.0) FROM (
            SELECT pnl FROM trades
             WHERE paper=1
               AND pnl IS NOT NULL
               AND status IN ('closed','settled')
               {_exclude}
               {_strategy}
             ORDER BY exit_time DESC LIMIT 30
        )"""
    ).fetchone()
    recent_pnl = round(float(recent_pnl_row[0] or 0.0), 2)
    recent_avg_profit_row = conn.execute(
        """SELECT AVG(pnl) FROM (
            SELECT pnl FROM trades
             WHERE paper=1
               AND pnl IS NOT NULL
               AND status IN ('closed','settled')
               AND COALESCE(exit_reason, '') NOT IN ('paper_reset', 'bulk_cleanup')
             ORDER BY exit_time DESC LIMIT 30
        )"""
    ).fetchone()
    recent_avg_profit = round(float(recent_avg_profit_row[0] or 0.0), 4)

    positive_clv = conn.execute(
        f"""SELECT COUNT(*) FROM trades
            WHERE paper=1
              AND clv > 0
              AND status IN ('closed','settled')
              {_exclude}
              {_strategy}
"""
    ).fetchone()[0]
    positive_clv_rate = round(positive_clv / learning_samples, 4) if learning_samples > 0 else 0.0

    pred_row = conn.execute(
        f"""SELECT SUM(CASE WHEN prediction_correct=1 THEN 1 ELSE 0 END), COUNT(*)
            FROM trades
            WHERE paper=1
              AND prediction_correct IS NOT NULL
              AND status IN ('closed','settled')
              {_exclude}
              {_strategy}
"""
    ).fetchone()
    prediction_correct_count = int(pred_row[0] or 0) if pred_row else 0
    prediction_sample_count = int(pred_row[1] or 0) if pred_row else 0
    prediction_accuracy = round(prediction_correct_count / prediction_sample_count, 4) if prediction_sample_count > 0 else 0.0

    recent_pred_row = conn.execute(
        f"""SELECT SUM(CASE WHEN prediction_correct=1 THEN 1 ELSE 0 END), COUNT(*)
           FROM (SELECT prediction_correct FROM trades
                  WHERE paper=1
                    AND prediction_correct IS NOT NULL
                    AND status IN ('closed','settled')
                    {_exclude}
                    {_strategy}
                  ORDER BY exit_time DESC LIMIT 100)"""
    ).fetchone()
    recent_pred_correct = int(recent_pred_row[0] or 0) if recent_pred_row else 0
    recent_pred_count = int(recent_pred_row[1] or 0) if recent_pred_row else 0
    recent_prediction_accuracy = round(recent_pred_correct / recent_pred_count, 4) if recent_pred_count > 0 else prediction_accuracy

    recent_positive_clv_row = conn.execute(
        f"""SELECT COUNT(*), SUM(CASE WHEN clv > 0 THEN 1 ELSE 0 END)
           FROM (SELECT clv FROM trades
                  WHERE paper=1
                    AND clv IS NOT NULL
                    AND status IN ('closed','settled')
                    {_exclude}
                    {_strategy}
                  ORDER BY exit_time DESC LIMIT 100)"""
    ).fetchone()
    recent_positive_clv_total = int(recent_positive_clv_row[0] or 0)
    recent_positive_clv_count = int(recent_positive_clv_row[1] or 0)
    recent_positive_clv_rate = round(recent_positive_clv_count / recent_positive_clv_total, 4) if recent_positive_clv_total > 0 else positive_clv_rate

    refresh_segments = bool(conn.execute(
        """SELECT EXISTS (
              SELECT 1
                FROM trades
               WHERE paper=1
                 AND clv IS NOT NULL
                 AND status IN ('closed','settled')
                 AND COALESCE(exit_reason, '') != 'paper_reset'
                 AND datetime(coalesce(exit_time, entry_time, '1970-01-01')) >
                     datetime(coalesce((SELECT MAX(updated_at) FROM adaptive_segments), '1970-01-01'))
            )"""
    ).fetchone()[0])

    conn.close()

    if refresh_segments:
        adaptive_policy.rebuild_snapshots()

    segments = adaptive_policy.get_all_segments()
    auto_eligible_count = sum(1 for s in segments if s.get("auto_eligible"))
    paper_auto_eligible_count = sum(
        1
        for s in segments
        if (s.get("details") or {}).get("paper_auto_eligible")
    )

    # Live-readiness gate: paper automation can keep collecting small samples,
    # but real-money readiness still requires the current entry-quality signals
    # to agree.
    _best_segment_ok = any(
        s.get("trade_count", 0) >= 5
        and float(s.get("recent_avg_clv") or 0.0) >= 0.0
        and float(s.get("positive_clv_rate") or 0.0) >= 0.40
        for s in segments
    )
    entry_quality_ok = learning_samples >= 20 and (
        avg_clv >= 0.0
        and recent_clv >= 0.0
        and positive_clv_rate >= 0.50
        and _best_segment_ok
    )
    learning_active = total_trades > 0 or learning_samples > 0
    lessons = []
    for seg in segments[:4]:
        for lesson in (seg.get("details") or {}).get("lessons", []):
            if lesson not in lessons:
                lessons.append(lesson)

    score = _compute_brain_score(
        settled=learning_samples,
        avg_clv=avg_clv,
        recent_clv=recent_clv,
        positive_clv_rate=positive_clv_rate,
        realized_pnl=realized_pnl,
        recent_pnl=recent_pnl,
        entry_quality_ok=entry_quality_ok,
        auto_eligible_count=max(auto_eligible_count, paper_auto_eligible_count),
        prediction_accuracy=prediction_accuracy,
        prediction_sample_count=prediction_sample_count,
        recent_prediction_accuracy=recent_prediction_accuracy,
        recent_positive_clv_rate=recent_positive_clv_rate,
    )

    from app import config as cfg
    settings = cfg.load()

    return {
        "state": _score_to_state(score),
        "readiness_label": _readiness_label(score, learning_samples, entry_quality_ok, avg_clv),
        "learning_active": learning_active,
        "score": score,
        "paper_trading": settings.get("paper_trading", True),
        "automation_enabled": settings.get("automation_enabled", False),
        "auto_paper_trade_enabled": settings.get("auto_paper_trade_enabled", False),
        "auto_trade_enabled": settings.get("auto_trade_enabled", False),
        "total_trades": total_trades,
        "open_trades": open_trades,
        "settled_trades": closed_trades,
        "learning_samples": learning_samples,
        "pending_settlement_trades": pending_settlement,
        "excluded_reset_trades": excluded_reset_trades,
        "avg_clv": avg_clv,
        "recent_30_avg_clv": recent_clv,
        "realized_pnl_paper": realized_pnl,
        "recent_30_pnl_paper": recent_pnl,
        "avg_pnl_per_trade": avg_pnl,
        "deficit_recovery": _deficit_recovery(realized_pnl, recent_avg_profit),
        "positive_clv_rate": positive_clv_rate,
        "entry_quality_ok": entry_quality_ok,
        "prediction_accuracy": prediction_accuracy,
        "prediction_correct_count": prediction_correct_count,
        "prediction_sample_count": prediction_sample_count,
        "recent_prediction_accuracy": recent_prediction_accuracy,
        "recent_positive_clv_rate": recent_positive_clv_rate,
        "auto_eligible_segments": auto_eligible_count,
        "paper_auto_eligible_segments": paper_auto_eligible_count,
        "segments": segments,
        "next_actions": _next_actions(
            settled=learning_samples,
            open_trades=open_trades,
            avg_clv=avg_clv,
            recent_clv=recent_clv,
            realized_pnl=realized_pnl,
            recent_pnl=recent_pnl,
            positive_clv_rate=positive_clv_rate,
            auto_eligible_count=auto_eligible_count,
            paper_auto_eligible_count=paper_auto_eligible_count,
            pending_settlement=pending_settlement,
            lessons=lessons,
            prediction_accuracy=prediction_accuracy,
            prediction_sample_count=prediction_sample_count,
        ),
    }


def _deficit_recovery(realized_pnl: float, recent_avg_profit: float) -> dict:
    deficit = min(0.0, float(realized_pnl or 0.0))
    estimated = None
    if deficit < 0 and recent_avg_profit > 0:
        estimated = int((abs(deficit) / recent_avg_profit) + 0.999)
    return {
        "current_deficit": round(deficit, 2),
        "avg_profit_per_recent_trade": round(float(recent_avg_profit or 0.0), 4),
        "estimated_trades_to_breakeven": estimated,
    }


def _compute_brain_score(
    settled: int,
    avg_clv: float,
    recent_clv: float,
    positive_clv_rate: float,
    realized_pnl: float,
    recent_pnl: float,
    entry_quality_ok: bool,
    auto_eligible_count: int,
    prediction_accuracy: float = 0.0,
    prediction_sample_count: int = 0,
    recent_prediction_accuracy: float = 0.0,
    recent_positive_clv_rate: float = 0.0,
) -> int:
    """Continuous brain score weighted toward recent performance.
    Old bad trades fade out so the score reflects the bot's current ability,
    not its historical mistakes."""

    sample_score = min(10.0, settled * 0.20)

    # CLV: 70% recent, 30% overall so improvements show fast
    blended_clv = recent_clv * 0.7 + avg_clv * 0.3
    clv_score = max(0.0, min(15.0, (blended_clv + 5.0) * 1.5))

    # Recent CLV trend (0-10): pure recent signal
    recent_clv_score = max(0.0, min(10.0, (recent_clv + 2.0) * 2.5))

    # Positive CLV rate: blend recent and overall
    blended_rate = recent_positive_clv_rate * 0.7 + positive_clv_rate * 0.3
    rate_score = max(0.0, min(15.0, (blended_rate - 0.15) * 33.33))

    # P&L: recent P&L matters more than cumulative
    if recent_pnl >= 0:
        pnl_score = min(10.0, 5.0 + recent_pnl * 1.0)
    else:
        pnl_score = max(0.0, 5.0 + recent_pnl * 0.5)

    segment_score = min(10.0, auto_eligible_count * 3.5)

    # Prediction accuracy: blend recent (last 100) with overall
    if prediction_sample_count >= 10:
        blended_accuracy = recent_prediction_accuracy * 0.7 + prediction_accuracy * 0.3
        pred_score = max(0.0, min(30.0, (blended_accuracy - 0.25) * 85.71))
    else:
        pred_score = 0.0

    score = sample_score + clv_score + recent_clv_score + rate_score + pnl_score + segment_score + pred_score
    return int(round(max(0, min(100, score))))


def _readiness_label(score: int, settled: int, entry_quality_ok: bool, avg_clv: float = 0.0) -> str:
    if settled == 0:
        return "Collecting paper data"
    if score >= 80 and entry_quality_ok and avg_clv >= 0.0:
        return "Paper ready"
    if entry_quality_ok and avg_clv >= 0.0:
        return "Learning, improving"
    if entry_quality_ok and avg_clv < 0.0:
        # One segment is improving but overall CLV is still negative
        return "Mixed — one segment positive, avg CLV still negative"
    return "Learning, not ready"


def _next_actions(
    settled: int,
    open_trades: int,
    avg_clv: float,
    recent_clv: float,
    realized_pnl: float,
    recent_pnl: float,
    positive_clv_rate: float,
    auto_eligible_count: int,
    paper_auto_eligible_count: int,
    pending_settlement: int,
    lessons: list,
    prediction_accuracy: float = 0.0,
    prediction_sample_count: int = 0,
) -> list:
    actions = []
    if prediction_sample_count >= 10 and prediction_accuracy < 0.40:
        actions.append(f"Prediction accuracy is {prediction_accuracy*100:.1f}% on {prediction_sample_count} trades — bot is wrong more than right. Tighten entry filters or retune weather model sigma.")
    if open_trades > 30:
        actions.append(f"Wait for or close stale paper positions: {open_trades} open trades are not learning samples yet.")
    if pending_settlement > 0:
        actions.append(f"Backfill settlements: {pending_settlement} closed trades still have unknown CLV.")
    if settled == 0:
        actions.append("Collect CLV-backed settlements so the model can score real outcomes instead of open positions.")
    if avg_clv < 0:
        actions.append(f"Raise entry quality: average CLV is {avg_clv:.1f}c, so the bot is entering worse than the later market.")
    if recent_clv < 0:
        actions.append(f"Fix recent entries first: recent entry move is {recent_clv:.1f}c.")
    if realized_pnl < 0:
        actions.append(f"Paper equity is down ${abs(realized_pnl):.2f} on closed trades; live sizing needs positive paper expectancy first.")
    if recent_pnl < 0:
        actions.append(f"Recent closed trades lost ${abs(recent_pnl):.2f}; live sizing should stay off until recent expectancy turns positive.")
    if positive_clv_rate < 0.50:
        actions.append(f"Good-entry rate is {positive_clv_rate * 100:.0f}%; live sizing needs the profitable side of the distribution to dominate.")
    if auto_eligible_count == 0:
        if paper_auto_eligible_count > 0:
            actions.append(f"{paper_auto_eligible_count} segment has earned paper-only exploration; live sizing still needs stronger evidence.")
        else:
            actions.append("No segment has earned auto sizing. Keep approvals small until one segment proves good entries.")
    actions.extend(lessons[:2])
    return actions[:6]
