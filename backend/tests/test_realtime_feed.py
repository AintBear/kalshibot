import os
import sqlite3
import tempfile
import unittest


class TestSnapshotThrottle(unittest.TestCase):
    def test_first_quote_always_snapshots(self):
        from app.services.realtime import should_snapshot

        self.assertTrue(should_snapshot(None, None, 0.30, 1000.0, None))

    def test_quote_within_interval_and_small_move_skipped(self):
        from app.services.realtime import should_snapshot

        self.assertFalse(should_snapshot(995.0, 0.30, 0.31, 1000.0, None))

    def test_big_move_snapshots_inside_interval(self):
        from app.services.realtime import should_snapshot

        self.assertTrue(should_snapshot(995.0, 0.30, 0.33, 1000.0, None))

    def test_interval_elapsed_snapshots(self):
        from app.services.realtime import should_snapshot

        self.assertTrue(should_snapshot(960.0, 0.30, 0.30, 1000.0, None))

    def test_near_close_uses_faster_interval(self):
        from app.services.realtime import should_snapshot

        # 12s elapsed: under the 30s normal interval, over the 10s close interval.
        self.assertFalse(should_snapshot(988.0, 0.30, 0.30, 1000.0, minutes_to_close=120.0))
        self.assertTrue(should_snapshot(988.0, 0.30, 0.30, 1000.0, minutes_to_close=20.0))

    def test_no_mid_never_snapshots(self):
        from app.services.realtime import should_snapshot

        self.assertFalse(should_snapshot(None, None, None, 1000.0, 5.0))


class TestWsHelpers(unittest.TestCase):
    def test_ws_url_from_api_base(self):
        from app.services.realtime import ws_url_from_api_base

        self.assertEqual(
            ws_url_from_api_base("https://external-api.kalshi.com/trade-api/v2"),
            "wss://external-api.kalshi.com/trade-api/ws/v2",
        )

    def test_quote_from_ws_converts_cents(self):
        from app.services.realtime import RealtimeFeed

        q = RealtimeFeed._quote_from_ws({"yes_bid": 28, "yes_ask": 32, "price": 30})
        self.assertAlmostEqual(q["yes_bid"], 0.28)
        self.assertAlmostEqual(q["yes_ask"], 0.32)
        self.assertAlmostEqual(q["yes_mid"], 0.30)
        self.assertAlmostEqual(q["last_price"], 0.30)

    def test_quote_from_ws_prefers_dollar_fields(self):
        from app.services.realtime import RealtimeFeed

        # Real schema observed live on the elections host.
        q = RealtimeFeed._quote_from_ws({
            "market_ticker": "KXHIGHCHI-26JUN10-B90.5",
            "price_dollars": "0.3000",
            "yes_bid_dollars": "0.2600",
            "yes_ask_dollars": "0.2900",
            "yes_bid_size_fp": "5.03",
            "yes_ask_size_fp": "200.00",
            "volume_fp": "7627.94",
            "open_interest_fp": "4323.66",
            "ts": 1781069104,
        })
        self.assertAlmostEqual(q["yes_bid"], 0.26)
        self.assertAlmostEqual(q["yes_ask"], 0.29)
        self.assertAlmostEqual(q["yes_mid"], 0.275)
        self.assertAlmostEqual(q["last_price"], 0.30)
        self.assertAlmostEqual(q["yes_bid_size"], 5.03)
        self.assertAlmostEqual(q["volume"], 7627.94)

    def test_true_clv_direction(self):
        from app.services.realtime import true_clv

        # NO trade entered at YES 0.30; market closed at YES 0.25 -> entry beat close by 5c.
        self.assertAlmostEqual(true_clv("no", 0.30, 0.25), 0.05)
        # YES trade entered at 0.30; close 0.25 -> entry was 5c worse than close.
        self.assertAlmostEqual(true_clv("yes", 0.30, 0.25), -0.05)


class TestCloseMarkAndTrueClv(unittest.TestCase):
    def setUp(self):
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_file.close()
        os.environ["DB_PATH"] = self.db_file.name
        from app.database import init_db
        init_db()

    def tearDown(self):
        os.unlink(self.db_file.name)

    def _conn(self):
        conn = sqlite3.connect(self.db_file.name)
        conn.row_factory = sqlite3.Row
        return conn

    def test_close_mark_picks_last_snapshot_before_close(self):
        from app.services.realtime import close_mark_for

        conn = self._conn()
        conn.execute(
            """INSERT INTO price_snapshots (market_ticker, yes_bid, yes_ask, yes_mid, created_at)
               VALUES ('KXHIGHNY-T', 0.20, 0.24, 0.22, '2026-06-10 20:00:00')"""
        )
        conn.execute(
            """INSERT INTO price_snapshots (market_ticker, yes_bid, yes_ask, yes_mid, created_at)
               VALUES ('KXHIGHNY-T', 0.26, 0.30, 0.28, '2026-06-10 22:30:00')"""
        )
        # After close — must be ignored.
        conn.execute(
            """INSERT INTO price_snapshots (market_ticker, yes_bid, yes_ask, yes_mid, created_at)
               VALUES ('KXHIGHNY-T', 0.90, 0.94, 0.92, '2026-06-10 23:30:00')"""
        )
        conn.commit()
        conn.close()

        mark = close_mark_for("KXHIGHNY-T", "2026-06-10 23:00:00")
        self.assertIsNotNone(mark)
        self.assertAlmostEqual(mark["yes_mid"], 0.28)

    def test_close_mark_none_when_no_snapshots(self):
        from app.services.realtime import close_mark_for

        self.assertIsNone(close_mark_for("KXNOPE-T", "2026-06-10 23:00:00"))

    def test_settlement_fills_true_clv_when_snapshots_exist(self):
        from app.services.trade_lifecycle import _close_trade

        conn = self._conn()
        conn.execute(
            """INSERT INTO markets (ticker, title, close_time, result, market_price)
               VALUES ('KXHIGHCHI-T', 't', '2026-06-10 23:00:00', 'no', 0.30)"""
        )
        conn.execute(
            """INSERT INTO price_snapshots (market_ticker, yes_bid, yes_ask, yes_mid, created_at)
               VALUES ('KXHIGHCHI-T', 0.24, 0.28, 0.26, '2026-06-10 22:55:00')"""
        )
        conn.execute(
            """INSERT INTO trades (market_ticker, direction, entry_price, contracts, status)
               VALUES ('KXHIGHCHI-T', 'no', 0.31, 1, 'open')"""
        )
        trade_id = conn.execute("SELECT id FROM trades").fetchone()["id"]
        conn.commit()
        conn.close()

        _close_trade(trade_id, 0.0, "market_closed", settlement_result="no", refresh_learning=False)

        conn = self._conn()
        row = conn.execute("SELECT close_mark_yes, true_clv FROM trades WHERE id=?", (trade_id,)).fetchone()
        conn.close()
        self.assertAlmostEqual(row["close_mark_yes"], 0.26)
        # NO at yes 0.31, close mark 0.26 -> entry beat the close by 5c.
        self.assertAlmostEqual(row["true_clv"], 0.05)

    def test_settlement_leaves_true_clv_null_without_snapshots(self):
        from app.services.trade_lifecycle import _close_trade

        conn = self._conn()
        conn.execute(
            """INSERT INTO markets (ticker, title, close_time, result, market_price)
               VALUES ('KXHIGHLAX-T', 't', '2026-06-10 23:00:00', 'no', 0.30)"""
        )
        conn.execute(
            """INSERT INTO trades (market_ticker, direction, entry_price, contracts, status)
               VALUES ('KXHIGHLAX-T', 'no', 0.31, 1, 'open')"""
        )
        trade_id = conn.execute("SELECT id FROM trades").fetchone()["id"]
        conn.commit()
        conn.close()

        _close_trade(trade_id, 0.0, "market_closed", settlement_result="no", refresh_learning=False)

        conn = self._conn()
        row = conn.execute("SELECT close_mark_yes, true_clv FROM trades WHERE id=?", (trade_id,)).fetchone()
        conn.close()
        self.assertIsNone(row["close_mark_yes"])
        self.assertIsNone(row["true_clv"])


if __name__ == "__main__":
    unittest.main()
