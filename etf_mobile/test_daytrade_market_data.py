from __future__ import annotations

import unittest

import daytrade_market_data as md


class DaytradeMarketDataTest(unittest.TestCase):
    def test_api_adapter_is_market_data_only_stub(self):
        result = md.get_adapter("api").fetch_latest_bars(["1570"])

        self.assertEqual(result.adapter, "api")
        self.assertEqual(result.status, "not_configured")
        self.assertEqual(result.bars_written, 0)

    def test_unknown_adapter_falls_back_to_csv(self):
        result = md.get_adapter("unknown").fetch_latest_bars(["1570"])

        self.assertEqual(result.adapter, "csv")


if __name__ == "__main__":
    unittest.main()
