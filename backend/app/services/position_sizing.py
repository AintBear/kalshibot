import math
import re
from typing import Optional, Tuple

BLOCKED_CITY_SEGMENTS = {
    "KXLOWTDC",      # 28.6% accuracy, -$7.41, 7 trades
    "KXLOWTDEN",     # 25.0% accuracy, -$8.16, 8 trades
    "KXLOWTPHIL",    # 28.6% accuracy, -$8.12, 7 trades
    "KXLOWTOKC",     # 36.4% accuracy, -$8.48, 11 trades
    "KXHIGHTNOLA",   # 37.5% accuracy, -$4.84, 8 trades
    "KXHIGHTSATX",   # 37.5% accuracy, -$6.24, 8 trades
    "KXLOWTDAL",     # 50.0% accuracy, -$3.60, 8 trades
    "KXLOWTNOLA",    # 57.1% accuracy, -$4.24, 14 trades
    "KXHIGHTSFO",    # 60.0% accuracy, -$4.08, 15 trades
}
_BLOCKED_CITY_SEGMENTS = BLOCKED_CITY_SEGMENTS

# Stop/TP audit snapshot from data/sibylla.db on 2026-05-08:
# - Stop-loss exits are negative in every time-held bucket:
#   <4h 66 trades avg P&L -$0.2368; 4-24h 48 trades -$0.4955;
#   >24h 51 trades -$0.2029.
# - Take-profit exits are positive in every bucket:
#   <4h 24 trades +$0.2930; 4-24h 22 trades +$0.8936;
#   >24h 24 trades +$0.4170.
# - Market-close exits are better than stops, so stops need wider time-aware
#   distances while live gates remain strict.


def paper_balance(settings: dict) -> float:
    from app.database import get_conn

    conn = get_conn()
    row = conn.execute(
        "SELECT COALESCE(SUM(pnl), 0.0) FROM trades WHERE paper=1 AND pnl IS NOT NULL"
    ).fetchone()
    conn.close()
    return round(float(settings.get("paper_starting_balance", 500.0) or 500.0) + float(row[0] or 0.0), 2)


def recommend_alert(alert: dict, settings: dict, explore: bool = False) -> dict:
    """Adaptive sizing: paper mode uses relaxed gates to collect learning data;
    live mode keeps all strict trust/segment gates.

    explore=True (paper only) suppresses soft, evidence-based blockers
    (segment performance, threshold markets, 40c+ NO, blocked city+segment,
    bracket within 1° of forecast). Iron-law blockers (YES at all, NO sub-20c,
    NO 85c+) still apply — those are confirmed catastrophic patterns.
    Explore trades are forced to 1 contract."""
    is_paper = settings.get("paper_trading", True)

    direction = alert.get("direction") or "yes"
    details = alert.get("details") or {}
    mark_yes_price = _bounded(alert.get("market_price"))
    yes_prob = _bounded(alert.get("model_prob"))
    fill_model = _resolve_fill_model(settings, is_paper)
    entry, entry_yes_price, fill_meta = _entry_prices(
        alert, details, direction, mark_yes_price, fill_model=fill_model
    )
    side_prob = 1.0 - yes_prob if direction == "no" else yes_prob
    edge = side_prob - entry
    max_gain = max(0.0, 1.0 - entry)
    max_loss = max(0.0, entry)
    ev = (side_prob * max_gain) - ((1.0 - side_prob) * max_loss)

    brain = (alert.get("details") or {}).get("brain") or {}
    learned = dict(brain.get("learned") or {})
    learned.update(_current_segment_learning(details, direction))
    phantom = alert.get("phantom_risk_level") or (brain.get("phantom_risk") or {}).get("level") or "none"
    score = int(alert.get("brain_score") or brain.get("score") or 0)
    state = alert.get("brain_state") or brain.get("state") or "skip"
    balance = paper_balance(settings)

    kelly_full = _kelly_fraction(side_prob, entry)
    kelly_fraction = float(settings.get("kelly_fraction", 0.25) or 0.25)
    time_urgency_multiplier = _time_urgency_multiplier(details.get("hours_to_close"))
    adaptive_multiplier = _adaptive_risk_multiplier(score, state, learned, alert.get("confidence"), phantom, details)
    adaptive_multiplier = adaptive_multiplier * time_urgency_multiplier
    risk_fraction = min(0.95, max(0.0, kelly_full * kelly_fraction * adaptive_multiplier))
    risk_budget = balance * risk_fraction

    seg_trade_count = int(learned.get("trade_count") or 0)
    seg_recent_clv = float(learned.get("recent_avg_clv") or 0.0)
    seg_recent_positive_rate = float(learned.get("recent_positive_clv_rate") or 0.0)
    seg_positive_rate = float(learned.get("positive_clv_rate") or 0.0)
    seg_avg_pnl = float(learned.get("avg_pnl") or 0.0)
    seg_prediction_count = int(learned.get("prediction_sample_count") or 0)
    seg_prediction_accuracy = float(learned.get("prediction_accuracy") or 0.0)
    seg_prediction_bad = (
        (seg_prediction_count >= 5 and seg_prediction_accuracy < 0.20)
        or (seg_prediction_count >= 10 and seg_prediction_accuracy < 0.40)
    )
    seg_prediction_good = seg_prediction_count >= 10 and seg_prediction_accuracy >= 0.55
    seg_auto_eligible = bool(brain.get("auto_eligible") or learned.get("auto_eligible"))

    blockers = []
    if edge <= 0:
        blockers.append("no positive edge")
    if ev <= 0:
        blockers.append("negative expected value")
    if not is_paper and phantom == "high":
        blockers.append("high forecast disagreement risk")
    if not is_paper and max_gain < 0.04:
        blockers.append("tiny remaining payout")
    if not is_paper and details.get("event_has_open_trade"):
        blockers.append("event already has an open paper trade")
    spread = details.get("spread")
    try:
        if spread is not None and float(spread) >= 0.15:
            # Wide spreads make limit-order fills unrealistic and market-order
            # fills ruinously expensive. Block in paper too — there is no
            # honest fill model that survives a 15c+ spread.
            blockers.append("wide bid/ask spread")
    except (TypeError, ValueError):
        pass

    # Liquidity floor — thin markets have wide spreads, bad fills, and
    # outsized slippage when our 1-3 contract order moves the price. The
    # bot historically traded markets with $20 in 24h volume.
    min_volume = float(settings.get("min_volume_24h") or 0.0)
    if min_volume > 0:
        vol = details.get("volume_24h")
        try:
            if vol is not None and float(vol) < min_volume:
                blockers.append(f"thin market ({float(vol):.0f} 24h vol < ${min_volume:.0f} floor)")
        except (TypeError, ValueError):
            pass

    min_oi = float(settings.get("min_open_interest") or 0.0)
    if min_oi > 0:
        oi = details.get("open_interest")
        try:
            if oi is not None and float(oi) < min_oi:
                blockers.append(f"low open interest ({float(oi):.0f} < {min_oi:.0f} floor)")
        except (TypeError, ValueError):
            pass

    ticker_upper = (alert.get("market_ticker") or details.get("ticker") or "").upper()
    is_low_market = "LOW" in ticker_upper
    unlimited_paper = bool(settings.get("paper_unlimited_learning", False))
    no_entry_cost = 1.0 - mark_yes_price if direction == "no" else 0.0
    breakeven_accuracy = no_entry_cost if direction == "no" else 0.0
    forecast_distance = _forecast_bracket_distance(ticker_upper, details)
    is_threshold = bool(re.search(r"-T\d", ticker_upper))
    ticker_series = ticker_upper.split("-")[0] if "-" in ticker_upper else ticker_upper
    is_blocked_city_segment = ticker_series in _BLOCKED_CITY_SEGMENTS
    if is_paper:
        # Iron-law blockers: catastrophic patterns confirmed on real settlements.
        # Applied even in unlimited_paper and explore modes.
        if direction == "yes":
            blockers.append("yes blocked (0% accuracy on 26 real settlements)")
        if direction == "no" and mark_yes_price < 0.20:
            blockers.append(f"no sub-20c blocked (need {breakeven_accuracy*100:.0f}% accuracy to break even)")
        if direction == "no" and mark_yes_price > 0.85:
            blockers.append("no against 85c+ market (0% accuracy)")

        # Soft, evidence-based blockers: suppressed in unlimited or explore mode.
        soft_off = unlimited_paper or explore
        if not soft_off and seg_prediction_bad:
            blockers.append(f"similar predictions only {seg_prediction_accuracy * 100:.0f}% correct")
        if not soft_off and seg_trade_count >= 10 and seg_recent_clv < 0 and seg_positive_rate < 0.25:
            blockers.append(f"similar entries have weak results ({seg_positive_rate * 100:.0f}% good, recent {seg_recent_clv * 100:+.1f}c)")
        if not soft_off and is_threshold:
            blockers.append("threshold markets blocked (25% NO accuracy, 0% YES accuracy)")
        if not soft_off and direction == "no" and not is_low_market and mark_yes_price > 0.40:
            blockers.append("no-HIGH above 40c blocked (44% accuracy at 40-50c, losing money)")
        if not soft_off and direction == "no" and is_low_market and mark_yes_price > 0.40:
            blockers.append("no-LOW above 40c blocked (25% accuracy, losing money)")
        if not soft_off and is_blocked_city_segment:
            blockers.append(f"{ticker_series} blocked (losing city+segment combo)")
        if not soft_off and forecast_distance is not None and forecast_distance <= 1.0:
            blockers.append(f"bracket within {forecast_distance:.1f}° of forecast (coin flip zone)")
    else:
        if not seg_auto_eligible:
            blockers.append("similar trades have not earned auto sizing")
        if seg_prediction_bad:
            blockers.append(f"similar predictions only {seg_prediction_accuracy * 100:.0f}% correct")
        if not seg_prediction_good and seg_trade_count >= 10 and seg_positive_rate <= 0.50:
            blockers.append(f"similar trades only {seg_positive_rate * 100:.0f}% good entries")
        if not seg_prediction_good and seg_trade_count >= 10 and seg_recent_clv < 0:
            blockers.append(f"recent similar-trade edge {seg_recent_clv * 100:+.1f}c")
        if not seg_prediction_good and seg_trade_count >= 10 and seg_avg_pnl < 0:
            blockers.append("similar paper P&L is negative")
        if direction == "yes":
            blockers.append("yes blocked (0% accuracy on real settlements)")
        if is_threshold:
            blockers.append("threshold markets blocked (25% NO accuracy)")
        if direction == "no" and mark_yes_price < 0.20:
            blockers.append(f"no sub-20c blocked (need {breakeven_accuracy*100:.0f}% to break even)")
        if direction == "no" and mark_yes_price > 0.40:
            blockers.append("no above 40c yes price (losing at 40c+)")
        if is_blocked_city_segment:
            blockers.append(f"{ticker_series} blocked (losing city+segment combo)")
        if forecast_distance is not None and forecast_distance <= 2.0:
            blockers.append(f"bracket within {forecast_distance:.0f}° of forecast (coin flip zone)")

    max_contracts_setting = int(settings.get("max_contracts_per_trade") or 5)
    max_contracts_cap = max(1, min(10, max_contracts_setting))
    ABSOLUTE_MAX_CONTRACTS = 10
    max_risk_fraction = float(settings.get("max_trade_risk_fraction", 0.025) or 0.025)
    max_dollar_risk = max(0.01, balance * max(0.0, min(0.05, max_risk_fraction)))
    risk_budget = min(risk_budget, max_dollar_risk)
    trust_max = 0

    if blockers:
        contracts = 0
        action = "skip"
    else:
        affordable_contracts = math.floor(max(balance, 0.0) / max(entry, 0.01))
        sized_contracts = math.floor(risk_budget / max(entry, 0.01))
        # Always allocate at least 1 contract when edge and EV are positive.
        if sized_contracts < 1:
            sized_contracts = 1

        if is_paper:
            trust_max = _paper_contract_cap(
                settings=settings,
                ev=ev,
                edge=edge,
                confidence=alert.get("confidence"),
                learned=learned,
                phantom=phantom,
                details=details,
            )
        else:
            trust_max = max_contracts_cap

        contracts = max(0, min(affordable_contracts, sized_contracts, trust_max, ABSOLUTE_MAX_CONTRACTS))
        dollar_risk = contracts * entry
        if dollar_risk > max_dollar_risk:
            contracts = max(1, min(contracts, math.floor(max_dollar_risk / max(entry, 0.01))))
        contracts = min(contracts, ABSOLUTE_MAX_CONTRACTS)

        if is_paper and explore:
            contracts = 1

        if is_paper:
            action = "paper" if state == "paper_ready" else "learn"
        else:
            action = "live" if contracts > 0 else "watch"

    tier, tier_label = _tier(action, contracts, edge, ev, score, state, phantom, learned, blockers)
    drivers = _drivers(edge, ev, score, state, learned, phantom, details)

    limit_side = round(entry, 4)
    limit_yes = round(entry_yes_price, 4)

    if is_paper:
        # Paper trades ride to settlement so we learn actual win rates.
        # SL/TP on paper trades was destroying P&L (-$61 from stops alone)
        # while hiding whether predictions were actually correct.
        stop_loss_price_yes = None
        take_profit_price = None
    else:
        stop_loss_price_yes, take_profit_price = _adaptive_exit_prices(
            direction=direction,
            yes_price=entry_yes_price,
            yes_prob=yes_prob,
            side_entry=entry,
            side_prob=side_prob,
            confidence=float(alert.get("confidence") or 0.0),
            learned=learned,
            contracts=contracts,
            hours_to_close=details.get("hours_to_close"),
        )

    return {
        "action": action,
        "contracts": contracts,
        "tier": tier,
        "tier_label": tier_label,
        "limit_price_side": limit_side,
        "limit_price_yes": limit_yes,
        "fill_model": fill_meta.get("fill_model"),
        "side_bid": fill_meta.get("side_bid"),
        "side_ask": fill_meta.get("side_ask"),
        "stop_loss_price": stop_loss_price_yes,
        "take_profit_price": take_profit_price,
        "risk_budget": round(risk_budget, 2),
        "paper_balance": balance,
        "kelly_full": round(kelly_full, 4),
        "kelly_fraction": kelly_fraction,
        "adaptive_risk_fraction": round(risk_fraction, 4),
        "adaptive_risk_multiplier": round(adaptive_multiplier, 4),
        "time_urgency_multiplier": round(time_urgency_multiplier, 4),
        "time_priority": _time_priority(details.get("hours_to_close")),
        "side_probability": round(side_prob, 4),
        "side_edge": round(edge, 4),
        "expected_value_per_contract": round(ev, 4),
        "max_gain_per_contract": round(max_gain, 4),
        "max_loss_per_contract": round(max_loss, 4),
        "historical_trade_count": int(learned.get("trade_count") or 0),
        "historical_positive_clv_rate": float(learned.get("positive_clv_rate") or 0.0),
        "historical_recent_clv": float(learned.get("recent_avg_clv") or 0.0),
        "historical_prediction_accuracy": seg_prediction_accuracy,
        "historical_prediction_sample_count": seg_prediction_count,
        "historical_prediction_correct_count": int(learned.get("prediction_correct_count") or 0),
        "paper_contract_cap": trust_max if is_paper else None,
        "drivers": drivers,
        "blockers": blockers,
        "reason": "; ".join(blockers[:3]) if blockers else "positive edge, positive expected value, and trust check passed",
        "learning_mode": "explore" if (is_paper and explore) else None,
    }


def _paper_contract_cap(
    settings: dict,
    ev: float,
    edge: float,
    confidence,
    learned: dict,
    phantom: str,
    details: dict,
) -> int:
    try:
        cap = int(settings.get("paper_learning_max_contracts") or 3)
    except (TypeError, ValueError):
        cap = 3
    cap = max(1, min(10, cap))

    try:
        conf = max(0.0, min(1.0, float(confidence or 0.0)))
    except (TypeError, ValueError):
        conf = 0.0

    contracts = 1
    if ev >= 0.04 and edge >= 0.04 and conf >= 0.40:
        contracts = 2
    if ev >= 0.08 and edge >= 0.08 and conf >= 0.55:
        contracts = 3
    if ev >= 0.15 and edge >= 0.15 and conf >= 0.65:
        contracts = min(cap, 5)

    spread = details.get("spread")
    try:
        wide_spread = spread is not None and float(spread) >= 0.15
    except (TypeError, ValueError):
        wide_spread = False

    if phantom == "high" or wide_spread:
        contracts = 1
    return max(1, min(cap, contracts))


def _current_segment_learning(details: dict, direction: str) -> dict:
    keys = _segment_keys_from_details(details, direction)
    if not keys:
        return {}
    try:
        from app.services import adaptive_policy
        for key in keys:
            learned = adaptive_policy.get_segment_learning(key)
            if learned and not learned.get("fallback"):
                return learned
    except Exception:
        return {}
    return {}


def _segment_keys_from_details(details: dict, direction: str) -> list[str]:
    from app.services.adaptive_policy import segment_keys_from_details
    return segment_keys_from_details(details, direction)


def _adaptive_risk_multiplier(
    score: int,
    state: str,
    learned: dict,
    confidence,
    phantom: str,
    details: dict,
) -> float:
    trade_count = int(learned.get("trade_count") or 0)
    positive_rate = float(learned.get("positive_clv_rate") or 0.0)
    recent_clv = float(learned.get("recent_avg_clv") or 0.0)
    stop_loss_rate = float(learned.get("stop_loss_rate") or 0.0)
    prediction_count = int(learned.get("prediction_sample_count") or 0)
    prediction_accuracy = float(learned.get("prediction_accuracy") or 0.0)
    conf = max(0.0, min(1.0, float(confidence or 0.0)))

    multiplier = 0.18 + conf * 0.45
    if score >= 80:
        multiplier += 0.45
    elif score >= 60:
        multiplier += 0.22
    elif state in ("skip", "caution"):
        multiplier -= 0.05

    if trade_count >= 10:
        multiplier += (positive_rate - 0.35) * 0.7
        multiplier += max(-0.20, min(0.25, recent_clv * 3.0))
        multiplier -= max(0.0, stop_loss_rate - 0.35) * 0.35
    if prediction_count >= 10:
        if prediction_accuracy < 0.40:
            multiplier *= 0.25
        elif prediction_accuracy > 0.55:
            multiplier += min(0.20, (prediction_accuracy - 0.55) * 0.8)

    spread = details.get("spread")
    try:
        if spread is not None:
            multiplier -= max(0.0, float(spread) - 0.04) * 1.4
    except (TypeError, ValueError):
        pass

    if phantom == "medium":
        multiplier *= 0.55
    elif phantom == "high":
        multiplier = 0.0

    return max(0.03, min(1.0, multiplier))


def _time_urgency_multiplier(hours_to_close) -> float:
    try:
        hours = float(hours_to_close)
    except (TypeError, ValueError):
        return 1.0
    if hours < 4:
        return 1.5
    if hours < 12:
        return 1.2
    if hours <= 24:
        return 1.0
    return 0.8


def _forecast_bracket_distance(ticker: str, details: dict) -> Optional[float]:
    """Distance in degrees between the bracket midpoint and the NWS forecast."""
    import re
    forecast = details.get("forecast") or {}
    if "HIGH" in ticker:
        temp = forecast.get("high")
    elif "LOW" in ticker:
        temp = forecast.get("low")
    else:
        return None
    if temp is None:
        return None
    match = re.search(r"-B(\d+(?:\.\d+)?)", ticker)
    if not match:
        return None
    bracket_mid = float(match.group(1))
    return abs(float(temp) - bracket_mid)


def _time_priority(hours_to_close) -> str:
    try:
        hours = float(hours_to_close)
    except (TypeError, ValueError):
        return "normal"
    if hours < 4:
        return "high"
    if hours > 24:
        return "low"
    return "normal"


def _quote_value(alert: dict, details: dict, field: str) -> Optional[float]:
    value = alert.get(field)
    if value is None:
        value = details.get(field)
    try:
        if value is None:
            return None
        return _bounded(value)
    except (TypeError, ValueError):
        return None


FILL_MODELS = ("ask", "midpoint", "bid_plus_1c")
DEFAULT_PAPER_FILL_MODEL = "midpoint"
DEFAULT_LIVE_FILL_MODEL = "ask"


def _resolve_fill_model(settings: dict, is_paper: bool) -> str:
    """Pick the entry-fill model.

    Paper mode defaults to ``midpoint`` because the bot was historically
    entering at the ask and bleeding the spread (-0.57c recent CLV). For
    live mode we still default to ``ask`` so we never simulate fills that
    we have not actually built order plumbing for.
    """
    if is_paper:
        key = "paper_fill_model"
        default = DEFAULT_PAPER_FILL_MODEL
    else:
        key = "live_fill_model"
        default = DEFAULT_LIVE_FILL_MODEL
    model = str(settings.get(key) or default).lower()
    if model not in FILL_MODELS:
        model = default
    return model


def _round_cent(value: float) -> float:
    return round(round(value * 100) / 100.0, 4)


def _apply_fill_model(bid: Optional[float], ask: Optional[float], model: str) -> Optional[float]:
    """Return the realized side-entry price under the chosen fill model.

    Returns None if the inputs are insufficient (caller should fall back).
    """
    if ask is None:
        return None
    if bid is None or bid <= 0:
        # No bid published — only the ask is realistic.
        return _bounded(_round_cent(ask))
    if ask < bid:
        # Crossed book or stale quote — treat as ask.
        return _bounded(_round_cent(ask))
    if model == "ask":
        return _bounded(_round_cent(ask))
    if model == "bid_plus_1c":
        # Post a passive bid one cent above the resting bid.
        cand = min(ask, bid + 0.01)
        return _bounded(_round_cent(cand))
    # midpoint (default for paper). Bias 1c toward the ask on penny rounding
    # so we never claim a sub-bid fill — the queue at bid still has to clear.
    mid = (bid + ask) / 2.0
    rounded = _round_cent(mid)
    if rounded <= bid:
        rounded = _round_cent(bid + 0.01)
    if rounded > ask:
        rounded = _round_cent(ask)
    return _bounded(rounded)


def _entry_prices(
    alert: dict,
    details: dict,
    direction: str,
    mark_yes_price: float,
    fill_model: str = "ask",
) -> Tuple[float, float, dict]:
    """Return side-entry price, canonical YES price, and fill metadata.

    Trades are stored as YES-price coordinates so settlement math remains
    consistent. The historical default was ``ask`` (pay the spread on every
    fill); the new paper default is ``midpoint`` (model a passive limit
    order that fills near the middle of the book).
    """
    if direction == "no":
        bid = _quote_value(alert, details, "no_bid")
        ask = _quote_value(alert, details, "no_ask")
    else:
        bid = _quote_value(alert, details, "yes_bid")
        ask = _quote_value(alert, details, "yes_ask")

    realized = _apply_fill_model(bid, ask, fill_model)
    if realized is None:
        if direction == "no":
            realized = 1.0 - mark_yes_price
        else:
            realized = mark_yes_price
        used_model = "fallback"
    else:
        used_model = fill_model

    side_entry = _bounded(realized)
    yes_entry = _bounded(1.0 - side_entry) if direction == "no" else side_entry

    meta = {
        "fill_model": used_model,
        "side_bid": _bounded(bid) if bid is not None else None,
        "side_ask": _bounded(ask) if ask is not None else None,
        "side_entry": side_entry,
    }
    return side_entry, yes_entry, meta


def _adaptive_exit_prices(
    direction: str,
    yes_price: float,
    yes_prob: float,
    side_entry: float,
    side_prob: float,
    confidence: float,
    learned: dict,
    contracts: int,
    hours_to_close=None,
) -> Tuple[Optional[float], Optional[float]]:
    if contracts <= 0:
        return None, None

    settlement_win_rate = float(learned.get("settlement_win_rate") or 0.0)
    stop_loss_rate = float(learned.get("stop_loss_rate") or 0.0)
    confidence = max(0.0, min(1.0, confidence))

    side_entry = max(0.01, min(0.99, float(side_entry or 0.0)))
    max_loss = side_entry
    max_gain = max(0.0, 1.0 - side_entry)

    no_stop = bool(yes_price <= 0.15 or yes_price >= 0.85)

    edge = max(0.02, side_prob - side_entry)

    # Let winners run — target 60-90% of max gain instead of a fraction of
    # the edge. Weather markets resolve binary (0 or 1), so the real upside
    # is the full payout minus entry cost. Setting TP too tight clips winners
    # that would have settled at max gain.
    capture = 0.60 + confidence * 0.25
    capture = max(0.55, min(0.90, capture))

    target_distance = max(0.15, max_gain * capture)
    target_distance = min(max_gain * 0.92, target_distance)
    if target_distance <= 0:
        return None, None

    # Stop loss: wider distance so we don't get shaken out by noise.
    # Minimum 2:1 reward-to-risk ratio.
    stop_distance = min(max_loss * 0.40, target_distance / 2.0)
    stop_distance = max(stop_distance, _minimum_stop_distance(hours_to_close))
    if stop_distance >= side_entry:
        no_stop = True

    side_target = min(0.99, side_entry + target_distance)
    side_stop = None if no_stop else max(0.01, side_entry - stop_distance)

    if direction == "no":
        stop_yes = None if side_stop is None else 1.0 - side_stop
        target_yes = 1.0 - side_target
    else:
        stop_yes = side_stop
        target_yes = side_target

    return (
        round(max(0.01, min(0.99, stop_yes)), 4) if stop_yes is not None else None,
        round(max(0.01, min(0.99, target_yes)), 4),
    )


def _minimum_stop_distance(hours_to_close) -> float:
    try:
        hours = float(hours_to_close)
    except (TypeError, ValueError):
        hours = 24.0
    if hours < 4:
        return 0.20
    if hours <= 24:
        return 0.15
    return 0.12


def _kelly_fraction(probability: float, entry_price: float) -> float:
    p = _bounded(probability)
    entry = max(0.01, min(0.99, float(entry_price or 0.0)))
    b = (1.0 - entry) / entry
    if b <= 0:
        return 0.0
    kelly = (p * b - (1.0 - p)) / b
    return max(0.0, min(1.0, kelly))


def _bounded(value) -> float:
    try:
        return max(0.0, min(1.0, float(value or 0.0)))
    except (TypeError, ValueError):
        return 0.0


def _tier(
    action: str,
    contracts: int,
    edge: float,
    ev: float,
    score: int,
    state: str,
    phantom: str,
    learned: dict,
    blockers: list,
) -> tuple[str, str]:
    positive_clv_rate = float(learned.get("positive_clv_rate") or 0.0)
    trade_count = int(learned.get("trade_count") or 0)
    recent_clv = float(learned.get("recent_avg_clv") or 0.0)
    prediction_count = int(learned.get("prediction_sample_count") or 0)
    prediction_accuracy = float(learned.get("prediction_accuracy") or 0.0)
    prediction_good = prediction_count >= 10 and prediction_accuracy >= 0.55

    if blockers:
        if edge > 0 and ev > 0:
            return "watch", "Wait"
        return "avoid", "Avoid"
    if action in ("learn", "paper") and contracts > 0 and phantom == "high":
        return "learning", "Learning shot"
    if phantom == "high" or edge <= 0 or ev <= 0:
        return "avoid", "Avoid"
    if contracts > 0 and state == "paper_ready" and score >= 82 and edge >= 0.08:
        if prediction_good or (trade_count >= 5 and positive_clv_rate >= 0.50 and recent_clv >= 0):
            return "tier_a", "Tier A"
        return "tier_b", "Tier B"
    if action == "watch" or score >= 65:
        return "watch", "Watch"
    if action == "learn" and contracts > 0:
        return "learning", "Learning shot"
    return "avoid", "Avoid"


def _drivers(edge: float, ev: float, score: int, state: str, learned: dict, phantom: str, details: dict) -> list[str]:
    drivers = []
    if edge > 0:
        drivers.append(f"edge +{edge * 100:.1f}c")
    if ev > 0:
        drivers.append(f"expected +{ev * 100:.1f}c/contract")
    drivers.append(f"trust {score}/100 ({state})")

    trade_count = int(learned.get("trade_count") or 0)
    if trade_count:
        positive_rate = float(learned.get("positive_clv_rate") or 0.0) * 100
        recent_clv = float(learned.get("recent_avg_clv") or 0.0) * 100
        drivers.append(f"similar trades: {trade_count}, {positive_rate:.0f}% good entries, recent {recent_clv:+.1f}c")
    else:
        drivers.append("no direct segment sample yet")
    prediction_count = int(learned.get("prediction_sample_count") or 0)
    if prediction_count:
        prediction_accuracy = float(learned.get("prediction_accuracy") or 0.0) * 100
        drivers.append(f"prediction accuracy {prediction_accuracy:.0f}% on {prediction_count}")

    if phantom and phantom not in ("none", "low"):
        drivers.append(f"forecast disagreement {phantom}")
    if details.get("event_has_open_trade"):
        drivers.append("already tracking this event in paper")
    spread = details.get("spread")
    if spread is not None:
        try:
            drivers.append(f"spread {float(spread) * 100:.1f}c")
        except (TypeError, ValueError):
            pass
    return drivers[:5]
