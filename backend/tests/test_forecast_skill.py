import json
import os
import sqlite3
import tempfile
import unittest


class _SkillCase(unittest.TestCase):
    def setUp(self):
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_file.close()
        os.environ["DB_PATH"] = self.db_file.name
        from app.database import init_db
        init_db()
        from app.services import forecast_skill
        forecast_skill._CACHE["ts"] = 0.0  # fresh lookup cache per test

    def tearDown(self):
        os.unlink(self.db_file.name)

    def conn(self):
        c = sqlite3.connect(self.db_file.name)
        c.row_factory = sqlite3.Row
        return c

    def _seed_event(self, series, date, actual_mid, forecast_temp):
        """One settled event: YES bracket at actual_mid, alert carrying the forecast."""
        kind = "low" if "LOW" in series else "high"
        c = self.conn()
        c.execute(
            """INSERT INTO trades (market_ticker, direction, entry_price, contracts,
                                   status, paper, settlement_result, exit_reason)
               VALUES (?, 'no', 0.30, 1, 'closed', 1, 'yes', 'market_closed')""",
            (f"{series}-{date}-B{actual_mid}",),
        )
        c.execute(
            "INSERT INTO alerts (market_ticker, status, details) VALUES (?, 'expired', ?)",
            (f"{series}-{date}-B{actual_mid}",
             json.dumps({"forecast": {kind: forecast_temp}})),
        )
        c.commit()
        c.close()


class TestRebuildCitySkill(_SkillCase):
    def test_computes_bias_and_std(self):
        # KXHIGHNY: forecast always 2F above actual -> bias +2, sd 0.
        for i, (actual, fc) in enumerate([(80.5, 82.5), (75.5, 77.5), (70.5, 72.5)]):
            self._seed_event("KXHIGHNY", f"26JUN{10 + i:02d}", actual, fc)
        from app.services.forecast_skill import rebuild_city_skill

        result = rebuild_city_skill()
        self.assertGreaterEqual(result["updated"], 1)

        c = self.conn()
        row = c.execute(
            "SELECT * FROM city_forecast_skill WHERE series='KXHIGHNY' AND kind='high'"
        ).fetchone()
        c.close()
        self.assertEqual(row["sample_count"], 3)
        self.assertAlmostEqual(row["bias"], 2.0, places=3)
        self.assertAlmostEqual(row["error_std"], 0.0, places=3)

    def test_low_series_uses_low_forecast(self):
        self._seed_event("KXLOWTCHI", "26JUN10", 60.5, 61.5)
        self._seed_event("KXLOWTCHI", "26JUN11", 58.5, 57.5)
        from app.services.forecast_skill import rebuild_city_skill

        rebuild_city_skill()
        c = self.conn()
        row = c.execute(
            "SELECT * FROM city_forecast_skill WHERE series='KXLOWTCHI' AND kind='low'"
        ).fetchone()
        c.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["sample_count"], 2)


class TestBaseSigmaFor(_SkillCase):
    def _seed_skill(self, series, kind, n, sd):
        c = self.conn()
        c.execute(
            """INSERT INTO city_forecast_skill (series, kind, sample_count, bias, error_std)
               VALUES (?, ?, ?, 0.0, ?)""",
            (series, kind, n, sd),
        )
        c.commit()
        c.close()

    def test_falls_back_to_legacy_without_data(self):
        from app.services.forecast_skill import base_sigma_for

        self.assertEqual(base_sigma_for("KXHIGHNY-26JUN15-B82.5", "high"), 9.0)
        self.assertEqual(base_sigma_for("KXLOWTCHI-26JUN15-B60.5", "low"), 8.0)

    def test_uses_city_sigma_at_full_ramp(self):
        self._seed_skill("KXHIGHNY", "high", 40, 2.0)
        from app.services.forecast_skill import base_sigma_for

        self.assertAlmostEqual(base_sigma_for("KXHIGHNY-26JUN15-B82.5", "high"), 2.0, places=2)

    def test_blends_toward_global_below_ramp(self):
        # Global pool: 40 samples sd 2.0 (other city) + this city 10 samples sd 4.0.
        self._seed_skill("KXHIGHCHI", "high", 40, 2.0)
        self._seed_skill("KXHIGHNY", "high", 10, 4.0)
        from app.services.forecast_skill import base_sigma_for

        sigma = base_sigma_for("KXHIGHNY-26JUN15-B82.5", "high")
        # w = 10/20 = 0.5; global pooled sd = (40*2 + 10*4)/50 = 2.4
        self.assertAlmostEqual(sigma, 0.5 * 4.0 + 0.5 * 2.4, places=2)

    def test_thin_city_uses_global(self):
        self._seed_skill("KXHIGHCHI", "high", 40, 2.5)
        self._seed_skill("KXHIGHNY", "high", 3, 9.9)  # below MIN_SAMPLES
        from app.services.forecast_skill import base_sigma_for

        sigma = base_sigma_for("KXHIGHNY-26JUN15-B82.5", "high")
        # pooled global = (40*2.5 + 3*9.9)/43 ~= 3.016
        self.assertLess(sigma, 3.5)
        self.assertGreater(sigma, 2.5)

    def test_never_wider_than_legacy_and_never_below_floor(self):
        self._seed_skill("KXHIGHNY", "high", 40, 25.0)
        self._seed_skill("KXLOWTCHI", "low", 40, 0.2)
        from app.services.forecast_skill import base_sigma_for

        self.assertEqual(base_sigma_for("KXHIGHNY-26JUN15-B82.5", "high"), 9.0)
        self.assertEqual(base_sigma_for("KXLOWTCHI-26JUN15-B60.5", "low"), 1.0)


class TestSnapshotBackfill(_SkillCase):
    def test_resolves_snapshots_from_settled_bracket(self):
        self._seed_event("KXHIGHNY", "26JUN10", 80.5, 81.0)
        c = self.conn()
        c.execute(
            """INSERT INTO forecast_snapshots (market_ticker, snapshot_date, forecast_high, resolved)
               VALUES ('KXHIGHNY-26JUN10-B80.5', '2026-06-10', 81.0, 0)"""
        )
        c.execute(
            """INSERT INTO forecast_snapshots (market_ticker, snapshot_date, forecast_high, resolved)
               VALUES ('KXHIGHNY-26JUN12-B79.5', '2026-06-12', 78.0, 0)"""
        )
        c.commit()
        c.close()

        from app.services.forecast_skill import backfill_forecast_actuals

        result = backfill_forecast_actuals()
        self.assertEqual(result["resolved"], 1)

        c = self.conn()
        done = c.execute(
            "SELECT actual_high, resolved FROM forecast_snapshots WHERE market_ticker LIKE '%26JUN10%'"
        ).fetchone()
        pending = c.execute(
            "SELECT resolved FROM forecast_snapshots WHERE market_ticker LIKE '%26JUN12%'"
        ).fetchone()
        c.close()
        self.assertEqual(done["resolved"], 1)
        self.assertAlmostEqual(done["actual_high"], 80.5)
        self.assertFalse(pending["resolved"])


class TestModelUsesSkillSigma(_SkillCase):
    def test_score_path_picks_up_learned_sigma(self):
        c = self.conn()
        c.execute(
            """INSERT INTO city_forecast_skill (series, kind, sample_count, bias, error_std)
               VALUES ('KXHIGHNY', 'high', 40, 0.0, 2.0)"""
        )
        c.commit()
        c.close()

        from app.services.forecast_skill import base_sigma_for
        from app.services.weather_model import _adaptive_sigma

        base = base_sigma_for("KXHIGHNY-26JUN15-B82.5", "high")
        self.assertAlmostEqual(base, 2.0, places=2)
        # >24h: time scale 1.10, floor 2.0 -> 2.2
        self.assertAlmostEqual(_adaptive_sigma(base, 30.0, {}), 2.2, places=2)


if __name__ == "__main__":
    unittest.main()
