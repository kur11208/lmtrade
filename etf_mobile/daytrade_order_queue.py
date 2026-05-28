from __future__ import annotations

import json
import uuid
from datetime import datetime

import daytrade_config
import daytrade_runner as runner


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def queue_path():
    return daytrade_config.data_path_from_config("order_queue_file")


def events_path():
    return daytrade_config.data_path_from_config("order_events_file")


def append_jsonl(path, row: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def load_jsonl(path):
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def order_side_from_action(action: str) -> str:
    if action.startswith("BUY_"):
        return "BUY"
    if action.startswith("SELL_"):
        return "SELL"
    return ""


def build_order_candidate(state: dict) -> dict | None:
    cfg = daytrade_config.load_runtime_config()
    if not cfg.get("order_queue_enabled", True):
        return None

    signal = str(state.get("signal", "")).strip()
    action = str(state.get("last_action", "")).strip()
    side = order_side_from_action(action)
    if signal not in ("ENTRY", "EXIT") or not side:
        return None

    symbol = str(state.get("symbol", "")).strip()
    latest_ts = str(state.get("latest_ts", "")).strip()
    if not symbol or not latest_ts:
        return None

    if side == "BUY":
        weight = runner.parse_float(state.get("current_w_today"), 0.0)
        price_ref = state.get("entry_price") or state.get("last_price")
        order_type = "ENTRY"
    else:
        weight = runner.parse_float(state.get("last_exit_w"), runner.parse_float(state.get("current_w_today"), 0.0))
        price_ref = state.get("exit_price") or state.get("last_price")
        order_type = "EXIT"

    stable_key = f"{symbol}|{latest_ts}|{action}|{side}|{cfg.get('trading_mode')}"
    return {
        "id": str(uuid.uuid5(uuid.NAMESPACE_URL, stable_key)),
        "created_at": now_text(),
        "source_ts": latest_ts,
        "symbol": symbol,
        "side": side,
        "order_type": order_type,
        "action": action,
        "reason": state.get("strategy_reason", ""),
        "weight": weight,
        "price_ref": price_ref,
        "stop_price": state.get("stop_price"),
        "target_price": state.get("target_price"),
        "mode": cfg.get("trading_mode", "paper"),
        "order_adapter": cfg.get("order_adapter", "paper"),
        "allow_live_order": False,
        "status": "NEW",
    }


def enqueue_from_state(state: dict):
    candidate = build_order_candidate(state)
    if candidate is None:
        return None

    path = queue_path()
    existing_ids = {str(row.get("id", "")) for row in load_jsonl(path)}
    if candidate["id"] in existing_ids:
        return None

    append_jsonl(path, candidate)
    append_jsonl(events_path(), {
        "created_at": now_text(),
        "order_id": candidate["id"],
        "event": "QUEUED",
        "symbol": candidate["symbol"],
        "side": candidate["side"],
        "mode": candidate["mode"],
        "live_order": False,
    })
    return candidate


def pending_orders():
    return [row for row in load_jsonl(queue_path()) if str(row.get("status", "NEW")) == "NEW"]
