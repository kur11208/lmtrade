from __future__ import annotations

from pathlib import Path

import daytrade_runner as runner

CONFIG_FILE = runner.DATA_DIR / "runtime_config.json"

DEFAULT_RUNTIME_CONFIG = {
    "trading_mode": "paper",
    "market_data_adapter": "csv",
    "order_adapter": "paper",
    "allow_live_order": False,
    "api_usage": "market_data_only",
    "order_queue_enabled": True,
    "order_queue_file": "order_queue.jsonl",
    "order_events_file": "order_events.jsonl",
    "evaluation_log_enabled": True,
    "evaluation_log_file": "evaluation_log.jsonl",
    "evaluation_log_interval_seconds": 300,
    "screener_auto_apply": True,
    "screener_top_n": 0,
    "screener_refresh_times": ["09:40", "12:40"],
    "screener_refresh_window_minutes": 20,
    "demo_feed_max_symbols": 40,
    "portfolio_daily_loss_limit_rate": 0.006,
    "portfolio_max_position_w": 0.40,
    "portfolio_max_entry_orders_per_day": 4,
    "portfolio_max_open_positions": 2,
    "portfolio_max_consecutive_losses": 3,
    "portfolio_block_on_feed_unhealthy": True,
    "quality_min_open_coverage_rate": 0.95,
    "quality_min_close_coverage_rate": 0.95,
    "quality_min_opening_bar_count": 20,
    "quality_max_opening_gap_minutes": 3,
    "rss_recover_opening_backfill_enabled": True,
    "rss_recover_opening_backfill_cooldown_seconds": 900,
    "rss_recover_opening_backfill_max_symbols": 159,
    "rss_recover_opening_backfill_max_attempts_per_day": 3,
}


def load_runtime_config() -> dict:
    loaded = runner.load_json(CONFIG_FILE, {})
    cfg = dict(DEFAULT_RUNTIME_CONFIG)
    if isinstance(loaded, dict):
        cfg.update(loaded)

    # Hard safety rail: this project can consume market data APIs, but must not
    # place live orders until the user explicitly asks for a separate live-order build.
    cfg["allow_live_order"] = False
    if str(cfg.get("api_usage", "")).strip() != "market_data_only":
        cfg["api_usage"] = "market_data_only"
    if str(cfg.get("order_adapter", "")).strip().lower() not in ("paper", "noop"):
        cfg["order_adapter"] = "paper"
    return cfg

def data_path_from_config(key: str) -> Path:
    cfg = load_runtime_config()
    value = str(cfg.get(key, DEFAULT_RUNTIME_CONFIG[key])).strip()
    return runner.DATA_DIR / value
