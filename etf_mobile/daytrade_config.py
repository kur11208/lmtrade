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
    "screener_auto_apply": True,
    "screener_top_n": 4,
    "screener_refresh_times": ["09:40", "12:40"],
    "screener_refresh_window_minutes": 20,
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
