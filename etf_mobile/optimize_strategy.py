from __future__ import annotations

import argparse
import itertools
import json
import math
from pathlib import Path

import runner_pc_multi as runner
from backtest_real_ohlc import data_quality_flags, has_ohlc, load_candidate_universe, read_real_ohlc
from backtest_synthetic_100y import pct


TRAIN_END = "2018-12-31"
TEST_START = "2019-01-01"
RESULT_PATH = Path("logs") / "optimization_results.json"

GRID = {
    "trend_ma_days": [100, 150, 200],
    "momentum_days": [20, 60],
    "max_atr_rate": [0.04, 0.06, 0.08],
    "risk_cash": [0.0, 0.1, 0.2],
    "max_position_w": [0.4, 0.6, 0.8, 1.0],
    "atr_mult": [2.0, 3.0],
    "buy_th": [0.50, 0.55],
    "sell_th": [0.35, 0.40],
}

BASE_COST_SETTINGS = {
    "fee_rate": runner.DEFAULT_FEE_RATE,
    "slippage_rate": runner.DEFAULT_SLIPPAGE_RATE,
}


def grid_settings():
    keys = list(GRID.keys())
    for values in itertools.product(*(GRID[key] for key in keys)):
        settings = dict(zip(keys, values))
        settings.update(BASE_COST_SETTINGS)
        settings["strategy_enabled"] = True
        yield settings


def rolling_average(values: list[float], days: int) -> list[float | None]:
    out = [None] * len(values)
    total = 0.0
    for idx, value in enumerate(values):
        total += value
        if idx >= days:
            total -= values[idx - days]
        if idx >= days - 1:
            out[idx] = total / days
    return out


def rolling_return(values: list[float], days: int) -> list[float | None]:
    out = [None] * len(values)
    for idx in range(days, len(values)):
        prev = values[idx - days]
        if prev > 0:
            out[idx] = values[idx] / prev - 1.0
    return out


def precompute(rows: list[dict]) -> dict:
    closes = [row["close"] for row in rows]
    atr = [runner.calc_atr_from_rows(rows, 1.0, idx) for idx in range(len(rows))]
    ma = {days: rolling_average(closes, days) for days in GRID["trend_ma_days"]}
    mom = {days: rolling_return(closes, days) for days in GRID["momentum_days"]}
    return {
        "closes": closes,
        "atr": atr,
        "ma": ma,
        "mom": mom,
    }


def max_drawdown(curve: list[float]) -> float:
    peak = curve[0]
    out = 0.0
    for value in curve:
        if value > peak:
            peak = value
        if peak > 0:
            out = min(out, value / peak - 1.0)
    return out


def action_name(current_w: float, next_w: float) -> str:
    return runner.action_name(current_w, next_w)


def decision(current_pos: str, row: dict, idx: int, settings: dict, pc: dict) -> dict:
    close = row["close"]
    atr_14 = pc["atr"][idx]
    ma = pc["ma"][settings["trend_ma_days"]][idx]
    momentum = pc["mom"][settings["momentum_days"]][idx]
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
    dynamic_risk_cash = settings["risk_cash"]
    max_atr_rate = settings["max_atr_rate"]
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
    target_w = min(settings["max_position_w"], max(0.0, 1.0 - dynamic_risk_cash))
    force_sell = (
        (trend_ready and not trend_ok)
        or (momentum is not None and momentum < -0.05)
        or not atr_ok
        or score <= settings["sell_th"]
    )
    allow_buy = trend_ok and momentum_ok and atr_ok and score >= settings["buy_th"]

    if current_pos == "LONG":
        next_w = 0.0 if force_sell else target_w
    else:
        next_w = target_w if allow_buy else 0.0

    return {
        "next_w": next_w,
        "atr_14": atr_14,
        "score": score,
        "reason": ",".join(reasons),
    }


def backtest_fast(rows: list[dict], pc: dict, settings: dict, start_idx: int, end_idx: int) -> dict:
    equity = 1.0
    current_w = 0.0
    current_pos = "CASH"
    pending_action = "HOLD_CASH"
    next_w = 0.0
    current_stop = None
    next_stop = None
    prev_close = rows[start_idx]["close"]
    cost_rate = settings["fee_rate"] + settings["slippage_rate"]

    curve = []
    exposure_days = 0
    stop_count = 0
    closed_trade_returns = []
    entry_equity = None
    buys = 0
    sells = 0

    for idx in range(start_idx, end_idx + 1):
        row = rows[idx]

        old_w = current_w
        old_pos = current_pos
        if pending_action == "BUY_NEXT_OPEN" and current_pos == "CASH":
            current_w = next_w
            current_pos = "LONG"
        elif pending_action == "SELL_NEXT_OPEN" and current_pos == "LONG":
            current_w = 0.0
            current_pos = "CASH"
        elif pending_action == "HOLD_LONG":
            current_w = next_w
            current_pos = "LONG"
        elif pending_action == "HOLD_CASH":
            current_w = 0.0
            current_pos = "CASH"

        trade_cost = abs(current_w - old_w) * cost_rate
        if trade_cost:
            equity *= max(0.0, 1.0 - trade_cost)

        if old_pos != "LONG" and current_pos == "LONG":
            buys += 1
            entry_equity = equity
        elif old_pos == "LONG" and current_pos != "LONG":
            sells += 1
            if entry_equity:
                closed_trade_returns.append(equity / entry_equity - 1.0)
            entry_equity = None

        stop_triggered = False
        if current_pos == "LONG" and prev_close > 0:
            exposure_w = current_w
            if current_stop is not None and current_stop > 0 and row["low"] <= current_stop:
                exit_price = row["open"] if row["open"] <= current_stop else current_stop
                position_return = exit_price / prev_close - 1.0
                stop_triggered = True
                stop_count += 1
                current_w = 0.0
                current_pos = "CASH"
                equity *= max(0.0, 1.0 + exposure_w * position_return - exposure_w * cost_rate)
                if entry_equity:
                    closed_trade_returns.append(equity / entry_equity - 1.0)
                entry_equity = None
            else:
                position_return = row["close"] / prev_close - 1.0
                equity *= max(0.0, 1.0 + exposure_w * position_return)

        dec = decision(current_pos, row, idx, settings, pc)
        if stop_triggered:
            dec = decision("CASH", row, idx, settings, pc)
        next_w = dec["next_w"]
        pending_action = action_name(current_w, next_w)

        current_stop = row["close"] - settings["atr_mult"] * dec["atr_14"] if current_pos == "LONG" else None
        next_stop = row["close"] - settings["atr_mult"] * dec["atr_14"] if next_w > 0 else None
        if current_pos != "LONG":
            current_stop = None
        if current_pos == "LONG":
            current_stop = next_stop

        if current_pos == "LONG":
            exposure_days += 1

        curve.append(equity)
        prev_close = row["close"]

    years = (
        runner.dt.datetime.strptime(rows[end_idx]["date"], "%Y-%m-%d").date()
        - runner.dt.datetime.strptime(rows[start_idx]["date"], "%Y-%m-%d").date()
    ).days / 365.25
    final_equity = curve[-1]
    cagr = final_equity ** (1.0 / years) - 1.0 if final_equity > 0 and years > 0 else 0.0
    dd = max_drawdown(curve)
    trades = len(closed_trade_returns)
    wins = sum(1 for value in closed_trade_returns if value > 0)
    avg_trade = sum(closed_trade_returns) / trades if trades else 0.0
    return {
        "final_equity": final_equity,
        "total_return": final_equity - 1.0,
        "cagr": cagr,
        "max_dd": dd,
        "score": score_result(
            cagr,
            dd,
            exposure_days / len(curve),
            trades / max(years, 1.0),
            settings["max_position_w"],
        ),
        "exposure": exposure_days / len(curve),
        "trades": trades,
        "wins": wins,
        "win_rate": wins / trades if trades else 0.0,
        "avg_trade": avg_trade,
        "stops": stop_count,
        "buys": buys,
        "sells": sells,
    }


def score_result(
    cagr: float,
    max_dd: float,
    exposure: float,
    trades_per_year: float,
    max_position_w: float,
) -> float:
    exposure_penalty = max(0.0, exposure - 0.80) * 0.03
    trade_penalty = max(0.0, trades_per_year - 20.0) * 0.001
    concentration_penalty = max(0.0, max_position_w - 0.4) * 0.20
    return cagr - 0.7 * abs(max_dd) - exposure_penalty - trade_penalty - concentration_penalty


def split_indexes(rows: list[dict]) -> tuple[int, int, int, int]:
    train_start = 0
    train_candidates = [i for i, row in enumerate(rows) if row["date"] <= TRAIN_END]
    test_start_candidates = [i for i, row in enumerate(rows) if row["date"] >= TEST_START]
    if not train_candidates or not test_start_candidates:
        split = max(1, int(len(rows) * 0.7))
        return train_start, split - 1, split, len(rows) - 1
    train_end = max(train_candidates)
    test_start = test_start_candidates[0]
    if test_start <= train_end:
        split = max(1, int(len(rows) * 0.7))
        return train_start, split - 1, split, len(rows) - 1
    return train_start, train_end, test_start, len(rows) - 1


def choose_for_symbol(symbol: str, rows: list[dict]) -> dict:
    pc = precompute(rows)
    train_start, train_end, test_start, test_end = split_indexes(rows)
    quality = data_quality_flags(rows)

    best = None
    for settings in grid_settings():
        train = backtest_fast(rows, pc, settings, train_start, train_end)
        item = {"settings": settings, "train": train}
        if best is None or train["score"] > best["train"]["score"]:
            best = item

    assert best is not None
    test = backtest_fast(rows, pc, best["settings"], test_start, test_end)
    full = backtest_fast(rows, pc, best["settings"], 0, len(rows) - 1)

    quality_blocked = "large_long_term_drop" in quality or "max_jump" in quality
    adopted = (
        not quality_blocked
        and test["cagr"] > 0.0
        and test["max_dd"] > -0.35
        and test["score"] > -0.05
    )

    recommended_settings = dict(best["settings"])
    recommended_settings["symbol"] = symbol
    recommended_settings["strategy_enabled"] = adopted
    recommended_settings["optimized_train_end"] = TRAIN_END
    recommended_settings["optimized_test_start"] = TEST_START
    recommended_settings["optimization_score_train"] = round(best["train"]["score"], 6)
    recommended_settings["optimization_score_test"] = round(test["score"], 6)
    recommended_settings["data_quality"] = quality

    return {
        "symbol": symbol,
        "period": f"{rows[0]['date']}..{rows[-1]['date']}",
        "quality": quality,
        "adopted": adopted,
        "settings": recommended_settings,
        "train": best["train"],
        "test": test,
        "full": full,
    }


def print_summary(results: list[dict]):
    if not results:
        print("No candidates with OHLC data were available.")
        return

    headers = [
        "symbol", "adopt", "quality", "train_cagr", "train_dd", "test_cagr",
        "test_dd", "test_score", "full_cagr", "full_dd", "exposure", "settings",
    ]
    rows = []
    for result in results:
        settings = result["settings"]
        setting_text = (
            f"ma={settings['trend_ma_days']} mom={settings['momentum_days']} "
            f"atrR={settings['max_atr_rate']} risk={settings['risk_cash']} "
            f"maxW={settings['max_position_w']} atrM={settings['atr_mult']} "
            f"buy={settings['buy_th']} sell={settings['sell_th']}"
        )
        rows.append([
            result["symbol"],
            "yes" if result["adopted"] else "no",
            result["quality"],
            pct(result["train"]["cagr"]),
            pct(result["train"]["max_dd"]),
            pct(result["test"]["cagr"]),
            pct(result["test"]["max_dd"]),
            f"{result['test']['score']:.4f}",
            pct(result["full"]["cagr"]),
            pct(result["full"]["max_dd"]),
            pct(result["full"]["exposure"]),
            setting_text,
        ])

    widths = [len(header) for header in headers]
    for row in rows:
        widths = [max(width, len(cell)) for width, cell in zip(widths, row)]

    print(f"Strategy optimization train<= {TRAIN_END}, test>= {TEST_START}")
    print("Selection score: CAGR - 0.7*abs(maxDD), with exposure/trade and high-position penalties.")
    print()
    print("  ".join(header.ljust(width) for header, width in zip(headers, widths)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(cell.ljust(width) for cell, width in zip(row, widths)))


def print_missing(missing: list[dict]):
    if not missing:
        return
    print()
    print("Missing OHLC candidates skipped:")
    for item in missing:
        name = item.get("name") or "-"
        asset = item.get("asset_class") or "-"
        print(f"- {item['symbol']} ({asset}) {name}")


def apply_settings(results: list[dict]):
    for result in results:
        path = Path("data") / result["symbol"] / "paper_settings.json"
        data = result["settings"]
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write recommended settings to data/<symbol>/paper_settings.json")
    args = parser.parse_args()

    universe = load_candidate_universe()
    available = [item for item in universe if has_ohlc(item["symbol"])]
    missing = [item for item in universe if not has_ohlc(item["symbol"])]

    results = []
    for item in available:
        symbol = item["symbol"]
        rows = read_real_ohlc(symbol)
        results.append(choose_for_symbol(symbol, rows))

    print_summary(results)
    print_missing(missing)
    RESULT_PATH.parent.mkdir(exist_ok=True)
    payload = {
        "results": results,
        "missing_ohlc": missing,
    }
    RESULT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print(f"wrote {RESULT_PATH}")

    if args.apply:
        apply_settings(results)
        print("applied recommended settings")


if __name__ == "__main__":
    main()
