from __future__ import annotations

import argparse
import csv
from pathlib import Path

import daytrade_status
import daytrade_runner as runner


ALIASES = {
    "timestamp": ("timestamp", "datetime", "date_time", "time", "日時", "時刻"),
    "open": ("open", "o", "始値"),
    "high": ("high", "h", "高値"),
    "low": ("low", "l", "安値"),
    "close": ("close", "c", "終値", "price", "価格"),
    "volume": ("volume", "vol", "出来高"),
}


def get_value(row: dict, logical_name: str):
    lower_map = {str(k).strip().lower(): v for k, v in row.items()}
    raw_map = {str(k).strip(): v for k, v in row.items()}
    for alias in ALIASES[logical_name]:
        if alias in raw_map:
            return raw_map[alias]
        value = lower_map.get(alias.lower())
        if value is not None:
            return value
    return ""


def read_source(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            mapped = {
                "timestamp": get_value(row, "timestamp"),
                "open": get_value(row, "open"),
                "high": get_value(row, "high"),
                "low": get_value(row, "low"),
                "close": get_value(row, "close"),
                "volume": get_value(row, "volume") or 0,
            }
            bar = runner.normalize_bar(mapped)
            if bar is not None:
                rows.append(bar)
    return rows


def write_symbol_bars(symbol: str, rows: list[dict], replace: bool):
    out_path = runner.DATA_DIR / symbol / "intraday_bars.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    by_ts = {}
    if not replace and out_path.exists():
        for row in runner.load_intraday_bars(symbol):
            by_ts[row["timestamp"]] = row
    for row in rows:
        by_ts[row["timestamp"]] = row

    merged = sorted(by_ts.values(), key=lambda row: row["ts"])
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for row in merged:
            writer.writerow([
                row["timestamp"],
                row["open"],
                row["high"],
                row["low"],
                row["close"],
                row["volume"],
            ])
    runner.BAR_CACHE.pop(symbol, None)
    return len(merged)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--file", required=True)
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--run-runner", action="store_true")
    args = parser.parse_args()

    symbol = args.symbol.strip()
    rows = read_source(Path(args.file))
    total = write_symbol_bars(symbol, rows, replace=args.replace)
    daytrade_status.set_data_dir(runner.DATA_DIR)
    daytrade_status.write_feed_status(
        adapter="csv_import",
        status="ok",
        message=f"imported {len(rows)} rows",
        symbols=[symbol],
        bars_written=len(rows),
        last_bar_ts=max((row["timestamp"] for row in rows), default=""),
    )
    print(f"imported={len(rows)} total={total} symbol={symbol}")
    if args.run_runner and rows:
        runner.tick_symbol(symbol, replay_all=False, now=max(row["ts"] for row in rows))


if __name__ == "__main__":
    main()
