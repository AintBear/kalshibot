import json
import unittest
from unittest.mock import patch


class _Response:
    ok = True
    status_code = 201
    text = "{}"

    def json(self):
        return {"order": {"order_id": "order-test"}}


class TestLiveOrderPayload(unittest.TestCase):
    def test_yes_order_uses_yes_price(self):
        from app.services.order_manager import _submit_to_kalshi

        with patch("app.services.order_manager._get_settings", return_value={
            "kalshi_key_id": "key",
            "kalshi_private_key_path": "/tmp/key.pem",
        }), patch("app.services.kalshi_client.kalshi_request", return_value=_Response()) as request:
            order_id = _submit_to_kalshi("KXTEST", "yes", 0.42, 2)

        self.assertEqual(order_id, "order-test")
        payload = json.loads(request.call_args.kwargs["data"])
        self.assertEqual(payload["side"], "yes")
        self.assertEqual(payload["yes_price"], 42)
        self.assertNotIn("no_price", payload)
        self.assertTrue(payload["client_order_id"])

    def test_no_order_uses_no_price_from_yes_coordinate(self):
        from app.services.order_manager import _submit_to_kalshi

        with patch("app.services.order_manager._get_settings", return_value={
            "kalshi_key_id": "key",
            "kalshi_private_key_path": "/tmp/key.pem",
        }), patch("app.services.kalshi_client.kalshi_request", return_value=_Response()) as request:
            order_id = _submit_to_kalshi("KXTEST", "no", 0.39, 3)

        self.assertEqual(order_id, "order-test")
        payload = json.loads(request.call_args.kwargs["data"])
        self.assertEqual(payload["side"], "no")
        self.assertEqual(payload["no_price"], 61)
        self.assertNotIn("yes_price", payload)
        self.assertTrue(payload["client_order_id"])
