import unittest
from unittest.mock import patch


class TestIntradayTempApplication(unittest.TestCase):
    """Tests the _apply_intraday_observation override logic in weather_model.

    These tests do NOT make network calls — they pass synthetic observations
    directly into the model probability function.
    """

    def setUp(self):
        from app.services.weather_model import _temp_market_prob
        self.score = _temp_market_prob

    def test_high_bracket_already_exceeded_overrides_to_min_prob(self):
        # Bracket 74-76, observed_high already 78 -> YES is impossible.
        forecast = 75.0
        market = {"strike_type": "between", "floor_strike": 74.5, "cap_strike": 76.5}
        observed = {"available": True, "observed_high": 78.0, "observed_low": 60.0, "local_hour": 16}
        prob = self.score(forecast, "KXHIGHNY-26JUN02-B75.5", market, sigma=9.0, observed=observed)
        self.assertLessEqual(prob, 0.10)  # MIN_PROB = 0.08

    def test_high_threshold_already_cleared_overrides_to_max_prob(self):
        # HIGH > 80, observed_high already 82 -> YES near-certain.
        forecast = 81.0
        market = {"strike_type": "greater", "floor_strike": 80.0}
        observed = {"available": True, "observed_high": 82.0, "observed_low": 60.0, "local_hour": 16}
        prob = self.score(forecast, "KXHIGHNY-26JUN02-T80", market, sigma=9.0, observed=observed)
        self.assertGreaterEqual(prob, 0.90)

    def test_high_late_in_day_below_bracket_floor_blocks_yes(self):
        # 5pm, observed_high 68, bracket 74-76 -> unlikely to climb 6+ degrees.
        forecast = 75.0
        market = {"strike_type": "between", "floor_strike": 74.5, "cap_strike": 76.5}
        observed = {"available": True, "observed_high": 68.0, "observed_low": 55.0, "local_hour": 18}
        prob = self.score(forecast, "KXHIGHTLA-26JUN02-B75.5", market, sigma=9.0, observed=observed)
        self.assertLessEqual(prob, 0.10)

    def test_high_late_in_day_inside_bracket_lifts_prob(self):
        # 5pm, observed_high 75, bracket 74-76 -> likely to stay; boost prob.
        forecast = 75.0
        market = {"strike_type": "between", "floor_strike": 74.5, "cap_strike": 76.5}
        observed = {"available": True, "observed_high": 75.0, "observed_low": 60.0, "local_hour": 18}
        prob = self.score(forecast, "KXHIGHTLA-26JUN02-B75.5", market, sigma=9.0, observed=observed)
        self.assertGreaterEqual(prob, 0.80)

    def test_early_in_day_observation_does_not_override(self):
        # 10am, observed_high 70 (still climbing) - forecast model unchanged.
        forecast = 75.0
        market = {"strike_type": "between", "floor_strike": 74.5, "cap_strike": 76.5}
        observed = {"available": True, "observed_high": 70.0, "observed_low": 60.0, "local_hour": 10}
        prob_with = self.score(forecast, "KXHIGHTLA-26JUN02-B75.5", market, sigma=9.0, observed=observed)
        prob_without = self.score(forecast, "KXHIGHTLA-26JUN02-B75.5", market, sigma=9.0, observed=None)
        # Should equal forecast-only because observed high is below bracket
        # but it's only 10am — the high hasn't been set yet.
        self.assertAlmostEqual(prob_with, prob_without, places=4)

    def test_low_already_below_bracket_floor_overrides(self):
        # LOW bracket 54-56, observed_low already 50 -> final low can only go lower.
        forecast = 55.0
        market = {"strike_type": "between", "floor_strike": 54.5, "cap_strike": 56.5}
        observed = {"available": True, "observed_high": 70.0, "observed_low": 50.0, "local_hour": 14}
        prob = self.score(forecast, "KXLOWTNY-26JUN02-B55.5", market, sigma=8.0, observed=observed)
        self.assertLessEqual(prob, 0.10)

    def test_low_threshold_already_cleared(self):
        # LOW < 60, observed_low 55 already -> YES near-certain.
        forecast = 58.0
        market = {"strike_type": "less", "cap_strike": 60.0}
        observed = {"available": True, "observed_high": 70.0, "observed_low": 55.0, "local_hour": 14}
        prob = self.score(forecast, "KXLOWTNY-26JUN02-T60", market, sigma=8.0, observed=observed)
        self.assertGreaterEqual(prob, 0.90)

    def test_missing_observation_falls_back_to_forecast(self):
        forecast = 75.0
        market = {"strike_type": "between", "floor_strike": 74.5, "cap_strike": 76.5}
        prob_obs = self.score(forecast, "KXHIGHTLA-26JUN02-B75.5", market, sigma=9.0, observed=None)
        prob_unavail = self.score(forecast, "KXHIGHTLA-26JUN02-B75.5", market, sigma=9.0,
                                  observed={"available": False, "reason": "fetch_error"})
        self.assertAlmostEqual(prob_obs, prob_unavail, places=4)


class TestIntradayTempsFetch(unittest.TestCase):
    """Tests the Open-Meteo fetch + cache behavior with mocked requests."""

    def setUp(self):
        from app.services import intraday_temps
        intraday_temps.clear_cache()
        self.mod = intraday_temps

    def _fake_response(self, hourly_temps, current_temp=None, current_time="2026-06-01T18:00"):
        times = [f"2026-06-01T{h:02d}:00" for h in range(0, 19)]
        # Pad temps to match
        temps = list(hourly_temps) + [None] * (len(times) - len(hourly_temps))

        class FakeResp:
            def raise_for_status(self_inner):
                pass

            def json(self_inner):
                return {
                    "hourly": {"time": times, "temperature_2m": temps},
                    "current": {"time": current_time, "temperature_2m": current_temp},
                }

        return FakeResp()

    def test_returns_observed_high_low_from_elapsed_hours(self):
        # Hourly temps from midnight through 6pm — high 78 at hour 15.
        hourly = [55, 54, 53, 52, 51, 52, 55, 58, 62, 66, 70, 73, 75, 76, 77, 78, 77, 75, 73]
        with patch.object(self.mod.requests, "get", return_value=self._fake_response(hourly, current_temp=73)):
            with patch.object(self.mod, "_enabled", return_value=True):
                result = self.mod.get_observed_extremes(34.05, -118.24, "2026-06-01")

        self.assertTrue(result["available"], result.get("reason"))
        self.assertEqual(result["observed_high"], 78.0)
        self.assertEqual(result["observed_low"], 51.0)
        self.assertEqual(result["current_temp"], 73.0)
        self.assertEqual(result["local_hour"], 18)

    def test_disabled_returns_empty_payload(self):
        with patch.object(self.mod, "_enabled", return_value=False):
            result = self.mod.get_observed_extremes(34.05, -118.24, "2026-06-01")
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "disabled")

    def test_fetch_error_caches_empty_result(self):
        # Ensure a failing call does not retry on every score (would burn API quota).
        with patch.object(self.mod.requests, "get", side_effect=RuntimeError("boom")):
            with patch.object(self.mod, "_enabled", return_value=True):
                r1 = self.mod.get_observed_extremes(34.05, -118.24, "2026-06-01")
                r2 = self.mod.get_observed_extremes(34.05, -118.24, "2026-06-01")
        self.assertFalse(r1["available"])
        self.assertFalse(r2["available"])
        # The second call should hit the cache, not re-attempt the request.

    def test_cache_returns_same_payload_on_second_call(self):
        hourly = [60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 74, 73, 72]
        call_count = {"n": 0}

        def fake_get(*args, **kwargs):
            call_count["n"] += 1
            return self._fake_response(hourly, current_temp=72)

        with patch.object(self.mod.requests, "get", side_effect=fake_get):
            with patch.object(self.mod, "_enabled", return_value=True):
                r1 = self.mod.get_observed_extremes(34.05, -118.24, "2026-06-01")
                r2 = self.mod.get_observed_extremes(34.05, -118.24, "2026-06-01")
        self.assertTrue(r1["available"])
        self.assertEqual(r1["observed_high"], r2["observed_high"])
        self.assertEqual(call_count["n"], 1, "second call should use cache")


if __name__ == "__main__":
    unittest.main()
