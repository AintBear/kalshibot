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
    # paper/live: "bid_plus_1c" posts a passive limit one cent above the
    # resting bid instead of crossing the ask. Switch to "ask" to reproduce the
    # legacy spread-paying behavior, or "midpoint" for faster paper sampling.
    "paper_fill_model": "bid_plus_1c",
    "live_fill_model": "bid_plus_1c",
    # Intraday observed-temperature injection. When true, weather_model checks
    # what temperature has already been observed for the city today and uses
    # it to sharpen the model probability (e.g. if a HIGH bracket has already
    # been exceeded, model prob -> near certainty).
    "intraday_temps_enabled": True,
    "intraday_temps_cache_seconds": 600,
    # Liquidity floors. Thin Kalshi weather markets ($20-50 24h volume) have
    # wide spreads, bad fills, and outsized slippage when even 1-3 contracts
    # move the price. min_volume_24h is conservative; raise if recent CLV is
    # still negative on the strategy slice.
    "min_volume_24h": 25.0,
    "min_open_interest": 0.0,
    # Entry window: only enter when the market closes within this many hours.
    # Settled-trade audit (2026-06-10, n=734 non-explore market_closed): entries
    # <=12h to close earned +13.06c/contract (n=48, t=+2.49) while entries >24h
    # out lost -7.32c/contract (n=551). Within 12h the day's weather is partly
    # observable, so the model finally knows something the market hasn't priced;
    # further out the market is the better forecaster (Brier 0.205 vs 0.296).
    # Set to 0 to disable the gate.
    "max_entry_hours_to_close": 12.0,
    # ECMWF (European weather model) via Open-Meteo — independent of NWS/GFS,
    # so its agreement/disagreement is a real confidence signal. Set false to
    # save one HTTP call per scan if the third source is causing rate issues.
    "ecmwf_enabled": True,
    "automation_enabled": False,
    "auto_trade_enabled": False,
    # Live execution engine (inert while paper_trading=true). Entries post
    # passively at bid+1c; if outbid, cancel/re-post chasing at most
    # live_max_chase_cents above the original price; inside
    # live_cross_minutes_to_close the order crosses the spread (a passive
    # order that dies unfilled at close has negative expected value vs the
    # measured <=12h entry edge).
    # Shadow-live: when paper_trading=false, the FULL live engine runs
    # (pre-trade checks, work-the-bid, requotes, exits, reconciliation paths)
    # but orders are logged as SHADOW-* instead of being submitted to Kalshi;
    # fills are simulated when the market trades through our price. Real
    # money requires BOTH paper_trading=false AND live_shadow_mode=false —
    # defense in depth so flipping live lands in shadow first.
    "live_shadow_mode": True,
    "live_requote_enabled": True,
    "live_max_chase_cents": 3,
    "live_cross_minutes_to_close": 45,
    "live_max_requotes_per_order": 10,
    # Settlement sniper: trades only MATHEMATICALLY decided markets (observed
    # high already above a bracket / observed low already below one) that the
    # market still misprices by sniper_min_edge_cents. Margin absorbs
    # grid-vs-station skew. Paper-only until sniper_live_enabled (owner call).
    "sniper_enabled": True,
    "sniper_live_enabled": False,
    "sniper_margin_f": 1.5,
    "sniper_min_edge_cents": 5,
    "sniper_max_open": 20,
    # Risk layer. kill_switch blocks all new entries (paper + live) and
    # cancels working live entries; exits stay allowed. Loss limits are
    # realized live P&L in trailing 1d/7d windows; a breach reverts to paper
    # AND trips the kill switch. Caps default to the capped-pilot numbers in
    # docs/STRATEGY_RECOMMENDATIONS.md §6 (owner must still confirm before
    # any live order). All inert while paper_trading=true.
    "kill_switch": False,
    "live_daily_loss_limit": 5.0,
    "live_weekly_loss_limit": 15.0,
    "live_max_total_exposure": 25.0,
    "live_max_contracts_per_trade": 2,
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
