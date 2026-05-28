from __future__ import annotations

import datetime as dt
import unittest

import daytrade_market_calendar as cal


class DaytradeMarketCalendarTest(unittest.TestCase):
    def test_weekend_and_exchange_holidays_are_closed(self):
        self.assertFalse(cal.is_trading_day(dt.date(2026, 5, 23)))
        self.assertFalse(cal.is_trading_day(dt.date(2026, 1, 2)))
        self.assertFalse(cal.is_trading_day(dt.date(2026, 2, 11)))

    def test_regular_weekday_session_is_open(self):
        self.assertTrue(cal.is_trading_day(dt.date(2026, 5, 21)))
        self.assertTrue(cal.is_market_time(dt.datetime(2026, 5, 21, 9, 30)))
        self.assertFalse(cal.is_market_time(dt.datetime(2026, 5, 21, 12, 0)))

    def test_next_market_open_skips_weekend(self):
        next_open = cal.next_market_open(dt.datetime(2026, 5, 23, 10, 0))

        self.assertEqual(next_open, dt.datetime(2026, 5, 25, 9, 0))


if __name__ == "__main__":
    unittest.main()
