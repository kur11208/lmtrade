from __future__ import annotations

from pathlib import Path
import argparse
import bisect
import csv
import datetime as dt
import json
import math
import os
import time

import daytrade_market_calendar as market_calendar
import daytrade_status

try:
    import daytrade_order_queue
except Exception:
    daytrade_order_queue = None

DATA_DIR = Path("data_daytrade")
SYMBOLS_FILE = DATA_DIR / "symbols.json"

SETTINGS_KEYS = (
    "strategy_enabled",
    "strategy_mode",
    "opening_range_minutes",
    "breakout_buffer_rate",
    "vwap_slop_rate",
    "risk_per_trade_rate",
    "daily_loss_limit_rate",
    "max_position_w",
    "min_position_w",
    "stop_buffer_rate",
    "take_profit_r",
    "partial_take_profit_r",
    "partial_exit_rate",
    "trailing_stop_r",
    "fee_rate",
    "slippage_rate",
    "spread_rate",
    "tick_size",
    "share_unit",
    "sim_capital_yen",
    "min_notional_yen",
    "max_bar_volume_participation_rate",
    "max_trades_per_day",
    "max_consecutive_losses",
    "cooldown_minutes",
    "use_volume_filter",
    "volume_lookback_bars",
    "min_volume_ratio",
    "use_gap_filter",
    "min_gap_rate",
    "max_gap_rate",
    "use_market_filter",
    "market_filter_symbol",
    "market_filter_min_return",
    "market_filter_require_vwap",
    "market_filter_strict",
    "bar_interval_minutes",
    "max_expected_bar_gap_minutes",
    "stale_data_warn_seconds",
    "stale_data_block_seconds",
    "block_on_stale_data",
    "block_on_data_warning",
    "no_new_entry_time",
    "force_flat_time",
)

DEFAULT_SETTINGS = {
    "strategy_enabled": True,
    "strategy_mode": "both",
    "opening_range_minutes": 30,
    "breakout_buffer_rate": 0.0005,
    "vwap_slop_rate": 0.0010,
    "risk_per_trade_rate": 0.0030,
    "daily_loss_limit_rate": 0.0100,
    "max_position_w": 0.40,
    "min_position_w": 0.05,
    "stop_buffer_rate": 0.0100,
    "take_profit_r": 1.20,
    "partial_take_profit_r": 0.80,
    "partial_exit_rate": 0.50,
    "trailing_stop_r": 1.00,
    "fee_rate": 0.0005,
    "slippage_rate": 0.0005,
    "spread_rate": 0.0005,
    "tick_size": 1.0,
    "share_unit": 100,
    "sim_capital_yen": 10000.0,
    "min_notional_yen": 0.0,
    "max_bar_volume_participation_rate": 0.02,
    "max_trades_per_day": 2,
    "max_consecutive_losses": 2,
    "cooldown_minutes": 20,
    "use_volume_filter": True,
    "volume_lookback_bars": 6,
    "min_volume_ratio": 1.20,
    "use_gap_filter": True,
    "min_gap_rate": -0.0400,
    "max_gap_rate": 0.0500,
    "use_market_filter": False,
    "market_filter_symbol": "1570",
    "market_filter_min_return": -0.0030,
    "market_filter_require_vwap": True,
    "market_filter_strict": False,
    "bar_interval_minutes": 1,
    "max_expected_bar_gap_minutes": 3,
    "stale_data_warn_seconds": 90,
    "stale_data_block_seconds": 300,
    "block_on_stale_data": False,
    "block_on_data_warning": False,
    "no_new_entry_time": "15:00",
    "force_flat_time": "15:25",
}

CHECK_INTERVAL_SEC = float(os.environ.get("DAYTRADE_CHECK_INTERVAL_SEC", "1"))
SETTINGS_CACHE = {}
BAR_CACHE = {}
HISTORY_TS_CACHE = {}


def read_text_auto(path: Path) -> str:
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return path.read_text(encoding=enc)
        except Exception:
            pass
    return path.read_text(encoding="utf-8", errors="replace")


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(read_text_auto(path))
    except Exception:
        return default


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def parse_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def parse_int(value, default=0):
    try:
        return int(float(value))
    except Exception:
        return default


def parse_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    return default


def clamp(value, low, high, default=0.0):
    return max(low, min(high, parse_float(value, default)))


def parse_time(value, default: str) -> dt.time:
    text = str(value or default).strip()
    try:
        hour_s, minute_s = text.split(":", 1)
        return dt.time(int(hour_s), int(minute_s))
    except Exception:
        hour_s, minute_s = default.split(":", 1)
        return dt.time(int(hour_s), int(minute_s))


def parse_ts(value) -> dt.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return dt.datetime.fromisoformat(text.replace("T", " "))
    except Exception:
        return None


def ts_text(ts: dt.datetime) -> str:
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def is_daytrade_market_time(ts: dt.datetime) -> bool:
    return market_calendar.is_market_time(ts)


def calc_data_age_sec(latest_ts, now: dt.datetime) -> int | None:
    ts = parse_ts(latest_ts)
    if ts is None:
        return None
    return int((now - ts).total_seconds())


def load_symbols():
    return [row for row in load_symbol_universe() if row.get("enabled", True) is not False]


def load_symbol_universe():
    rows = load_json(SYMBOLS_FILE, [])
    universe = []
    for row in rows:
        symbol = str(row.get("symbol", "")).strip()
        if not symbol or row.get("candidate", True) is False:
            continue
        item = dict(row)
        item["symbol"] = symbol
        universe.append(item)
    return universe


def load_symbol_settings(symbol: str) -> dict:
    path = DATA_DIR / symbol / "paper_settings.json"
    if not path.exists():
        return dict(DEFAULT_SETTINGS)

    stat = path.stat()
    cache_key = (str(path), stat.st_mtime_ns, stat.st_size)
    cached = SETTINGS_CACHE.get(symbol)
    if cached and cached.get("cache_key") == cache_key:
        return dict(cached["settings"])

    loaded = load_json(path, {})
    settings = dict(DEFAULT_SETTINGS)
    for key in SETTINGS_KEYS:
        if key in loaded:
            settings[key] = loaded[key]
    SETTINGS_CACHE[symbol] = {
        "cache_key": cache_key,
        "settings": dict(settings),
    }
    return settings


def normalize_bar(row: dict):
    ts = parse_ts(row.get("timestamp"))
    close = parse_float(row.get("close"), None)
    if ts is None or close is None or close <= 0:
        return None

    open_price = parse_float(row.get("open"), close)
    high = parse_float(row.get("high"), max(open_price, close))
    low = parse_float(row.get("low"), min(open_price, close))
    volume = max(0.0, parse_float(row.get("volume"), 0.0))
    high = max(high, open_price, close)
    low = min(low, open_price, close)
    return {
        "timestamp": ts_text(ts),
        "ts": ts,
        "date": ts.strftime("%Y-%m-%d"),
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def load_intraday_bars(symbol: str) -> list[dict]:
    path = DATA_DIR / symbol / "intraday_bars.csv"
    if not path.exists():
        return []

    stat = path.stat()
    cache_key = (str(path), stat.st_mtime_ns, stat.st_size)
    cached = BAR_CACHE.get(symbol)
    if cached and cached.get("cache_key") == cache_key:
        return list(cached["rows"])

    rows_by_ts = {}
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                bar = normalize_bar(row)
                if bar is None:
                    continue
                rows_by_ts[bar["timestamp"]] = bar
    except Exception:
        return []

    rows = sorted(rows_by_ts.values(), key=lambda x: x["ts"])
    BAR_CACHE[symbol] = {
        "cache_key": cache_key,
        "rows": list(rows),
    }
    return rows


def state_paths(symbol: str):
    sym_dir = DATA_DIR / symbol
    return (
        sym_dir,
        sym_dir / "paper_state_daytrade.json",
        sym_dir / "mobile_runner_state_daytrade.json",
    )


def update_sim_fields(state: dict) -> dict:
    equity = parse_float(state.get("equity"), 1.0)
    sim_capital = parse_float(state.get("sim_capital_yen"), DEFAULT_SETTINGS["sim_capital_yen"])
    state["sim_amount_yen"] = equity * sim_capital
    state["sim_pnl_rate"] = equity - 1.0
    day_start = parse_float(state.get("day_start_equity"), equity)
    state["daily_pnl_rate"] = equity / day_start - 1.0 if day_start > 0 else 0.0
    return state


def day_bars_until(all_bars: list[dict], bar: dict) -> list[dict]:
    date_s = bar["date"]
    limit = bar["ts"]
    return [x for x in all_bars if x["date"] == date_s and x["ts"] <= limit]


def calc_vwap(rows: list[dict]) -> float | None:
    if not rows:
        return None
    vol_sum = sum(max(0.0, row["volume"]) for row in rows)
    if vol_sum > 0:
        return sum(row["close"] * max(0.0, row["volume"]) for row in rows) / vol_sum
    return sum(row["close"] for row in rows) / len(rows)


def calc_opening_range(rows: list[dict], minutes: int) -> tuple[float | None, float | None, bool]:
    if not rows:
        return None, None, False

    session_start = dt.datetime.combine(rows[-1]["ts"].date(), dt.time(9, 0))
    cutoff = session_start + dt.timedelta(minutes=max(1, minutes))
    opening_rows = [row for row in rows if session_start <= row["ts"] < cutoff]
    if not opening_rows:
        return None, None, False

    high = max(row["high"] for row in opening_rows)
    low = min(row["low"] for row in opening_rows)
    done = rows[-1]["ts"] >= cutoff
    return high, low, done


def previous_close(all_bars: list[dict], date_s: str) -> float | None:
    prior = [row for row in all_bars if row["date"] < date_s]
    if not prior:
        return None
    return prior[-1]["close"]


def calc_gap_rate(all_bars: list[dict], rows_today: list[dict]) -> float | None:
    if not rows_today:
        return None
    prev = previous_close(all_bars, rows_today[-1]["date"])
    if prev is None or prev <= 0:
        return None
    return rows_today[0]["open"] / prev - 1.0


def calc_volume_ratio(rows: list[dict], lookback: int) -> float | None:
    if len(rows) < 2:
        return None
    lookback = max(1, lookback)
    prior = rows[max(0, len(rows) - 1 - lookback):len(rows) - 1]
    values = [row["volume"] for row in prior if row["volume"] > 0]
    if not values:
        return None
    return rows[-1]["volume"] / (sum(values) / len(values))


def calc_data_warning(rows_today: list[dict], max_gap_minutes: int) -> str:
    if len(rows_today) < 2:
        return ""
    max_gap_minutes = max(1, max_gap_minutes)
    gaps = []
    for prev, cur in zip(rows_today, rows_today[1:]):
        delta = (cur["ts"] - prev["ts"]).total_seconds() / 60.0
        if delta > max_gap_minutes:
            gaps.append(int(delta))
    if not gaps:
        return ""
    return f"bar gap {max(gaps)}m"


def update_freshness_fields(state: dict, now: dt.datetime, settings: dict) -> dict:
    warn_sec = max(1, parse_int(settings.get("stale_data_warn_seconds"), DEFAULT_SETTINGS["stale_data_warn_seconds"]))
    block_sec = max(warn_sec, parse_int(settings.get("stale_data_block_seconds"), DEFAULT_SETTINGS["stale_data_block_seconds"]))
    age_sec = calc_data_age_sec(state.get("latest_ts"), now)

    if age_sec is not None and not is_daytrade_market_time(now):
        status = "market_closed"
        stale = False
        blocked = False
    elif age_sec is None:
        status = "missing"
        stale = True
        blocked = parse_bool(settings.get("block_on_stale_data"), DEFAULT_SETTINGS["block_on_stale_data"])
    elif age_sec < -5:
        status = "future"
        stale = False
        blocked = False
    elif age_sec <= warn_sec:
        status = "fresh"
        stale = False
        blocked = False
    elif age_sec <= block_sec:
        status = "stale"
        stale = True
        blocked = False
    else:
        status = "blocked_stale"
        stale = True
        blocked = parse_bool(settings.get("block_on_stale_data"), DEFAULT_SETTINGS["block_on_stale_data"])

    state["data_age_sec"] = age_sec
    state["data_freshness_status"] = status
    state["data_stale"] = stale
    state["data_stale_blocked"] = blocked
    state["stale_data_warn_seconds"] = warn_sec
    state["stale_data_block_seconds"] = block_sec
    if blocked and state.get("current_position_today") != "LONG":
        state["signal"] = "BLOCKED"
        state["strategy_reason"] = "stale data blocked"
    return state


def apply_settings_snapshot(state: dict, settings: dict) -> dict:
    force_flat_time = parse_time(settings.get("force_flat_time"), DEFAULT_SETTINGS["force_flat_time"])
    no_new_entry_time = parse_time(settings.get("no_new_entry_time"), DEFAULT_SETTINGS["no_new_entry_time"])
    state["force_flat_time"] = force_flat_time.strftime("%H:%M")
    state["no_new_entry_time"] = no_new_entry_time.strftime("%H:%M")
    state["bar_interval_minutes"] = max(1, parse_int(settings.get("bar_interval_minutes"), DEFAULT_SETTINGS["bar_interval_minutes"]))
    state["strategy_enabled"] = settings.get("strategy_enabled", True) is not False
    state["sim_capital_yen"] = parse_float(settings.get("sim_capital_yen"), DEFAULT_SETTINGS["sim_capital_yen"])
    state["trade_cost_rate"] = trade_fee_rate(settings) + clamp(settings.get("slippage_rate"), 0.0, 0.05, DEFAULT_SETTINGS["slippage_rate"])
    state["spread_rate"] = clamp(settings.get("spread_rate"), 0.0, 0.05, DEFAULT_SETTINGS["spread_rate"])
    state["max_position_w"] = clamp(settings.get("max_position_w"), 0.0, 1.0, DEFAULT_SETTINGS["max_position_w"])
    state["risk_per_trade_rate"] = clamp(settings.get("risk_per_trade_rate"), 0.0, 0.20, DEFAULT_SETTINGS["risk_per_trade_rate"])
    state["daily_loss_limit_rate"] = clamp(settings.get("daily_loss_limit_rate"), 0.0, 0.50, DEFAULT_SETTINGS["daily_loss_limit_rate"])
    state["take_profit_r"] = max(0.10, parse_float(settings.get("take_profit_r"), DEFAULT_SETTINGS["take_profit_r"]))
    state["partial_take_profit_r"] = max(0.10, parse_float(settings.get("partial_take_profit_r"), DEFAULT_SETTINGS["partial_take_profit_r"]))
    return state


def round_price(price: float, tick_size: float, side: str) -> float:
    tick = parse_float(tick_size, 0.0)
    if tick <= 0:
        return float(price)
    units = price / tick
    if side == "buy":
        return math.ceil(units - 1e-12) * tick
    if side == "sell":
        return math.floor(units + 1e-12) * tick
    return round(units) * tick


def effective_entry_price(raw_price: float, settings: dict) -> float:
    slippage = clamp(settings.get("slippage_rate"), 0.0, 0.05, DEFAULT_SETTINGS["slippage_rate"])
    spread = clamp(settings.get("spread_rate"), 0.0, 0.05, DEFAULT_SETTINGS["spread_rate"])
    price = raw_price * (1.0 + slippage + spread * 0.5)
    return round_price(price, parse_float(settings.get("tick_size"), DEFAULT_SETTINGS["tick_size"]), "buy")


def effective_exit_price(raw_price: float, settings: dict) -> float:
    slippage = clamp(settings.get("slippage_rate"), 0.0, 0.05, DEFAULT_SETTINGS["slippage_rate"])
    spread = clamp(settings.get("spread_rate"), 0.0, 0.05, DEFAULT_SETTINGS["spread_rate"])
    price = raw_price * (1.0 - slippage - spread * 0.5)
    return max(0.01, round_price(price, parse_float(settings.get("tick_size"), DEFAULT_SETTINGS["tick_size"]), "sell"))


def trade_fee_rate(settings: dict) -> float:
    return clamp(settings.get("fee_rate"), 0.0, 0.05, DEFAULT_SETTINGS["fee_rate"])


def calc_liquidity_position_cap(bar: dict, state: dict, settings: dict) -> float:
    participation = clamp(
        settings.get("max_bar_volume_participation_rate"),
        0.0,
        1.0,
        DEFAULT_SETTINGS["max_bar_volume_participation_rate"],
    )
    if participation <= 0:
        return 1.0
    sim_capital = parse_float(settings.get("sim_capital_yen"), DEFAULT_SETTINGS["sim_capital_yen"])
    equity = parse_float(state.get("equity"), 1.0)
    account_yen = max(1.0, sim_capital * equity)
    bar_capacity_yen = max(0.0, bar["close"] * bar["volume"] * participation)
    return max(0.0, min(1.0, bar_capacity_yen / account_yen))


def market_filter_result(symbol: str, bar: dict, settings: dict):
    if not parse_bool(settings.get("use_market_filter"), DEFAULT_SETTINGS["use_market_filter"]):
        return True, "market filter off", {}

    market_symbol = str(settings.get("market_filter_symbol") or "").strip()
    if not market_symbol or market_symbol == symbol:
        return True, "market filter skipped", {}

    rows = load_intraday_bars(market_symbol)
    same_day = [row for row in rows if row["date"] == bar["date"] and row["ts"] <= bar["ts"]]
    strict = parse_bool(settings.get("market_filter_strict"), DEFAULT_SETTINGS["market_filter_strict"])
    if not same_day:
        return (False if strict else True), "market data unavailable", {}

    latest = same_day[-1]
    market_vwap = calc_vwap(same_day)
    open_price = same_day[0]["open"]
    market_return = latest["close"] / open_price - 1.0 if open_price > 0 else None
    min_return = parse_float(settings.get("market_filter_min_return"), DEFAULT_SETTINGS["market_filter_min_return"])
    require_vwap = parse_bool(settings.get("market_filter_require_vwap"), DEFAULT_SETTINGS["market_filter_require_vwap"])

    ok = True
    reasons = []
    if market_return is not None and market_return < min_return:
        ok = False
        reasons.append("market return weak")
    if require_vwap and market_vwap is not None and latest["close"] < market_vwap:
        ok = False
        reasons.append("market under vwap")

    if ok:
        reasons.append("market ok")
    return ok, ",".join(reasons), {
        "market_symbol": market_symbol,
        "market_return": market_return,
        "market_vwap": market_vwap,
        "market_price": latest["close"],
    }


def reset_for_new_day(state: dict, symbol: str, bar: dict, settings: dict) -> dict:
    date_s = bar["date"]
    if state.get("latest_date") == date_s:
        return state

    equity = parse_float(state.get("equity"), 1.0)
    closed_trade_count = parse_int(state.get("closed_trade_count"), 0)
    win_count = parse_int(state.get("win_count"), 0)
    loss_count = parse_int(state.get("loss_count"), 0)
    gross_profit_rate = parse_float(state.get("gross_profit_rate"), 0.0)
    gross_loss_rate = parse_float(state.get("gross_loss_rate"), 0.0)
    recent_trades = list(state.get("recent_trades", []))[-20:]
    state.clear()
    state.update({
        "symbol": symbol,
        "latest_date": date_s,
        "day_start_equity": equity,
        "daily_high_equity": equity,
        "realized_pnl_today": 0.0,
        "trade_count_today": 0,
        "consecutive_losses_today": 0,
        "current_position_today": "FLAT",
        "current_w_today": 0.0,
        "entry_price": None,
        "entry_equity": equity,
        "trade_start_equity": equity,
        "stop_price": None,
        "target_price": None,
        "partial_target_price": None,
        "partial_exited": False,
        "highest_price_since_entry": None,
        "initial_risk_price": None,
        "disabled_after_loss": False,
        "cooldown_until_ts": "",
        "last_action": "NEW_DAY",
        "signal": "WAIT",
        "strategy_reason": "new trading day",
        "closed_trade_count": closed_trade_count,
        "win_count": win_count,
        "loss_count": loss_count,
        "gross_profit_rate": gross_profit_rate,
        "gross_loss_rate": gross_loss_rate,
        "recent_trades": recent_trades,
        "sim_capital_yen": parse_float(settings.get("sim_capital_yen"), DEFAULT_SETTINGS["sim_capital_yen"]),
    })
    return update_sim_fields(state)


def record_trade(state: dict, exit_ts: str, exit_price: float, trade_return: float, pnl_rate: float, reason: str, entry_ts: str):
    state["closed_trade_count"] = parse_int(state.get("closed_trade_count"), 0) + 1
    if pnl_rate > 0:
        state["win_count"] = parse_int(state.get("win_count"), 0) + 1
        state["gross_profit_rate"] = parse_float(state.get("gross_profit_rate"), 0.0) + pnl_rate
        state["consecutive_losses_today"] = 0
    else:
        state["loss_count"] = parse_int(state.get("loss_count"), 0) + 1
        state["gross_loss_rate"] = parse_float(state.get("gross_loss_rate"), 0.0) + abs(pnl_rate)
        state["consecutive_losses_today"] = parse_int(state.get("consecutive_losses_today"), 0) + 1

    trades = list(state.get("recent_trades", []))
    trades.append({
        "entry_ts": entry_ts,
        "exit_ts": exit_ts,
        "exit_reason": reason,
        "exit_price": exit_price,
        "trade_return": trade_return,
        "pnl_rate": pnl_rate,
    })
    state["recent_trades"] = trades[-20:]


def enter_long(
    state: dict,
    bar: dict,
    settings: dict,
    raw_stop_price: float,
    position_w: float,
    setup_name: str,
):
    fee_rate = trade_fee_rate(settings)
    entry_price = effective_entry_price(bar["close"], settings)
    stop_price = min(entry_price * 0.999, raw_stop_price)
    stop_price = round_price(max(0.01, stop_price), parse_float(settings.get("tick_size"), DEFAULT_SETTINGS["tick_size"]), "sell")
    initial_risk = max(entry_price - stop_price, entry_price * 0.001)

    take_profit_r = max(0.10, parse_float(settings.get("take_profit_r"), DEFAULT_SETTINGS["take_profit_r"]))
    partial_take_profit_r = max(0.10, parse_float(settings.get("partial_take_profit_r"), DEFAULT_SETTINGS["partial_take_profit_r"]))
    target_price = round_price(entry_price + initial_risk * take_profit_r, parse_float(settings.get("tick_size"), 1.0), "sell")
    partial_target = round_price(entry_price + initial_risk * partial_take_profit_r, parse_float(settings.get("tick_size"), 1.0), "sell")

    equity = parse_float(state.get("equity"), 1.0)
    entry_equity = equity * max(0.0, 1.0 - position_w * fee_rate)
    action = "BUY_BREAKOUT" if setup_name == "breakout" else "BUY_VWAP_RECLAIM"
    reason = "opening range breakout above vwap" if setup_name == "breakout" else "vwap reclaim"
    state.update({
        "current_position_today": "LONG",
        "current_w_today": position_w,
        "entry_price": entry_price,
        "entry_ts": bar["timestamp"],
        "entry_equity": entry_equity,
        "trade_start_equity": equity,
        "equity": entry_equity,
        "stop_price": stop_price,
        "target_price": target_price,
        "partial_target_price": partial_target,
        "partial_exited": False,
        "highest_price_since_entry": bar["high"],
        "initial_risk_price": initial_risk,
        "trade_count_today": parse_int(state.get("trade_count_today"), 0) + 1,
        "last_action": action,
        "signal": "ENTRY",
        "setup": setup_name,
        "strategy_reason": reason,
    })
    return state


def partial_exit_long(state: dict, raw_exit_price: float, settings: dict, bar: dict):
    current_w = clamp(state.get("current_w_today"), 0.0, 1.0, 0.0)
    exit_rate = clamp(settings.get("partial_exit_rate"), 0.0, 1.0, DEFAULT_SETTINGS["partial_exit_rate"])
    exit_w = min(current_w, current_w * exit_rate)
    if exit_w <= 0:
        return state

    entry_price = parse_float(state.get("entry_price"), 0.0)
    entry_equity = parse_float(state.get("entry_equity"), parse_float(state.get("equity"), 1.0))
    exit_price = effective_exit_price(raw_exit_price, settings)
    trade_return = exit_price / entry_price - 1.0 if entry_price > 0 else 0.0
    equity = entry_equity * max(0.0, 1.0 + exit_w * trade_return - exit_w * trade_fee_rate(settings))
    remaining_w = max(0.0, current_w - exit_w)
    state.update({
        "equity": equity,
        "entry_equity": equity,
        "current_w_today": remaining_w,
        "partial_exited": True,
        "partial_exit_price": exit_price,
        "last_action": "SELL_PARTIAL_TARGET",
        "signal": "HOLD",
        "strategy_reason": "partial target reached",
    })
    return state


def exit_long(state: dict, raw_exit_price: float, reason: str, settings: dict, bar: dict):
    entry_price = parse_float(state.get("entry_price"), 0.0)
    entry_equity = parse_float(state.get("entry_equity"), parse_float(state.get("equity"), 1.0))
    trade_start = parse_float(state.get("trade_start_equity"), entry_equity)
    position_w = clamp(state.get("current_w_today"), 0.0, 1.0, 0.0)
    exit_price = effective_exit_price(raw_exit_price, settings)

    trade_return = exit_price / entry_price - 1.0 if entry_price > 0 else 0.0
    equity = entry_equity * max(0.0, 1.0 + position_w * trade_return - position_w * trade_fee_rate(settings))
    pnl_rate = equity / trade_start - 1.0 if trade_start > 0 else 0.0
    record_trade(
        state=state,
        exit_ts=bar["timestamp"],
        exit_price=exit_price,
        trade_return=trade_return,
        pnl_rate=pnl_rate,
        reason=reason,
        entry_ts=str(state.get("entry_ts", "")),
    )

    cooldown_minutes = max(0, parse_int(settings.get("cooldown_minutes"), DEFAULT_SETTINGS["cooldown_minutes"]))
    cooldown_until = bar["ts"] + dt.timedelta(minutes=cooldown_minutes) if cooldown_minutes else None
    state.update({
        "equity": equity,
        "current_position_today": "FLAT",
        "current_w_today": 0.0,
        "exit_price": exit_price,
        "last_exit_w": position_w,
        "last_trade_return": trade_return,
        "last_trade_pnl_rate": pnl_rate,
        "realized_pnl_today": parse_float(state.get("realized_pnl_today"), 0.0) + pnl_rate,
        "entry_price": None,
        "entry_equity": equity,
        "trade_start_equity": equity,
        "stop_price": None,
        "target_price": None,
        "partial_target_price": None,
        "partial_exited": False,
        "highest_price_since_entry": None,
        "initial_risk_price": None,
        "cooldown_until_ts": ts_text(cooldown_until) if cooldown_until else "",
        "last_action": f"SELL_{reason}",
        "signal": "EXIT",
        "strategy_reason": f"exit by {reason.lower()}",
    })

    max_losses = max(0, parse_int(settings.get("max_consecutive_losses"), DEFAULT_SETTINGS["max_consecutive_losses"]))
    if max_losses and parse_int(state.get("consecutive_losses_today"), 0) >= max_losses:
        state["disabled_after_loss"] = True
        state["strategy_reason"] = "consecutive loss limit reached"
    return state


def mark_long(state: dict, close: float) -> dict:
    if state.get("current_position_today") != "LONG":
        return state
    entry_price = parse_float(state.get("entry_price"), 0.0)
    if entry_price <= 0:
        return state
    entry_equity = parse_float(state.get("entry_equity"), parse_float(state.get("equity"), 1.0))
    position_w = clamp(state.get("current_w_today"), 0.0, 1.0, 0.0)
    state["equity"] = entry_equity * max(0.0, 1.0 + position_w * (close / entry_price - 1.0))
    return state


def update_trailing_stop(state: dict, bar: dict, settings: dict):
    if state.get("current_position_today") != "LONG":
        return state
    trailing_r = parse_float(settings.get("trailing_stop_r"), DEFAULT_SETTINGS["trailing_stop_r"])
    if trailing_r <= 0:
        return state
    initial_risk = parse_float(state.get("initial_risk_price"), 0.0)
    entry_price = parse_float(state.get("entry_price"), 0.0)
    if initial_risk <= 0 or entry_price <= 0:
        return state

    highest = max(parse_float(state.get("highest_price_since_entry"), entry_price), bar["high"])
    old_stop = parse_float(state.get("stop_price"), 0.0)
    new_stop = highest - initial_risk * trailing_r
    new_stop = min(bar["close"] * 0.999, new_stop)
    if new_stop > old_stop:
        state["stop_price"] = round_price(new_stop, parse_float(settings.get("tick_size"), 1.0), "sell")
    state["highest_price_since_entry"] = highest
    return state


def entry_block_reason(state: dict, bar: dict, settings: dict, opening_range_done: bool, rows_today: list[dict]):
    if not opening_range_done:
        return "opening range is still forming"

    day_start = parse_float(state.get("day_start_equity"), parse_float(state.get("equity"), 1.0))
    daily_loss_limit = clamp(settings.get("daily_loss_limit_rate"), 0.0, 0.50, DEFAULT_SETTINGS["daily_loss_limit_rate"])
    if day_start > 0 and parse_float(state.get("equity"), 1.0) <= day_start * (1.0 - daily_loss_limit):
        state["disabled_after_loss"] = True

    if parse_bool(state.get("disabled_after_loss"), False):
        return "daily loss or consecutive loss limit reached"

    max_trades = max(0, parse_int(settings.get("max_trades_per_day"), DEFAULT_SETTINGS["max_trades_per_day"]))
    if max_trades and parse_int(state.get("trade_count_today"), 0) >= max_trades:
        return "max trades per day reached"

    cooldown_until = parse_ts(state.get("cooldown_until_ts"))
    if cooldown_until is not None and bar["ts"] < cooldown_until:
        return "cooldown active"

    no_new_entry_time = parse_time(settings.get("no_new_entry_time"), DEFAULT_SETTINGS["no_new_entry_time"])
    if bar["ts"].time() >= no_new_entry_time:
        return "new entry window closed"

    warning = state.get("data_warning", "")
    if warning and parse_bool(settings.get("block_on_data_warning"), DEFAULT_SETTINGS["block_on_data_warning"]):
        return "data quality warning"

    if parse_bool(state.get("data_stale_blocked"), False):
        return "stale data blocked"

    gap_rate = state.get("gap_rate")
    if parse_bool(settings.get("use_gap_filter"), DEFAULT_SETTINGS["use_gap_filter"]) and gap_rate is not None:
        min_gap = parse_float(settings.get("min_gap_rate"), DEFAULT_SETTINGS["min_gap_rate"])
        max_gap = parse_float(settings.get("max_gap_rate"), DEFAULT_SETTINGS["max_gap_rate"])
        if gap_rate < min_gap or gap_rate > max_gap:
            return "opening gap outside range"

    volume_ratio = state.get("volume_ratio")
    if parse_bool(settings.get("use_volume_filter"), DEFAULT_SETTINGS["use_volume_filter"]):
        min_volume_ratio = parse_float(settings.get("min_volume_ratio"), DEFAULT_SETTINGS["min_volume_ratio"])
        if volume_ratio is None:
            return "volume baseline unavailable"
        if volume_ratio < min_volume_ratio:
            return "volume confirmation not met"

    market_ok = state.get("market_filter_ok")
    if market_ok is False:
        return "market filter not met"

    if len(rows_today) < 2:
        return "not enough intraday bars"
    return ""


def choose_entry_setup(state: dict, bar: dict, rows_today: list[dict], settings: dict):
    mode = str(settings.get("strategy_mode", DEFAULT_SETTINGS["strategy_mode"])).strip().lower()
    vwap = state.get("vwap")
    or_high = state.get("opening_range_high")
    or_low = state.get("opening_range_low")
    if vwap is None or or_high is None or or_low is None:
        return None, "not enough intraday bars"

    breakout_buffer = clamp(settings.get("breakout_buffer_rate"), 0.0, 0.05, DEFAULT_SETTINGS["breakout_buffer_rate"])
    vwap_slop = clamp(settings.get("vwap_slop_rate"), 0.0, 0.05, DEFAULT_SETTINGS["vwap_slop_rate"])
    trigger_price = or_high * (1.0 + breakout_buffer)
    vwap_floor = vwap * (1.0 - vwap_slop)

    allow_breakout = mode in ("breakout", "both", "breakout_or_reclaim")
    allow_reclaim = mode in ("vwap_reclaim", "both", "breakout_or_reclaim")

    if allow_breakout and bar["close"] > trigger_price and bar["close"] >= vwap_floor:
        return "breakout", "opening range breakout above vwap"

    if allow_reclaim and len(rows_today) >= 2:
        prev_rows = rows_today[:-1]
        prev = prev_rows[-1]
        prev_vwap = calc_vwap(prev_rows)
        if prev_vwap is not None:
            touched_vwap = prev["close"] <= prev_vwap * (1.0 + vwap_slop) or bar["low"] <= vwap * (1.0 + vwap_slop)
            reclaimed = bar["close"] > vwap * (1.0 + breakout_buffer)
            upward = bar["close"] > prev["high"]
            above_mid_range = bar["close"] > (or_high + or_low) / 2.0
            if touched_vwap and reclaimed and upward and above_mid_range:
                return "vwap_reclaim", "vwap reclaim"

    return None, "breakout or vwap condition not met"


def calc_stop_price(setup_name: str, bar: dict, state: dict, settings: dict) -> float:
    stop_buffer_rate = clamp(settings.get("stop_buffer_rate"), 0.0001, 0.20, DEFAULT_SETTINGS["stop_buffer_rate"])
    or_low = parse_float(state.get("opening_range_low"), bar["close"] * (1.0 - stop_buffer_rate))
    vwap = parse_float(state.get("vwap"), bar["close"])
    stop_from_buffer = bar["close"] * (1.0 - stop_buffer_rate)
    if setup_name == "vwap_reclaim":
        raw_stop = max(or_low, vwap * (1.0 - stop_buffer_rate), stop_from_buffer)
    else:
        raw_stop = max(or_low, stop_from_buffer)
    return min(bar["close"] * 0.999, raw_stop)


def calc_position_weight(bar: dict, state: dict, settings: dict, entry_price: float, stop_price: float):
    risk_per_trade = clamp(settings.get("risk_per_trade_rate"), 0.0, 0.20, DEFAULT_SETTINGS["risk_per_trade_rate"])
    max_position_w = clamp(settings.get("max_position_w"), 0.0, 1.0, DEFAULT_SETTINGS["max_position_w"])
    min_position_w = clamp(settings.get("min_position_w"), 0.0, 1.0, DEFAULT_SETTINGS["min_position_w"])
    liquidity_cap = calc_liquidity_position_cap(bar, state, settings)
    risk_rate = max(0.0, (entry_price - stop_price) / entry_price) if entry_price > 0 else 0.0
    position_w = min(max_position_w, liquidity_cap, risk_per_trade / risk_rate) if risk_rate > 0 else 0.0

    sim_capital = parse_float(settings.get("sim_capital_yen"), DEFAULT_SETTINGS["sim_capital_yen"])
    notional_yen = sim_capital * parse_float(state.get("equity"), 1.0) * position_w
    min_notional = max(0.0, parse_float(settings.get("min_notional_yen"), DEFAULT_SETTINGS["min_notional_yen"]))
    if notional_yen < min_notional:
        return 0.0, "notional below minimum"
    if position_w < min_position_w:
        return position_w, "risk size is below minimum position"
    return position_w, ""


def process_bar(symbol: str, state: dict, all_bars: list[dict], bar: dict) -> dict:
    settings = load_symbol_settings(symbol)
    state = reset_for_new_day(state, symbol, bar, settings)

    strategy_enabled = settings.get("strategy_enabled", True) is not False
    opening_range_minutes = max(5, parse_int(settings.get("opening_range_minutes"), DEFAULT_SETTINGS["opening_range_minutes"]))
    force_flat_time = parse_time(settings.get("force_flat_time"), DEFAULT_SETTINGS["force_flat_time"])
    volume_lookback = max(1, parse_int(settings.get("volume_lookback_bars"), DEFAULT_SETTINGS["volume_lookback_bars"]))
    bar_interval_minutes = max(1, parse_int(settings.get("bar_interval_minutes"), DEFAULT_SETTINGS["bar_interval_minutes"]))
    max_gap_minutes = max(
        bar_interval_minutes + 1,
        parse_int(settings.get("max_expected_bar_gap_minutes"), DEFAULT_SETTINGS["max_expected_bar_gap_minutes"]),
    )

    rows_today = day_bars_until(all_bars, bar)
    vwap = calc_vwap(rows_today)
    or_high, or_low, opening_range_done = calc_opening_range(rows_today, opening_range_minutes)
    gap_rate = calc_gap_rate(all_bars, rows_today)
    volume_ratio = calc_volume_ratio(rows_today, volume_lookback)
    data_warning = calc_data_warning(rows_today, max_gap_minutes)
    market_ok, market_reason, market_metrics = market_filter_result(symbol, bar, settings)

    state.update({
        "latest_ts": bar["timestamp"],
        "latest_date": bar["date"],
        "last_price": bar["close"],
        "open": bar["open"],
        "high": bar["high"],
        "low": bar["low"],
        "close": bar["close"],
        "vwap": vwap,
        "opening_range_high": or_high,
        "opening_range_low": or_low,
        "opening_range_done": opening_range_done,
        "gap_rate": gap_rate,
        "volume_ratio": volume_ratio,
        "data_warning": data_warning,
        "bar_interval_minutes": bar_interval_minutes,
        "max_expected_bar_gap_minutes": max_gap_minutes,
        "market_filter_ok": market_ok,
        "market_filter_reason": market_reason,
        "strategy_enabled": strategy_enabled,
        "last_runner_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "sim_capital_yen": parse_float(settings.get("sim_capital_yen"), DEFAULT_SETTINGS["sim_capital_yen"]),
    })
    state.update(market_metrics)

    if not strategy_enabled:
        if state.get("current_position_today") == "LONG":
            state = exit_long(state, bar["close"], "DISABLED", settings, bar)
        else:
            state["last_action"] = "DISABLED"
            state["signal"] = "WAIT"
            state["strategy_reason"] = "strategy disabled"
        return update_sim_fields(state)

    position = str(state.get("current_position_today", "FLAT"))
    if position == "LONG":
        stop_price = parse_float(state.get("stop_price"), None)
        target_price = parse_float(state.get("target_price"), None)
        partial_target = parse_float(state.get("partial_target_price"), None)

        if stop_price is not None and bar["low"] <= stop_price:
            exit_price = bar["open"] if bar["open"] <= stop_price else stop_price
            state = exit_long(state, max(0.01, exit_price), "STOP", settings, bar)
        elif target_price is not None and bar["high"] >= target_price:
            state = exit_long(state, target_price, "TARGET", settings, bar)
        elif (
            partial_target is not None
            and not parse_bool(state.get("partial_exited"), False)
            and bar["high"] >= partial_target
        ):
            state = partial_exit_long(state, partial_target, settings, bar)
            state = update_trailing_stop(state, bar, settings)
            state = mark_long(state, bar["close"])
        elif bar["ts"].time() >= force_flat_time:
            state = exit_long(state, bar["close"], "EOD", settings, bar)
        else:
            state = update_trailing_stop(state, bar, settings)
            state = mark_long(state, bar["close"])
            state["last_action"] = "HOLD_LONG"
            state["signal"] = "HOLD"
            state["strategy_reason"] = "position open"
    else:
        state["current_position_today"] = "FLAT"
        state["current_w_today"] = 0.0
        state["entry_equity"] = parse_float(state.get("equity"), 1.0)
        state["trade_start_equity"] = parse_float(state.get("equity"), 1.0)
        state["last_action"] = "WAIT"
        state["signal"] = "WAIT"
        state["strategy_reason"] = "waiting"

        block_reason = entry_block_reason(state, bar, settings, opening_range_done, rows_today)
        if block_reason:
            state["strategy_reason"] = block_reason
            if block_reason not in ("opening range is still forming", "new entry window closed"):
                state["signal"] = "BLOCKED"
            if block_reason == "new entry window closed":
                state["last_action"] = "NO_NEW_ENTRY"
        else:
            setup_name, setup_reason = choose_entry_setup(state, bar, rows_today, settings)
            if setup_name is None:
                state["strategy_reason"] = setup_reason
            else:
                raw_stop = calc_stop_price(setup_name, bar, state, settings)
                entry_price = effective_entry_price(bar["close"], settings)
                stop_price = min(entry_price * 0.999, raw_stop)
                position_w, weight_reason = calc_position_weight(bar, state, settings, entry_price, stop_price)
                if weight_reason:
                    state["signal"] = "BLOCKED"
                    state["strategy_reason"] = weight_reason
                else:
                    state = enter_long(state, bar, settings, stop_price, position_w, setup_name)
                    state["strategy_reason"] = setup_reason

    day_start = parse_float(state.get("day_start_equity"), parse_float(state.get("equity"), 1.0))
    if day_start > 0 and parse_float(state.get("equity"), 1.0) <= day_start * (
        1.0 - clamp(settings.get("daily_loss_limit_rate"), 0.0, 0.50, DEFAULT_SETTINGS["daily_loss_limit_rate"])
    ):
        state["disabled_after_loss"] = True

    state["daily_high_equity"] = max(parse_float(state.get("daily_high_equity"), 0.0), parse_float(state.get("equity"), 1.0))
    state["no_new_entries"] = (
        bool(state.get("disabled_after_loss"))
        or bar["ts"].time() >= parse_time(settings.get("no_new_entry_time"), DEFAULT_SETTINGS["no_new_entry_time"])
        or parse_int(state.get("trade_count_today"), 0) >= max(0, parse_int(settings.get("max_trades_per_day"), DEFAULT_SETTINGS["max_trades_per_day"]))
    )
    state = apply_settings_snapshot(state, settings)
    profit = parse_float(state.get("gross_profit_rate"), 0.0)
    loss = parse_float(state.get("gross_loss_rate"), 0.0)
    state["profit_factor"] = None if loss <= 0 else profit / loss
    return update_sim_fields(state)


def latest_bar_at_or_before(bars: list[dict], now: dt.datetime) -> dict | None:
    eligible = [bar for bar in bars if bar["ts"] <= now]
    if eligible:
        return eligible[-1]
    return None


def monitor_open_position_without_new_bar(
    symbol: str,
    state: dict,
    bars: list[dict],
    now: dt.datetime,
) -> tuple[dict, bool]:
    if state.get("current_position_today") != "LONG":
        return state, False

    latest = latest_bar_at_or_before(bars, now)
    if latest is None:
        return state, False

    settings = load_symbol_settings(symbol)
    state = process_bar(symbol, state, bars, latest)

    if state.get("current_position_today") == "LONG":
        force_flat_time = parse_time(settings.get("force_flat_time"), DEFAULT_SETTINGS["force_flat_time"])
        if latest["ts"].date() < now.date():
            state = exit_long(state, latest["close"], "STALE_EOD", settings, latest)
        elif latest["ts"].date() == now.date() and now.time() >= force_flat_time:
            state = exit_long(state, latest["close"], "EOD", settings, latest)

    state = apply_settings_snapshot(state, settings)
    state = update_sim_fields(state)
    return state, True


def append_history_if_needed(sym_dir: Path, state: dict):
    ts = str(state.get("latest_ts", "")).strip()
    if not ts:
        return

    hist_path = sym_dir / "paper_history_daytrade.csv"
    cache_key = str(hist_path)
    known = HISTORY_TS_CACHE.get(cache_key)
    if known is None:
        known = set()
        if hist_path.exists():
            with hist_path.open("r", encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    value = str(row.get("timestamp", "")).strip()
                    if value:
                        known.add(value)
        HISTORY_TS_CACHE[cache_key] = known

    if ts in known:
        return

    need_header = (not hist_path.exists()) or hist_path.stat().st_size == 0
    with hist_path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if need_header:
            writer.writerow([
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
            ])
        writer.writerow([
            ts,
            state.get("equity", 1.0),
            state.get("current_position_today", "FLAT"),
            state.get("current_w_today", 0.0),
            state.get("last_price", ""),
            state.get("last_action", ""),
            state.get("signal", ""),
            state.get("strategy_reason", ""),
            state.get("setup", ""),
            "" if state.get("volume_ratio") is None else state.get("volume_ratio"),
            "" if state.get("gap_rate") is None else state.get("gap_rate"),
            "" if state.get("vwap") is None else state.get("vwap"),
            "" if state.get("opening_range_high") is None else state.get("opening_range_high"),
            "" if state.get("opening_range_low") is None else state.get("opening_range_low"),
            "" if state.get("stop_price") is None else state.get("stop_price"),
            "" if state.get("target_price") is None else state.get("target_price"),
        ])
    known.add(ts)


def append_alert_if_needed(sym_dir: Path, state: dict):
    ts = str(state.get("latest_ts", "")).strip()
    action = str(state.get("last_action", "")).strip()
    signal = str(state.get("signal", "")).strip()
    if not ts or signal not in ("ENTRY", "EXIT", "BLOCKED"):
        return

    path = sym_dir / "alerts_daytrade.csv"
    key = f"{ts}|{action}|{signal}"
    known = set()
    if path.exists():
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                old_key = f"{row.get('timestamp', '')}|{row.get('action', '')}|{row.get('signal', '')}"
                known.add(old_key)
    if key in known:
        return

    need_header = (not path.exists()) or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if need_header:
            writer.writerow(["timestamp", "symbol", "signal", "action", "price", "reason"])
        writer.writerow([
            ts,
            state.get("symbol", ""),
            signal,
            action,
            state.get("last_price", ""),
            state.get("strategy_reason", ""),
        ])


def enqueue_order_candidate_if_needed(state: dict):
    if daytrade_order_queue is None:
        return
    try:
        candidate = daytrade_order_queue.enqueue_from_state(state)
        if candidate:
            state["last_order_candidate_id"] = candidate["id"]
            state["last_order_candidate_side"] = candidate["side"]
            state["last_order_candidate_at"] = candidate["created_at"]
            state["live_order_enabled"] = False
    except Exception as e:
        state["order_queue_error"] = f"{type(e).__name__}: {e}"


def fresh_state(symbol: str):
    return {"symbol": symbol, "equity": 1.0, "base_equity": 100.0}


def tick_symbol(symbol: str, replay_all: bool = False, now: dt.datetime | None = None, rebuild_history: bool = True):
    now = now or dt.datetime.now()
    bars = load_intraday_bars(symbol)
    sym_dir, state_path, runner_state_path = state_paths(symbol)

    if replay_all:
        state = fresh_state(symbol)
        runner_state = {}
        if rebuild_history:
            hist_path = sym_dir / "paper_history_daytrade.csv"
            if hist_path.exists():
                hist_path.unlink()
            HISTORY_TS_CACHE.pop(str(hist_path), None)
            alert_path = sym_dir / "alerts_daytrade.csv"
            if alert_path.exists():
                alert_path.unlink()
    else:
        state = load_json(state_path, fresh_state(symbol))
        runner_state = load_json(runner_state_path, {})

    last_ts = None if replay_all else parse_ts(str(runner_state.get("last_processed_ts") or state.get("latest_ts") or ""))

    processed = 0
    for bar in bars:
        if last_ts is not None and bar["ts"] <= last_ts:
            continue
        if bar["ts"] > now and not replay_all:
            continue
        state = process_bar(symbol, state, bars, bar)
        runner_state["last_processed_ts"] = bar["timestamp"]
        processed += 1
        append_history_if_needed(sym_dir, state)
        append_alert_if_needed(sym_dir, state)
        enqueue_order_candidate_if_needed(state)

    monitored = False
    if not replay_all and processed == 0:
        state, monitored = monitor_open_position_without_new_bar(symbol, state, bars, now)
        if monitored:
            append_history_if_needed(sym_dir, state)
            append_alert_if_needed(sym_dir, state)
            enqueue_order_candidate_if_needed(state)

    settings = load_symbol_settings(symbol)
    state = apply_settings_snapshot(state, settings)
    state = update_freshness_fields(state, now, settings)
    state["live_order_enabled"] = False
    state["last_runner_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
    save_json(state_path, state)
    save_json(runner_state_path, runner_state)

    print(
        f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] {symbol} "
        f"processed={processed} pos={state.get('current_position_today')} "
        f"action={state.get('last_action')} equity={parse_float(state.get('equity'), 1.0):.6f}"
    )
    return {"symbol": symbol, "processed": processed, "state": state}


def tick_all(symbol_filter: str | None = None, replay_all: bool = False):
    daytrade_status.set_data_dir(DATA_DIR)
    results = []
    for item in load_symbols():
        symbol = str(item["symbol"])
        if symbol_filter and symbol != symbol_filter:
            continue
        results.append(tick_symbol(symbol, replay_all=replay_all))
    daytrade_status.write_runner_status(
        status="ok",
        message="runner loop completed",
        symbols=[result["symbol"] for result in results],
        processed=sum(parse_int(result.get("processed"), 0) for result in results),
    )
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", type=str, default="")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--replay-all", action="store_true")
    args = parser.parse_args()

    symbol_filter = args.symbol.strip() or None

    if args.once:
        try:
            tick_all(symbol_filter=symbol_filter, replay_all=args.replay_all)
        except Exception as e:
            daytrade_status.write_runner_status("error", error=f"{type(e).__name__}: {e}")
            raise
        return

    while True:
        try:
            tick_all(symbol_filter=symbol_filter, replay_all=False)
        except Exception as e:
            daytrade_status.write_runner_status("error", error=f"{type(e).__name__}: {e}")
            raise
        time.sleep(CHECK_INTERVAL_SEC)


if __name__ == "__main__":
    main()
