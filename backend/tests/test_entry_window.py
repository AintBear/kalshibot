import os
import tempfile
import unittest


def _zone_alert(hours_to_close=None):
    details = {
        "brain": {
            "score": 90,
            "state": "paper_ready",
            "learned": {"trade_count": 10, "positive_clv_rate": 0.60, "recent_avg_clv": 0.03},
        },
    }
    if hours_to_close is not None:
        details["hours_to_close"] = hours_to_close
    return {
        "direction": "no",
        "market_price": 0.30,
        "model_prob": 0.12,
        "yes_bid": 0.28,
        "yes_ask": 0.32,
        "no_bid": 0.68,
        "no_ask": 0.72,
        "market_ticker": "KXHIGHNY-26JUN15-B82.5",
        "brain_score": 90,
        "brain_state": "paper_ready",
        "confidence": 0.70,
        "details": details,
    }


class TestEntryWindowGate(unittest.TestCase):
    """Entry-window gate from the 2026-06-10 settled-trade audit: entries
    <=12h to close earned +13.06c/contract (n=48) while >24h entries lost
    -7.32c/contract (n=551). Only near-close entries are allowed."""

    def setUp(self):
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_file.close()
        os.environ["DB_PATH"] = self.db_file.name
        from app.database import init_db
        init_db()

    def tearDown(self):
        os.unlink(self.db_file.name)

    def test_far_entry_blocked_in_paper(self):
        from app.services.position_sizing import recommend_alert

        rec = recommend_alert(_zone_alert(hours_to_close=30.0), {"paper_starting_balance": 500})
        self.assertTrue(any("entry too early" in b for b in rec["blockers"]))
        self.assertEqual(rec["contracts"], 0)

    def test_near_entry_allowed_in_paper(self):
        from app.services.position_sizing import recommend_alert

        rec = recommend_alert(_zone_alert(hours_to_close=8.0), {"paper_starting_balance": 500})
        self.assertFalse(any("entry too early" in b for b in rec["blockers"]))
        self.assertGreaterEqual(rec["contracts"], 1)

    def test_missing_hours_allowed_in_paper(self):
        # Scanner alerts always carry hours_to_close; synthetic/manual alerts
        # without it should not be paper-blocked by the window gate.
        from app.services.position_sizing import recommend_alert

        rec = recommend_alert(_zone_alert(), {"paper_starting_balance": 500})
        self.assertFalse(any("entry" in b and "window" in b for b in rec["blockers"]))

    def test_far_entry_blocked_in_live(self):
        from app.services.position_sizing import recommend_alert

        rec = recommend_alert(
            _zone_alert(hours_to_close=30.0),
            {"paper_trading": False, "paper_starting_balance": 500},
        )
        self.assertTrue(any("entry too early" in b for b in rec["blockers"]))

    def test_missing_hours_blocked_in_live(self):
        from app.services.position_sizing import recommend_alert

        rec = recommend_alert(
            _zone_alert(),
            {"paper_trading": False, "paper_starting_balance": 500},
        )
        self.assertTrue(any("entry window unknown" in b for b in rec["blockers"]))

    def test_explore_mode_suppresses_window_gate(self):
        from app.services.position_sizing import recommend_alert

        rec = recommend_alert(
            _zone_alert(hours_to_close=30.0),
            {"paper_starting_balance": 500},
            explore=True,
        )
        self.assertFalse(any("entry too early" in b for b in rec["blockers"]))

    def test_gate_disabled_when_setting_zero(self):
        from app.services.position_sizing import recommend_alert

        rec = recommend_alert(
            _zone_alert(hours_to_close=30.0),
            {"paper_starting_balance": 500, "max_entry_hours_to_close": 0},
        )
        self.assertFalse(any("entry too early" in b for b in rec["blockers"]))


if __name__ == "__main__":
    unittest.main()
