import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock


class _RiskCase(unittest.TestCase):
    def setUp(self):
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_file.close()
        os.environ["DB_PATH"] = self.db_file.name
        from app.database import init_db
        init_db()

        self.config_file = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
        json.dump({"paper_trading": False, "kill_switch": False}, self.config_file)
        self.config_file.close()
        import app.config as cfg
        self._old_path = cfg.CONFIG_PATH
        cfg.CONFIG_PATH = self.config_file.name

    def tearDown(self):
        import app.config as cfg
        cfg.CONFIG_PATH = self._old_path
        os.unlink(self.db_file.name)
        os.unlink(self.config_file.name)

    def conn(self):
        c = sqlite3.connect(self.db_file.name)
        c.row_factory = sqlite3.Row
        return c

    def _open_live_trade(self, ticker="KXHIGHNY-26JUN10-B81.5", direction="no",
                         entry=0.30, contracts=2, status="open", pnl=None, exit_time=None):
        c = self.conn()
        c.execute(
            """INSERT INTO trades (market_ticker, direction, entry_price, contracts,
                                   status, paper, pnl, exit_time)
               VALUES (?, ?, ?, ?, ?, 0, ?, ?)""",
            (ticker, direction, entry, contracts, status, pnl, exit_time),
        )
        c.commit()
        c.close()


class TestKillSwitch(_RiskCase):
    def test_activate_blocks_and_cancels_working_entries(self):
        c = self.conn()
        c.execute(
            """INSERT INTO trades (market_ticker, direction, entry_price, contracts, status, paper)
               VALUES ('KXHIGHCHI-26JUN10-B90.5', 'no', 0.30, 1, 'open', 0)"""
        )
        tid = c.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
        c.execute(
            """INSERT INTO orders (trade_id, market_ticker, side, price, contracts, status,
                                   order_type, kalshi_order_id, purpose, paper)
               VALUES (?, 'KXHIGHCHI-26JUN10-B90.5', 'no', 0.30, 1, 'submitted', 'limit',
                       'K1', 'entry', 0)""",
            (tid,),
        )
        c.commit()
        c.close()

        with mock.patch("app.services.order_manager.cancel_kalshi_order", return_value=True):
            from app.services.risk import activate_kill_switch, kill_switch_active
            result = activate_kill_switch("test kill")

        self.assertTrue(kill_switch_active())
        self.assertEqual(result["working_entries_cancelled"], 1)
        c = self.conn()
        trade = c.execute("SELECT status FROM trades WHERE id=?", (tid,)).fetchone()
        actions = [r["action"] for r in c.execute("SELECT action FROM audit_log").fetchall()]
        c.close()
        self.assertEqual(trade["status"], "cancelled")
        self.assertIn("kill_switch_activated", actions)
        self.assertIn("live_entry_cancelled", actions)

    def test_kill_switch_blocks_live_place(self):
        from app.services.risk import activate_kill_switch
        with mock.patch("app.services.order_manager.cancel_kalshi_order", return_value=True):
            activate_kill_switch("test")

        from app.services.order_manager import place_order
        with self.assertRaises(RuntimeError) as ctx:
            place_order("KXHIGHNY-26JUN10-B81.5", "no", 0.30, contracts=1)
        self.assertIn("kill switch", str(ctx.exception))

    def test_kill_switch_blocks_auto_entry(self):
        from app.services.auto_entry import auto_enter_qualifying_alerts

        result = auto_enter_qualifying_alerts({
            "paper_trading": True, "auto_paper_trade_enabled": True,
            "kill_switch": True,
        })
        self.assertTrue(result.get("skipped"))
        self.assertEqual(result.get("reason"), "kill switch active")

    def test_deactivate_restores(self):
        from app.services.risk import activate_kill_switch, deactivate_kill_switch, kill_switch_active
        with mock.patch("app.services.order_manager.cancel_kalshi_order", return_value=True):
            activate_kill_switch("test")
        deactivate_kill_switch()
        self.assertFalse(kill_switch_active())


class TestPreTradeChecks(_RiskCase):
    def _checks(self, settings=None, **kw):
        from app.services.risk import pre_trade_checks

        defaults = dict(market_ticker="KXHIGHNY-26JUN10-B81.5", direction="no",
                        yes_price=0.30, contracts=2)
        defaults.update(kw)
        base_settings = {"paper_trading": False, "kill_switch": False,
                         "live_max_contracts_per_trade": 2,
                         "live_max_total_exposure": 25.0}
        if settings:
            base_settings.update(settings)
        with mock.patch("app.services.kalshi_client.get_balance",
                        return_value={"balance": 100.0}):
            return pre_trade_checks(settings=base_settings, **defaults)

    def test_clean_order_passes(self):
        self.assertEqual(self._checks(), [])

    def test_price_bounds(self):
        v = self._checks(yes_price=1.5)
        self.assertTrue(any("price sanity" in x for x in v))

    def test_contract_cap(self):
        v = self._checks(contracts=5)
        self.assertTrue(any("live cap" in x for x in v))

    def test_duplicate_guard(self):
        self._open_live_trade(ticker="KXHIGHNY-26JUN10-B81.5")
        v = self._checks()
        self.assertTrue(any("duplicate guard" in x for x in v))

    def test_exposure_cap(self):
        # 30 open contracts of NO at 0.30 -> exposure 0.70*30 = 21; adding
        # 2x0.70 = 1.40 -> 22.4 OK at cap 25, fails at cap 22.
        for _ in range(10):
            self._open_live_trade(ticker=f"KXT-{_}", contracts=3)
        ok = self._checks()
        self.assertEqual(ok, [])
        v = self._checks(settings={"live_max_total_exposure": 22.0})
        self.assertTrue(any("exposure cap" in x for x in v))

    def test_insufficient_balance(self):
        from app.services.risk import pre_trade_checks
        with mock.patch("app.services.kalshi_client.get_balance",
                        return_value={"balance": 0.5}):
            v = pre_trade_checks("KXHIGHNY-26JUN10-B81.5", "no", 0.30, 2,
                                 settings={"paper_trading": False, "kill_switch": False,
                                           "live_max_contracts_per_trade": 2,
                                           "live_max_total_exposure": 25.0})
        self.assertTrue(any("insufficient balance" in x for x in v))


class TestLossLimits(_RiskCase):
    def test_paper_mode_skips(self):
        import app.config as cfg
        with open(cfg.CONFIG_PATH, "w") as f:
            json.dump({"paper_trading": True}, f)
        from app.services.risk import check_loss_limits
        self.assertTrue(check_loss_limits().get("skipped"))

    def test_daily_breach_reverts_to_paper_and_kills(self):
        import app.config as cfg
        with open(cfg.CONFIG_PATH, "w") as f:
            json.dump({"paper_trading": False, "live_daily_loss_limit": 5.0,
                       "live_weekly_loss_limit": 0}, f)
        self._open_live_trade(status="closed", pnl=-6.0,
                              exit_time="2200-01-01 00:00:00")
        # exit_time in the future of now? Use datetime('now') compatible value:
        c = self.conn()
        c.execute("UPDATE trades SET exit_time=datetime('now') WHERE paper=0")
        c.commit()
        c.close()

        from app.services.risk import check_loss_limits
        result = check_loss_limits()
        self.assertIsNotNone(result["breached"])

        settings = cfg.load()
        self.assertTrue(settings["paper_trading"])
        self.assertTrue(settings["kill_switch"])
        self.assertFalse(settings["auto_trade_enabled"])
        c = self.conn()
        actions = [r["action"] for r in c.execute("SELECT action FROM audit_log").fetchall()]
        c.close()
        self.assertIn("loss_limit_revert_to_paper", actions)
        self.assertIn("kill_switch_activated", actions)

    def test_no_breach_within_limits(self):
        import app.config as cfg
        with open(cfg.CONFIG_PATH, "w") as f:
            json.dump({"paper_trading": False, "live_daily_loss_limit": 5.0}, f)
        self._open_live_trade(status="closed", pnl=-2.0)
        c = self.conn()
        c.execute("UPDATE trades SET exit_time=datetime('now') WHERE paper=0")
        c.commit()
        c.close()

        from app.services.risk import check_loss_limits
        result = check_loss_limits()
        self.assertIsNone(result["breached"])
        self.assertFalse(cfg.load().get("kill_switch", False))


class TestExposure(_RiskCase):
    def test_exposure_uses_side_cost(self):
        from app.services.risk import current_live_exposure

        # NO at yes 0.30 -> side cost 0.70 x 2 = 1.40
        self._open_live_trade(direction="no", entry=0.30, contracts=2)
        # YES at 0.20 x 3 = 0.60
        self._open_live_trade(ticker="KXT2", direction="yes", entry=0.20, contracts=3)
        self.assertAlmostEqual(current_live_exposure(), 2.0)


if __name__ == "__main__":
    unittest.main()
