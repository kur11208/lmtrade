from __future__ import annotations

import csv
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

import daytrade_config
import daytrade_runner as runner
import daytrade_screener as screener


def write_json(path: Path, data: dict | list):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_bars(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        writer.writerows(rows)


class DaytradeScreenerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old_data_dir = runner.DATA_DIR
        self.old_symbols_file = runner.SYMBOLS_FILE
        self.old_config_file = daytrade_config.CONFIG_FILE
        runner.DATA_DIR = self.root / "data_daytrade"
        runner.SYMBOLS_FILE = runner.DATA_DIR / "symbols.json"
        daytrade_config.CONFIG_FILE = runner.DATA_DIR / "runtime_config.json"
        runner.DATA_DIR.mkdir()
        runner.SETTINGS_CACHE.clear()
        runner.BAR_CACHE.clear()

    def tearDown(self):
        runner.DATA_DIR = self.old_data_dir
        runner.SYMBOLS_FILE = self.old_symbols_file
        daytrade_config.CONFIG_FILE = self.old_config_file
        runner.SETTINGS_CACHE.clear()
        runner.BAR_CACHE.clear()
        self.tmp.cleanup()

    def test_score_function_boundaries(self):
        self.assertEqual(screener.score_liquidity(0), 0.0)
        self.assertEqual(screener.score_range(0), 0.0)
        self.assertEqual(screener.score_volume_ratio(None), 4.0)
        self.assertEqual(screener.score_volume_ratio(3.0), 20.0)
        self.assertGreater(screener.score_short_vwap_position(-0.01), screener.score_short_vwap_position(0.01))
        self.assertGreater(screener.score_short_breakdown_position(-0.01), screener.score_short_breakdown_position(0.02))

    def test_apply_top_keeps_open_position_and_does_not_skip_symbol_loss_state(self):
        now = dt.datetime(2026, 5, 20, 9, 41)
        write_json(runner.SYMBOLS_FILE, [
            {"symbol": "A", "enabled": True},
            {"symbol": "B", "enabled": True},
            {"symbol": "C", "enabled": False},
            {"symbol": "D", "enabled": False},
        ])
        write_json(runner.DATA_DIR / "A" / "paper_state_daytrade.json", {
            "symbol": "A",
            "current_position_today": "LONG",
            "latest_date": "2026-05-20",
        })
        write_json(runner.DATA_DIR / "B" / "paper_state_daytrade.json", {
            "symbol": "B",
            "current_position_today": "FLAT",
            "latest_date": "2026-05-20",
            "disabled_after_loss": True,
        })

        results = [
            {"symbol": "B", "score": 90.0, "reasons": ["ok"]},
            {"symbol": "C", "score": 80.0, "reasons": ["ok"]},
            {"symbol": "D", "score": 70.0, "reasons": ["ok"]},
            {"symbol": "A", "score": 10.0, "reasons": ["ok"]},
        ]
        screener.apply_top(results, top_n=2, now=now)

        rows = runner.load_json(runner.SYMBOLS_FILE, [])
        enabled = {row["symbol"] for row in rows if row.get("enabled")}
        self.assertEqual(enabled, {"A", "B", "C"})
        self.assertTrue(next(row for row in rows if row["symbol"] == "A")["screener_kept_open_position"])
        self.assertTrue(next(row for row in rows if row["symbol"] == "B")["enabled"])

    def test_apply_top_skips_gap_too_large(self):
        now = dt.datetime(2026, 5, 20, 9, 41)
        write_json(runner.SYMBOLS_FILE, [
            {"symbol": "A", "enabled": True},
            {"symbol": "B", "enabled": True},
        ])

        results = [
            {"symbol": "A", "score": 90.0, "reasons": ["gap too large"]},
            {"symbol": "B", "score": 70.0, "reasons": ["ok"]},
        ]
        screener.apply_top(results, top_n=1, now=now)

        rows = runner.load_json(runner.SYMBOLS_FILE, [])
        enabled = [row["symbol"] for row in rows if row.get("enabled")]
        self.assertEqual(enabled, ["B"])

    def test_apply_top_zero_selects_all_selectable_symbols(self):
        now = dt.datetime(2026, 5, 20, 9, 41)
        write_json(runner.SYMBOLS_FILE, [
            {"symbol": "A", "enabled": False},
            {"symbol": "B", "enabled": False},
            {"symbol": "C", "enabled": True},
            {"symbol": "D", "enabled": True},
        ])

        results = [
            {"symbol": "A", "score": 90.0, "reasons": ["ok"]},
            {"symbol": "B", "score": 70.0, "reasons": ["ok"]},
            {"symbol": "C", "score": 0.0, "reasons": ["no intraday bars"]},
            {"symbol": "D", "score": 60.0, "reasons": ["opening range incomplete"]},
        ]
        screener.apply_top(results, top_n=0, now=now)

        rows = runner.load_json(runner.SYMBOLS_FILE, [])
        enabled = {row["symbol"] for row in rows if row.get("enabled")}
        self.assertEqual(enabled, {"A", "B", "D"})

    def test_apply_top_no_selection_disables_non_open_positions(self):
        now = dt.datetime(2026, 5, 20, 9, 41)
        write_json(runner.SYMBOLS_FILE, [
            {"symbol": "A", "enabled": True},
            {"symbol": "B", "enabled": True},
        ])
        write_json(runner.DATA_DIR / "B" / "paper_state_daytrade.json", {
            "symbol": "B",
            "current_position_today": "LONG",
        })

        screener.apply_top([
            {"symbol": "A", "score": 0.0, "reasons": ["no intraday bars"]},
            {"symbol": "B", "score": 0.0, "reasons": ["no intraday bars"]},
        ], top_n=1, now=now)

        rows = runner.load_json(runner.SYMBOLS_FILE, [])
        by_symbol = {row["symbol"]: row for row in rows}
        self.assertFalse(by_symbol["A"]["enabled"])
        self.assertTrue(by_symbol["B"]["enabled"])
        self.assertTrue(by_symbol["B"]["screener_kept_open_position"])

    def test_short_screen_scores_breakdown_above_breakout(self):
        write_json(runner.SYMBOLS_FILE, [
            {"symbol": "WEAK", "enabled": True},
            {"symbol": "STRONG", "enabled": True},
        ])
        write_bars(runner.DATA_DIR / "WEAK" / "intraday_bars.csv", [
            {"timestamp": "2026-05-20 09:00:00", "open": 100.0, "high": 100.5, "low": 99.0, "close": 100.0, "volume": 1000},
            {"timestamp": "2026-05-20 09:10:00", "open": 100.0, "high": 100.2, "low": 98.8, "close": 99.0, "volume": 1200},
            {"timestamp": "2026-05-20 09:30:00", "open": 99.0, "high": 99.1, "low": 97.5, "close": 98.0, "volume": 5000},
            {"timestamp": "2026-05-20 09:40:00", "open": 98.0, "high": 98.2, "low": 96.8, "close": 97.0, "volume": 6000},
        ])
        write_bars(runner.DATA_DIR / "STRONG" / "intraday_bars.csv", [
            {"timestamp": "2026-05-20 09:00:00", "open": 100.0, "high": 101.0, "low": 99.5, "close": 100.0, "volume": 1000},
            {"timestamp": "2026-05-20 09:10:00", "open": 100.0, "high": 101.2, "low": 99.8, "close": 101.0, "volume": 1200},
            {"timestamp": "2026-05-20 09:30:00", "open": 101.0, "high": 102.5, "low": 100.9, "close": 102.0, "volume": 5000},
            {"timestamp": "2026-05-20 09:40:00", "open": 102.0, "high": 103.2, "low": 101.8, "close": 103.0, "volume": 6000},
        ])

        results = screener.run_screen(top_n=2, apply=False, now=dt.datetime(2026, 5, 20, 9, 41))

        by_symbol = {row["symbol"]: row for row in results}
        self.assertGreater(by_symbol["WEAK"]["score"], by_symbol["STRONG"]["score"])
        self.assertLess(by_symbol["WEAK"]["metrics"]["vwap_position"], 0)
        self.assertLess(by_symbol["WEAK"]["metrics"]["breakdown_distance"], 0)

    def test_short_score_not_added_when_short_disabled(self):
        write_json(runner.SYMBOLS_FILE, [{"symbol": "WEAK", "enabled": True}])
        write_json(runner.DATA_DIR / "WEAK" / "paper_settings.json", {
            "allow_short": False,
            "use_gap_filter": False,
        })
        write_bars(runner.DATA_DIR / "WEAK" / "intraday_bars.csv", [
            {"timestamp": "2026-05-20 09:00:00", "open": 100.0, "high": 100.5, "low": 99.0, "close": 100.0, "volume": 1000},
            {"timestamp": "2026-05-20 09:10:00", "open": 100.0, "high": 100.2, "low": 98.8, "close": 99.0, "volume": 1200},
            {"timestamp": "2026-05-20 09:30:00", "open": 99.0, "high": 99.1, "low": 97.5, "close": 98.0, "volume": 5000},
            {"timestamp": "2026-05-20 09:40:00", "open": 98.0, "high": 98.2, "low": 96.8, "close": 97.0, "volume": 6000},
        ])

        disabled = screener.score_symbol({"symbol": "WEAK"})
        write_json(runner.DATA_DIR / "WEAK" / "paper_settings.json", {
            "allow_short": True,
            "use_gap_filter": False,
        })
        runner.SETTINGS_CACHE.clear()
        enabled = screener.score_symbol({"symbol": "WEAK"})

        self.assertGreater(enabled["score"], disabled["score"])

    def test_scheduler_applies_once_inside_refresh_window(self):
        now = dt.datetime(2026, 5, 20, 9, 41)
        write_json(runner.SYMBOLS_FILE, [
            {"symbol": "A", "enabled": False},
            {"symbol": "B", "enabled": False},
        ])
        write_json(runner.DATA_DIR / "runtime_config.json", {
            "screener_auto_apply": True,
            "screener_top_n": 1,
            "screener_refresh_times": ["09:40"],
            "screener_refresh_window_minutes": 20,
        })
        write_bars(runner.DATA_DIR / "A" / "intraday_bars.csv", [
            {"timestamp": "2026-05-20 09:00:00", "open": 100.0, "high": 101.0, "low": 99.5, "close": 100.5, "volume": 1000},
            {"timestamp": "2026-05-20 09:30:00", "open": 100.5, "high": 104.0, "low": 100.4, "close": 103.0, "volume": 8000},
            {"timestamp": "2026-05-20 09:40:00", "open": 103.0, "high": 105.0, "low": 102.8, "close": 104.5, "volume": 9000},
        ])
        write_bars(runner.DATA_DIR / "B" / "intraday_bars.csv", [
            {"timestamp": "2026-05-20 09:00:00", "open": 100.0, "high": 100.3, "low": 99.8, "close": 100.1, "volume": 100},
            {"timestamp": "2026-05-20 09:30:00", "open": 100.1, "high": 100.4, "low": 99.9, "close": 100.2, "volume": 100},
            {"timestamp": "2026-05-20 09:40:00", "open": 100.2, "high": 100.5, "low": 100.0, "close": 100.3, "volume": 100},
        ])

        first = screener.scheduler_tick(now=now)
        second = screener.scheduler_tick(now=now + dt.timedelta(minutes=1))

        rows = runner.load_json(runner.SYMBOLS_FILE, [])
        enabled = [row["symbol"] for row in rows if row.get("enabled")]
        self.assertEqual(enabled, ["B"])
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])

    def test_scheduler_retries_without_disabling_when_no_data(self):
        now = dt.datetime(2026, 5, 20, 9, 41)
        write_json(runner.SYMBOLS_FILE, [
            {"symbol": "A", "enabled": True},
            {"symbol": "B", "enabled": True},
        ])
        write_json(runner.DATA_DIR / "runtime_config.json", {
            "screener_auto_apply": True,
            "screener_top_n": 1,
            "screener_refresh_times": ["09:40"],
            "screener_refresh_window_minutes": 20,
        })

        ran = screener.scheduler_tick(now=now)

        rows = runner.load_json(runner.SYMBOLS_FILE, [])
        enabled = {row["symbol"] for row in rows if row.get("enabled")}
        state = runner.load_json(screener.schedule_state_file(), {})
        self.assertEqual(ran, [])
        self.assertEqual(enabled, {"A", "B"})
        self.assertEqual(state, {})


if __name__ == "__main__":
    unittest.main()
