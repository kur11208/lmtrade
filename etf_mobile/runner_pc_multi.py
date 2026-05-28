from __future__ import annotations

from pathlib import Path
import argparse
import bisect
import csv
import json
import time
import datetime as dt

try:
    import jpholiday
except Exception:
    jpholiday = None

DATA_DIR = Path("data")
SYMBOLS_FILE = DATA_DIR / "symbols.json"
SETTINGS_KEYS = (
    "buy_th",
    "sell_th",
    "risk_cash",
    "atr_mult",
    "fee_rate",
    "slippage_rate",
    "trend_ma_days",
    "momentum_days",
    "max_atr_rate",
    "max_position_w",
    "strategy_enabled",
)
DEFAULT_FEE_RATE = 0.0005
DEFAULT_SLIPPAGE_RATE = 0.0005
DEFAULT_TREND_MA_DAYS = 200
DEFAULT_MOMENTUM_DAYS = 20
DEFAULT_MAX_ATR_RATE = 0.06
DEFAULT_MAX_POSITION_W = 1.0
SETTINGS_CACHE = {}
OHLC_CACHE = {}
HISTORY_DATE_CACHE = {}

SIGNAL_HOUR = 18
SIGNAL_MINUTE = 5
EXEC_HOUR = 9
EXEC_MINUTE = 0
CHECK_INTERVAL_SEC = 20
BACKFILL_MIN_HISTORY_ROWS = 2
BACKFILL_MAX_HISTORY_ROWS = 220
DATA_STATUS_OK = "ok"
DATA_STATUS_MISSING = "missing_ohlc"
DATA_STATUS_STALE = "stale_ohlc"


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
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_symbols():
    rows = load_json(SYMBOLS_FILE, [])
    return [x for x in rows if x.get("enabled", True) is not False]


def is_jp_trading_day(date_str: str) -> bool:
    d = dt.datetime.strptime(date_str, "%Y-%m-%d").date()
    if d.weekday() >= 5:
        return False
    if jpholiday is not None and jpholiday.is_holiday(d):
        return False
    return True


def next_jp_trading_day_str(date_str: str) -> str:
    d = dt.datetime.strptime(date_str, "%Y-%m-%d").date()
    while True:
        d = d + dt.timedelta(days=1)
        s = d.strftime("%Y-%m-%d")
        if is_jp_trading_day(s):
            return s


def previous_jp_trading_day_str(date_str: str) -> str:
    d = dt.datetime.strptime(date_str, "%Y-%m-%d").date()
    while True:
        d = d - dt.timedelta(days=1)
        s = d.strftime("%Y-%m-%d")
        if is_jp_trading_day(s):
            return s


def expected_market_data_date(now: dt.datetime) -> str:
    today_s = now.strftime("%Y-%m-%d")
    if is_jp_trading_day(today_s) and (now.hour, now.minute) >= (SIGNAL_HOUR, SIGNAL_MINUTE):
        return today_s
    return previous_jp_trading_day_str(today_s)


def compare_date(left: str, right: str) -> int:
    left = str(left or "").strip()
    right = str(right or "").strip()
    if left == right:
        return 0
    if not left:
        return -1
    if not right:
        return 1
    return 1 if left > right else -1


def position_name(w: float) -> str:
    return "LONG" if float(w) > 0 else "CASH"


def action_name(current_w: float, next_w: float) -> str:
    cur_long = float(current_w) > 0
    nxt_long = float(next_w) > 0

    if (not cur_long) and nxt_long:
        return "BUY_NEXT_OPEN"
    if cur_long and (not nxt_long):
        return "SELL_NEXT_OPEN"
    if cur_long and nxt_long:
        return "HOLD_LONG"
    return "HOLD_CASH"


def build_display_fields(
    today_is_trading_day: bool,
    current_position_today: str,
    pending_action: str,
    effective_from_date: str,
    executed_today: bool,
    executed_action: str,
    next_position: str,
):
    market_status = "本日は取引日" if today_is_trading_day else "本日は休場日"

    if today_is_trading_day:
        if executed_today and executed_action == "BUY_NEXT_OPEN":
            today_status = "今日は寄りで買い反映済み（LONG）"
        elif executed_today and executed_action == "SELL_NEXT_OPEN":
            today_status = "今日は寄りで売り反映済み（CASH）"
        elif executed_today and executed_action == "HOLD_LONG":
            today_status = "今日は LONG 継続"
        elif executed_today and executed_action == "HOLD_CASH":
            today_status = "今日は CASH 継続"
        else:
            today_status = f"今日は {current_position_today}"
    else:
        today_status = "今日は休場日のため売買執行なし"

    if pending_action == "BUY_NEXT_OPEN":
        next_plan = f"{effective_from_date} に BUY_NEXT_OPEN 予定"
    elif pending_action == "SELL_NEXT_OPEN":
        next_plan = f"{effective_from_date} に SELL_NEXT_OPEN 予定"
    elif pending_action == "HOLD_LONG":
        next_plan = f"{effective_from_date} も LONG 継続予定"
    elif pending_action == "HOLD_CASH":
        next_plan = f"{effective_from_date} も CASH 継続予定"
    else:
        next_plan = f"{effective_from_date} に {next_position} 予定"

    note = ""
    if not today_is_trading_day:
        note = "休場日のため、価格グラフは前営業日ベースの表示になる場合があります"

    return market_status, today_status, next_plan, note


def parse_float(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default


def clamp_weight(value) -> float:
    return max(0.0, min(1.0, parse_float(value, 0.0)))


def clamp_rate(value, default=0.0) -> float:
    return max(0.0, parse_float(value, default))


def update_sim_fields(state: dict) -> dict:
    equity = parse_float(state.get("equity"), 1.0)
    base_equity = parse_float(state.get("base_equity"), 100.0)
    state["raw_equity"] = base_equity * equity
    state["sim_amount_yen"] = equity * 10000.0
    state["sim_pnl_rate"] = equity - 1.0
    state["sim_pnl_yen"] = state["sim_amount_yen"] - 10000.0
    return state


def trade_cost_rate(state: dict) -> float:
    fee_rate = clamp_rate(state.get("fee_rate"), DEFAULT_FEE_RATE)
    slippage_rate = clamp_rate(state.get("slippage_rate"), DEFAULT_SLIPPAGE_RATE)
    return fee_rate + slippage_rate


def load_symbol_settings(symbol: str) -> dict:
    path = DATA_DIR / symbol / "paper_settings.json"
    if not path.exists():
        return {}

    stat = path.stat()
    cache_key = (str(path), stat.st_mtime_ns, stat.st_size)
    cached = SETTINGS_CACHE.get(symbol)
    if cached and cached.get("cache_key") == cache_key:
        return dict(cached["settings"])

    settings = load_json(path, {})
    SETTINGS_CACHE[symbol] = {
        "cache_key": cache_key,
        "settings": dict(settings),
    }
    return settings


def apply_symbol_settings(symbol: str, state: dict) -> dict:
    settings = load_symbol_settings(symbol)
    for key in SETTINGS_KEYS:
        if key in settings:
            state[key] = settings[key]
    return state


def load_ohlc_data(symbol: str) -> dict:
    path = DATA_DIR / symbol / "ohlc.csv"
    if not path.exists():
        return {"rows": [], "dates": []}

    stat = path.stat()
    cache_key = (str(path), stat.st_mtime_ns, stat.st_size)
    cached = OHLC_CACHE.get(symbol)
    if cached and cached.get("cache_key") == cache_key:
        return cached

    rows = []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                date_s = str(row.get("date", "")).strip()
                close = parse_float(row.get("close"), None)
                if not date_s or close is None or close <= 0:
                    continue
                open_price = parse_float(row.get("open"), close)
                high = parse_float(row.get("high"), max(open_price, close))
                low = parse_float(row.get("low"), min(open_price, close))
                rows.append({
                    "date": date_s,
                    "open": open_price,
                    "high": max(high, open_price, close),
                    "low": min(low, open_price, close),
                    "close": close,
                })
    except Exception:
        return {"rows": [], "dates": []}

    rows = sorted(rows, key=lambda x: x["date"])
    data = {
        "cache_key": cache_key,
        "rows": rows,
        "dates": [row["date"] for row in rows],
    }
    OHLC_CACHE[symbol] = data
    return data


def load_ohlc_rows(symbol: str) -> list[dict]:
    return load_ohlc_data(symbol)["rows"]


def latest_ohlc_date(symbol: str) -> str:
    dates = load_ohlc_data(symbol)["dates"]
    return dates[-1] if dates else ""


def classify_ohlc_freshness(symbol: str, expected_date: str) -> tuple[str, str]:
    latest = latest_ohlc_date(symbol)
    if not latest:
        return DATA_STATUS_MISSING, latest
    if compare_date(latest, expected_date) < 0:
        return DATA_STATUS_STALE, latest
    return DATA_STATUS_OK, latest


def mark_market_data_blocked(
    state: dict,
    runner_state: dict,
    now: dt.datetime,
    status: str,
    expected_date: str,
    latest_date: str,
) -> tuple[dict, dict]:
    state["today_jst"] = now.strftime("%Y-%m-%d")
    state["today_is_trading_day"] = is_jp_trading_day(state["today_jst"])
    state["data_freshness_status"] = status
    state["data_expected_date"] = expected_date
    state["data_latest_ohlc_date"] = latest_date
    state["evaluation_status"] = "blocked_stale_data"
    state["price_source"] = status
    state["pred_source"] = "blocked_stale_data"
    state["strategy_reason"] = "OHLC data missing" if status == DATA_STATUS_MISSING else "OHLC data stale"
    state["display_market_status"] = "DATA STALE"
    state["display_today_status"] = "DATA STALE: evaluation paused"
    state["display_next_plan"] = "OHLC refresh required"
    state["display_note"] = (
        f"DATA STALE: latest OHLC={latest_date or '-'} expected={expected_date}. "
        "Trading evaluation is paused until OHLC is refreshed."
    )
    return update_sim_fields(state), runner_state


def calc_atr_from_rows(rows: list[dict], fallback: float, end_idx: int | None = None) -> float:
    if not rows:
        return float(max(parse_float(fallback, 1.0), 1.0))

    if end_idx is None:
        end_idx = len(rows) - 1
    end_idx = max(0, min(end_idx, len(rows) - 1))
    start = max(0, end_idx - 13)
    trs = []
    for idx in range(start, end_idx + 1):
        row = rows[idx]
        prev_close = rows[idx - 1]["close"] if idx > 0 else row["close"]
        tr = max(
            row["high"] - row["low"],
            abs(row["high"] - prev_close),
            abs(row["low"] - prev_close),
        )
        if tr > 0:
            trs.append(tr)

    if not trs:
        return float(max(parse_float(fallback, 1.0), 1.0))
    return float(max(sum(trs) / len(trs), 1.0))


def calc_ma_from_rows(rows: list[dict], end_idx: int, days: int):
    if days <= 0 or end_idx < days - 1:
        return None
    start = end_idx - days + 1
    values = [row["close"] for row in rows[start:end_idx + 1]]
    if not values:
        return None
    return sum(values) / len(values)


def calc_return_from_rows(rows: list[dict], end_idx: int, days: int):
    if days <= 0 or end_idx < days:
        return None
    prev = rows[end_idx - days]["close"]
    if prev <= 0:
        return None
    return rows[end_idx]["close"] / prev - 1.0


def load_market_snapshot(symbol: str, today_s: str, current_latest_date: str, prev_atr: float):
    data = load_ohlc_data(symbol)
    rows = data["rows"]
    dates = data["dates"]
    idx = bisect.bisect_right(dates, today_s) - 1
    if idx < 0:
        return None

    latest = rows[idx]
    if current_latest_date and latest["date"] < current_latest_date:
        return None

    return {
        "date": latest["date"],
        "open": latest["open"],
        "high": latest["high"],
        "low": latest["low"],
        "close": latest["close"],
        "atr_14": calc_atr_from_rows(rows, prev_atr, end_idx=idx),
        "price_source": "ohlc.csv",
        "rows": rows,
        "idx": idx,
    }


def calc_rule_strategy(
    current_pos: str,
    buy_th: float,
    sell_th: float,
    risk_cash: float,
    max_position_w: float,
    market: dict,
    trend_ma_days: int,
    momentum_days: int,
    max_atr_rate: float,
) -> dict:
    rows = market["rows"]
    idx = market["idx"]
    close = market["close"]
    atr_14 = market["atr_14"]
    ma = calc_ma_from_rows(rows, idx, trend_ma_days)
    momentum = calc_return_from_rows(rows, idx, momentum_days)
    atr_rate = atr_14 / close if close > 0 else None

    score = 0.50
    reasons = []

    trend_ready = ma is not None
    trend_ok = True if ma is None else close >= ma
    if ma is None:
        score -= 0.05
        reasons.append("MA不足")
    elif trend_ok:
        score += 0.20
        reasons.append("MA上")
    else:
        score -= 0.25
        reasons.append("MA下")

    momentum_ok = True if momentum is None else momentum > 0
    if momentum is None:
        score -= 0.03
        reasons.append("momentum不足")
    elif momentum > 0.03:
        score += 0.15
        reasons.append("momentum強")
    elif momentum > 0:
        score += 0.08
        reasons.append("momentum正")
    elif momentum < -0.05:
        score -= 0.20
        reasons.append("momentum弱")
    else:
        score -= 0.10
        reasons.append("momentum負")

    atr_ok = True
    dynamic_risk_cash = risk_cash
    if atr_rate is None:
        score -= 0.02
        reasons.append("ATR不足")
    elif atr_rate > max_atr_rate:
        score -= 0.20
        dynamic_risk_cash += 0.30
        atr_ok = False
        reasons.append("ATR高")
    elif atr_rate > max_atr_rate * 0.75:
        score -= 0.05
        dynamic_risk_cash += 0.15
        reasons.append("ATRやや高")
    else:
        score += 0.07
        reasons.append("ATR通常")

    score = max(0.01, min(0.99, score))
    dynamic_risk_cash = max(0.0, min(0.95, dynamic_risk_cash))
    target_w = min(max_position_w, max(0.0, 1.0 - dynamic_risk_cash))

    force_sell = (
        (trend_ready and not trend_ok)
        or (momentum is not None and momentum < -0.05)
        or not atr_ok
        or score <= sell_th
    )
    allow_buy = trend_ok and momentum_ok and atr_ok and score >= buy_th

    if current_pos == "LONG":
        next_w = 0.0 if force_sell else target_w
    else:
        next_w = target_w if allow_buy else 0.0

    return {
        "pred_prob": round(score, 6),
        "pred_source": f"rule_ma{trend_ma_days}_mom{momentum_days}_atr",
        "next_w": next_w,
        "ma": ma,
        "momentum": momentum,
        "atr_rate": atr_rate,
        "dynamic_risk_cash": dynamic_risk_cash,
        "target_w": target_w,
        "strategy_reason": ",".join(reasons),
        "allow_buy": allow_buy,
        "force_sell": force_sell,
    }


def disabled_strategy_result(reason: str) -> dict:
    return {
        "pred_prob": 0.0,
        "pred_source": "disabled_by_settings",
        "next_w": 0.0,
        "ma": None,
        "momentum": None,
        "atr_rate": None,
        "dynamic_risk_cash": 1.0,
        "target_w": 0.0,
        "strategy_reason": reason,
        "allow_buy": False,
        "force_sell": True,
    }


def calc_dummy_prob(today: dt.date, symbol: str) -> float:
    base_map = {
        "1321": 0.52,
        "1306": 0.56,
        "1343": 0.47,
        "1540": 0.58,
        "2510": 0.49,
    }
    wiggle_map = {
        "1321": [-0.03, -0.01, 0.00, 0.02, 0.04],
        "1306": [-0.02, 0.00, 0.01, 0.03, 0.05],
        "1343": [-0.04, -0.02, 0.00, 0.01, 0.03],
        "1540": [-0.01, 0.00, 0.02, 0.03, 0.05],
        "2510": [-0.03, -0.01, 0.00, 0.01, 0.04],
    }
    base = base_map.get(symbol, 0.52)
    wiggles = wiggle_map.get(symbol, [-0.03, -0.01, 0.00, 0.02, 0.04])
    value = base + wiggles[today.day % len(wiggles)]
    value = max(0.01, min(0.99, value))
    return round(value, 6)


def calc_dummy_close(today: dt.date, current_close: float, symbol: str) -> float:
    delta_map = {
        "1321": [-120, -40, 0, 60, 140],
        "1306": [-6, -2, 0, 3, 8],
        "1343": [-12, -4, 0, 5, 10],
        "1540": [-80, -20, 0, 30, 90],
        "2510": [-5, -2, 0, 3, 7],
    }
    deltas = delta_map.get(symbol, [-10, -3, 0, 4, 9])
    out = current_close + deltas[today.day % len(deltas)]
    return float(max(out, 1.0))


def calc_dummy_atr(today: dt.date, current_atr: float, symbol: str) -> float:
    mul_map = {
        "1321": [0.96, 0.98, 1.00, 1.02, 1.04],
        "1306": [0.94, 0.97, 1.00, 1.03, 1.06],
        "1343": [0.93, 0.97, 1.00, 1.02, 1.05],
        "1540": [0.95, 0.98, 1.00, 1.02, 1.05],
        "2510": [0.94, 0.98, 1.00, 1.03, 1.05],
    }
    muls = mul_map.get(symbol, [0.96, 0.98, 1.00, 1.02, 1.04])
    out = current_atr * muls[today.day % len(muls)]
    return float(max(out, 1.0))


def append_history_if_needed(sym_dir: Path, state: dict):
    hist_path = sym_dir / "paper_history.csv"
    target_date = str(state.get("latest_date", "")).strip()
    if not target_date:
        return

    cache_key = str(hist_path)
    known_dates = HISTORY_DATE_CACHE.get(cache_key)
    if known_dates is None:
        known_dates = set()
        if hist_path.exists():
            with hist_path.open("r", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    date_s = str(row.get("date", "")).strip()
                    if date_s:
                        known_dates.add(date_s)
        HISTORY_DATE_CACHE[cache_key] = known_dates

    if target_date in known_dates:
        return

    need_header = (not hist_path.exists()) or hist_path.stat().st_size == 0
    with hist_path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if need_header:
            writer.writerow(["date", "equity", "w_prev", "desired_pos_prev", "stop_price"])
        writer.writerow([
            target_date,
            state.get("equity", 1.0),
            state.get("w_prev", 0.0),
            state.get("desired_pos_prev", 0),
            "" if state.get("stop_price") in [None, ""] else state.get("stop_price"),
        ])
    known_dates.add(target_date)


def count_history_rows(symbol: str) -> int:
    hist_path = DATA_DIR / symbol / "paper_history.csv"
    if not hist_path.exists():
        return 0
    try:
        with hist_path.open("r", encoding="utf-8-sig", newline="") as f:
            return sum(1 for _ in csv.DictReader(f))
    except Exception:
        return 0


def fresh_backfill_state(symbol: str) -> dict:
    state = {
        "symbol": symbol,
        "latest_date": "",
        "equity": 1.0,
        "base_equity": 100.0,
        "close": 100.0,
        "atr_14": 2.0,
        "current_w_today": 0.0,
        "current_position_today": "CASH",
        "pending_action": "HOLD_CASH",
        "next_w": 0.0,
        "buy_th": 0.50,
        "sell_th": 0.43,
        "risk_cash": 0.0,
        "atr_mult": 2.0,
        "fee_rate": DEFAULT_FEE_RATE,
        "slippage_rate": DEFAULT_SLIPPAGE_RATE,
        "trend_ma_days": DEFAULT_TREND_MA_DAYS,
        "momentum_days": DEFAULT_MOMENTUM_DAYS,
        "max_atr_rate": DEFAULT_MAX_ATR_RATE,
        "max_position_w": DEFAULT_MAX_POSITION_W,
        "strategy_enabled": True,
    }
    return update_sim_fields(apply_symbol_settings(symbol, state))


def simulate_history_rows(symbol: str, max_rows: int = BACKFILL_MAX_HISTORY_ROWS) -> list[dict]:
    rows = load_ohlc_rows(symbol)
    if len(rows) < BACKFILL_MIN_HISTORY_ROWS:
        return []

    state = fresh_backfill_state(symbol)
    runner_state = {
        "last_signal_run_date": "",
        "last_execution_run_date": "",
    }
    out = []
    for row in rows:
        d = dt.datetime.strptime(row["date"], "%Y-%m-%d")
        state, runner_state = execute_pending(state, runner_state, d.replace(hour=EXEC_HOUR, minute=EXEC_MINUTE), force=False)
        state, runner_state = run_signal(state, runner_state, d.replace(hour=SIGNAL_HOUR, minute=SIGNAL_MINUTE + 5), force=False)
        latest_date = str(state.get("latest_date", "")).strip()
        if latest_date:
            out.append({
                "date": latest_date,
                "equity": state.get("equity", 1.0),
                "w_prev": state.get("w_prev", 0.0),
                "desired_pos_prev": state.get("desired_pos_prev", 0),
                "stop_price": "" if state.get("stop_price") in [None, ""] else state.get("stop_price"),
            })

    unique = {}
    for row in out:
        unique[row["date"]] = row
    result = [unique[key] for key in sorted(unique.keys())]
    return result[-max(2, max_rows):]


def write_history_rows(symbol: str, rows: list[dict]):
    if len(rows) < BACKFILL_MIN_HISTORY_ROWS:
        return 0
    hist_path = DATA_DIR / symbol / "paper_history.csv"
    hist_path.parent.mkdir(parents=True, exist_ok=True)
    with hist_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "equity", "w_prev", "desired_pos_prev", "stop_price"])
        for row in rows:
            writer.writerow([
                row.get("date", ""),
                row.get("equity", 1.0),
                row.get("w_prev", 0.0),
                row.get("desired_pos_prev", 0),
                row.get("stop_price", ""),
            ])
    HISTORY_DATE_CACHE.pop(str(hist_path), None)
    return len(rows)


def backfill_history(symbol: str, replace: bool = False, min_rows: int = BACKFILL_MIN_HISTORY_ROWS) -> dict:
    existing = count_history_rows(symbol)
    if not replace and existing >= min_rows:
        return {"symbol": symbol, "status": "skipped", "existing": existing, "written": 0}

    rows = simulate_history_rows(symbol)
    written = write_history_rows(symbol, rows)
    if written <= 0:
        return {"symbol": symbol, "status": "no_ohlc_history", "existing": existing, "written": 0}
    return {"symbol": symbol, "status": "written", "existing": existing, "written": written}


def backfill_all_history(symbol_filter: str | None = None, replace: bool = False) -> list[dict]:
    results = []
    for item in load_symbols():
        symbol = str(item["symbol"])
        if symbol_filter and symbol != symbol_filter:
            continue
        result = backfill_history(symbol, replace=replace)
        results.append(result)
        print(
            f"backfill {symbol} status={result['status']} "
            f"existing={result['existing']} written={result['written']}"
        )
    return results


def state_paths(symbol: str):
    sym_dir = DATA_DIR / symbol
    return (
        sym_dir,
        sym_dir / "paper_state_atr.json",
        sym_dir / "mobile_runner_state.json",
    )


def execute_pending(state: dict, runner_state: dict, now: dt.datetime, force: bool = False):
    today_s = now.strftime("%Y-%m-%d")
    symbol = str(state.get("symbol", "")).strip()
    if symbol:
        state = apply_symbol_settings(symbol, state)

    if not force:
        if not is_jp_trading_day(today_s):
            return state, runner_state
        if (now.hour, now.minute) < (EXEC_HOUR, EXEC_MINUTE):
            return state, runner_state
        if runner_state.get("last_execution_run_date") == today_s:
            return state, runner_state
        if str(state.get("effective_from_date", "")) != today_s:
            return state, runner_state

    action = str(state.get("pending_action", "HOLD_CASH"))
    current_w = parse_float(state.get("current_w_today"), 0.0)
    current_pos = str(state.get("current_position_today", "CASH"))
    next_w = parse_float(state.get("next_w"), current_w)
    old_w = current_w

    executed_action = action
    executed_today = True

    if action == "BUY_NEXT_OPEN" and current_pos == "CASH":
        current_w = next_w
        current_pos = "LONG"
    elif action == "SELL_NEXT_OPEN" and current_pos == "LONG":
        current_w = 0.0
        current_pos = "CASH"
    elif action == "HOLD_LONG":
        current_w = next_w
        current_pos = "LONG"
    elif action == "HOLD_CASH":
        current_w = 0.0
        current_pos = "CASH"
    elif force:
        executed_action = "NONE"
        executed_today = False

    cost_rate = trade_cost_rate(state)
    cost_weight = abs(clamp_weight(current_w) - clamp_weight(old_w))
    executed_trade_cost = cost_weight * cost_rate if executed_today else 0.0
    if executed_trade_cost > 0:
        state["equity"] = max(0.0, parse_float(state.get("equity"), 1.0) * (1.0 - executed_trade_cost))

    state["executed_today"] = executed_today
    state["executed_action"] = executed_action
    state["executed_trade_cost"] = executed_trade_cost
    state["current_w_today"] = current_w
    state["current_position_today"] = current_pos
    state["position"] = current_pos

    if current_pos == "LONG":
        state["current_stop_today"] = state.get("next_stop_price")
        state["stop_price"] = state.get("next_stop_price")
    else:
        state["current_stop_today"] = None
        state["stop_price"] = None

    if executed_today or force:
        runner_state["last_execution_run_date"] = today_s

    state = update_sim_fields(state)
    return state, runner_state


def run_signal(state: dict, runner_state: dict, now: dt.datetime, force: bool = False):
    today_s = now.strftime("%Y-%m-%d")
    today_d = now.date()

    if not force:
        if not is_jp_trading_day(today_s):
            state["today_jst"] = today_s
            state["today_is_trading_day"] = False
            state["display_market_status"] = "本日は休場日"
            state["display_today_status"] = "今日は休場日のため売買執行なし"
            state["display_note"] = "休場日のため、価格グラフは前営業日ベースの表示になる場合があります"
            return state, runner_state

        if (now.hour, now.minute) < (SIGNAL_HOUR, SIGNAL_MINUTE):
            return state, runner_state

        if runner_state.get("last_signal_run_date") == today_s:
            return state, runner_state

    symbol = str(state["symbol"])
    state = apply_symbol_settings(symbol, state)
    buy_th = parse_float(state.get("buy_th"), 0.50)
    sell_th = parse_float(state.get("sell_th"), 0.43)
    risk_cash = clamp_weight(state.get("risk_cash"))
    atr_mult = parse_float(state.get("atr_mult"), 2.0)
    fee_rate = clamp_rate(state.get("fee_rate"), DEFAULT_FEE_RATE)
    slippage_rate = clamp_rate(state.get("slippage_rate"), DEFAULT_SLIPPAGE_RATE)
    trend_ma_days = int(parse_float(state.get("trend_ma_days"), DEFAULT_TREND_MA_DAYS))
    momentum_days = int(parse_float(state.get("momentum_days"), DEFAULT_MOMENTUM_DAYS))
    max_atr_rate = clamp_rate(state.get("max_atr_rate"), DEFAULT_MAX_ATR_RATE)
    max_position_w = clamp_weight(state.get("max_position_w", DEFAULT_MAX_POSITION_W))
    strategy_enabled = state.get("strategy_enabled", True) is not False
    trend_ma_days = max(20, trend_ma_days)
    momentum_days = max(2, momentum_days)
    max_atr_rate = max(0.005, max_atr_rate)
    max_position_w = max(0.0, min(DEFAULT_MAX_POSITION_W, max_position_w))

    prev_close = parse_float(state.get("close"), 1000.0)
    prev_atr = parse_float(state.get("atr_14"), max(prev_close * 0.02, 1.0))
    equity = parse_float(state.get("equity"), 1.0)

    current_pos = str(state.get("current_position_today", "CASH"))
    current_w = clamp_weight(state.get("current_w_today"))

    expected_date = expected_market_data_date(now)
    data_status, available_ohlc_date = classify_ohlc_freshness(symbol, expected_date)
    if data_status != DATA_STATUS_OK:
        return mark_market_data_blocked(state, runner_state, now, data_status, expected_date, available_ohlc_date)

    market = load_market_snapshot(
        symbol=symbol,
        today_s=today_s,
        current_latest_date=str(state.get("latest_date", "")).strip(),
        prev_atr=prev_atr,
    )
    if market is None:
        return mark_market_data_blocked(state, runner_state, now, DATA_STATUS_STALE, expected_date, available_ohlc_date)
    else:
        close = market["close"]
        atr_14 = market["atr_14"]
        open_price = market["open"]
        high = market["high"]
        low = market["low"]
        signal_date = market["date"]
        price_source = market["price_source"]

    if not strategy_enabled:
        pred_prob = 0.0
        pred_source = "disabled_by_settings"
        strategy = disabled_strategy_result("strategy disabled")
    elif market is None:
        pred_prob = calc_dummy_prob(today_d, symbol)
        pred_source = "dummy_calendar"
        strategy = {
            "next_w": None,
            "ma": None,
            "momentum": None,
            "atr_rate": atr_14 / close if close > 0 else None,
            "dynamic_risk_cash": risk_cash,
            "target_w": min(max_position_w, max(0.0, 1.0 - risk_cash)),
            "strategy_reason": "dummy fallback",
            "allow_buy": None,
            "force_sell": None,
        }
    else:
        strategy = calc_rule_strategy(
            current_pos=current_pos,
            buy_th=buy_th,
            sell_th=sell_th,
            risk_cash=risk_cash,
            max_position_w=max_position_w,
            market=market,
            trend_ma_days=trend_ma_days,
            momentum_days=momentum_days,
            max_atr_rate=max_atr_rate,
        )
        pred_prob = strategy["pred_prob"]
        pred_source = strategy["pred_source"]

    stop_triggered_today = False
    stop_exit_price = None
    position_return = 0.0
    stop_trade_cost = 0.0

    if current_pos == "LONG" and prev_close > 0:
        exposure_w = current_w
        prev_stop = parse_float(state.get("current_stop_today", state.get("stop_price")), None)
        if prev_stop is not None and prev_stop > 0 and low <= prev_stop:
            stop_exit_price = open_price if open_price <= prev_stop else prev_stop
            stop_exit_price = max(stop_exit_price, 0.01)
            position_return = stop_exit_price / prev_close - 1.0
            stop_triggered_today = True
            current_w = 0.0
            current_pos = "CASH"
            stop_trade_cost = exposure_w * (fee_rate + slippage_rate)
        else:
            position_return = close / prev_close - 1.0
        equity = equity * max(0.0, 1.0 + exposure_w * position_return - stop_trade_cost)

    if not strategy_enabled:
        next_w = 0.0
    elif market is None:
        if current_pos == "CASH":
            next_w = min(max_position_w, max(0.0, 1.0 - risk_cash)) if pred_prob >= buy_th else 0.0
        else:
            next_w = 0.0 if pred_prob <= sell_th else min(max_position_w, max(0.0, 1.0 - risk_cash))
    else:
        if stop_triggered_today:
            strategy = calc_rule_strategy(
                current_pos=current_pos,
                buy_th=buy_th,
                sell_th=sell_th,
                risk_cash=risk_cash,
                max_position_w=max_position_w,
                market=market,
                trend_ma_days=trend_ma_days,
                momentum_days=momentum_days,
                max_atr_rate=max_atr_rate,
            )
            pred_prob = strategy["pred_prob"]
        next_w = strategy["next_w"]

    next_position = position_name(next_w)
    pending_action = action_name(current_w, next_w)

    current_stop_today = None
    if current_pos == "LONG":
        current_stop_today = close - atr_mult * atr_14

    next_stop_price = None
    if next_position == "LONG":
        next_stop_price = close - atr_mult * atr_14

    effective_from_date = next_jp_trading_day_str(signal_date)

    state["signal_date"] = signal_date
    state["effective_from_date"] = effective_from_date
    state["latest_date"] = signal_date
    state["data_freshness_status"] = DATA_STATUS_OK
    state["data_expected_date"] = expected_date
    state["data_latest_ohlc_date"] = available_ohlc_date
    state["evaluation_status"] = "ok"
    state["today_jst"] = today_s
    state["today_is_trading_day"] = True
    state["equity"] = equity
    state["pred_prob"] = pred_prob
    state["pred_source"] = pred_source
    state["price_source"] = price_source
    state["strategy_reason"] = strategy["strategy_reason"]
    state["strategy_enabled"] = strategy_enabled
    state["trend_ma_days"] = trend_ma_days
    state["trend_ma"] = strategy["ma"]
    state["momentum_days"] = momentum_days
    state["momentum_return"] = strategy["momentum"]
    state["atr_rate"] = strategy["atr_rate"]
    state["max_atr_rate"] = max_atr_rate
    state["max_position_w"] = max_position_w
    state["dynamic_risk_cash"] = strategy["dynamic_risk_cash"]
    state["target_w"] = strategy["target_w"]
    state["allow_buy"] = strategy["allow_buy"]
    state["force_sell"] = strategy["force_sell"]
    state["fee_rate"] = fee_rate
    state["slippage_rate"] = slippage_rate
    state["trade_cost_rate"] = fee_rate + slippage_rate
    state["close"] = close
    state["open"] = open_price
    state["high"] = high
    state["low"] = low
    state["atr_14"] = atr_14
    state["current_w_today"] = current_w
    state["current_position_today"] = current_pos
    state["current_stop_today"] = current_stop_today
    state["w_prev"] = current_w
    state["desired_pos_prev"] = 1 if next_position == "LONG" else 0
    state["position"] = current_pos
    state["stop_price"] = current_stop_today
    state["position_return"] = position_return
    state["stop_triggered_today"] = stop_triggered_today
    state["stop_exit_price"] = stop_exit_price
    state["stop_trade_cost"] = stop_trade_cost
    state["next_w"] = next_w
    state["next_position"] = next_position
    state["next_stop_price"] = next_stop_price
    state["pending_action"] = pending_action

    if runner_state.get("last_execution_run_date") != today_s:
        state["executed_today"] = False
        state["executed_action"] = "NONE"

    display_market_status, display_today_status, display_next_plan, display_note = build_display_fields(
        today_is_trading_day=True,
        current_position_today=current_pos,
        pending_action=pending_action,
        effective_from_date=effective_from_date,
        executed_today=bool(state.get("executed_today", False)),
        executed_action=str(state.get("executed_action", "NONE")),
        next_position=next_position,
    )

    state["display_market_status"] = display_market_status
    state["display_today_status"] = display_today_status
    state["display_next_plan"] = display_next_plan
    state = update_sim_fields(state)

    source_note = "予測はMA/勢い/ATRのルール判定です"
    if pred_source == "dummy_calendar":
        source_note = "予測はテスト用ダミーです"
    if price_source == "ohlc.csv":
        source_note += " / 価格はohlc.csvを使用"
    else:
        source_note += " / 価格もダミー更新"
    if strategy["strategy_reason"]:
        source_note += f" / 理由: {strategy['strategy_reason']}"
    if stop_triggered_today:
        source_note = f"ATRストップ発動: {round(stop_exit_price, 4)} でCASH化 / " + source_note
    state["display_note"] = " / ".join(x for x in [display_note, source_note] if x)

    runner_state["last_signal_run_date"] = today_s
    return state, runner_state


def refresh_runner_stamp(state: dict, now: dt.datetime):
    state["last_runner_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
    state["today_jst"] = now.strftime("%Y-%m-%d")
    return state


def tick_symbol(symbol: str, force_signal: bool = False, force_execute: bool = False):
    now = dt.datetime.now()
    sym_dir, state_path, runner_state_path = state_paths(symbol)

    state = load_json(state_path, {})
    runner_state = load_json(runner_state_path, {
        "last_signal_run_date": "",
        "last_execution_run_date": ""
    })

    state = refresh_runner_stamp(state, now)

    state, runner_state = execute_pending(state, runner_state, now, force=force_execute)
    state, runner_state = run_signal(state, runner_state, now, force=force_signal)

    save_json(state_path, state)
    save_json(runner_state_path, runner_state)

    if str(state.get("latest_date", "")).strip():
        append_history_if_needed(sym_dir, state)

    print(
        f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] "
        f"{symbol} "
        f"pos={state.get('current_position_today')} "
        f"next={state.get('pending_action')} "
        f"equity={round(parse_float(state.get('equity'), 1.0), 6)} "
        f"prob={round(parse_float(state.get('pred_prob'), 0.0), 6)}"
    )


def tick_all(symbol_filter: str | None = None, force_signal: bool = False, force_execute: bool = False):
    for item in load_symbols():
        symbol = str(item["symbol"])
        if symbol_filter and symbol != symbol_filter:
            continue
        tick_symbol(symbol, force_signal=force_signal, force_execute=force_execute)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--signal-now", action="store_true")
    parser.add_argument("--execute-now", action="store_true")
    parser.add_argument("--backfill-history", action="store_true")
    parser.add_argument("--replace-history", action="store_true")
    parser.add_argument("--symbol", type=str, default="")
    args = parser.parse_args()

    symbol_filter = args.symbol.strip() or None

    if args.backfill_history:
        backfill_all_history(symbol_filter=symbol_filter, replace=args.replace_history)
        return

    if args.signal_now:
        tick_all(symbol_filter=symbol_filter, force_signal=True, force_execute=False)
        return

    if args.execute_now:
        tick_all(symbol_filter=symbol_filter, force_signal=False, force_execute=True)
        return

    while True:
        tick_all(symbol_filter=symbol_filter, force_signal=False, force_execute=False)
        time.sleep(CHECK_INTERVAL_SEC)


if __name__ == "__main__":
    main()
