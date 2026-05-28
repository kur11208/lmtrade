from __future__ import annotations

from pathlib import Path
import argparse
import csv
import datetime as dt
import math
import random
import time

import daytrade_market_calendar as market_calendar
import daytrade_status
import daytrade_runner as runner


def is_market_time(ts: dt.datetime) -> bool:
    return market_calendar.is_market_time(ts)


def floor_to_interval(ts: dt.datetime, interval_minutes: int) -> dt.datetime:
    interval_minutes = max(1, interval_minutes)
    minute = (ts.minute // interval_minutes) * interval_minutes
    return ts.replace(minute=minute, second=0, microsecond=0)


def next_market_ts(last_ts: dt.datetime, interval_minutes: int) -> dt.datetime:
    ts = last_ts + dt.timedelta(minutes=interval_minutes)
    if not market_calendar.is_trading_day(ts.date()) or ts.time() > market_calendar.MARKET_CLOSE:
        return dt.datetime.combine(market_calendar.next_trading_day(ts.date()), market_calendar.MORNING_OPEN)
    if market_calendar.MORNING_CLOSE < ts.time() < market_calendar.AFTERNOON_OPEN:
        return dt.datetime.combine(ts.date(), market_calendar.AFTERNOON_OPEN)
    if ts.time() < market_calendar.MORNING_OPEN:
        return dt.datetime.combine(ts.date(), market_calendar.MORNING_OPEN)
    return ts


def choose_next_ts(rows: list[dict], interval_minutes: int, realtime: bool) -> dt.datetime | None:
    if rows:
        last_ts = rows[-1]["ts"]
        if realtime:
            now_ts = floor_to_interval(dt.datetime.now(), interval_minutes)
            if is_market_time(now_ts) and now_ts > last_ts:
                return now_ts
            return None
        return next_market_ts(last_ts, interval_minutes)

    now_ts = floor_to_interval(dt.datetime.now(), interval_minutes)
    if realtime and is_market_time(now_ts):
        return now_ts
    if realtime:
        return None
    today = dt.date.today()
    if not market_calendar.is_trading_day(today):
        today = market_calendar.next_trading_day(today)
    return dt.datetime.combine(today, market_calendar.MORNING_OPEN)


def append_bar(symbol: str, interval_minutes: int, rng: random.Random, realtime: bool = False) -> dict | None:
    path = runner.DATA_DIR / symbol / "intraday_bars.csv"
    rows = runner.load_intraday_bars(symbol)
    if rows:
        ts = choose_next_ts(rows, interval_minutes, realtime)
        if ts is None:
            return None
        prev_close = rows[-1]["close"]
    else:
        ts = choose_next_ts(rows, interval_minutes, realtime)
        if ts is None:
            return None
        prev_close = runner.parse_float(runner.load_json(runner.DATA_DIR / symbol / "paper_state_daytrade.json", {}).get("last_price"), 1000.0)

    drift = 0.00015 * math.sin(ts.hour + ts.minute / 60.0)
    noise = rng.uniform(-0.0025, 0.0028)
    close = max(1.0, prev_close * (1.0 + drift + noise))
    open_price = prev_close
    high = max(open_price, close) * (1.0 + rng.uniform(0.0002, 0.0018))
    low = min(open_price, close) * (1.0 - rng.uniform(0.0002, 0.0018))
    volume = max(100.0, (rows[-1]["volume"] if rows else 100000.0) * rng.uniform(0.75, 1.35))

    path.parent.mkdir(parents=True, exist_ok=True)
    need_header = (not path.exists()) or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if need_header:
            writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        writer.writerow([
            runner.ts_text(ts),
            round(open_price, 6),
            round(high, 6),
            round(low, 6),
            round(close, 6),
            round(volume, 0),
        ])
    runner.BAR_CACHE.pop(symbol, None)
    return {"timestamp": runner.ts_text(ts), "close": close, "volume": volume}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", type=str, default="")
    parser.add_argument("--steps", type=int, default=1)
    parser.add_argument("--interval-minutes", type=int, default=1)
    parser.add_argument("--sleep", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--realtime", action="store_true")
    parser.add_argument("--run-runner", action="store_true")
    args = parser.parse_args()

    daytrade_status.set_data_dir(runner.DATA_DIR)
    rng = random.Random(args.seed)
    step = 0
    while True:
        if args.symbol.strip():
            symbols = [args.symbol.strip()]
            runner_symbols = symbols
        else:
            symbols = [str(item["symbol"]) for item in runner.load_symbol_universe()]
            runner_symbols = [str(item["symbol"]) for item in runner.load_symbols()]

        appended = []
        for symbol in symbols:
            row = append_bar(symbol, max(1, args.interval_minutes), rng, realtime=args.realtime)
            if row is None:
                continue
            appended.append(row)
            print(f"{symbol} appended {row['timestamp']} close={row['close']:.3f}")
        last_bar_ts = max((row["timestamp"] for row in appended), default="")
        daytrade_status.write_feed_status(
            adapter="demo_csv",
            status="ok" if appended else "idle",
            message="demo feed appended bars" if appended else "waiting for market time or next interval",
            symbols=symbols,
            bars_written=len(appended),
            last_bar_ts=last_bar_ts,
        )
        if args.run_runner and appended:
            max_ts = max((runner.parse_ts(row["timestamp"]) for row in appended if runner.parse_ts(row["timestamp"]) is not None), default=dt.datetime.now())
            for symbol in runner_symbols:
                runner.tick_symbol(symbol, replay_all=False, now=max_ts)

        step += 1
        if not args.loop and step >= max(1, args.steps):
            break
        if args.sleep > 0:
            time.sleep(args.sleep)


if __name__ == "__main__":
    main()
