from __future__ import annotations

import csv
import datetime as dt
import re
import tempfile
import unittest
from pathlib import Path

import daytrade_app as app
import daytrade_status


def write_history(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "timestamp",
                "equity",
                "position",
                "weight",
                "price",
                "action",
                "signal",
                "reason",
                "setup",
                "volume_ratio",
                "gap_rate",
                "vwap",
                "or_high",
                "or_low",
                "stop_price",
                "target_price",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


class DaytradeAppTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_data_dir = app.DATA_DIR
        self.old_symbols_file = app.SYMBOLS_FILE
        self.old_runtime_config_file = app.RUNTIME_CONFIG_FILE
        self.old_status_data_dir = daytrade_status.DATA_DIR
        self.old_status_feed = daytrade_status.FEED_STATUS_FILE
        self.old_status_runner = daytrade_status.RUNNER_STATUS_FILE
        app.DATA_DIR = Path(self.tmp.name) / "data_daytrade"
        app.SYMBOLS_FILE = app.DATA_DIR / "symbols.json"
        app.RUNTIME_CONFIG_FILE = app.DATA_DIR / "runtime_config.json"

    def tearDown(self):
        app.DATA_DIR = self.old_data_dir
        app.SYMBOLS_FILE = self.old_symbols_file
        app.RUNTIME_CONFIG_FILE = self.old_runtime_config_file
        daytrade_status.DATA_DIR = self.old_status_data_dir
        daytrade_status.FEED_STATUS_FILE = self.old_status_feed
        daytrade_status.RUNNER_STATUS_FILE = self.old_status_runner
        self.tmp.cleanup()

    def test_load_history_keeps_latest_intraday_session(self):
        write_history(
            app.DATA_DIR / "TEST" / "paper_history_daytrade.csv",
            [
                {"timestamp": "2026-05-20 09:00:00", "equity": "1.0", "price": "100"},
                {"timestamp": "2026-05-20 14:56:00", "equity": "1.001", "price": "101"},
                {"timestamp": "2026-05-21 09:00:00", "equity": "1.0", "price": "102"},
                {"timestamp": "2026-05-21 09:05:00", "equity": "1.002", "price": "103"},
            ],
        )

        rows = app.load_history("TEST")

        self.assertEqual([row["date"] for row in rows], ["09:00", "09:05"])
        self.assertEqual([row["price"] for row in rows], [102.0, 103.0])
        self.assertTrue(all(row["timestamp"].strftime("%Y-%m-%d") == "2026-05-21" for row in rows))

    def test_price_chart_uses_elapsed_time_for_x_axis(self):
        rows = [
            {"timestamp": app.parse_history_ts("2026-05-21 09:00:00"), "date": "09:00", "price": 100.0, "vwap": 99.8},
            {"timestamp": app.parse_history_ts("2026-05-21 09:05:00"), "date": "09:05", "price": 101.0, "vwap": 100.2},
            {"timestamp": app.parse_history_ts("2026-05-21 10:55:00"), "date": "10:55", "price": 102.0, "vwap": 101.2},
            {"timestamp": app.parse_history_ts("2026-05-21 10:56:00"), "date": "10:56", "price": 101.5, "vwap": 101.3},
        ]

        svg = app.build_price_svg(rows)
        points = re.search(r'<polyline[^>]+stroke="#2563eb"[^>]+points="([^"]+)"', svg).group(1).split()
        second_x = float(points[1].split(",")[0])

        self.assertLess(second_x, 100.0)
        self.assertIn("05/21 09:00", svg)
        self.assertIn("価格チャート", svg)

    def test_weekend_hides_chart(self):
        rows = [
            {"timestamp": app.parse_history_ts("2026-05-21 09:00:00"), "date": "09:00", "price": 100.0},
            {"timestamp": app.parse_history_ts("2026-05-21 09:05:00"), "date": "09:05", "price": 101.0},
        ]
        st = {"data_freshness_status": "market_closed"}
        now = dt.datetime(2026, 5, 23, 10, 0)

        self.assertFalse(app.should_show_chart(st, rows, now))
        self.assertEqual(app.chart_block(st, rows, now), "")

    def test_condition_text_explains_volume_requirement(self):
        st = {
            "strategy_reason": "volume confirmation not met",
            "data_freshness_status": "fresh",
            "volume_ratio": 0.83,
            "current_position_today": "FLAT",
        }

        text = app.condition_text(st, {"min_volume_ratio": 1.2}, dt.datetime(2026, 5, 21, 10, 0))

        self.assertIn("出来高倍率 0.83 / 必要 1.2", text)

    def test_current_freshness_uses_display_time(self):
        st = {
            "latest_ts": "2026-05-21 10:58:00",
            "data_freshness_status": "future",
            "data_age_sec": -46559,
        }

        updated = app.current_freshness_state(st, {}, dt.datetime(2026, 5, 25, 10, 0))

        self.assertEqual(updated["data_freshness_status"], "blocked_stale")
        self.assertGreater(updated["data_age_sec"], 0)
        self.assertEqual(st["data_freshness_status"], "future")

    def test_market_closed_flat_display_clears_stale_blocked_signal(self):
        st = {
            "latest_ts": "2026-05-22 15:30:00",
            "current_position_today": "FLAT",
            "signal": "BLOCKED",
            "last_action": "WAIT",
            "strategy_reason": "volume confirmation not met",
        }

        updated = app.current_freshness_state(st, {}, dt.datetime(2026, 5, 22, 16, 0))

        self.assertEqual(updated["data_freshness_status"], "market_closed")
        self.assertEqual(updated["signal"], "WAIT")
        self.assertEqual(updated["last_action"], "MARKET_CLOSED")
        self.assertEqual(updated["strategy_reason"], "market closed")
        self.assertEqual(st["signal"], "BLOCKED")

    def test_condition_text_for_long_shows_exit_levels(self):
        st = {
            "current_position_today": "LONG",
            "stop_price": 99.0,
            "partial_target_price": 103.0,
            "target_price": 106.0,
        }

        text = app.condition_text(st, {"force_flat_time": "15:25"}, dt.datetime(2026, 5, 21, 10, 0))

        self.assertIn("損切り 99", text)
        self.assertIn("一部利確 103", text)
        self.assertIn("利確 106", text)

    def test_badge_text_matches_short_side(self):
        self.assertEqual(app.badge_text("ENTRY", "SELL_BREAKDOWN"), "売り")
        self.assertEqual(app.badge_text("EXIT", "BUY_TARGET"), "買戻し")
        self.assertEqual(app.badge_text("ENTRY", "BUY_BREAKOUT"), "買い")

    def test_route_path_ignores_refresh_query(self):
        self.assertEqual(app.route_path("/?_=123"), "/")
        self.assertEqual(app.route_path("/index.html?_=123"), "/index.html")
        self.assertEqual(app.route_path("/status.json?_=123"), "/status.json")

    def test_build_page_has_summary_and_no_weekend_chart(self):
        app.DATA_DIR.mkdir(parents=True, exist_ok=True)
        app.SYMBOLS_FILE.write_text('[{"symbol":"TEST","name":"Demo","enabled":true}]', encoding="utf-8")
        (app.DATA_DIR / "runtime_config.json").write_text('{"allow_live_order": false}', encoding="utf-8")
        (app.DATA_DIR / "TEST").mkdir()
        (app.DATA_DIR / "TEST" / "paper_state_daytrade.json").write_text(
            """
            {
              "symbol": "TEST",
              "current_position_today": "FLAT",
              "last_action": "WAIT",
              "signal": "WAIT",
              "strategy_reason": "volume confirmation not met",
              "data_freshness_status": "market_closed",
              "latest_ts": "2026-05-21 09:05:00",
              "last_runner_at": "2026-05-23 10:00:00",
              "data_age_sec": 100000,
              "volume_ratio": 0.8
            }
            """,
            encoding="utf-8",
        )
        write_history(
            app.DATA_DIR / "TEST" / "paper_history_daytrade.csv",
            [
                {"timestamp": "2026-05-21 09:00:00", "equity": "1.0", "price": "100"},
                {"timestamp": "2026-05-21 09:05:00", "equity": "1.0", "price": "101"},
            ],
        )

        html = app.build_page()

        self.assertIn("実売買", html)
        self.assertIn("feed", html)
        self.assertIn("次の条件", html)
        self.assertIn("累計損益", html)
        self.assertIn("本日損益", html)
        self.assertIn('data-detail-key="TEST"', html)
        self.assertIn('id="daytrade-content"', html)
        self.assertIn("daytrade.openDetails", html)
        self.assertIn("window.scrollTo(0, savedY)", html)
        self.assertIn("fetch(`${window.location.pathname}", html)
        self.assertNotIn("location.reload()", html)
        if dt.datetime.now().weekday() >= 5:
            self.assertNotIn("価格チャート", html)

    def test_stale_eod_reason_is_user_facing(self):
        st = {"last_action": "SELL_STALE_EOD", "strategy_reason": "consecutive loss limit reached"}

        self.assertEqual(app.display_reason_text(st), "データ停止後にノーポジ化済み")


if __name__ == "__main__":
    unittest.main()
