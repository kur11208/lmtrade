from __future__ import annotations

import csv
import json
import tempfile
from pathlib import Path

import runner_pc_multi as runner
from backtest_synthetic_100y import backtest_symbol, pct, setup_symbol


REAL_DATA_DIR = Path("data")
CANDIDATE_UNIVERSE_FILE = REAL_DATA_DIR / "candidate_universe.json"
SYMBOLS_FILE = REAL_DATA_DIR / "symbols.json"
FALLBACK_SYMBOLS = ["1321", "1306", "1343", "1540", "2510"]


def load_candidate_universe() -> list[dict]:
    source = CANDIDATE_UNIVERSE_FILE if CANDIDATE_UNIVERSE_FILE.exists() else SYMBOLS_FILE
    rows = read_json(source, [])
    out = []
    for row in rows:
        symbol = str(row.get("symbol", "")).strip()
        if not symbol:
            continue
        if row.get("candidate", True) is False:
            continue
        out.append({
            "symbol": symbol,
            "name": row.get("name", ""),
            "asset_class": row.get("asset_class", ""),
        })
    if out:
        return out
    return [{"symbol": symbol, "name": "", "asset_class": ""} for symbol in FALLBACK_SYMBOLS]


def has_ohlc(symbol: str) -> bool:
    return (REAL_DATA_DIR / symbol / "ohlc.csv").exists()


def read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_real_ohlc(symbol: str) -> list[dict]:
    path = REAL_DATA_DIR / symbol / "ohlc.csv"
    rows = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            try:
                rows.append({
                    "date": str(row["date"]).strip(),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": int(float(row.get("volume") or 0)),
                })
            except Exception:
                continue
    return sorted(rows, key=lambda x: x["date"])


def data_quality_flags(rows: list[dict]) -> str:
    flags = []
    if len(rows) < 500:
        flags.append("short_history")
    first_close = rows[0]["close"]
    last_close = rows[-1]["close"]
    if first_close > 0 and last_close / first_close < 0.35:
        flags.append("large_long_term_drop")

    max_abs_return = 0.0
    large_jump_count = 0
    prev_close = rows[0]["close"]
    for row in rows[1:]:
        if prev_close > 0:
            ret = row["close"] / prev_close - 1.0
            max_abs_return = max(max_abs_return, abs(ret))
            if abs(ret) >= 0.20:
                large_jump_count += 1
        prev_close = row["close"]

    if large_jump_count:
        flags.append(f"daily_jump>=20%:{large_jump_count}")
    if max_abs_return >= 0.35:
        flags.append(f"max_jump={max_abs_return * 100:.1f}%")
    return ",".join(flags) if flags else "ok"


def print_results(results: list[dict]):
    if not results:
        print("No candidates with OHLC data were available.")
        return

    headers = [
        "symbol",
        "active",
        "period",
        "days",
        "final_eq",
        "return",
        "cagr",
        "max_dd",
        "buy_hold",
        "exposure",
        "trades",
        "win_rate",
        "avg_trade",
        "stops",
        "quality",
    ]
    rows = []
    for item in results:
        rows.append([
            item["symbol"],
            "yes" if item["strategy_enabled"] else "no",
            item["period"],
            str(item["days"]),
            f"{item['final_equity']:.4f}",
            pct(item["total_return"]),
            pct(item["cagr"]),
            pct(item["max_dd"]),
            pct(item["buy_hold"]),
            pct(item["exposure"]),
            str(item["trades"]),
            pct(item["win_rate"]),
            pct(item["avg_trade"]),
            str(item["stops"]),
            item["quality"],
        ])

    widths = [len(header) for header in headers]
    for row in rows:
        widths = [max(width, len(cell)) for width, cell in zip(widths, row)]

    print("Real OHLC backtest using current runner logic")
    print("Data is copied to a temporary directory; real data/ is not modified.")
    print()
    print("  ".join(header.ljust(width) for header, width in zip(headers, widths)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(cell.ljust(width) for cell, width in zip(row, widths)))

    avg_return = sum(item["total_return"] for item in results) / len(results)
    avg_dd = sum(item["max_dd"] for item in results) / len(results)
    total_trades = sum(item["trades"] for item in results)
    total_wins = sum(item["wins"] for item in results)
    total_stops = sum(item["stops"] for item in results)
    print()
    print(f"average_return={pct(avg_return)}")
    print(f"average_max_dd={pct(avg_dd)}")
    print(f"total_trades={total_trades}")
    print(f"portfolio_win_rate={pct(total_wins / total_trades if total_trades else 0.0)}")
    print(f"total_stops={total_stops}")

    active = [item for item in results if item["strategy_enabled"]]
    if active and len(active) != len(results):
        active_return = sum(item["total_return"] for item in active) / len(active)
        active_dd = sum(item["max_dd"] for item in active) / len(active)
        active_trades = sum(item["trades"] for item in active)
        active_wins = sum(item["wins"] for item in active)
        active_stops = sum(item["stops"] for item in active)
        print()
        print(f"active_symbols={','.join(item['symbol'] for item in active)}")
        print(f"active_average_return={pct(active_return)}")
        print(f"active_average_max_dd={pct(active_dd)}")
        print(f"active_total_trades={active_trades}")
        print(f"active_win_rate={pct(active_wins / active_trades if active_trades else 0.0)}")
        print(f"active_total_stops={active_stops}")


def print_missing(missing: list[dict]):
    if not missing:
        return
    print()
    print("Missing OHLC candidates skipped:")
    for item in missing:
        name = item.get("name") or "-"
        asset = item.get("asset_class") or "-"
        print(f"- {item['symbol']} ({asset}) {name}")


def main():
    old_data_dir = runner.DATA_DIR
    old_symbols_file = runner.SYMBOLS_FILE

    try:
        with tempfile.TemporaryDirectory() as tmp:
            temp_data = Path(tmp) / "data"
            temp_data.mkdir()
            runner.DATA_DIR = temp_data
            runner.SYMBOLS_FILE = temp_data / "symbols.json"
            runner.SETTINGS_CACHE.clear()
            runner.OHLC_CACHE.clear()
            runner.HISTORY_DATE_CACHE.clear()

            universe = load_candidate_universe()
            available = [item for item in universe if has_ohlc(item["symbol"])]
            missing = [item for item in universe if not has_ohlc(item["symbol"])]
            write_json(runner.SYMBOLS_FILE, [{"symbol": item["symbol"], "enabled": True} for item in available])

            results = []
            for item in available:
                symbol = item["symbol"]
                rows = read_real_ohlc(symbol)
                settings = read_json(REAL_DATA_DIR / symbol / "paper_settings.json", {})
                sym_dir = setup_symbol(temp_data, symbol, rows, settings)
                result = backtest_symbol(symbol, rows, sym_dir)
                result["period"] = f"{rows[0]['date']}..{rows[-1]['date']}"
                result["quality"] = data_quality_flags(rows)
                result["asset_class"] = item.get("asset_class", "")
                results.append(result)

            print_results(results)
            print_missing(missing)
    finally:
        runner.DATA_DIR = old_data_dir
        runner.SYMBOLS_FILE = old_symbols_file
        runner.SETTINGS_CACHE.clear()
        runner.OHLC_CACHE.clear()
        runner.HISTORY_DATE_CACHE.clear()


if __name__ == "__main__":
    main()
