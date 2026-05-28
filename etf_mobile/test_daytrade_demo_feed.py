from __future__ import annotations

import datetime as dt
import unittest

import daytrade_demo_feed as feed


class DaytradeDemoFeedTest(unittest.TestCase):
    def test_realtime_feed_does_not_create_future_bar_in_same_interval(self):
        now_slot = feed.floor_to_interval(dt.datetime.now(), 1)
        rows = [{"ts": now_slot}]

        next_ts = feed.choose_next_ts(rows, interval_minutes=1, realtime=True)

        if feed.is_market_time(now_slot):
            self.assertIsNone(next_ts)

    def test_non_realtime_feed_can_backfill_next_market_bar(self):
        rows = [{"ts": dt.datetime(2026, 5, 20, 9, 0)}]

        next_ts = feed.choose_next_ts(rows, interval_minutes=1, realtime=False)

        self.assertEqual(next_ts, dt.datetime(2026, 5, 20, 9, 1))


if __name__ == "__main__":
    unittest.main()
