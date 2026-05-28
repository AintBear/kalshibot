import os
import sqlite3
import tempfile
import unittest


class TestTradeLifecycle(unittest.TestCase):
    def setUp(self):
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.db_file.name
        os.environ["DB_PATH"] = self.db_path
        from app.database import init_db
        init_db()

    def tearDown(self):
        os.unlink(self.db_path)

    def _insert_market(self, ticker, status="open", price=0.50, result=None):
        close_time = None
        if status == "past_close":
            status = "open"
            close_time = "2000-01-01T00:00:00Z"
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """INSERT INTO markets
               (ticker, title, category, market_price, status, result, close_time)
               VALUES (?, ?, 'weather', ?, ?, ?, ?)
               ON CONFLICT(ticker) DO UPDATE SET
                 market_price=excluded.market_price,
                 status=excluded.status,
                 result=excluded.result,
                 close_time=excluded.close_time""",
            (ticker, ticker, price, status, result, close_time),
        )
        conn.commit()
        conn.close()

    def _insert_open_trade(self, ticker, direction="yes", entry_price=0.50, stop_loss=None, take_profit=None):
        conn = sqlite3.connect(self.db_path)
        cur = conn.execute(
            """INSERT INTO trades
               (market_ticker, direction, entry_price, contracts, paper, status,
                stop_loss_price, take_profit_price, entry_time)
               VALUES (?, ?, ?, 1, 1, 'open', ?, ?, datetime('now'))""",
            (ticker, direction, entry_price, stop_loss, take_profit),
        )
        trade_id = cur.lastrowid
        conn.commit()
        conn.close()
        return trade_id

    def test_market_resolved_closes_trade(self):
        self._insert_market("KXTEST-001", status="settled", price=0.0, result="yes")
        trade_id = self._insert_open_trade("KXTEST-001", direction="yes", entry_price=0.60)

        from app.services.trade_lifecycle import check_and_close_trades
        check_and_close_trades()

        conn = sqlite3.connect(self.db_path)
        row = conn.execute("SELECT status, exit_reason FROM trades WHERE id=?", (trade_id,)).fetchone()
        conn.close()
        self.assertEqual(row[0], "closed")
        self.assertEqual(row[1], "market_resolved")

    def test_closed_market_without_result_keeps_learning_pending(self):
        self._insert_market("KXTEST-NO-RESULT", status="closed", price=0.42, result=None)
        trade_id = self._insert_open_trade("KXTEST-NO-RESULT", direction="yes", entry_price=0.60)

        from app.services.trade_lifecycle import check_and_close_trades
        check_and_close_trades()

        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT status, exit_reason, exit_price, pnl, clv FROM trades WHERE id=?",
            (trade_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], "closed")
        self.assertEqual(row[1], "market_resolved")
        self.assertAlmostEqual(row[2], 0.42, places=4)
        self.assertIsNone(row[3])
        self.assertIsNone(row[4])

    def test_no_market_resolved_uses_yes_settlement_price(self):
        self._insert_market("KXTEST-NO-WIN", status="settled", price=0.0, result="no")
        trade_id = self._insert_open_trade("KXTEST-NO-WIN", direction="no", entry_price=0.60)

        from app.services.trade_lifecycle import check_and_close_trades
        check_and_close_trades()

        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT status, exit_price, pnl, clv FROM trades WHERE id=?",
            (trade_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], "closed")
        self.assertAlmostEqual(row[1], 0.0, places=4)
        self.assertAlmostEqual(row[2], 0.60, places=4)
        self.assertAlmostEqual(row[3], 0.60, places=4)

    def test_stop_loss_triggers_on_yes_direction(self):
        self._insert_market("KXTEST-002", status="open", price=0.30)
        trade_id = self._insert_open_trade("KXTEST-002", direction="yes", entry_price=0.50, stop_loss=0.35)

        from app.services.trade_lifecycle import check_and_close_trades
        check_and_close_trades()

        conn = sqlite3.connect(self.db_path)
        row = conn.execute("SELECT status, exit_reason FROM trades WHERE id=?", (trade_id,)).fetchone()
        conn.close()
        self.assertEqual(row[0], "closed")
        self.assertEqual(row[1], "stop_loss")

    def test_past_close_time_no_settlement_leaves_clv_null(self):
        # When close_time has passed but market has no result yet, CLV must be NULL
        # so it doesn't pollute avg_clv until we have actual settlement data.
        self._insert_market("KXTEST-PAST-CLOSE", status="past_close", price=0.70)
        trade_id = self._insert_open_trade("KXTEST-PAST-CLOSE", direction="yes", entry_price=0.50)

        from app.services.trade_lifecycle import check_and_close_trades
        check_and_close_trades()

        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT status, exit_reason, exit_price, clv FROM trades WHERE id=?",
            (trade_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], "closed")
        self.assertEqual(row[1], "market_closed")
        self.assertAlmostEqual(row[2], 0.70, places=4)
        self.assertIsNone(row[3])  # CLV unknown until settlement

    def test_past_close_time_with_settlement_sets_clv(self):
        # When market settled YES, exit_price=1.0 and CLV is computed correctly.
        self._insert_market("KXTEST-SETTLED", status="past_close", price=0.70, result="yes")
        trade_id = self._insert_open_trade("KXTEST-SETTLED", direction="yes", entry_price=0.50)

        from app.services.trade_lifecycle import check_and_close_trades
        check_and_close_trades()

        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT status, exit_reason, exit_price, clv FROM trades WHERE id=?",
            (trade_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], "closed")
        self.assertEqual(row[1], "market_closed")
        self.assertAlmostEqual(row[2], 1.0, places=4)
        self.assertAlmostEqual(row[3], 0.50, places=4)  # CLV = 1.0 - 0.50

    def test_expired_open_trade_settles_from_kalshi_result_api(self):
        from unittest.mock import patch

        self._insert_market("KXTEST-KALSHI-SETTLED", status="past_close", price=0.70)
        trade_id = self._insert_open_trade("KXTEST-KALSHI-SETTLED", direction="no", entry_price=0.60)

        with patch("app.services.kalshi_client.get_market", return_value={"result": "no", "status": "settled"}):
            from app.services.trade_lifecycle import settle_expired_open_trades
            result = settle_expired_open_trades()

        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            """SELECT status, exit_reason, exit_price, clv, pnl,
                      settlement_result, prediction_correct, settlement_pnl
                 FROM trades WHERE id=?""",
            (trade_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(result["settled"], 1)
        self.assertEqual(row[0], "closed")
        self.assertEqual(row[1], "market_closed")
        self.assertAlmostEqual(row[2], 0.0, places=4)
        self.assertAlmostEqual(row[3], 0.60, places=4)
        self.assertAlmostEqual(row[4], 0.60, places=4)
        self.assertEqual(row[5], "no")
        self.assertEqual(row[6], 1)
        self.assertAlmostEqual(row[7], 0.60, places=4)

    def test_settlement_cross_reference_ignores_paper_reset_rows(self):
        from unittest.mock import patch

        conn = sqlite3.connect(self.db_path)
        for ticker, reason in (
            ("KXTEST-RESET", "paper_reset"),
            ("KXTEST-NORMAL", "market_closed"),
        ):
            conn.execute(
                """INSERT INTO markets
                   (ticker, title, category, market_price, status, close_time, result)
                   VALUES (?, ?, 'weather', 0.0, 'settled', datetime('now','-1 hour'), 'yes')""",
                (ticker, ticker),
            )
            conn.execute(
                """INSERT INTO trades
                   (market_ticker, direction, entry_price, exit_price, contracts,
                    paper, status, exit_reason, pnl, clv, entry_time, exit_time)
                   VALUES (?, 'yes', 0.40, 1.0, 1, 1, 'closed', ?, 0.60, 0.60,
                           datetime('now','-2 hours'), datetime('now','-1 hour'))""",
                (ticker, reason),
            )
        conn.commit()
        conn.close()

        with patch("app.services.kalshi_client.get_market", return_value={"result": "yes"}):
            from app.services.trade_lifecycle import backfill_settlement_cross_reference
            result = backfill_settlement_cross_reference()

        conn = sqlite3.connect(self.db_path)
        rows = dict(conn.execute(
            "SELECT market_ticker, prediction_correct FROM trades"
        ).fetchall())
        conn.close()

        self.assertEqual(result["cross_referenced"], 1)
        self.assertIsNone(rows["KXTEST-RESET"])
        self.assertEqual(rows["KXTEST-NORMAL"], 1)

    def test_open_trade_not_closed_prematurely(self):
        self._insert_market("KXTEST-003", status="open", price=0.55)
        trade_id = self._insert_open_trade("KXTEST-003", direction="yes", entry_price=0.50, stop_loss=0.35)

        from app.services.trade_lifecycle import check_and_close_trades
        check_and_close_trades()

        conn = sqlite3.connect(self.db_path)
        row = conn.execute("SELECT status FROM trades WHERE id=?", (trade_id,)).fetchone()
        conn.close()
        self.assertEqual(row[0], "open")

    def test_take_profit_triggers_on_yes_direction(self):
        self._insert_market("KXTEST-TP-YES", status="open", price=0.62)
        trade_id = self._insert_open_trade("KXTEST-TP-YES", direction="yes", entry_price=0.40, take_profit=0.60)

        from app.services.trade_lifecycle import check_and_close_trades
        check_and_close_trades()

        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT status, exit_reason, exit_price, clv FROM trades WHERE id=?",
            (trade_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], "closed")
        self.assertEqual(row[1], "take_profit")
        self.assertAlmostEqual(row[2], 0.60, places=4)
        self.assertAlmostEqual(row[3], 0.20, places=4)

    def test_live_price_monitor_triggers_no_take_profit(self):
        from unittest.mock import patch

        trade_id = self._insert_open_trade("KXTEST-TP-NO", direction="no", entry_price=0.60, take_profit=0.25)

        with patch("app.services.trade_lifecycle._refresh_trade_quote", return_value={"yes_bid": 0.20, "market_status": "open"}):
            from app.services.trade_lifecycle import check_live_prices
            result = check_live_prices()

        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT status, exit_reason, exit_price, clv FROM trades WHERE id=?",
            (trade_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(result["closed"], 1)
        self.assertEqual(row[0], "closed")
        self.assertEqual(row[1], "take_profit")
        self.assertAlmostEqual(row[2], 0.25, places=4)
        self.assertAlmostEqual(row[3], 0.35, places=4)

    def test_live_price_monitor_uses_yes_ask_for_no_exit_mark(self):
        from unittest.mock import patch

        trade_id = self._insert_open_trade("KXTEST-NO-ASK", direction="no", entry_price=0.60, take_profit=0.25)

        with patch("app.services.trade_lifecycle._refresh_trade_quote", return_value={"yes_bid": 0.20, "yes_ask": 0.30, "market_status": "open"}):
            from app.services.trade_lifecycle import check_live_prices
            result = check_live_prices()

        conn = sqlite3.connect(self.db_path)
        row = conn.execute("SELECT status FROM trades WHERE id=?", (trade_id,)).fetchone()
        conn.close()
        self.assertEqual(result["closed"], 0)
        self.assertEqual(row[0], "open")


class TestOrderManager(unittest.TestCase):
    def setUp(self):
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.db_file.name
        os.environ["DB_PATH"] = self.db_path
        from app.database import init_db
        init_db()

    def tearDown(self):
        os.unlink(self.db_path)

    def test_paper_close_sets_clv(self):
        from app.services.order_manager import place_order, close_order
        result = place_order("KXTEST-CLV", "yes", 0.50, contracts=1)
        trade_id = result["trade_id"]
        close_result = close_order(trade_id, 0.65, "settlement")
        self.assertAlmostEqual(close_result["clv"], 0.15, places=4)

    def test_paper_close_pnl_correct_yes(self):
        from app.services.order_manager import place_order, close_order
        result = place_order("KXTEST-PNL", "yes", 0.40, contracts=2)
        trade_id = result["trade_id"]
        close_result = close_order(trade_id, 0.70, "settlement")
        self.assertAlmostEqual(close_result["pnl"], 0.60, places=4)

    def test_paper_close_pnl_correct_no(self):
        from app.services.order_manager import place_order, close_order
        result = place_order("KXTEST-PNL-NO", "no", 0.60, contracts=1)
        trade_id = result["trade_id"]
        close_result = close_order(trade_id, 0.30, "settlement")
        self.assertAlmostEqual(close_result["pnl"], 0.30, places=4)
        self.assertAlmostEqual(close_result["clv"], 0.30, places=4)

    def test_no_stop_loss_moves_against_yes_price(self):
        from app.services.order_manager import place_order
        result = place_order("KXTEST-NO-STOP", "no", 0.60, contracts=1, stop_loss_pct=0.50)
        conn = sqlite3.connect(self.db_path)
        stop_loss = conn.execute("SELECT stop_loss_price FROM trades WHERE id=?", (result["trade_id"],)).fetchone()[0]
        conn.close()
        self.assertAlmostEqual(stop_loss, 0.80, places=4)



class TestStaleAlertCleanup(unittest.TestCase):
    def setUp(self):
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.db_file.name
        os.environ["DB_PATH"] = self.db_path
        from app.database import init_db
        init_db()

    def tearDown(self):
        os.unlink(self.db_path)

    def _insert_alert(self, ticker, price, updated_at="datetime('now')", close_time="datetime('now','+1 hour')"):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            f"""INSERT INTO markets
                (ticker, title, category, market_price, status, close_time)
                VALUES (?, ?, 'weather', ?, 'open', {close_time})""",
            (ticker, ticker, price),
        )
        conn.execute(
            f"""INSERT INTO alerts
                (market_ticker, status, edge, direction, market_price, updated_at)
                VALUES (?, 'pending', 0.50, 'yes', ?, {updated_at})""",
            (ticker, price),
        )
        conn.commit()
        conn.close()

    def test_extreme_price_alert_is_not_expired_by_price(self):
        self._insert_alert("KXTEST-EXTREME", 0.01)

        from app.services.scheduler import _stale_alert_cleanup
        _stale_alert_cleanup()

        conn = sqlite3.connect(self.db_path)
        status = conn.execute("SELECT status FROM alerts WHERE market_ticker='KXTEST-EXTREME'").fetchone()[0]
        conn.close()
        self.assertEqual(status, "pending")

    def test_old_alert_is_expired(self):
        self._insert_alert("KXTEST-OLD", 0.50, updated_at="datetime('now','-2 hours')")

        from app.services.scheduler import _stale_alert_cleanup
        _stale_alert_cleanup()

        conn = sqlite3.connect(self.db_path)
        status = conn.execute("SELECT status FROM alerts WHERE market_ticker='KXTEST-OLD'").fetchone()[0]
        conn.close()
        self.assertEqual(status, "expired")

    def test_closed_market_alert_cleanup_expires_only_old_closed_alerts(self):
        conn = sqlite3.connect(self.db_path)
        for ticker, created_at, close_time in (
            ("KXTEST-OLD-CLOSED", "datetime('now','-49 hours')", "datetime('now','-1 hour')"),
            ("KXTEST-YOUNG-CLOSED", "datetime('now','-2 hours')", "datetime('now','-1 hour')"),
            ("KXTEST-OLD-OPEN", "datetime('now','-49 hours')", "datetime('now','+1 hour')"),
        ):
            conn.execute(
                f"""INSERT INTO markets
                    (ticker, title, category, market_price, status, close_time)
                    VALUES (?, ?, 'weather', 0.50, 'open', {close_time})""",
                (ticker, ticker),
            )
            conn.execute(
                f"""INSERT INTO alerts
                    (market_ticker, status, edge, direction, market_price, created_at, updated_at)
                    VALUES (?, 'active', 0.10, 'yes', 0.50, {created_at}, {created_at})""",
                (ticker,),
            )
        conn.commit()
        conn.close()

        from app.services.scheduler import _expire_closed_market_alerts
        result = _expire_closed_market_alerts()

        conn = sqlite3.connect(self.db_path)
        rows = dict(conn.execute("SELECT market_ticker, status FROM alerts").fetchall())
        conn.close()
        self.assertEqual(result["expired"], 1)
        self.assertEqual(rows["KXTEST-OLD-CLOSED"], "expired")
        self.assertEqual(rows["KXTEST-YOUNG-CLOSED"], "active")
        self.assertEqual(rows["KXTEST-OLD-OPEN"], "active")

    def test_model_history_cleanup_deletes_only_old_rows(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """INSERT INTO model_outputs
               (market_ticker, category, model_prob, created_at)
               VALUES ('KXTEST-OLD', 'weather', 0.50, datetime('now','-8 days'))"""
        )
        conn.execute(
            """INSERT INTO model_outputs
               (market_ticker, category, model_prob, created_at)
               VALUES ('KXTEST-NEW', 'weather', 0.50, datetime('now'))"""
        )
        conn.execute(
            """INSERT INTO forecast_snapshots
               (market_ticker, snapshot_date, model_prob, created_at)
               VALUES ('KXTEST-OLD', date('now','-8 days'), 0.50, datetime('now','-8 days'))"""
        )
        conn.execute(
            """INSERT INTO forecast_snapshots
               (market_ticker, snapshot_date, model_prob, created_at)
               VALUES ('KXTEST-NEW', date('now'), 0.50, datetime('now'))"""
        )
        conn.commit()
        conn.close()

        from app.services.scheduler import _cleanup_old_model_history
        result = _cleanup_old_model_history()

        conn = sqlite3.connect(self.db_path)
        model_count = conn.execute("SELECT COUNT(*) FROM model_outputs").fetchone()[0]
        snapshot_count = conn.execute("SELECT COUNT(*) FROM forecast_snapshots").fetchone()[0]
        conn.close()
        self.assertEqual(result["model_outputs_deleted"], 1)
        self.assertEqual(result["forecast_snapshots_deleted"], 1)
        self.assertEqual(model_count, 1)
        self.assertEqual(snapshot_count, 1)

    def test_stale_open_trade_is_expired_with_zero_pnl(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """INSERT INTO markets
               (ticker, title, category, market_price, status, close_time, result)
               VALUES ('KXTEST-STALE-TRADE', 'Stale trade', 'weather', 0.50,
                       'closed', datetime('now','-4 days'), NULL)"""
        )
        cur = conn.execute(
            """INSERT INTO trades
               (market_ticker, direction, entry_price, contracts, paper, status, entry_time)
               VALUES ('KXTEST-STALE-TRADE', 'yes', 0.42, 1, 1, 'open', datetime('now','-5 days'))"""
        )
        trade_id = cur.lastrowid
        conn.commit()
        conn.close()

        from app.services.scheduler import _expire_stale_open_trades
        result = _expire_stale_open_trades()

        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT status, exit_reason, exit_price, pnl, clv FROM trades WHERE id=?",
            (trade_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(result["expired"], 1)
        self.assertEqual(row[0], "expired")
        self.assertEqual(row[1], "market_expired")
        self.assertAlmostEqual(row[2], 0.42, places=4)
        self.assertAlmostEqual(row[3], 0.0, places=4)
        self.assertIsNone(row[4])
