import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock


class TestDecideMarket(unittest.TestCase):
    """Mathematical certainty detection. Margin = 1.5F throughout."""

    def _decide(self, kind, strikes, high=None, low=None, margin=1.5):
        from app.services.settlement_sniper import decide_market

        return decide_market(kind, strikes, high, low, margin)

    def test_high_bracket_dead_when_observed_above_cap(self):
        strikes = {"kind": "bracket", "floor": 80.0, "cap": 82.0}
        self.assertEqual(self._decide("high", strikes, high=84.0), "no")

    def test_high_bracket_undecided_below_margin(self):
        strikes = {"kind": "bracket", "floor": 80.0, "cap": 82.0}
        self.assertIsNone(self._decide("high", strikes, high=83.0))  # only +1.0 over cap

    def test_high_bracket_never_decided_yes(self):
        # High inside the bracket can still rise OUT of it. Never certain-yes.
        strikes = {"kind": "bracket", "floor": 80.0, "cap": 82.0}
        self.assertIsNone(self._decide("high", strikes, high=81.0))

    def test_high_below_bracket_is_not_decided(self):
        # The high can still rise into the bracket.
        strikes = {"kind": "bracket", "floor": 80.0, "cap": 82.0}
        self.assertIsNone(self._decide("high", strikes, high=70.0))

    def test_high_threshold_greater_decided_yes(self):
        strikes = {"kind": "threshold", "strike": 82.0, "strike_type": "greater"}
        self.assertEqual(self._decide("high", strikes, high=84.0), "yes")
        self.assertIsNone(self._decide("high", strikes, high=83.0))

    def test_low_bracket_dead_when_observed_below_floor(self):
        strikes = {"kind": "bracket", "floor": 60.0, "cap": 62.0}
        self.assertEqual(self._decide("low", strikes, low=58.0), "no")
        self.assertIsNone(self._decide("low", strikes, low=59.0))

    def test_low_above_bracket_is_not_decided(self):
        # The low can still fall into the bracket later.
        strikes = {"kind": "bracket", "floor": 60.0, "cap": 62.0}
        self.assertIsNone(self._decide("low", strikes, low=65.0))

    def test_low_threshold_greater_decided_no(self):
        strikes = {"kind": "threshold", "strike": 60.0, "strike_type": "greater"}
        self.assertEqual(self._decide("low", strikes, low=58.0), "no")

    def test_no_observation_no_decision(self):
        strikes = {"kind": "bracket", "floor": 80.0, "cap": 82.0}
        self.assertIsNone(self._decide("high", strikes, high=None))


class TestTickerParsing(unittest.TestCase):
    def test_event_date(self):
        from app.services.settlement_sniper import event_date_from_ticker

        self.assertEqual(event_date_from_ticker("KXHIGHNY-26JUN10-B81.5"), "2026-06-10")
        self.assertEqual(event_date_from_ticker("KXLOWTCHI-26DEC03-T40"), "2026-12-03")
        self.assertIsNone(event_date_from_ticker("KXWEIRD"))

    def test_strikes_bracket_from_raw(self):
        from app.services.settlement_sniper import strikes_from_ticker

        s = strikes_from_ticker("KXHIGHNY-26JUN10-B81.5", {"floor_strike": 81, "cap_strike": 82})
        self.assertEqual(s, {"kind": "bracket", "floor": 81.0, "cap": 82.0})

    def test_strikes_bracket_fallback(self):
        from app.services.settlement_sniper import strikes_from_ticker

        s = strikes_from_ticker("KXHIGHNY-26JUN10-B81.5", {})
        self.assertEqual(s, {"kind": "bracket", "floor": 80.5, "cap": 82.5})

    def test_strikes_threshold(self):
        from app.services.settlement_sniper import strikes_from_ticker

        s = strikes_from_ticker("KXHIGHNY-26JUN10-T82", {"strike_type": "greater"})
        self.assertEqual(s["kind"], "threshold")
        self.assertEqual(s["strike"], 82.0)


class TestSniperScan(unittest.TestCase):
    def setUp(self):
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_file.close()
        os.environ["DB_PATH"] = self.db_file.name
        from app.database import init_db
        init_db()

        self.config_file = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
        json.dump({"paper_trading": True, "sniper_enabled": True,
                   "sniper_min_edge_cents": 5, "sniper_margin_f": 1.5}, self.config_file)
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

    def _seed_market(self, ticker, yes_bid, yes_ask, floor, cap):
        c = self.conn()
        c.execute(
            """INSERT INTO markets (ticker, title, status, close_time, yes_bid, yes_ask,
                                    market_price, raw_json)
               VALUES (?, 't', 'open', datetime('now', '+6 hours'), ?, ?, ?, ?)""",
            (ticker, yes_bid, yes_ask, (yes_bid + yes_ask) / 2,
             json.dumps({"floor_strike": floor, "cap_strike": cap})),
        )
        c.commit()
        c.close()

    def test_decided_mispriced_market_is_entered(self):
        # Bracket 81-82, observed high already 85 -> YES dead. Market still
        # bids YES at 12c -> NO is free money; sniper should enter NO.
        self._seed_market("KXHIGHNY-26JUN10-B81.5", 0.12, 0.16, 81, 82)
        obs = {"available": True, "observed_high": 85.0, "observed_low": 70.0}

        with mock.patch("app.services.settlement_sniper._observed_for_ticker", return_value=obs):
            from app.services.settlement_sniper import run_sniper_scan
            result = run_sniper_scan()

        self.assertEqual(result["entered"], 1)
        self.assertEqual(result["opportunities"][0]["direction"], "no")
        self.assertAlmostEqual(result["opportunities"][0]["edge"], 0.12)

        c = self.conn()
        trade = c.execute("SELECT * FROM trades").fetchone()
        alert = c.execute("SELECT details FROM alerts WHERE id=?", (trade["alert_id"],)).fetchone()
        audit = c.execute("SELECT action FROM audit_log WHERE action='sniper_entry'").fetchone()
        c.close()
        self.assertEqual(trade["direction"], "no")
        self.assertEqual(trade["contracts"], 1)
        self.assertEqual(json.loads(alert["details"])["learning_mode"], "sniper")
        self.assertIsNotNone(audit)

    def test_fairly_priced_decided_market_is_skipped(self):
        # Decided NO but market already prices YES at 2c bid — no edge left.
        self._seed_market("KXHIGHNY-26JUN10-B81.5", 0.02, 0.04, 81, 82)
        obs = {"available": True, "observed_high": 85.0, "observed_low": 70.0}
        with mock.patch("app.services.settlement_sniper._observed_for_ticker", return_value=obs):
            from app.services.settlement_sniper import run_sniper_scan
            result = run_sniper_scan()
        self.assertEqual(result["entered"], 0)

    def test_undecided_market_is_skipped(self):
        self._seed_market("KXHIGHNY-26JUN10-B81.5", 0.30, 0.34, 81, 82)
        obs = {"available": True, "observed_high": 79.0, "observed_low": 70.0}
        with mock.patch("app.services.settlement_sniper._observed_for_ticker", return_value=obs):
            from app.services.settlement_sniper import run_sniper_scan
            result = run_sniper_scan()
        self.assertEqual(result["entered"], 0)
        self.assertEqual(result["opportunities"], [])

    def test_kill_switch_blocks_sniper(self):
        import app.config as cfg
        with open(cfg.CONFIG_PATH, "w") as f:
            json.dump({"paper_trading": True, "kill_switch": True}, f)
        from app.services.settlement_sniper import run_sniper_scan
        result = run_sniper_scan()
        self.assertTrue(result.get("skipped"))

    def test_live_mode_requires_explicit_enable(self):
        import app.config as cfg
        with open(cfg.CONFIG_PATH, "w") as f:
            json.dump({"paper_trading": False, "sniper_enabled": True}, f)
        from app.services.settlement_sniper import run_sniper_scan
        result = run_sniper_scan()
        self.assertTrue(result.get("skipped"))

    def test_existing_position_not_duplicated(self):
        self._seed_market("KXHIGHNY-26JUN10-B81.5", 0.12, 0.16, 81, 82)
        c = self.conn()
        c.execute(
            """INSERT INTO trades (market_ticker, direction, entry_price, contracts, status, paper)
               VALUES ('KXHIGHNY-26JUN10-B81.5', 'no', 0.20, 1, 'open', 1)"""
        )
        c.commit()
        c.close()
        obs = {"available": True, "observed_high": 85.0, "observed_low": 70.0}
        with mock.patch("app.services.settlement_sniper._observed_for_ticker", return_value=obs):
            from app.services.settlement_sniper import run_sniper_scan
            result = run_sniper_scan()
        self.assertEqual(result["entered"], 0)


if __name__ == "__main__":
    unittest.main()
