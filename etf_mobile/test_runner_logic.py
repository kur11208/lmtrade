from __future__ import annotations

import csv
import datetime as dt
import json
import math
import tempfile
import unittest
from pathlib import Path

import runner_pc_multi as runner


def write_json(path: Path, data: dict | list):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def make_ohlc_rows(start: dt.date, end: dt.date, seed: int = 0):
    rows = []
    close = 100.0 + seed * 25.0
    day_index = 0
    d = start
    while d <= end:
        if d.weekday() < 5:
            drift = 0.00008 + seed * 0.00001
            cycle = (((day_index + seed * 7) % 29) - 14) * (0.00015 + seed * 0.00001)
            shock_every = max(251, 997 - seed * 113)
            shock = -(0.04 + seed * 0.01) if day_index > 0 and day_index % shock_every == 0 else 0.0
            open_price = close * (1.0 + cycle * 0.25)
            close = max(1.0, close * (1.0 + drift + cycle + shock))
            high = max(open_price, close) * 1.006
            low = min(open_price, close) * (0.90 if shock else 0.994)
            rows.append({
                "date": d.strftime("%Y-%m-%d"),
                "open": round(open_price, 6),
                "high": round(high, 6),
                "low": round(low, 6),
                "close": round(close, 6),
                "volume": 100000 + day_index,
            })
            day_index += 1
        d += dt.timedelta(days=1)
    return rows


class RunnerLogicTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old_data_dir = runner.DATA_DIR
        self.old_symbols_file = runner.SYMBOLS_FILE
        runner.DATA_DIR = self.root / "data"
        runner.SYMBOLS_FILE = runner.DATA_DIR / "symbols.json"
        runner.SETTINGS_CACHE.clear()
        runner.OHLC_CACHE.clear()
        runner.HISTORY_DATE_CACHE.clear()
        runner.DATA_DIR.mkdir()

    def tearDown(self):
        runner.DATA_DIR = self.old_data_dir
        runner.SYMBOLS_FILE = self.old_symbols_file
        runner.SETTINGS_CACHE.clear()
        runner.OHLC_CACHE.clear()
        runner.HISTORY_DATE_CACHE.clear()
        self.tmp.cleanup()

    def setup_symbol(self, rows, symbol="TEST", settings=None, write_symbols=True):
        sym_dir = runner.DATA_DIR / symbol
        sym_dir.mkdir()
        if write_symbols:
            write_json(runner.SYMBOLS_FILE, [{"symbol": symbol, "enabled": True}])
        base_settings = {
            "symbol": symbol,
            "buy_th": 0.50,
            "sell_th": 0.45,
            "risk_cash": 0.50,
            "atr_mult": 2.0,
            "fee_rate": 0.0,
            "slippage_rate": 0.0,
            "trend_ma_days": 20,
            "momentum_days": 2,
            "max_atr_rate": 0.50,
        }
        if settings:
            base_settings.update(settings)
        write_json(sym_dir / "paper_settings.json", base_settings)
        write_json(sym_dir / "paper_state_atr.json", {
            "symbol": symbol,
            "latest_date": "",
            "equity": 1.0,
            "base_equity": 100.0,
            "close": 100.0,
            "atr_14": 2.0,
            "current_w_today": 0.5,
            "current_position_today": "LONG",
            "pending_action": "HOLD_LONG",
            "next_w": 0.5,
            "buy_th": 0.50,
            "sell_th": 0.45,
            "risk_cash": 0.50,
            "atr_mult": 2.0,
            "fee_rate": 0.0,
            "slippage_rate": 0.0,
            "current_stop_today": 80.0,
        })
        write_json(sym_dir / "mobile_runner_state.json", {
            "last_signal_run_date": "",
            "last_execution_run_date": "",
        })
        with (sym_dir / "ohlc.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["date", "open", "high", "low", "close", "volume"])
            writer.writeheader()
            writer.writerows(rows)
        return sym_dir

    def test_weight_is_reflected_in_equity(self):
        rows = [
            {"date": "2026-01-02", "open": 100.0, "high": 111.0, "low": 99.0, "close": 110.0, "volume": 1},
        ]
        self.setup_symbol(rows)

        state = {
            "symbol": "TEST",
            "latest_date": "",
            "equity": 1.0,
            "base_equity": 100.0,
            "close": 100.0,
            "atr_14": 2.0,
            "current_w_today": 0.5,
            "current_position_today": "LONG",
            "buy_th": 0.50,
            "sell_th": 0.45,
            "risk_cash": 0.50,
            "atr_mult": 2.0,
            "fee_rate": 0.0,
            "slippage_rate": 0.0,
        }
        runner_state = {}

        state, _ = runner.run_signal(state, runner_state, dt.datetime(2026, 1, 2, 18, 10), force=True)

        self.assertAlmostEqual(state["equity"], 1.05, places=8)
        self.assertEqual(state["price_source"], "ohlc.csv")
        self.assertEqual(state["pred_source"], "rule_ma20_mom2_atr")

    def test_atr_stop_moves_to_cash(self):
        rows = [
            {"date": "2026-01-02", "open": 95.0, "high": 100.0, "low": 88.0, "close": 98.0, "volume": 1},
        ]
        self.setup_symbol(rows)

        state = {
            "symbol": "TEST",
            "latest_date": "",
            "equity": 1.0,
            "base_equity": 100.0,
            "close": 100.0,
            "atr_14": 2.0,
            "current_w_today": 0.5,
            "current_position_today": "LONG",
            "current_stop_today": 90.0,
            "buy_th": 0.50,
            "sell_th": 0.45,
            "risk_cash": 0.50,
            "atr_mult": 2.0,
            "fee_rate": 0.0,
            "slippage_rate": 0.0,
        }
        runner_state = {}

        state, _ = runner.run_signal(state, runner_state, dt.datetime(2026, 1, 2, 18, 10), force=True)

        self.assertTrue(state["stop_triggered_today"])
        self.assertEqual(state["current_position_today"], "CASH")
        self.assertEqual(state["current_w_today"], 0.0)
        self.assertAlmostEqual(state["equity"], 0.95, places=8)

    def test_stale_ohlc_blocks_dummy_signal(self):
        rows = [
            {"date": "2026-01-02", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1},
        ]
        self.setup_symbol(rows)
        state = {
            "symbol": "TEST",
            "latest_date": "2026-01-02",
            "equity": 1.0,
            "base_equity": 100.0,
            "close": 100.0,
            "atr_14": 2.0,
            "current_w_today": 0.5,
            "current_position_today": "LONG",
            "buy_th": 0.50,
            "sell_th": 0.45,
            "risk_cash": 0.50,
            "atr_mult": 2.0,
            "fee_rate": 0.0,
            "slippage_rate": 0.0,
        }
        runner_state = {}

        updated, _ = runner.run_signal(state, runner_state, dt.datetime(2026, 1, 5, 18, 10), force=True)

        self.assertEqual(updated["data_freshness_status"], "stale_ohlc")
        self.assertEqual(updated["evaluation_status"], "blocked_stale_data")
        self.assertEqual(updated["price_source"], "stale_ohlc")
        self.assertEqual(updated["latest_date"], "2026-01-02")
        self.assertEqual(updated["current_position_today"], "LONG")
        self.assertAlmostEqual(updated["equity"], 1.0, places=8)

    def test_one_hundred_year_synthetic_ohlc_run(self):
        symbols = ["1321", "1306", "1343", "1540", "2510"]
        write_json(runner.SYMBOLS_FILE, [{"symbol": symbol, "enabled": True} for symbol in symbols])

        rows_by_symbol = {}
        for idx, symbol in enumerate(symbols):
            rows = make_ohlc_rows(dt.date(1926, 1, 1), dt.date(2025, 12, 31), seed=idx)
            rows_by_symbol[symbol] = rows
            self.setup_symbol(
                rows,
                symbol=symbol,
                settings={
                    "buy_th": 0.49 + idx * 0.01,
                    "sell_th": 0.35 + idx * 0.02,
                    "risk_cash": min(0.4, idx * 0.1),
                    "atr_mult": 2.0 + idx * 0.25,
                },
                write_symbols=False,
            )

        for symbol, rows in rows_by_symbol.items():
            sym_dir = runner.DATA_DIR / symbol
            state = json.loads((sym_dir / "paper_state_atr.json").read_text(encoding="utf-8"))
            runner_state = json.loads((sym_dir / "mobile_runner_state.json").read_text(encoding="utf-8"))

            for row in rows:
                d = dt.datetime.strptime(row["date"], "%Y-%m-%d")
                state, runner_state = runner.execute_pending(state, runner_state, d.replace(hour=9, minute=5), force=True)
                state, runner_state = runner.run_signal(state, runner_state, d.replace(hour=18, minute=10), force=True)

            runner.save_json(sym_dir / "paper_state_atr.json", state)
            runner.append_history_if_needed(sym_dir, state)
            with (sym_dir / "paper_history.csv").open("r", encoding="utf-8-sig") as f:
                hist_rows = list(csv.DictReader(f))

            self.assertGreaterEqual(len(rows), 26000)
            self.assertEqual(len(hist_rows), 1)
            self.assertTrue(math.isfinite(float(state["equity"])))
            self.assertGreater(float(state["equity"]), 0.0)
            self.assertEqual(state["latest_date"], rows[-1]["date"])
            self.assertEqual(state["price_source"], "ohlc.csv")

        for symbol in symbols:
            state = json.loads((runner.DATA_DIR / symbol / "paper_state_atr.json").read_text(encoding="utf-8"))
            self.assertTrue(str(state["pred_source"]).startswith("rule_ma"))

    def test_backfill_history_writes_graphable_rows_from_ohlc(self):
        rows = [
            {"date": "2026-01-02", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1},
            {"date": "2026-01-05", "open": 101.0, "high": 102.0, "low": 100.0, "close": 101.0, "volume": 1},
            {"date": "2026-01-06", "open": 102.0, "high": 103.0, "low": 101.0, "close": 102.0, "volume": 1},
        ]
        sym_dir = self.setup_symbol(rows)

        result = runner.backfill_history("TEST", replace=True)

        self.assertEqual(result["status"], "written")
        self.assertGreaterEqual(result["written"], 2)
        with (sym_dir / "paper_history.csv").open("r", encoding="utf-8-sig", newline="") as f:
            hist = list(csv.DictReader(f))
        self.assertGreaterEqual(len(hist), 2)
        self.assertEqual(hist[0]["date"], "2026-01-02")


if __name__ == "__main__":
    unittest.main()
