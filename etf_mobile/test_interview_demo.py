from __future__ import annotations

from contextlib import ExitStack
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import daytrade_backtest as backtest
import daytrade_runner as runner
import interview_demo as demo


class InterviewDemoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = demo.build_demo()

    def test_exact_reproduction_and_no_wall_clock_dependency(self):
        self.assertEqual(demo.encode_json(self.model), demo.encode_json(demo.build_demo()))
        command = [sys.executable, str(demo.ROOT.parent / "demo.py"), "--check"]
        with tempfile.TemporaryDirectory() as cwd:
            first = subprocess.run(command, cwd=cwd, capture_output=True, check=True)
            second = subprocess.run(command, cwd=cwd, capture_output=True, check=True)
            self.assertEqual(first.stdout, second.stdout)
            self.assertEqual(list(Path(cwd).iterdir()), [])
        self.assertEqual(json.loads(first.stdout)["fingerprint"], self.model["fingerprint"])

    def test_profit_loss_and_no_trade_are_real_engine_outputs(self):
        trend, reversal, flat = self.model["scenarios"]
        self.assertGreater(trend["frames"][-1]["pnl_yen"], 0)
        self.assertLess(reversal["frames"][-1]["pnl_yen"], 0)
        self.assertEqual(trend["trades"][0]["exit_reason"], "TARGET")
        self.assertEqual(reversal["trades"][0]["exit_reason"], "STOP")
        self.assertEqual(flat["trades"], [])
        self.assertEqual(flat["fills"], [])
        self.assertTrue(all(frame["pnl_yen"] == 0 for frame in flat["frames"]))
        for scenario in self.model["scenarios"]:
            self.assertEqual(len(scenario["frames"]), 41)
            self.assertEqual(scenario["frames"][0]["timestamp"], "2026-05-20 09:00:00")
            self.assertEqual(scenario["frames"][-1]["timestamp"], "2026-05-20 09:40:00")
            self.assertEqual(scenario["frames"][-1]["position"], "FLAT")
            self.assertTrue(all(t["entry_ts"] < t["exit_ts"] for t in scenario["trades"]))
            self.assertEqual(scenario["frames"][-1]["closed_trades"], len(scenario["trades"]))
            self.assertAlmostEqual(sum(t["pnl_yen"] for t in scenario["trades"]), scenario["frames"][-1]["pnl_yen"])

    def test_independent_decimal_ledger_includes_both_fees(self):
        # Independently derive weighted account equity from known fill prices.
        # This deliberately does not call the engine's pricing/P&L functions.
        d = Decimal
        capital, weight, fee = d("100000"), d("0.4"), d("0.0005")
        cases = [("1004.8", "1012.6", 1), ("995.2", "1003.2", -1)]
        for scenario, (entry, exit_price, direction) in zip(self.model["scenarios"], cases):
            trade = scenario["trades"][0]
            self.assertAlmostEqual(trade["entry_price"], float(entry))
            self.assertAlmostEqual(trade["exit_price"], float(exit_price))
            change = d(direction) * (d(exit_price) / d(entry) - 1)
            entry_equity = 1 - weight * fee
            expected = capital * (entry_equity * (1 + weight * change - weight * fee) - 1)
            self.assertAlmostEqual(trade["pnl_yen"], float(expected), places=7)
            at_entry = scenario["frames"][30]
            self.assertAlmostEqual(at_entry["balance_yen"], float(capital * entry_equity))
            holding = scenario["frames"][31]
            mark_change = d(direction) * (d(str(holding["close"])) / d(entry) - 1)
            expected_mark = capital * entry_equity * (1 + weight * mark_change)
            self.assertAlmostEqual(holding["balance_yen"], float(expected_mark), places=7)

    def test_costs_and_tick_rounding_change_profit(self):
        # Same strategy/data, but removing execution costs must change the result.
        settings = dict(self.model["settings"], fee_rate=0, slippage_rate=0, spread_rate=0)
        clean = demo.build_scenario("trend", "test", "TEST", settings)
        self.assertGreater(clean["frames"][-1]["pnl_yen"], self.model["scenarios"][0]["frames"][-1]["pnl_yen"])

    def test_replay_has_no_real_data_order_or_external_io(self):
        with ExitStack() as stack:
            for name in ("load_json", "load_symbol_settings", "load_intraday_bars", "save_json", "portfolio_entry_block_reason", "enqueue_order_candidate_if_needed"):
                stack.enter_context(mock.patch.object(runner, name, side_effect=AssertionError(name)))
            for name in ("subprocess.Popen", "socket.socket.connect", "urllib.request.urlopen"):
                stack.enter_context(mock.patch(name, side_effect=AssertionError(name)))
            actual = demo.build_demo()
        self.assertEqual(actual["allow_live_order"], False)
        self.assertEqual(actual["order_adapter"], "paper")
        self.assertEqual(actual["trading_mode"], "paper")
        self.assertEqual(demo.encode_json(actual), demo.encode_json(self.model))

    def test_no_lookahead_and_snapshots_are_independent(self):
        settings = self.model["settings"]
        bars = demo.load_fixture(demo.FIXTURES / "trend.csv")
        full = list(backtest.replay_bars("TEST", bars, settings))
        for length in (1, 20, 30, 31, 34):
            prefix = list(backtest.replay_bars("TEST", bars[:length], settings))
            self.assertEqual(prefix, full[:length])
        full[-1]["recent_trades"].clear()
        self.assertEqual(len(full[33]["recent_trades"]), 1)
        with self.assertRaises(ValueError):
            list(backtest.replay_bars("TEST", [bars[1], bars[0]], settings))
        with self.assertRaises(ValueError):
            list(backtest.replay_bars("TEST", [bars[0], bars[0]], settings))
        with self.assertRaises(ValueError):
            list(backtest.replay_bars("TEST", bars, dict(settings, use_market_filter=True)))

    def test_fixture_validation_rejects_bad_input(self):
        header = "timestamp,open,high,low,close,volume\n"
        bad_rows = ["", "bad,100,101,99,100,20\n", "2026-05-20 09:00:00,100,101,99,NaN,20\n",
                    "2026-05-20 09:00:00,100,99,99,100,20\n", "2026-05-20 09:00:00,100,101,99,100,-1\n"]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.csv"
            for row in bad_rows:
                with self.subTest(row=row):
                    path.write_text(header + row, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        demo.load_fixture(path)

    def test_http_is_loopback_read_only_and_assets_are_bundled(self):
        with demo.create_server(0) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                self.assertEqual(server.server_address[0], "127.0.0.1")
                for path in ("/", "/demo.js", "/demo.css", "/api/demo", "/download.json"):
                    with urlopen(base + path, timeout=5) as response:
                        self.assertEqual(response.status, 200)
                        self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])
                        if path.endswith("json") or path == "/api/demo":
                            self.assertEqual(json.loads(response.read()), self.model)
                with urlopen(Request(base + "/", method="HEAD"), timeout=5) as response:
                    self.assertEqual(response.read(), b"")
                for path, method, code in (("/api/order", "POST", 501), ("/../data_daytrade/runtime_config.json", "GET", 404)):
                    with self.assertRaises(HTTPError) as error:
                        urlopen(Request(base + path, method=method), timeout=5)
                    self.assertEqual(error.exception.code, code)
            finally:
                server.shutdown()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
