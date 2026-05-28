from __future__ import annotations

import csv
import datetime as dt
import json
import math
import random
import tempfile
from pathlib import Path

import runner_pc_multi as runner


START_DATE = dt.date(1926, 1, 1)
END_DATE = dt.date(2025, 12, 31)
REAL_DATA_DIR = Path("data")
CANDIDATE_UNIVERSE_FILE = REAL_DATA_DIR / "candidate_universe.json"
SYMBOLS_FILE = REAL_DATA_DIR / "symbols.json"
FALLBACK_SYMBOLS = ["1321", "1306", "1343", "1540", "2510"]


def read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_symbol_universe() -> list[dict]:
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


SYNTHETIC_PROFILES = {
    "japan_equity": {"drift": 0.00024, "vol": 0.0110, "shock": 0.110},
    "reit": {"drift": 0.00018, "vol": 0.0130, "shock": 0.120},
    "gold": {"drift": 0.00013, "vol": 0.0100, "shock": 0.090},
    "bond": {"drift": 0.00004, "vol": 0.0040, "shock": 0.045},
    "global_equity": {"drift": 0.00025, "vol": 0.0105, "shock": 0.100},
    "us_equity": {"drift": 0.00028, "vol": 0.0120, "shock": 0.115},
    "dividend_equity": {"drift": 0.00020, "vol": 0.0095, "shock": 0.095},
}


def make_ohlc_rows(start: dt.date, end: dt.date, seed: int, asset_class: str = ""):
    rows = []
    profile = SYNTHETIC_PROFILES.get(asset_class, SYNTHETIC_PROFILES["japan_equity"])
    drift = profile["drift"] * (0.90 + (seed % 5) * 0.05)
    vol = profile["vol"] * (0.85 + (seed % 7) * 0.04)
    shock_size = profile["shock"] * (0.85 + (seed % 3) * 0.08)
    shock_every = 640 + (seed * 47) % 520
    rng = random.Random(1009 + seed * 7919)
    close = 100.0 + (seed % 11) * 12.0
    day_index = 0
    d = start
    while d <= end:
        if d.weekday() < 5:
            regime = math.sin((day_index + seed * 29) / 1511.0) * drift * 1.5
            shock = -shock_size if day_index > 0 and day_index % shock_every == 0 else 0.0
            rebound = shock_size * 0.35 if day_index > 2 and (day_index - 3) % shock_every == 0 else 0.0
            noise = rng.gauss(0.0, vol)
            daily_return = max(-0.20, min(0.15, drift + regime + noise + shock + rebound))
            open_gap = max(-0.08, min(0.08, rng.gauss(0.0, vol * 0.35)))
            open_price = close * (1.0 + open_gap)
            close = max(1.0, close * (1.0 + daily_return))
            intraday = max(0.004, vol * 0.80 + abs(daily_return) * 0.25)
            high = max(open_price, close) * (1.0 + intraday)
            low = min(open_price, close) * (1.0 - intraday)
            rows.append({
                "date": d.strftime("%Y-%m-%d"),
                "open": round(open_price, 6),
                "high": round(high, 6),
                "low": round(low, 6),
                "close": round(close, 6),
                "volume": 100000 + day_index,
            })
            day_index += 1
        d += dt.timedelta(days=1)
    return rows


def setup_symbol(data_dir: Path, symbol: str, rows: list[dict], settings: dict):
    sym_dir = data_dir / symbol
    sym_dir.mkdir()
    merged_settings = {
        "symbol": symbol,
        "buy_th": settings.get("buy_th", 0.50),
        "sell_th": settings.get("sell_th", 0.43),
        "risk_cash": settings.get("risk_cash", 0.0),
        "atr_mult": settings.get("atr_mult", 2.0),
        "fee_rate": settings.get("fee_rate", runner.DEFAULT_FEE_RATE),
        "slippage_rate": settings.get("slippage_rate", runner.DEFAULT_SLIPPAGE_RATE),
        "trend_ma_days": settings.get("trend_ma_days", runner.DEFAULT_TREND_MA_DAYS),
        "momentum_days": settings.get("momentum_days", runner.DEFAULT_MOMENTUM_DAYS),
        "max_atr_rate": settings.get("max_atr_rate", runner.DEFAULT_MAX_ATR_RATE),
        "max_position_w": settings.get("max_position_w", runner.DEFAULT_MAX_POSITION_W),
        "strategy_enabled": settings.get("strategy_enabled", True),
    }
    write_json(sym_dir / "paper_settings.json", merged_settings)
    write_json(sym_dir / "paper_state_atr.json", {
        "symbol": symbol,
        "latest_date": "",
        "equity": 1.0,
        "base_equity": 100.0,
        "close": rows[0]["close"],
        "atr_14": max(rows[0]["close"] * 0.02, 1.0),
        "current_w_today": 0.0,
        "current_position_today": "CASH",
        "pending_action": "HOLD_CASH",
        "next_w": 0.0,
        "buy_th": merged_settings["buy_th"],
        "sell_th": merged_settings["sell_th"],
        "risk_cash": merged_settings["risk_cash"],
        "atr_mult": merged_settings["atr_mult"],
        "fee_rate": merged_settings["fee_rate"],
        "slippage_rate": merged_settings["slippage_rate"],
        "trend_ma_days": merged_settings["trend_ma_days"],
        "momentum_days": merged_settings["momentum_days"],
        "max_atr_rate": merged_settings["max_atr_rate"],
        "max_position_w": merged_settings["max_position_w"],
        "strategy_enabled": merged_settings["strategy_enabled"],
        "current_stop_today": None,
    })
    write_json(sym_dir / "mobile_runner_state.json", {
        "last_signal_run_date": "",
        "last_execution_run_date": "",
    })
    with (sym_dir / "ohlc.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        writer.writerows(rows)
    return sym_dir


def max_drawdown(equity_curve: list[float]) -> float:
    peak = equity_curve[0]
    out = 0.0
    for value in equity_curve:
        if value > peak:
            peak = value
        if peak > 0:
            out = min(out, value / peak - 1.0)
    return out


def pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def backtest_symbol(symbol: str, rows: list[dict], sym_dir: Path):
    state = read_json(sym_dir / "paper_state_atr.json", {})
    runner_state = read_json(sym_dir / "mobile_runner_state.json", {})
    equity_curve = []
    exposure_days = 0
    stop_count = 0
    buys = 0
    sells = 0
    closed_trade_returns = []
    entry_equity = None
    prev_position = state.get("current_position_today", "CASH")

    for row in rows:
        now = dt.datetime.strptime(row["date"], "%Y-%m-%d")

        before_exec_position = state.get("current_position_today", "CASH")
        state, runner_state = runner.execute_pending(state, runner_state, now.replace(hour=9, minute=5), force=True)
        after_exec_position = state.get("current_position_today", "CASH")

        if before_exec_position != "LONG" and after_exec_position == "LONG":
            buys += 1
            entry_equity = float(state.get("equity", 1.0))
        elif before_exec_position == "LONG" and after_exec_position != "LONG":
            sells += 1
            if entry_equity:
                closed_trade_returns.append(float(state.get("equity", 1.0)) / entry_equity - 1.0)
            entry_equity = None

        state, runner_state = runner.run_signal(state, runner_state, now.replace(hour=18, minute=10), force=True)
        current_position = state.get("current_position_today", "CASH")

        if prev_position != "LONG" and current_position == "LONG" and entry_equity is None:
            buys += 1
            entry_equity = float(state.get("equity", 1.0))
        elif prev_position == "LONG" and current_position != "LONG":
            sells += 1
            if entry_equity:
                closed_trade_returns.append(float(state.get("equity", 1.0)) / entry_equity - 1.0)
            entry_equity = None

        if state.get("stop_triggered_today"):
            stop_count += 1

        if current_position == "LONG":
            exposure_days += 1

        prev_position = current_position
        equity_curve.append(float(state.get("equity", 1.0)))

    final_equity = equity_curve[-1]
    years = (dt.datetime.strptime(rows[-1]["date"], "%Y-%m-%d").date() - dt.datetime.strptime(rows[0]["date"], "%Y-%m-%d").date()).days / 365.25
    cagr = final_equity ** (1.0 / years) - 1.0 if final_equity > 0 and years > 0 else 0.0
    bh_return = rows[-1]["close"] / rows[0]["close"] - 1.0
    trade_count = len(closed_trade_returns)
    wins = sum(1 for value in closed_trade_returns if value > 0)
    win_rate = wins / trade_count if trade_count else 0.0
    avg_trade = sum(closed_trade_returns) / trade_count if trade_count else 0.0

    return {
        "symbol": symbol,
        "strategy_enabled": bool(state.get("strategy_enabled", True)),
        "days": len(rows),
        "final_equity": final_equity,
        "total_return": final_equity - 1.0,
        "cagr": cagr,
        "max_dd": max_drawdown(equity_curve),
        "buy_hold": bh_return,
        "exposure": exposure_days / len(rows),
        "trades": trade_count,
        "wins": wins,
        "win_rate": win_rate,
        "avg_trade": avg_trade,
        "stops": stop_count,
        "buys": buys,
        "sells": sells,
        "open_trade": entry_equity is not None,
    }


def print_results(results: list[dict]):
    headers = [
        "symbol", "active", "days", "final_eq", "return", "cagr", "max_dd",
        "buy_hold", "exposure", "trades", "win_rate", "avg_trade", "stops",
    ]
    rows = []
    for item in results:
        rows.append([
            item["symbol"],
            "yes" if item["strategy_enabled"] else "no",
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
        ])

    widths = [len(header) for header in headers]
    for row in rows:
        widths = [max(width, len(cell)) for width, cell in zip(widths, row)]

    print("Synthetic 100-year backtest, 1926-01-01 to 2025-12-31")
    print(f"Data is generated for {len(results)} candidates in a temporary directory; real data/ is not modified.")
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


def main():
    universe = load_symbol_universe()
    real_settings = {
        item["symbol"]: read_json(REAL_DATA_DIR / item["symbol"] / "paper_settings.json", {})
        for item in universe
    }

    old_data_dir = runner.DATA_DIR
    old_symbols_file = runner.SYMBOLS_FILE

    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        runner.DATA_DIR = data_dir
        runner.SYMBOLS_FILE = data_dir / "symbols.json"
        runner.SETTINGS_CACHE.clear()
        runner.OHLC_CACHE.clear()
        runner.HISTORY_DATE_CACHE.clear()

        write_json(runner.SYMBOLS_FILE, [{"symbol": item["symbol"], "enabled": True} for item in universe])

        results = []
        for idx, item in enumerate(universe):
            symbol = item["symbol"]
            rows = make_ohlc_rows(START_DATE, END_DATE, idx, item.get("asset_class", ""))
            sym_dir = setup_symbol(data_dir, symbol, rows, real_settings.get(symbol, {}))
            results.append(backtest_symbol(symbol, rows, sym_dir))

        print_results(results)

    runner.DATA_DIR = old_data_dir
    runner.SYMBOLS_FILE = old_symbols_file
    runner.SETTINGS_CACHE.clear()
    runner.OHLC_CACHE.clear()
    runner.HISTORY_DATE_CACHE.clear()


if __name__ == "__main__":
    main()
