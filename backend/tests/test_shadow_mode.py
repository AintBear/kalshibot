import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock


class _ShadowCase(unittest.TestCase):
    def setUp(self):
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_file.close()
        os.environ["DB_PATH"] = self.db_file.name
        from app.database import init_db
        init_db()

        self.config_file = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
        json.dump({
            "paper_trading": False,
            "live_shadow_mode": True,
            "kill_switch": False,
            "live_max_contracts_per_trade": 2,
            "live_max_total_exposure": 25.0,
            "kalshi_key_id": "k",
            "kalshi_private_key_path": "p",
        }, self.config_file)
        self.config_file.close()
        import app.config as cfg
        self._old = cfg.CONFIG_PATH
        cfg.CONFIG_PATH = self.config_file.name

    def tearDown(self):
        import app.config as cfg
        cfg.CONFIG_PATH = self._old
        os.unlink(self.db_file.name)
        os.unlink(self.config_file.name)

    def conn(self):
        c = sqlite3.connect(self.db_file.name)
        c.row_factory = sqlite3.Row
        return c


class TestShadowPlacement(_ShadowCase):
    def test_live_mode_with_shadow_logs_instead_of_submitting(self):
        with mock.patch("app.services.kalshi_client.get_balance", return_value={"balance": 100.0}), \
             mock.patch("app.services.order_manager._submit_to_kalshi") as real_submit:
            from app.services.order_manager import place_order
            result = place_order("KXHIGHNY-26JUN12-B81.5", "no", 0.30, contracts=1)

        real_submit.assert_not_called()
        self.assertTrue(result["kalshi_order_id"].startswith("SHADOW-"))

        c = self.conn()
        trade = c.execute("SELECT paper, status FROM trades").fetchone()
        actions = [r["action"] for r in c.execute("SELECT action FROM audit_log").fetchall()]
        c.close()
        self.assertEqual(trade["paper"], 0)       # full live path, not paper
        self.assertEqual(trade["status"], "open")
        self.assertIn("shadow_order_logged", actions)
        self.assertIn("live_entry_submitted", actions)

    def test_pre_trade_checks_still_enforced_in_shadow(self):
        with mock.patch("app.services.kalshi_client.get_balance", return_value={"balance": 100.0}):
            from app.services.order_manager import place_order
            with self.assertRaises(RuntimeError):
                place_order("KXHIGHNY-26JUN12-B81.5", "no", 0.30, contracts=99)

    def test_shadow_cancel_always_succeeds_without_api(self):
        with mock.patch("app.services.kalshi_client.kalshi_request") as api:
            from app.services.order_manager import cancel_kalshi_order
            self.assertTrue(cancel_kalshi_order("SHADOW-sib-1-entry-0"))
        api.assert_not_called()

    def test_reconcile_skipped_in_shadow(self):
        from app.services.order_manager import reconcile_with_kalshi

        result = reconcile_with_kalshi()
        self.assertTrue(result.get("skipped"))


class TestShadowFills(_ShadowCase):
    def _seed_shadow_order(self, purpose="entry", order_yes=0.30, direction="no",
                           exit_reason=None):
        c = self.conn()
        c.execute(
            """INSERT INTO trades (market_ticker, direction, entry_price, contracts, status, paper)
               VALUES ('KXHIGHNY-26JUN12-B81.5', ?, 0.30, 1, 'open', 0)""",
            (direction,),
        )
        tid = c.execute("SELECT last_insert_rowid() i").fetchone()["i"]
        c.execute(
            """INSERT INTO orders (trade_id, market_ticker, side, price, contracts, status,
                                   order_type, kalshi_order_id, purpose, exit_reason, paper)
               VALUES (?, 'KXHIGHNY-26JUN12-B81.5', ?, ?, 1, 'submitted', 'limit',
                       'SHADOW-sib-x', ?, ?, 0)""",
            (tid, direction, order_yes, purpose, exit_reason),
        )
        c.commit()
        c.close()
        return tid

    def test_entry_fills_when_market_trades_through(self):
        tid = self._seed_shadow_order(order_yes=0.30)  # NO resting at side 0.70
        # yes_bid 0.28 / yes_ask 0.30 -> NO side ask = 0.72... not filled.
        # yes 0.32/0.34 -> NO side ask = 1-0.32 = 0.68 <= 0.70 -> filled.
        quote = {"yes_bid": 0.32, "yes_ask": 0.34}
        with mock.patch("app.services.order_manager._current_quote", return_value=quote):
            from app.services.order_manager import monitor_live_orders
            result = monitor_live_orders()
        self.assertEqual(result["filled"], 1)
        c = self.conn()
        order = c.execute("SELECT status FROM orders WHERE trade_id=?", (tid,)).fetchone()
        trade = c.execute("SELECT status FROM trades WHERE id=?", (tid,)).fetchone()
        c.close()
        self.assertEqual(order["status"], "filled")
        self.assertEqual(trade["status"], "open")  # entry fill keeps position open

    def test_entry_does_not_fill_away_from_market(self):
        self._seed_shadow_order(order_yes=0.30)  # NO side 0.70
        quote = {"yes_bid": 0.26, "yes_ask": 0.28}  # NO side ask 0.74 > 0.70
        with mock.patch("app.services.order_manager._current_quote", return_value=quote):
            from app.services.order_manager import monitor_live_orders
            result = monitor_live_orders()
        self.assertEqual(result["filled"], 0)

    def test_exit_fill_closes_trade(self):
        tid = self._seed_shadow_order(purpose="exit", order_yes=0.26, exit_reason="take_profit")
        # Selling NO at side 0.74: fills when NO side bid >= 0.74 -> yes_ask <= 0.26.
        quote = {"yes_bid": 0.22, "yes_ask": 0.25}
        with mock.patch("app.services.order_manager._current_quote", return_value=quote), \
             mock.patch("app.services.adaptive_policy.rebuild_snapshots"):
            from app.services.order_manager import monitor_live_orders
            result = monitor_live_orders()
        self.assertEqual(result["filled"], 1)
        c = self.conn()
        trade = c.execute("SELECT status, exit_reason, pnl FROM trades WHERE id=?", (tid,)).fetchone()
        c.close()
        self.assertEqual(trade["status"], "closed")
        self.assertEqual(trade["exit_reason"], "take_profit")
        # NO entry 0.30 yes -> exit 0.26 yes: pnl = (0.30-0.26)*1 = 0.04
        self.assertAlmostEqual(trade["pnl"], 0.04)


if __name__ == "__main__":
    unittest.main()
