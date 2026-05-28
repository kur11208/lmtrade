from __future__ import annotations

import csv
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

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
        runner.DATA_DIR = self.root / "data_daytrade"
        runner.SYMBOLS_FILE = runner.DATA_DIR / "symbols.json"
        runner.DATA_DIR.mkdir()
        runner.SETTINGS_CACHE.clear()
        runner.BAR_CACHE.clear()

    def tearDown(self):
        runner.DATA_DIR = self.old_data_dir
        runner.SYMBOLS_FILE = self.old_symbols_file
        runner.SETTINGS_CACHE.clear()
        runner.BAR_CACHE.clear()
        self.tmp.cleanup()

    def test_apply_top_keeps_open_position_and_skips_daily_stopped(self):
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
        self.assertEqual(enabled, {"A", "C", "D"})
        self.assertTrue(next(row for row in rows if row["symbol"] == "A")["screener_kept_open_position"])
        self.assertFalse(next(row for row in rows if row["symbol"] == "B")["enabled"])

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
        self.assertEqual(enabled, ["A"])
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
