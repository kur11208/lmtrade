from __future__ import annotations

import datetime as dt
import json

import daytrade_config
import daytrade_runner as runner
import daytrade_status


RISK_STATUS_FILE = runner.DATA_DIR / "portfolio_risk_status.json"
ENTRY_ORDER_COUNT_CACHE = {}


def today_text(now: dt.datetime | None = None) -> str:
    return (now or dt.datetime.now()).strftime("%Y-%m-%d")


def load_symbol_state(symbol: str) -> dict:
    return runner.load_json(runner.DATA_DIR / symbol / "paper_state_daytrade.json", {})


def is_open_position(state: dict) -> bool:
    return str(state.get("current_position_today", "")).strip().upper() in ("LONG", "SHORT")


def iter_symbol_states():
    seen = set()
    for item in runner.load_symbols():
        symbol = str(item.get("symbol", "")).strip()
        if symbol and symbol not in seen:
            seen.add(symbol)
            yield symbol, load_symbol_state(symbol)
    if runner.DATA_DIR.exists():
        for path in runner.DATA_DIR.glob("*/paper_state_daytrade.json"):
            symbol = path.parent.name
            if symbol and symbol not in seen:
                seen.add(symbol)
                yield symbol, runner.load_json(path, {})


def iter_enabled_symbol_states():
    yield from iter_symbol_states()


def today_closed_trades(states: list[tuple[str, dict]], now: dt.datetime) -> list[dict]:
    today = today_text(now)
    trades = []
    for symbol, state in states:
        for trade in state.get("recent_trades", []):
            if not isinstance(trade, dict):
                continue
            exit_ts = str(trade.get("exit_ts", ""))
            if not exit_ts.startswith(today):
                continue
            row = dict(trade)
            row["symbol"] = symbol
            trades.append(row)
    trades.sort(key=lambda row: str(row.get("exit_ts", "")))
    return trades


def consecutive_losses(trades: list[dict]) -> int:
    count = 0
    for trade in reversed(trades):
        if runner.parse_float(trade.get("pnl_rate"), 0.0) > 0:
            break
        count += 1
    return count


def count_today_entry_orders(now: dt.datetime | None = None) -> int:
    if daytrade_config.load_runtime_config().get("order_queue_enabled", True) is False:
        return 0
    path = daytrade_config.data_path_from_config("order_queue_file")
    if not path.exists():
        return 0
    today = today_text(now)
    try:
        stat = path.stat()
        cache_key = (str(path), today, stat.st_mtime_ns, stat.st_size)
        cached = ENTRY_ORDER_COUNT_CACHE.get(cache_key)
        if cached is not None:
            return cached
    except Exception:
        cache_key = None
    count = 0
    try:
        with path.open("r", encoding="utf-8-sig") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if str(row.get("created_at", "")).startswith(today) and str(row.get("order_type", "")) == "ENTRY":
                    count += 1
    except Exception:
        return 0
    if cache_key is not None:
        ENTRY_ORDER_COUNT_CACHE.clear()
        ENTRY_ORDER_COUNT_CACHE[cache_key] = count
    return count


def portfolio_snapshot(now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now()
    states = list(iter_symbol_states())
    start_total = 0.0
    equity_total = 0.0
    open_positions = 0
    open_weight = 0.0
    today = today_text(now)
    for _, state in states:
        capital = runner.parse_float(state.get("sim_capital_yen"), runner.DEFAULT_SETTINGS["sim_capital_yen"])
        equity = runner.parse_float(state.get("equity"), 1.0)
        if str(state.get("latest_date", "")).strip() == today:
            day_start = runner.parse_float(state.get("day_start_equity"), equity)
        else:
            day_start = equity
        start_total += capital * day_start
        equity_total += capital * equity
        if is_open_position(state):
            open_positions += 1
            open_weight += runner.parse_float(state.get("current_w_today"), 0.0)
    trades = today_closed_trades(states, now)
    daily_pnl_rate = 0.0 if start_total <= 0 else equity_total / start_total - 1.0
    return {
        "checked_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "symbol_count": len(states),
        "day_start_amount": start_total,
        "equity_amount": equity_total,
        "daily_pnl_rate": daily_pnl_rate,
        "open_positions": open_positions,
        "open_position_w": open_weight,
        "entry_orders_today": count_today_entry_orders(now),
        "closed_trades_today": len(trades),
        "consecutive_losses_today": consecutive_losses(trades),
    }


def feed_is_unhealthy(now: dt.datetime) -> tuple[bool, str]:
    feed = daytrade_status.read_feed_status()
    health = daytrade_status.feed_health(feed, now)
    tone = str(health.get("tone", ""))
    if tone == "bad":
        return True, f"feed unhealthy: {health.get('label', '')}"
    return False, ""


def entry_block_reason(proposed_weight: float = 0.0, now: dt.datetime | None = None, check_feed: bool = True) -> str:
    now = now or dt.datetime.now()
    cfg = daytrade_config.load_runtime_config()
    snapshot = portfolio_snapshot(now)

    market_data_adapter = str(cfg.get("market_data_adapter", "")).strip().lower()
    if check_feed and market_data_adapter == "rss" and cfg.get("portfolio_block_on_feed_unhealthy", True):
        bad_feed, feed_reason = feed_is_unhealthy(now)
        if bad_feed:
            write_risk_status(snapshot, blocked=True, reason=feed_reason)
            return feed_reason

    daily_limit = runner.parse_float(cfg.get("portfolio_daily_loss_limit_rate"), 0.006)
    if daily_limit > 0 and snapshot["daily_pnl_rate"] <= -abs(daily_limit):
        reason = f"portfolio daily loss limit reached {snapshot['daily_pnl_rate']:.4f}"
        write_risk_status(snapshot, blocked=True, reason=reason)
        return reason

    max_losses = runner.parse_int(cfg.get("portfolio_max_consecutive_losses"), 0)
    if max_losses > 0 and snapshot["consecutive_losses_today"] >= max_losses:
        reason = "portfolio consecutive loss limit reached"
        write_risk_status(snapshot, blocked=True, reason=reason)
        return reason

    max_weight = runner.parse_float(cfg.get("portfolio_max_position_w"), 0.40)
    if max_weight > 0 and snapshot["open_position_w"] + max(0.0, proposed_weight) > max_weight:
        reason = "portfolio max position weight reached"
        write_risk_status(snapshot, blocked=True, reason=reason)
        return reason

    max_positions = runner.parse_int(cfg.get("portfolio_max_open_positions"), 2)
    if max_positions > 0 and snapshot["open_positions"] >= max_positions:
        reason = "portfolio max open positions reached"
        write_risk_status(snapshot, blocked=True, reason=reason)
        return reason

    max_entries = runner.parse_int(cfg.get("portfolio_max_entry_orders_per_day"), 4)
    if max_entries > 0 and snapshot["entry_orders_today"] >= max_entries:
        reason = "portfolio max entry orders reached"
        write_risk_status(snapshot, blocked=True, reason=reason)
        return reason

    write_risk_status(snapshot, blocked=False, reason="")
    return ""


def write_risk_status(snapshot: dict, blocked: bool, reason: str):
    payload = dict(snapshot)
    payload.update({
        "kind": "portfolio_risk",
        "blocked": blocked,
        "reason": reason,
    })
    runner.save_json(RISK_STATUS_FILE, payload)
