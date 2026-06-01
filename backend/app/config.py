import json
import os
from pathlib import Path

_repo_root = Path(__file__).resolve().parents[2]
_default_config_path = str(_repo_root / "config" / "settings.json")
CONFIG_PATH = os.environ.get("CONFIG_PATH", _default_config_path)

_defaults = {
    "paper_trading": True,
    "auto_paper_trade_enabled": False,
    "paper_unlimited_learning": False,
    "max_open_paper_trades": 50,
    "paper_settlement_backlog_limit": 20,
    "paper_learning_max_entries_per_scan": 0,
    "paper_learning_max_open_per_event": 0,
    "paper_learning_min_ev": 0.0,
    "paper_learning_min_side_edge": 0.0,
    "paper_learning_min_confidence": 0.0,
    "paper_learning_max_contracts": 3,
    "paper_learning_explore_enabled": False,
    "paper_learning_explore_max_per_scan": 3,
    "paper_learning_explore_max_open": 30,
    # Fill model controls how entry_price is computed from the live order book.
    # paper: "midpoint" simulates a passive limit order; switch to "ask" to
    # reproduce the legacy spread-paying behavior. live: stays "ask" until the
    # live limit-order management plumbing in order_manager.py lands.
    "paper_fill_model": "midpoint",
    "live_fill_model": "ask",
    # Intraday observed-temperature injection. When true, weather_model checks
    # what temperature has already been observed for the city today and uses
    # it to sharpen the model probability (e.g. if a HIGH bracket has already
    # been exceeded, model prob -> near certainty).
    "intraday_temps_enabled": True,
    "intraday_temps_cache_seconds": 600,
    "automation_enabled": False,
    "auto_trade_enabled": False,
    "max_contracts_per_trade": 5,
    "stop_loss_pct": 0.50,
    "take_profit_pct": 0.50,
    "kelly_fraction": 0.25,
    "max_trade_risk_fraction": 0.025,
    "paper_starting_balance": 500.0,
    "kalshi_key_id": "",
    "kalshi_private_key_path": str(_repo_root / "config" / "kalshi_private_key.pem"),
    "kalshi_api_base_url": "https://external-api.kalshi.com/trade-api/v2",
    "noaa_token": "",
    "nws_user_agent": "sibylla-weather-bot/1.0 contact@sibylla.local",
    "accuweather_api_key": "",
    "scan_interval_minutes": 15,
    "stale_alert_expiry_minutes": 60,
}


def load() -> dict:
    path = Path(CONFIG_PATH)
    if path.exists():
        try:
            with open(path) as f:
                data = json.load(f)
            return {**_defaults, **data}
        except Exception:
            pass
    return dict(_defaults)


def save(settings: dict):
    path = Path(CONFIG_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(settings, f, indent=2)
