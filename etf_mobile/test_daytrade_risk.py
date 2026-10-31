from __future__ import annotations

import datetime as dt
import concurrent.futures
import json
import tempfile
import unittest
from pathlib import Path

import daytrade_config
import daytrade_order_queue as queue
import daytrade_risk
import daytrade_runner as runner
import daytrade_status


def write_json(path: Path, data: dict | list):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class DaytradeRiskTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old_data_dir = runner.DATA_DIR
        self.old_symbols_file = runner.SYMBOLS_FILE
        self.old_config_file = daytrade_config.CONFIG_FILE
        self.old_risk_file = daytrade_risk.RISK_STATUS_FILE
        self.old_status_data_dir = daytrade_status.DATA_DIR
        self.old_feed_status_file = daytrade_status.FEED_STATUS_FILE
        runner.DATA_DIR = self.root / "data_daytrade"
        runner.SYMBOLS_FILE = runner.DATA_DIR / "symbols.json"
        daytrade_config.CONFIG_FILE = runner.DATA_DIR / "runtime_config.json"
        daytrade_risk.RISK_STATUS_FILE = runner.DATA_DIR / "portfolio_risk_status.json"
        daytrade_status.set_data_dir(runner.DATA_DIR)
        runner.DATA_DIR.mkdir()
        daytrade_risk.ENTRY_ORDER_COUNT_CACHE.clear()

    def tearDown(self):
        runner.DATA_DIR = self.old_data_dir
        runner.SYMBOLS_FILE = self.old_symbols_file
        daytrade_config.CONFIG_FILE = self.old_config_file
        daytrade_risk.RISK_STATUS_FILE = self.old_risk_file
        daytrade_status.set_data_dir(self.old_status_data_dir)
        daytrade_status.FEED_STATUS_FILE = self.old_feed_status_file
        daytrade_risk.ENTRY_ORDER_COUNT_CACHE.clear()
        self.tmp.cleanup()

    def test_entry_order_blocked_by_daily_order_cap(self):
        write_json(runner.SYMBOLS_FILE, [{"symbol": "A", "enabled": True}])
        write_json(daytrade_config.CONFIG_FILE, {
            "order_queue_enabled": True,
            "order_queue_file": "order_queue.jsonl",
            "order_events_file": "order_events.jsonl",
            "portfolio_max_entry_orders_per_day": 1,
            "portfolio_block_on_feed_unhealthy": False,
        })
        today = dt.datetime.now().strftime("%Y-%m-%d")
        queue.append_jsonl(queue.queue_path(), {
            "id": "old",
            "created_at": f"{today} 09:30:00",
            "order_type": "ENTRY",
            "status": "NEW",
        })

        candidate = queue.enqueue_from_state({
            "symbol": "A",
            "signal": "ENTRY",
            "last_action": "SELL_ENTRY",
            "latest_ts": f"{today} 09:35:00",
            "current_w_today": 0.1,
            "entry_price": 100.0,
        })

        self.assertIsNone(candidate)
        risk = runner.load_json(daytrade_risk.RISK_STATUS_FILE, {})
        self.assertTrue(risk["blocked"])
        self.assertEqual(risk["reason"], "portfolio max entry orders reached")

    def test_exit_order_not_blocked_by_entry_risk_guard(self):
        write_json(runner.SYMBOLS_FILE, [{"symbol": "A", "enabled": True}])
        write_json(daytrade_config.CONFIG_FILE, {
            "order_queue_enabled": True,
            "order_queue_file": "order_queue.jsonl",
            "order_events_file": "order_events.jsonl",
            "portfolio_max_entry_orders_per_day": 0,
            "portfolio_block_on_feed_unhealthy": True,
        })
        today = dt.datetime.now().strftime("%Y-%m-%d")

        candidate = queue.enqueue_from_state({
            "symbol": "A",
            "signal": "EXIT",
            "last_action": "BUY_STOP",
            "latest_ts": f"{today} 09:35:00",
            "last_exit_w": 0.1,
            "exit_price": 100.0,
        })

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["order_type"], "EXIT")

    def test_concurrent_enqueue_writes_one_order(self):
        write_json(runner.SYMBOLS_FILE, [{"symbol": "A", "enabled": True}])
        write_json(daytrade_config.CONFIG_FILE, {
            "order_queue_enabled": True,
            "order_queue_file": "order_queue.jsonl",
            "order_events_file": "order_events.jsonl",
            "portfolio_daily_loss_limit_rate": 0,
            "portfolio_max_consecutive_losses": 0,
            "portfolio_max_position_w": 1,
            "portfolio_max_open_positions": 0,
            "portfolio_max_entry_orders_per_day": 0,
            "portfolio_block_on_feed_unhealthy": False,
        })
        state = {
            "symbol": "A",
            "signal": "ENTRY",
            "last_action": "SELL_ENTRY",
            "latest_ts": f"{dt.datetime.now():%Y-%m-%d} 09:35:00",
            "current_w_today": 0.1,
            "entry_price": 100.0,
        }

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(queue.enqueue_from_state, [state] * 8))

        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(len(queue.load_jsonl(queue.queue_path())), 1)
        queued_events = [
            row for row in queue.load_jsonl(queue.events_path())
            if row.get("event") == "QUEUED"
        ]
        self.assertEqual(len(queued_events), 1)

    def test_portfolio_snapshot_includes_state_files_outside_symbols(self):
        write_json(runner.SYMBOLS_FILE, [{"symbol": "A", "enabled": True}])
        write_json(runner.DATA_DIR / "A" / "paper_state_daytrade.json", {
            "symbol": "A",
            "latest_date": "2026-05-20",
            "sim_capital_yen": 10000,
            "day_start_equity": 1.0,
            "equity": 1.01,
            "current_position_today": "FLAT",
        })
        write_json(runner.DATA_DIR / "OLD" / "paper_state_daytrade.json", {
            "symbol": "OLD",
            "latest_date": "2026-05-20",
            "sim_capital_yen": 10000,
            "day_start_equity": 1.0,
            "equity": 0.99,
            "current_position_today": "FLAT",
        })

        snapshot = daytrade_risk.portfolio_snapshot(dt.datetime(2026, 5, 20, 10, 0))

        self.assertEqual(snapshot["symbol_count"], 2)
        self.assertAlmostEqual(snapshot["daily_pnl_rate"], 0.0)

    def test_entry_blocked_by_portfolio_consecutive_losses(self):
        write_json(runner.SYMBOLS_FILE, [{"symbol": "A", "enabled": True}, {"symbol": "B", "enabled": False}])
        write_json(daytrade_config.CONFIG_FILE, {
            "portfolio_max_consecutive_losses": 2,
            "portfolio_block_on_feed_unhealthy": False,
        })
        for symbol, minute in (("A", "30"), ("B", "35")):
            write_json(runner.DATA_DIR / symbol / "paper_state_daytrade.json", {
                "symbol": symbol,
                "sim_capital_yen": 10000,
                "day_start_equity": 1.0,
                "equity": 0.999,
                "current_position_today": "FLAT",
                "recent_trades": [{
                    "exit_ts": f"2026-05-20 09:{minute}:00",
                    "pnl_rate": -0.001,
                }],
            })

        reason = daytrade_risk.entry_block_reason(now=dt.datetime(2026, 5, 20, 9, 40), check_feed=False)

        self.assertEqual(reason, "portfolio consecutive loss limit reached")

    def test_old_symbol_state_is_neutral_for_today_pnl(self):
        write_json(runner.SYMBOLS_FILE, [{"symbol": "A", "enabled": True}])
        write_json(runner.DATA_DIR / "A" / "paper_state_daytrade.json", {
            "symbol": "A",
            "latest_date": "2026-05-20",
            "sim_capital_yen": 10000,
            "day_start_equity": 1.0,
            "equity": 1.0,
            "current_position_today": "FLAT",
        })
        write_json(runner.DATA_DIR / "OLD" / "paper_state_daytrade.json", {
            "symbol": "OLD",
            "latest_date": "2026-05-19",
            "sim_capital_yen": 10000,
            "day_start_equity": 1.0,
            "equity": 0.90,
            "current_position_today": "FLAT",
        })

        snapshot = daytrade_risk.portfolio_snapshot(dt.datetime(2026, 5, 20, 10, 0))

        self.assertEqual(snapshot["symbol_count"], 2)
        self.assertAlmostEqual(snapshot["daily_pnl_rate"], 0.0)

    def test_portfolio_snapshot_sums_open_weight_and_mixed_losses(self):
        write_json(runner.SYMBOLS_FILE, [{"symbol": "A", "enabled": True}, {"symbol": "B", "enabled": True}])
        for symbol, weight, pnl, minute in (("A", 0.15, -0.001, "30"), ("B", 0.20, -0.002, "35")):
            write_json(runner.DATA_DIR / symbol / "paper_state_daytrade.json", {
                "symbol": symbol,
                "latest_date": "2026-05-20",
                "sim_capital_yen": 10000,
                "day_start_equity": 1.0,
                "equity": 1.0 + pnl,
                "current_position_today": "LONG",
                "current_w_today": weight,
                "recent_trades": [{
                    "exit_ts": f"2026-05-20 09:{minute}:00",
                    "pnl_rate": pnl,
                }],
            })

        snapshot = daytrade_risk.portfolio_snapshot(dt.datetime(2026, 5, 20, 9, 40))

        self.assertEqual(snapshot["open_positions"], 2)
        self.assertAlmostEqual(snapshot["open_position_w"], 0.35)
        self.assertEqual(snapshot["consecutive_losses_today"], 2)


if __name__ == "__main__":
    unittest.main()
