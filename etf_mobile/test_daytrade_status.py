from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path

import daytrade_status as status


class DaytradeStatusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_data_dir = status.DATA_DIR
        self.old_feed = status.FEED_STATUS_FILE
        self.old_runner = status.RUNNER_STATUS_FILE
        self.old_auto_power = status.AUTO_POWER_STATUS_FILE
        self.old_feed_recover = status.FEED_RECOVER_STATUS_FILE
        status.set_data_dir(Path(self.tmp.name) / "data_daytrade")

    def tearDown(self):
        status.DATA_DIR = self.old_data_dir
        status.FEED_STATUS_FILE = self.old_feed
        status.RUNNER_STATUS_FILE = self.old_runner
        status.AUTO_POWER_STATUS_FILE = self.old_auto_power
        status.FEED_RECOVER_STATUS_FILE = self.old_feed_recover
        self.tmp.cleanup()

    def test_feed_status_round_trip(self):
        status.write_feed_status("demo_csv", "ok", symbols=["1570"], bars_written=1, last_bar_ts="2026-05-21 09:00:00")

        data = status.read_feed_status()

        self.assertEqual(data["adapter"], "demo_csv")
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["bars_written"], 1)

    def test_feed_health_uses_market_closed_outside_session(self):
        status.write_feed_status("demo_csv", "ok")

        health = status.feed_health(status.read_feed_status(), dt.datetime(2026, 5, 23, 10, 0))

        self.assertEqual(health["label"], "市場外")

    def test_feed_health_marks_degraded_as_warning(self):
        status.write_feed_status("rakuten_rss", "degraded")

        health = status.feed_health(status.read_feed_status(), dt.datetime(2026, 5, 20, 10, 0))

        self.assertEqual(health["label"], "低下")
        self.assertEqual(health["tone"], "warn")

    def test_http_status_includes_auto_power_status(self):
        status.AUTO_POWER_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        status.AUTO_POWER_STATUS_FILE.write_text(
            '{"ok": true, "source": "phone_wake", "message": "phone wake flag accepted"}',
            encoding="utf-8",
        )

        data = status.build_http_status([], {}, dt.datetime(2026, 5, 23, 10, 0))

        self.assertEqual(data["pc_power"]["source"], "phone_wake")
        self.assertTrue(data["pc_power"]["ok"])

    def test_http_status_includes_feed_recover_status(self):
        status.FEED_RECOVER_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        status.FEED_RECOVER_STATUS_FILE.write_text(
            '{"consecutive_restarts": 2, "last_reason": "feed bar stale"}',
            encoding="utf-8",
        )

        data = status.build_http_status([], {}, dt.datetime(2026, 5, 23, 10, 0))

        self.assertEqual(data["feed_recover"]["consecutive_restarts"], 2)
        self.assertEqual(data["feed_recover"]["last_reason"], "feed bar stale")


if __name__ == "__main__":
    unittest.main()
