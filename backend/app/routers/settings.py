from fastapi import APIRouter, Body
from app import config as cfg

router = APIRouter()

_EDITABLE = {
    "paper_trading", "auto_paper_trade_enabled", "automation_enabled", "auto_trade_enabled",
    "paper_unlimited_learning", "paper_settlement_backlog_limit",
    "max_contracts_per_trade", "stop_loss_pct", "take_profit_pct",
    "kelly_fraction", "max_trade_risk_fraction",
    "paper_starting_balance", "max_open_paper_trades",
    "paper_learning_max_entries_per_scan", "paper_learning_max_open_per_event",
    "paper_learning_min_ev", "paper_learning_min_side_edge",
    "paper_learning_min_confidence", "paper_learning_max_contracts",
    "daily_loss_limit_paper", "weekly_loss_limit_paper",
    "scan_interval_minutes", "stale_alert_expiry_minutes",
    "kalshi_key_id", "kalshi_private_key_path",
    "noaa_token", "nws_user_agent", "accuweather_api_key",
    "circuit_breaker_consecutive_losses",
}

_SECRET_KEYS = {"kalshi_key_id", "noaa_token", "accuweather_api_key"}


@router.get("/settings")
def get_settings():
    data = cfg.load()
    masked = dict(data)
    for key in _SECRET_KEYS:
        masked[f"{key}_configured"] = bool(data.get(key))
        masked[key] = ""
    return masked


@router.post("/settings")
def save_settings(payload: dict = Body(...)):
    return _save_settings(payload)


@router.patch("/settings")
def patch_settings(payload: dict = Body(...)):
    return _save_settings(payload)


def _save_settings(payload: dict):
    current = cfg.load()
    updates = {}
    for k, v in payload.items():
        if k in _EDITABLE:
            if k in _SECRET_KEYS and v in ("", None, "********"):
                continue
            updates[k] = v

    candidate = {**current, **updates}
    if updates.get("auto_trade_enabled") is True:
        _validate_live_auto_settings(candidate)

    current.update(updates)
    cfg.save(current)
    return {"saved": True}


@router.get("/settings/effective-risk")
def effective_risk():
    from app.services.weather_brain import get_brain_status
    settings = cfg.load()
    brain = get_brain_status()
    score = brain.get("score", 0)
    pred_accuracy = brain.get("prediction_accuracy", 0)
    avg_clv = brain.get("avg_clv", 0)
    positive_rate = brain.get("positive_clv_rate", 0)

    base_kelly = float(settings.get("kelly_fraction", 0.25) or 0.25)
    base_risk = float(settings.get("max_trade_risk_fraction", 0.025) or 0.025)
    base_max_contracts = int(settings.get("max_contracts_per_trade", 5) or 5)

    if pred_accuracy > 0 and pred_accuracy < 0.40:
        risk_scalar = 0.25
        reason = f"Prediction accuracy {pred_accuracy*100:.0f}% < 40% — risk heavily reduced"
    elif score >= 80:
        risk_scalar = 1.0
        reason = "Brain score high — full risk budget"
    elif score >= 60:
        risk_scalar = 0.6
        reason = f"Brain score {score} — moderate risk"
    else:
        risk_scalar = 0.35
        reason = f"Brain score {score} — conservative risk"

    effective_kelly = round(base_kelly * risk_scalar, 4)
    effective_risk = round(base_risk * risk_scalar, 4)
    effective_contracts = max(1, int(base_max_contracts * risk_scalar))

    return {
        "brain_score": score,
        "prediction_accuracy": pred_accuracy,
        "risk_scalar": round(risk_scalar, 2),
        "reason": reason,
        "base": {
            "kelly_fraction": base_kelly,
            "max_trade_risk_fraction": base_risk,
            "max_contracts_per_trade": base_max_contracts,
        },
        "effective": {
            "kelly_fraction": effective_kelly,
            "max_trade_risk_fraction": effective_risk,
            "max_contracts_per_trade": effective_contracts,
        },
        "adaptive_factors": [
            f"Brain trust: {score}/100",
            f"Prediction accuracy: {pred_accuracy*100:.1f}%",
            f"Avg entry move: {avg_clv:+.1f}¢",
            f"Good-entry rate: {positive_rate*100:.0f}%",
        ],
    }


def _validate_live_auto_settings(settings: dict):
    from fastapi import HTTPException
    from app.services.kalshi_client import credentials_configured

    if settings.get("paper_trading", True):
        raise HTTPException(
            status_code=400,
            detail="Live auto trading requires Paper Trading to be off. Keep paper on until the live gates are actually ready.",
        )
    if not credentials_configured(settings):
        raise HTTPException(
            status_code=400,
            detail="Live auto trading requires Kalshi API credentials. Configure your key ID and private key path first.",
        )

    try:
        from app.services.weather_brain import get_brain_status
        brain = get_brain_status()
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Live auto trading cannot be enabled until brain readiness can be verified: {exc}",
        )

    from app.services.auto_entry import live_auto_blocker
    blocker = live_auto_blocker(brain, settings)
    if blocker:
        raise HTTPException(
            status_code=400,
            detail=f"Live auto trading is blocked: {blocker}",
        )
