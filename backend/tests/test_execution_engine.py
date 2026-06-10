import os
import sqlite3
import tempfile
import unittest
from unittest import mock


class TestRequoteDecision(unittest.TestCase):
    """Pure decision logic for working passive entry orders (side coords)."""

    def _decide(self, **kw):
        from app.services.order_manager import requote_decision

        defaults = dict(
            resting_side_price=0.70,
            side_bid=0.70,
            side_ask=0.74,
            minutes_to_close=600.0,
            requote_count=0,
            max_chase_side_price=0.73,
            cross_minutes=45.0,
            max_requotes=10,
        )
        defaults.update(kw)
        return requote_decision(**defaults)

    def test_holds_while_at_bid(self):
        self.assertEqual(self._decide()["action"], "hold")

    def test_requotes_when_outbid(self):
        d = self._decide(side_bid=0.71)
        self.assertEqual(d["action"], "requote")
        self.assertAlmostEqual(d["price"], 0.72)

    def test_requote_capped_at_chase_limit(self):
        d = self._decide(side_bid=0.74, side_ask=0.78)
        self.assertEqual(d["action"], "requote")
        self.assertAlmostEqual(d["price"], 0.73)  # capped, not bid+1c=0.75

    def test_holds_when_chase_cap_already_reached(self):
        d = self._decide(resting_side_price=0.73, side_bid=0.75, side_ask=0.79)
        self.assertEqual(d["action"], "hold")

    def test_never_crosses_ask_when_requoting(self):
        d = self._decide(side_bid=0.72, side_ask=0.725, max_chase_side_price=0.99)
        self.assertEqual(d["action"], "requote")
        self.assertLess(d["price"], 0.725)

    def test_crosses_near_close_when_affordable(self):
        d = self._decide(minutes_to_close=30.0, side_ask=0.72)
        self.assertEqual(d["action"], "cross")
        self.assertAlmostEqual(d["price"], 0.72)

    def test_abandons_near_close_when_ask_beyond_cap(self):
        d = self._decide(minutes_to_close=30.0, side_ask=0.80)
        self.assertEqual(d["action"], "abandon")

    def test_abandons_when_requote_budget_exhausted(self):
        d = self._decide(side_bid=0.71, requote_count=10)
        self.assertEqual(d["action"], "abandon")

    def test_holds_without_quote(self):
        d = self._decide(side_bid=None, side_ask=None)
        self.assertEqual(d["action"], "hold")


class TestClientOrderId(unittest.TestCase):
    def test_deterministic(self):
        from app.services.order_manager import make_client_order_id

        self.assertEqual(make_client_order_id(42, "entry", 0), "sib-42-entry-0")
        self.assertEqual(make_client_order_id(42, "entry", 0), make_client_order_id(42, "entry", 0))
        self.assertNotEqual(make_client_order_id(42, "entry", 1), make_client_order_id(42, "entry", 0))
        self.assertNotEqual(make_client_order_id(42, "exit", 0), make_client_order_id(42, "entry", 0))


class _DbCase(unittest.TestCase):
    def setUp(self):
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_file.close()
        os.environ["DB_PATH"] = self.db_file.name
        from app.database import init_db
        init_db()

    def tearDown(self):
        os.unlink(self.db_file.name)

    def conn(self):
        c = sqlite3.connect(self.db_file.name)
        c.row_factory = sqlite3.Row
        return c


class TestLiveExitFlow(_DbCase):
    def _open_live_trade(self, **kw):
        c = self.conn()
        c.execute(
            """INSERT INTO trades (market_ticker, direction, entry_price, contracts,
                                   status, paper, stop_loss_price, take_profit_price)
               VALUES (?, ?, ?, ?, 'open', 0, ?, ?)""",
            (kw.get("ticker", "KXHIGHCHI-26JUN10-B90.5"), kw.get("direction", "no"),
             kw.get("entry_price", 0.30), kw.get("contracts", 2),
             kw.get("stop_loss_price"), kw.get("take_profit_price")),
        )
        tid = c.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
        c.commit()
        c.close()
        return tid

    def test_submit_live_exit_posts_sell_and_keeps_trade_open(self):
        tid = self._open_live_trade()
        quote = {"yes_bid": 0.24, "yes_ask": 0.28, "yes_mid": 0.26}

        with mock.patch("app.services.order_manager._current_quote", return_value=quote), \
             mock.patch("app.services.order_manager._shadow_mode", return_value=False), \
             mock.patch("app.services.order_manager._submit_to_kalshi", return_value="K123") as sub:
            from app.services.order_manager import submit_live_exit
            result = submit_live_exit(tid, "take_profit", cross=False)

        self.assertEqual(result["kalshi_order_id"], "K123")
        # NO position: side_bid = 1-yes_ask = 0.72, side_ask = 1-yes_bid = 0.76.
        # Passive exit = max(bid+1c, ask-1c) = 0.75 side -> 0.25 yes.
        self.assertAlmostEqual(result["price_yes"], 0.25)
        sub.assert_called_once()
        self.assertEqual(sub.call_args.kwargs.get("action"), "sell")
        self.assertEqual(sub.call_args.kwargs.get("client_order_id"), f"sib-{tid}-exit-0")

        c = self.conn()
        trade = c.execute("SELECT status FROM trades WHERE id=?", (tid,)).fetchone()
        order = c.execute("SELECT * FROM orders WHERE trade_id=?", (tid,)).fetchone()
        audit_row = c.execute("SELECT * FROM audit_log WHERE action='live_exit_submitted'").fetchone()
        c.close()
        self.assertEqual(trade["status"], "open")  # closes only on confirmed fill
        self.assertEqual(order["purpose"], "exit")
        self.assertEqual(order["exit_reason"], "take_profit")
        self.assertIsNotNone(audit_row)

    def test_submit_live_exit_is_idempotent_while_working(self):
        tid = self._open_live_trade()
        quote = {"yes_bid": 0.24, "yes_ask": 0.28}
        with mock.patch("app.services.order_manager._current_quote", return_value=quote), \
             mock.patch("app.services.order_manager._shadow_mode", return_value=False), \
             mock.patch("app.services.order_manager._submit_to_kalshi", return_value="K123"):
            from app.services.order_manager import submit_live_exit
            first = submit_live_exit(tid, "stop_loss", cross=True)
            second = submit_live_exit(tid, "stop_loss", cross=True)
        self.assertNotIn("error", first)
        self.assertTrue(second.get("skipped"))

    def test_stop_loss_cross_exits_at_side_bid(self):
        tid = self._open_live_trade()
        quote = {"yes_bid": 0.40, "yes_ask": 0.44}
        with mock.patch("app.services.order_manager._current_quote", return_value=quote), \
             mock.patch("app.services.order_manager._shadow_mode", return_value=False), \
             mock.patch("app.services.order_manager._submit_to_kalshi", return_value="K9"):
            from app.services.order_manager import submit_live_exit
            result = submit_live_exit(tid, "stop_loss", cross=True)
        # NO side bid = 1-yes_ask = 0.56 -> yes 0.44.
        self.assertAlmostEqual(result["price_yes"], 0.44)

    def test_check_live_trade_exits_triggers_on_stop(self):
        tid = self._open_live_trade(stop_loss_price=0.40)  # NO trade: stop when yes >= 0.40
        quote = {"yes_bid": 0.45, "yes_ask": 0.49}
        with mock.patch("app.services.order_manager._current_quote", return_value=quote), \
             mock.patch("app.services.order_manager._shadow_mode", return_value=False), \
             mock.patch("app.services.order_manager._submit_to_kalshi", return_value="K77") as sub:
            from app.services.trade_lifecycle import check_live_trade_exits
            result = check_live_trade_exits()
        self.assertEqual(result["exits_submitted"], 1)
        self.assertEqual(sub.call_args.kwargs.get("action"), "sell")

    def test_check_live_trade_exits_quiet_when_inside_thresholds(self):
        self._open_live_trade(stop_loss_price=0.60, take_profit_price=0.05)
        quote = {"yes_bid": 0.28, "yes_ask": 0.32}
        with mock.patch("app.services.order_manager._current_quote", return_value=quote):
            from app.services.trade_lifecycle import check_live_trade_exits
            result = check_live_trade_exits()
        self.assertEqual(result["exits_submitted"], 0)


class TestExitFillClosesTrade(_DbCase):
    def test_exit_fill_closes_trade_at_fill_price(self):
        c = self.conn()
        c.execute(
            """INSERT INTO trades (market_ticker, direction, entry_price, contracts, status, paper)
               VALUES ('KXHIGHNY-26JUN10-B81.5', 'no', 0.30, 2, 'open', 0)"""
        )
        tid = c.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
        c.execute(
            """INSERT INTO orders (trade_id, market_ticker, side, price, contracts, status,
                                   order_type, kalshi_order_id, purpose, exit_reason, paper)
               VALUES (?, 'KXHIGHNY-26JUN10-B81.5', 'no', 0.25, 2, 'submitted',
                       'limit', 'K555', 'exit', 'take_profit', 0)""",
            (tid,),
        )
        c.commit()
        c.close()

        class FakeResponse:
            ok = True
            def json(self):
                return {"order": {"status": "filled", "count_filled": 2, "no_price": 74}}

        settings = {"kalshi_key_id": "k", "kalshi_private_key_path": "p"}
        with mock.patch("app.services.kalshi_client.kalshi_request", return_value=FakeResponse()), \
             mock.patch("app.services.order_manager._get_settings", return_value=settings), \
             mock.patch("app.services.adaptive_policy.rebuild_snapshots"):
            from app.services.order_manager import monitor_live_orders
            result = monitor_live_orders()

        self.assertEqual(result["filled"], 1)
        c = self.conn()
        trade = c.execute("SELECT * FROM trades WHERE id=?", (tid,)).fetchone()
        order = c.execute("SELECT status FROM orders WHERE trade_id=?", (tid,)).fetchone()
        c.close()
        self.assertEqual(trade["status"], "closed")
        self.assertEqual(trade["exit_reason"], "take_profit")
        # no_price 74c -> yes exit 0.26; NO entered at yes 0.30 -> pnl (0.30-0.26)*2.
        self.assertAlmostEqual(trade["exit_price"], 0.26)
        self.assertAlmostEqual(trade["pnl"], 0.08)
        self.assertEqual(order["status"], "filled")


class TestManageWorkingOrders(_DbCase):
    def _working_entry(self, price_yes=0.30, requotes=0):
        c = self.conn()
        c.execute(
            """INSERT INTO trades (market_ticker, direction, entry_price, contracts, status, paper)
               VALUES ('KXHIGHCHI-26JUN10-B90.5', 'no', ?, 1, 'open', 0)""",
            (price_yes,),
        )
        tid = c.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
        c.execute(
            """INSERT INTO orders (trade_id, market_ticker, side, price, contracts, status,
                                   order_type, kalshi_order_id, client_order_id, purpose,
                                   requote_count, paper)
               VALUES (?, 'KXHIGHCHI-26JUN10-B90.5', 'no', ?, 1, 'submitted', 'limit',
                       'K100', ?, 'entry', ?, 0)""",
            (tid, price_yes, f"sib-{tid}-entry-{requotes}", requotes),
        )
        c.commit()
        c.close()
        return tid

    def test_paper_mode_is_inert(self):
        with mock.patch("app.services.order_manager._get_settings",
                        return_value={"paper_trading": True}):
            from app.services.order_manager import manage_working_orders
            result = manage_working_orders()
        self.assertTrue(result.get("skipped"))

    def test_outbid_order_is_cancelled_and_reposted(self):
        tid = self._working_entry(price_yes=0.30)  # NO side resting at 0.70
        # Market moved: yes 0.27/0.29 -> NO side bid 0.71, ask 0.73. We're outbid.
        quote = {"yes_bid": 0.27, "yes_ask": 0.29}
        settings = {"paper_trading": False, "live_shadow_mode": False, "kalshi_key_id": "k",
                    "kalshi_private_key_path": "p", "live_max_chase_cents": 3,
                    "live_cross_minutes_to_close": 45, "live_max_requotes_per_order": 10}

        with mock.patch("app.services.order_manager._get_settings", return_value=settings), \
             mock.patch("app.services.order_manager._current_quote", return_value=quote), \
             mock.patch("app.services.order_manager._minutes_to_close_from_markets", return_value=600.0), \
             mock.patch("app.services.order_manager.cancel_kalshi_order", return_value=True) as cancel, \
             mock.patch("app.services.order_manager._submit_to_kalshi", return_value="K101") as sub:
            from app.services.order_manager import manage_working_orders
            result = manage_working_orders()

        self.assertEqual(result["requoted"], 1)
        cancel.assert_called_once()
        # New NO side price = bid+1c = 0.72 -> yes 0.28.
        submitted_yes = sub.call_args.args[2]
        self.assertAlmostEqual(submitted_yes, 0.28)
        self.assertEqual(sub.call_args.kwargs.get("client_order_id"), f"sib-{tid}-entry-1")

        c = self.conn()
        orders = c.execute(
            "SELECT status, price, requote_count FROM orders WHERE trade_id=? ORDER BY id", (tid,)
        ).fetchall()
        trade = c.execute("SELECT entry_price FROM trades WHERE id=?", (tid,)).fetchone()
        c.close()
        self.assertEqual(orders[0]["status"], "replaced")
        self.assertEqual(orders[1]["requote_count"], 1)
        self.assertAlmostEqual(orders[1]["price"], 0.28)
        self.assertAlmostEqual(trade["entry_price"], 0.28)

    def test_crosses_near_close(self):
        self._working_entry(price_yes=0.30)
        quote = {"yes_bid": 0.27, "yes_ask": 0.29}  # NO ask = 0.73, within 3c chase of 0.70
        settings = {"paper_trading": False, "live_shadow_mode": False, "kalshi_key_id": "k",
                    "kalshi_private_key_path": "p", "live_max_chase_cents": 3,
                    "live_cross_minutes_to_close": 45, "live_max_requotes_per_order": 10}
        with mock.patch("app.services.order_manager._get_settings", return_value=settings), \
             mock.patch("app.services.order_manager._current_quote", return_value=quote), \
             mock.patch("app.services.order_manager._minutes_to_close_from_markets", return_value=20.0), \
             mock.patch("app.services.order_manager.cancel_kalshi_order", return_value=True), \
             mock.patch("app.services.order_manager._submit_to_kalshi", return_value="K102") as sub:
            from app.services.order_manager import manage_working_orders
            result = manage_working_orders()
        self.assertEqual(result["crossed"], 1)
        # Cross at NO ask 0.73 -> yes 0.27.
        self.assertAlmostEqual(sub.call_args.args[2], 0.27)

    def test_abandons_when_market_runs_away_near_close(self):
        tid = self._working_entry(price_yes=0.30)
        quote = {"yes_bid": 0.20, "yes_ask": 0.22}  # NO ask 0.80 > chase cap 0.73
        settings = {"paper_trading": False, "live_shadow_mode": False, "kalshi_key_id": "k",
                    "kalshi_private_key_path": "p", "live_max_chase_cents": 3,
                    "live_cross_minutes_to_close": 45, "live_max_requotes_per_order": 10}
        with mock.patch("app.services.order_manager._get_settings", return_value=settings), \
             mock.patch("app.services.order_manager._current_quote", return_value=quote), \
             mock.patch("app.services.order_manager._minutes_to_close_from_markets", return_value=20.0), \
             mock.patch("app.services.order_manager.cancel_kalshi_order", return_value=True), \
             mock.patch("app.services.order_manager._submit_to_kalshi") as sub:
            from app.services.order_manager import manage_working_orders
            result = manage_working_orders()
        self.assertEqual(result["abandoned"], 1)
        sub.assert_not_called()
        c = self.conn()
        trade = c.execute("SELECT status FROM trades WHERE id=?", (tid,)).fetchone()
        c.close()
        self.assertEqual(trade["status"], "cancelled")


class TestReconcile(_DbCase):
    def test_reconcile_flags_mismatch(self):
        c = self.conn()
        c.execute(
            """INSERT INTO trades (market_ticker, direction, entry_price, contracts, status, paper)
               VALUES ('KXHIGHNY-26JUN10-B81.5', 'no', 0.30, 3, 'open', 0)"""
        )
        c.commit()
        c.close()

        class FakeResponse:
            ok = True
            status_code = 200
            def json(self):
                # Kalshi says we hold 1 NO contract (position=-1); DB says 3.
                return {"market_positions": [{"ticker": "KXHIGHNY-26JUN10-B81.5", "position": -1}]}

        with mock.patch("app.services.kalshi_client.kalshi_request", return_value=FakeResponse()), \
             mock.patch("app.services.order_manager._get_settings",
                        return_value={"live_shadow_mode": False}):
            from app.services.order_manager import reconcile_with_kalshi
            result = reconcile_with_kalshi()

        self.assertEqual(len(result["mismatches"]), 1)
        m = result["mismatches"][0]
        self.assertEqual(m["db_contracts"], 3)
        self.assertEqual(m["kalshi_contracts"], 1)
        c = self.conn()
        audit_row = c.execute("SELECT * FROM audit_log WHERE action='reconcile_mismatch'").fetchone()
        c.close()
        self.assertIsNotNone(audit_row)

    def test_reconcile_clean_when_matching(self):
        c = self.conn()
        c.execute(
            """INSERT INTO trades (market_ticker, direction, entry_price, contracts, status, paper)
               VALUES ('KXHIGHNY-26JUN10-B81.5', 'no', 0.30, 3, 'open', 0)"""
        )
        c.commit()
        c.close()

        class FakeResponse:
            ok = True
            status_code = 200
            def json(self):
                return {"market_positions": [{"ticker": "KXHIGHNY-26JUN10-B81.5", "position": -3}]}

        with mock.patch("app.services.kalshi_client.kalshi_request", return_value=FakeResponse()), \
             mock.patch("app.services.order_manager._get_settings",
                        return_value={"live_shadow_mode": False}):
            from app.services.order_manager import reconcile_with_kalshi
            result = reconcile_with_kalshi()
        self.assertEqual(result["mismatches"], [])


if __name__ == "__main__":
    unittest.main()
