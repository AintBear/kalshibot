from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from app.routers import health


class TestHealthScanStatus(unittest.TestCase):
    def _status(self, **updates):
        now = datetime.now(timezone.utc)
        base = {
            "status": "complete",
            "started_at": (now - timedelta(minutes=2)).isoformat(),
            "completed_at": now.isoformat(),
            "series_total": 40,
            "series_errors": 0,
            "markets_processed": 300,
        }
        base.update(updates)
        return base

    def _check(self, status):
        with patch("app.config.load", return_value={"automation_enabled": True}), \
             patch("app.services.scanner.get_scan_status", return_value=status):
            return health._check_scan()

    def test_scan_health_is_skipped_when_automation_is_disabled(self):
        status = self._status(completed_at=None)

        with patch("app.config.load", return_value={"automation_enabled": False}), \
             patch("app.services.scanner.get_scan_status", return_value=status) as get_status:
            self.assertIsNone(health._check_scan())

        get_status.assert_not_called()

    def test_running_scan_is_not_unhealthy_until_stuck_window(self):
        status = self._status(
            status="running",
            started_at=(datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
            completed_at=None,
        )

        self.assertIsNone(self._check(status))

    def test_running_scan_older_than_window_is_stuck(self):
        status = self._status(
            status="running",
            started_at=(datetime.now(timezone.utc) - timedelta(minutes=25)).isoformat(),
            completed_at=None,
        )

        self.assertEqual(self._check(status), "scan_stuck")

    def test_stale_completed_scan_is_degraded(self):
        status = self._status(
            completed_at=(datetime.now(timezone.utc) - timedelta(minutes=50)).isoformat()
        )

        self.assertEqual(self._check(status), "scan_stale")

    def test_completed_scan_with_high_error_rate_is_degraded(self):
        status = self._status(series_total=40, series_errors=10, markets_processed=12)

        self.assertEqual(self._check(status), "scan_high_error_rate")

    def test_completed_scan_with_all_series_failed_is_degraded(self):
        status = self._status(series_total=40, series_errors=40, markets_processed=0)

        self.assertEqual(self._check(status), "scan_failed")
