import unittest
from unittest.mock import patch


class TestSliceCalibrationSafeguards(unittest.TestCase):
    """The original slice calibration shipped +0.62 biases from 5-8 sample
    slices in session 4 and had to be disabled. These tests pin the
    safeguards that make it safe to re-enable."""

    def setUp(self):
        from app.services import weather_model
        weather_model.invalidate_calibration_cache()
        self.mod = weather_model

    def test_insufficient_samples_does_not_apply(self):
        # 10 samples is below the 20-sample floor — must pass through.
        with patch.object(
            self.mod, "_load_calibration_slices",
            return_value={("NYC", "high_bracket"): {
                "sample_count": 10,
                "calibration_bias": 0.30,
                "avg_model_prob": 0.10,
                "avg_settlement_rate": 0.40,
            }},
        ):
            adjusted, meta = self.mod._apply_calibration(0.10, "KXHIGHNY-26JUN02-B75.5", "high_bracket")
        self.assertEqual(adjusted, 0.10)
        self.assertFalse(meta["applied"])
        self.assertEqual(meta["reason"], "insufficient_samples")

    def test_bias_is_capped_at_max_abs(self):
        # Real bias is +0.62 (the session-4 disaster). With 100 samples this
        # would naively shift +0.62; the cap keeps it at +0.15.
        with patch.object(
            self.mod, "_load_calibration_slices",
            return_value={("LV", "high_bracket"): {
                "sample_count": 100,
                "calibration_bias": 0.62,
                "avg_model_prob": 0.10,
                "avg_settlement_rate": 0.72,
            }},
        ):
            adjusted, meta = self.mod._apply_calibration(0.10, "KXHIGHTLV-26JUN02-B95.5", "high_bracket")
        self.assertTrue(meta["applied"])
        self.assertEqual(meta["raw_bias"], 0.62)
        self.assertEqual(meta["capped_bias"], 0.15)
        # weight is 1.0 at 100 samples, applied_bias = 0.15 * 1.0 = 0.15
        self.assertAlmostEqual(adjusted, 0.25, places=4)

    def test_ramp_weight_at_min_samples_is_half(self):
        with patch.object(
            self.mod, "_load_calibration_slices",
            return_value={("LAX", "high_bracket"): {
                "sample_count": 20,
                "calibration_bias": 0.10,
                "avg_model_prob": 0.20,
                "avg_settlement_rate": 0.30,
            }},
        ):
            adjusted, meta = self.mod._apply_calibration(0.20, "KXHIGHTLAX-26JUN02-B85.5", "high_bracket")
        self.assertTrue(meta["applied"])
        self.assertAlmostEqual(meta["weight"], 0.5, places=4)
        # bias 0.10 * weight 0.5 = +0.05
        self.assertAlmostEqual(adjusted, 0.25, places=4)

    def test_ramp_weight_at_full_samples_is_one(self):
        with patch.object(
            self.mod, "_load_calibration_slices",
            return_value={("LAX", "high_bracket"): {
                "sample_count": 50,
                "calibration_bias": 0.10,
                "avg_model_prob": 0.20,
                "avg_settlement_rate": 0.30,
            }},
        ):
            adjusted, meta = self.mod._apply_calibration(0.20, "KXHIGHTLAX-26JUN02-B85.5", "high_bracket")
        self.assertAlmostEqual(meta["weight"], 1.0, places=4)
        self.assertAlmostEqual(adjusted, 0.30, places=4)

    def test_no_slice_match_passes_through(self):
        with patch.object(self.mod, "_load_calibration_slices", return_value={}):
            adjusted, meta = self.mod._apply_calibration(0.10, "KXHIGHNY-26JUN02-B75.5", "high_bracket")
        self.assertEqual(adjusted, 0.10)
        self.assertEqual(meta["reason"], "no_slice_calibration")

    def test_negative_bias_capped_at_negative_max(self):
        # Model overconfident (real settlement rate lower than predicted).
        with patch.object(
            self.mod, "_load_calibration_slices",
            return_value={("MIA", "low_bracket"): {
                "sample_count": 100,
                "calibration_bias": -0.40,
                "avg_model_prob": 0.55,
                "avg_settlement_rate": 0.15,
            }},
        ):
            adjusted, meta = self.mod._apply_calibration(0.55, "KXLOWTMIA-26JUN02-B68.5", "low_bracket")
        self.assertEqual(meta["capped_bias"], -0.15)
        self.assertAlmostEqual(adjusted, 0.40, places=4)


class TestBrainScoreBreakdown(unittest.TestCase):
    """Verify the breakdown matches the production score and pinpoints
    the gap between 82 and 90 in the current live runtime."""

    def setUp(self):
        from app.services import weather_brain
        self.mod = weather_brain

    def test_live_runtime_score_decomposes_to_82(self):
        # These are the values from /api/brain/status on 2026-06-01 when
        # the score was 82 — captured here so a regression that breaks the
        # breakdown is obvious in test output.
        result = self.mod._compute_brain_score_breakdown(
            settled=310,
            avg_clv=3.6,
            recent_clv=-0.57,
            positive_clv_rate=0.7484,
            realized_pnl=23.51,
            recent_pnl=-0.54,
            entry_quality_ok=False,
            auto_eligible_count=5,
            prediction_accuracy=0.7484,
            prediction_sample_count=310,
            recent_prediction_accuracy=0.68,
            recent_positive_clv_rate=0.68,
        )
        self.assertEqual(result["score"], 82)
        names = {c["name"]: c for c in result["components"]}
        # The four maxed components.
        self.assertEqual(names["samples"]["value"], 10.0)
        self.assertEqual(names["segments"]["value"], 10.0)
        self.assertEqual(names["positive_rate"]["value"], 15.0)
        self.assertEqual(names["prediction"]["value"], 30.0)
        # The three that are short — biggest gap should be recent_pnl OR
        # recent_clv depending on which has more absolute headroom.
        self.assertGreater(names["clv"]["headroom"], 0.0)
        self.assertGreater(names["recent_clv"]["headroom"], 0.0)
        self.assertGreater(names["recent_pnl"]["headroom"], 0.0)
        # Biggest single source of upside is identified.
        self.assertIn(result["biggest_gap"]["component"], {"clv", "recent_clv", "recent_pnl"})

    def test_perfect_inputs_hit_100(self):
        result = self.mod._compute_brain_score_breakdown(
            settled=500, avg_clv=8.0, recent_clv=5.0,
            positive_clv_rate=0.80, realized_pnl=200.0, recent_pnl=20.0,
            entry_quality_ok=True, auto_eligible_count=10,
            prediction_accuracy=0.80, prediction_sample_count=500,
            recent_prediction_accuracy=0.80, recent_positive_clv_rate=0.80,
        )
        self.assertEqual(result["score"], 100)

    def test_breakdown_matches_score_function(self):
        # The wrapper _compute_brain_score must equal the breakdown's score.
        args = dict(
            settled=120, avg_clv=1.0, recent_clv=-0.5,
            positive_clv_rate=0.55, realized_pnl=10.0, recent_pnl=-0.2,
            entry_quality_ok=False, auto_eligible_count=3,
            prediction_accuracy=0.60, prediction_sample_count=120,
            recent_prediction_accuracy=0.60, recent_positive_clv_rate=0.60,
        )
        score = self.mod._compute_brain_score(**args)
        breakdown = self.mod._compute_brain_score_breakdown(**args)
        self.assertEqual(score, breakdown["score"])


if __name__ == "__main__":
    unittest.main()
