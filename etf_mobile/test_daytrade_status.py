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
        status.set_data_dir(Path(self.tmp.name) / "data_daytrade")

    def tearDown(self):
        status.DATA_DIR = self.old_data_dir
        status.FEED_STATUS_FILE = self.old_feed
        status.RUNNER_STATUS_FILE = self.old_runner
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


if __name__ == "__main__":
    unittest.main()
