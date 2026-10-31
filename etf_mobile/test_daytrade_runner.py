from __future__ import annotations

import csv
import datetime as dt
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import daytrade_config
import daytrade_order_queue as order_queue
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
        self.old_config_file = daytrade_config.CONFIG_FILE
        self.old_status_data_dir = runner.daytrade_status.DATA_DIR
        runner.DATA_DIR = self.root / "data_daytrade"
        runner.SYMBOLS_FILE = runner.DATA_DIR / "symbols.json"
        daytrade_config.CONFIG_FILE = runner.DATA_DIR / "runtime_config.json"
        runner.daytrade_status.set_data_dir(runner.DATA_DIR)
        runner.SETTINGS_CACHE.clear()
        runner.BAR_CACHE.clear()
        runner.HISTORY_TS_CACHE.clear()
        runner.ALERT_KEYS_CACHE.clear()
        runner.DATA_DIR.mkdir()

    def tearDown(self):
        runner.DATA_DIR = self.old_data_dir
        runner.SYMBOLS_FILE = self.old_symbols_file
        daytrade_config.CONFIG_FILE = self.old_config_file
        runner.daytrade_status.set_data_dir(self.old_status_data_dir)
        runner.SETTINGS_CACHE.clear()
        runner.BAR_CACHE.clear()
        runner.HISTORY_TS_CACHE.clear()
        runner.ALERT_KEYS_CACHE.clear()
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
            "allow_long": True,
            "allow_short": True,
            "use_gap_filter": False,
            "use_market_filter": False,
            "min_volume_ratio": 1.2,
            "quality_min_opening_bar_count": 1,
            "quality_max_opening_gap_minutes": 0,
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
        self.assertAlmostEqual(state["sim_pnl_rate"], state["equity"] - 1.0)
        self.assertAlmostEqual(state["daily_pnl_rate"], state["equity"] - 1.0)
        self.assertEqual(state["trade_count_today"], 1)

    def test_breakdown_enters_short_and_target_exits(self):
        rows = [
            {"timestamp": "2026-05-20 09:00:00", "open": 100.0, "high": 100.5, "low": 99.6, "close": 100.1, "volume": 1000},
            {"timestamp": "2026-05-20 09:05:00", "open": 100.1, "high": 100.2, "low": 99.4, "close": 99.7, "volume": 1200},
            {"timestamp": "2026-05-20 09:10:00", "open": 99.7, "high": 99.8, "low": 99.0, "close": 99.2, "volume": 1200},
            {"timestamp": "2026-05-20 09:20:00", "open": 99.2, "high": 99.5, "low": 98.9, "close": 99.1, "volume": 1200},
            {"timestamp": "2026-05-20 09:30:00", "open": 99.1, "high": 99.2, "low": 98.3, "close": 98.5, "volume": 2200},
            {"timestamp": "2026-05-20 09:35:00", "open": 98.5, "high": 98.6, "low": 97.0, "close": 97.2, "volume": 2400},
        ]
        symbol, _ = self.setup_symbol(rows, settings={"take_profit_r": 1.0, "stop_buffer_rate": 0.01})

        runner.tick_symbol(symbol, replay_all=True, now=dt.datetime(2026, 5, 20, 10, 0))

        state = runner.load_json(runner.DATA_DIR / symbol / "paper_state_daytrade.json", {})
        self.assertEqual(state["current_position_today"], "FLAT")
        self.assertEqual(state["last_action"], "BUY_TARGET")
        self.assertGreater(state["equity"], 1.0)
        self.assertAlmostEqual(state["sim_pnl_rate"], state["equity"] - 1.0)
        self.assertAlmostEqual(state["daily_pnl_rate"], state["equity"] - 1.0)
        self.assertEqual(state["trade_count_today"], 1)

    def test_append_alert_if_needed_deduplicates_with_cache(self):
        sym_dir = runner.DATA_DIR / "TEST"
        state = {
            "symbol": "TEST",
            "latest_ts": "2026-05-20 09:30:00",
            "signal": "ENTRY",
            "last_action": "SELL_BREAKDOWN",
            "last_price": 100.0,
            "strategy_reason": "test",
        }

        runner.append_alert_if_needed(sym_dir, state)
        runner.append_alert_if_needed(sym_dir, state)

        with (sym_dir / "alerts_daytrade.csv").open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 1)

    def test_recent_trades_keeps_more_than_twenty_for_portfolio_risk(self):
        state = {"current_position_today": "LONG", "closed_trade_count": 0, "recent_trades": []}
        for idx in range(25):
            runner.record_trade(
                state=state,
                exit_ts=f"2026-05-20 10:{idx:02d}:00",
                exit_price=100.0,
                trade_return=-0.001,
                pnl_rate=-0.001,
                reason="STOP",
                entry_ts=f"2026-05-20 09:{idx:02d}:00",
            )

        self.assertEqual(len(state["recent_trades"]), 25)

    def test_tick_all_keeps_running_when_one_symbol_fails(self):
        write_json(runner.SYMBOLS_FILE, [
            {"symbol": "OK", "enabled": True},
            {"symbol": "BAD", "enabled": True},
        ])

        def fake_tick(symbol, replay_all=False):
            if symbol == "BAD":
                raise RuntimeError("boom")
            return {"symbol": symbol, "processed": 1, "state": {}}

        with mock.patch.object(runner, "tick_symbol", side_effect=fake_tick):
            results = runner.tick_all()

        self.assertEqual([row["symbol"] for row in results], ["OK"])
        status = runner.load_json(runner.DATA_DIR / "runner_status.json", {})
        self.assertEqual(status["status"], "error")
        self.assertIn("BAD: RuntimeError: boom", status["last_error"])

        log_path = runner.DATA_DIR / "evaluation_log.jsonl"
        self.assertTrue(log_path.exists())
        rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows[-1]["kind"], "daytrade_evaluation")
        self.assertEqual(rows[-1]["symbols_requested"], 2)
        self.assertEqual(rows[-1]["symbols_processed"], 1)
        self.assertIn("BAD: RuntimeError: boom", rows[-1]["errors"][0])

    def test_short_market_filter_allows_weak_market(self):
        market_rows = [
            {"timestamp": "2026-05-20 09:00:00", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000},
            {"timestamp": "2026-05-20 09:30:00", "open": 99.0, "high": 99.0, "low": 98.0, "close": 98.0, "volume": 1000},
        ]
        market_dir = runner.DATA_DIR / "1570"
        write_bars(market_dir / "intraday_bars.csv", market_rows)

        rows = [
            {"timestamp": "2026-05-20 09:00:00", "open": 100.0, "high": 100.5, "low": 99.6, "close": 100.1, "volume": 1000},
            {"timestamp": "2026-05-20 09:05:00", "open": 100.1, "high": 100.2, "low": 99.4, "close": 99.7, "volume": 1200},
            {"timestamp": "2026-05-20 09:10:00", "open": 99.7, "high": 99.8, "low": 99.0, "close": 99.2, "volume": 1200},
            {"timestamp": "2026-05-20 09:20:00", "open": 99.2, "high": 99.5, "low": 98.9, "close": 99.1, "volume": 1200},
            {"timestamp": "2026-05-20 09:30:00", "open": 99.1, "high": 99.2, "low": 98.3, "close": 98.5, "volume": 2200},
        ]
        symbol, _ = self.setup_symbol(rows, settings={
            "allow_long": False,
            "allow_short": True,
            "use_market_filter": True,
            "market_filter_symbol": "1570",
        })

        runner.tick_symbol(symbol, replay_all=True, now=dt.datetime(2026, 5, 20, 10, 0))

        state = runner.load_json(runner.DATA_DIR / symbol / "paper_state_daytrade.json", {})
        self.assertEqual(state["current_position_today"], "SHORT")
        self.assertEqual(state["market_filter_side"], "short")
        self.assertEqual(state["market_filter_reason"], "market ok")

    def test_short_market_filter_blocks_strong_market(self):
        market_rows = [
            {"timestamp": "2026-05-20 09:00:00", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000},
            {"timestamp": "2026-05-20 09:30:00", "open": 102.0, "high": 103.0, "low": 102.0, "close": 103.0, "volume": 1000},
        ]
        market_dir = runner.DATA_DIR / "1570"
        write_bars(market_dir / "intraday_bars.csv", market_rows)

        rows = [
            {"timestamp": "2026-05-20 09:00:00", "open": 100.0, "high": 100.5, "low": 99.6, "close": 100.1, "volume": 1000},
            {"timestamp": "2026-05-20 09:05:00", "open": 100.1, "high": 100.2, "low": 99.4, "close": 99.7, "volume": 1200},
            {"timestamp": "2026-05-20 09:10:00", "open": 99.7, "high": 99.8, "low": 99.0, "close": 99.2, "volume": 1200},
            {"timestamp": "2026-05-20 09:20:00", "open": 99.2, "high": 99.5, "low": 98.9, "close": 99.1, "volume": 1200},
            {"timestamp": "2026-05-20 09:30:00", "open": 99.1, "high": 99.2, "low": 98.3, "close": 98.5, "volume": 2200},
        ]
        symbol, _ = self.setup_symbol(rows, settings={
            "allow_long": False,
            "allow_short": True,
            "use_market_filter": True,
            "market_filter_symbol": "1570",
        })

        runner.tick_symbol(symbol, replay_all=True, now=dt.datetime(2026, 5, 20, 10, 0))

        state = runner.load_json(runner.DATA_DIR / symbol / "paper_state_daytrade.json", {})
        self.assertEqual(state["current_position_today"], "FLAT")
        self.assertEqual(state["signal"], "BLOCKED")
        self.assertEqual(state["strategy_reason"], "market filter not met")
        self.assertEqual(state["market_filter_side"], "short")
        self.assertIn("market return strong", state["market_filter_reason"])

    def test_new_day_preserves_cumulative_equity(self):
        rows = [
            {"timestamp": "2026-05-21 09:00:00", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000},
        ]
        symbol, sym_dir = self.setup_symbol(rows)
        write_json(sym_dir / "paper_state_daytrade.json", {
            "symbol": symbol,
            "latest_date": "2026-05-20",
            "equity": 0.9985,
            "closed_trade_count": 1,
            "win_count": 0,
            "loss_count": 1,
            "gross_profit_rate": 0.0,
            "gross_loss_rate": 0.0015,
            "current_position_today": "FLAT",
            "current_w_today": 0.0,
        })

        runner.tick_symbol(symbol, replay_all=False, now=dt.datetime(2026, 5, 21, 10, 0))

        state = runner.load_json(runner.DATA_DIR / symbol / "paper_state_daytrade.json", {})
        self.assertAlmostEqual(state["equity"], 0.9985)
        self.assertAlmostEqual(state["day_start_equity"], 0.9985)
        self.assertAlmostEqual(state["sim_pnl_rate"], -0.0015)
        self.assertAlmostEqual(state["daily_pnl_rate"], 0.0)

    def test_missing_equity_uses_day_start_equity(self):
        state = runner.update_sim_fields({
            "day_start_equity": 0.9985,
            "sim_capital_yen": 10000.0,
        })

        self.assertAlmostEqual(state["equity"], 0.9985)
        self.assertAlmostEqual(state["sim_pnl_rate"], -0.0015)
        self.assertAlmostEqual(state["daily_pnl_rate"], 0.0)

    def test_volume_ratio_uses_recent_signal_average(self):
        rows = []
        start = dt.datetime(2026, 5, 20, 9, 0)
        for i, volume in enumerate([1000, 1000, 1000, 1000, 1000, 1000, 2000, 2000, 2000]):
            rows.append({
                "ts": start + dt.timedelta(minutes=i),
                "timestamp": (start + dt.timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S"),
                "date": "2026-05-20",
                "open": 100.0,
                "high": 100.0,
                "low": 100.0,
                "close": 100.0,
                "volume": volume,
            })

        self.assertAlmostEqual(runner.calc_volume_ratio(rows, lookback=6, signal_bars=3), 2.0)

    def test_short_gap_filter_allows_moderate_up_gap(self):
        settings = dict(runner.DEFAULT_SETTINGS)
        settings.update({"use_gap_filter": True, "max_short_gap_rate": 0.08})

        self.assertEqual(runner.gap_filter_reason(0.055, settings, "short"), "")
        self.assertEqual(runner.gap_filter_reason(0.055, settings, "long"), "opening gap outside range")

    def test_data_warning_ignores_lunch_break_gap(self):
        rows = [
            {
                "ts": dt.datetime(2026, 5, 20, 11, 30),
                "timestamp": "2026-05-20 11:30:00",
                "date": "2026-05-20",
                "open": 100.0,
                "high": 100.0,
                "low": 100.0,
                "close": 100.0,
                "volume": 1000,
            },
            {
                "ts": dt.datetime(2026, 5, 20, 12, 30),
                "timestamp": "2026-05-20 12:30:00",
                "date": "2026-05-20",
                "open": 100.0,
                "high": 100.0,
                "low": 100.0,
                "close": 100.0,
                "volume": 1000,
            },
        ]

        self.assertEqual(runner.calc_data_warning(rows, max_gap_minutes=3), "")

    def test_data_warning_ignores_closing_auction_gap(self):
        rows = [
            {
                "ts": dt.datetime(2026, 5, 20, 15, 24),
                "timestamp": "2026-05-20 15:24:00",
                "date": "2026-05-20",
                "open": 100.0,
                "high": 100.0,
                "low": 100.0,
                "close": 100.0,
                "volume": 1000,
            },
            {
                "ts": dt.datetime(2026, 5, 20, 15, 30),
                "timestamp": "2026-05-20 15:30:00",
                "date": "2026-05-20",
                "open": 100.0,
                "high": 100.0,
                "low": 100.0,
                "close": 100.0,
                "volume": 1000,
            },
        ]

        self.assertEqual(runner.calc_data_warning(rows, max_gap_minutes=3), "")

    def test_zero_max_trades_does_not_mark_no_new_entries(self):
        rows = [
            {"timestamp": "2026-05-21 09:00:00", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000},
        ]
        symbol, sym_dir = self.setup_symbol(rows, settings={"max_trades_per_day": 0})
        write_json(sym_dir / "paper_state_daytrade.json", {
            "symbol": symbol,
            "latest_date": "2026-05-21",
            "equity": 1.0,
            "trade_count_today": 3,
            "current_position_today": "FLAT",
            "current_w_today": 0.0,
        })

        runner.tick_symbol(symbol, replay_all=True, now=dt.datetime(2026, 5, 21, 10, 0))

        state = runner.load_json(runner.DATA_DIR / symbol / "paper_state_daytrade.json", {})
        self.assertFalse(state["no_new_entries"])

    def test_history_marks_replay_run_mode(self):
        rows = [
            {"timestamp": "2026-05-21 09:00:00", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000},
        ]
        symbol, sym_dir = self.setup_symbol(rows)

        runner.tick_symbol(symbol, replay_all=True, now=dt.datetime(2026, 5, 21, 10, 0))

        with (sym_dir / "paper_history_daytrade.csv").open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(rows[-1]["run_mode"], "replay")

    def test_legacy_history_schema_is_migrated_before_append(self):
        symbol, sym_dir = self.setup_symbol([
            {"timestamp": "2026-05-21 09:00:00", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000},
        ])
        legacy_row = [
            "2026-05-20 15:30:00",
            "1.0",
            "FLAT",
            "0.0",
            "100.0",
            "WAIT",
            "WAIT",
            "done",
            "",
            "",
            "",
            "100.0",
            "101.0",
            "99.0",
            "",
            "",
            "live",
        ]
        history_path = sym_dir / "paper_history_daytrade.csv"
        with history_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(runner.LEGACY_HISTORY_HEADER)
            writer.writerow(legacy_row)

        runner.append_history_if_needed(sym_dir, {
            "latest_ts": "2026-05-21 09:00:00",
            "equity": 1.0,
            "current_position_today": "FLAT",
            "current_w_today": 0.0,
            "last_price": 100.0,
            "last_action": "WAIT",
            "signal": "WAIT",
            "strategy_reason": "done",
            "run_mode": "live",
        })

        with history_path.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0], runner.HISTORY_HEADER)
        self.assertTrue(all(len(row) == len(runner.HISTORY_HEADER) for row in rows))
        self.assertEqual(rows[1][11:15], ["", "", "", ""])
        self.assertEqual(rows[1][15], "100.0")

    def test_idle_tick_repairs_missing_equity_and_pnl_fields(self):
        rows = [
            {"timestamp": "2026-05-21 09:00:00", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000},
        ]
        symbol, sym_dir = self.setup_symbol(rows)
        write_json(sym_dir / "paper_state_daytrade.json", {
            "symbol": symbol,
            "latest_date": "2026-05-21",
            "latest_ts": "2026-05-21 09:00:00",
            "day_start_equity": 0.9985,
            "sim_pnl_rate": 0.0,
            "daily_pnl_rate": 0.0015,
            "current_position_today": "FLAT",
            "current_w_today": 0.0,
        })
        write_json(sym_dir / "mobile_runner_state_daytrade.json", {
            "last_processed_ts": "2026-05-21 09:00:00",
        })

        runner.tick_symbol(symbol, replay_all=False, now=dt.datetime(2026, 5, 21, 10, 0))

        state = runner.load_json(runner.DATA_DIR / symbol / "paper_state_daytrade.json", {})
        self.assertAlmostEqual(state["equity"], 0.9985)
        self.assertAlmostEqual(state["sim_pnl_rate"], -0.0015)
        self.assertAlmostEqual(state["daily_pnl_rate"], 0.0)

    def test_idle_tick_repairs_flat_equity_from_complete_recent_trades(self):
        rows = [
            {"timestamp": "2026-05-28 15:30:00", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000},
        ]
        symbol, sym_dir = self.setup_symbol(rows)
        write_json(sym_dir / "paper_state_daytrade.json", {
            "symbol": symbol,
            "latest_date": "2026-05-28",
            "latest_ts": "2026-05-28 15:30:00",
            "equity": 1.0,
            "day_start_equity": 1.0,
            "sim_pnl_rate": 0.0,
            "daily_pnl_rate": 0.0,
            "closed_trade_count": 2,
            "current_position_today": "FLAT",
            "current_w_today": 0.0,
            "recent_trades": [
                {"pnl_rate": 0.001},
                {"pnl_rate": -0.002},
            ],
        })
        write_json(sym_dir / "mobile_runner_state_daytrade.json", {
            "last_processed_ts": "2026-05-28 15:30:00",
        })

        runner.tick_symbol(symbol, replay_all=False, now=dt.datetime(2026, 5, 29, 10, 0))

        state = runner.load_json(runner.DATA_DIR / symbol / "paper_state_daytrade.json", {})
        expected = 1.001 * 0.998
        self.assertAlmostEqual(state["equity"], expected)
        self.assertAlmostEqual(state["day_start_equity"], expected)
        self.assertAlmostEqual(state["sim_pnl_rate"], expected - 1.0)
        self.assertAlmostEqual(state["daily_pnl_rate"], 0.0)

    def test_short_entry_builds_sell_order_candidate(self):
        write_json(runner.DATA_DIR / "runtime_config.json", {
            "order_queue_enabled": True,
            "order_queue_file": "order_queue.jsonl",
            "order_events_file": "order_events.jsonl",
        })
        state = {
            "symbol": "TEST",
            "latest_ts": "2026-05-20 09:30:00",
            "signal": "ENTRY",
            "last_action": "SELL_BREAKDOWN",
            "current_position_today": "SHORT",
            "current_w_today": 0.25,
            "entry_price": 98.5,
            "last_price": 98.5,
        }

        candidate = order_queue.build_order_candidate(state)

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["side"], "SELL")
        self.assertEqual(candidate["order_type"], "ENTRY")
        self.assertEqual(candidate["weight"], 0.25)

    def test_short_exit_builds_buy_order_candidate(self):
        write_json(runner.DATA_DIR / "runtime_config.json", {
            "order_queue_enabled": True,
            "order_queue_file": "order_queue.jsonl",
            "order_events_file": "order_events.jsonl",
        })
        state = {
            "symbol": "TEST",
            "latest_ts": "2026-05-20 09:35:00",
            "signal": "EXIT",
            "last_action": "BUY_TARGET",
            "current_position_today": "FLAT",
            "last_exit_w": 0.25,
            "exit_price": 97.5,
            "last_price": 97.5,
        }

        candidate = order_queue.build_order_candidate(state)

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["side"], "BUY")
        self.assertEqual(candidate["order_type"], "EXIT")
        self.assertEqual(candidate["weight"], 0.25)

    def test_replay_does_not_enqueue_order_candidate(self):
        state = {
            "symbol": "TEST",
            "latest_ts": "2026-05-20 09:30:00",
            "signal": "ENTRY",
            "last_action": "SELL_BREAKDOWN",
            "run_mode": "replay",
        }
        with mock.patch.object(order_queue, "enqueue_from_state") as enqueue:
            runner.enqueue_order_candidate_if_needed(state)

        enqueue.assert_not_called()

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

    def test_missing_market_open_bars_blocks_late_entry(self):
        start = dt.datetime(2026, 5, 20, 13, 56)
        rows = []
        for i in range(31):
            ts = start + dt.timedelta(minutes=i)
            close = 100.0 + i * 0.1
            rows.append({
                "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "open": close - 0.05,
                "high": close + 0.20,
                "low": close - 0.20,
                "close": close,
                "volume": 2000,
            })
        symbol, _ = self.setup_symbol(rows)

        runner.tick_symbol(symbol, replay_all=True, now=dt.datetime(2026, 5, 20, 14, 30))

        state = runner.load_json(runner.DATA_DIR / symbol / "paper_state_daytrade.json", {})
        self.assertEqual(state["current_position_today"], "FLAT")
        self.assertEqual(state["signal"], "BLOCKED")
        self.assertEqual(state["strategy_reason"], "opening range unavailable: missing market open bars")
        self.assertEqual(state["trade_count_today"], 0)

    def test_delayed_market_open_within_grace_uses_available_opening_range(self):
        start = dt.datetime(2026, 5, 20, 9, 3)
        rows = []
        for i in range(40):
            ts = start + dt.timedelta(minutes=i)
            close = 100.0 + i * 0.1
            rows.append({
                "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "open": close - 0.05,
                "high": close + 0.20,
                "low": close - 0.20,
                "close": close,
                "volume": 2000,
            })
        symbol, _ = self.setup_symbol(rows)

        runner.tick_symbol(symbol, replay_all=True, now=dt.datetime(2026, 5, 20, 10, 0))

        state = runner.load_json(runner.DATA_DIR / symbol / "paper_state_daytrade.json", {})
        self.assertEqual(state["current_position_today"], "FLAT")
        self.assertEqual(state["signal"], "BLOCKED")
        self.assertEqual(state["strategy_reason"], "volume confirmation not met")
        self.assertEqual(state["trade_count_today"], 0)

    def test_opening_quality_thresholds_block_incomplete_or_gapped_range(self):
        def bar_at(minute: int):
            ts = dt.datetime(2026, 5, 20, 9, 0) + dt.timedelta(minutes=minute)
            return runner.normalize_bar({
                "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "open": 100.0,
                "high": 100.2,
                "low": 99.8,
                "close": 100.0,
                "volume": 1000,
            })

        incomplete = [bar_at(i) for i in range(19)] + [bar_at(30)]
        self.assertEqual(
            runner.opening_range_unavailable_reason(
                incomplete, 30, 1, 10, False, 240, 20, 3
            ),
            "opening range unavailable: incomplete opening bars",
        )

        gapped = [bar_at(i) for i in list(range(10)) + list(range(14, 30))] + [bar_at(30)]
        self.assertEqual(
            runner.opening_range_unavailable_reason(
                gapped, 30, 1, 10, False, 240, 20, 3
            ),
            "opening range unavailable: opening bar gap",
        )

        complete = [bar_at(i) for i in range(31)]
        self.assertEqual(
            runner.opening_range_unavailable_reason(
                complete, 30, 1, 10, False, 240, 20, 3
            ),
            "",
        )

    def test_missing_market_open_bars_blocks_outside_grace(self):
        start = dt.datetime(2026, 5, 20, 9, 11)
        rows = []
        for i in range(40):
            ts = start + dt.timedelta(minutes=i)
            close = 100.0 + i * 0.1
            rows.append({
                "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "open": close - 0.05,
                "high": close + 0.20,
                "low": close - 0.20,
                "close": close,
                "volume": 2000,
            })
        symbol, _ = self.setup_symbol(rows)

        runner.tick_symbol(symbol, replay_all=True, now=dt.datetime(2026, 5, 20, 10, 0))

        state = runner.load_json(runner.DATA_DIR / symbol / "paper_state_daytrade.json", {})
        self.assertEqual(state["current_position_today"], "FLAT")
        self.assertEqual(state["signal"], "BLOCKED")
        self.assertEqual(state["strategy_reason"], "opening range unavailable: missing market open bars")
        self.assertEqual(state["trade_count_today"], 0)

    def test_recovered_feed_uses_first_available_range_after_opening_period(self):
        start = dt.datetime(2026, 5, 20, 10, 14)
        rows = []
        for i in range(40):
            ts = start + dt.timedelta(minutes=i)
            close = 100.0 + i * 0.1
            rows.append({
                "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "open": close - 0.05,
                "high": close + 0.20,
                "low": close - 0.20,
                "close": close,
                "volume": 2000,
            })
        symbol, _ = self.setup_symbol(rows)

        runner.tick_symbol(symbol, replay_all=True, now=dt.datetime(2026, 5, 20, 11, 0))

        state = runner.load_json(runner.DATA_DIR / symbol / "paper_state_daytrade.json", {})
        self.assertTrue(state["opening_range_done"])
        self.assertTrue(state["opening_range_recovered"])
        self.assertNotEqual(state["strategy_reason"], "opening range unavailable: missing market open bars")
        self.assertEqual(state["strategy_reason"], "volume confirmation not met")

    def test_recovered_feed_after_late_morning_restart_does_not_stay_missing(self):
        start = dt.datetime(2026, 5, 20, 11, 7)
        rows = []
        for i in range(24):
            ts = start + dt.timedelta(minutes=i)
            close = 100.0 + i * 0.1
            rows.append({
                "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "open": close - 0.05,
                "high": close + 0.20,
                "low": close - 0.20,
                "close": close,
                "volume": 2000,
            })
        symbol, _ = self.setup_symbol(rows)

        runner.tick_symbol(symbol, replay_all=True, now=dt.datetime(2026, 5, 20, 11, 30))

        state = runner.load_json(runner.DATA_DIR / symbol / "paper_state_daytrade.json", {})
        self.assertFalse(state["opening_range_done"])
        self.assertTrue(state["opening_range_recovered"])
        self.assertEqual(state["strategy_reason"], "opening range is still forming")

    def test_runtime_config_can_disable_recovered_opening_range_entries(self):
        start = dt.datetime(2026, 5, 20, 10, 14)
        rows = []
        for i in range(40):
            ts = start + dt.timedelta(minutes=i)
            close = 100.0 + i * 0.1
            rows.append({
                "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "open": close - 0.05,
                "high": close + 0.20,
                "low": close - 0.20,
                "close": close,
                "volume": 2000,
            })
        symbol, _ = self.setup_symbol(rows)
        write_json(daytrade_config.CONFIG_FILE, {"allow_recovered_opening_range": False})

        runner.tick_symbol(symbol, replay_all=True, now=dt.datetime(2026, 5, 20, 11, 0))

        state = runner.load_json(runner.DATA_DIR / symbol / "paper_state_daytrade.json", {})
        self.assertFalse(state["opening_range_done"])
        self.assertFalse(state["opening_range_recovered"])
        self.assertEqual(state["signal"], "BLOCKED")
        self.assertEqual(state["strategy_reason"], "opening range unavailable: missing market open bars")

    def test_backfilled_real_opening_bars_unlock_late_current_entry(self):
        late_start = dt.datetime(2026, 5, 20, 13, 56)
        late_rows = []
        for i in range(31):
            ts = late_start + dt.timedelta(minutes=i)
            close = 98.8 - i * 0.04
            late_rows.append({
                "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "open": close + 0.05,
                "high": close + 0.15,
                "low": close - 0.20,
                "close": close,
                "volume": 2500,
            })
        symbol, sym_dir = self.setup_symbol(late_rows, settings={
            "allow_long": False,
            "allow_short": True,
            "use_volume_filter": False,
            "min_entry_price": 0.0,
        })

        runner.tick_symbol(symbol, replay_all=True, now=dt.datetime(2026, 5, 20, 14, 30))

        state = runner.load_json(sym_dir / "paper_state_daytrade.json", {})
        self.assertEqual(state["current_position_today"], "FLAT")
        self.assertEqual(state["strategy_reason"], "opening range unavailable: missing market open bars")

        opening_rows = [
            {"timestamp": "2026-05-20 09:00:00", "open": 100.0, "high": 100.5, "low": 99.6, "close": 100.1, "volume": 1000},
            {"timestamp": "2026-05-20 09:05:00", "open": 100.1, "high": 100.2, "low": 99.4, "close": 99.7, "volume": 1200},
            {"timestamp": "2026-05-20 09:10:00", "open": 99.7, "high": 99.8, "low": 99.0, "close": 99.2, "volume": 1200},
            {"timestamp": "2026-05-20 09:20:00", "open": 99.2, "high": 99.5, "low": 98.9, "close": 99.1, "volume": 1200},
            {"timestamp": "2026-05-20 09:30:00", "open": 99.1, "high": 99.2, "low": 98.3, "close": 98.5, "volume": 2200},
        ]
        write_bars(sym_dir / "intraday_bars.csv", opening_rows + late_rows)
        runner.BAR_CACHE.clear()

        runner.tick_symbol(symbol, replay_all=True, now=dt.datetime(2026, 5, 20, 14, 30))

        state = runner.load_json(sym_dir / "paper_state_daytrade.json", {})
        self.assertTrue(state["opening_range_done"])
        self.assertFalse(state["opening_range_recovered"])
        self.assertEqual(state["current_position_today"], "SHORT")
        self.assertEqual(state["signal"], "HOLD")
        self.assertEqual(state["last_action"], "HOLD_SHORT")
        self.assertEqual(state["trade_count_today"], 1)

    def test_min_entry_price_blocks_low_priced_entry_candidate(self):
        rows = [
            {"timestamp": "2026-05-20 09:00:00", "open": 50.0, "high": 50.5, "low": 49.6, "close": 50.1, "volume": 1000},
            {"timestamp": "2026-05-20 09:05:00", "open": 50.1, "high": 50.2, "low": 49.4, "close": 49.7, "volume": 1200},
            {"timestamp": "2026-05-20 09:10:00", "open": 49.7, "high": 49.8, "low": 49.0, "close": 49.2, "volume": 1200},
            {"timestamp": "2026-05-20 09:20:00", "open": 49.2, "high": 49.5, "low": 48.9, "close": 49.1, "volume": 1200},
            {"timestamp": "2026-05-20 09:30:00", "open": 49.1, "high": 49.2, "low": 48.3, "close": 48.5, "volume": 2200},
        ]
        symbol, _ = self.setup_symbol(rows, settings={
            "allow_long": False,
            "allow_short": True,
            "min_entry_price": 100.0,
        })

        runner.tick_symbol(symbol, replay_all=True, now=dt.datetime(2026, 5, 20, 10, 0))

        state = runner.load_json(runner.DATA_DIR / symbol / "paper_state_daytrade.json", {})
        self.assertEqual(state["current_position_today"], "FLAT")
        self.assertEqual(state["signal"], "BLOCKED")
        self.assertEqual(state["strategy_reason"], "entry price below minimum")

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

    def test_same_timestamp_update_cannot_stop_until_next_bar(self):
        rows = [
            {"timestamp": "2026-05-20 09:30:00", "open": 100.0, "high": 101.0, "low": 98.0, "close": 98.5, "volume": 2000},
            {"timestamp": "2026-05-20 09:31:00", "open": 98.5, "high": 99.0, "low": 98.0, "close": 98.2, "volume": 1800},
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
        self.assertEqual(state["current_position_today"], "LONG")
        self.assertEqual(state["last_action"], "HOLD_LONG")
        self.assertEqual(state["strategy_reason"], "entry bar awaiting completion")

        runner.tick_symbol(symbol, now=dt.datetime(2026, 5, 20, 9, 31, 30))

        state = runner.load_json(sym_dir / "paper_state_daytrade.json", {})
        self.assertEqual(state["current_position_today"], "FLAT")
        self.assertEqual(state["last_action"], "SELL_STOP")
        self.assertEqual(state["signal"], "EXIT")

    def test_eod_finalization_closes_position_without_new_bar(self):
        rows = [
            {"timestamp": "2026-05-20 14:50:00", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 2000},
        ]
        symbol, sym_dir = self.setup_symbol(rows, settings={"force_flat_time": "15:25"})
        write_json(sym_dir / "paper_state_daytrade.json", {
            "symbol": symbol,
            "latest_date": "2026-05-20",
            "latest_ts": "2026-05-20 14:50:00",
            "equity": 1.0,
            "entry_equity": 1.0,
            "trade_start_equity": 1.0,
            "current_position_today": "LONG",
            "current_w_today": 0.2,
            "entry_price": 100.0,
            "entry_ts": "2026-05-20 14:30:00",
            "last_price": 100.0,
            "stop_price": 90.0,
            "target_price": 110.0,
            "partial_target_price": 105.0,
            "initial_risk_price": 10.0,
            "closed_trade_count": 0,
        })
        write_json(sym_dir / "mobile_runner_state_daytrade.json", {
            "last_processed_ts": "2026-05-20 14:50:00",
        })

        results = runner.finalize_open_positions(now=dt.datetime(2026, 5, 20, 15, 45))

        state = runner.load_json(sym_dir / "paper_state_daytrade.json", {})
        self.assertEqual(state["current_position_today"], "FLAT")
        self.assertEqual(state["last_action"], "SELL_EOD")
        self.assertEqual(state["signal"], "EXIT")
        self.assertEqual(results[0]["finalized"], True)
        queued = order_queue.load_jsonl(order_queue.queue_path())
        self.assertEqual(len([row for row in queued if row.get("order_type") == "EXIT"]), 1)

    def test_older_monitor_bar_does_not_close_newer_position(self):
        rows = [
            {"timestamp": "2026-05-20 09:02:00", "open": 100.0, "high": 110.0, "low": 99.0, "close": 105.0, "volume": 2000},
        ]
        symbol, sym_dir = self.setup_symbol(rows)
        write_json(sym_dir / "paper_state_daytrade.json", {
            "symbol": symbol,
            "latest_date": "2026-05-20",
            "latest_ts": "2026-05-20 09:46:00",
            "equity": 1.0,
            "entry_equity": 1.0,
            "trade_start_equity": 1.0,
            "current_position_today": "SHORT",
            "current_w_today": 0.5,
            "entry_price": 100.0,
            "entry_ts": "2026-05-20 09:46:00",
            "stop_price": 101.0,
            "target_price": 90.0,
            "partial_target_price": 95.0,
            "initial_risk_price": 1.0,
            "closed_trade_count": 0,
        })
        write_json(sym_dir / "mobile_runner_state_daytrade.json", {
            "last_processed_ts": "2026-05-20 09:46:00",
        })

        runner.tick_symbol(symbol, now=dt.datetime(2026, 5, 20, 9, 50, 0))

        state = runner.load_json(sym_dir / "paper_state_daytrade.json", {})
        self.assertEqual(state["current_position_today"], "SHORT")
        self.assertEqual(state["closed_trade_count"], 0)

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

    def test_ar_signal_forecasts_mean_reversion(self):
        rows = []
        start = dt.datetime(2026, 5, 20, 9, 0)
        close = 100.0
        for idx, ret in enumerate([0.01, -0.01] * 8 + [0.01]):
            rows.append({
                "timestamp": (start + dt.timedelta(minutes=idx)).strftime("%Y-%m-%d %H:%M:%S"),
                "ts": start + dt.timedelta(minutes=idx),
                "date": "2026-05-20",
                "open": close,
                "high": close * (1.0 + abs(ret)),
                "low": close * (1.0 - abs(ret)),
                "close": close * math.exp(ret),
                "volume": 1000,
            })
            close = rows[-1]["close"]

        signal = runner.calc_ar_signal(rows, lookback=30, min_samples=8, horizon_bars=1)

        self.assertEqual(signal["ar_status"], "ok")
        self.assertLess(signal["ar_slope"], 0.0)
        self.assertLess(signal["ar_forecast_return"], 0.0)

    def test_ar_filter_blocks_opposite_direction_when_enabled(self):
        settings = dict(runner.DEFAULT_SETTINGS)
        settings.update({
            "use_ar_filter": True,
            "ar_block_opposite_threshold_rate": 0.0001,
        })
        state = {
            "ar_status": "ok",
            "ar_forecast_return": -0.001,
        }

        self.assertEqual(runner.ar_entry_block_reason(state, settings, "long"), "ar forecast opposes long")
        self.assertEqual(runner.ar_entry_block_reason(state, settings, "short"), "")

    def test_ar_compare_log_records_without_and_with_ar(self):
        rows = [
            {"timestamp": "2026-05-20 09:00:00", "open": 100.0, "high": 100.5, "low": 99.6, "close": 100.1, "volume": 1000},
            {"timestamp": "2026-05-20 09:05:00", "open": 100.1, "high": 100.2, "low": 99.4, "close": 99.7, "volume": 1200},
            {"timestamp": "2026-05-20 09:10:00", "open": 99.7, "high": 99.8, "low": 99.0, "close": 99.2, "volume": 1200},
            {"timestamp": "2026-05-20 09:20:00", "open": 99.2, "high": 99.5, "low": 98.9, "close": 99.1, "volume": 1200},
            {"timestamp": "2026-05-20 09:30:00", "open": 99.1, "high": 99.2, "low": 98.3, "close": 98.5, "volume": 2200},
        ]
        symbol, sym_dir = self.setup_symbol(rows, settings={
            "allow_long": False,
            "allow_short": True,
            "use_ar_filter": False,
            "ar_min_samples": 3,
        })

        runner.tick_symbol(symbol, replay_all=True, now=dt.datetime(2026, 5, 20, 10, 0))

        with (sym_dir / "paper_ar_compare_daytrade.csv").open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(rows[-1]["setup"], "breakdown")
        self.assertEqual(rows[-1]["side"], "short")
        self.assertEqual(rows[-1]["without_ar"], "ENTRY_CANDIDATE")
        self.assertIn(rows[-1]["with_ar"], ("ENTRY_CANDIDATE", "AR_BLOCKED"))
        self.assertEqual(rows[-1]["actual_signal"], "ENTRY")


if __name__ == "__main__":
    unittest.main()
