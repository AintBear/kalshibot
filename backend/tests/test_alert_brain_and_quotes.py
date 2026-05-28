import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import requests


class TestQuoteParsing(unittest.TestCase):
    def test_zero_bid_still_uses_bid_ask_mid(self):
        from app.services.kalshi_client import quote_from_market

        quote = quote_from_market({"yes_bid": 0, "yes_ask": 4, "last_price": 20})

        self.assertEqual(quote["yes_bid"], 0.0)
        self.assertEqual(quote["yes_ask"], 0.04)
        self.assertEqual(quote["market_price"], 0.02)
        self.assertEqual(quote["spread"], 0.04)


class TestKalshiApiTransport(unittest.TestCase):
    def test_api_base_normalization_strips_duplicate_trade_api_path(self):
        from app.services.kalshi_client import _normalize_api_base

        self.assertEqual(
            _normalize_api_base("https://external-api.kalshi.com/trade-api/v2/trade-api/v2"),
            "https://external-api.kalshi.com/trade-api/v2",
        )
        self.assertEqual(
            _normalize_api_base("https://external-api.kalshi.com/trade-api/v2/markets"),
            "https://external-api.kalshi.com/trade-api/v2",
        )

    def test_request_falls_back_to_legacy_host_on_connect_error(self):
        from app.services.kalshi_client import kalshi_request

        class Response:
            ok = True
            status_code = 200

            def json(self):
                return {"markets": []}

        with patch(
            "app.services.kalshi_client.requests.request",
            side_effect=[requests.ConnectionError("dns"), Response()],
        ) as request:
            response = kalshi_request("GET", "/markets", settings={}, timeout=1)

        self.assertTrue(response.ok)
        self.assertEqual(request.call_count, 2)
        self.assertIn("external-api.kalshi.com", request.call_args_list[0].args[1])
        self.assertIn("api.elections.kalshi.com", request.call_args_list[1].args[1])

    def test_settlement_result_uses_alternate_kalshi_fields(self):
        from app.services.kalshi_client import settlement_exit_price_from_market, settlement_result_from_market

        self.assertEqual(settlement_result_from_market({"winning_side": "YES"}), "yes")
        self.assertEqual(settlement_exit_price_from_market({"settlement_result": "no"}), 0.0)


class TestBrainStatusSamples(unittest.TestCase):
    def setUp(self):
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.db_file.name
        os.environ["DB_PATH"] = self.db_path
        from app.database import init_db
        init_db()

    def tearDown(self):
        os.unlink(self.db_path)

    def test_positive_clv_rate_uses_only_clv_backed_samples(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """INSERT INTO trades
               (market_ticker, direction, entry_price, exit_price, clv, pnl, status, paper, exit_time)
               VALUES ('KXHIGHTBOS-26MAY22-B75.5', 'no', 0.30, 0.0, 0.30, 0.30, 'closed', 1, datetime('now'))"""
        )
        conn.execute(
            """INSERT INTO trades
               (market_ticker, direction, entry_price, exit_price, clv, pnl, status, paper, exit_time)
               VALUES ('KXHIGHTBOS-26MAY22-B77.5', 'no', 0.30, 0.30, NULL, NULL, 'closed', 1, datetime('now'))"""
        )
        conn.commit()
        conn.close()

        from app.services.weather_brain import get_brain_status
        status = get_brain_status()

        self.assertEqual(status["settled_trades"], 2)
        self.assertEqual(status["learning_samples"], 1)
        self.assertEqual(status["pending_settlement_trades"], 1)
        self.assertEqual(status["positive_clv_rate"], 1.0)

    def test_prediction_accuracy_excludes_paper_reset_trades(self):
        conn = sqlite3.connect(self.db_path)
        for i in range(10):
            conn.execute(
                """INSERT INTO trades
                   (market_ticker, direction, entry_price, exit_price, clv, pnl,
                    status, exit_reason, paper, prediction_correct, exit_time)
                   VALUES (?, 'no', 0.30, 0.30, 0.0, 0.0,
                           'closed', 'paper_reset', 1, 1, datetime('now'))""",
                (f"KXHIGHTBOS-26MAY22-B{70+i}.5",),
            )
        for i in range(10):
            conn.execute(
                """INSERT INTO trades
                   (market_ticker, direction, entry_price, exit_price, clv, pnl,
                    status, exit_reason, paper, prediction_correct, exit_time)
                   VALUES (?, 'no', 0.30, 1.0, -0.70, -0.70,
                           'closed', 'market_closed', 1, 0, datetime('now'))""",
                (f"KXLOWTBOS-26MAY22-B{50+i}.5",),
            )
        conn.commit()
        conn.close()

        from app.services.weather_brain import get_brain_status
        status = get_brain_status()

        self.assertEqual(status["prediction_sample_count"], 10)
        self.assertEqual(status["prediction_correct_count"], 0)
        self.assertEqual(status["prediction_accuracy"], 0.0)
        self.assertEqual(status["excluded_reset_trades"], 10)

    def test_live_entry_quality_stays_blocked_when_paper_pnl_and_hit_rate_are_bad(self):
        conn = sqlite3.connect(self.db_path)
        for i in range(7):
            conn.execute(
                """INSERT INTO trades
                   (market_ticker, direction, entry_price, exit_price, clv, pnl, status, paper, exit_time)
                   VALUES (?, 'no', 0.30, 1.0, -0.70, -0.70, 'closed', 1, datetime('now'))""",
                (f"KXHIGHTBOS-26MAY22-B{70+i}.5",),
            )
        for i in range(13):
            conn.execute(
                """INSERT INTO trades
                   (market_ticker, direction, entry_price, exit_price, clv, pnl, status, paper, exit_time)
                   VALUES (?, 'no', 0.35, 1.0, -0.65, -0.65, 'closed', 1, datetime('now'))""",
                (f"KXLOWTBOS-26MAY22-B{50+i}.5",),
            )
        conn.commit()
        conn.close()

        from app.services.weather_brain import get_brain_status
        status = get_brain_status()

        self.assertFalse(status["entry_quality_ok"])
        self.assertLessEqual(status["score"], 72)
        self.assertLess(status["realized_pnl_paper"], 0)
        self.assertIn("deficit_recovery", status)
        self.assertLess(status["deficit_recovery"]["current_deficit"], 0)


class TestPositionSizingTiers(unittest.TestCase):
    def setUp(self):
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.db_file.name
        os.environ["DB_PATH"] = self.db_path
        from app.database import init_db
        init_db()

    def tearDown(self):
        os.unlink(self.db_path)

    def test_open_event_does_not_block_paper_learning_recommendation(self):
        from app.services.position_sizing import recommend_alert

        rec = recommend_alert(
            {
                "direction": "no",
                "market_price": 0.20,
                "model_prob": 0.08,
                "no_ask": 0.82,
                "market_ticker": "KXHIGHNY-TEST",
                "brain_score": 90,
                "brain_state": "paper_ready",
                "details": {
                    "event_has_open_trade": True,
                    "brain": {
                        "score": 90,
                        "state": "paper_ready",
                        "learned": {"trade_count": 10, "positive_clv_rate": 0.60, "recent_avg_clv": 0.03},
                    },
                },
            },
            {"paper_starting_balance": 500, "max_contracts_per_trade": 5},
        )

        self.assertEqual(rec["contracts"], 1)
        self.assertNotIn("event already has an open paper trade", rec["blockers"])
        self.assertIn(rec["tier"], ("learning", "tier_b", "tier_a"))

    def test_recommendation_uses_side_ask_for_no_entry(self):
        from app.services.position_sizing import recommend_alert

        rec = recommend_alert(
            {
                "direction": "no",
                "market_price": 0.205,
                "model_prob": 0.135,
                "yes_bid": 0.18,
                "yes_ask": 0.23,
                "no_bid": 0.77,
                "no_ask": 0.82,
                "brain_score": 90,
                "brain_state": "paper_ready",
                "confidence": 0.70,
                "details": {
                    "brain": {
                        "score": 90,
                        "state": "paper_ready",
                        "learned": {"trade_count": 10, "positive_clv_rate": 0.60, "recent_avg_clv": 0.03},
                    },
                },
            },
            {"paper_starting_balance": 500, "kelly_fraction": 0.25},
        )

        self.assertAlmostEqual(rec["limit_price_side"], 0.82, places=4)
        self.assertAlmostEqual(rec["limit_price_yes"], 0.18, places=4)
        self.assertAlmostEqual(rec["side_edge"], 0.045, places=4)

    def test_bad_paper_segment_still_allows_contracts(self):
        from app.services.position_sizing import recommend_alert

        rec = recommend_alert(
            {
                "direction": "no",
                "market_price": 0.20,
                "model_prob": 0.08,
                "no_ask": 0.82,
                "market_ticker": "KXHIGHNY-TEST",
                "brain_score": 61,
                "brain_state": "caution",
                "confidence": 0.70,
                "details": {
                    "brain": {
                        "score": 61,
                        "state": "caution",
                        "learned": {
                            "trade_count": 40,
                            "positive_clv_rate": 0.25,
                            "recent_avg_clv": -0.04,
                            "avg_pnl": -0.12,
                            "stop_loss_rate": 0.58,
                        },
                    },
                },
            },
            {"paper_starting_balance": 500, "paper_trading": True},
        )

        self.assertGreaterEqual(rec["contracts"], 1)
        self.assertEqual(rec["action"], "learn")
        self.assertEqual(rec["blockers"], [])

    def test_recovering_paper_segment_allows_contracts(self):
        from app.services.position_sizing import recommend_alert

        rec = recommend_alert(
            {
                "direction": "no",
                "market_price": 0.32,
                "model_prob": 0.08,
                "no_ask": 0.70,
                "market_ticker": "KXHIGHCHI-TEST",
                "brain_score": 59,
                "brain_state": "caution",
                "confidence": 0.70,
                "details": {
                    "brain": {
                        "score": 59,
                        "state": "caution",
                        "learned": {
                            "trade_count": 80,
                            "positive_clv_rate": 0.35,
                            "recent_avg_clv": 0.05,
                            "recent_positive_clv_rate": 0.40,
                            "avg_pnl": -0.08,
                            "stop_loss_rate": 0.48,
                        },
                    },
                },
            },
            {"paper_starting_balance": 500, "paper_trading": True},
        )

        self.assertGreaterEqual(rec["contracts"], 1)
        self.assertEqual(rec["action"], "learn")
        self.assertEqual(rec["blockers"], [])

    def test_strong_paper_signal_scales_learning_contracts(self):
        from app.services.position_sizing import recommend_alert

        rec = recommend_alert(
            {
                "direction": "no",
                "market_price": 0.30,
                "model_prob": 0.10,
                "no_ask": 0.72,
                "market_ticker": "KXHIGHNY-TEST",
                "brain_score": 86,
                "brain_state": "paper_ready",
                "confidence": 0.82,
                "details": {
                    "brain": {
                        "score": 86,
                        "state": "paper_ready",
                        "learned": {
                            "trade_count": 40,
                            "positive_clv_rate": 0.62,
                            "recent_avg_clv": 0.04,
                            "avg_pnl": 0.06,
                            "stop_loss_rate": 0.20,
                        },
                    },
                },
            },
            {"paper_starting_balance": 500, "paper_trading": True, "paper_learning_max_contracts": 3},
        )

        self.assertGreaterEqual(rec["contracts"], 2)
        self.assertEqual(rec["blockers"], [])

    def test_live_sizing_stays_blocked_for_recovering_but_weak_segment(self):
        from app.services.position_sizing import recommend_alert

        rec = recommend_alert(
            {
                "direction": "no",
                "market_price": 0.25,
                "model_prob": 0.08,
                "no_ask": 0.77,
                "market_ticker": "KXHIGHCHI-TEST",
                "brain_score": 59,
                "brain_state": "caution",
                "confidence": 0.70,
                "details": {
                    "brain": {
                        "score": 59,
                        "state": "caution",
                        "auto_eligible": False,
                        "learned": {
                            "trade_count": 80,
                            "positive_clv_rate": 0.35,
                            "recent_avg_clv": 0.05,
                            "recent_positive_clv_rate": 0.40,
                            "avg_pnl": -0.08,
                            "stop_loss_rate": 0.48,
                            "auto_eligible": False,
                        },
                    },
                },
            },
            {"paper_starting_balance": 500, "paper_trading": False},
        )

        self.assertEqual(rec["contracts"], 0)
        self.assertIn("similar trades have not earned auto sizing", rec["blockers"])
        self.assertIn("similar paper P&L is negative", rec["blockers"])

    def test_exit_targets_keep_wider_minimum_stop_distance(self):
        from app.services.position_sizing import recommend_alert

        rec = recommend_alert(
            {
                "direction": "no",
                "market_price": 0.22,
                "model_prob": 0.05,
                "yes_bid": 0.20,
                "yes_ask": 0.24,
                "no_bid": 0.76,
                "no_ask": 0.78,
                "brain_score": 88,
                "brain_state": "paper_ready",
                "confidence": 0.70,
                "details": {
                    "brain": {
                        "score": 88,
                        "state": "paper_ready",
                        "auto_eligible": True,
                        "learned": {
                            "trade_count": 40,
                            "positive_clv_rate": 0.60,
                            "recent_avg_clv": 0.03,
                            "avg_pnl": 0.05,
                            "stop_loss_rate": 0.20,
                            "settlement_win_rate": 0.55,
                        },
                    },
                },
            },
            {"paper_starting_balance": 500, "paper_trading": False},
        )

        entry_side = rec["limit_price_side"]
        stop_side = 1.0 - rec["stop_loss_price"]
        target_side = 1.0 - rec["take_profit_price"]
        risk = entry_side - stop_side
        reward = target_side - entry_side

        self.assertGreater(rec["contracts"], 0)
        self.assertGreaterEqual(risk, 0.12)
        self.assertGreater(reward, 0)

    def test_near_close_recommendation_uses_wider_stop_and_time_multiplier(self):
        from app.services.position_sizing import recommend_alert

        rec = recommend_alert(
            {
                "direction": "no",
                "market_price": 0.40,
                "model_prob": 0.10,
                "no_ask": 0.62,
                "market_ticker": "KXHIGHNY-TEST",
                "brain_score": 88,
                "brain_state": "paper_ready",
                "confidence": 0.80,
                "details": {
                    "hours_to_close": 2.0,
                    "brain": {
                        "score": 88,
                        "state": "paper_ready",
                        "auto_eligible": True,
                        "learned": {
                            "trade_count": 40,
                            "positive_clv_rate": 0.60,
                            "recent_avg_clv": 0.03,
                            "avg_pnl": 0.05,
                            "stop_loss_rate": 0.20,
                            "settlement_win_rate": 0.55,
                        },
                    },
                },
            },
            {"paper_starting_balance": 500, "paper_trading": False},
        )

        self.assertEqual(rec["time_priority"], "high")
        self.assertAlmostEqual(rec["time_urgency_multiplier"], 1.5, places=4)

    def test_no_on_expensive_market_blocked(self):
        """No trades on 85c+ markets are blocked — historically 0% correct."""
        from app.services.position_sizing import recommend_alert

        rec = recommend_alert(
            {
                "direction": "no",
                "market_price": 0.90,
                "model_prob": 0.40,
                "yes_bid": 0.89,
                "yes_ask": 0.91,
                "no_bid": 0.09,
                "no_ask": 0.10,
                "brain_score": 88,
                "brain_state": "paper_ready",
                "confidence": 0.80,
                "details": {
                    "hours_to_close": 3.0,
                    "brain": {
                        "score": 88,
                        "state": "paper_ready",
                        "learned": {
                            "trade_count": 40,
                            "positive_clv_rate": 0.60,
                            "recent_avg_clv": 0.03,
                            "avg_pnl": 0.05,
                        },
                    },
                },
            },
            {"paper_starting_balance": 500, "paper_trading": True},
        )

        self.assertEqual(rec["contracts"], 0)
        self.assertTrue(any("85c+" in b for b in rec["blockers"]))


class TestAutoPaperGates(unittest.TestCase):
    def test_paper_auto_pauses_when_bad_evidence_has_no_eligible_segment(self):
        from app.services.auto_entry import paper_auto_blocker

        reason = paper_auto_blocker({
            "learning_samples": 200,
            "avg_clv": -1.5,
            "recent_30_avg_clv": -3.3,
            "realized_pnl_paper": -16.11,
            "recent_30_pnl_paper": -2.0,
            "positive_clv_rate": 0.305,
            "auto_eligible_segments": 0,
        })

        self.assertIn("entry evidence is negative", reason)

    def test_paper_auto_pauses_when_prediction_accuracy_has_no_good_segment(self):
        from app.services.auto_entry import paper_auto_blocker

        reason = paper_auto_blocker({
            "prediction_accuracy": 0.3235,
            "prediction_sample_count": 510,
            "learning_samples": 510,
            "paper_auto_eligible_segments": 0,
        })

        self.assertIn("prediction accuracy 32.4%", reason)

    def test_paper_auto_pauses_when_settlement_backlog_is_high(self):
        from app.services.auto_entry import paper_auto_blocker

        reason = paper_auto_blocker({
            "open_trades": 0,
            "pending_settlement_trades": 20,
            "learning_samples": 115,
            "prediction_accuracy": 0.55,
            "prediction_sample_count": 115,
            "paper_auto_eligible_segments": 1,
        }, {"max_open_paper_trades": 50, "paper_settlement_backlog_limit": 20})

        self.assertIn("settlement backlog too high", reason)

    def test_paper_auto_allows_early_learning(self):
        from app.services.auto_entry import paper_auto_blocker

        reason = paper_auto_blocker({
            "learning_samples": 12,
            "recent_30_avg_clv": -3.3,
            "realized_pnl_paper": -1.0,
            "positive_clv_rate": 0.1,
        })

        self.assertEqual(reason, "")

    def test_paper_auto_resumes_when_recent_learning_recovers(self):
        from app.services.auto_entry import paper_auto_blocker

        reason = paper_auto_blocker({
            "learning_samples": 225,
            "recent_30_avg_clv": 7.1,
            "realized_pnl_paper": -8.32,
            "recent_30_pnl_paper": 8.29,
            "positive_clv_rate": 0.33,
            "auto_eligible_segments": 0,
            "paper_auto_eligible_segments": 2,
        })

        self.assertEqual(reason, "")


class TestPaperEntryPricing(unittest.TestCase):
    def test_no_paper_entry_uses_no_ask_as_yes_coordinate(self):
        from app.routers.alerts import _paper_entry_yes_price

        price = _paper_entry_yes_price(
            {"direction": "no", "market_price": 0.205, "live_no_ask": 0.82},
            {},
            {},
        )

        self.assertAlmostEqual(price, 0.18, places=4)

    def test_yes_paper_entry_uses_yes_ask(self):
        from app.routers.alerts import _paper_entry_yes_price

        price = _paper_entry_yes_price(
            {"direction": "yes", "market_price": 0.205, "live_yes_ask": 0.23},
            {},
            {},
        )

        self.assertAlmostEqual(price, 0.23, places=4)

    def test_no_exit_recommendation_does_not_readd_default_exits(self):
        from app.services.order_manager import recommendation_exit_args

        args = recommendation_exit_args(
            {"stop_loss_price": None, "take_profit_price": None},
            stop_loss_pct=0.50,
            take_profit_pct=0.50,
        )

        self.assertIsNone(args["stop_loss_pct"])
        self.assertIsNone(args["take_profit_pct"])
        self.assertIsNone(args["stop_loss_price"])
        self.assertIsNone(args["take_profit_price"])

class TestAutoEntryExecutionGates(unittest.TestCase):
    def setUp(self):
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.db_file.name
        os.environ["DB_PATH"] = self.db_path
        from app.database import init_db
        init_db()

    def tearDown(self):
        os.unlink(self.db_path)

    def _settings(self):
        return {
            "paper_trading": True,
            "auto_paper_trade_enabled": True,
            "auto_trade_enabled": False,
            "max_open_paper_trades": 20,
            "paper_starting_balance": 500,
        }

    def _brain(self):
        return {
            "learning_samples": 100,
            "recent_30_avg_clv": 5.0,
            "realized_pnl_paper": -1.0,
            "recent_30_pnl_paper": 2.0,
            "positive_clv_rate": 0.40,
            "paper_auto_eligible_segments": 1,
            "auto_eligible_segments": 0,
            "score": 55,
            "avg_clv": 3.0,
            "entry_quality_ok": False,
        }

    def _insert_open_market(self, conn, ticker):
        raw = json.dumps({"event_ticker": ticker.rsplit("-", 1)[0], "series_ticker": "KXTEST"})
        conn.execute(
            """INSERT INTO markets
               (ticker, title, category, market_price, yes_bid, yes_ask, no_bid, no_ask,
                status, close_time, raw_json)
               VALUES (?, ?, 'weather', 0.40, 0.39, 0.41, 0.59, 0.61,
                       'open', '2099-01-01T00:00:00Z', ?)""",
            (ticker, ticker, raw),
        )

    def test_auto_entry_respects_max_open_paper_trades_for_learning(self):
        conn = sqlite3.connect(self.db_path)
        for i in range(20):
            conn.execute(
                """INSERT INTO trades
                   (market_ticker, direction, entry_price, contracts, paper, status, entry_time)
                   VALUES (?, 'no', 0.80, 1, 1, 'open', datetime('now'))""",
                (f"KXOPEN-{i}",),
            )
        self._insert_open_market(conn, "KXHIGHTEST-26APR30-B51")
        conn.execute(
            """INSERT INTO alerts
               (market_ticker, status, edge, direction, market_price, model_prob,
                confidence, brain_score, brain_state, phantom_risk_level, details)
               VALUES ('KXHIGHTEST-26APR30-B51', 'pending', 0.30, 'no', 0.15, 0.05,
                       0.80, 20, 'skip', 'none', '{}')"""
        )
        conn.commit()
        conn.close()

        settings = {
            **self._settings(),
            "paper_unlimited_learning": False,
            "paper_learning_max_open_per_event": 1,
        }
        with patch("app.config.load", return_value=settings), \
             patch("app.services.weather_brain.get_brain_status", return_value={**self._brain(), "open_trades": 20}), \
             patch("app.services.order_manager.place_order", return_value={"trade_id": 123}) as place_order:
            from app.services.auto_entry import auto_enter_qualifying_alerts
            result = auto_enter_qualifying_alerts()

        self.assertTrue(result["skipped"])
        self.assertEqual(result["total_entered"], 0)
        self.assertIn("open paper book at cap", result["reason"])
        place_order.assert_not_called()

    def test_auto_entry_allows_medium_phantom_risk_for_paper_learning(self):
        conn = sqlite3.connect(self.db_path)
        self._insert_open_market(conn, "KXTEST-26APR30-B50")
        conn.execute(
            """INSERT INTO alerts
               (market_ticker, status, edge, direction, market_price, model_prob,
                confidence, brain_score, brain_state, phantom_risk_level, details)
               VALUES ('KXTEST-26APR30-B50', 'pending', 0.20, 'yes', 0.40, 0.60,
                       0.80, 59, 'caution', 'medium', '{}')"""
        )
        conn.commit()
        conn.close()

        settings = {
            **self._settings(),
            "paper_unlimited_learning": False,
            "paper_learning_max_open_per_event": 1,
        }
        with patch("app.config.load", return_value=settings), \
             patch("app.services.weather_brain.get_brain_status", return_value=self._brain()), \
             patch("app.services.position_sizing.recommend_alert", return_value={
                 "contracts": 1,
                 "limit_price_yes": 0.40,
                 "expected_value_per_contract": 0.10,
                 "side_edge": 0.10,
             }), \
             patch("app.services.order_manager.place_order", return_value={"trade_id": 123}) as place_order:
            from app.services.auto_entry import auto_enter_qualifying_alerts
            result = auto_enter_qualifying_alerts()

        self.assertEqual(result["total_entered"], 1)
        place_order.assert_called_once()

    def test_auto_entry_caps_duplicate_events_for_paper_learning(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """INSERT INTO trades
               (market_ticker, direction, entry_price, contracts, paper, status, entry_time)
               VALUES ('KXHIGHDUP-26APR30-OPEN', 'no', 0.80, 1, 1, 'open', datetime('now'))"""
        )
        for i in range(65):
            ticker = f"KXHIGHDUP-26APR30-B{i}"
            self._insert_open_market(conn, ticker)
            conn.execute(
                """INSERT INTO alerts
                   (market_ticker, status, edge, direction, market_price, model_prob,
                    confidence, brain_score, brain_state, phantom_risk_level, details)
                   VALUES (?, 'pending', 0.40, 'no', 0.30, 0.05,
                           0.80, 80, 'watch', 'none', '{}')""",
                (ticker,),
            )
        self._insert_open_market(conn, "KXHIGHELIG-26APR30-B51")
        conn.execute(
            """INSERT INTO alerts
               (market_ticker, status, edge, direction, market_price, model_prob,
                confidence, brain_score, brain_state, phantom_risk_level, details)
               VALUES ('KXHIGHELIG-26APR30-B51', 'pending', 0.20, 'no', 0.30, 0.05,
                       0.80, 64, 'caution', 'none', '{}')"""
        )
        conn.commit()
        conn.close()

        settings = {
            **self._settings(),
            "paper_unlimited_learning": False,
            "paper_learning_max_open_per_event": 1,
        }
        with patch("app.config.load", return_value=settings), \
             patch("app.services.weather_brain.get_brain_status", return_value=self._brain()), \
             patch("app.services.order_manager.place_order", return_value={"trade_id": 456}) as place_order:
            from app.services.auto_entry import auto_enter_qualifying_alerts
            result = auto_enter_qualifying_alerts()

        self.assertEqual(result["total_entered"], 1)
        self.assertEqual(result["paper_max_open_per_event"], 1)
        tickers = [call.kwargs["market_ticker"] for call in place_order.call_args_list]
        self.assertIn("KXHIGHELIG-26APR30-B51", tickers)
        self.assertNotIn("KXHIGHDUP-26APR30-B0", tickers)

    def test_auto_entry_skips_current_low_prediction_accuracy_segment(self):
        conn = sqlite3.connect(self.db_path)
        self._insert_open_market(conn, "KXBADPRED-26APR30-B50")
        conn.execute(
            """INSERT INTO adaptive_segments
               (segment_key, auto_eligible, avg_clv, avg_pnl,
                positive_clv_rate, recent_avg_clv, recent_positive_clv_rate,
                trade_count, details, updated_at)
               VALUES ('low_bracket:same_day', 0, -0.01, -0.02,
                       0.20, -0.03, 0.10, 40, ?, datetime('now'))""",
            (
                json.dumps({
                    "prediction_accuracy": 0.32,
                    "prediction_sample_count": 20,
                    "prediction_correct_count": 6,
                }),
            ),
        )
        conn.execute(
            """INSERT INTO alerts
               (market_ticker, status, edge, direction, market_price, model_prob,
                confidence, brain_score, brain_state, phantom_risk_level, details)
               VALUES ('KXBADPRED-26APR30-B50', 'pending', 0.30, 'yes', 0.35, 0.65,
                       0.80, 80, 'watch', 'none', ?)""",
            (json.dumps({"segment": "low_bracket", "time_bucket": "same_day"}),),
        )
        conn.commit()
        conn.close()

        with patch("app.config.load", return_value=self._settings()), \
             patch("app.services.weather_brain.get_brain_status", return_value=self._brain()), \
             patch("app.services.order_manager.place_order", return_value={"trade_id": 456}) as place_order:
            from app.services.auto_entry import auto_enter_qualifying_alerts
            result = auto_enter_qualifying_alerts()

        self.assertEqual(result["total_entered"], 0)
        place_order.assert_not_called()

    def test_auto_entry_limits_paper_entries_per_scan(self):
        conn = sqlite3.connect(self.db_path)
        for i in range(5):
            ticker = f"KXHIGHLIMIT{i}-26APR30-B50"
            self._insert_open_market(conn, ticker)
            conn.execute(
                """INSERT INTO alerts
                   (market_ticker, status, edge, direction, market_price, model_prob,
                    confidence, brain_score, brain_state, phantom_risk_level, details)
                   VALUES (?, 'pending', 0.30, 'no', 0.30, 0.05,
                           0.80, 64, 'caution', 'none', '{}')""",
                (ticker,),
            )
        conn.commit()
        conn.close()

        settings = {**self._settings(), "paper_learning_max_entries_per_scan": 2}
        with patch("app.config.load", return_value=settings), \
             patch("app.services.weather_brain.get_brain_status", return_value=self._brain()), \
             patch("app.services.order_manager.place_order", return_value={"trade_id": 789}) as place_order:
            from app.services.auto_entry import auto_enter_qualifying_alerts
            result = auto_enter_qualifying_alerts()

        self.assertEqual(result["total_entered"], 2)
        self.assertEqual(result["paper_entry_limit"], 2)
        self.assertEqual(place_order.call_count, 2)
