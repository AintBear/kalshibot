import os
import sqlite3
import tempfile
import unittest
import time
from unittest.mock import patch
from app.services.weather_model import (
    _phantom_risk_assessment,
    _temp_exceed_prob,
    _temp_market_prob,
    _market_to_coords,
    _target_date_from_ticker,
    _requires_accumulation_model,
    _segment_from_ticker,
    _estimate_confidence,
    _estimate_model_prob,
    _extract_accuweather_forecast,
    _merge_forecasts,
    _market_anchor,
)


class _RateLimitedResponse:
    status_code = 429
    headers = {}

    def raise_for_status(self):
        raise AssertionError("rate-limit responses should be handled before raise_for_status")


class _ForbiddenResponse:
    status_code = 403
    headers = {}

    def raise_for_status(self):
        raise AssertionError("forbidden responses should be handled before raise_for_status")


class _JsonResponse:
    status_code = 200

    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class TestPhantomRisk(unittest.TestCase):
    def test_no_phantom_risk_below_threshold(self):
        result = _phantom_risk_assessment(0.10, 0.60, 0.50, 0.70)
        self.assertEqual(result["level"], "none")
        self.assertEqual(result["score"], 0.0)

    def test_phantom_risk_high_confidence(self):
        result = _phantom_risk_assessment(0.40, 0.50, 0.50, 0.20)
        self.assertIn("low_confidence", result["flags"])
        self.assertGreaterEqual(result["score"], 40)

    def test_phantom_risk_thin_market(self):
        result = _phantom_risk_assessment(0.40, 0.50, 0.90, 0.70)
        self.assertIn("thin_market", result["flags"])

    def test_phantom_risk_extreme_model_prob(self):
        result = _phantom_risk_assessment(0.40, 0.95, 0.50, 0.70)
        self.assertIn("extreme_model_prob", result["flags"])

    def test_phantom_level_high(self):
        result = _phantom_risk_assessment(0.40, 0.95, 0.50, 0.20)
        self.assertEqual(result["level"], "high")


class TestTempExceedProb(unittest.TestCase):
    def test_forecast_equals_threshold_is_fifty(self):
        prob = _temp_exceed_prob(70.0, 70.0)
        self.assertAlmostEqual(prob, 0.5, places=2)

    def test_forecast_above_threshold_gt_fifty(self):
        prob = _temp_exceed_prob(80.0, 70.0)
        self.assertGreater(prob, 0.5)

    def test_forecast_below_threshold_lt_fifty(self):
        prob = _temp_exceed_prob(60.0, 70.0)
        self.assertLess(prob, 0.5)

    def test_between_bracket_is_not_treated_as_below_threshold(self):
        prob = _temp_market_prob(
            55.0,
            "KXHIGHTBOS-26APR27-B61.5",
            {"strike_type": "between", "floor_strike": 61, "cap_strike": 62},
            sigma=4.0,
        )
        self.assertLess(prob, 0.10)

    def test_greater_threshold_uses_yes_probability(self):
        prob = _temp_market_prob(
            92.0,
            "KXHIGHMIA-26APR27-T88",
            {"strike_type": "greater", "floor_strike": 88},
            sigma=4.0,
        )
        self.assertGreater(prob, 0.75)


class TestMarketMapping(unittest.TestCase):
    def test_series_maps_to_boston_coordinates(self):
        lat, lon = _market_to_coords("KXHIGHTBOS-26APR27-B61.5")
        self.assertAlmostEqual(lat, 42.3656, places=3)
        self.assertAlmostEqual(lon, -71.0096, places=3)

    def test_target_date_comes_from_market_ticker_not_utc_close(self):
        target = _target_date_from_ticker("KXHIGHTBOS-26APR27-B61.5", "2026-04-28T05:00:00Z")
        self.assertEqual(target, "2026-04-27")

    def test_monthly_rain_requires_accumulation_model(self):
        self.assertTrue(_requires_accumulation_model("KXRAINMIAM-26APR-3", {"rules_primary": "total precipitation above 3 inches"}))

    def test_monthly_rain_requires_real_accumulation_model(self):
        prob = _estimate_model_prob(
            "KXRAINMIAM-26APR-3",
            0.30,
            {"precip_pct": 60},
            "precipitation",
            {"rules_primary": "total precipitation above 3 inches"},
        )
        self.assertIsNone(prob)

    def test_daily_temp_does_not_require_accumulation_model(self):
        self.assertFalse(_requires_accumulation_model("KXHIGHTBOS-26APR27-B61.5", {"rules_primary": "maximum temperature"}))


class TestSegmentDetection(unittest.TestCase):
    def test_segment_detection(self):
        self.assertEqual(_segment_from_ticker("KXRAINNYCM"), "precipitation")
        self.assertEqual(_segment_from_ticker("KXHIGH-NYC-20"), "high_bracket")
        self.assertEqual(_segment_from_ticker("KXLOW-CHI-50"), "low_bracket")
        self.assertEqual(_segment_from_ticker("KXWEATHER-GEN"), "weather_all")


class TestConfidence(unittest.TestCase):
    def test_both_temps_high_confidence(self):
        # model_prob=0.85 gives margin=0.35, scale=0.85 → 0.72*0.85=0.612
        self.assertGreater(_estimate_confidence({"high": 75, "low": 55}, "high_bracket", model_prob=0.85), 0.6)

    def test_both_temps_low_margin_low_confidence(self):
        # model_prob=0.55 (near coin-flip) gives scale=0.55 → 0.72*0.55=0.396
        self.assertLess(_estimate_confidence({"high": 75, "low": 55}, "high_bracket", model_prob=0.55), 0.45)

    def test_no_forecast_low_confidence(self):
        self.assertLess(_estimate_confidence({}, "high_bracket"), 0.4)

    def test_source_disagreement_reduces_confidence(self):
        clean = _estimate_confidence({"high": 75, "low": 55}, "high_bracket", model_prob=0.85)
        split = _estimate_confidence({"high": 75, "low": 55, "source_disagreement": 6.0}, "high_bracket", model_prob=0.85)
        self.assertLess(split, clean)

    def test_single_forecast_source_penalty(self):
        single = _estimate_confidence(
            {"high": 75, "low": 55, "forecast_sources": ["nws_free"]},
            "high_bracket",
            model_prob=0.85,
        )
        blended = _estimate_confidence(
            {"high": 75, "low": 55, "forecast_sources": ["nws_free", "accuweather"]},
            "high_bracket",
            model_prob=0.85,
        )
        self.assertLess(single, blended)

    def test_active_weather_event_bonus_raises_confidence(self):
        base = _estimate_confidence(
            {"high": 105, "low": 82, "forecast_sources": ["nws_free", "accuweather"]},
            "high_bracket",
            model_prob=0.85,
            event_bonus=0.0,
        )
        boosted = _estimate_confidence(
            {"high": 105, "low": 82, "forecast_sources": ["nws_free", "accuweather"]},
            "high_bracket",
            model_prob=0.85,
            event_bonus=0.20,
        )
        self.assertGreater(boosted, base)


class TestAccuWeatherSupplement(unittest.TestCase):
    def tearDown(self):
        from app.services import weather_model
        weather_model._ACCU_BACKOFF_UNTIL = 0.0
        weather_model._ACCU_RATE_LIMIT_STREAK = 0
        weather_model._ACCU_LAST_RATE_LIMIT_AT = 0.0
        weather_model._ACCU_LAST_CACHE_EVENT = {}
        weather_model._ACCU_LOCATION_CACHE.clear()
        weather_model._ACCU_FORECAST_CACHE.clear()
        weather_model._ACCU_CURRENT_CACHE.clear()

    def test_extract_accuweather_daily_forecast(self):
        data = {
            "DailyForecasts": [
                {
                    "Date": "2026-04-28T07:00:00-04:00",
                    "Temperature": {
                        "Maximum": {"Value": 72},
                        "Minimum": {"Value": 51},
                    },
                    "Day": {"PrecipitationProbability": 30},
                    "Night": {"RainProbability": 45},
                }
            ]
        }
        result = _extract_accuweather_forecast(data, "2026-04-28")
        self.assertEqual(result["high"], 72.0)
        self.assertEqual(result["low"], 51.0)
        self.assertEqual(result["precip_pct"], 45.0)

    def test_merge_forecasts_averages_available_sources(self):
        result = _merge_forecasts(
            {"high": 70, "low": 50, "precip_pct": 20},
            {"high": 74, "low": 52, "precip_pct": 40},
        )
        self.assertEqual(result["high"], 71.6)
        self.assertEqual(result["low"], 50.8)
        self.assertEqual(result["precip_pct"], 28.0)
        self.assertEqual(result["source"], "NWS+AccuWeather")
        self.assertEqual(result["source_disagreement"], 4.0)
        self.assertEqual(result["precip_source_disagreement"], 20.0)

    def test_accuweather_429_sets_global_backoff(self):
        from app.services import weather_model

        weather_model._ACCU_BACKOFF_UNTIL = 0.0
        weather_model._ACCU_LOCATION_CACHE.clear()

        with patch("app.services.weather_model._accuweather_api_key", return_value="key"), \
             patch("app.services.weather_model.requests.get", return_value=_RateLimitedResponse()) as get:
            self.assertIsNone(weather_model._fetch_accuweather_location_key(42.0, -71.0))
            self.assertGreater(weather_model._ACCU_BACKOFF_UNTIL, time.time())
            self.assertIsNone(weather_model._fetch_accuweather_location_key(39.0, -75.0))

        self.assertEqual(get.call_count, 1)

    def test_accuweather_403_sets_global_backoff(self):
        from app.services import weather_model

        weather_model._ACCU_BACKOFF_UNTIL = 0.0
        weather_model._ACCU_LOCATION_CACHE.clear()

        with patch("app.services.weather_model._accuweather_api_key", return_value="key"), \
             patch("app.services.weather_model.requests.get", return_value=_ForbiddenResponse()) as get:
            self.assertIsNone(weather_model._fetch_accuweather_location_key(42.0, -71.0))
            self.assertGreater(weather_model._ACCU_BACKOFF_UNTIL, time.time())
            self.assertIsNone(weather_model._fetch_accuweather_location_key(39.0, -75.0))

        self.assertEqual(get.call_count, 1)
        self.assertEqual(weather_model.accuweather_cache_status()["last_cache_event"]["reason"], "auth_or_permission_failed")

    def test_accuweather_429_uses_recent_cached_forecast(self):
        from app.services import weather_model

        weather_model._ACCU_LOCATION_CACHE[(42.0, -71.0)] = {"key": "loc", "_ts": time.time()}
        cached = {"DailyForecasts": [{"Date": "2026-04-28T00:00:00Z"}]}
        weather_model._ACCU_FORECAST_CACHE["loc"] = {"data": cached, "_ts": time.time() - 3600}

        with patch("app.services.weather_model._accuweather_api_key", return_value="key"), \
             patch("app.services.weather_model.requests.get", return_value=_RateLimitedResponse()):
            result = weather_model._fetch_accuweather_forecast(42.0, -71.0)

        self.assertEqual(result, cached)
        status = weather_model.accuweather_cache_status()
        self.assertEqual(status["last_cache_event"]["reason"], "rate_limited")
        self.assertGreaterEqual(status["last_cache_event"]["cache_age_seconds"], 3600)

    def test_score_market_uses_settlement_station_forecast_coordinates(self):
        from app.services import weather_model

        with patch("app.services.weather_model._fetch_nws_forecast", return_value=None) as nws, \
             patch("app.services.weather_model._fetch_accuweather_forecast", return_value=None), \
             patch("app.services.weather_model.current_conditions_for_ticker", return_value={}), \
             patch("app.services.weather_model._estimate_model_prob", return_value=0.60), \
             patch("app.services.weather_model._apply_calibration", side_effect=lambda p, t, s: (p, {"applied": False})), \
             patch("app.services.weather_model._weather_event_context", return_value={"events": [], "confidence_bonus": 0.0}):
            result = weather_model.score_market("KXHIGHCHI-26APR30-B70", 0.40, market={"close_time": "2099-01-01T00:00:00Z"})

        args = nws.call_args.args
        self.assertAlmostEqual(args[0], weather_model.SETTLEMENT_STATION_COORDS["KMDW"][0], places=3)
        self.assertEqual(result["settlement_station"], "KMDW")
        self.assertEqual(result["forecast_coordinates_source"], "settlement_station")


class TestModelCalibration(unittest.TestCase):
    def setUp(self):
        self.old_db_path = os.environ.get("DB_PATH")
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.db_file.name
        os.environ["DB_PATH"] = self.db_path
        from app.database import init_db
        init_db()

    def tearDown(self):
        os.unlink(self.db_path)
        if self.old_db_path is None:
            os.environ.pop("DB_PATH", None)
        else:
            os.environ["DB_PATH"] = self.old_db_path

    def test_calibration_disabled_returns_unchanged_prob(self):
        from app.services import weather_model

        adjusted, info = weather_model._apply_calibration(0.50, "KXHIGHCHI-26APR30-B70", "high_bracket")
        self.assertFalse(info.get("applied", True))
        self.assertAlmostEqual(adjusted, 0.50, places=4)

    def test_model_calibration_excludes_paper_reset_rows(self):
        from app.services import weather_model

        conn = sqlite3.connect(self.db_path)
        details = '{"city_code":"CHI","segment":"high_bracket"}'
        for i in range(10):
            cur = conn.execute(
                """INSERT INTO alerts
                   (market_ticker, status, model_prob, details)
                   VALUES (?, 'paper_traded', 0.60, ?)""",
                (f"KXHIGHCHI-26APR30-RESET-{i}", details),
            )
            conn.execute(
                """INSERT INTO trades
                   (market_ticker, alert_id, direction, entry_price, exit_price,
                    clv, pnl, contracts, status, exit_reason, paper, entry_time, exit_time)
                   VALUES (?, ?, 'yes', 0.40, 1.0, 0.60, 0.60, 1,
                           'closed', 'paper_reset', 1, datetime('now'), datetime('now'))""",
                (f"KXHIGHCHI-26APR30-RESET-{i}", cur.lastrowid),
            )
        conn.commit()
        conn.close()

        result = weather_model.update_model_calibration()
        self.assertEqual(result["segments_seen"], 0)
        self.assertEqual(result["updated"], 0)


class TestMarketAnchor(unittest.TestCase):
    def test_no_anchoring_when_close(self):
        self.assertAlmostEqual(_market_anchor(0.60, 0.70), 0.60, places=4)

    def test_anchors_toward_extreme_high_market(self):
        result = _market_anchor(0.04, 0.99)
        self.assertGreater(result, 0.04)
        self.assertLess(result, 0.99)

    def test_anchors_toward_extreme_low_market(self):
        result = _market_anchor(0.90, 0.05)
        self.assertLess(result, 0.90)
        self.assertGreater(result, 0.05)

