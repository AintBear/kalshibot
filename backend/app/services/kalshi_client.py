import base64
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

DEFAULT_KALSHI_API_BASE = "https://external-api.kalshi.com/trade-api/v2"
LEGACY_KALSHI_API_BASE = "https://api.elections.kalshi.com/trade-api/v2"
KALSHI_API_BASE = os.environ.get("KALSHI_API_BASE", DEFAULT_KALSHI_API_BASE)
_API_PATH_PREFIX = "/trade-api/v2"


def credentials_configured(settings: dict) -> bool:
    key_id = settings.get("kalshi_key_id")
    key_path = settings.get("kalshi_private_key_path")
    return bool(key_id and key_path and _resolve_private_key_path(key_path).exists())


def get_balance(settings: Optional[dict] = None) -> dict:
    if settings is None:
        from app import config as cfg
        settings = cfg.load()

    if not credentials_configured(settings):
        return {
            "configured": False,
            "connected": False,
            "balance": None,
            "portfolio_value": None,
            "updated_ts": None,
            "error": "Kalshi API key ID or private key file is not configured.",
        }

    path = "/portfolio/balance"
    try:
        response = kalshi_request("GET", path, settings=settings, signed=True, timeout=10)
    except requests.RequestException as exc:
        return {
            "configured": True,
            "connected": False,
            "balance": None,
            "portfolio_value": None,
            "updated_ts": None,
            "error": str(exc),
        }
    if not response.ok:
        return {
            "configured": True,
            "connected": False,
            "balance": None,
            "portfolio_value": None,
            "updated_ts": None,
            "error": f"Kalshi balance request failed: HTTP {response.status_code}",
        }

    data = response.json()
    balance_cents = data.get("balance")
    portfolio_cents = data.get("portfolio_value")
    return {
        "configured": True,
        "connected": True,
        "balance": _cents_to_dollars(balance_cents),
        "portfolio_value": _cents_to_dollars(portfolio_cents),
        "balance_cents": balance_cents,
        "portfolio_value_cents": portfolio_cents,
        "updated_ts": data.get("updated_ts"),
        "error": None,
    }


def get_market(ticker: str) -> Optional[dict]:
    try:
        response = kalshi_request("GET", f"/markets/{ticker}", timeout=10)
    except requests.RequestException:
        return None
    if not response.ok:
        return None
    return response.json().get("market")


def kalshi_api_base(settings: Optional[dict] = None) -> str:
    configured = None
    if settings:
        configured = settings.get("kalshi_api_base_url")
    configured = configured or os.environ.get("KALSHI_API_BASE") or KALSHI_API_BASE
    return _normalize_api_base(configured)


def kalshi_api_bases(settings: Optional[dict] = None) -> list[str]:
    primary = kalshi_api_base(settings)
    bases = [primary]
    for fallback in (DEFAULT_KALSHI_API_BASE, LEGACY_KALSHI_API_BASE):
        normalized = _normalize_api_base(fallback)
        if normalized not in bases:
            bases.append(normalized)
    return bases


def kalshi_request(method: str, path: str, settings: Optional[dict] = None, signed: bool = False, **kwargs):
    """Request Kalshi using the configured external API host with safe fallback."""
    method = method.upper()
    if not path.startswith("/"):
        path = "/" + path
    headers = dict(kwargs.pop("headers", {}) or {})
    headers.setdefault("User-Agent", "sibylla-weather-bot/1.0")
    if signed:
        if settings is None:
            from app import config as cfg
            settings = cfg.load()
        headers.update(_signed_headers(settings, method, path))

    bases = kalshi_api_bases(settings)
    last_exc = None
    attempts = []
    for idx, base in enumerate(bases, start=1):
        url = f"{base}{path}"
        try:
            response = requests.request(
                method,
                url,
                headers=headers,
                **kwargs,
            )
            attempt = {
                "base": base,
                "url": getattr(response, "url", url),
                "status_code": response.status_code,
                "ok": response.ok,
            }
            if not response.ok:
                attempt["body"] = _response_text(response)
            attempts.append(attempt)
            response.kalshi_attempts = list(attempts)
            if response.ok or response.status_code < 500 or idx == len(bases):
                return response
            last_exc = requests.HTTPError(f"Kalshi HTTP {response.status_code}")
            last_exc.response = response
            last_exc.kalshi_attempts = list(attempts)
        except requests.RequestException as exc:
            attempts.append({
                "base": base,
                "url": url,
                "exception_type": exc.__class__.__name__,
                "exception": str(exc),
            })
            exc.kalshi_attempts = list(attempts)
            last_exc = exc
    if last_exc:
        raise last_exc
    raise requests.RequestException("Kalshi request failed")


def settlement_result_from_market(market: Optional[dict]) -> Optional[str]:
    if not market:
        return None
    for key in (
        "result",
        "settlement_result",
        "winning_side",
        "winning_contract",
        "outcome",
        "market_result",
    ):
        result = _normalize_settlement_result(market.get(key))
        if result:
            return result
    return None


def settlement_exit_price_from_market(market: Optional[dict]) -> Optional[float]:
    result = settlement_result_from_market(market)
    if result == "yes":
        return 1.0
    if result == "no":
        return 0.0
    return None


def quote_from_market(market: dict) -> dict:
    yes_bid = _price(market.get("yes_bid_dollars"), market.get("yes_bid"))
    yes_ask = _price(market.get("yes_ask_dollars"), market.get("yes_ask"))
    last_price = _price(market.get("last_price_dollars"), market.get("last_price"))
    if yes_bid is not None and yes_ask is not None:
        mid = round((yes_bid + yes_ask) / 2, 4)
    elif yes_ask is not None:
        mid = round(yes_ask, 4)
    elif last_price is not None:
        mid = round(last_price, 4)
    else:
        mid = None
    return {
        "market_price": mid,
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_bid": _price(market.get("no_bid_dollars"), market.get("no_bid")),
        "no_ask": _price(market.get("no_ask_dollars"), market.get("no_ask")),
        "spread": round((yes_ask - yes_bid), 4) if yes_bid is not None and yes_ask is not None else None,
        "volume": _number(market.get("volume_fp"), market.get("volume")),
        "volume_24h": _number(market.get("volume_24h_fp"), market.get("volume_24h")),
        "liquidity": _number(market.get("liquidity_dollars"), market.get("liquidity")),
    }


def refresh_market_in_db(ticker: str) -> Optional[dict]:
    market = get_market(ticker)
    if not market:
        return None
    quote = quote_from_market(market)
    if quote["market_price"] is None:
        return None

    from app.database import get_conn
    conn = get_conn()
    conn.execute(
        """INSERT INTO markets
           (ticker, title, category, market_price, yes_bid, yes_ask, no_bid, no_ask,
            status, close_time, expiration_time, result, volume, open_interest, raw_json, updated_at)
           VALUES (?, ?, 'weather', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
           ON CONFLICT(ticker) DO UPDATE SET
             title=excluded.title,
             market_price=excluded.market_price,
             yes_bid=excluded.yes_bid,
             yes_ask=excluded.yes_ask,
             no_bid=excluded.no_bid,
             no_ask=excluded.no_ask,
             status=excluded.status,
             close_time=excluded.close_time,
             expiration_time=excluded.expiration_time,
             result=excluded.result,
             volume=excluded.volume,
             open_interest=excluded.open_interest,
             raw_json=excluded.raw_json,
             updated_at=excluded.updated_at""",
        (
            ticker,
            market.get("title") or market.get("subtitle") or ticker,
            quote["market_price"],
            quote["yes_bid"],
            quote["yes_ask"],
            quote["no_bid"],
            quote["no_ask"],
            "open" if market.get("status") in ("open", "active") else market.get("status", "open"),
            market.get("close_time") or market.get("expiration_time"),
            market.get("expiration_time") or market.get("close_time"),
            settlement_result_from_market(market),
            int(float(quote["volume"] or 0)),
            int(float(market.get("open_interest") or 0)),
            json.dumps(market),
        ),
    )
    conn.commit()
    conn.close()
    quote["raw_market"] = market
    quote["updated_at"] = datetime.now(timezone.utc).isoformat()
    return quote


def _signed_headers(settings: dict, method: str, path: str) -> dict:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    timestamp = str(int(time.time() * 1000))
    sig_path = _API_PATH_PREFIX + path
    message = f"{timestamp}{method.upper()}{sig_path}".encode("utf-8")
    private_key = _load_private_key(settings["kalshi_private_key_path"])
    signature = private_key.sign(
        message,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    return {
        "KALSHI-ACCESS-KEY": settings["kalshi_key_id"],
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("utf-8"),
    }


def _load_private_key(key_path: str):
    from cryptography.hazmat.primitives import serialization

    data = _resolve_private_key_path(key_path).read_bytes()
    return serialization.load_pem_private_key(data, password=None)


def _resolve_private_key_path(key_path: str) -> Path:
    path = Path(key_path)
    if path.exists():
        return path
    docker_prefix = "/app/config/"
    if str(key_path).startswith(docker_prefix):
        local_path = Path(__file__).resolve().parents[3] / "config" / str(key_path)[len(docker_prefix):]
        if local_path.exists():
            return local_path
    return path


def _normalize_api_base(value: str) -> str:
    base = str(value or DEFAULT_KALSHI_API_BASE).strip().rstrip("/")
    if not base:
        base = DEFAULT_KALSHI_API_BASE
    prefix_idx = base.find(_API_PATH_PREFIX)
    if prefix_idx >= 0:
        return base[:prefix_idx + len(_API_PATH_PREFIX)]
    return base + _API_PATH_PREFIX


def _response_text(response, limit: int = 800) -> str:
    try:
        return str(response.text or "")[:limit]
    except Exception:
        return ""


def _normalize_settlement_result(value) -> Optional[str]:
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    if text in ("yes", "y", "true", "1", "settled_yes", "yes_win", "yes_won"):
        return "yes"
    if text in ("no", "n", "false", "0", "settled_no", "no_win", "no_won"):
        return "no"
    return None


def backfill_settlements() -> dict:
    """Compatibility wrapper for the canonical lifecycle backfill path."""
    from app.services.trade_lifecycle import backfill_settlements as _backfill

    return _backfill()


def _cents_to_dollars(value):
    if value is None:
        return None
    return round(float(value) / 100.0, 2)


def _price(*values):
    for value in values:
        if value in (None, ""):
            continue
        try:
            n = float(value)
        except (TypeError, ValueError):
            continue
        if n > 1:
            n = n / 100.0
        return round(n, 4)
    return None


def _number(*values):
    for value in values:
        if value in (None, ""):
            continue
        try:
            return round(float(value), 4)
        except (TypeError, ValueError):
            continue
    return None
