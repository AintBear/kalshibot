import os
import sqlite3
import tempfile
import unittest

os.environ.setdefault("DB_PATH", ":memory:")


class TestAdaptivePolicy(unittest.TestCase):
    def setUp(self):
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.db_file.name
        os.environ["DB_PATH"] = self.db_path
        from app.database import init_db
        init_db()
        from app.services.adaptive_policy import _ensure_segments_table
        _ensure_segments_table()

    def tearDown(self):
        os.unlink(self.db_path)

    def _insert_trades(self, n_trades, avg_clv, avg_pnl, exit_reason="settlement"):
        conn = sqlite3.connect(self.db_path)
        import json
        for i in range(n_trades):
            conn.execute(
                """INSERT INTO trades
                   (market_ticker, direction, entry_price, exit_price, clv, pnl,
                    status, exit_reason, paper, entry_time, exit_time)
                   VALUES (?, 'yes', 0.50, ?, ?, ?, 'closed', ?, 1, datetime('now'), datetime('now'))""",
                (
                    f"TEST-{i}",
                    round(0.50 + avg_clv, 4),
                    round(avg_clv, 4),
                    round(avg_pnl, 4),
                    exit_reason,
                ),
            )
        conn.commit()
        conn.close()

    def test_auto_eligible_with_good_clv(self):
        self._insert_trades(10, 0.030, 0.05)
        from app.services.adaptive_policy import rebuild_snapshots
        result = rebuild_snapshots()
        self.assertIn("weather_all:all", result)
        self.assertTrue(result["weather_all:all"]["auto_eligible"])
        self.assertTrue(result["weather_all:all"]["details"]["paper_auto_eligible"])

    def test_not_auto_eligible_when_hit_rate_or_pnl_is_weak(self):
        conn = sqlite3.connect(self.db_path)
        for i in range(7):
            conn.execute(
                """INSERT INTO trades
                   (market_ticker, direction, entry_price, exit_price, clv, pnl,
                    status, exit_reason, paper, entry_time, exit_time)
                   VALUES (?, 'yes', 0.20, 0.80, 0.60, -0.10, 'closed', 'market_closed', 1, datetime('now'), datetime('now'))""",
                (f"BIGWIN-{i}",),
            )
        for i in range(13):
            conn.execute(
                """INSERT INTO trades
                   (market_ticker, direction, entry_price, exit_price, clv, pnl,
                    status, exit_reason, paper, entry_time, exit_time)
                   VALUES (?, 'yes', 0.50, 0.49, -0.01, -0.10, 'closed', 'stop_loss', 1, datetime('now'), datetime('now'))""",
                (f"DRAG-{i}",),
            )
        conn.commit()
        conn.close()

        from app.services.adaptive_policy import rebuild_snapshots
        result = rebuild_snapshots()

        self.assertGreater(result["weather_all:all"]["avg_clv"], 0.02)
        self.assertLess(result["weather_all:all"]["details"]["positive_clv_rate"], 0.50)
        self.assertFalse(result["weather_all:all"]["auto_eligible"])

    def test_paper_auto_eligible_when_recent_clv_recovers(self):
        conn = sqlite3.connect(self.db_path)
        for i in range(12):
            conn.execute(
                """INSERT INTO trades
                   (market_ticker, direction, entry_price, exit_price, clv, pnl,
                    status, exit_reason, paper, entry_time, exit_time)
                   VALUES (?, 'yes', 0.50, 0.49, -0.01, -0.10, 'closed', 'stop_loss', 1,
                           datetime('now', '-2 days'), datetime('now', '-2 days'))""",
                (f"OLD-DRAG-{i}",),
            )
        for i in range(8):
            conn.execute(
                """INSERT INTO trades
                   (market_ticker, direction, entry_price, exit_price, clv, pnl,
                    status, exit_reason, paper, entry_time, exit_time)
                   VALUES (?, 'yes', 0.40, 0.47, 0.07, 0.07, 'closed', 'take_profit', 1,
                           datetime('now'), datetime('now'))""",
                (f"RECOVERED-{i}",),
            )
        conn.commit()
        conn.close()

        from app.services.adaptive_policy import rebuild_snapshots
        result = rebuild_snapshots()

        self.assertFalse(result["weather_all:all"]["auto_eligible"])
        self.assertTrue(result["weather_all:all"]["details"]["paper_auto_eligible"])

    def test_not_auto_eligible_negative_clv(self):
        self._insert_trades(10, -0.05, -0.10)
        from app.services.adaptive_policy import rebuild_snapshots
        result = rebuild_snapshots()
        self.assertFalse(result["weather_all:all"]["auto_eligible"])

    def test_consistent_small_sample_uses_confidence_bounds(self):
        self._insert_trades(3, 0.050, 0.10)
        from app.services.adaptive_policy import rebuild_snapshots
        result = rebuild_snapshots()
        self.assertTrue(result["weather_all:all"]["auto_eligible"])
        self.assertGreater(result["weather_all:all"]["details"]["clv_lower_bound"], 0)

    def test_rebuild_excludes_paper_reset_from_clv_and_prediction_metrics(self):
        conn = sqlite3.connect(self.db_path)
        for i in range(10):
            conn.execute(
                """INSERT INTO trades
                   (market_ticker, direction, entry_price, exit_price, clv, pnl,
                    status, exit_reason, paper, prediction_correct, entry_time, exit_time)
                   VALUES (?, 'yes', 0.10, 1.00, 0.90, 0.90,
                           'closed', 'paper_reset', 1, 1, datetime('now'), datetime('now'))""",
                (f"RESET-{i}",),
            )
        for i in range(10):
            conn.execute(
                """INSERT INTO trades
                   (market_ticker, direction, entry_price, exit_price, clv, pnl,
                    status, exit_reason, paper, prediction_correct, entry_time, exit_time)
                   VALUES (?, 'yes', 0.50, 0.00, -0.50, -0.50,
                           'closed', 'market_closed', 1, 0, datetime('now'), datetime('now'))""",
                (f"REAL-{i}",),
            )
        conn.commit()
        conn.close()

        from app.services.adaptive_policy import rebuild_snapshots
        result = rebuild_snapshots()
        details = result["weather_all:all"]["details"]

        self.assertEqual(result["weather_all:all"]["trade_count"], 10)
        self.assertEqual(details["prediction_sample_count"], 10)
        self.assertEqual(details["prediction_correct_count"], 0)
        self.assertEqual(details["prediction_accuracy"], 0.0)
        self.assertEqual(result["weather_all:all"]["avg_clv"], -0.5)

    def test_lookup_returns_fallback_for_unknown_segment(self):
        from app.services.adaptive_policy import lookup_adjustment
        adj = lookup_adjustment("unknown:segment")
        self.assertFalse(adj["auto_eligible"])
        self.assertTrue(adj["fallback"])


class TestDescribeContext(unittest.TestCase):
    def setUp(self):
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.db_file.name
        os.environ["DB_PATH"] = self.db_path
        from app.database import init_db
        init_db()
        from app.services.adaptive_policy import _ensure_segments_table
        _ensure_segments_table()

    def tearDown(self):
        os.unlink(self.db_path)

    def test_describe_context_does_not_inherit_auto_eligible_from_aggregate_fallback(self):
        """
        Session 5 regression: describe_context for an unknown segment must NOT
        inherit auto_eligible=True from the aggregate weather_all:all snapshot,
        even if weather_all:all is auto_eligible.
        """
        import json
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """INSERT INTO adaptive_segments
               (segment_key, auto_eligible, avg_clv, avg_pnl, trade_count, details)
               VALUES ('weather_all:all', 1, 0.030, 0.05, 20, '{}')"""
        )
        conn.commit()
        conn.close()

        from app.services.adaptive_policy import describe_context
        ctx = describe_context("some_unknown:segment")
        self.assertFalse(
            ctx["auto_eligible"],
            "Unknown segment must not inherit auto_eligible from aggregate fallback"
        )
        self.assertTrue(ctx["fallback"])

    def test_describe_context_returns_auto_eligible_true_when_segment_has_own_data(self):
        """
        Session 5 regression: describe_context returns auto_eligible=True only
        when the segment itself has qualifying data, not via fallback.
        """
        import json
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """INSERT INTO adaptive_segments
               (segment_key, auto_eligible, avg_clv, avg_pnl, trade_count, details)
               VALUES ('low_bracket:same_day', 1, 0.025, 0.04, 8, '{}')"""
        )
        conn.commit()
        conn.close()

        from app.services.adaptive_policy import describe_context
        ctx = describe_context("low_bracket:same_day")
        self.assertTrue(ctx["auto_eligible"])
        self.assertFalse(ctx["fallback"])
