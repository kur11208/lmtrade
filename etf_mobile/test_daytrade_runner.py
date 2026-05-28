from __future__ import annotations

import csv
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

import daytrade_runner as runner


def write_json(path: Path, data: dict | list):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_bars(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        writer.writerows(rows)


class DaytradeRunnerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old_data_dir = runner.DATA_DIR
        self.old_symbols_file = runner.SYMBOLS_FILE
        runner.DATA_DIR = self.root / "data_daytrade"
        runner.SYMBOLS_FILE = runner.DATA_DIR / "symbols.json"
        runner.SETTINGS_CACHE.clear()
        runner.BAR_CACHE.clear()
        runner.HISTORY_TS_CACHE.clear()
        runner.DATA_DIR.mkdir()

    def tearDown(self):
        runner.DATA_DIR = self.old_data_dir
        runner.SYMBOLS_FILE = self.old_symbols_file
        runner.SETTINGS_CACHE.clear()
        runner.BAR_CACHE.clear()
        runner.HISTORY_TS_CACHE.clear()
        self.tmp.cleanup()

    def setup_symbol(self, rows, settings=None):
        symbol = "TEST"
        sym_dir = runner.DATA_DIR / symbol
        write_json(runner.SYMBOLS_FILE, [{"symbol": symbol, "enabled": True}])
        base_settings = dict(runner.DEFAULT_SETTINGS)
        base_settings.update({
            "opening_range_minutes": 30,
            "max_position_w": 0.50,
            "risk_per_trade_rate": 0.005,
            "daily_loss_limit_rate": 0.01,
            "fee_rate": 0.0,
            "slippage_rate": 0.0,
            "spread_rate": 0.0,
            "tick_size": 0.01,
            "use_gap_filter": False,
            "use_market_filter": False,
            "min_volume_ratio": 1.2,
        })
        if settings:
            base_settings.update(settings)
        write_json(sym_dir / "paper_settings.json", base_settings)
        write_json(sym_dir / "paper_state_daytrade.json", {
            "symbol": symbol,
            "equity": 1.0,
            "base_equity": 100.0,
            "current_position_today": "FLAT",
            "current_w_today": 0.0,
        })
        write_json(sym_dir / "mobile_runner_state_daytrade.json", {})
        write_bars(sym_dir / "intraday_bars.csv", rows)
        return symbol, sym_dir

    def test_breakout_enters_and_target_exits(self):
        rows = [
            {"timestamp": "2026-05-20 09:00:00", "open": 100.0, "high": 100.6, "low": 99.5, "close": 100.1, "volume": 1000},
            {"timestamp": "2026-05-20 09:05:00", "open": 100.1, "high": 100.8, "low": 99.8, "close": 100.4, "volume": 1200},
            {"timestamp": "2026-05-20 09:10:00", "open": 100.4, "high": 101.0, "low": 100.0, "close": 100.8, "volume": 1200},
            {"timestamp": "2026-05-20 09:20:00", "open": 100.8, "high": 101.2, "low": 100.5, "close": 101.0, "volume": 1200},
            {"timestamp": "2026-05-20 09:30:00", "open": 101.0, "high": 102.4, "low": 100.9, "close": 102.0, "volume": 2000},
            {"timestamp": "2026-05-20 09:35:00", "open": 102.0, "high": 104.0, "low": 101.9, "close": 103.5, "volume": 2200},
        ]
        symbol, _ = self.setup_symbol(rows, settings={"take_profit_r": 1.0, "stop_buffer_rate": 0.01})

        runner.tick_symbol(symbol, replay_all=True, now=dt.datetime(2026, 5, 20, 10, 0))

        state = runner.load_json(runner.DATA_DIR / symbol / "paper_state_daytrade.json", {})
        self.assertEqual(state["current_position_today"], "FLAT")
        self.assertEqual(state["last_action"], "SELL_TARGET")
        self.assertGreater(state["equity"], 1.0)
        self.assertEqual(state["trade_count_today"], 1)

    def test_force_flat_before_close(self):
        rows = [
            {"timestamp": "2026-05-20 09:00:00", "open": 100.0, "high": 100.6, "low": 99.5, "close": 100.1, "volume": 1000},
            {"timestamp": "2026-05-20 09:10:00", "open": 100.1, "high": 101.0, "low": 99.8, "close": 100.8, "volume": 1000},
            {"timestamp": "2026-05-20 09:25:00", "open": 100.8, "high": 101.1, "low": 100.4, "close": 101.0, "volume": 1000},
            {"timestamp": "2026-05-20 09:30:00", "open": 101.0, "high": 102.2, "low": 100.9, "close": 102.0, "volume": 2000},
            {"timestamp": "2026-05-20 14:56:00", "open": 102.2, "high": 102.4, "low": 101.8, "close": 102.1, "volume": 2000},
        ]
        symbol, _ = self.setup_symbol(rows, settings={"take_profit_r": 10.0, "force_flat_time": "14:55"})

        runner.tick_symbol(symbol, replay_all=True, now=dt.datetime(2026, 5, 20, 15, 0))

        state = runner.load_json(runner.DATA_DIR / symbol / "paper_state_daytrade.json", {})
        self.assertEqual(state["current_position_today"], "FLAT")
        self.assertEqual(state["last_action"], "SELL_EOD")
        self.assertEqual(state["signal"], "EXIT")

    def test_low_volume_breakout_is_blocked(self):
        rows = [
            {"timestamp": "2026-05-20 09:00:00", "open": 100.0, "high": 100.6, "low": 99.5, "close": 100.1, "volume": 3000},
            {"timestamp": "2026-05-20 09:05:00", "open": 100.1, "high": 100.8, "low": 99.8, "close": 100.4, "volume": 3200},
            {"timestamp": "2026-05-20 09:10:00", "open": 100.4, "high": 101.0, "low": 100.0, "close": 100.8, "volume": 3100},
            {"timestamp": "2026-05-20 09:20:00", "open": 100.8, "high": 101.2, "low": 100.5, "close": 101.0, "volume": 3000},
            {"timestamp": "2026-05-20 09:30:00", "open": 101.0, "high": 102.4, "low": 100.9, "close": 102.0, "volume": 1000},
        ]
        symbol, _ = self.setup_symbol(rows, settings={"min_volume_ratio": 1.2})

        runner.tick_symbol(symbol, replay_all=True, now=dt.datetime(2026, 5, 20, 10, 0))

        state = runner.load_json(runner.DATA_DIR / symbol / "paper_state_daytrade.json", {})
        self.assertEqual(state["current_position_today"], "FLAT")
        self.assertEqual(state["signal"], "BLOCKED")
        self.assertEqual(state["strategy_reason"], "volume confirmation not met")

    def test_partial_profit_reduces_position(self):
        rows = [
            {"timestamp": "2026-05-20 09:00:00", "open": 100.0, "high": 100.6, "low": 99.5, "close": 100.1, "volume": 1000},
            {"timestamp": "2026-05-20 09:05:00", "open": 100.1, "high": 100.8, "low": 99.8, "close": 100.4, "volume": 1200},
            {"timestamp": "2026-05-20 09:10:00", "open": 100.4, "high": 101.0, "low": 100.0, "close": 100.8, "volume": 1200},
            {"timestamp": "2026-05-20 09:20:00", "open": 100.8, "high": 101.2, "low": 100.5, "close": 101.0, "volume": 1200},
            {"timestamp": "2026-05-20 09:30:00", "open": 101.0, "high": 102.4, "low": 100.9, "close": 102.0, "volume": 2000},
            {"timestamp": "2026-05-20 09:35:00", "open": 102.0, "high": 103.2, "low": 101.9, "close": 103.0, "volume": 2200},
        ]
        symbol, _ = self.setup_symbol(rows, settings={
            "take_profit_r": 5.0,
            "partial_take_profit_r": 1.0,
            "partial_exit_rate": 0.5,
            "trailing_stop_r": 0.0,
            "stop_buffer_rate": 0.01,
        })

        runner.tick_symbol(symbol, replay_all=True, now=dt.datetime(2026, 5, 20, 10, 0))

        state = runner.load_json(runner.DATA_DIR / symbol / "paper_state_daytrade.json", {})
        self.assertEqual(state["current_position_today"], "LONG")
        self.assertEqual(state["last_action"], "SELL_PARTIAL_TARGET")
        self.assertTrue(state["partial_exited"])
        self.assertLess(state["current_w_today"], 0.5)

    def test_same_timestamp_update_can_stop_open_position(self):
        rows = [
            {"timestamp": "2026-05-20 09:30:00", "open": 100.0, "high": 101.0, "low": 98.0, "close": 98.5, "volume": 2000},
        ]
        symbol, sym_dir = self.setup_symbol(rows)
        write_json(sym_dir / "paper_state_daytrade.json", {
            "symbol": symbol,
            "latest_date": "2026-05-20",
            "latest_ts": "2026-05-20 09:30:00",
            "equity": 1.0,
            "entry_equity": 1.0,
            "trade_start_equity": 1.0,
            "current_position_today": "LONG",
            "current_w_today": 0.5,
            "entry_price": 100.0,
            "entry_ts": "2026-05-20 09:30:00",
            "stop_price": 99.0,
            "target_price": 110.0,
            "partial_target_price": 105.0,
            "initial_risk_price": 1.0,
            "closed_trade_count": 0,
        })
        write_json(sym_dir / "mobile_runner_state_daytrade.json", {
            "last_processed_ts": "2026-05-20 09:30:00",
        })

        runner.tick_symbol(symbol, now=dt.datetime(2026, 5, 20, 9, 30, 30))

        state = runner.load_json(sym_dir / "paper_state_daytrade.json", {})
        self.assertEqual(state["current_position_today"], "FLAT")
        self.assertEqual(state["last_action"], "SELL_STOP")
        self.assertEqual(state["signal"], "EXIT")

    def test_stale_overnight_position_is_forced_flat(self):
        rows = [
            {"timestamp": "2026-05-20 10:58:00", "open": 100.0, "high": 101.0, "low": 99.8, "close": 100.5, "volume": 2000},
        ]
        symbol, sym_dir = self.setup_symbol(rows)
        write_json(sym_dir / "paper_state_daytrade.json", {
            "symbol": symbol,
            "latest_date": "2026-05-20",
            "latest_ts": "2026-05-20 10:58:00",
            "equity": 1.0,
            "entry_equity": 1.0,
            "trade_start_equity": 1.0,
            "current_position_today": "LONG",
            "current_w_today": 0.5,
            "entry_price": 100.0,
            "entry_ts": "2026-05-20 10:50:00",
            "stop_price": 98.0,
            "target_price": 110.0,
            "partial_target_price": 105.0,
            "initial_risk_price": 2.0,
            "closed_trade_count": 0,
        })
        write_json(sym_dir / "mobile_runner_state_daytrade.json", {
            "last_processed_ts": "2026-05-20 10:58:00",
        })

        runner.tick_symbol(symbol, now=dt.datetime(2026, 5, 21, 9, 0, 0))

        state = runner.load_json(sym_dir / "paper_state_daytrade.json", {})
        self.assertEqual(state["current_position_today"], "FLAT")
        self.assertEqual(state["last_action"], "SELL_STALE_EOD")

    def test_freshness_is_market_closed_outside_session(self):
        state = {"latest_ts": "2026-05-22 15:30:00", "current_position_today": "FLAT"}

        updated = runner.update_freshness_fields(state, dt.datetime(2026, 5, 23, 10, 0), runner.DEFAULT_SETTINGS)

        self.assertEqual(updated["data_freshness_status"], "market_closed")
        self.assertFalse(updated["data_stale"])


if __name__ == "__main__":
    unittest.main()
