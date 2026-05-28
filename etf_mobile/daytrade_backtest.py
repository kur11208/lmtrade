from __future__ import annotations

from pathlib import Path
import argparse
import json

import daytrade_runner as runner

RESULTS_FILE = Path("logs") / "daytrade_backtest_results.json"


def max_drawdown(values: list[float]) -> float:
    if not values:
        return 0.0
    peak = values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1.0)
    return worst


def simulate_symbol(symbol: str) -> dict:
    bars = runner.load_intraday_bars(symbol)
    state = runner.fresh_state(symbol)
    curve = []
    for bar in bars:
        state = runner.process_bar(symbol, state, bars, bar)
        curve.append({
            "timestamp": bar["timestamp"],
            "equity": runner.parse_float(state.get("equity"), 1.0),
            "action": state.get("last_action", ""),
            "position": state.get("current_position_today", ""),
        })

    values = [row["equity"] for row in curve] or [1.0]
    gross_profit = runner.parse_float(state.get("gross_profit_rate"), 0.0)
    gross_loss = runner.parse_float(state.get("gross_loss_rate"), 0.0)
    closed = runner.parse_int(state.get("closed_trade_count"), 0)
    wins = runner.parse_int(state.get("win_count"), 0)
    losses = runner.parse_int(state.get("loss_count"), 0)
    return {
        "symbol": symbol,
        "bars": len(bars),
        "final_equity": values[-1],
        "pnl_rate": values[-1] - 1.0,
        "max_drawdown": max_drawdown(values),
        "closed_trade_count": closed,
        "win_count": wins,
        "loss_count": losses,
        "win_rate": wins / closed if closed else None,
        "profit_factor": None if gross_loss <= 0 else gross_profit / gross_loss,
        "gross_profit_rate": gross_profit,
        "gross_loss_rate": gross_loss,
        "recent_trades": state.get("recent_trades", []),
        "last_state": {
            "latest_ts": state.get("latest_ts"),
            "last_action": state.get("last_action"),
            "strategy_reason": state.get("strategy_reason"),
            "position": state.get("current_position_today"),
        },
    }


def save_results(rows: list[dict]):
    RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_FILE.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", type=str, default="")
    args = parser.parse_args()
    symbol_filter = args.symbol.strip() or None

    results = []
    for item in runner.load_symbols():
        symbol = str(item["symbol"])
        if symbol_filter and symbol != symbol_filter:
            continue
        results.append(simulate_symbol(symbol))

    save_results(results)
    for row in results:
        win_rate = "-" if row["win_rate"] is None else f"{row['win_rate'] * 100:.1f}%"
        profit_factor = "-" if row["profit_factor"] is None else f"{row['profit_factor']:.2f}"
        print(
            f"{row['symbol']} pnl={row['pnl_rate'] * 100:.2f}% "
            f"dd={row['max_drawdown'] * 100:.2f}% trades={row['closed_trade_count']} "
            f"win={win_rate} pf={profit_factor}"
        )


if __name__ == "__main__":
    main()
