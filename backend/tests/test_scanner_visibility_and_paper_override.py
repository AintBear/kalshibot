import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch


def _model_result(edge=-0.061, confidence=0.20, market_price=0.99):
    return {
        "ticker": "KXTEST-EXTREME",
        "market_price": market_price,
        "model_prob": round(market_price + edge, 4),
        "edge": edge,
        "direction": "yes" if edge > 0 else "no",
        "confidence": confidence,
        "segment": "low_bracket",
        "time_bucket": "same_day",
        "hours_to_close": 24.0,
        "forecast": {"high": 70, "low": 50, "precip_pct": 10, "source": "NWS"},
        "current_conditions": {},
        "phantom_risk_score": 0.0,
        "phantom_risk_flags": "[]",
        "phantom_risk_level": "none",
        "analysis": "test analysis",
    }


class TestScannerVisibilityRules(unittest.TestCase):
    def setUp(self):
        self.old_db_path = os.environ.get("DB_PATH")
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.db_file.name
        os.environ["DB_PATH"] = self.db_path
        from app.database import init_db
        init_db()

    def tearDown(self):
        os.unlink(self.db_path)
        if self.old_db_path is None:
            os.environ.pop("DB_PATH", None)
        else:
            os.environ["DB_PATH"] = self.old_db_path

    def test_low_confidence_extreme_quote_still_creates_visible_alert(self):
        from app.services import scanner

        market = {
            "ticker": "KXTEST-EXTREME",
            "title": "Extreme quote test",
            "status": "open",
            "yes_bid": 98,
            "yes_ask": 100,
            "no_bid": 0,
            "no_ask": 2,
            "close_time": "2099-01-01T00:00:00Z",
            "event_ticker": "KXTEST",
            "series_ticker": "KXTEST",
        }
        brain = {
            "score": 42,
            "state": "skip",
            "auto_qualified": False,
            "auto_eligible": False,
            "segment": "low_bracket:same_day",
            "messages": ["low_confidence_model", "extreme_quote"],
            "cautions": [],
            "phantom_risk": {"level": "none", "score": 0.0, "flags": []},
            "adjustment": {},
            "learned": {},
            "market_read": {},
            "components": {},
        }

        with patch("app.services.scanner.weather_model.score_market", return_value=_model_result()), \
             patch("app.services.scanner.weather_brain.evaluate_alert", return_value=brain):
            created = scanner._process_market(market, {"paper_starting_balance": 500})

        self.assertTrue(created)
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT status, details FROM alerts WHERE market_ticker='KXTEST-EXTREME'"
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], "pending")
        details = json.loads(row[1])
        self.assertEqual(details["confidence"], 0.20)
        self.assertEqual(details["market_price"], 0.99)

    def test_priority_sort_puts_near_close_high_conviction_first(self):
        from app.services import scanner

        items = [
            {
                "market": {"ticker": "LATE"},
                "result": {"hours_to_close": 30.0, "time_priority": "low", "edge": 0.50, "confidence": 0.90},
            },
            {
                "market": {"ticker": "SOON"},
                "result": {"hours_to_close": 2.0, "time_priority": "high", "edge": 0.08, "confidence": 0.50},
            },
            {
                "market": {"ticker": "SOONER"},
                "result": {"hours_to_close": 2.0, "time_priority": "high", "edge": 0.20, "confidence": 0.80},
            },
        ]

        ordered = sorted(items, key=scanner._priority_sort_key)

        self.assertEqual([item["market"]["ticker"] for item in ordered], ["SOONER", "SOON", "LATE"])


class TestPaperLearningOverride(unittest.TestCase):
    def test_learning_override_requires_positive_gap_and_ev(self):
        from app.routers.alerts import _validate_learning_override

        with self.assertRaises(Exception):
            _validate_learning_override(
                {"phantom_risk_level": "none"},
                {"side_edge": 0.01, "expected_value_per_contract": -0.02},
            )

    def test_learning_override_allows_positive_gap_and_ev(self):
        from app.routers.alerts import _validate_learning_override

        _validate_learning_override(
            {"phantom_risk_level": "none"},
            {"side_edge": 0.04, "expected_value_per_contract": 0.03},
        )


class TestAlertListOpenEventState(unittest.TestCase):
    def setUp(self):
        self.old_db_path = os.environ.get("DB_PATH")
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.db_file.name
        os.environ["DB_PATH"] = self.db_path
        from app.database import init_db
        init_db()

    def tearDown(self):
        os.unlink(self.db_path)
        if self.old_db_path is None:
            os.environ.pop("DB_PATH", None)
        else:
            os.environ["DB_PATH"] = self.old_db_path

    def test_list_alerts_marks_open_event_from_live_trades(self):
        from app.routers.alerts import list_alerts

        conn = sqlite3.connect(self.db_path)
        raw = json.dumps({"event_ticker": "KXTEST-26APR30", "series_ticker": "KXTEST"})
        conn.execute(
            """INSERT INTO markets
               (ticker, title, category, market_price, yes_bid, yes_ask, no_bid, no_ask,
                status, close_time, raw_json)
               VALUES ('KXTEST-26APR30-B50', 'Test bracket', 'weather', 0.30, 0.29, 0.31, 0.69, 0.71,
                       'open', '2099-01-01T00:00:00Z', ?)""",
            (raw,),
        )
        details = json.dumps({
            "brain": {
                "score": 90,
                "state": "paper_ready",
                "learned": {"trade_count": 20, "positive_clv_rate": 0.60, "recent_avg_clv": 0.03, "avg_pnl": 0.05},
                "auto_eligible": True,
            }
        })
        conn.execute(
            """INSERT INTO alerts
               (market_ticker, status, edge, direction, market_price, model_prob,
                confidence, brain_score, brain_state, details)
               VALUES ('KXTEST-26APR30-B50', 'pending', 0.20, 'no', 0.30, 0.05,
                       0.80, 90, 'paper_ready', ?)""",
            (details,),
        )
        conn.execute(
            """INSERT INTO trades
               (market_ticker, direction, entry_price, contracts, status, paper, entry_time)
               VALUES ('KXTEST-26APR30-B49', 'no', 0.85, 1, 'open', 1, datetime('now'))"""
        )
        conn.commit()
        conn.close()

        response = list_alerts(status="pending", limit=10, offset=0, refresh=False, context=False)
        alert = response["alerts"][0]

        self.assertTrue(alert["event_has_open_trade"])
        self.assertTrue(alert["details"]["event_has_open_trade"])
        self.assertGreaterEqual(alert["recommendation"]["contracts"], 1)
        self.assertNotIn("event already has an open paper trade", alert["recommendation"]["blockers"])
        self.assertNotIn("analysis_context", alert["details"])


class TestAutoPaperCandidateSafety(unittest.TestCase):
    def setUp(self):
        self.old_db_path = os.environ.get("DB_PATH")
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.db_file.name
        os.environ["DB_PATH"] = self.db_path
        from app.database import init_db
        init_db()

    def tearDown(self):
        os.unlink(self.db_path)
        if self.old_db_path is None:
            os.environ.pop("DB_PATH", None)
        else:
            os.environ["DB_PATH"] = self.old_db_path

    def _insert_pending_alert(self, ticker, phantom_level):
        conn = sqlite3.connect(self.db_path)
        raw = json.dumps({"event_ticker": ticker.rsplit("-", 1)[0], "series_ticker": "KXTEST"})
        conn.execute(
            """INSERT INTO markets
               (ticker, title, category, market_price, yes_bid, yes_ask, no_bid, no_ask,
                status, close_time, raw_json)
               VALUES (?, 'Test market', 'weather', 0.40, 0.39, 0.42, 0.58, 0.61,
                       'open', '2099-01-01T00:00:00Z', ?)""",
            (ticker, raw),
        )
        conn.execute(
            """INSERT INTO alerts
               (market_ticker, status, edge, direction, market_price, model_prob,
                confidence, brain_score, brain_state, phantom_risk_level, details)
               VALUES (?, 'pending', 0.20, 'yes', 0.40, 0.60,
                       0.80, 59, 'caution', ?, '{}')""",
            (ticker, phantom_level),
        )
        conn.commit()
        conn.close()

    def test_auto_entry_opens_best_tradable_alert_and_filters_weak_ev(self):
        from app.services.auto_entry import auto_enter_qualifying_alerts

        self._insert_pending_alert("KXTEST-26APR30-B50", "none")
        self._insert_pending_alert("KXTEST-26APR30-B60", "none")

        def rec(alert, settings):
            if alert["market_ticker"].endswith("B60"):
                return {
                    "contracts": 1,
                    "limit_price_yes": 0.20,
                    "expected_value_per_contract": 0.15,
                    "side_edge": 0.15,
                }
            return {
                "contracts": 1,
                "limit_price_yes": 0.80,
                "expected_value_per_contract": 0.01,
                "side_edge": 0.01,
            }

        with patch("app.services.position_sizing.recommend_alert", side_effect=rec), \
             patch("app.services.order_manager.place_order", return_value={"trade_id": 123}) as place_order:
            result = auto_enter_qualifying_alerts(settings_override={
                "paper_trading": True,
                "auto_paper_trade_enabled": True,
                "auto_trade_enabled": False,
                "max_open_paper_trades": 20,
                "paper_learning_min_ev": 0.08,
                "paper_learning_min_side_edge": 0.08,
            })

        self.assertEqual(result["total_entered"], 1)
        self.assertEqual(place_order.call_args_list[0].kwargs["market_ticker"], "KXTEST-26APR30-B60")
