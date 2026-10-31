from __future__ import annotations

from pathlib import Path
import argparse
import datetime as dt
import json
import math
import os
import time

import daytrade_config
import daytrade_market_calendar as market_calendar
import daytrade_runner as runner

DEFAULT_TOP_N = 4
DEFAULT_REFRESH_TIMES = ("09:40", "12:40")
DEFAULT_REFRESH_WINDOW_MINUTES = 20


def results_file() -> Path:
    return runner.DATA_DIR / "screener_results.json"


def schedule_state_file() -> Path:
    return runner.DATA_DIR / "screener_schedule_state.json"


def latest_day_rows(rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    date_s = rows[-1]["date"]
    return [row for row in rows if row["date"] == date_s]


def score_liquidity(turnover_yen: float) -> float:
    if turnover_yen <= 0:
        return 0.0
    return min(25.0, math.log10(max(1.0, turnover_yen)) * 3.0)


def score_range(range_rate: float) -> float:
    if range_rate <= 0:
        return 0.0
    if range_rate < 0.004:
        return range_rate / 0.004 * 8.0
    if range_rate <= 0.035:
        return 18.0
    return max(0.0, 18.0 - (range_rate - 0.035) * 300.0)


def score_volume_ratio(volume_ratio) -> float:
    if volume_ratio is None:
        return 4.0
    return max(0.0, min(20.0, volume_ratio / 2.0 * 20.0))


def score_short_vwap_position(vwap_position) -> float:
    if vwap_position is None:
        return 0.0
    if vwap_position <= 0:
        return min(15.0, 8.0 + abs(vwap_position) * 1000.0)
    return max(0.0, 8.0 - vwap_position * 800.0)


def score_short_breakdown_position(breakdown_distance) -> float:
    if breakdown_distance is None:
        return 0.0
    if breakdown_distance <= 0:
        return min(15.0, 10.0 + abs(breakdown_distance) * 800.0)
    if breakdown_distance < 0.01:
        return max(0.0, 10.0 - breakdown_distance * 900.0)
    return 0.0


def score_symbol(item: dict) -> dict:
    symbol = str(item.get("symbol", "")).strip()
    settings = runner.load_symbol_settings(symbol)
    rows = runner.load_intraday_bars(symbol)
    rows_today = latest_day_rows(rows)

    result = {
        "symbol": symbol,
        "name": item.get("name", ""),
        "enabled": item.get("enabled", True) is not False,
        "score": 0.0,
        "reasons": [],
        "metrics": {},
    }
    if not rows_today:
        result["reasons"].append("no intraday bars")
        return result

    latest = rows_today[-1]
    turnover_yen = sum(row["close"] * row["volume"] for row in rows_today)
    high = max(row["high"] for row in rows_today)
    low = min(row["low"] for row in rows_today)
    range_rate = high / low - 1.0 if low > 0 else 0.0
    vwap = runner.calc_vwap(rows_today)
    vwap_position = latest["close"] / vwap - 1.0 if vwap and vwap > 0 else None
    or_high, or_low, opening_range_done, opening_range_recovered = runner.calc_opening_range(
        rows_today,
        max(5, runner.parse_int(settings.get("opening_range_minutes"), runner.DEFAULT_SETTINGS["opening_range_minutes"])),
        runner.parse_bool(settings.get("allow_recovered_opening_range"), runner.DEFAULT_SETTINGS["allow_recovered_opening_range"]),
        max(1, runner.parse_int(settings.get("max_recovered_opening_range_start_minutes"), runner.DEFAULT_SETTINGS["max_recovered_opening_range_start_minutes"])),
    )
    breakdown_distance = latest["close"] / or_low - 1.0 if or_low and or_low > 0 else None
    volume_ratio = runner.calc_volume_ratio(
        rows_today,
        max(1, runner.parse_int(settings.get("volume_lookback_bars"), runner.DEFAULT_SETTINGS["volume_lookback_bars"])),
        max(1, runner.parse_int(settings.get("volume_signal_bars"), runner.DEFAULT_SETTINGS["volume_signal_bars"])),
    )
    gap_rate = runner.calc_gap_rate(rows, rows_today)
    data_warning = runner.calc_data_warning(
        rows_today,
        runner.parse_int(settings.get("max_expected_bar_gap_minutes"), runner.DEFAULT_SETTINGS["max_expected_bar_gap_minutes"]),
    )
    spread_rate = runner.parse_float(settings.get("spread_rate"), runner.DEFAULT_SETTINGS["spread_rate"])
    allow_short = runner.parse_bool(settings.get("allow_short"), runner.DEFAULT_SETTINGS["allow_short"])

    score = 0.0
    score += score_liquidity(turnover_yen)
    score += score_range(range_rate)
    score += score_volume_ratio(volume_ratio)
    if allow_short:
        score += score_short_vwap_position(vwap_position)
        score += score_short_breakdown_position(breakdown_distance)

    if not opening_range_done:
        score -= 12.0
        result["reasons"].append("opening range incomplete")
    gap_reason = runner.gap_filter_reason(gap_rate, settings, "short" if allow_short else "long")
    if gap_reason:
        score -= 12.0
        result["reasons"].append("gap too large")
    if data_warning:
        score -= 6.0
        result["reasons"].append(data_warning)
    if spread_rate > 0.002:
        score -= min(10.0, spread_rate * 2000.0)
        result["reasons"].append("spread penalty")
    if turnover_yen <= 0:
        result["reasons"].append("no liquidity")
    if not result["reasons"]:
        result["reasons"].append("ok")

    result["score"] = round(max(0.0, min(100.0, score)), 3)
    result["metrics"] = {
        "date": latest["date"],
        "latest_ts": latest["timestamp"],
        "turnover_yen": turnover_yen,
        "range_rate": range_rate,
        "vwap_position": vwap_position,
        "opening_range_high": or_high,
        "opening_range_low": or_low,
        "opening_range_recovered": opening_range_recovered,
        "breakdown_distance": breakdown_distance,
        "volume_ratio": volume_ratio,
        "gap_rate": gap_rate,
        "data_warning": data_warning,
        "last_price": latest["close"],
    }
    return result


def load_universe() -> list[dict]:
    return runner.load_symbol_universe()


def save_results(results: list[dict]):
    path = results_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


def load_state(symbol: str) -> dict:
    return runner.load_json(runner.DATA_DIR / symbol / "paper_state_daytrade.json", {})


def has_open_position(symbol: str) -> bool:
    state = load_state(symbol)
    return str(state.get("current_position_today", "")).strip().upper() in ("LONG", "SHORT")


def is_selectable(row: dict) -> bool:
    symbol = str(row.get("symbol", "")).strip()
    if not symbol:
        return False
    reasons = {str(value) for value in row.get("reasons", [])}
    if "no intraday bars" in reasons:
        return False
    if "gap too large" in reasons:
        return False
    return runner.parse_float(row.get("score"), 0.0) > 0.0


def selected_symbols(results: list[dict], top_n: int, now: dt.datetime) -> set[str]:
    selected = set()
    limit = int(top_n)
    for row in results:
        symbol = str(row.get("symbol", "")).strip()
        if not is_selectable(row):
            continue
        selected.add(symbol)
        if limit > 0 and len(selected) >= limit:
            break
    return selected


def apply_top(results: list[dict], top_n: int, now: dt.datetime | None = None):
    now = now or dt.datetime.now()
    symbols = runner.load_json(runner.SYMBOLS_FILE, [])
    selected = selected_symbols(results, top_n, now)
    keep_open = {str(item.get("symbol", "")).strip() for item in symbols if has_open_position(str(item.get("symbol", "")).strip())}
    if not selected:
        for item in symbols:
            symbol = str(item.get("symbol", "")).strip()
            item["enabled"] = symbol in keep_open
            item["screener_score"] = next((row["score"] for row in results if row["symbol"] == symbol), 0.0)
            item["screener_selected"] = False
            item["screener_kept_open_position"] = symbol in keep_open
            item["screener_selected_at"] = now.strftime("%Y-%m-%d %H:%M:%S") if item["enabled"] else ""
        runner.save_json(runner.SYMBOLS_FILE, symbols)
        return

    for item in symbols:
        symbol = str(item.get("symbol", "")).strip()
        item["enabled"] = symbol in selected or symbol in keep_open
        item["screener_score"] = next((row["score"] for row in results if row["symbol"] == symbol), 0.0)
        item["screener_selected"] = symbol in selected
        item["screener_kept_open_position"] = symbol in keep_open and symbol not in selected
        item["screener_selected_at"] = now.strftime("%Y-%m-%d %H:%M:%S") if item["enabled"] else ""
    runner.save_json(runner.SYMBOLS_FILE, symbols)


def run_screen(top_n: int, apply: bool = False, now: dt.datetime | None = None) -> list[dict]:
    now = now or dt.datetime.now()
    universe = load_universe()
    results = [score_symbol(item) for item in universe]
    results.sort(key=lambda row: row["score"], reverse=True)
    save_results(results)

    if apply:
        apply_top(results, int(top_n), now=now)

    return results


def load_runtime_config() -> dict:
    return daytrade_config.load_runtime_config()


def parse_refresh_times(value) -> list[dt.time]:
    if value is None or value == "":
        values = list(DEFAULT_REFRESH_TIMES)
    elif isinstance(value, str):
        values = [part.strip() for part in value.split(",")]
    elif isinstance(value, list):
        values = [str(part).strip() for part in value]
    else:
        values = list(DEFAULT_REFRESH_TIMES)

    out = []
    for text in values:
        try:
            hour_s, minute_s = text.split(":", 1)
            out.append(dt.time(int(hour_s), int(minute_s)))
        except Exception:
            continue
    return out or [dt.time(9, 40), dt.time(12, 40)]


def slot_key(day: dt.date, slot: dt.time) -> str:
    return f"{day.isoformat()} {slot.strftime('%H:%M')}"


def due_slots(now: dt.datetime, cfg: dict, state: dict) -> list[dt.time]:
    if not runner.parse_bool(cfg.get("screener_auto_apply"), True):
        return []
    if not market_calendar.is_trading_day(now.date()):
        return []

    window_minutes = max(
        1,
        runner.parse_int(cfg.get("screener_refresh_window_minutes"), DEFAULT_REFRESH_WINDOW_MINUTES),
    )
    runs = state.get("runs", {}) if isinstance(state.get("runs"), dict) else {}
    due = []
    for slot in parse_refresh_times(cfg.get("screener_refresh_times")):
        key = slot_key(now.date(), slot)
        slot_dt = dt.datetime.combine(now.date(), slot)
        if key in runs:
            continue
        if slot_dt <= now <= slot_dt + dt.timedelta(minutes=window_minutes):
            due.append(slot)
    return due


def save_schedule_run(state: dict, slot: dt.time, now: dt.datetime, results: list[dict], top_n: int):
    runs = state.get("runs", {}) if isinstance(state.get("runs"), dict) else {}
    today_prefix = now.date().isoformat()
    runs = {key: value for key, value in runs.items() if key.startswith(today_prefix)}
    selected = sorted(selected_symbols(results, top_n, now))
    runs[slot_key(now.date(), slot)] = {
        "ran_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "selected": selected,
    }
    state["runs"] = runs
    state["last_run_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
    state["last_selected"] = selected
    runner.save_json(schedule_state_file(), state)


def scheduler_tick(now: dt.datetime | None = None) -> list[dict]:
    now = now or dt.datetime.now()
    cfg = load_runtime_config()
    state = runner.load_json(schedule_state_file(), {})
    if not isinstance(state, dict):
        state = {}

    ran = []
    top_n = runner.parse_int(cfg.get("screener_top_n"), DEFAULT_TOP_N)
    for slot in due_slots(now, cfg, state):
        results = run_screen(top_n=top_n, apply=False, now=now)
        selected = selected_symbols(results, top_n, now)
        if not selected:
            continue
        apply_top(results, top_n, now=now)
        save_schedule_run(state, slot, now, results, top_n)
        ran.append({
            "slot": slot.strftime("%H:%M"),
            "selected": sorted(selected),
        })
    return ran


def scheduler_loop(sleep_sec: float):
    while True:
        ran = scheduler_tick()
        for item in ran:
            print(f"screener auto-applied slot={item['slot']} selected={','.join(item['selected'])}")
        time.sleep(max(1.0, sleep_sec))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=DEFAULT_TOP_N)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--sleep", type=float, default=float(os.environ.get("DAYTRADE_SCREENER_SLEEP_SEC", "20")))
    args = parser.parse_args()

    if args.loop:
        scheduler_loop(args.sleep)
        return

    top_n = int(args.top)
    results = run_screen(top_n=top_n, apply=args.apply)
    preview = results if top_n <= 0 else results[:max(1, top_n)]
    for row in preview:
        print(f"{row['symbol']} score={row['score']:.3f} reasons={','.join(row['reasons'])}")


if __name__ == "__main__":
    main()
