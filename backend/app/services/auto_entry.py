"""
Auto-entry gate: runs after each scan to open paper/live trades for qualifying signals.

Gates:
  Paper auto  — opens adaptive learning trades for pending alerts. In unlimited
                 learning mode the open-book and settlement backlog are reported
                 but do not block new paper samples.
  Live auto   — requires live mode, Kalshi credentials, at least one live-eligible
                 segment, and candidate-level positive sizing.

No circuit breaker is applied to paper learning. The brain adapts sizing from real
settlement CLV, prediction accuracy, and segment quality.
"""
import json
import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)
_automation_lock = threading.Lock()


def run_automation_cycle(settings_override: Optional[dict] = None) -> dict:
    """Run the full unattended maintenance cycle before attempting entries."""
    if not _automation_lock.acquire(blocking=False):
        return {
            "skipped": True,
            "reason": "automation cycle already running",
            "total_entered": 0,
        }
    try:
        lifecycle_result = {}
        backfill_result = {}
        rebuilt_segments = 0

        try:
            from app.services.trade_lifecycle import check_and_close_trades, backfill_settlements
            check_and_close_trades()
            lifecycle_result = {"ran": True}
            backfill_result = backfill_settlements()
        except Exception as exc:
            lifecycle_result = {"ran": False, "error": str(exc)}
            logger.warning("Automation lifecycle refresh failed: %s", exc)

        try:
            from app.services import adaptive_policy
            rebuilt_segments = len(adaptive_policy.rebuild_snapshots())
        except Exception as exc:
            logger.warning("Automation learning rebuild failed: %s", exc)

        entry_result = auto_enter_qualifying_alerts(settings_override=settings_override)
        return {
            "lifecycle": lifecycle_result,
            "backfill": backfill_result,
            "segments_rebuilt": rebuilt_segments,
            "entry": entry_result,
            "total_entered": int(entry_result.get("total_entered") or 0),
        }
    finally:
        _automation_lock.release()


def paper_auto_blocker(brain: dict, settings: Optional[dict] = None) -> str:
    """Return the reason automatic paper entries should pause, or an empty string.

    Trade-level blockers in position_sizing.py handle candidate filtering. This
    gate stops the sampler when the book is already full or when aggregate
    evidence is bad and no segment has earned continued paper exploration.
    """
    settings = settings or {}
    if _paper_unlimited_learning(settings):
        return ""

    open_trades = int(brain.get("open_trades") or 0)
    open_cap = _setting_int(settings, "max_open_paper_trades", 50, 1, 1000)
    if open_trades >= open_cap:
        return f"open paper book at cap ({open_trades}/{open_cap}); wait for settlements"

    pending_settlement = int(brain.get("pending_settlement_trades") or 0)
    backlog_default = max(1, min(open_cap, 20))
    backlog_cap = _setting_int(
        settings,
        "paper_settlement_backlog_limit",
        backlog_default,
        1,
        max(open_cap, 1000),
    )
    if pending_settlement >= backlog_cap:
        return (
            f"settlement backlog too high ({pending_settlement}/{backlog_cap}); "
            "wait for Kalshi results before opening more paper trades"
        )

    learning_samples = int(brain.get("learning_samples") or 0)
    prediction_count = int(brain.get("prediction_sample_count") or 0)
    prediction_accuracy = _as_float(brain.get("prediction_accuracy"))
    paper_segments = int(brain.get("paper_auto_eligible_segments") or 0)
    recent_clv_cents = _as_float(brain.get("recent_30_avg_clv"))
    avg_clv_cents = _as_float(brain.get("avg_clv"))
    positive_rate = _as_float(brain.get("positive_clv_rate"))

    if learning_samples >= 20 and prediction_count >= 10 and prediction_accuracy < 0.40 and paper_segments <= 0:
        return (
            f"prediction accuracy {prediction_accuracy * 100:.1f}% on "
            f"{prediction_count} settled outcomes with no paper-eligible segment"
        )
    if learning_samples >= 20 and paper_segments <= 0 and avg_clv_cents < 0 and recent_clv_cents < 0 and positive_rate < 0.40:
        return "entry evidence is negative with no paper-eligible segment"
    return ""


def live_auto_blocker(brain: dict, settings: Optional[dict] = None) -> str:
    """Return the reason live auto entries should pause, or an empty string."""
    settings = settings or {}
    if settings.get("paper_trading", True):
        return "paper trading is still on; switch to live mode before real orders"

    try:
        from app.services.kalshi_client import credentials_configured
        if not credentials_configured(settings):
            return "Kalshi API credentials are not configured"
    except Exception as exc:
        return f"Kalshi credential check failed: {exc}"

    if int(brain.get("auto_eligible_segments") or 0) <= 0:
        return "no segment has earned live auto sizing from settlement-backed evidence"

    realized_pnl = _as_float(brain.get("realized_pnl_paper"))
    avg_clv = _as_float(brain.get("avg_clv"))
    recent_clv = _as_float(brain.get("recent_30_avg_clv"))
    if realized_pnl < 0:
        return f"closed paper P&L is still negative (${realized_pnl:.2f})"
    if avg_clv < 0 and recent_clv < 0:
        return f"entry movement is negative overall ({avg_clv:.1f}c) and recent ({recent_clv:.1f}c)"
    return ""


def auto_enter_qualifying_alerts(settings_override: Optional[dict] = None) -> dict:
    from app import config as cfg
    settings = dict(settings_override) if settings_override is not None else cfg.load()

    paper_auto = settings.get("auto_paper_trade_enabled", False)
    live_auto  = settings.get("auto_trade_enabled", False)

    if not paper_auto and not live_auto:
        return {"skipped": True, "reason": "auto entry disabled"}

    from app.services.risk import kill_switch_active
    if kill_switch_active(settings):
        return {"skipped": True, "reason": "kill switch active"}

    # ── Brain gate ────────────────────────────────────────────────────
    from app.services.weather_brain import get_brain_status
    brain = get_brain_status()
    score      = brain.get("score", 0)
    avg_clv    = brain.get("avg_clv", 0.0)
    entry_ok   = brain.get("entry_quality_ok", False)
    realized_pnl = brain.get("realized_pnl_paper", 0.0)

    if live_auto and not settings.get("paper_trading", True):
        blocker = live_auto_blocker(brain, settings)
        if blocker:
            return {"skipped": True, "reason": f"live gate: {blocker}", "brain_score": score, "paper": False, "total_entered": 0}
    elif paper_auto:
        blocker = paper_auto_blocker(brain, settings)
        if blocker:
            return {
                "skipped": True,
                "reason": f"paper gate: {blocker}",
                "brain_score": score,
                "paper": True,
                "total_entered": 0,
            }
        logger.info("Paper auto learning enabled at brain score %s", score)

    is_paper = settings.get("paper_trading", True) or not live_auto

    # ── Fetch qualifying pending alerts ───────────────────────────────
    from app.database import get_conn
    conn = get_conn()
    slots_remaining = None
    paper_entry_limit = 0
    paper_max_per_event = 0
    paper_min_ev = 0.0
    paper_min_side_edge = 0.0
    explore_open_count = 0
    explore_open_cap = 0
    open_event_counts = {}
    if is_paper:
        unlimited_paper = _paper_unlimited_learning(settings)
        open_count = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE status='open' AND paper=1"
        ).fetchone()[0]
        explore_open_count = _open_explore_trade_count(conn)
        paper_entry_limit = _setting_int(settings, "paper_learning_max_entries_per_scan", 0, 0, 1000)
        paper_max_per_event = _setting_int(settings, "paper_learning_max_open_per_event", 0, 0, 100)
        paper_min_ev = _setting_float(settings, "paper_learning_min_ev", 0.0, 0.0, 0.50)
        paper_min_side_edge = _setting_float(settings, "paper_learning_min_side_edge", 0.0, 0.0, 0.50)
        open_cap = _setting_int(settings, "max_open_paper_trades", 50, 1, 1000)
        explore_open_cap = _setting_int(
            settings,
            "paper_learning_explore_max_open",
            min(open_cap, 30),
            1,
            open_cap,
        )
        slots_remaining = None if unlimited_paper or paper_entry_limit <= 0 else paper_entry_limit
        open_event_counts = _open_trade_event_counts(conn, paper_only=True)
        logger.info(
            "Paper adaptive learning enabled; open=%d, explore_open=%d/%d, max_entries=%d, max_open_per_event=%d",
            open_count,
            explore_open_count,
            explore_open_cap,
            paper_entry_limit,
            paper_max_per_event,
        )
    else:
        slots_remaining = int(settings.get("max_live_entries_per_run") or 5)
    open_trade_events = set() if is_paper else _open_trade_events(conn)
    open_trade_tickers = {r["market_ticker"] for r in conn.execute(
        "SELECT market_ticker FROM trades WHERE status='open'"
    ).fetchall()}
    rows = conn.execute(
        """SELECT alerts.id, alerts.market_ticker, alerts.direction,
                  alerts.market_price, alerts.model_prob, alerts.edge,
                  alerts.confidence, alerts.brain_score, alerts.brain_state,
                  alerts.brain_auto_qualified, alerts.phantom_risk_level,
                  alerts.details, markets.raw_json AS market_raw_json
             FROM alerts
             JOIN markets ON markets.ticker = alerts.market_ticker
            WHERE alerts.status = 'pending'
              AND lower(coalesce(markets.status, '')) IN ('open', 'active')
              AND (markets.close_time IS NULL OR datetime(markets.close_time) > datetime('now'))
            LIMIT 800"""
    ).fetchall()
    conn.close()

    candidates = []
    candidates_by_event = {}
    considered = 0
    for row in rows:
        considered += 1
        try:
            details = json.loads(row["details"] or "{}")
        except Exception:
            details = {}
        event_ticker = _event_ticker(row, details)
        event_open_count = int(open_event_counts.get(event_ticker, 0))
        if is_paper and paper_max_per_event > 0 and event_open_count >= paper_max_per_event:
            continue
        if event_ticker in open_trade_events:
            continue
        if row["market_ticker"] in open_trade_tickers:
            continue
        phantom_level = row["phantom_risk_level"] or "none"
        if not is_paper and phantom_level == "high":
            continue

        from app.services.position_sizing import recommend_alert
        rec = recommend_alert({**dict(row), "details": details}, settings)
        rec = _with_current_segment_learning(rec, details, row["direction"])
        contracts = int(rec.get("contracts") or 0)
        expected = _as_float(rec.get("expected_value_per_contract"))
        side_edge = _as_float(rec.get("side_edge"))
        rank = _candidate_rank(rec, row, details, event_open_count)
        if is_paper:
            min_confidence = _setting_float(settings, "paper_learning_min_confidence", 0.0, 0.0, 1.0)
            alert_confidence = _as_float(row["confidence"])
            if expected < paper_min_ev or side_edge < paper_min_side_edge:
                continue
            if alert_confidence < min_confidence:
                continue
            if _paper_segment_blocker(rec, brain, settings):
                continue
            contracts = _paper_learning_contracts(rec, row, details, settings, event_open_count)
            if contracts < 1:
                continue
        elif contracts < 1:
            continue

        if live_auto and not is_paper:
            side_edge = float(rec.get("side_edge") or rec.get("expected_value_per_contract") or 0)
            if side_edge <= 0:
                continue
            live_pred_count = int(rec.get("historical_prediction_sample_count") or 0)
            live_pred_accuracy = _as_float(rec.get("historical_prediction_accuracy"))
            if live_pred_count >= 10 and live_pred_accuracy <= 0.50:
                continue

        entry_price = float(rec.get("limit_price_yes") or row["market_price"] or 0)
        if entry_price <= 0:
            continue

        candidate = {
            "row": row,
            "details": details,
            "event_ticker": event_ticker,
            "recommendation": rec,
            "contracts": contracts,
            "entry_price": entry_price,
            "rank": rank,
        }
        if is_paper:
            candidates.append(candidate)
        else:
            current = candidates_by_event.get(event_ticker)
            if current is None or candidate["rank"] > current["rank"]:
                candidates_by_event[event_ticker] = candidate

    if not is_paper:
        candidates = list(candidates_by_event.values())

    entered = []
    errors  = []
    run_event_counts = dict(open_event_counts)

    for candidate in sorted(candidates, key=lambda item: item["rank"], reverse=True):
        if slots_remaining is not None and len(entered) >= slots_remaining:
            break
        row = candidate["row"]
        alert_id = row["id"]
        ticker = row["market_ticker"]
        direction = row["direction"] or "yes"
        rec = candidate["recommendation"]
        contracts = candidate["contracts"]
        entry_price = candidate["entry_price"]
        event_ticker = candidate["event_ticker"]
        if is_paper and paper_max_per_event > 0 and int(run_event_counts.get(event_ticker, 0)) >= paper_max_per_event:
            continue

        stop_loss_pct = float(settings.get("stop_loss_pct") or 0.50)
        take_profit_pct = float(settings.get("take_profit_pct") or 0.50)

        try:
            from app.services.order_manager import place_order, recommendation_exit_args
            result = place_order(
                market_ticker=ticker,
                direction=direction,
                entry_price=entry_price,
                alert_id=alert_id,
                contracts=contracts,
                fill_context=rec,
                **recommendation_exit_args(rec, stop_loss_pct, take_profit_pct),
            )

            _mark_alert_traded(alert_id, is_paper, rec.get("learning_mode"))

            logger.info("Auto-entry: %s %s x%d @ %.3f (trade_id=%s)%s",
                        direction, ticker, contracts, entry_price, result.get("trade_id"),
                        " [explore]" if rec.get("learning_mode") == "explore" else "")
            entered.append({"alert_id": alert_id, "ticker": ticker, "paper": is_paper, **result})
            open_trade_tickers.add(ticker)
            if is_paper:
                run_event_counts[event_ticker] = int(run_event_counts.get(event_ticker, 0)) + 1

        except Exception as exc:
            logger.error("Auto-entry failed for alert %d (%s): %s", alert_id, ticker, exc)
            errors.append({"alert_id": alert_id, "ticker": ticker, "error": str(exc)})

    explore_entered = 0
    explore_quota = 0
    if is_paper and bool(settings.get("paper_learning_explore_enabled", False)):
        explore_quota = _setting_int(settings, "paper_learning_explore_max_per_scan", 3, 0, 20)
        if explore_open_cap > 0:
            explore_quota = max(0, min(explore_quota, explore_open_cap - explore_open_count))
        if explore_quota > 0:
            already = {c["row"]["id"] for c in candidates}
            explore_entered = _run_explore_pass(
                rows=rows,
                already_entered_alert_ids=already.union({e["alert_id"] for e in entered}),
                open_trade_tickers=open_trade_tickers,
                open_trade_events=open_trade_events,
                open_event_counts=run_event_counts,
                paper_max_per_event=paper_max_per_event,
                settings=settings,
                brain=brain,
                quota=explore_quota,
                entered=entered,
                errors=errors,
            )

    return {
        "entered": entered,
        "errors": errors,
        "brain_score": score,
        "paper": is_paper,
        "total_entered": len(entered),
        "candidates_considered": considered,
        "eligible_candidates": len(candidates),
        "explore_quota": explore_quota,
        "explore_entered": explore_entered,
        "explore_open_count": explore_open_count + explore_entered,
        "explore_open_cap": explore_open_cap if is_paper else None,
        "paper_learning_mode": "adaptive_sampler" if is_paper else None,
        "paper_entry_limit": paper_entry_limit if is_paper else None,
        "paper_max_open_per_event": paper_max_per_event if is_paper else None,
        "paper_min_ev": paper_min_ev if is_paper else None,
        "paper_min_side_edge": paper_min_side_edge if is_paper else None,
    }


def _mark_alert_traded(alert_id: int, is_paper: bool, learning_mode: Optional[str]) -> None:
    from app.database import get_conn
    conn = get_conn()
    action = "auto_paper" if is_paper else "auto_live"
    if learning_mode:
        conn.execute(
            """UPDATE alerts
                  SET status='paper_traded',
                      details=json_set(
                          json_set(coalesce(details,'{}'), '$.auto_entry_action', ?),
                          '$.learning_mode', ?),
                      updated_at=datetime('now')
                WHERE id=?""",
            (action, learning_mode, alert_id),
        )
    else:
        conn.execute(
            """UPDATE alerts
                  SET status='paper_traded',
                      details=json_set(coalesce(details,'{}'), '$.auto_entry_action', ?),
                      updated_at=datetime('now')
                WHERE id=?""",
            (action, alert_id),
        )
    conn.commit()
    conn.close()


def _run_explore_pass(
    rows,
    already_entered_alert_ids: set,
    open_trade_tickers: set,
    open_trade_events: set,
    open_event_counts: dict,
    paper_max_per_event: int,
    settings: dict,
    brain: dict,
    quota: int,
    entered: list,
    errors: list,
) -> int:
    """Second pass: pick up to `quota` 1-contract paper trades from candidates
    blocked by soft (evidence-based) blockers but NOT by iron-law blockers.
    Used for forward-learning data collection on patterns we previously banned
    based on retroactive estimates."""
    from app.services.position_sizing import recommend_alert
    from app.services.order_manager import place_order, recommendation_exit_args

    candidates = []
    for row in rows:
        if row["id"] in already_entered_alert_ids:
            continue
        try:
            details = json.loads(row["details"] or "{}")
        except Exception:
            details = {}
        event_ticker = _event_ticker(row, details)
        if event_ticker in open_trade_events:
            continue
        if row["market_ticker"] in open_trade_tickers:
            continue
        if paper_max_per_event > 0 and int(open_event_counts.get(event_ticker, 0)) >= paper_max_per_event:
            continue

        rec = recommend_alert({**dict(row), "details": details}, settings, explore=True)
        rec = _with_current_segment_learning(rec, details, row["direction"])
        if rec.get("blockers"):
            # Iron-law (YES, NO sub-20c, NO 85c+) still active even in explore.
            continue
        contracts = int(rec.get("contracts") or 0)
        if contracts < 1:
            continue
        side_edge = _as_float(rec.get("side_edge"))
        expected = _as_float(rec.get("expected_value_per_contract"))
        if side_edge <= 0 or expected <= 0:
            continue
        entry_price = float(rec.get("limit_price_yes") or row["market_price"] or 0)
        if entry_price <= 0:
            continue
        candidates.append({
            "row": row,
            "details": details,
            "event_ticker": event_ticker,
            "recommendation": rec,
            "entry_price": entry_price,
            "side_edge": side_edge,
        })

    candidates.sort(key=lambda c: c["side_edge"], reverse=True)

    n_entered = 0
    stop_loss_pct = float(settings.get("stop_loss_pct") or 0.50)
    take_profit_pct = float(settings.get("take_profit_pct") or 0.50)
    for cand in candidates:
        if n_entered >= quota:
            break
        row = cand["row"]
        ticker = row["market_ticker"]
        event_ticker = cand["event_ticker"]
        if ticker in open_trade_tickers:
            continue
        if paper_max_per_event > 0 and int(open_event_counts.get(event_ticker, 0)) >= paper_max_per_event:
            continue
        rec = cand["recommendation"]
        direction = row["direction"] or "yes"
        alert_id = row["id"]
        try:
            result = place_order(
                market_ticker=ticker,
                direction=direction,
                entry_price=cand["entry_price"],
                alert_id=alert_id,
                contracts=1,
                fill_context=rec,
                **recommendation_exit_args(rec, stop_loss_pct, take_profit_pct),
            )
            _mark_alert_traded(alert_id, True, "explore")
            logger.info("Explore-entry: %s %s x1 @ %.3f side_edge=%+.3f (trade_id=%s)",
                        direction, ticker, cand["entry_price"], cand["side_edge"], result.get("trade_id"))
            entered.append({"alert_id": alert_id, "ticker": ticker, "paper": True,
                            "learning_mode": "explore", **result})
            open_trade_tickers.add(ticker)
            open_event_counts[event_ticker] = int(open_event_counts.get(event_ticker, 0)) + 1
            n_entered += 1
        except Exception as exc:
            logger.error("Explore-entry failed for alert %d (%s): %s", alert_id, ticker, exc)
            errors.append({"alert_id": alert_id, "ticker": ticker, "error": str(exc)})
    return n_entered


def _open_trade_events(conn) -> set[str]:
    rows = conn.execute("SELECT market_ticker FROM trades WHERE status='open'").fetchall()
    return {row["market_ticker"].rsplit("-", 1)[0] for row in rows}


def _open_explore_trade_count(conn) -> int:
    row = conn.execute(
        """SELECT COUNT(*)
             FROM trades t
             LEFT JOIN alerts a ON a.id = t.alert_id
            WHERE t.status='open'
              AND t.paper=1
              AND COALESCE(json_extract(a.details, '$.learning_mode'), '') = 'explore'"""
    ).fetchone()
    return int(row[0] or 0)


def _current_open_explore_trade_count() -> int:
    from app.database import get_conn
    conn = get_conn()
    try:
        return _open_explore_trade_count(conn)
    finally:
        conn.close()


def _open_trade_event_counts(conn, paper_only: bool = False) -> dict[str, int]:
    where = "status='open'"
    if paper_only:
        where += " AND paper=1"
    rows = conn.execute(f"SELECT market_ticker FROM trades WHERE {where}").fetchall()
    counts: dict[str, int] = {}
    for row in rows:
        event_ticker = row["market_ticker"].rsplit("-", 1)[0]
        counts[event_ticker] = counts.get(event_ticker, 0) + 1
    return counts


def _event_ticker(row, details: dict) -> str:
    try:
        raw = json.loads(row["market_raw_json"] or "{}")
    except Exception:
        raw = {}
    return raw.get("event_ticker") or details.get("event_ticker") or row["market_ticker"].rsplit("-", 1)[0]


def _candidate_rank(recommendation: dict, row, details: Optional[dict] = None, event_open_count: int = 0) -> tuple[float, float, float, float, float]:
    details = details or {}
    expected = _as_float(recommendation.get("expected_value_per_contract"))
    side_edge = _as_float(recommendation.get("side_edge"))
    confidence = _as_float(row["confidence"])
    raw_edge = abs(_as_float(row["edge"]))
    score = expected * 4.0 + side_edge * 3.0 + confidence * 0.35 + raw_edge * 0.35

    phantom = (row["phantom_risk_level"] or details.get("phantom_risk_level") or "none").lower()
    if phantom == "high":
        score -= 0.35
    elif phantom == "medium":
        score -= 0.12
    elif phantom == "low":
        score -= 0.04

    spread = details.get("spread")
    try:
        if spread is not None:
            score -= max(0.0, float(spread) - 0.08) * 1.5
    except (TypeError, ValueError):
        pass

    score -= max(0, event_open_count) * 0.08
    time_priority = recommendation.get("time_priority") or details.get("time_priority")
    if time_priority == "high":
        score += 0.04
    elif time_priority == "low":
        score -= 0.03

    trade_count = int(recommendation.get("historical_trade_count") or 0)
    positive_rate = _as_float(recommendation.get("historical_positive_clv_rate"))
    recent_clv = _as_float(recommendation.get("historical_recent_clv"))
    if trade_count >= 10:
        score += max(-0.12, min(0.12, (positive_rate - 0.40) * 0.30))
        score += max(-0.10, min(0.10, recent_clv * 1.5))
    prediction_count = int(recommendation.get("historical_prediction_sample_count") or 0)
    prediction_accuracy = _as_float(recommendation.get("historical_prediction_accuracy"))
    if prediction_count >= 10:
        if prediction_accuracy < 0.40:
            score -= 0.65
        elif prediction_accuracy > 0.55:
            score += min(0.25, (prediction_accuracy - 0.55) * 0.9)

    return (score, expected, side_edge, confidence, raw_edge)


def _with_current_segment_learning(recommendation: dict, details: dict, direction: Optional[str] = None) -> dict:
    """Overlay current adaptive segment stats onto possibly stale alert details."""
    segment_keys = _segment_keys_from_details(details, direction)
    if not segment_keys:
        return recommendation
    learned = None
    try:
        from app.services import adaptive_policy
        for segment_key in segment_keys:
            candidate = adaptive_policy.get_segment_learning(segment_key)
            if not candidate.get("fallback"):
                learned = candidate
                break
    except Exception:
        return recommendation

    if not learned:
        return recommendation

    updated = dict(recommendation)
    updated["historical_trade_count"] = int(learned.get("trade_count") or 0)
    updated["historical_positive_clv_rate"] = float(learned.get("positive_clv_rate") or 0.0)
    updated["historical_recent_clv"] = float(learned.get("recent_avg_clv") or 0.0)
    updated["historical_prediction_accuracy"] = float(learned.get("prediction_accuracy") or 0.0)
    updated["historical_prediction_sample_count"] = int(learned.get("prediction_sample_count") or 0)
    updated["historical_prediction_correct_count"] = int(learned.get("prediction_correct_count") or 0)
    return updated


def _segment_keys_from_details(details: dict, direction: Optional[str] = None) -> list[str]:
    from app.services.adaptive_policy import segment_keys_from_details
    return segment_keys_from_details(details, direction)


def _paper_segment_blocker(recommendation: dict, brain: dict, settings: Optional[dict] = None) -> str:
    if _paper_unlimited_learning(settings or {}):
        return ""

    prediction_count = int(recommendation.get("historical_prediction_sample_count") or 0)
    prediction_accuracy = _as_float(recommendation.get("historical_prediction_accuracy"))
    if prediction_count >= 5 and prediction_accuracy < 0.20:
        return f"segment prediction accuracy {prediction_accuracy * 100:.0f}%"
    if prediction_count >= 10 and prediction_accuracy < 0.40:
        return f"segment prediction accuracy {prediction_accuracy * 100:.0f}%"

    trade_count = int(recommendation.get("historical_trade_count") or 0)
    positive_rate = _as_float(recommendation.get("historical_positive_clv_rate"))
    recent_clv = _as_float(recommendation.get("historical_recent_clv"))
    if trade_count >= 10 and positive_rate < 0.25 and recent_clv < 0:
        return "segment entry quality is negative"

    if (
        int(brain.get("learning_samples") or 0) >= 20
        and int(brain.get("paper_auto_eligible_segments") or 0) <= 0
        and prediction_count == 0
    ):
        return "no eligible paper segment for new exploration"
    return ""


def _paper_unlimited_learning(settings: dict) -> bool:
    return bool(settings.get("paper_unlimited_learning", False))


def _paper_learning_contracts(recommendation: dict, row, details: dict, settings: dict, event_open_count: int) -> int:
    if recommendation.get("blockers"):
        return 0

    max_contracts = _setting_int(settings, "paper_learning_max_contracts", 3, 1, 10)
    requested = max(0, int(recommendation.get("contracts") or 0))
    expected = _as_float(recommendation.get("expected_value_per_contract"))
    side_edge = _as_float(recommendation.get("side_edge"))
    confidence = _as_float(row["confidence"])

    contracts = 1
    if expected >= 0.04 and side_edge >= 0.04 and confidence >= 0.40:
        contracts = 2
    if expected >= 0.08 and side_edge >= 0.08 and confidence >= 0.55:
        contracts = 3
    if expected >= 0.15 and side_edge >= 0.15 and confidence >= 0.65:
        contracts = min(max_contracts, 5)

    phantom = (row["phantom_risk_level"] or details.get("phantom_risk_level") or "none").lower()
    spread = details.get("spread")
    try:
        wide_spread = spread is not None and float(spread) >= 0.15
    except (TypeError, ValueError):
        wide_spread = False

    if phantom == "high" or wide_spread:
        contracts = 1

    if requested > 0:
        contracts = max(contracts, min(requested, max_contracts))
    return max(1, min(max_contracts, contracts, 10))


def _setting_int(settings: dict, key: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(settings.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _setting_float(settings: dict, key: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(settings.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _as_float(value) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def get_auto_entry_status() -> dict:
    """Return current auto-entry configuration and readiness for UI display."""
    from app import config as cfg
    from app.services.weather_brain import get_brain_status

    settings = cfg.load()
    brain = get_brain_status()
    score = brain.get("score", 0)
    avg_clv = brain.get("avg_clv", 0.0)
    entry_ok = brain.get("entry_quality_ok", False)

    paper_auto = settings.get("auto_paper_trade_enabled", False)
    live_auto  = settings.get("auto_trade_enabled", False)

    paper_blocker = paper_auto_blocker(brain, settings) if paper_auto else ""
    paper_ready = bool(paper_auto and not paper_blocker)
    realized_pnl = brain.get("realized_pnl_paper", 0.0)
    live_blocker = live_auto_blocker(brain, settings)
    live_ready = bool(live_auto and not live_blocker)

    blockers = []
    if not paper_auto and not live_auto:
        blockers.append("Automatic entries are off")
    if paper_auto and paper_blocker:
        blockers.append(f"Paper auto paused: {paper_blocker}")
    if live_auto and live_blocker:
        blockers.append(f"Live auto paused: {live_blocker}")

    return {
        "paper_auto_enabled": paper_auto,
        "live_auto_enabled": live_auto,
        "paper_learning_mode": "adaptive_sampler",
        "paper_unlimited": _paper_unlimited_learning(settings),
        "paper_open_cap": _setting_int(settings, "max_open_paper_trades", 50, 1, 1000),
        "paper_pending_settlement": int(brain.get("pending_settlement_trades") or 0),
        "paper_settlement_backlog_limit": _setting_int(
            settings,
            "paper_settlement_backlog_limit",
            max(1, min(_setting_int(settings, "max_open_paper_trades", 50, 1, 1000), 20)),
            1,
            1000,
        ),
        "paper_entry_limit": _setting_int(settings, "paper_learning_max_entries_per_scan", 0, 0, 1000),
        "paper_max_open_per_event": _setting_int(settings, "paper_learning_max_open_per_event", 0, 0, 100),
        "paper_min_ev": _setting_float(settings, "paper_learning_min_ev", 0.08, 0.0, 0.50),
        "paper_min_side_edge": _setting_float(settings, "paper_learning_min_side_edge", 0.08, 0.0, 0.50),
        "paper_max_contracts": _setting_int(settings, "paper_learning_max_contracts", 3, 1, 10),
        "paper_explore_enabled": bool(settings.get("paper_learning_explore_enabled", False)),
        "paper_explore_quota": _setting_int(settings, "paper_learning_explore_max_per_scan", 3, 0, 20),
        "paper_explore_open": _current_open_explore_trade_count(),
        "paper_explore_open_cap": _setting_int(
            settings,
            "paper_learning_explore_max_open",
            min(_setting_int(settings, "max_open_paper_trades", 50, 1, 1000), 30),
            1,
            _setting_int(settings, "max_open_paper_trades", 50, 1, 1000),
        ),
        "paper_ready": paper_ready,
        "live_ready": live_ready,
        "live_blocker": live_blocker,
        "brain_score": score,
        "avg_clv": avg_clv,
        "realized_pnl_paper": realized_pnl,
        "entry_quality_ok": entry_ok,
        "blockers": blockers,
    }


def get_live_readiness_report() -> dict:
    """Explain whether live automation can place orders and why."""
    from app import config as cfg
    from app.database import get_conn
    from app.services.weather_brain import get_brain_status
    from app.services.position_sizing import recommend_alert

    settings = cfg.load()
    brain = get_brain_status()
    blocker = live_auto_blocker(brain, settings)
    live_settings = dict(settings)
    live_settings["paper_trading"] = False

    conn = get_conn()
    rows = conn.execute(
        """SELECT alerts.id, alerts.market_ticker, alerts.direction,
                  alerts.market_price, alerts.model_prob, alerts.edge,
                  alerts.confidence, alerts.brain_score, alerts.brain_state,
                  alerts.brain_auto_qualified, alerts.phantom_risk_level,
                  alerts.details, markets.raw_json AS market_raw_json
             FROM alerts
             JOIN markets ON markets.ticker = alerts.market_ticker
            WHERE alerts.status = 'pending'
              AND lower(coalesce(markets.status, '')) IN ('open', 'active')
              AND (markets.close_time IS NULL OR datetime(markets.close_time) > datetime('now'))
            LIMIT 200"""
    ).fetchall()
    conn.close()

    candidates = []
    blocked_reasons: dict[str, int] = {}
    for row in rows:
        try:
            details = json.loads(row["details"] or "{}")
        except Exception:
            details = {}
        rec = recommend_alert({**dict(row), "details": details}, live_settings)
        rec = _with_current_segment_learning(rec, details, row["direction"])
        blockers = list(rec.get("blockers") or [])
        for reason in blockers:
            blocked_reasons[reason] = blocked_reasons.get(reason, 0) + 1
        contracts = int(rec.get("contracts") or 0)
        if contracts > 0:
            candidates.append({
                "alert_id": row["id"],
                "ticker": row["market_ticker"],
                "direction": row["direction"],
                "contracts": contracts,
                "limit_price_yes": rec.get("limit_price_yes"),
                "side_edge": rec.get("side_edge"),
                "expected_value_per_contract": rec.get("expected_value_per_contract"),
                "drivers": rec.get("drivers") or [],
            })

    candidates.sort(
        key=lambda item: (
            float(item.get("expected_value_per_contract") or 0.0),
            float(item.get("side_edge") or 0.0),
        ),
        reverse=True,
    )

    return {
        "live_auto_enabled": bool(settings.get("auto_trade_enabled", False)),
        "paper_trading": bool(settings.get("paper_trading", True)),
        "ready": bool(settings.get("auto_trade_enabled", False) and not blocker and candidates),
        "blocker": blocker,
        "brain": {
            "score": brain.get("score"),
            "prediction_accuracy": brain.get("prediction_accuracy"),
            "avg_clv": brain.get("avg_clv"),
            "realized_pnl_paper": brain.get("realized_pnl_paper"),
            "auto_eligible_segments": brain.get("auto_eligible_segments"),
            "paper_auto_eligible_segments": brain.get("paper_auto_eligible_segments"),
            "open_trades": brain.get("open_trades"),
            "pending_settlement_trades": brain.get("pending_settlement_trades"),
        },
        "pending_alerts_checked": len(rows),
        "live_sized_candidates": candidates[:10],
        "blocked_reason_counts": blocked_reasons,
    }
