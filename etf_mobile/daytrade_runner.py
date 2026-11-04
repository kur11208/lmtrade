from __future__ import annotations

from pathlib import Path
import argparse
import bisect
import concurrent.futures
import csv
import datetime as dt
import json
import math
import os
import threading
import time

import daytrade_market_calendar as market_calendar
import daytrade_status

try:
    import daytrade_order_queue
except Exception:
    daytrade_order_queue = None

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data_daytrade"
SYMBOLS_FILE = DATA_DIR / "symbols.json"

SETTINGS_KEYS = (
    "strategy_enabled",
    "strategy_mode",
    "allow_long",
    "allow_short",
    "opening_range_minutes",
    "market_open_grace_minutes",
    "allow_recovered_opening_range",
    "max_recovered_opening_range_start_minutes",
    "quality_min_opening_bar_count",
    "quality_max_opening_gap_minutes",
    "breakout_buffer_rate",
    "vwap_slop_rate",
    "risk_per_trade_rate",
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
    "min_entry_price",
    "min_notional_yen",
    "max_bar_volume_participation_rate",
    "max_trades_per_day",
    "cooldown_minutes",
    "use_volume_filter",
    "volume_lookback_bars",
    "volume_signal_bars",
    "min_volume_ratio",
    "use_gap_filter",
    "min_gap_rate",
    "max_gap_rate",
    "max_short_gap_rate",
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
    "use_ar_filter",
    "ar_lookback_bars",
    "ar_min_samples",
    "ar_horizon_bars",
    "ar_block_opposite_threshold_rate",
    "ar_filter_strict",
    "no_new_entry_time",
    "force_flat_time",
)

DEFAULT_SETTINGS = {
    "strategy_enabled": True,
    "strategy_mode": "both",
    "allow_long": False,
    "allow_short": True,
    "opening_range_minutes": 30,
    "market_open_grace_minutes": 10,
    "allow_recovered_opening_range": True,
    "max_recovered_opening_range_start_minutes": 240,
    "quality_min_opening_bar_count": 20,
    "quality_max_opening_gap_minutes": 3,
    "breakout_buffer_rate": 0.0005,
    "vwap_slop_rate": 0.0010,
    "risk_per_trade_rate": 0.0030,
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
    "min_entry_price": 0.0,
    "min_notional_yen": 0.0,
    "max_bar_volume_participation_rate": 0.02,
    "max_trades_per_day": 0,
    "cooldown_minutes": 20,
    "use_volume_filter": True,
    "volume_lookback_bars": 6,
    "volume_signal_bars": 3,
    "min_volume_ratio": 0.70,
    "use_gap_filter": True,
    "min_gap_rate": -0.0400,
    "max_gap_rate": 0.0500,
    "max_short_gap_rate": 0.0800,
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
    "use_ar_filter": False,
    "ar_lookback_bars": 40,
    "ar_min_samples": 12,
    "ar_horizon_bars": 1,
    "ar_block_opposite_threshold_rate": 0.0005,
    "ar_filter_strict": False,
    "no_new_entry_time": "15:00",
    "force_flat_time": "15:25",
}

CHECK_INTERVAL_SEC = float(os.environ.get("DAYTRADE_CHECK_INTERVAL_SEC", "1"))
SETTINGS_CACHE = {}
BAR_CACHE = {}
HISTORY_TS_CACHE = {}
ALERT_KEYS_CACHE = {}
CACHE_LOCK = threading.RLock()
RECENT_TRADES_LIMIT = 500
EVALUATION_LOG_CACHE = {}
HISTORY_HEADER = [
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
    "ar_status",
    "ar_forecast_return",
    "ar_slope",
    "ar_sample_count",
    "vwap",
    "or_high",
    "or_low",
    "stop_price",
    "target_price",
    "run_mode",
]
LEGACY_HISTORY_HEADER = HISTORY_HEADER[:11] + HISTORY_HEADER[15:]
ALERT_HEADER = ["timestamp", "symbol", "signal", "action", "price", "reason"]
AR_COMPARE_HEADER = [
    "timestamp",
    "symbol",
    "setup",
    "side",
    "price",
    "without_ar",
    "with_ar",
    "ar_reason",
    "ar_status",
    "ar_forecast_return",
    "ar_slope",
    "ar_sample_count",
    "actual_signal",
    "actual_action",
    "actual_reason",
    "run_mode",
]
UNSET = object()

try:
    import daytrade_config
    import daytrade_risk
except Exception:
    daytrade_config = None
    daytrade_risk = None


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
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}.{threading.get_ident()}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    last_error = None
    for _ in range(10):
        try:
            tmp.replace(path)
            return
        except OSError as exc:
            last_error = exc
            time.sleep(0.2)
    raise last_error


def append_jsonl(path: Path, row: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


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
    runtime_path = DATA_DIR / "runtime_config.json"
    runtime_cache_key = None
    if runtime_path.exists():
        runtime_stat = runtime_path.stat()
        runtime_cache_key = (str(runtime_path), runtime_stat.st_mtime_ns, runtime_stat.st_size)

    if not path.exists():
        settings = dict(DEFAULT_SETTINGS)
        runtime_loaded = load_json(runtime_path, {}) if runtime_path.exists() else {}
        if isinstance(runtime_loaded, dict):
            for key in SETTINGS_KEYS:
                if key in runtime_loaded:
                    settings[key] = runtime_loaded[key]
        return settings

    stat = path.stat()
    cache_key = (str(path), stat.st_mtime_ns, stat.st_size, runtime_cache_key)
    with CACHE_LOCK:
        cached = SETTINGS_CACHE.get(symbol)
        if cached and cached.get("cache_key") == cache_key:
            return dict(cached["settings"])

    loaded = load_json(path, {})
    settings = dict(DEFAULT_SETTINGS)
    for key in SETTINGS_KEYS:
        if key in loaded:
            settings[key] = loaded[key]
    runtime_loaded = load_json(runtime_path, {}) if runtime_path.exists() else {}
    if isinstance(runtime_loaded, dict):
        for key in SETTINGS_KEYS:
            if key in runtime_loaded:
                settings[key] = runtime_loaded[key]
    with CACHE_LOCK:
        SETTINGS_CACHE[symbol] = {
            "cache_key": cache_key,
            "settings": dict(settings),
        }
    return settings


# 1本の価格データを、日時オブジェクトを含むエンジン共通形式に変換する。
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
    with CACHE_LOCK:
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
    with CACHE_LOCK:
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


def rebuild_equity_from_recent_trades(state: dict) -> float | None:
    trades = state.get("recent_trades")
    if not isinstance(trades, list):
        return None
    closed_count = parse_int(state.get("closed_trade_count"), 0)
    if closed_count <= 0 or len(trades) != closed_count:
        return None
    equity = 1.0
    for trade in trades:
        if not isinstance(trade, dict):
            return None
        if "pnl_rate" not in trade:
            return None
        equity *= max(0.0, 1.0 + parse_float(trade.get("pnl_rate"), 0.0))
    return equity


def repair_flat_equity_from_recent_trades(state: dict) -> dict:
    if state.get("current_position_today") in ("LONG", "SHORT"):
        return state
    rebuilt = rebuild_equity_from_recent_trades(state)
    if rebuilt is None:
        return state
    equity = parse_float(state.get("equity"), parse_float(state.get("day_start_equity"), 1.0))
    if abs(equity - rebuilt) <= 1e-9:
        return state
    state["equity"] = rebuilt
    state["entry_equity"] = rebuilt
    state["trade_start_equity"] = rebuilt
    state["day_start_equity"] = rebuilt
    state["daily_high_equity"] = max(parse_float(state.get("daily_high_equity"), rebuilt), rebuilt)
    state["realized_pnl_today"] = 0.0
    return state


def update_sim_fields(state: dict) -> dict:
    state = repair_flat_equity_from_recent_trades(state)
    equity = parse_float(state.get("equity"), parse_float(state.get("day_start_equity"), 1.0))
    state["equity"] = equity
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


# この実装のVWAPは終値を出来高で加重平均する簡易指標。
def calc_vwap(rows: list[dict]) -> float | None:
    if not rows:
        return None
    vol_sum = sum(max(0.0, row["volume"]) for row in rows)
    if vol_sum > 0:
        return sum(row["close"] * max(0.0, row["volume"]) for row in rows) / vol_sum
    return sum(row["close"] for row in rows) / len(rows)


# 寄り付きから指定分数の高値・安値を求め、判定時間を過ぎたかも返す。
def calc_opening_range(
    rows: list[dict],
    minutes: int,
    allow_recovered: bool = False,
    max_recovered_start_minutes: int = 120,
) -> tuple[float | None, float | None, bool, bool]:
    if not rows:
        return None, None, False, False

    session_start = dt.datetime.combine(rows[-1]["ts"].date(), dt.time(9, 0))
    cutoff = session_start + dt.timedelta(minutes=max(1, minutes))
    opening_rows = [row for row in rows if session_start <= row["ts"] < cutoff]
    recovered = False
    if not opening_rows:
        first_ts = rows[0]["ts"]
        latest_recovered_start = session_start + dt.timedelta(minutes=max(1, max_recovered_start_minutes))
        if allow_recovered and first_ts >= cutoff and first_ts <= latest_recovered_start:
            recovered_cutoff = first_ts + dt.timedelta(minutes=max(1, minutes))
            opening_rows = [row for row in rows if first_ts <= row["ts"] < recovered_cutoff]
            recovered = bool(opening_rows)
        if not opening_rows:
            return None, None, False, False

    high = max(row["high"] for row in opening_rows)
    low = min(row["low"] for row in opening_rows)
    done_cutoff = opening_rows[0]["ts"] + dt.timedelta(minutes=max(1, minutes)) if recovered else cutoff
    done = rows[-1]["ts"] >= done_cutoff
    return high, low, done, recovered


# 寄り付きデータの本数・時刻間隔などを確認し、問題があれば理由を返す。
def opening_range_unavailable_reason(
    rows: list[dict],
    minutes: int,
    bar_interval_minutes: int,
    open_grace_minutes: int,
    allow_recovered: bool = False,
    max_recovered_start_minutes: int = 120,
    min_opening_bar_count: int = 20,
    max_opening_gap_minutes: int = 3,
) -> str:
    if not rows:
        return ""

    session_start = dt.datetime.combine(rows[-1]["ts"].date(), dt.time(9, 0))
    cutoff = session_start + dt.timedelta(minutes=max(1, minutes))
    if rows[-1]["ts"] < cutoff:
        return ""

    first_allowed = session_start + dt.timedelta(minutes=max(1, open_grace_minutes, bar_interval_minutes))
    has_session_open = any(session_start <= row["ts"] <= first_allowed for row in rows)
    if not has_session_open:
        first_ts = rows[0]["ts"]
        latest_recovered_start = session_start + dt.timedelta(minutes=max(1, max_recovered_start_minutes))
        if allow_recovered and first_ts >= cutoff and first_ts <= latest_recovered_start:
            return ""
        return "opening range unavailable: missing market open bars"

    opening_rows = [row for row in rows if session_start <= row["ts"] < cutoff]
    if not opening_rows:
        return "opening range unavailable: missing opening bars"
    if len(opening_rows) < max(1, min_opening_bar_count):
        return "opening range unavailable: incomplete opening bars"
    max_gap = max(
        (
            (right["ts"] - left["ts"]).total_seconds() / 60.0
            for left, right in zip(opening_rows, opening_rows[1:])
        ),
        default=0.0,
    )
    if max_opening_gap_minutes > 0 and max_gap > max(1, max_opening_gap_minutes):
        return "opening range unavailable: opening bar gap"
    return ""


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


def calc_gap_rate_from_previous(rows_today: list[dict], prev: float | None) -> float | None:
    if not rows_today or prev is None or prev <= 0:
        return None
    return rows_today[0]["open"] / prev - 1.0


def calc_volume_ratio(rows: list[dict], lookback: int, signal_bars: int = 1) -> float | None:
    if len(rows) < 2:
        return None
    lookback = max(1, lookback)
    signal_bars = max(1, signal_bars)
    signal = rows[max(0, len(rows) - signal_bars):]
    prior_end = max(0, len(rows) - signal_bars)
    prior = rows[max(0, prior_end - lookback):prior_end]
    values = [row["volume"] for row in prior if row["volume"] > 0]
    if not values:
        return None
    signal_values = [row["volume"] for row in signal if row["volume"] > 0]
    if not signal_values:
        return 0.0
    return (sum(signal_values) / len(signal_values)) / (sum(values) / len(values))


def calc_ar_signal(rows: list[dict], lookback: int, min_samples: int, horizon_bars: int = 1) -> dict:
    lookback = max(3, int(lookback))
    min_samples = max(3, int(min_samples))
    horizon_bars = max(1, int(horizon_bars))
    recent = rows[-(lookback + 1):]
    returns = []
    for prev, cur in zip(recent, recent[1:]):
        prev_close = parse_float(prev.get("close"), 0.0)
        cur_close = parse_float(cur.get("close"), 0.0)
        if prev_close > 0 and cur_close > 0:
            returns.append(math.log(cur_close / prev_close))

    if len(returns) < min_samples:
        return {
            "ar_status": "insufficient",
            "ar_sample_count": len(returns),
            "ar_forecast_return": None,
            "ar_slope": None,
            "ar_intercept": None,
        }

    x_values = returns[:-1]
    y_values = returns[1:]
    if len(x_values) < 2:
        return {
            "ar_status": "insufficient",
            "ar_sample_count": len(returns),
            "ar_forecast_return": None,
            "ar_slope": None,
            "ar_intercept": None,
        }

    x_mean = sum(x_values) / len(x_values)
    y_mean = sum(y_values) / len(y_values)
    denom = sum((x - x_mean) ** 2 for x in x_values)
    slope = 0.0 if denom <= 1e-18 else sum((x - x_mean) * (y - y_mean) for x, y in zip(x_values, y_values)) / denom
    slope = max(-0.99, min(0.99, slope))
    intercept = y_mean - slope * x_mean

    last_return = returns[-1]
    forecast_sum = 0.0
    forecast_next = last_return
    for _ in range(horizon_bars):
        forecast_next = intercept + slope * forecast_next
        forecast_sum += forecast_next

    return {
        "ar_status": "ok",
        "ar_sample_count": len(returns),
        "ar_forecast_return": forecast_sum,
        "ar_slope": slope,
        "ar_intercept": intercept,
    }


def ar_entry_block_reason(state: dict, settings: dict, side: str) -> str:
    if not parse_bool(settings.get("use_ar_filter"), DEFAULT_SETTINGS["use_ar_filter"]):
        return ""

    status = str(state.get("ar_status", "")).strip()
    strict = parse_bool(settings.get("ar_filter_strict"), DEFAULT_SETTINGS["ar_filter_strict"])
    if status != "ok":
        return "ar signal unavailable" if strict else ""

    forecast = state.get("ar_forecast_return")
    if forecast is None:
        return "ar signal unavailable" if strict else ""

    threshold = max(0.0, parse_float(
        settings.get("ar_block_opposite_threshold_rate"),
        DEFAULT_SETTINGS["ar_block_opposite_threshold_rate"],
    ))
    side = str(side or "long").strip().lower()
    forecast = parse_float(forecast, 0.0)
    if side == "short" and forecast > threshold:
        return "ar forecast opposes short"
    if side != "short" and forecast < -threshold:
        return "ar forecast opposes long"
    return ""


def ar_shadow_block_reason(state: dict, settings: dict, side: str) -> str:
    shadow_settings = dict(settings)
    shadow_settings["use_ar_filter"] = True
    return ar_entry_block_reason(state, shadow_settings, side)


def gap_filter_reason(gap_rate, settings: dict, side: str = "long") -> str:
    if not parse_bool(settings.get("use_gap_filter"), DEFAULT_SETTINGS["use_gap_filter"]) or gap_rate is None:
        return ""
    min_gap = parse_float(settings.get("min_gap_rate"), DEFAULT_SETTINGS["min_gap_rate"])
    side = str(side or "long").strip().lower()
    if side == "short":
        max_gap = parse_float(settings.get("max_short_gap_rate"), DEFAULT_SETTINGS["max_short_gap_rate"])
    else:
        max_gap = parse_float(settings.get("max_gap_rate"), DEFAULT_SETTINGS["max_gap_rate"])
    if gap_rate < min_gap:
        return "opening gap outside range"
    if gap_rate > max_gap:
        return "opening gap outside range"
    return ""


def calc_data_warning(rows_today: list[dict], max_gap_minutes: int) -> str:
    if len(rows_today) < 2:
        return ""
    max_gap_minutes = max(1, max_gap_minutes)
    gaps = []
    for prev, cur in zip(rows_today, rows_today[1:]):
        if (
            prev["ts"].date() == cur["ts"].date()
            and prev["ts"].time() <= market_calendar.MORNING_CLOSE
            and cur["ts"].time() >= market_calendar.AFTERNOON_OPEN
        ):
            continue
        if prev["ts"].date() == cur["ts"].date() and cur["ts"].time() == market_calendar.MARKET_CLOSE:
            continue
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
    if blocked and state.get("current_position_today") not in ("LONG", "SHORT"):
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
    state["take_profit_r"] = max(0.10, parse_float(settings.get("take_profit_r"), DEFAULT_SETTINGS["take_profit_r"]))
    state["partial_take_profit_r"] = max(0.10, parse_float(settings.get("partial_take_profit_r"), DEFAULT_SETTINGS["partial_take_profit_r"]))
    state["use_ar_filter"] = parse_bool(settings.get("use_ar_filter"), DEFAULT_SETTINGS["use_ar_filter"])
    state["ar_lookback_bars"] = max(3, parse_int(settings.get("ar_lookback_bars"), DEFAULT_SETTINGS["ar_lookback_bars"]))
    state["ar_horizon_bars"] = max(1, parse_int(settings.get("ar_horizon_bars"), DEFAULT_SETTINGS["ar_horizon_bars"]))
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


def effective_short_entry_price(raw_price: float, settings: dict) -> float:
    slippage = clamp(settings.get("slippage_rate"), 0.0, 0.05, DEFAULT_SETTINGS["slippage_rate"])
    spread = clamp(settings.get("spread_rate"), 0.0, 0.05, DEFAULT_SETTINGS["spread_rate"])
    price = raw_price * (1.0 - slippage - spread * 0.5)
    return max(0.01, round_price(price, parse_float(settings.get("tick_size"), DEFAULT_SETTINGS["tick_size"]), "sell"))


def effective_cover_price(raw_price: float, settings: dict) -> float:
    slippage = clamp(settings.get("slippage_rate"), 0.0, 0.05, DEFAULT_SETTINGS["slippage_rate"])
    spread = clamp(settings.get("spread_rate"), 0.0, 0.05, DEFAULT_SETTINGS["spread_rate"])
    price = raw_price * (1.0 + slippage + spread * 0.5)
    return round_price(price, parse_float(settings.get("tick_size"), DEFAULT_SETTINGS["tick_size"]), "buy")


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


def market_filter_result(symbol: str, bar: dict, settings: dict, side: str = "long"):
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
    side = str(side or "long").strip().lower()

    ok = True
    reasons = []
    if side == "short":
        max_return = abs(min_return)
        if market_return is not None and market_return > max_return:
            ok = False
            reasons.append("market return strong")
        if require_vwap and market_vwap is not None and latest["close"] > market_vwap:
            ok = False
            reasons.append("market over vwap")
    else:
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
        "market_filter_side": side,
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
    recent_trades = list(state.get("recent_trades", []))[-RECENT_TRADES_LIMIT:]
    state.clear()
    state.update({
        "symbol": symbol,
        "latest_date": date_s,
        "equity": equity,
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
        "lowest_price_since_entry": None,
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
        "direction": state.get("current_position_today", ""),
        "exit_reason": reason,
        "exit_price": exit_price,
        "trade_return": trade_return,
        "pnl_rate": pnl_rate,
    })
    state["recent_trades"] = trades[-RECENT_TRADES_LIMIT:]


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


def enter_short(
    state: dict,
    bar: dict,
    settings: dict,
    raw_stop_price: float,
    position_w: float,
    setup_name: str,
):
    fee_rate = trade_fee_rate(settings)
    entry_price = effective_short_entry_price(bar["close"], settings)
    stop_price = max(entry_price * 1.001, raw_stop_price)
    stop_price = round_price(max(0.01, stop_price), parse_float(settings.get("tick_size"), DEFAULT_SETTINGS["tick_size"]), "buy")
    initial_risk = max(stop_price - entry_price, entry_price * 0.001)

    take_profit_r = max(0.10, parse_float(settings.get("take_profit_r"), DEFAULT_SETTINGS["take_profit_r"]))
    partial_take_profit_r = max(0.10, parse_float(settings.get("partial_take_profit_r"), DEFAULT_SETTINGS["partial_take_profit_r"]))
    target_price = round_price(max(0.01, entry_price - initial_risk * take_profit_r), parse_float(settings.get("tick_size"), 1.0), "buy")
    partial_target = round_price(max(0.01, entry_price - initial_risk * partial_take_profit_r), parse_float(settings.get("tick_size"), 1.0), "buy")

    equity = parse_float(state.get("equity"), 1.0)
    entry_equity = equity * max(0.0, 1.0 - position_w * fee_rate)
    action = "SELL_BREAKDOWN" if setup_name == "breakdown" else "SELL_VWAP_REJECT"
    reason = "opening range breakdown below vwap" if setup_name == "breakdown" else "vwap rejection"
    state.update({
        "current_position_today": "SHORT",
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
        "highest_price_since_entry": None,
        "lowest_price_since_entry": bar["low"],
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
    exit_w = current_w * exit_rate
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


def partial_exit_short(state: dict, raw_exit_price: float, settings: dict, bar: dict):
    current_w = clamp(state.get("current_w_today"), 0.0, 1.0, 0.0)
    exit_rate = clamp(settings.get("partial_exit_rate"), 0.0, 1.0, DEFAULT_SETTINGS["partial_exit_rate"])
    exit_w = current_w * exit_rate
    if exit_w <= 0:
        return state

    entry_price = parse_float(state.get("entry_price"), 0.0)
    entry_equity = parse_float(state.get("entry_equity"), parse_float(state.get("equity"), 1.0))
    exit_price = effective_cover_price(raw_exit_price, settings)
    trade_return = 1.0 - exit_price / entry_price if entry_price > 0 else 0.0
    equity = entry_equity * max(0.0, 1.0 + exit_w * trade_return - exit_w * trade_fee_rate(settings))
    remaining_w = max(0.0, current_w - exit_w)
    state.update({
        "equity": equity,
        "entry_equity": equity,
        "current_w_today": remaining_w,
        "partial_exited": True,
        "partial_exit_price": exit_price,
        "last_action": "BUY_PARTIAL_TARGET",
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
        "lowest_price_since_entry": None,
        "initial_risk_price": None,
        "cooldown_until_ts": ts_text(cooldown_until) if cooldown_until else "",
        "last_action": f"SELL_{reason}",
        "signal": "EXIT",
        "strategy_reason": f"exit by {reason.lower()}",
    })

    return state


def exit_short(state: dict, raw_exit_price: float, reason: str, settings: dict, bar: dict):
    entry_price = parse_float(state.get("entry_price"), 0.0)
    entry_equity = parse_float(state.get("entry_equity"), parse_float(state.get("equity"), 1.0))
    trade_start = parse_float(state.get("trade_start_equity"), entry_equity)
    position_w = clamp(state.get("current_w_today"), 0.0, 1.0, 0.0)
    exit_price = effective_cover_price(raw_exit_price, settings)

    trade_return = 1.0 - exit_price / entry_price if entry_price > 0 else 0.0
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
        "lowest_price_since_entry": None,
        "initial_risk_price": None,
        "cooldown_until_ts": ts_text(cooldown_until) if cooldown_until else "",
        "last_action": f"BUY_{reason}",
        "signal": "EXIT",
        "strategy_reason": f"exit by {reason.lower()}",
    })

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


def mark_short(state: dict, close: float) -> dict:
    if state.get("current_position_today") != "SHORT":
        return state
    entry_price = parse_float(state.get("entry_price"), 0.0)
    if entry_price <= 0:
        return state
    entry_equity = parse_float(state.get("entry_equity"), parse_float(state.get("equity"), 1.0))
    position_w = clamp(state.get("current_w_today"), 0.0, 1.0, 0.0)
    state["equity"] = entry_equity * max(0.0, 1.0 + position_w * (1.0 - close / entry_price))
    return state


def update_trailing_stop(state: dict, bar: dict, settings: dict):
    position = state.get("current_position_today")
    if position not in ("LONG", "SHORT"):
        return state
    trailing_r = parse_float(settings.get("trailing_stop_r"), DEFAULT_SETTINGS["trailing_stop_r"])
    if trailing_r <= 0:
        return state
    initial_risk = parse_float(state.get("initial_risk_price"), 0.0)
    entry_price = parse_float(state.get("entry_price"), 0.0)
    if initial_risk <= 0 or entry_price <= 0:
        return state

    if position == "LONG":
        highest = max(parse_float(state.get("highest_price_since_entry"), entry_price), bar["high"])
        old_stop = parse_float(state.get("stop_price"), 0.0)
        new_stop = highest - initial_risk * trailing_r
        new_stop = min(bar["close"] * 0.999, new_stop)
        if new_stop > old_stop:
            state["stop_price"] = round_price(new_stop, parse_float(settings.get("tick_size"), 1.0), "sell")
        state["highest_price_since_entry"] = highest
    else:
        lowest = min(parse_float(state.get("lowest_price_since_entry"), entry_price), bar["low"])
        old_stop = parse_float(state.get("stop_price"), float("inf"))
        new_stop = lowest + initial_risk * trailing_r
        new_stop = max(bar["close"] * 1.001, new_stop)
        if new_stop < old_stop:
            state["stop_price"] = round_price(max(0.01, new_stop), parse_float(settings.get("tick_size"), 1.0), "buy")
        state["lowest_price_since_entry"] = lowest
    return state


# 新規エントリーの前に、データ品質・取引回数・時間帯などの禁止条件を確認する。
def entry_block_reason(state: dict, bar: dict, settings: dict, opening_range_done: bool, rows_today: list[dict]):
    missing_reason = opening_range_unavailable_reason(
        rows_today,
        max(5, parse_int(settings.get("opening_range_minutes"), DEFAULT_SETTINGS["opening_range_minutes"])),
        max(1, parse_int(settings.get("bar_interval_minutes"), DEFAULT_SETTINGS["bar_interval_minutes"])),
        max(1, parse_int(settings.get("market_open_grace_minutes"), DEFAULT_SETTINGS["market_open_grace_minutes"])),
        parse_bool(settings.get("allow_recovered_opening_range"), DEFAULT_SETTINGS["allow_recovered_opening_range"]),
        max(1, parse_int(settings.get("max_recovered_opening_range_start_minutes"), DEFAULT_SETTINGS["max_recovered_opening_range_start_minutes"])),
        max(1, parse_int(settings.get("quality_min_opening_bar_count"), DEFAULT_SETTINGS["quality_min_opening_bar_count"])),
        max(0, parse_int(settings.get("quality_max_opening_gap_minutes"), DEFAULT_SETTINGS["quality_max_opening_gap_minutes"])),
    )
    if missing_reason:
        return missing_reason

    if not opening_range_done:
        return "opening range is still forming"

    max_trades = max(0, parse_int(settings.get("max_trades_per_day"), DEFAULT_SETTINGS["max_trades_per_day"]))
    # デモ設定は1日1回まで。決済後も売買回数はリセットされない。
    if max_trades and parse_int(state.get("trade_count_today"), 0) >= max_trades:
        return "max trades per day reached"

    cooldown_until = parse_ts(state.get("cooldown_until_ts"))
    if cooldown_until is not None and bar["ts"] < cooldown_until:
        return "cooldown active"

    no_new_entry_time = parse_time(settings.get("no_new_entry_time"), DEFAULT_SETTINGS["no_new_entry_time"])
    if bar["ts"].time() >= no_new_entry_time:
        return "new entry window closed"

    min_entry_price = parse_float(settings.get("min_entry_price"), DEFAULT_SETTINGS["min_entry_price"])
    if min_entry_price > 0 and parse_float(bar.get("close"), 0.0) < min_entry_price:
        return "entry price below minimum"

    warning = state.get("data_warning", "")
    if warning and parse_bool(settings.get("block_on_data_warning"), DEFAULT_SETTINGS["block_on_data_warning"]):
        return "data quality warning"

    if parse_bool(state.get("data_stale_blocked"), False):
        return "stale data blocked"

    volume_ratio = state.get("volume_ratio")
    if parse_bool(settings.get("use_volume_filter"), DEFAULT_SETTINGS["use_volume_filter"]):
        min_volume_ratio = parse_float(settings.get("min_volume_ratio"), DEFAULT_SETTINGS["min_volume_ratio"])
        if volume_ratio is None:
            return "volume baseline unavailable"
        if volume_ratio < min_volume_ratio:
            return "volume confirmation not met"

    if len(rows_today) < 2:
        return "not enough intraday bars"
    return ""


def portfolio_entry_block_reason(proposed_weight: float, bar: dict) -> str:
    if daytrade_config is None or daytrade_risk is None:
        return ""
    if not daytrade_config.CONFIG_FILE.exists():
        return ""
    try:
        return daytrade_risk.entry_block_reason(proposed_weight=proposed_weight, now=bar.get("ts"), check_feed=False)
    except Exception:
        return ""


# 価格・VWAP・寄り付き高安から、買い/空売りのセットアップを選ぶ。
def choose_entry_setup(state: dict, bar: dict, rows_today: list[dict], settings: dict):
    mode = str(settings.get("strategy_mode", DEFAULT_SETTINGS["strategy_mode"])).strip().lower()
    allow_long = parse_bool(settings.get("allow_long"), DEFAULT_SETTINGS["allow_long"])
    allow_short = parse_bool(settings.get("allow_short"), DEFAULT_SETTINGS["allow_short"])
    vwap = state.get("vwap")
    or_high = state.get("opening_range_high")
    or_low = state.get("opening_range_low")
    if vwap is None or or_high is None or or_low is None:
        return None, "not enough intraday bars"

    breakout_buffer = clamp(settings.get("breakout_buffer_rate"), 0.0, 0.05, DEFAULT_SETTINGS["breakout_buffer_rate"])
    vwap_slop = clamp(settings.get("vwap_slop_rate"), 0.0, 0.05, DEFAULT_SETTINGS["vwap_slop_rate"])
    trigger_price = or_high * (1.0 + breakout_buffer)
    breakdown_price = or_low * (1.0 - breakout_buffer)
    vwap_floor = vwap * (1.0 - vwap_slop)
    vwap_ceiling = vwap * (1.0 + vwap_slop)

    allow_breakout = mode in ("breakout", "both", "breakout_or_reclaim")
    allow_reclaim = mode in ("vwap_reclaim", "both", "breakout_or_reclaim")

    if allow_long and allow_breakout and bar["close"] > trigger_price and bar["close"] >= vwap_floor:
        return "breakout", "opening range breakout above vwap"

    if allow_long and allow_reclaim and len(rows_today) >= 2:
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

    if allow_short and allow_breakout and bar["close"] < breakdown_price and bar["close"] <= vwap_ceiling:
        return "breakdown", "opening range breakdown below vwap"

    if allow_short and allow_reclaim and len(rows_today) >= 2:
        prev_rows = rows_today[:-1]
        prev = prev_rows[-1]
        prev_vwap = calc_vwap(prev_rows)
        if prev_vwap is not None:
            touched_vwap = prev["close"] >= prev_vwap * (1.0 - vwap_slop) or bar["high"] >= vwap * (1.0 - vwap_slop)
            rejected = bar["close"] < vwap * (1.0 - breakout_buffer)
            downward = bar["close"] < prev["low"]
            below_mid_range = bar["close"] < (or_high + or_low) / 2.0
            if touched_vwap and rejected and downward and below_mid_range:
                return "vwap_reject", "vwap rejection"

    return None, "breakout/breakdown or vwap condition not met"


def calc_stop_price(setup_name: str, bar: dict, state: dict, settings: dict) -> float:
    stop_buffer_rate = clamp(settings.get("stop_buffer_rate"), 0.0001, 0.20, DEFAULT_SETTINGS["stop_buffer_rate"])
    or_low = parse_float(state.get("opening_range_low"), bar["close"] * (1.0 - stop_buffer_rate))
    or_high = parse_float(state.get("opening_range_high"), bar["close"] * (1.0 + stop_buffer_rate))
    vwap = parse_float(state.get("vwap"), bar["close"])
    if setup_name in ("breakdown", "vwap_reject"):
        stop_from_buffer = bar["close"] * (1.0 + stop_buffer_rate)
        if setup_name == "vwap_reject":
            raw_stop = min(or_high, vwap * (1.0 + stop_buffer_rate), stop_from_buffer)
        else:
            raw_stop = min(or_high, stop_from_buffer)
        return max(bar["close"] * 1.001, raw_stop)

    stop_from_buffer = bar["close"] * (1.0 - stop_buffer_rate)
    if setup_name == "vwap_reclaim":
        raw_stop = max(or_low, vwap * (1.0 - stop_buffer_rate), stop_from_buffer)
    else:
        raw_stop = max(or_low, stop_from_buffer)
    return min(bar["close"] * 0.999, raw_stop)


# 損切り幅・許容リスク・流動性から、投入資金の比率を決める。
def calc_position_weight(bar: dict, state: dict, settings: dict, entry_price: float, stop_price: float):
    risk_per_trade = clamp(settings.get("risk_per_trade_rate"), 0.0, 0.20, DEFAULT_SETTINGS["risk_per_trade_rate"])
    max_position_w = clamp(settings.get("max_position_w"), 0.0, 1.0, DEFAULT_SETTINGS["max_position_w"])
    min_position_w = clamp(settings.get("min_position_w"), 0.0, 1.0, DEFAULT_SETTINGS["min_position_w"])
    liquidity_cap = calc_liquidity_position_cap(bar, state, settings)
    risk_rate = abs(entry_price - stop_price) / entry_price if entry_price > 0 else 0.0
    position_w = min(max_position_w, liquidity_cap, risk_per_trade / risk_rate) if risk_rate > 0 else 0.0

    sim_capital = parse_float(settings.get("sim_capital_yen"), DEFAULT_SETTINGS["sim_capital_yen"])
    notional_yen = sim_capital * parse_float(state.get("equity"), 1.0) * position_w
    min_notional = max(0.0, parse_float(settings.get("min_notional_yen"), DEFAULT_SETTINGS["min_notional_yen"]))
    if notional_yen < min_notional:
        return 0.0, "notional below minimum"
    if position_w < min_position_w:
        return position_w, "risk size is below minimum position"
    return position_w, ""


# 1分足ごとに指標を更新し、エントリー・保有・決済の状態を遷移させる中心関数。
def process_bar(
    symbol: str,
    state: dict,
    all_bars: list[dict],
    bar: dict,
    *,
    settings_override: dict | None = None,
    rows_today_override: list[dict] | None = None,
    previous_close_override=UNSET,
    data_warning_override: str | None = None,
    skip_portfolio_risk: bool = False,
) -> dict:
    latest_ts = parse_ts(str(state.get("latest_ts", "")))
    if latest_ts is not None and bar["ts"] < latest_ts:
        return state

    settings = settings_override if settings_override is not None else load_symbol_settings(symbol)
    state = reset_for_new_day(state, symbol, bar, settings)

    strategy_enabled = settings.get("strategy_enabled", True) is not False
    opening_range_minutes = max(5, parse_int(settings.get("opening_range_minutes"), DEFAULT_SETTINGS["opening_range_minutes"]))
    force_flat_time = parse_time(settings.get("force_flat_time"), DEFAULT_SETTINGS["force_flat_time"])
    volume_lookback = max(1, parse_int(settings.get("volume_lookback_bars"), DEFAULT_SETTINGS["volume_lookback_bars"]))
    volume_signal_bars = max(1, parse_int(settings.get("volume_signal_bars"), DEFAULT_SETTINGS["volume_signal_bars"]))
    ar_lookback = max(3, parse_int(settings.get("ar_lookback_bars"), DEFAULT_SETTINGS["ar_lookback_bars"]))
    ar_min_samples = max(3, parse_int(settings.get("ar_min_samples"), DEFAULT_SETTINGS["ar_min_samples"]))
    ar_horizon = max(1, parse_int(settings.get("ar_horizon_bars"), DEFAULT_SETTINGS["ar_horizon_bars"]))
    bar_interval_minutes = max(1, parse_int(settings.get("bar_interval_minutes"), DEFAULT_SETTINGS["bar_interval_minutes"]))
    max_gap_minutes = max(
        bar_interval_minutes + 1,
        parse_int(settings.get("max_expected_bar_gap_minutes"), DEFAULT_SETTINGS["max_expected_bar_gap_minutes"]),
    )

    # この時刻までに見えている足だけで指標を計算し、未来情報を混入させない。
    rows_today = rows_today_override if rows_today_override is not None else day_bars_until(all_bars, bar)
    vwap = calc_vwap(rows_today)
    or_high, or_low, opening_range_done, recovered_opening_range = calc_opening_range(
        rows_today,
        opening_range_minutes,
        parse_bool(settings.get("allow_recovered_opening_range"), DEFAULT_SETTINGS["allow_recovered_opening_range"]),
        max(1, parse_int(settings.get("max_recovered_opening_range_start_minutes"), DEFAULT_SETTINGS["max_recovered_opening_range_start_minutes"])),
    )
    gap_rate = (
        calc_gap_rate(all_bars, rows_today)
        if previous_close_override is UNSET
        else calc_gap_rate_from_previous(rows_today, previous_close_override)
    )
    volume_ratio = calc_volume_ratio(rows_today, volume_lookback, volume_signal_bars)
    ar_signal = calc_ar_signal(rows_today, ar_lookback, ar_min_samples, ar_horizon)
    data_warning = (
        calc_data_warning(rows_today, max_gap_minutes)
        if data_warning_override is None
        else data_warning_override
    )
    default_market_side = "short" if (
        parse_bool(settings.get("allow_short"), DEFAULT_SETTINGS["allow_short"])
        and not parse_bool(settings.get("allow_long"), DEFAULT_SETTINGS["allow_long"])
    ) else "long"
    market_ok, market_reason, market_metrics = market_filter_result(symbol, bar, settings, default_market_side)

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
        "opening_range_recovered": recovered_opening_range,
        "gap_rate": gap_rate,
        "volume_ratio": volume_ratio,
        "ar_filter_enabled": parse_bool(settings.get("use_ar_filter"), DEFAULT_SETTINGS["use_ar_filter"]),
        "ar_lookback_bars": ar_lookback,
        "ar_min_samples": ar_min_samples,
        "ar_horizon_bars": ar_horizon,
        "ar_block_opposite_threshold_rate": parse_float(
            settings.get("ar_block_opposite_threshold_rate"),
            DEFAULT_SETTINGS["ar_block_opposite_threshold_rate"],
        ),
        "data_warning": data_warning,
        "bar_interval_minutes": bar_interval_minutes,
        "max_expected_bar_gap_minutes": max_gap_minutes,
        "market_filter_ok": market_ok,
        "market_filter_reason": market_reason,
        "ar_compare_candidate": False,
        "ar_compare_setup": "",
        "ar_compare_side": "",
        "ar_compare_without_ar": "",
        "ar_compare_with_ar": "",
        "ar_compare_ar_reason": "",
        "strategy_enabled": strategy_enabled,
        "last_runner_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "sim_capital_yen": parse_float(settings.get("sim_capital_yen"), DEFAULT_SETTINGS["sim_capital_yen"]),
    })
    state.update(ar_signal)
    state.update(market_metrics)

    if not strategy_enabled:
        if state.get("current_position_today") == "LONG":
            state = exit_long(state, bar["close"], "DISABLED", settings, bar)
        elif state.get("current_position_today") == "SHORT":
            state = exit_short(state, bar["close"], "DISABLED", settings, bar)
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
        entry_ts = parse_ts(str(state.get("entry_ts", "")))
        is_entry_bar = entry_ts is not None and bar["ts"] <= entry_ts

        if is_entry_bar and bar["ts"].time() < force_flat_time:
            state = mark_long(state, bar["close"])
            state["last_action"] = "HOLD_LONG"
            state["signal"] = "HOLD"
            state["strategy_reason"] = "entry bar awaiting completion"
        elif stop_price is not None and bar["low"] <= stop_price:
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
    elif position == "SHORT":
        stop_price = parse_float(state.get("stop_price"), None)
        target_price = parse_float(state.get("target_price"), None)
        partial_target = parse_float(state.get("partial_target_price"), None)
        entry_ts = parse_ts(str(state.get("entry_ts", "")))
        is_entry_bar = entry_ts is not None and bar["ts"] <= entry_ts

        if is_entry_bar and bar["ts"].time() < force_flat_time:
            state = mark_short(state, bar["close"])
            state["last_action"] = "HOLD_SHORT"
            state["signal"] = "HOLD"
            state["strategy_reason"] = "entry bar awaiting completion"
        elif stop_price is not None and bar["high"] >= stop_price:
            exit_price = bar["open"] if bar["open"] >= stop_price else stop_price
            state = exit_short(state, max(0.01, exit_price), "STOP", settings, bar)
        elif target_price is not None and bar["low"] <= target_price:
            state = exit_short(state, target_price, "TARGET", settings, bar)
        elif (
            partial_target is not None
            and not parse_bool(state.get("partial_exited"), False)
            and bar["low"] <= partial_target
        ):
            state = partial_exit_short(state, partial_target, settings, bar)
            state = update_trailing_stop(state, bar, settings)
            state = mark_short(state, bar["close"])
        elif bar["ts"].time() >= force_flat_time:
            state = exit_short(state, bar["close"], "EOD", settings, bar)
        else:
            state = update_trailing_stop(state, bar, settings)
            state = mark_short(state, bar["close"])
            state["last_action"] = "HOLD_SHORT"
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

        # 取引回数やデータ不足で禁止されていたら、新規売買は行わない。
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
                setup_side = "short" if setup_name in ("breakdown", "vwap_reject") else "long"
                ar_shadow_reason = ar_shadow_block_reason(state, settings, setup_side)
                state.update({
                    "ar_compare_candidate": True,
                    "ar_compare_setup": setup_name,
                    "ar_compare_side": setup_side,
                    "ar_compare_without_ar": "ENTRY_CANDIDATE",
                    "ar_compare_with_ar": "AR_BLOCKED" if ar_shadow_reason else "ENTRY_CANDIDATE",
                    "ar_compare_ar_reason": ar_shadow_reason,
                })
                gap_reason = gap_filter_reason(state.get("gap_rate"), settings, setup_side)
                if gap_reason:
                    state["signal"] = "BLOCKED"
                    state["strategy_reason"] = gap_reason
                    continue_entry = False
                else:
                    continue_entry = True
                if not continue_entry:
                    pass
                else:
                    ar_reason = ar_entry_block_reason(state, settings, setup_side)
                    if ar_reason:
                        state["signal"] = "BLOCKED"
                        state["strategy_reason"] = ar_reason
                    else:
                        market_ok, market_reason, market_metrics = market_filter_result(symbol, bar, settings, setup_side)
                        state.update({
                            "market_filter_ok": market_ok,
                            "market_filter_reason": market_reason,
                        })
                        state.update(market_metrics)
                        if market_ok is False:
                            state["signal"] = "BLOCKED"
                            state["strategy_reason"] = "market filter not met"
                        else:
                            raw_stop = calc_stop_price(setup_name, bar, state, settings)
                            if setup_name in ("breakdown", "vwap_reject"):
                                entry_price = effective_short_entry_price(bar["close"], settings)
                                stop_price = max(entry_price * 1.001, raw_stop)
                            else:
                                entry_price = effective_entry_price(bar["close"], settings)
                                stop_price = min(entry_price * 0.999, raw_stop)
                            position_w, weight_reason = calc_position_weight(bar, state, settings, entry_price, stop_price)
                            if weight_reason:
                                state["signal"] = "BLOCKED"
                                state["strategy_reason"] = weight_reason
                            else:
                                risk_reason = "" if skip_portfolio_risk else portfolio_entry_block_reason(position_w, bar)
                                if risk_reason:
                                    state["signal"] = "BLOCKED"
                                    state["strategy_reason"] = risk_reason
                                else:
                                    if setup_name in ("breakdown", "vwap_reject"):
                                        state = enter_short(state, bar, settings, stop_price, position_w, setup_name)
                                    else:
                                        state = enter_long(state, bar, settings, stop_price, position_w, setup_name)
                                    state["strategy_reason"] = setup_reason

    state["disabled_after_loss"] = False

    state["daily_high_equity"] = max(parse_float(state.get("daily_high_equity"), 0.0), parse_float(state.get("equity"), 1.0))
    max_trades = max(0, parse_int(settings.get("max_trades_per_day"), DEFAULT_SETTINGS["max_trades_per_day"]))
    state["no_new_entries"] = (
        bar["ts"].time() >= parse_time(settings.get("no_new_entry_time"), DEFAULT_SETTINGS["no_new_entry_time"])
        or (max_trades > 0 and parse_int(state.get("trade_count_today"), 0) >= max_trades)
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
    last_seen_ts: dt.datetime | None = None,
) -> tuple[dict, bool]:
    if state.get("current_position_today") not in ("LONG", "SHORT"):
        return state, False

    latest = latest_bar_at_or_before(bars, now)
    if latest is None:
        return state, False
    if last_seen_ts is not None and latest["ts"] < last_seen_ts:
        return state, False

    settings = load_symbol_settings(symbol)
    state = process_bar(symbol, state, bars, latest)

    if state.get("current_position_today") in ("LONG", "SHORT"):
        force_flat_time = parse_time(settings.get("force_flat_time"), DEFAULT_SETTINGS["force_flat_time"])
        exit_fn = exit_short if state.get("current_position_today") == "SHORT" else exit_long
        if latest["ts"].date() < now.date():
            state = exit_fn(state, latest["close"], "STALE_EOD", settings, latest)
        elif latest["ts"].date() == now.date() and now.time() >= force_flat_time:
            state = exit_fn(state, latest["close"], "EOD", settings, latest)

    state = apply_settings_snapshot(state, settings)
    state = update_sim_fields(state)
    return state, True


def history_row(state: dict) -> list:
    return [
        str(state.get("latest_ts", "")).strip(),
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
        state.get("ar_status", ""),
        "" if state.get("ar_forecast_return") is None else state.get("ar_forecast_return"),
        "" if state.get("ar_slope") is None else state.get("ar_slope"),
        state.get("ar_sample_count", ""),
        "" if state.get("vwap") is None else state.get("vwap"),
        "" if state.get("opening_range_high") is None else state.get("opening_range_high"),
        "" if state.get("opening_range_low") is None else state.get("opening_range_low"),
        "" if state.get("stop_price") is None else state.get("stop_price"),
        "" if state.get("target_price") is None else state.get("target_price"),
        state.get("run_mode", ""),
    ]


def alert_row(state: dict) -> list | None:
    ts = str(state.get("latest_ts", "")).strip()
    action = str(state.get("last_action", "")).strip()
    signal = str(state.get("signal", "")).strip()
    if not ts or signal not in ("ENTRY", "EXIT", "BLOCKED"):
        return None
    return [
        ts,
        state.get("symbol", ""),
        signal,
        action,
        state.get("last_price", ""),
        state.get("strategy_reason", ""),
    ]


def ar_compare_row(state: dict) -> list | None:
    if not parse_bool(state.get("ar_compare_candidate"), False):
        return None
    ts = str(state.get("latest_ts", "")).strip()
    setup = str(state.get("ar_compare_setup", "")).strip()
    side = str(state.get("ar_compare_side", "")).strip()
    if not ts or not setup or not side:
        return None
    return [
        ts,
        state.get("symbol", ""),
        setup,
        side,
        state.get("last_price", ""),
        state.get("ar_compare_without_ar", ""),
        state.get("ar_compare_with_ar", ""),
        state.get("ar_compare_ar_reason", ""),
        state.get("ar_status", ""),
        "" if state.get("ar_forecast_return") is None else state.get("ar_forecast_return"),
        "" if state.get("ar_slope") is None else state.get("ar_slope"),
        state.get("ar_sample_count", ""),
        state.get("signal", ""),
        state.get("last_action", ""),
        state.get("strategy_reason", ""),
        state.get("run_mode", ""),
    ]


def write_csv_atomic(path: Path, header: list[str], rows: list[list]):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}.{threading.get_ident()}")
    try:
        with tmp.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerows(rows)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def ensure_history_schema(hist_path: Path):
    if not hist_path.exists() or hist_path.stat().st_size == 0:
        return
    with hist_path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    if not rows or rows[0] == HISTORY_HEADER:
        return
    if rows[0] != LEGACY_HISTORY_HEADER:
        raise ValueError(f"unsupported daytrade history schema: {hist_path}")

    migrated = [HISTORY_HEADER]
    for row in rows[1:]:
        if not row:
            continue
        if len(row) == len(LEGACY_HISTORY_HEADER):
            migrated.append(row[:11] + ["", "", "", ""] + row[11:])
        elif len(row) >= len(HISTORY_HEADER):
            migrated.append(row[: len(HISTORY_HEADER)])
        else:
            migrated.append((row + [""] * len(HISTORY_HEADER))[: len(HISTORY_HEADER)])

    tmp = hist_path.with_name(
        f"{hist_path.name}.schema.tmp.{os.getpid()}.{threading.get_ident()}"
    )
    try:
        with tmp.open("w", encoding="utf-8", newline="") as f:
            csv.writer(f).writerows(migrated)
        os.replace(tmp, hist_path)
    finally:
        if tmp.exists():
            tmp.unlink()


def append_history_if_needed(sym_dir: Path, state: dict):
    ts = str(state.get("latest_ts", "")).strip()
    if not ts:
        return
    history_key = f"{ts}|{state.get('last_action', '')}|{state.get('signal', '')}"

    hist_path = sym_dir / "paper_history_daytrade.csv"
    cache_key = str(hist_path)
    with CACHE_LOCK:
        known = HISTORY_TS_CACHE.get(cache_key)
    if known is None:
        ensure_history_schema(hist_path)
        known = set()
        if hist_path.exists():
            with hist_path.open("r", encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    value = str(row.get("timestamp", "")).strip()
                    if value:
                        known.add(f"{value}|{row.get('action', '')}|{row.get('signal', '')}")
        with CACHE_LOCK:
            HISTORY_TS_CACHE[cache_key] = known

    if history_key in known:
        return

    need_header = (not hist_path.exists()) or hist_path.stat().st_size == 0
    with hist_path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if need_header:
            writer.writerow(HISTORY_HEADER)
        writer.writerow(history_row(state))
    with CACHE_LOCK:
        known.add(history_key)


def append_alert_if_needed(sym_dir: Path, state: dict):
    row = alert_row(state)
    if row is None:
        return
    ts, _, signal, action, _, _ = row

    path = sym_dir / "alerts_daytrade.csv"
    key = f"{ts}|{action}|{signal}"
    cache_key = str(path)
    with CACHE_LOCK:
        known = ALERT_KEYS_CACHE.get(cache_key)
    if known is None:
        known = set()
        if path.exists():
            with path.open("r", encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    old_key = f"{row.get('timestamp', '')}|{row.get('action', '')}|{row.get('signal', '')}"
                    known.add(old_key)
        with CACHE_LOCK:
            ALERT_KEYS_CACHE[cache_key] = known
    if key in known:
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    need_header = (not path.exists()) or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if need_header:
            writer.writerow(ALERT_HEADER)
        writer.writerow(row)
    with CACHE_LOCK:
        known.add(key)


def append_ar_compare_if_needed(sym_dir: Path, state: dict):
    row = ar_compare_row(state)
    if row is None:
        return
    ts, _, setup, side = row[:4]

    path = sym_dir / "paper_ar_compare_daytrade.csv"
    key = f"{ts}|{setup}|{side}"
    cache_key = str(path)
    with CACHE_LOCK:
        known = HISTORY_TS_CACHE.get(cache_key)
    if known is None:
        known = set()
        if path.exists():
            with path.open("r", encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    old_key = f"{row.get('timestamp', '')}|{row.get('setup', '')}|{row.get('side', '')}"
                    known.add(old_key)
        with CACHE_LOCK:
            HISTORY_TS_CACHE[cache_key] = known
    if key in known:
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    need_header = (not path.exists()) or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if need_header:
            writer.writerow(AR_COMPARE_HEADER)
        writer.writerow(row)
    with CACHE_LOCK:
        known.add(key)


def enqueue_order_candidate_if_needed(state: dict):
    global daytrade_order_queue
    if daytrade_order_queue is None:
        try:
            import importlib

            daytrade_order_queue = importlib.import_module("daytrade_order_queue")
        except Exception:
            return
    if str(state.get("run_mode", "")).strip().lower() == "replay":
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


def count_values(states: list[dict], key: str) -> dict:
    counts = {}
    for state in states:
        value = str(state.get(key, "") or "").strip() or "-"
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def today_closed_trade_count(states: list[dict], today: str) -> int:
    total = 0
    for state in states:
        for trade in state.get("recent_trades", []):
            if isinstance(trade, dict) and str(trade.get("exit_ts", "")).startswith(today):
                total += 1
    return total


def evaluation_snapshot(results: list[dict], errors: list[str], symbols: list[str], now: dt.datetime) -> dict:
    states = [result.get("state", {}) for result in results if isinstance(result.get("state"), dict)]
    today = now.strftime("%Y-%m-%d")
    total_capital = 0.0
    total_equity_amount = 0.0
    day_start_amount = 0.0
    for state in states:
        capital = parse_float(state.get("sim_capital_yen"), DEFAULT_SETTINGS["sim_capital_yen"])
        equity = parse_float(state.get("equity"), 1.0)
        day_start = parse_float(state.get("day_start_equity"), equity)
        total_capital += capital
        total_equity_amount += capital * equity
        day_start_amount += capital * day_start

    feed = daytrade_status.read_feed_status()
    feed_health = daytrade_status.feed_health(feed, now)
    return {
        "kind": "daytrade_evaluation",
        "logged_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "date": today,
        "market_open": market_calendar.is_market_time(now),
        "symbols_requested": len(symbols),
        "symbols_processed": len(results),
        "processed_bars": sum(parse_int(result.get("processed"), 0) for result in results),
        "errors": errors[:10],
        "position_counts": count_values(states, "current_position_today"),
        "signal_counts": count_values(states, "signal"),
        "action_counts": count_values(states, "last_action"),
        "reason_counts": count_values(states, "strategy_reason"),
        "open_positions": sum(1 for state in states if str(state.get("current_position_today", "")) in ("LONG", "SHORT")),
        "closed_trades_today": today_closed_trade_count(states, today),
        "capital_yen": round(total_capital, 2),
        "equity_amount_yen": round(total_equity_amount, 2),
        "total_pnl_rate": (total_equity_amount / total_capital - 1.0) if total_capital > 0 else 0.0,
        "daily_pnl_rate": (total_equity_amount / day_start_amount - 1.0) if day_start_amount > 0 else 0.0,
        "feed_status": feed.get("status", ""),
        "feed_message": feed.get("message", ""),
        "feed_last_bar_ts": feed.get("last_bar_ts", ""),
        "feed_age_sec": feed_health.get("age_sec"),
        "feed_health_label": feed_health.get("label", ""),
        "feed_health_tone": feed_health.get("tone", ""),
    }


def maybe_append_evaluation_log(results: list[dict], errors: list[str], symbols: list[str]):
    if daytrade_config is None:
        return
    cfg = daytrade_config.load_runtime_config()
    if cfg.get("evaluation_log_enabled", True) is False:
        return
    interval = max(0, parse_int(cfg.get("evaluation_log_interval_seconds"), 300))
    log_name = str(cfg.get("evaluation_log_file", "evaluation_log.jsonl") or "evaluation_log.jsonl").strip()
    if not log_name:
        return
    path = DATA_DIR / log_name
    now = dt.datetime.now()
    cache_key = str(path)
    last_logged = EVALUATION_LOG_CACHE.get(cache_key)
    if last_logged is None and path.exists():
        try:
            last_logged = dt.datetime.fromtimestamp(path.stat().st_mtime)
        except Exception:
            last_logged = None
    if interval > 0 and last_logged is not None and (now - last_logged).total_seconds() < interval:
        return
    append_jsonl(path, evaluation_snapshot(results, errors, symbols, now))
    EVALUATION_LOG_CACHE[cache_key] = now


def fresh_state(symbol: str):
    return {"symbol": symbol, "equity": 1.0, "base_equity": 100.0}


def tick_symbol(symbol: str, replay_all: bool = False, now: dt.datetime | None = None, rebuild_history: bool = True):
    now = now or dt.datetime.now()
    bars = load_intraday_bars(symbol)
    sym_dir, state_path, runner_state_path = state_paths(symbol)
    hist_path = sym_dir / "paper_history_daytrade.csv"
    alert_path = sym_dir / "alerts_daytrade.csv"
    ar_compare_path = sym_dir / "paper_ar_compare_daytrade.csv"
    replay_history_rows: list[list] = []
    replay_alert_rows: list[list] = []
    replay_ar_compare_rows: list[list] = []

    if replay_all:
        state = fresh_state(symbol)
        runner_state = {}
        if rebuild_history:
            with CACHE_LOCK:
                HISTORY_TS_CACHE.pop(str(hist_path), None)
                HISTORY_TS_CACHE.pop(str(ar_compare_path), None)
                ALERT_KEYS_CACHE.pop(str(alert_path), None)
    else:
        state = load_json(state_path, fresh_state(symbol))
        runner_state = load_json(runner_state_path, {})

    last_ts = None if replay_all else parse_ts(str(runner_state.get("last_processed_ts") or state.get("latest_ts") or ""))
    settings = load_symbol_settings(symbol)
    replay_date = ""
    replay_rows_today: list[dict] = []
    replay_previous_close: float | None = None
    replay_max_gap = 0
    replay_max_gap_minutes = max(
        max(1, parse_int(settings.get("bar_interval_minutes"), DEFAULT_SETTINGS["bar_interval_minutes"])) + 1,
        parse_int(settings.get("max_expected_bar_gap_minutes"), DEFAULT_SETTINGS["max_expected_bar_gap_minutes"]),
    )

    processed = 0
    for bar in bars:
        if last_ts is not None and bar["ts"] <= last_ts:
            continue
        if bar["ts"] > now and not replay_all:
            continue
        if replay_all:
            if bar["date"] != replay_date:
                if replay_rows_today:
                    replay_previous_close = replay_rows_today[-1]["close"]
                replay_date = bar["date"]
                replay_rows_today = []
                replay_max_gap = 0
            if replay_rows_today:
                previous_bar = replay_rows_today[-1]
                is_lunch_gap = (
                    previous_bar["ts"].time() <= market_calendar.MORNING_CLOSE
                    and bar["ts"].time() >= market_calendar.AFTERNOON_OPEN
                )
                is_closing_auction = bar["ts"].time() == market_calendar.MARKET_CLOSE
                if not is_lunch_gap and not is_closing_auction:
                    gap_minutes = int((bar["ts"] - previous_bar["ts"]).total_seconds() / 60.0)
                    if gap_minutes > replay_max_gap_minutes:
                        replay_max_gap = max(replay_max_gap, gap_minutes)
            replay_rows_today.append(bar)
            state = process_bar(
                symbol,
                state,
                bars,
                bar,
                settings_override=settings,
                rows_today_override=replay_rows_today,
                previous_close_override=replay_previous_close,
                data_warning_override=f"bar gap {replay_max_gap}m" if replay_max_gap else "",
                skip_portfolio_risk=True,
            )
        else:
            state = process_bar(symbol, state, bars, bar, settings_override=settings)
        state["run_mode"] = "replay" if replay_all else "live"
        runner_state["last_processed_ts"] = bar["timestamp"]
        processed += 1
        if replay_all and rebuild_history:
            replay_history_rows.append(history_row(state))
            compare_row = ar_compare_row(state)
            if compare_row is not None:
                replay_ar_compare_rows.append(compare_row)
            signal_row = alert_row(state)
            if signal_row is not None:
                replay_alert_rows.append(signal_row)
        else:
            append_history_if_needed(sym_dir, state)
            append_ar_compare_if_needed(sym_dir, state)
            append_alert_if_needed(sym_dir, state)
        enqueue_order_candidate_if_needed(state)

    if replay_all and rebuild_history:
        write_csv_atomic(hist_path, HISTORY_HEADER, replay_history_rows)
        if replay_alert_rows:
            write_csv_atomic(alert_path, ALERT_HEADER, replay_alert_rows)
        elif alert_path.exists():
            alert_path.unlink()
        if replay_ar_compare_rows:
            write_csv_atomic(ar_compare_path, AR_COMPARE_HEADER, replay_ar_compare_rows)
        elif ar_compare_path.exists():
            ar_compare_path.unlink()

    monitored = False
    if not replay_all and processed == 0:
        state, monitored = monitor_open_position_without_new_bar(symbol, state, bars, now, last_ts)
        if monitored:
            state["run_mode"] = "live_monitor"
            append_history_if_needed(sym_dir, state)
            append_ar_compare_if_needed(sym_dir, state)
            append_alert_if_needed(sym_dir, state)
            enqueue_order_candidate_if_needed(state)

    state = apply_settings_snapshot(state, settings)
    state = update_sim_fields(state)
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


def replay_symbol_worker(symbol: str):
    return tick_symbol(symbol, replay_all=True)


def tick_all(symbol_filter: str | None = None, replay_all: bool = False, replay_workers: int = 1):
    daytrade_status.set_data_dir(DATA_DIR)
    results = []
    errors = []
    if symbol_filter:
        symbols = [symbol_filter]
    else:
        symbols = [str(item["symbol"]) for item in load_symbols()]

    if replay_all and not symbol_filter and replay_workers > 1:
        order = {symbol: index for index, symbol in enumerate(symbols)}
        with concurrent.futures.ProcessPoolExecutor(max_workers=replay_workers) as executor:
            futures = {executor.submit(replay_symbol_worker, symbol): symbol for symbol in symbols}
            for future in concurrent.futures.as_completed(futures):
                symbol = futures[future]
                try:
                    results.append(future.result())
                except Exception as e:
                    error = f"{symbol}: {type(e).__name__}: {e}"
                    errors.append(error)
                    print(f"[{dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {error}")
        results.sort(key=lambda result: order.get(result["symbol"], len(order)))
    else:
        for symbol in symbols:
            try:
                results.append(tick_symbol(symbol, replay_all=replay_all))
            except Exception as e:
                error = f"{symbol}: {type(e).__name__}: {e}"
                errors.append(error)
                print(f"[{dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {error}")

    daytrade_status.write_runner_status(
        status="error" if errors else "ok",
        message="runner loop completed with symbol errors" if errors else "runner loop completed",
        symbols=[result["symbol"] for result in results],
        processed=sum(parse_int(result.get("processed"), 0) for result in results),
        error="; ".join(errors[:10]),
    )
    maybe_append_evaluation_log(results, errors, symbols)
    return results


def finalize_open_positions(symbol_filter: str | None = None, now: dt.datetime | None = None):
    now = now or dt.datetime.now()
    daytrade_status.set_data_dir(DATA_DIR)

    if symbol_filter:
        symbols = [symbol_filter]
    else:
        symbols = [str(item["symbol"]) for item in load_symbols()]
        state_symbols = sorted(
            path.parent.name for path in DATA_DIR.glob("*/paper_state_daytrade.json")
        )
        symbols.extend(symbol for symbol in state_symbols if symbol not in symbols)

    results = []
    errors = []
    for symbol in symbols:
        sym_dir, state_path, _ = state_paths(symbol)
        state = load_json(state_path, fresh_state(symbol))
        if state.get("current_position_today") not in ("LONG", "SHORT"):
            continue

        try:
            tick_result = tick_symbol(symbol, now=now)
            state = tick_result["state"]

            if state.get("current_position_today") in ("LONG", "SHORT"):
                settings = load_symbol_settings(symbol)
                force_flat_time = parse_time(settings.get("force_flat_time"), DEFAULT_SETTINGS["force_flat_time"])
                bars = load_intraday_bars(symbol)
                latest = latest_bar_at_or_before(bars, now)
                latest_ts = latest["ts"] if latest is not None else parse_ts(state.get("latest_ts"))
                price = latest["close"] if latest is not None else parse_float(state.get("last_price"), 0.0)
                due = latest_ts is not None and (
                    latest_ts.date() < now.date() or now.time() >= force_flat_time
                )
                if due and price > 0:
                    if latest is None:
                        latest = {
                            "timestamp": ts_text(latest_ts),
                            "ts": latest_ts,
                            "date": latest_ts.strftime("%Y-%m-%d"),
                            "open": price,
                            "high": price,
                            "low": price,
                            "close": price,
                            "volume": 0.0,
                        }
                    reason = "STALE_EOD" if latest_ts.date() < now.date() else "EOD"
                    exit_fn = exit_short if state.get("current_position_today") == "SHORT" else exit_long
                    state = exit_fn(state, price, reason, settings, latest)
                    state = apply_settings_snapshot(state, settings)
                    state = update_sim_fields(state)
                    state = update_freshness_fields(state, now, settings)
                    state["run_mode"] = "eod_finalize"
                    state["last_runner_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
                    append_history_if_needed(sym_dir, state)
                    append_alert_if_needed(sym_dir, state)
                    enqueue_order_candidate_if_needed(state)
                    save_json(state_path, state)

            finalized = state.get("current_position_today") not in ("LONG", "SHORT")
            result = {
                "symbol": symbol,
                "finalized": finalized,
                "action": state.get("last_action", ""),
                "latest_ts": state.get("latest_ts", ""),
            }
            results.append(result)
            if not finalized:
                errors.append(f"{symbol}: position remained open")
        except Exception as exc:
            error = f"{symbol}: {type(exc).__name__}: {exc}"
            errors.append(error)
            results.append({"symbol": symbol, "finalized": False, "error": error})

    finalized_symbols = [result["symbol"] for result in results if result.get("finalized")]
    daytrade_status.write_runner_status(
        status="error" if errors else "ok",
        message="eod finalization completed with errors" if errors else "eod finalization completed",
        symbols=finalized_symbols,
        processed=len(finalized_symbols),
        error="; ".join(errors[:10]),
    )
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", type=str, default="")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--finalize-eod", action="store_true")
    parser.add_argument("--replay-all", action="store_true")
    parser.add_argument("--replay-workers", type=int, default=4)
    args = parser.parse_args()

    symbol_filter = args.symbol.strip() or None

    if args.finalize_eod:
        try:
            finalize_open_positions(symbol_filter=symbol_filter)
        except Exception as e:
            daytrade_status.write_runner_status("error", error=f"{type(e).__name__}: {e}")
            raise
        return

    if args.once:
        try:
            tick_all(
                symbol_filter=symbol_filter,
                replay_all=args.replay_all,
                replay_workers=max(1, args.replay_workers),
            )
        except Exception as e:
            daytrade_status.write_runner_status("error", error=f"{type(e).__name__}: {e}")
            raise
        return

    while True:
        try:
            tick_all(symbol_filter=symbol_filter, replay_all=False)
        except Exception as e:
            daytrade_status.write_runner_status("error", error=f"{type(e).__name__}: {e}")
            print(f"[{dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] runner loop error: {type(e).__name__}: {e}")
        time.sleep(CHECK_INTERVAL_SEC)


if __name__ == "__main__":
    main()
